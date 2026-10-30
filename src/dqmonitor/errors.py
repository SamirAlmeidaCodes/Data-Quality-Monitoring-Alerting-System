"""Errors raised by configuration, sources, and alert delivery."""


class DQError(Exception):
    """Base error for the data quality monitor."""


class ConfigError(DQError):
    """The YAML configuration is invalid."""


class SourceError(DQError):
    """A data source could not be read."""


class AlertDeliveryError(DQError):
    """An alert sink could not deliver a message."""
