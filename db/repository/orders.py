"""Order repository (MongoDB ``orders`` collection).

Storage/retrieval only - this is NOT an order-management API.  Document
fields: order_id, customer_id, status, amount, currency, items, payment_id,
delivered_at, created_at, updated_at, status_history.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db import mongo
from db.models import ValidationError, build_order, days_since, utc_now_iso

COLLECTION = mongo.ORDERS
_IMMUTABLE = {"order_id", "customer_id", "created_at", "status", "status_history"}


def store_order(order: Dict[str, Any]) -> Dict[str, Any]:
    """Store an order record (required: order_id, customer_id, status)."""
    return mongo.insert_document(COLLECTION, build_order(order))


def get_order(order_id: str) -> Optional[Dict[str, Any]]:
    """Return the order document or ``None``."""
    return mongo.get_document(COLLECTION, {"order_id": order_id})


def get_orders_for_customer(
    customer_id: str, status: Optional[str] = None, limit: int = 50
) -> List[Dict[str, Any]]:
    """Return a customer's orders, newest first (optionally by status)."""
    query: Dict[str, Any] = {"customer_id": customer_id}
    if status:
        query["status"] = status
    return mongo.find_documents(COLLECTION, query, sort=[("created_at", -1)], limit=limit)


def update_order_status(order_id: str, status: str, note: Optional[str] = None) -> Dict[str, Any]:
    """Set the order status and append it to ``status_history``.

    Returns the updated order; raises ``RecordNotFoundError`` if unknown.
    """
    if not status:
        raise ValidationError("status is required.")
    entry = {"status": status, "at": utc_now_iso(), "note": note}
    return mongo.update_document(
        COLLECTION, {"order_id": order_id}, {"status": status},
        push_fields={"status_history": entry},
    )


def update_order(order_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update non-status fields (use ``update_order_status`` for status)."""
    blocked = _IMMUTABLE.intersection(updates)
    if blocked:
        raise ValidationError(f"Cannot update field(s) here: {', '.join(sorted(blocked))}")
    return mongo.update_document(COLLECTION, {"order_id": order_id}, updates)


def get_days_since_delivery(order_id: str) -> Optional[int]:
    """Whole days since delivery, or ``None`` if unknown/undelivered."""
    order = get_order(order_id)
    return days_since(order.get("delivered_at")) if order else None


def delete_order(order_id: str) -> bool:
    """Delete an order; True if one was removed."""
    return mongo.delete_document(COLLECTION, {"order_id": order_id})
