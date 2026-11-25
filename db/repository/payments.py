"""Payment repository (MongoDB ``payments`` collection).

Storage/retrieval only - this is NOT a payment API.  Document fields:
payment_id, order_id, customer_id, amount, currency, status, method,
created_at, updated_at, status_history.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db import mongo
from db.models import ValidationError, build_payment, utc_now_iso

COLLECTION = mongo.PAYMENTS


def store_payment(payment: Dict[str, Any]) -> Dict[str, Any]:
    """Store a payment record (required: payment_id, order_id, customer_id,
    amount, status)."""
    return mongo.insert_document(COLLECTION, build_payment(payment))


def get_payment(payment_id: str) -> Optional[Dict[str, Any]]:
    """Return the payment document or ``None``."""
    return mongo.get_document(COLLECTION, {"payment_id": payment_id})


def get_payments_for_order(order_id: str) -> List[Dict[str, Any]]:
    """Return all payments recorded against an order, newest first."""
    return mongo.find_documents(COLLECTION, {"order_id": order_id}, sort=[("created_at", -1)])


def get_payments_for_customer(customer_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Return a customer's payments, newest first."""
    return mongo.find_documents(
        COLLECTION, {"customer_id": customer_id}, sort=[("created_at", -1)], limit=limit
    )


def update_payment_status(
    payment_id: str, status: str, note: Optional[str] = None
) -> Dict[str, Any]:
    """Set the payment status and append it to ``status_history``."""
    if not status:
        raise ValidationError("status is required.")
    entry = {"status": status, "at": utc_now_iso(), "note": note}
    return mongo.update_document(
        COLLECTION, {"payment_id": payment_id}, {"status": status},
        push_fields={"status_history": entry},
    )


def delete_payment(payment_id: str) -> bool:
    """Delete a payment record; True if one was removed."""
    return mongo.delete_document(COLLECTION, {"payment_id": payment_id})
