"""Decision-trace repository (PostgreSQL ``decision_trace`` table).

Thin, agent-friendly wrapper over ``db.postgre``.  Row fields: id, run_id,
step, agent, input, output, status, time.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db import postgre


def record_trace(
    run_id: str,
    step: Any,
    agent: str,
    input_data: Any = None,
    output_data: Any = None,
    status: str = "success",
) -> Dict[str, Any]:
    """Record one agent decision step and return the stored row."""
    return postgre.insert_decision_trace(run_id, step, agent, input_data, output_data, status)


def get_traces(
    run_id: str,
    agent: Optional[str] = None,
    status: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return a run's traces in chronological order."""
    return postgre.get_decision_traces(run_id, agent=agent, status=status, limit=limit)


def get_latest_trace(run_id: str) -> Optional[Dict[str, Any]]:
    """Return the most recent trace of a run, or ``None``."""
    return postgre.fetch_one(
        f"SELECT {postgre._TRACE_COLUMNS} FROM decision_trace "
        "WHERE run_id = %s ORDER BY id DESC LIMIT 1",
        (run_id,),
    )


def update_trace_status(
    run_id: str, step: Any, status: str, output_data: Any = None, agent: Optional[str] = None
) -> Dict[str, Any]:
    """Update status (and optionally output) of a recorded step."""
    return postgre.update_trace_status(run_id, step, status, output_data, agent)
