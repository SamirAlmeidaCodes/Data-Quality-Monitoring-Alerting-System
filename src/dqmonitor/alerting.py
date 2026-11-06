"""Deliver alerts to the console, email, and a webhook."""

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import urlparse

import httpx

from dqmonitor.config_models import AlertingConfig, EmailConfig, WebhookConfig
from dqmonitor.errors import AlertDeliveryError, ConfigError
from dqmonitor.redact import redact
from dqmonitor.reporting import check_to_dict
from dqmonitor.storage import StoredCheck

logger = logging.getLogger("dqmonitor.alert")


class AlertSink(Protocol):
    def send(self, check: StoredCheck) -> None:
        """Deliver one alert. Raise on failure so the router can log it."""


class AlertRouter:
    """Fan out an alert to every sink. One broken sink does not stop the others."""

    def __init__(self, sinks: list[AlertSink], on_statuses: set[str]) -> None:
        self.sinks = list(sinks)
        self.on_statuses = set(on_statuses)

    def send(self, check: StoredCheck) -> None:
        if check.status not in self.on_statuses:
            return
        for sink in self.sinks:
            try:
                sink.send(check)
            except Exception:
                logger.exception(
                    "Alert sink %s failed for rule %s",
                    type(sink).__name__,
                    check.rule_name,
                )


class ConsoleAlerter:
    """Write a single WARNING line. The process stdout is the console."""

    def send(self, check: StoredCheck) -> None:
        logger.warning(
            "ALERT status=%s rule=%s source=%s summary=%s",
            check.status,
            check.rule_name,
            check.source_name,
            check.summary,
        )


class EmailAlerter:
    def __init__(self, config: EmailConfig) -> None:
        self.config = config

    def send(self, check: StoredCheck) -> None:
        password = os.environ.get(self.config.password_env, "")
        if self.config.username and not password:
            raise AlertDeliveryError(
                f"Environment variable {self.config.password_env} is empty"
            )
        payload = check_to_dict(check)
        message = EmailMessage()
        message["Subject"] = _header_safe(f"[DQ {check.status.upper()}] {check.rule_name}")
        message["From"] = _header_safe(self.config.from_addr)
        message["To"] = _header_safe(", ".join(self.config.to_addrs))
        message.set_content(check.summary + "\n\n" + _pretty(payload))
        smtp_cls = smtplib.SMTP_SSL if self.config.use_ssl else smtplib.SMTP
        with smtp_cls(self.config.smtp_host, self.config.smtp_port, timeout=15) as smtp:
            if not self.config.use_ssl and self.config.use_tls:
                smtp.starttls()
            if self.config.username:
                smtp.login(self.config.username, password)
            smtp.send_message(message)
        logger.info("Sent email alert for %s to %s", check.rule_name, message["To"])


class WebhookAlerter:
    def __init__(self, config: WebhookConfig) -> None:
        self.config = config

    def send(self, check: StoredCheck) -> None:
        headers = dict(self.config.headers)
        if self.config.bearer_token_env:
            token = os.environ.get(self.config.bearer_token_env, "")
            if not token:
                raise AlertDeliveryError(
                    f"Environment variable {self.config.bearer_token_env} is empty"
                )
            headers["Authorization"] = f"Bearer {token}"
        response = httpx.post(
            self.config.url,
            json=check_to_dict(check),
            headers=headers,
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        logger.info("Sent webhook alert for %s to %s", check.rule_name, redact(self.config.url))


def build_alerter(config: AlertingConfig) -> AlertRouter:
    validate_alerting_config(config)
    sinks: list[AlertSink] = []
    if config.console:
        sinks.append(ConsoleAlerter())
    if config.email.enabled:
        sinks.append(EmailAlerter(config.email))
    if config.webhook.enabled:
        sinks.append(WebhookAlerter(config.webhook))
    return AlertRouter(sinks, on_statuses=set(config.on_statuses))


def validate_alerting_config(config: AlertingConfig) -> None:
    """Fail fast when an enabled channel is incomplete."""

    if config.email.enabled:
        _validate_email(config.email)
    if config.webhook.enabled:
        _validate_webhook(config.webhook)


def _validate_email(config: EmailConfig) -> None:
    if not config.smtp_host.strip():
        raise ConfigError("Email alerts are enabled but smtp_host is empty")
    if not config.from_addr.strip():
        raise ConfigError("Email alerts are enabled but from_addr is empty")
    if not config.to_addrs:
        raise ConfigError("Email alerts are enabled but to_addrs is empty")
    if config.use_ssl and config.use_tls:
        raise ConfigError("Email use_ssl and use_tls cannot both be true")
    if config.username and not os.environ.get(config.password_env):
        raise ConfigError(
            f"Email username is set but environment variable {config.password_env} is empty"
        )


def _validate_webhook(config: WebhookConfig) -> None:
    parsed = urlparse(config.url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("Webhook url must be an absolute http or https URL")
    if config.bearer_token_env and not os.environ.get(config.bearer_token_env):
        raise ConfigError(
            f"Webhook bearer_token_env {config.bearer_token_env} is not set"
        )


def _pretty(payload: dict) -> str:
    import json

    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _header_safe(value: str) -> str:
    """Drop CR and LF so a name cannot inject extra mail headers."""

    return " ".join(value.replace("\r", " ").replace("\n", " ").split())
