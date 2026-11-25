"""Short-term (temporary) memory: recent messages + current workflow context.

Memory is identified by ``(customer_id, run_id)`` - see ``get_memory_id``.
Data lives in process memory with a TTL and a per-session message cap.
Messages are also written through to the MongoDB ``chat_history`` collection
(``persist=True``, the default) so a restarted process can reload them; a
database failure is logged and never breaks the calling agent.

NOTE: the in-process store is shared only by code running in the same Python
process.  Use ``memory.shared_memory`` for cross-agent state.
"""
from __future__ import annotations

import copy
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from db.models import DatabaseError, build_chat_message

logger = logging.getLogger(__name__)

DEFAULT_MAX_MESSAGES = 50
DEFAULT_TTL_SECONDS = 3600


def get_memory_id(customer_id: str, run_id: Optional[str] = None) -> str:
    """Return the memory identifier ``"<customer_id>::<run_id>"``.

    Without a run id the identifier is ``"<customer_id>::-"`` (customer-level).
    """
    if not customer_id:
        raise ValueError("customer_id is required.")
    return f"{customer_id}::{run_id or '-'}"


class ShortTermMemory:
    """Thread-safe, TTL-bounded memory for conversations and workflow context."""

    def __init__(self, max_messages: int = DEFAULT_MAX_MESSAGES,
                 ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.max_messages = max_messages
        self.ttl_seconds = ttl_seconds
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    # -- internals -----------------------------------------------------
    def _purge(self) -> None:
        now = time.time()
        for key in [k for k, s in self._sessions.items() if s["expires_at"] <= now]:
            del self._sessions[key]

    def _session(self, customer_id: str, run_id: Optional[str]) -> Dict[str, Any]:
        self._purge()
        key = get_memory_id(customer_id, run_id)
        session = self._sessions.setdefault(
            key, {"customer_id": customer_id, "run_id": run_id,
                  "messages": [], "context": {}, "expires_at": 0.0})
        session["expires_at"] = time.time() + self.ttl_seconds
        return session

    @staticmethod
    def _load_persisted(customer_id: str, run_id: Optional[str], limit: int) -> List[Dict[str, Any]]:
        from db import mongo  # local import: keeps this module importable without a DB

        query: Dict[str, Any] = {"customer_id": customer_id}
        if run_id:
            query["run_id"] = run_id
        docs = mongo.find_documents(mongo.CHAT_HISTORY, query,
                                    sort=[("created_at", -1)], limit=limit)
        return list(reversed(docs))

    # -- messages ------------------------------------------------------
    def add_message(self, customer_id: str, run_id: str, role: str, content: str,
                    agent: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None,
                    persist: bool = True) -> Dict[str, Any]:
        """Store a message (``role`` e.g. ``customer``/``agent``/``system``)."""
        message = build_chat_message({
            "customer_id": customer_id, "run_id": run_id, "role": role,
            "content": content, "agent": agent, "metadata": metadata or {}})
        with self._lock:
            session = self._session(customer_id, run_id)
            session["messages"].append(message)
            del session["messages"][:-self.max_messages]
        if persist:
            try:
                from db import mongo

                mongo.insert_document(mongo.CHAT_HISTORY, message)
            except DatabaseError as exc:
                logger.warning("chat_history persist failed (kept in memory): %s", exc)
        return copy.deepcopy(message)

    def get_recent_messages(self, customer_id: str, run_id: Optional[str] = None,
                            limit: int = 10, from_db_if_empty: bool = True) -> List[Dict[str, Any]]:
        """Return the last ``limit`` messages, oldest first.

        With ``run_id=None`` messages of all the customer's active sessions are
        merged.  If nothing is in memory the persisted history is loaded.
        """
        with self._lock:
            self._purge()
            if run_id:
                sessions = [s for k, s in self._sessions.items()
                            if k == get_memory_id(customer_id, run_id)]
            else:
                sessions = [s for s in self._sessions.values() if s["customer_id"] == customer_id]
            messages = sorted((m for s in sessions for m in s["messages"]),
                              key=lambda m: m["created_at"])
        if not messages and from_db_if_empty:
            try:
                messages = self._load_persisted(customer_id, run_id, limit)
            except DatabaseError as exc:
                logger.warning("chat_history load failed: %s", exc)
        return copy.deepcopy(messages[-limit:] if limit else messages)

    # -- workflow context ----------------------------------------------
    def set_workflow_context(self, customer_id: str, run_id: str,
                             context: Dict[str, Any], merge: bool = True) -> Dict[str, Any]:
        """Set (or shallow-merge into) the current workflow context."""
        with self._lock:
            session = self._session(customer_id, run_id)
            if merge:
                session["context"].update(copy.deepcopy(context))
            else:
                session["context"] = copy.deepcopy(context)
            return copy.deepcopy(session["context"])

    def get_workflow_context(self, customer_id: str, run_id: str) -> Dict[str, Any]:
        """Return a copy of the workflow context ({} if none)."""
        with self._lock:
            self._purge()
            session = self._sessions.get(get_memory_id(customer_id, run_id))
            return copy.deepcopy(session["context"]) if session else {}

    # -- housekeeping --------------------------------------------------
    def clear(self, customer_id: str, run_id: Optional[str] = None) -> int:
        """Drop one session (or all of a customer's sessions); returns count."""
        with self._lock:
            if run_id:
                keys = [get_memory_id(customer_id, run_id)]
            else:
                keys = [k for k, s in self._sessions.items() if s["customer_id"] == customer_id]
            return sum(1 for k in keys if self._sessions.pop(k, None) is not None)


# Module-level default instance + function API -------------------------------
_default = ShortTermMemory()


def get_short_term_memory() -> ShortTermMemory:
    """Return the process-wide default ``ShortTermMemory`` instance."""
    return _default


def add_message(customer_id: str, run_id: str, role: str, content: str,
                agent: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None,
                persist: bool = True) -> Dict[str, Any]:
    """See ``ShortTermMemory.add_message``."""
    return _default.add_message(customer_id, run_id, role, content, agent, metadata, persist)


def get_recent_messages(customer_id: str, run_id: Optional[str] = None,
                        limit: int = 10) -> List[Dict[str, Any]]:
    """See ``ShortTermMemory.get_recent_messages``."""
    return _default.get_recent_messages(customer_id, run_id, limit)


def set_workflow_context(customer_id: str, run_id: str, context: Dict[str, Any],
                         merge: bool = True) -> Dict[str, Any]:
    """See ``ShortTermMemory.set_workflow_context``."""
    return _default.set_workflow_context(customer_id, run_id, context, merge)


def get_workflow_context(customer_id: str, run_id: str) -> Dict[str, Any]:
    """See ``ShortTermMemory.get_workflow_context``."""
    return _default.get_workflow_context(customer_id, run_id)


def clear_memory(customer_id: str, run_id: Optional[str] = None) -> int:
    """See ``ShortTermMemory.clear``."""
    return _default.clear(customer_id, run_id)


if __name__ == "__main__":  # basic verification only (writes sample chat rows to MongoDB)
    cid, rid = "CUST-1001", "VERIFY-SHORTTERM"
    add_message(cid, rid, "customer", "My earbud is not charging (sample).")
    set_workflow_context(cid, rid, {"order_ids": ["ORD-5092", "ORD-4871"]})
    print("messages:", get_recent_messages(cid, rid))
    print("context :", get_workflow_context(cid, rid))
    clear_memory(cid, rid)
    try:
        from db import mongo

        print("cleanup chat_history rows:", mongo.delete_documents(mongo.CHAT_HISTORY, {"run_id": rid}))
    except DatabaseError as exc:
        print("cleanup skipped:", exc)
