"""Engine orchestration: isolation, baselines, alerts, and config reload."""

from dqmonitor.alerting import AlertRouter
from dqmonitor.config import CompiledConfig
from dqmonitor.config_models import CsvFolderSourceConfig, Settings, StorageConfig
from dqmonitor.engine import CheckEngine, execute_run
from dqmonitor.rules.base import BaseRule
from dqmonitor.rules.null_rate import NullRateRule
from dqmonitor.rules.row_count import RowCountDropRule
from dqmonitor.rules.schema import SchemaConsistencyRule
from dqmonitor.storage import Store, sqlite_file_url
from tests.conftest import ROOT, make_rule, write_frame


class RecordingAlerter:
    def __init__(self):
        self.sent = []

    def send(self, check):
        self.sent.append(check)


class BoomRule(BaseRule):
    type_name = "boom"
    allowed_params = frozenset()

    def validate(self):
        return None

    def evaluate(self, df, context):
        del df, context
        raise RuntimeError("boom")


def _compiled(tmp_path, rules, rows):
    folder = tmp_path / "incoming"
    write_frame(folder / "orders.csv", rows)
    source = CsvFolderSourceConfig(
        name="orders",
        type="csv_folder",
        path=str(folder),
        pattern="*.csv",
    )
    settings = Settings(
        storage=StorageConfig(url=sqlite_file_url(tmp_path / "monitor.db")),
        sources=[source],
    )
    return (
        CompiledConfig(settings=settings, sources=[source], rules=rules),
        folder,
        settings.storage.url,
    )


def test_failing_rule_alerts_and_a_later_rule_still_runs(tmp_path):
    rules = [
        BoomRule(name="boom", source="orders", enabled=True, params={}),
        make_rule(
            SchemaConsistencyRule,
            name="orders_schema",
            expected_columns=["order_id"],
        ),
    ]
    compiled, _folder, url = _compiled(tmp_path, rules, [{"order_id": 1}])
    store = Store(url)
    store.create_tables()
    recorder = RecordingAlerter()
    router = AlertRouter([recorder], on_statuses={"fail", "error"})
    try:
        report = execute_run(compiled, store, router)
    finally:
        store.dispose()
    by_name = {item.rule_name: item for item in report.results}
    assert by_name["boom"].status == "error"
    assert "boom" in by_name["boom"].summary
    assert by_name["orders_schema"].status == "pass"
    assert [item.rule_name for item in recorder.sent] == ["boom"]


def test_missing_source_is_an_error_result(tmp_path):
    rule = make_rule(NullRateRule, max_null_rate=0.0, columns=["order_id"])
    compiled, _folder, url = _compiled(tmp_path, [rule], [{"order_id": 1}])
    compiled.sources[0] = CsvFolderSourceConfig(
        name="orders",
        type="csv_folder",
        path=str(tmp_path / "missing"),
        pattern="*.csv",
    )
    store = Store(url)
    store.create_tables()
    recorder = RecordingAlerter()
    try:
        report = execute_run(
            compiled, store, AlertRouter([recorder], on_statuses={"fail", "error"})
        )
    finally:
        store.dispose()
    assert report.results[0].status == "error"
    assert recorder.sent[0].status == "error"


def test_disabled_rule_is_skipped(tmp_path):
    rules = [
        make_rule(NullRateRule, name="skipped", enabled=False, max_null_rate=0.0),
        make_rule(SchemaConsistencyRule, name="kept", expected_columns=["order_id"]),
    ]
    compiled, _folder, url = _compiled(tmp_path, rules, [{"order_id": 1}])
    store = Store(url)
    store.create_tables()
    try:
        report = execute_run(compiled, store, AlertRouter([], on_statuses={"fail"}))
    finally:
        store.dispose()
    assert [item.rule_name for item in report.results] == ["kept"]


def test_row_count_baseline_is_read_from_the_previous_result(tmp_path):
    rule = make_rule(RowCountDropRule, max_drop_ratio=0.3, min_rows=1)
    compiled, folder, url = _compiled(
        tmp_path, [rule], [{"order_id": index} for index in range(10)]
    )
    store = Store(url)
    store.create_tables()
    router = AlertRouter([], on_statuses=set())
    try:
        first = execute_run(compiled, store, router)
        write_frame(folder / "orders.csv", [{"order_id": index} for index in range(4)])
        second = execute_run(compiled, store, router)
    finally:
        store.dispose()
    assert first.results[0].status == "pass"
    assert second.results[0].status == "fail"
    assert second.results[0].details["previous_row_count"] == 10
    assert second.results[0].details["row_count"] == 4


def test_config_reload_and_fallback(tmp_path):
    rows = [{"order_id": 1, "region": ""}, {"order_id": 2, "region": "north"}]
    config_path, _folder, database = _project_yaml(tmp_path, rows, max_null_rate=1.0)
    store = Store(sqlite_file_url(database))
    store.create_tables()
    engine = CheckEngine(config_path, store)
    try:
        first = engine.run()
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace("max_null_rate: 1.0", "max_null_rate: 0.0"),
            encoding="utf-8",
        )
        second = engine.run()
        config_path.write_text(":\n  - [\n", encoding="utf-8")
        third = engine.run()
    finally:
        store.dispose()
    assert first.results[0].status == "pass"
    assert second.results[0].status == "fail"
    assert third.results[0].status == "fail"
    assert third.results[0].rule_name == "orders_null_rate"


def test_shipped_sample_passes(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("DQ_DATABASE_URL", sqlite_file_url(tmp_path / "monitor.db"))
    store = Store(sqlite_file_url(tmp_path / "monitor.db"))
    store.create_tables()
    engine = CheckEngine(ROOT / "config" / "rules.yaml", store)
    try:
        report = engine.run()
    finally:
        store.dispose()
    failed = [item.rule_name for item in report.results if item.status != "pass"]
    assert failed == []
    assert len(report.results) == 9


def _project_yaml(tmp_path, rows, max_null_rate):
    from tests.conftest import build_project

    config_path, folder, database = build_project(
        tmp_path,
        rows=rows,
        rules=[
            {
                "name": "orders_null_rate",
                "type": "null_rate",
                "source": "orders",
                "max_null_rate": max_null_rate,
                "columns": ["order_id", "region"],
                "treat_blank_as_null": True,
            }
        ],
    )
    return config_path, folder, database
