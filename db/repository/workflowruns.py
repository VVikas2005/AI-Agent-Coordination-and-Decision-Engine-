"""Workflow-run repository (PostgreSQL ``workflow_runs`` table).

Row fields: run_id, customer_id, status, current_step, context (JSON), error,
created_at, updated_at, completed_at.  Status values are free text so the
Agent Builder can define the vocabulary; ``completed``, ``failed`` and
``cancelled`` are treated as terminal and set ``completed_at``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db import postgre
from db.models import RecordNotFoundError, ValidationError, new_id

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def create_workflow_run(
    customer_id: Optional[str] = None,
    run_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    status: str = "created",
) -> Dict[str, Any]:
    """Create a workflow execution record (generates ``run_id`` if omitted).

    Raises ``DuplicateRecordError`` if ``run_id`` already exists.
    """
    row = postgre.fetch_one(
        """INSERT INTO workflow_runs (run_id, customer_id, status, context)
           VALUES (%s, %s, %s, %s) RETURNING *""",
        (run_id or new_id("RUN"), customer_id, status, postgre.to_json(context)),
    )
    assert row is not None
    return row


def get_workflow_run(run_id: str) -> Optional[Dict[str, Any]]:
    """Return one workflow record or ``None``."""
    return postgre.fetch_one("SELECT * FROM workflow_runs WHERE run_id = %s", (run_id,))


def list_workflow_runs(
    customer_id: Optional[str] = None, status: Optional[str] = None, limit: int = 50
) -> List[Dict[str, Any]]:
    """Return workflow records, newest first (optionally filtered)."""
    clauses, params = [], []
    if customer_id:
        clauses.append("customer_id = %s")
        params.append(customer_id)
    if status:
        clauses.append("status = %s")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(int(limit))
    return postgre.fetch_all(
        f"SELECT * FROM workflow_runs {where} ORDER BY created_at DESC LIMIT %s", params
    )


def update_workflow_status(
    run_id: str,
    status: str,
    current_step: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Update status (and optionally current_step / error); returns the row.

    Raises ``RecordNotFoundError`` for unknown runs.
    """
    if not status:
        raise ValidationError("status is required.")
    row = postgre.fetch_one(
        """UPDATE workflow_runs
              SET status = %s,
                  current_step = COALESCE(%s, current_step),
                  error = COALESCE(%s, error),
                  updated_at = now(),
                  completed_at = CASE WHEN %s THEN now() ELSE completed_at END
            WHERE run_id = %s
        RETURNING *""",
        (status, current_step, error, status.lower() in TERMINAL_STATUSES, run_id),
    )
    if row is None:
        raise RecordNotFoundError(f"Workflow run {run_id!r} not found.")
    return row


def update_workflow_context(run_id: str, context_patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``context_patch`` into the run's context (top-level keys)."""
    row = postgre.fetch_one(
        """UPDATE workflow_runs
              SET context = context || %s::jsonb, updated_at = now()
            WHERE run_id = %s RETURNING *""",
        (postgre.to_json(context_patch), run_id),
    )
    if row is None:
        raise RecordNotFoundError(f"Workflow run {run_id!r} not found.")
    return row
