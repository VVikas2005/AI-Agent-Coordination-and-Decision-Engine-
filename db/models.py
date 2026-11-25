"""Shared data contracts for the Memory & Database layer (Role 5).

This module has NO project imports, so every other module can import it
without risking circular imports.  It contains:

* the exception hierarchy used by all db/ and memory/ modules,
* timestamp / id helpers (all timestamps are fixed-format UTC ISO-8601
  strings so they are JSON-compatible and sort lexicographically),
* ``build_*`` functions that validate a plain ``dict`` and fill defaults for
  each MongoDB document type,
* ``sample_rahul_scenario()`` - safe sample data for the Rahul Verma case.

All documents are plain JSON-compatible dicts.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------
class DatabaseError(Exception):
    """Base class for every database / persistence failure."""


class DatabaseConnectionError(DatabaseError):
    """The database could not be reached or is misconfigured."""


class DuplicateRecordError(DatabaseError):
    """A record with the same unique key already exists."""


class RecordNotFoundError(DatabaseError):
    """The requested record does not exist."""


class ConflictError(DatabaseError):
    """The operation conflicts with the record's current state."""


class ValidationError(ValueError):
    """Caller supplied invalid or incomplete data."""


# --------------------------------------------------------------------------
# Time / id helpers
# --------------------------------------------------------------------------
def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    """Format a datetime as a fixed-width UTC ISO string (``...Z``)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime(ISO_FORMAT)


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO string."""
    return to_iso(utc_now())


def days_ago_iso(days: float, now: Optional[datetime] = None) -> str:
    """Return the ISO timestamp ``days`` days before ``now`` (default: now)."""
    return to_iso((now or utc_now()) - timedelta(days=days))


def parse_iso(value: Union[str, datetime]) -> datetime:
    """Parse an ISO string (or pass through a datetime) into aware UTC."""
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def days_since(value: Optional[Union[str, datetime]]) -> Optional[int]:
    """Whole days elapsed since ``value``; ``None`` if value is empty."""
    if not value:
        return None
    return (utc_now() - parse_iso(value)).days


def new_id(prefix: str) -> str:
    """Generate a short unique id such as ``APR-3F9A1C2B7D``."""
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------
def _require(data: Dict[str, Any], *fields: str) -> None:
    if not isinstance(data, dict):
        raise ValidationError("Expected a dict.")
    missing = [f for f in fields if data.get(f) in (None, "")]
    if missing:
        raise ValidationError(f"Missing required field(s): {', '.join(missing)}")


def _lower_list(values: Optional[List[str]]) -> List[str]:
    return [str(v).strip().lower() for v in (values or []) if str(v).strip()]


# --------------------------------------------------------------------------
# Document builders  (each returns a NEW dict; unknown fields are preserved)
# --------------------------------------------------------------------------
def build_customer(data: Dict[str, Any]) -> Dict[str, Any]:
    """``customers`` document. Required: customer_id, name."""
    _require(data, "customer_id", "name")
    now = utc_now_iso()
    doc = dict(data)
    doc.setdefault("email", None)
    doc.setdefault("phone", None)
    doc["abuse_flag"] = bool(doc.get("abuse_flag", False))
    doc.setdefault("created_at", now)
    doc.setdefault("updated_at", now)
    return doc


def build_order(data: Dict[str, Any]) -> Dict[str, Any]:
    """``orders`` document. Required: order_id, customer_id, status."""
    _require(data, "order_id", "customer_id", "status")
    now = utc_now_iso()
    doc = dict(data)
    doc.setdefault("amount", None)
    doc.setdefault("currency", "INR")
    doc.setdefault("items", [])
    doc.setdefault("payment_id", None)
    doc.setdefault("delivered_at", None)
    doc.setdefault("created_at", now)
    doc.setdefault("updated_at", now)
    doc.setdefault(
        "status_history",
        [{"status": doc["status"], "at": doc["created_at"], "note": "created"}],
    )
    return doc


def build_payment(data: Dict[str, Any]) -> Dict[str, Any]:
    """``payments`` document. Required: payment_id, order_id, customer_id,
    amount (number), status."""
    _require(data, "payment_id", "order_id", "customer_id", "amount", "status")
    amount = data["amount"]
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise ValidationError("amount must be a number.")
    now = utc_now_iso()
    doc = dict(data)
    doc.setdefault("currency", "INR")
    doc.setdefault("method", None)
    doc.setdefault("created_at", now)
    doc.setdefault("updated_at", now)
    doc.setdefault(
        "status_history",
        [{"status": doc["status"], "at": doc["created_at"], "note": "created"}],
    )
    return doc


