"""MongoDB (PyMongo) connection, collection setup and generic CRUD helpers.

Credentials come from environment variables (see ``.env.example``):

* ``MONGO_URI``          - default ``mongodb://localhost:27017``
* ``MONGO_DB_NAME``      - default ``agent_engine``
* ``MONGO_USERNAME`` / ``MONGO_PASSWORD`` / ``MONGO_AUTH_SOURCE`` - optional
* ``MONGO_TIMEOUT_MS``   - server selection timeout, default 5000

Every returned document is a plain JSON-compatible ``dict`` with ``_id``
removed.  All driver errors are translated to ``db.models`` exceptions.

Run ``python -m db.mongo`` for a basic connection / init / sample-data check.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from db.models import (
    DatabaseConnectionError,
    DatabaseError,
    DuplicateRecordError,
    RecordNotFoundError,
    ValidationError,
    utc_now_iso,
)

try:  # optional: load a local .env file if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)

# Collection names ----------------------------------------------------------
CUSTOMERS = "customers"
ORDERS = "orders"
PAYMENTS = "payments"
POLICIES = "policies"
CUSTOMER_HISTORY = "customer_history"
CHAT_HISTORY = "chat_history"
PAST_CASES = "past_cases"

COLLECTIONS: Tuple[str, ...] = (
    CUSTOMERS, ORDERS, PAYMENTS, POLICIES, CUSTOMER_HISTORY, CHAT_HISTORY, PAST_CASES,
)

# (keys, unique) per collection ---------------------------------------------
_INDEXES: Dict[str, List[Tuple[List[Tuple[str, int]], bool]]] = {
    CUSTOMERS: [([("customer_id", ASCENDING)], True), ([("name", ASCENDING)], False)],
    ORDERS: [([("order_id", ASCENDING)], True),
             ([("customer_id", ASCENDING), ("created_at", DESCENDING)], False)],
    PAYMENTS: [([("payment_id", ASCENDING)], True), ([("order_id", ASCENDING)], False),
               ([("customer_id", ASCENDING), ("created_at", DESCENDING)], False)],
    POLICIES: [([("policy_id", ASCENDING)], True), ([("category", ASCENDING)], False)],
    CUSTOMER_HISTORY: [([("event_id", ASCENDING)], True),
                       ([("customer_id", ASCENDING), ("created_at", DESCENDING)], False),
                       ([("event_type", ASCENDING)], False)],
    CHAT_HISTORY: [([("message_id", ASCENDING)], True),
                   ([("customer_id", ASCENDING), ("run_id", ASCENDING),
                     ("created_at", ASCENDING)], False)],
    PAST_CASES: [([("case_id", ASCENDING)], True), ([("customer_id", ASCENDING)], False),
                 ([("issue_type", ASCENDING)], False)],
}

_NO_ID = {"_id": 0}
_client: Optional[MongoClient] = None
_lock = threading.Lock()


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------
def get_db_name() -> str:
    """Name of the MongoDB database in use."""
    return os.getenv("MONGO_DB_NAME", "agent_engine")


def get_client() -> MongoClient:
    """Return the shared ``MongoClient`` (created lazily, thread-safe)."""
    global _client
    with _lock:
        if _client is None:
            kwargs: Dict[str, Any] = {
                "serverSelectionTimeoutMS": int(os.getenv("MONGO_TIMEOUT_MS", "5000"))
            }
            username = os.getenv("MONGO_USERNAME")
            if username:
                kwargs.update(
                    username=username,
                    password=os.getenv("MONGO_PASSWORD"),
                    authSource=os.getenv("MONGO_AUTH_SOURCE", "admin"),
                )
            try:
                _client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), **kwargs)
            except (PyMongoError, ValueError) as exc:
                raise DatabaseConnectionError(f"Invalid MongoDB configuration: {exc}") from exc
        return _client


def get_database():
    """Return the configured ``pymongo.database.Database``."""
    return get_client()[get_db_name()]


def get_collection(name: str):
    """Return a collection handle; ``name`` must be one of ``COLLECTIONS``."""
    if name not in COLLECTIONS:
        raise ValidationError(f"Unknown collection '{name}'. Valid: {', '.join(COLLECTIONS)}")
    return get_database()[name]


def ping() -> bool:
    """Return True if the MongoDB server answers; False (and log) otherwise."""
    try:
        get_client().admin.command("ping")
        return True
    except (PyMongoError, DatabaseError) as exc:
        logger.error("MongoDB ping failed: %s", exc)
        return False


def close_client() -> None:
    """Close the shared client (mainly for tests / clean shutdown)."""
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None


def init_collections() -> Dict[str, str]:
    """Create every collection and its indexes if missing (idempotent).

    Returns ``{collection: "created" | "exists"}``.
    """
    result: Dict[str, str] = {}
    try:
        db = get_database()
        existing = set(db.list_collection_names())
        for name in COLLECTIONS:
            if name not in existing:
                db.create_collection(name)
                result[name] = "created"
            else:
                result[name] = "exists"
            for keys, unique in _INDEXES.get(name, []):
                db[name].create_index(keys, unique=unique)
    except PyMongoError as exc:
        raise DatabaseError(f"Failed to initialise MongoDB collections: {exc}") from exc
    return result


# --------------------------------------------------------------------------
# Generic CRUD helpers
# --------------------------------------------------------------------------
def insert_document(collection: str, document: Dict[str, Any]) -> Dict[str, Any]:
    """Insert one document; returns the stored dict (no ``_id``)."""
    doc = dict(document)
    try:
        get_collection(collection).insert_one(doc)
    except DuplicateKeyError as exc:
        raise DuplicateRecordError(f"Duplicate record in '{collection}': {exc}") from exc
    except PyMongoError as exc:
        raise DatabaseError(f"Insert into '{collection}' failed: {exc}") from exc
    doc.pop("_id", None)
    return doc


def insert_documents(collection: str, documents: Sequence[Dict[str, Any]]) -> int:
    """Insert many documents; returns the number inserted."""
    docs = [dict(d) for d in documents]
    if not docs:
        return 0
    try:
        return len(get_collection(collection).insert_many(docs).inserted_ids)
    except DuplicateKeyError as exc:
        raise DuplicateRecordError(f"Duplicate record in '{collection}': {exc}") from exc
    except PyMongoError as exc:
        raise DatabaseError(f"Bulk insert into '{collection}' failed: {exc}") from exc


def get_document(collection: str, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the first document matching ``query`` or ``None``."""
    try:
        return get_collection(collection).find_one(query, _NO_ID)
    except PyMongoError as exc:
        raise DatabaseError(f"Read from '{collection}' failed: {exc}") from exc


