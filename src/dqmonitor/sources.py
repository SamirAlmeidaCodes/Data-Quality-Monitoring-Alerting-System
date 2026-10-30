"""Load dataframes from a CSV folder or a SQLite table."""

import logging
import re
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url

from dqmonitor.config_models import CsvFolderSourceConfig, SourceConfig, SqliteSourceConfig
from dqmonitor.errors import SourceError

logger = logging.getLogger("dqmonitor.sources")

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_source(config: SourceConfig) -> pd.DataFrame:
    """Read one configured source into a dataframe.

    The frame's ``attrs['dq_meta']`` lists the files or table that were read
    so schema checks can see each input, not only the combined columns.
    """

    if isinstance(config, CsvFolderSourceConfig):
        return _load_csv(config)
    if isinstance(config, SqliteSourceConfig):
        return _load_sqlite(config)
    raise SourceError(f"Unsupported source type '{config.type}'")


def _load_csv(config: CsvFolderSourceConfig) -> pd.DataFrame:
    path = Path(config.path)
    if not path.is_absolute():
        path = Path.cwd() / path

    if path.is_file():
        files = [path]
        mode = "file"
    elif path.is_dir():
        files = sorted(item for item in path.glob(config.pattern) if item.is_file())
        mode = config.mode
        if mode == "latest" and files:
            files = [max(files, key=lambda item: (item.stat().st_mtime_ns, item.name))]
    else:
        raise SourceError(f"Source path does not exist: {path}")

    if not files:
        raise SourceError(f"No CSV files matching '{config.pattern}' in {path}")

    frames: list[pd.DataFrame] = []
    meta_files: list[dict] = []
    for file in files:
        try:
            frame = pd.read_csv(file, encoding=config.encoding)
        except Exception as exc:
            raise SourceError(f"Could not read {file.name}: {exc}") from exc
        frames.append(frame)
        meta_files.append(
            {
                "name": file.name,
                "path": str(file),
                "rows": int(len(frame)),
                "columns": [str(column) for column in frame.columns],
            }
        )
        logger.debug("Read %s rows from %s", len(frame), file)

    # concat drops attrs, so attach metadata after the frames are combined.
    dataset = pd.concat(frames, ignore_index=True)
    dataset.attrs["dq_meta"] = {"mode": mode, "files": meta_files}
    return dataset


def _load_sqlite(config: SqliteSourceConfig) -> pd.DataFrame:
    if not _IDENTIFIER.match(config.table):
        raise SourceError(
            f"Table name '{config.table}' is not a valid identifier"
        )
    _require_sqlite_file(config.url)
    query = text(f'SELECT * FROM "{config.table}"')
    engine = create_engine(config.url)
    try:
        with engine.connect() as connection:
            dataset = pd.read_sql_query(query, connection)
    except Exception as exc:
        raise SourceError(
            f"Could not read table '{config.table}' from {config.url}: {exc}"
        ) from exc
    finally:
        engine.dispose()

    dataset.attrs["dq_meta"] = {
        "mode": "sqlite",
        "files": [
            {
                "name": config.table,
                "path": config.url,
                "rows": int(len(dataset)),
                "columns": [str(column) for column in dataset.columns],
            }
        ],
    }
    return dataset


def _require_sqlite_file(url: str) -> None:
    """Refuse to open a missing file database.

    SQLAlchemy would otherwise create an empty file and then fail on the
    missing table, leaving a stray database behind.
    """

    try:
        parsed = make_url(url)
    except Exception as exc:
        raise SourceError(f"Invalid SQLite URL '{url}': {exc}") from exc
    if not parsed.drivername.startswith("sqlite"):
        raise SourceError(f"Source URL is not a SQLite URL: {url}")
    database = parsed.database
    if database in {None, "", ":memory:"}:
        return
    db_path = Path(database)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    if not db_path.is_file():
        raise SourceError(f"SQLite database does not exist: {db_path}")
