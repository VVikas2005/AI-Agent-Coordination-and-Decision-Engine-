"""PostgreSQL (psycopg2) connection pool, schema and decision_trace helpers.

Credentials come from environment variables (see ``.env.example``).  Either a
single DSN/URL or the individual parts may be supplied:

* ``POSTGRES_DSN`` or ``DATABASE_URL``
* ``POSTGRES_HOST`` (localhost), ``POSTGRES_PORT`` (5432),
  ``POSTGRES_DB`` (agent_engine), ``POSTGRES_USER``, ``POSTGRES_PASSWORD``
* ``POSTGRES_POOL_MAX`` (5), ``POSTGRES_CONNECT_TIMEOUT`` (5)

Tables created by ``init_schema()``: ``decision_trace``, ``approvals``,
``workflow_runs``.  Rows are returned as JSON-compatible dicts (timestamps as
ISO strings, Decimals as floats).

Run ``python -m db.postgre`` for a basic connection / schema / trace check.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Iterator, List, Optional, Sequence
from uuid import UUID

import psycopg2
from psycopg2 import errors as pg_errors
from psycopg2 import pool as pg_pool
from psycopg2.extras import Json, RealDictCursor

from db.models import (
    DatabaseConnectionError,
    DatabaseError,
    DuplicateRecordError,
    RecordNotFoundError,
    ValidationError,
    to_iso,
)

try:  # optional: load a local .env file if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)

_pool: Optional["pg_pool.ThreadedConnectionPool"] = None
_pool_lock = threading.Lock()

_SCHEMA_STATEMENTS: Sequence[str] = (
    """
    CREATE TABLE IF NOT EXISTS decision_trace (
        id       BIGSERIAL PRIMARY KEY,
        run_id   TEXT        NOT NULL,
        step     TEXT        NOT NULL,
        agent    TEXT        NOT NULL,
        "input"  JSONB       NOT NULL DEFAULT '{}'::jsonb,
        "output" JSONB       NOT NULL DEFAULT '{}'::jsonb,
        status   TEXT        NOT NULL DEFAULT 'success',
        "time"   TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_decision_trace_run ON decision_trace (run_id, id)",
    """
    CREATE TABLE IF NOT EXISTS approvals (
        approval_id     TEXT PRIMARY KEY,
        run_id          TEXT        NOT NULL,
        customer_id     TEXT,
        request_type    TEXT        NOT NULL,
        requested_by    TEXT,
        details         JSONB       NOT NULL DEFAULT '{}'::jsonb,
        status          TEXT        NOT NULL DEFAULT 'pending',
        decided_by      TEXT,
        decision_reason TEXT,
        requested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        decided_at      TIMESTAMPTZ
    )""",
    "CREATE INDEX IF NOT EXISTS idx_approvals_run ON approvals (run_id)",
    "CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals (status)",
    """
    CREATE TABLE IF NOT EXISTS workflow_runs (
        run_id       TEXT PRIMARY KEY,
        customer_id  TEXT,
        status       TEXT        NOT NULL DEFAULT 'created',
        current_step TEXT,
        context      JSONB       NOT NULL DEFAULT '{}'::jsonb,
        error        TEXT,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        completed_at TIMESTAMPTZ
    )""",
    "CREATE INDEX IF NOT EXISTS idx_workflow_runs_customer ON workflow_runs (customer_id)",
)

_TRACE_COLUMNS = 'id, run_id, step, agent, "input", "output", status, "time"'


# --------------------------------------------------------------------------
# Connection pool
# --------------------------------------------------------------------------
def _connection_kwargs() -> Dict[str, Any]:
    timeout = int(os.getenv("POSTGRES_CONNECT_TIMEOUT", "5"))
    dsn = os.getenv("POSTGRES_DSN") or os.getenv("DATABASE_URL")
    if dsn:
        return {"dsn": dsn, "connect_timeout": timeout}
    kwargs = {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "dbname": os.getenv("POSTGRES_DB", "agent_engine"),
        "user": os.getenv("POSTGRES_USER"),
        "password": os.getenv("POSTGRES_PASSWORD"),
        "connect_timeout": timeout,
    }
    return {k: v for k, v in kwargs.items() if v is not None}


def _get_pool() -> "pg_pool.ThreadedConnectionPool":
    global _pool
    with _pool_lock:
        if _pool is None:
            try:
                _pool = pg_pool.ThreadedConnectionPool(
                    1, int(os.getenv("POSTGRES_POOL_MAX", "5")), **_connection_kwargs()
                )
            except (psycopg2.Error, ValueError) as exc:
                raise DatabaseConnectionError(f"Cannot connect to PostgreSQL: {exc}") from exc
        return _pool


