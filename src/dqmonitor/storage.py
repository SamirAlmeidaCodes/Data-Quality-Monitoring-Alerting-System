"""Persist and query check results."""

import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import sessionmaker

from dqmonitor.models import Base, CheckResultRow


def utcnow() -> datetime:
    """Naive UTC timestamp truncated to seconds."""

    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def sqlite_file_url(path: Path) -> str:
    """Build a SQLAlchemy SQLite URL for an absolute file path."""

    return "sqlite:///" + path.resolve().as_posix()


def json_safe(value: Any) -> Any:
    """Convert rule details into plain JSON values."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return json_safe(item())
        except Exception:
            return str(value)
    return str(value)


@dataclass(frozen=True)
class StoredCheck:
    """A check result detached from the database session."""

    id: int
    checked_at: datetime
    rule_name: str
    rule_type: str
    source_name: str
    status: str
    summary: str
    details: dict[str, Any]


class Store:
    """SQLite (or other SQLAlchemy URL) storage for check results."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = _create_engine(url)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_tables(self) -> None:
        Base.metadata.create_all(self.engine)
        _restrict_sqlite_permissions(self.url)

    def dispose(self) -> None:
        self.engine.dispose()

    def save(
        self,
        *,
        checked_at: datetime,
        rule_name: str,
        rule_type: str,
        source_name: str,
        status: str,
        summary: str,
        details: dict[str, Any],
    ) -> StoredCheck:
        return self.save_many(
            [
                {
                    "checked_at": checked_at,
                    "rule_name": rule_name,
                    "rule_type": rule_type,
                    "source_name": source_name,
                    "status": status,
                    "summary": summary,
                    "details": details,
                }
            ]
        )[0]

    def save_many(self, checks: list[dict[str, Any]]) -> list[StoredCheck]:
        """Insert every result from one run and commit once."""

        if not checks:
            return []
        rows: list[CheckResultRow] = []
        for check in checks:
            status = check["status"]
            if status not in {"pass", "fail", "error"}:
                raise ValueError(f"Invalid status '{status}'")
            rows.append(
                CheckResultRow(
                    checked_at=check["checked_at"],
                    rule_name=check["rule_name"],
                    rule_type=check["rule_type"],
                    source_name=check["source_name"],
                    status=status,
                    summary=check["summary"],
                    details=json_safe(check["details"]),
                )
            )
        with self.Session() as session:
            session.add_all(rows)
            session.commit()
            stored = [_to_stored(row) for row in rows]
        _restrict_sqlite_permissions(self.url)
        return stored

    def latest_by_rule(self, rule_name: str) -> StoredCheck | None:
        return self.latest_for_rules([rule_name]).get(rule_name)

    def latest_for_rules(self, rule_names: list[str]) -> dict[str, StoredCheck]:
        """Latest stored result for each rule, using one query.

        Ordering matches ``checked_at`` descending, then ``id`` descending,
        so a later row with an older timestamp does not hide a newer one.
        """

        if not rule_names:
            return {}
        ranked = (
            select(
                CheckResultRow.id.label("id"),
                func.row_number()
                .over(
                    partition_by=CheckResultRow.rule_name,
                    order_by=[
                        CheckResultRow.checked_at.desc(),
                        CheckResultRow.id.desc(),
                    ],
                )
                .label("rank"),
            )
            .where(CheckResultRow.rule_name.in_(rule_names))
            .subquery()
        )
        stmt = select(CheckResultRow).join(ranked, CheckResultRow.id == ranked.c.id).where(
            ranked.c.rank == 1
        )
        with self.Session() as session:
            return {
                row.rule_name: _to_stored(row) for row in session.scalars(stmt).all()
            }

    def history(
        self,
        *,
        rule_name: str | None = None,
        status: str | None = None,
        source_name: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[StoredCheck]:
        stmt = select(CheckResultRow)
        if rule_name:
            stmt = stmt.where(CheckResultRow.rule_name == rule_name)
        if status:
            stmt = stmt.where(CheckResultRow.status == status)
        if source_name:
            stmt = stmt.where(CheckResultRow.source_name == source_name)
        if since is not None:
            stmt = stmt.where(CheckResultRow.checked_at >= since)
        if until is not None:
            stmt = stmt.where(CheckResultRow.checked_at <= until)
        stmt = stmt.order_by(CheckResultRow.checked_at.desc(), CheckResultRow.id.desc())
        stmt = stmt.offset(offset).limit(limit)
        with self.Session() as session:
            return [_to_stored(row) for row in session.scalars(stmt).all()]


def _to_stored(row: CheckResultRow) -> StoredCheck:
    details = row.details or {}
    return StoredCheck(
        id=row.id,
        checked_at=row.checked_at,
        rule_name=row.rule_name,
        rule_type=row.rule_type,
        source_name=row.source_name,
        status=row.status,
        summary=row.summary,
        details=dict(details),
    )


def _create_engine(url: str) -> Engine:
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()
            _restrict_sqlite_permissions(url)

    return engine


def _restrict_sqlite_permissions(url: str) -> None:
    """Keep a file database and its WAL sidecars readable only by the owner."""

    if not url.startswith("sqlite"):
        return
    try:
        parsed = make_url(url)
    except Exception:
        return
    database = parsed.database
    if not database or database == ":memory:":
        return
    path = Path(database)
    if not path.is_absolute():
        path = Path.cwd() / path
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if not candidate.is_file():
            continue
        try:
            os.chmod(candidate, 0o600)
        except OSError:
            continue
