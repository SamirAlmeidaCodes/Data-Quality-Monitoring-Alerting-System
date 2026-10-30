"""Load and validate the YAML configuration."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from dqmonitor.config_models import Settings, SourceConfig
from dqmonitor.errors import ConfigError
from dqmonitor.rules import BaseRule, get_rule_class

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class CompiledConfig:
    """Settings plus the rule objects built from them."""

    settings: Settings
    sources: list[SourceConfig]
    rules: list[BaseRule]


def load_application(path: str | Path) -> CompiledConfig:
    """Parse YAML, build rules, and validate alert settings."""

    settings = parse_settings(path)
    compiled = compile_settings(settings)
    # Imported here because the alerting module imports config models.
    from dqmonitor.alerting import validate_alerting_config

    validate_alerting_config(compiled.settings.alerting)
    return compiled


def parse_settings(path: str | Path) -> Settings:
    """Read YAML and apply environment overrides. Does not build rules."""

    config_path = Path(path).expanduser()
    if not config_path.is_file():
        raise ConfigError(f"Config file not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {config_path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("The config root must be a mapping of sections")
    try:
        settings = Settings.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from exc
    _apply_env_overrides(settings)
    return settings


def compile_settings(settings: Settings) -> CompiledConfig:
    """Turn rule entries into rule objects and check cross-references."""

    source_names = [source.name for source in settings.sources]
    if len(source_names) != len(set(source_names)):
        raise ConfigError("Source names must be unique")
    rule_names = [rule.name for rule in settings.rules]
    if len(rule_names) != len(set(rule_names)):
        raise ConfigError("Rule names must be unique")

    known_sources = set(source_names)
    rules: list[BaseRule] = []
    for rule_cfg in settings.rules:
        if rule_cfg.source not in known_sources:
            raise ConfigError(
                f"Rule '{rule_cfg.name}' references unknown source '{rule_cfg.source}'"
            )
        rule_cls = get_rule_class(rule_cfg.type)
        rules.append(
            rule_cls(
                name=rule_cfg.name,
                source=rule_cfg.source,
                enabled=rule_cfg.enabled,
                params=rule_cfg.parameters(),
            )
        )

    for source in settings.sources:
        table = getattr(source, "table", None)
        if table is not None and not _TABLE_NAME.match(table):
            raise ConfigError(
                f"Source '{source.name}' table name '{table}' is not a valid identifier"
            )

    return CompiledConfig(
        settings=settings,
        sources=list(settings.sources),
        rules=rules,
    )


def _apply_env_overrides(settings: Settings) -> None:
    database_url = os.environ.get("DQ_DATABASE_URL")
    if database_url:
        settings.storage.url = database_url.strip()
    level = os.environ.get("LOG_LEVEL")
    if level:
        normalized = level.strip().upper()
        if normalized not in _LOG_LEVELS:
            raise ConfigError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        settings.logging.level = normalized


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["Invalid configuration:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"- {location}: {error['msg']}")
    return "\n".join(lines)
