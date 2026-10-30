"""Console, email, and webhook alert delivery."""

import logging
import smtplib
from datetime import datetime

import httpx
import pytest

from dqmonitor.alerting import (
    AlertRouter,
    ConsoleAlerter,
    EmailAlerter,
    WebhookAlerter,
    validate_alerting_config,
)
from dqmonitor.config_models import AlertingConfig, EmailConfig, WebhookConfig
from dqmonitor.errors import ConfigError
from dqmonitor.storage import StoredCheck


def _check(status="fail") -> StoredCheck:
    return StoredCheck(
        id=7,
        checked_at=datetime(2026, 10, 30, 12, 0, 0),
        rule_name="orders_null_rate",
        rule_type="null_rate",
        source_name="orders",
        status=status,
        summary="null rate above 5.0%: region=12.0%",
        details={"column_rates": {"region": 0.12}},
    )


def test_console_alert_is_a_warning(caplog):
    caplog.set_level(logging.WARNING, logger="dqmonitor.alert")
    ConsoleAlerter().send(_check())
    assert "ALERT status=fail rule=orders_null_rate" in caplog.text


def test_router_skips_passes_and_continues_after_a_sink_error():
    class Boom:
        def send(self, check):
            raise RuntimeError("smtp down")

    class Record:
        def __init__(self):
            self.sent = []

        def send(self, check):
            self.sent.append(check.status)

    record = Record()
    router = AlertRouter([Boom(), record], on_statuses={"fail", "error"})
    router.send(_check("pass"))
    router.send(_check("fail"))
    assert record.sent == ["fail"]


def test_email_alert_uses_smtp(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=15):
            sent["init"] = (host, port, timeout)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            sent["tls"] = True

        def login(self, username, password):
            sent["login"] = (username, password)

        def send_message(self, message):
            sent["subject"] = message["Subject"]
            sent["to"] = message["To"]
            sent["body"] = message.get_content()

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("DQ_SMTP_PASSWORD", "secret")
    alerter = EmailAlerter(
        EmailConfig(
            enabled=True,
            smtp_host="smtp.example.com",
            smtp_port=587,
            username="monitor",
            password_env="DQ_SMTP_PASSWORD",
            from_addr="dq@example.com",
            to_addrs=["oncall@example.com"],
            use_tls=True,
            use_ssl=False,
        )
    )
    alerter.send(_check())
    assert sent["init"][0] == "smtp.example.com"
    assert sent["tls"] is True
    assert sent["login"] == ("monitor", "secret")
    assert sent["subject"] == "[DQ FAIL] orders_null_rate"
    assert "oncall@example.com" in sent["to"]
    assert "region" in sent["body"]


def test_webhook_posts_the_check_payload(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("DQ_WEBHOOK_TOKEN", "token-1")
    alerter = WebhookAlerter(
        WebhookConfig(
            enabled=True,
            url="https://hooks.example.com/dq",
            timeout_seconds=3,
            headers={"X-Test": "1"},
            bearer_token_env="DQ_WEBHOOK_TOKEN",
        )
    )
    alerter.send(_check())
    assert captured["url"] == "https://hooks.example.com/dq"
    assert captured["json"]["rule_name"] == "orders_null_rate"
    assert captured["json"]["status"] == "fail"
    assert captured["headers"]["Authorization"] == "Bearer token-1"
    assert captured["headers"]["X-Test"] == "1"
    assert captured["timeout"] == 3


def test_enabled_email_without_recipients_is_rejected():
    config = AlertingConfig(
        email=EmailConfig(enabled=True, from_addr="dq@example.com", to_addrs=[])
    )
    with pytest.raises(ConfigError, match="to_addrs"):
        validate_alerting_config(config)


def test_webhook_url_must_be_absolute():
    config = AlertingConfig(webhook=WebhookConfig(enabled=True, url="hooks.example.com"))
    with pytest.raises(ConfigError, match="http"):
        validate_alerting_config(config)