def close_pool() -> None:
    """Close all pooled connections."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None


@contextmanager
def _cursor() -> Iterator[Any]:
    """Yield a dict-cursor; commit on success, roll back on error."""
    pool = _get_pool()
    try:
        conn = pool.getconn()
    except psycopg2.Error as exc:
        raise DatabaseConnectionError(f"Cannot get PostgreSQL connection: {exc}") from exc
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except pg_errors.UniqueViolation as exc:
        conn.rollback()
        raise DuplicateRecordError(f"Duplicate record: {exc}") from exc
    except psycopg2.Error as exc:
        conn.rollback()
        raise DatabaseError(f"PostgreSQL error: {exc}") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn, close=bool(conn.closed))


# --------------------------------------------------------------------------
# Generic helpers (used by the repositories)
# --------------------------------------------------------------------------
def to_json(value: Any) -> Json:
    """Wrap a Python value for a JSONB parameter (datetimes etc. -> str)."""
    return Json({} if value is None else value, dumps=lambda o: json.dumps(o, default=str))


def _clean(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    out: Dict[str, Any] = {}
    for key, value in dict(row).items():
        if isinstance(value, datetime):
            value = to_iso(value)
        elif isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = float(value)
        elif isinstance(value, UUID):
            value = str(value)
        out[key] = value
    return out


def execute(query: str, params: Optional[Sequence[Any]] = None) -> int:
    """Run a statement; returns the affected row count."""
    with _cursor() as cur:
        cur.execute(query, params)
        return cur.rowcount


def fetch_one(query: str, params: Optional[Sequence[Any]] = None) -> Optional[Dict[str, Any]]:
    """Run a statement and return the first row (or None)."""
    with _cursor() as cur:
        cur.execute(query, params)
        return _clean(cur.fetchone()) if cur.description else None


def fetch_all(query: str, params: Optional[Sequence[Any]] = None) -> List[Dict[str, Any]]:
    """Run a statement and return all rows."""
    with _cursor() as cur:
        cur.execute(query, params)
        return [_clean(r) for r in cur.fetchall()] if cur.description else []


def ping() -> bool:
    """Return True if PostgreSQL answers ``SELECT 1``; False (and log) otherwise."""
    try:
        return fetch_one("SELECT 1 AS ok") == {"ok": 1}
    except DatabaseError as exc:
        logger.error("PostgreSQL ping failed: %s", exc)
        return False


def init_schema() -> None:
    """Create ``decision_trace``, ``approvals`` and ``workflow_runs`` (idempotent)."""
    with _cursor() as cur:
        for statement in _SCHEMA_STATEMENTS:
            cur.execute(statement)


# --------------------------------------------------------------------------
# decision_trace
# --------------------------------------------------------------------------
def insert_decision_trace(
    run_id: str,
    step: Any,
    agent: str,
    input_data: Any = None,
    output_data: Any = None,
    status: str = "success",
    time: Optional[Any] = None,
) -> Dict[str, Any]:
    """Insert one trace row and return it.

    ``step`` may be an int or a name (stored as text).  ``time`` (datetime or
    ISO string) defaults to the database's ``now()``.
    """
    if not run_id or not agent or step in (None, ""):
        raise ValidationError("run_id, step and agent are required.")
    row = fetch_one(
        f"""INSERT INTO decision_trace (run_id, step, agent, "input", "output", status, "time")
            VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now()))
            RETURNING {_TRACE_COLUMNS}""",
        (run_id, str(step), agent, to_json(input_data), to_json(output_data), status, time),
    )
    assert row is not None
    return row


def get_decision_traces(
    run_id: str,
    agent: Optional[str] = None,
    status: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return a run's traces in chronological order, optionally filtered."""
    sql = f"SELECT {_TRACE_COLUMNS} FROM decision_trace WHERE run_id = %s"
    params: List[Any] = [run_id]
    if agent:
        sql += " AND agent = %s"
        params.append(agent)
    if status:
        sql += " AND status = %s"
        params.append(status)
    sql += ' ORDER BY "time" ASC, id ASC'
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))
    return fetch_all(sql, params)


def update_trace_status(
    run_id: str,
    step: Any,
    status: str,
    output_data: Any = None,
    agent: Optional[str] = None,
) -> Dict[str, Any]:
    """Update status (and optionally output) of the latest matching trace row."""
    params: List[Any] = [status, None if output_data is None else to_json(output_data),
                         run_id, str(step)]
    agent_clause = ""
    if agent:
        agent_clause = " AND agent = %s"
        params.append(agent)
    row = fetch_one(
        f"""UPDATE decision_trace
               SET status = %s, "output" = COALESCE(%s::jsonb, "output")
             WHERE id = (SELECT id FROM decision_trace
                          WHERE run_id = %s AND step = %s{agent_clause}
                          ORDER BY id DESC LIMIT 1)
         RETURNING {_TRACE_COLUMNS}""",
        params,
    )
    if row is None:
        raise RecordNotFoundError(f"No trace for run_id={run_id!r}, step={step!r}")
    return row


def delete_decision_traces(run_id: str) -> int:
    """Delete all traces of a run (used to clean up sample/test data)."""
    return execute("DELETE FROM decision_trace WHERE run_id = %s", (run_id,))


if __name__ == "__main__":  # basic verification only
    import uuid

    logging.basicConfig(level=logging.INFO)
    ok = ping()
    print("PostgreSQL ping:", "OK" if ok else "FAILED")
    if ok:
        init_schema()
        rid = f"VERIFY-{uuid.uuid4().hex[:8]}"
        print("insert:", insert_decision_trace(rid, 1, "planning", {"q": "sample"}, {"ok": True}))
        print("update:", update_trace_status(rid, 1, "completed"))
        print("get   :", get_decision_traces(rid))
        print("cleanup rows deleted:", delete_decision_traces(rid))