def find_documents(
    collection: str,
    query: Optional[Dict[str, Any]] = None,
    sort: Optional[List[Tuple[str, int]]] = None,
    limit: int = 0,
    skip: int = 0,
) -> List[Dict[str, Any]]:
    """Return documents matching ``query`` (``limit=0`` means no limit).

    ``sort`` is a list like ``[("created_at", -1)]``.
    """
    try:
        cursor = get_collection(collection).find(query or {}, _NO_ID)
        if sort:
            cursor = cursor.sort(sort)
        if skip:
            cursor = cursor.skip(skip)
        if limit:
            cursor = cursor.limit(limit)
        return list(cursor)
    except PyMongoError as exc:
        raise DatabaseError(f"Query on '{collection}' failed: {exc}") from exc


def count_documents(collection: str, query: Optional[Dict[str, Any]] = None) -> int:
    """Count documents matching ``query``."""
    try:
        return get_collection(collection).count_documents(query or {})
    except PyMongoError as exc:
        raise DatabaseError(f"Count on '{collection}' failed: {exc}") from exc


def update_document(
    collection: str,
    query: Dict[str, Any],
    set_fields: Optional[Dict[str, Any]] = None,
    *,
    push_fields: Optional[Dict[str, Any]] = None,
    inc_fields: Optional[Dict[str, Any]] = None,
    upsert: bool = False,
    touch: bool = True,
) -> Dict[str, Any]:
    """Atomically update one document and return its new state.

    ``touch`` refreshes ``updated_at``.  Raises ``RecordNotFoundError`` if
    nothing matched and ``upsert`` is False.
    """
    fields = dict(set_fields or {})
    if touch:
        fields["updated_at"] = utc_now_iso()
    update: Dict[str, Any] = {}
    if fields:
        update["$set"] = fields
    if push_fields:
        update["$push"] = push_fields
    if inc_fields:
        update["$inc"] = inc_fields
    if not update:
        raise ValidationError("No update operations supplied.")
    try:
        doc = get_collection(collection).find_one_and_update(
            query, update, upsert=upsert,
            return_document=ReturnDocument.AFTER, projection=_NO_ID,
        )
    except DuplicateKeyError as exc:
        raise DuplicateRecordError(f"Duplicate record in '{collection}': {exc}") from exc
    except PyMongoError as exc:
        raise DatabaseError(f"Update on '{collection}' failed: {exc}") from exc
    if doc is None:
        raise RecordNotFoundError(f"No matching record in '{collection}' for {query}")
    return doc


def upsert_document(collection: str, query: Dict[str, Any], document: Dict[str, Any]) -> None:
    """Replace the document matching ``query`` (insert if absent)."""
    try:
        get_collection(collection).replace_one(query, dict(document), upsert=True)
    except PyMongoError as exc:
        raise DatabaseError(f"Upsert into '{collection}' failed: {exc}") from exc


def delete_document(collection: str, query: Dict[str, Any]) -> bool:
    """Delete one matching document; True if something was deleted."""
    try:
        return get_collection(collection).delete_one(query).deleted_count > 0
    except PyMongoError as exc:
        raise DatabaseError(f"Delete from '{collection}' failed: {exc}") from exc


def delete_documents(collection: str, query: Dict[str, Any]) -> int:
    """Delete all matching documents; returns the count deleted."""
    try:
        return get_collection(collection).delete_many(query).deleted_count
    except PyMongoError as exc:
        raise DatabaseError(f"Delete from '{collection}' failed: {exc}") from exc


# --------------------------------------------------------------------------
# Sample data (Rahul Verma scenario)
# --------------------------------------------------------------------------
_SEED_KEYS = {
    CUSTOMERS: "customer_id", ORDERS: "order_id", PAYMENTS: "payment_id",
    CUSTOMER_HISTORY: "event_id", POLICIES: "policy_id",
}


def seed_sample_data() -> Dict[str, int]:
    """Idempotently upsert the Rahul Verma sample documents.

    Returns ``{collection: documents_written}``.  Sample docs carry
    ``is_sample: true`` so they are easy to find and remove.
    """
    from db.models import sample_rahul_scenario  # local: keeps import graph flat

    written: Dict[str, int] = {}
    for collection, docs in sample_rahul_scenario().items():
        key = _SEED_KEYS[collection]
        for doc in docs:
            upsert_document(collection, {key: doc[key]}, doc)
        written[collection] = len(docs)
    return written


if __name__ == "__main__":  # basic verification only
    logging.basicConfig(level=logging.INFO)
    print("MongoDB ping:", "OK" if ping() else "FAILED")
    if ping():
        print("init_collections:", init_collections())
        print("seed_sample_data:", seed_sample_data())
        print("customer:", get_document(CUSTOMERS, {"customer_id": "CUST-1001"}))
