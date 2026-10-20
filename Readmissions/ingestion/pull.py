"""
Reading a hospital's own database: one query per table, only what changed.

Standalone on purpose - no MongoDB, nothing from api/ - so the same code runs
in two places:

    the worker        api/ingest_connections.run_pull, on the schedule
                      (ingestion/scheduler.py) or from Run now
    an on-site agent  ingestion/agent.py, inside a hospital that will not let
                      any server connect in; it pushes what it reads to
                      /ingest/v1/files

Each query is the hospital's own SQL, written once on the Data page, returning
rows for one of our tables (ingestion/profile.TABLES) in the hospital's own
column names - the hospital's data profile maps them as it does for files. A
query that mentions :since gets the time of the last successful pull, less an
overlap, so a nightly run reads one day's changes instead of everything:

    SELECT * FROM adt.discharges WHERE last_updated >= :since

Every value comes back as text, exactly as a CSV cell would, so files and
pulls take the same path from here on.

Connections are opened read-only where the database supports it (PostgreSQL,
MySQL, Oracle); the hospital's database user should be read-only in any case.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Optional

DRIVERS = {"postgresql": "postgresql+psycopg", "mssql": "mssql+pymssql",
           "mysql": "mysql+pymysql", "oracle": "oracle+oracledb"}
LABELS = {"postgresql": "PostgreSQL", "mssql": "SQL Server", "mysql": "MySQL / MariaDB", "oracle": "Oracle"}
DEFAULT_PORTS = {"postgresql": 5432, "mssql": 1433, "mysql": 3306, "oracle": 1521}
CONNECT_SECONDS = 15
MAX_ROWS = 500_000          # per table per run; more is refused rather than half-read

_READ_ONLY = {"postgresql": "SET TRANSACTION READ ONLY", "mysql": "SET SESSION TRANSACTION READ ONLY",
              "oracle": "SET TRANSACTION READ ONLY"}
_CONNECT_ARGS = {"postgresql": {"connect_timeout": CONNECT_SECONDS},
                 "mysql": {"connect_timeout": CONNECT_SECONDS},
                 "mssql": {"login_timeout": CONNECT_SECONDS},
                 "oracle": {"tcp_connect_timeout": CONNECT_SECONDS}}


class PullError(Exception):
    """Something the person setting the connection up can act on."""


def build_url(dialect: str, host: str, port: Optional[int], database: str, username: str, password: str):
    """A SQLAlchemy URL for one of the four supported databases. For Oracle,
    `database` is the service name."""
    from sqlalchemy.engine import URL
    if dialect not in DRIVERS:
        raise PullError(f"Unsupported database '{dialect}'; use one of {', '.join(LABELS.values())}")
    port = int(port) if port else DEFAULT_PORTS[dialect]
    if dialect == "oracle":
        return URL.create(DRIVERS[dialect], username=username, password=password, host=host, port=port,
                          query={"service_name": database})
    return URL.create(DRIVERS[dialect], username=username, password=password, host=host, port=port,
                      database=database)


def check_query(sql: str) -> Optional[str]:
    """Why a query is not acceptable, or None. One statement that reads."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        return "the query is empty"
    if ";" in text:
        return "one statement only"
    if not re.match(r"^(select|with)\b", text, re.IGNORECASE):
        return "the query must start with SELECT or WITH"
    return None


def _cell(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _engine(url):
    from sqlalchemy import create_engine
    dialect = next((d for d, driver in DRIVERS.items() if str(url).startswith(driver)), None)
    return create_engine(url, connect_args=_CONNECT_ARGS.get(dialect, {}), pool_pre_ping=True), dialect


def _run(conn, sql: str, since, limit: Optional[int]):
    from sqlalchemy import text
    result = conn.execute(text(sql.strip().rstrip(";")), {"since": since} if ":since" in sql else {})
    columns = [str(c) for c in result.keys()]
    rows = result.fetchmany(limit) if limit else result.fetchmany(MAX_ROWS + 1)
    if not limit and len(rows) > MAX_ROWS:
        raise PullError(f"more than {MAX_ROWS:,} rows; narrow the query with :since")
    return columns, [{c: v for c, v in zip(columns, (_cell(x) for x in row)) if v not in (None, "")}
                     for row in rows]


def fetch(url, queries: dict, since, limit: Optional[int] = None) -> dict:
    """{table: rows} for every query, in one read-only session. Any failure
    raises PullError naming the table, and nothing is returned for any table,
    so a half-read night is never stored as if it were whole."""
    from sqlalchemy.exc import SQLAlchemyError
    engine, dialect = _engine(url)
    out = {}
    try:
        with engine.connect() as conn:
            with conn.begin():
                if dialect in _READ_ONLY:
                    conn.exec_driver_sql(_READ_ONLY[dialect])
                for table, sql in queries.items():
                    problem = check_query(sql)
                    if problem:
                        raise PullError(f"{table}: {problem}")
                    try:
                        out[table] = _run(conn, sql, since, limit)[1]
                    except SQLAlchemyError as exc:
                        raise PullError(f"{table}: {_short(exc)}") from exc
    except SQLAlchemyError as exc:
        raise PullError(f"could not connect: {_short(exc)}") from exc
    finally:
        engine.dispose()
    return out


def probe(url, queries: dict, since, sample: int = 5) -> dict:
    """Test a connection: each query run with `since`, its columns and up to
    `sample` rows - or what went wrong, per table."""
    from sqlalchemy.exc import SQLAlchemyError
    engine, dialect = _engine(url)
    report = {"connected": False, "tables": {}}
    try:
        with engine.connect() as conn:
            report["connected"] = True
            for table, sql in queries.items():
                problem = check_query(sql)
                if problem:
                    report["tables"][table] = {"ok": False, "error": problem}
                    continue
                try:
                    # One transaction per table, so one failing query does not
                    # leave the others reading from an aborted transaction.
                    with conn.begin():
                        if dialect in _READ_ONLY:
                            conn.exec_driver_sql(_READ_ONLY[dialect])
                        columns, rows = _run(conn, sql, since, sample)
                    report["tables"][table] = {"ok": True, "columns": columns, "sample": rows}
                except (SQLAlchemyError, PullError) as exc:
                    report["tables"][table] = {"ok": False, "error": _short(exc)}
    except SQLAlchemyError as exc:
        report["error"] = f"could not connect: {_short(exc)}"
    finally:
        engine.dispose()
    return report


def _short(exc) -> str:
    """The database's own message, without SQLAlchemy's wrapping, the SQL or the
    parameters - and so without any patient value a failing query quoted."""
    orig = getattr(exc, "orig", None)
    text = str(orig if orig is not None else exc).splitlines()[0]
    return text[:300]
