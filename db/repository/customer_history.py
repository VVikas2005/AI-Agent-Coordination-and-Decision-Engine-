"""Customer history repository (MongoDB ``customer_history`` collection).

Event document: event_id, customer_id, event_type (e.g. ``"return"``,
``"refund"``, ``"complaint"``), order_id, details (dict), created_at.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db import mongo
from db.models import build_history_event, days_ago_iso

COLLECTION = mongo.CUSTOMER_HISTORY
RETURN_EVENT_TYPE = "return"
DAYS_PER_MONTH = 30  # "6 months" == 180 days


def store_customer_history(event: Dict[str, Any]) -> Dict[str, Any]:
    """Store a history event (required: customer_id, event_type)."""
    return mongo.insert_document(COLLECTION, build_history_event(event))


def add_history_event(
    customer_id: str,
    event_type: str,
    order_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Convenience wrapper around ``store_customer_history``."""
    event: Dict[str, Any] = {"customer_id": customer_id, "event_type": event_type,
                             "order_id": order_id, "details": details or {}}
    if created_at:
        event["created_at"] = created_at
    return store_customer_history(event)


def get_customer_history(
    customer_id: str,
    event_type: Optional[str] = None,
    since_days: Optional[float] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return a customer's events, newest first (optionally filtered)."""
    query: Dict[str, Any] = {"customer_id": customer_id}
    if event_type:
        query["event_type"] = event_type
    if since_days is not None:
        query["created_at"] = {"$gte": days_ago_iso(since_days)}
    return mongo.find_documents(COLLECTION, query, sort=[("created_at", -1)], limit=limit)


def get_customer_activities(customer_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Return all recorded activities of a customer, newest first."""
    return get_customer_history(customer_id, limit=limit)


def get_previous_returns(customer_id: str, months: int = 6) -> List[Dict[str, Any]]:
    """Return the customer's return events within the last ``months`` months."""
    return get_customer_history(
        customer_id, event_type=RETURN_EVENT_TYPE, since_days=months * DAYS_PER_MONTH, limit=0
    )


def count_previous_returns(customer_id: str, months: int = 6) -> int:
    """Number of return events within the last ``months`` months."""
    return mongo.count_documents(COLLECTION, {
        "customer_id": customer_id,
        "event_type": RETURN_EVENT_TYPE,
        "created_at": {"$gte": days_ago_iso(months * DAYS_PER_MONTH)},
    })
