"""Status, history, manual run, and the HTML dashboard."""

from fastapi.testclient import TestClient

from dqmonitor.api.app import create_app
from dqmonitor.api.filters import parse_history_filters
from dqmonitor.cli import main
from dqmonitor.storage import sqlite_file_url
from tests.conftest import ROOT, build_project


HEALTHY_ROWS = [
    {"order_id": 1, "amount": 10.0},
    {"order_id": 2, "amount": 12.0},
    {"order_id": 3, "amount": 11.0},
]
HEALTHY_RULES = [
    {
        "name": "orders_null_rate",
        "type": "null_rate",
        "source": "orders",
        "max_null_rate": 0.0,
        "columns": ["order_id", "amount"],
    },
    {
        "name": "orders_schema",
        "type": "schema_consistency",
        "source": "orders",
        "expected_columns": ["order_id", "amount"],
    },
]


def _client(config_path, *, startup=True):
    app = create_app(config_path, start_scheduler_on_boot=False)
    return TestClient(app)


def test_status_history_and_dashboard(tmp_path):
    config_path, _folder, _database = build_project(
        tmp_path, rows=HEALTHY_ROWS, rules=HEALTHY_RULES, run_on_startup=True
    )
    with _client(config_path) as client:
        status = client.get("/quality/status")
        assert status.status_code == 200
        body = status.json()
        assert body["overall_status"] == "pass"
        assert {item["rule_name"] for item in body["checks"]} == {
            "orders_null_rate",
            "orders_schema",
        }
        assert body["schedule"]["interval_minutes"] == 60

        history = client.get("/quality/history", params={"status": "pass", "source": "orders"})
        assert history.status_code == 200
        assert history.json()["count"] == 2

        empty = client.get("/quality/history", params={"since": "2999-01-01T00:00:00Z"})
        assert empty.json()["count"] == 0

        page = client.get("/")
        assert page.status_code == 200
        assert "orders_null_rate" in page.text
        assert "Every configured check passed." in page.text
        assert 'id="current-status"' in page.text
        assert "Run checks now" in page.text


def test_failing_rule_is_on_the_dashboard(tmp_path):
    rows = [{"order_id": 1, "amount": None}, {"order_id": 2, "amount": 12.0}]
    config_path, _folder, _database = build_project(tmp_path, rows=rows, rules=HEALTHY_RULES)
    with _client(config_path) as client:
        body = client.get("/quality/status").json()
        assert body["overall_status"] == "fail"
        page = client.get("/")
        failing = page.text.split('id="failing"', 1)[1].split('id="history"', 1)[0]
        assert "orders_null_rate" in failing
        assert "orders_schema" not in failing

        filtered = client.get("/", params={"status": "fail"})
        history = filtered.text.split('id="history"', 1)[1]
        assert "orders_null_rate" in history
        assert "orders_schema" not in history


def test_history_validation_and_manual_run(tmp_path):
    config_path, _folder, _database = build_project(
        tmp_path, rows=HEALTHY_ROWS, rules=HEALTHY_RULES, run_on_startup=False
    )
    with _client(config_path) as client:
        pending = client.get("/quality/status").json()
        assert pending["overall_status"] == "pending"
        assert pending["checks"][0]["summary"] == "Not run yet."

        bad = client.get("/quality/history", params={"status": "broken"})
        assert bad.status_code == 400
        assert "status" in bad.json()["detail"]

        bad_since = client.get("/quality/history", params={"since": "yesterday"})
        assert bad_since.status_code == 400

        bad_limit = client.get("/quality/history", params={"limit": 0})
        assert bad_limit.status_code == 400

        ran = client.post("/quality/run")
        assert ran.status_code == 200
        assert ran.json()["counts"]["pass"] == 2
        assert client.get("/quality/history").json()["count"] == 2

        redirect = client.post("/quality/run", headers={"accept": "text/html"}, follow_redirects=False)
        assert redirect.status_code == 303
        assert redirect.headers["location"] == "/"


def test_dashboard_shows_a_filter_error(tmp_path):
    config_path, _folder, _database = build_project(tmp_path, rows=HEALTHY_ROWS, rules=HEALTHY_RULES)
    with _client(config_path) as client:
        page = client.get("/", params={"limit": 5000})
        assert page.status_code == 200
        assert "limit must be between 1 and 1000" in page.text


def test_scheduler_can_start_with_the_app(tmp_path):
    config_path, _folder, _database = build_project(tmp_path, rows=HEALTHY_ROWS, rules=HEALTHY_RULES)
    app = create_app(config_path, start_scheduler_on_boot=True)
    with TestClient(app) as client:
        job = client.app.state.scheduler.get_job("dq-checks")
        assert job is not None
        assert client.get("/quality/status").status_code == 200


def test_cli_run_once_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("DQ_DATABASE_URL", sqlite_file_url(tmp_path / "monitor.db"))
    assert main(["run-once", "--config", "config/rules.yaml"]) == 0
    output = capsys.readouterr().out
    assert "orders_null_rate" in output
    assert "9 checks: 9 pass, 0 fail, 0 error" in output
    assert main(["--config", str(tmp_path / "missing.yaml"), "run-once"]) == 2


def test_security_headers_and_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("DQ_RATE_LIMIT", "2")
    config_path, _folder, _database = build_project(tmp_path, rows=HEALTHY_ROWS, rules=HEALTHY_RULES)
    with _client(config_path) as client:
        first = client.get("/quality/status")
        assert first.status_code == 200
        assert first.headers["x-content-type-options"] == "nosniff"
        assert first.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in first.headers["content-security-policy"]
        assert "access-control-allow-origin" not in first.headers
        assert client.get("/quality/status").status_code == 200
        blocked = client.get("/quality/status")
        assert blocked.status_code == 429
        assert blocked.json()["detail"] == "Too many requests"
        assert blocked.headers["retry-after"] == "60"


def test_unhandled_errors_hide_internal_details(tmp_path):
    config_path, _folder, _database = build_project(tmp_path, rows=HEALTHY_ROWS, rules=HEALTHY_RULES)
    app = create_app(config_path, start_scheduler_on_boot=False)

    @app.get("/boom")
    def boom() -> dict[str, str]:
        raise RuntimeError("secret /tmp/hidden token=abc")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    body = response.text.lower()
    assert "traceback" not in body
    assert "hidden" not in body
    assert "token=" not in body


def test_parse_history_filters_treats_naive_timestamps_as_utc():
    filters = parse_history_filters(
        rule_name=" orders_null_rate ",
        status="fail",
        source="",
        since="2026-10-30T12:00:00",
        until="2026-10-30T13:00:00Z",
        limit=10,
        offset=0,
    )
    assert filters.rule_name == "orders_null_rate"
    assert filters.source_name is None
    assert filters.since.tzinfo is None
    assert filters.since.hour == 12
    assert filters.until.hour == 13
