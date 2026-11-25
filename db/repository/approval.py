"""Approval repository (PostgreSQL ``approvals`` table).

Stores approval *requests* and *decisions* only; deciding whether something
needs approval is business logic owned by other roles.

Row fields: approval_id, run_id, customer_id, request_type, requested_by,
details (JSON), status, decided_by, decision_reason, requested_at, decided_at.
Status values used here: ``pending``, ``approved``, ``rejected``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db import postgre
from db.models import ConflictError, RecordNotFoundError, ValidationError, new_id

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
_DECISIONS = {"approved": APPROVED, "approve": APPROVED,
              "rejected": REJECTED, "reject": REJECTED, "denied": REJECTED}


def create_approval_request(
    run_id: str,
    request_type: str,
    details: Optional[Dict[str, Any]] = None,
    requested_by: Optional[str] = None,
    customer_id: Optional[str] = None,
    approval_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Store a new pending approval request and return it."""
    if not run_id or not request_type:
        raise ValidationError("run_id and request_type are required.")
    row = postgre.fetch_one(
        """INSERT INTO approvals
               (approval_id, run_id, customer_id, request_type, requested_by, details, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (approval_id or new_id("APR"), run_id, customer_id, request_type, requested_by,
         postgre.to_json(details), PENDING),
    )
    assert row is not None
    return row


def get_approval(approval_id: str) -> Optional[Dict[str, Any]]:
    """Return one approval row or ``None``."""
    return postgre.fetch_one("SELECT * FROM approvals WHERE approval_id = %s", (approval_id,))


def get_approvals_for_run(run_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return a run's approvals (oldest first), optionally by status."""
    sql, params = "SELECT * FROM approvals WHERE run_id = %s", [run_id]
    if status:
        sql += " AND status = %s"
        params.append(status)
    return postgre.fetch_all(sql + " ORDER BY requested_at ASC", params)


def get_pending_approvals(limit: int = 50) -> List[Dict[str, Any]]:
    """Return pending approvals across runs, oldest first."""
    return postgre.fetch_all(
        "SELECT * FROM approvals WHERE status = %s ORDER BY requested_at ASC LIMIT %s",
        (PENDING, int(limit)),
    )


def record_approval_decision(
    approval_id: str,
    decision: str,
    decided_by: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Store the decision (``approved`` / ``rejected``) for a pending request.

    Raises ``RecordNotFoundError`` for unknown ids and ``ConflictError`` if the
    request was already decided (decisions are never silently overwritten).
    """
    status = _DECISIONS.get(str(decision).strip().lower())
    if status is None:
        raise ValidationError("decision must be 'approved' or 'rejected'.")
    row = postgre.fetch_one(
        """UPDATE approvals
              SET status = %s, decided_by = %s, decision_reason = %s, decided_at = now()
            WHERE approval_id = %s AND status = %s
        RETURNING *""",
        (status, decided_by, reason, approval_id, PENDING),
    )
    if row is not None:
        return row
    if get_approval(approval_id) is None:
        raise RecordNotFoundError(f"Approval {approval_id!r} not found.")
    raise ConflictError(f"Approval {approval_id!r} has already been decided.")
