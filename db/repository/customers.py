"""Customer repository (MongoDB ``customers`` collection).

Document fields: customer_id, name, email, phone, abuse_flag, created_at,
updated_at (+ any extra fields supplied by the caller).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db import mongo
from db.models import ValidationError, build_customer

COLLECTION = mongo.CUSTOMERS
_IMMUTABLE = {"customer_id", "created_at"}


def insert_customer(customer: Dict[str, Any]) -> Dict[str, Any]:
    """Insert a customer (required: customer_id, name); returns stored doc.

    Raises ``DuplicateRecordError`` if the customer_id already exists.
    """
    return mongo.insert_document(COLLECTION, build_customer(customer))


def get_customer(customer_id: str) -> Optional[Dict[str, Any]]:
    """Return the customer document or ``None``."""
    return mongo.get_document(COLLECTION, {"customer_id": customer_id})


def get_customer_by_name(name: str) -> Optional[Dict[str, Any]]:
    """Return the first customer whose name matches exactly, or ``None``."""
    return mongo.get_document(COLLECTION, {"name": name})


def list_customers(limit: int = 50) -> List[Dict[str, Any]]:
    """Return up to ``limit`` customers, newest first."""
    return mongo.find_documents(COLLECTION, sort=[("created_at", -1)], limit=limit)


def update_customer(customer_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update fields of a customer and return the new document.

    ``customer_id`` and ``created_at`` cannot be changed.  Raises
    ``RecordNotFoundError`` if the customer does not exist.
    """
    blocked = _IMMUTABLE.intersection(updates)
    if blocked:
        raise ValidationError(f"Cannot update immutable field(s): {', '.join(sorted(blocked))}")
    if "abuse_flag" in updates:
        updates = {**updates, "abuse_flag": bool(updates["abuse_flag"])}
    return mongo.update_document(COLLECTION, {"customer_id": customer_id}, updates)


def delete_customer(customer_id: str) -> bool:
    """Delete a customer; True if one was removed."""
    return mongo.delete_document(COLLECTION, {"customer_id": customer_id})
