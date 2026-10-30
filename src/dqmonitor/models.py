"""SQLAlchemy model for stored check results."""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """Declarative base for the monitoring database."""


class CheckResultRow(Base):
    """One execution of one rule.

    The monitoring table is ``check_results``. Timestamps are naive UTC.
    """

    __tablename__ = "check_results"
    __table_args__ = (
        Index("ix_check_results_rule_time", "rule_name", "checked_at"),
        Index("ix_check_results_status_time", "status", "checked_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    rule_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    rule_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    details: Mapped[dict] = mapped_column(JSON, nullable=False)