def build_policy(data: Dict[str, Any]) -> Dict[str, Any]:
    """``policies`` document. Required: policy_id, title, content."""
    _require(data, "policy_id", "title", "content")
    now = utc_now_iso()
    doc = dict(data)
    doc["keywords"] = _lower_list(doc.get("keywords"))
    doc.setdefault("category", "general")
    doc.setdefault("version", "1")
    doc["active"] = bool(doc.get("active", True))
    doc.setdefault("created_at", now)
    doc.setdefault("updated_at", now)
    return doc


def build_history_event(data: Dict[str, Any]) -> Dict[str, Any]:
    """``customer_history`` document. Required: customer_id, event_type."""
    _require(data, "customer_id", "event_type")
    doc = dict(data)
    doc.setdefault("event_id", new_id("EVT"))
    doc.setdefault("order_id", None)
    doc.setdefault("details", {})
    doc.setdefault("created_at", utc_now_iso())
    return doc


def build_chat_message(data: Dict[str, Any]) -> Dict[str, Any]:
    """``chat_history`` document. Required: customer_id, run_id, role, content."""
    _require(data, "customer_id", "run_id", "role", "content")
    doc = dict(data)
    doc.setdefault("message_id", new_id("MSG"))
    doc.setdefault("agent", None)
    doc.setdefault("metadata", {})
    doc.setdefault("created_at", utc_now_iso())
    return doc


def build_past_case(data: Dict[str, Any]) -> Dict[str, Any]:
    """``past_cases`` document. Required: customer_id, issue_type, summary."""
    _require(data, "customer_id", "issue_type", "summary")
    doc = dict(data)
    doc.setdefault("case_id", new_id("CASE"))
    doc.setdefault("run_id", None)
    doc.setdefault("decision", None)
    doc.setdefault("outcome", None)
    doc["order_ids"] = list(doc.get("order_ids") or [])
    doc["tags"] = _lower_list(doc.get("tags"))
    doc.setdefault("created_at", utc_now_iso())
    return doc


# --------------------------------------------------------------------------
# Sample data - Rahul Verma scenario
# --------------------------------------------------------------------------
def sample_rahul_scenario(now: Optional[datetime] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Return sample documents for the Rahul Verma scenario, keyed by collection.

    Ids and the policy text are PLACEHOLDERS for local verification; replace
    them with the team's real data / official policy wording.
    """
    now = now or utc_now()
    sample = {"is_sample": True}

    customers = [build_customer({
        "customer_id": "CUST-1001", "name": "Rahul Verma",
        "email": "rahul.verma@example.com", "abuse_flag": False, **sample})]

    orders = [
        build_order({  # payment issue
            "order_id": "ORD-5092", "customer_id": "CUST-1001", "status": "Failed",
            "amount": 1499, "currency": "INR", "payment_id": "PAY-5092",
            "created_at": to_iso(now - timedelta(days=1)), **sample}),
        build_order({  # product issue: delivered 4 days ago
            "order_id": "ORD-4871", "customer_id": "CUST-1001", "status": "Delivered",
            "items": [{"name": "Wireless Earbuds", "quantity": 1}],
            "delivered_at": to_iso(now - timedelta(days=4)),
            "created_at": to_iso(now - timedelta(days=9)), **sample}),
        build_order({  # earlier order that was returned
            "order_id": "ORD-3310", "customer_id": "CUST-1001", "status": "Returned",
            "created_at": to_iso(now - timedelta(days=75)), **sample}),
    ]

    payments = [build_payment({
        "payment_id": "PAY-5092", "order_id": "ORD-5092", "customer_id": "CUST-1001",
        "amount": 1499, "currency": "INR", "status": "Deducted",
        "created_at": to_iso(now - timedelta(days=1)), **sample})]

    customer_history = [build_history_event({  # 1 return in last 6 months
        "event_id": "EVT-SEED-001", "customer_id": "CUST-1001", "event_type": "return",
        "order_id": "ORD-3310", "details": {"status": "completed"},
        "created_at": to_iso(now - timedelta(days=60)), **sample})]

    policies = [
        build_policy({
            "policy_id": "POL-PAY-001", "title": "Failed order payment deduction",
            "category": "payment",
            "keywords": ["refund", "payment", "deducted", "failed", "failed order",
                         "double charge", "debited"],
            "content": "[SAMPLE - replace with official policy] Amounts deducted for an "
                       "order in Failed status are reviewed for refund to the original "
                       "payment method.", **sample}),
        build_policy({
            "policy_id": "POL-RET-001", "title": "Defective product return or replacement",
            "category": "returns",
            "keywords": ["defective", "return", "replacement", "earbud", "not charging",
                         "warranty", "delivered", "damaged"],
            "content": "[SAMPLE - replace with official policy] Products reported "
                       "defective shortly after delivery are eligible for a return or "
                       "replacement review.", **sample}),
    ]

    return {"customers": customers, "orders": orders, "payments": payments,
            "customer_history": customer_history, "policies": policies}
