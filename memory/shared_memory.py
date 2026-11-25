"""Shared memory for the five agents, scoped per workflow run (``run_id``).

Each entry is stored under a key together with who wrote it::

    {"key", "value", "written_by", "version", "updated_at", "history"}

Overwrite protection
    * A key can be created by any valid agent.
    * Only the agent that wrote a key may update it.  Another agent gets a
      ``SharedMemoryConflict`` unless it passes ``overwrite=True`` explicitly.
    * ``expected_version`` enables optimistic locking for read-modify-write.
    * Every read returns deep copies, so callers cannot mutate shared state.

State is held in process memory (thread-safe).  ``persist_to_workflow`` /
``restore_from_workflow`` copy it to/from ``workflow_runs.context`` in
PostgreSQL if cross-process durability is needed.

Suggested keys (convention only): ``customer``, ``orders``, ``payments``,
``policies``, ``history``, ``plan``, ``analysis``, ``decision``,
``execution_result``.
"""
from __future__ import annotations

import copy
import threading
from typing import Any, Dict, Optional

from db.models import DatabaseError, utc_now_iso

AGENTS = ("planning", "research", "analysis", "decision_support", "execution")
SYSTEM = "system"  # for orchestration/bootstrap writes
STRICT_AGENT_NAMES = True  # set False to accept any non-empty writer name
_PERSIST_KEY = "shared_memory"


class SharedMemoryError(Exception):
    """Base error for shared memory."""


class SharedMemoryConflict(SharedMemoryError):
    """A write would overwrite another agent's data or a newer version."""


class UnknownAgentError(SharedMemoryError, ValueError):
    """The writer is not one of the known agents."""


def normalize_agent(name: str) -> str:
    """Normalise e.g. ``"Decision Support Agent"`` -> ``"decision_support"``."""
    cleaned = str(name or "").strip().lower().replace("-", " ").replace("_", " ")
    if cleaned.endswith(" agent"):
        cleaned = cleaned[: -len(" agent")]
    normalized = "_".join(cleaned.split())
    if not normalized:
        raise UnknownAgentError("An agent name is required.")
    if STRICT_AGENT_NAMES and normalized not in AGENTS + (SYSTEM,):
        raise UnknownAgentError(
            f"Unknown agent {name!r}. Expected one of: {', '.join(AGENTS)} (or '{SYSTEM}').")
    return normalized


class SharedMemory:
    """Thread-safe key/value store per ``run_id`` with ownership tracking."""

    def __init__(self) -> None:
        self._runs: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def write(self, run_id: str, key: str, value: Any, agent: str,
              overwrite: bool = False, expected_version: Optional[int] = None) -> Dict[str, Any]:
        """Write ``value`` under ``key``; returns the stored entry (a copy)."""
        if not run_id or not key:
            raise ValueError("run_id and key are required.")
        writer = normalize_agent(agent)
        with self._lock:
            entries = self._runs.setdefault(run_id, {})
            current = entries.get(key)
            if current is not None:
                if current["written_by"] != writer and not overwrite:
                    raise SharedMemoryConflict(
                        f"'{key}' in run {run_id!r} is owned by '{current['written_by']}'; "
                        f"'{writer}' must pass overwrite=True to replace it.")
                if expected_version is not None and current["version"] != expected_version:
                    raise SharedMemoryConflict(
                        f"'{key}' is at version {current['version']}, expected {expected_version}.")
            elif expected_version not in (None, 0):
                raise SharedMemoryConflict(f"'{key}' does not exist; expected version {expected_version}.")
            version = (current["version"] + 1) if current else 1
            now = utc_now_iso()
            history = (current["history"] if current else []) + [
                {"version": version, "written_by": writer, "at": now}]
            entries[key] = {"key": key, "value": copy.deepcopy(value), "written_by": writer,
                            "version": version, "updated_at": now, "history": history[-20:]}
            return copy.deepcopy(entries[key])

    def read(self, run_id: str, key: str, default: Any = None) -> Any:
        """Return the value stored under ``key`` (or ``default``)."""
        with self._lock:
            entry = self._runs.get(run_id, {}).get(key)
            return copy.deepcopy(entry["value"]) if entry else default

    def read_entry(self, run_id: str, key: str) -> Optional[Dict[str, Any]]:
        """Return the full entry (value + owner + version) or ``None``."""
        with self._lock:
            entry = self._runs.get(run_id, {}).get(key)
            return copy.deepcopy(entry) if entry else None

    def read_all(self, run_id: str) -> Dict[str, Any]:
        """Return ``{key: value}`` for a run."""
        with self._lock:
            return {k: copy.deepcopy(e["value"]) for k, e in self._runs.get(run_id, {}).items()}

    def read_by_agent(self, run_id: str, agent: str) -> Dict[str, Any]:
        """Return ``{key: value}`` written by one agent."""
        writer = normalize_agent(agent)
        with self._lock:
            return {k: copy.deepcopy(e["value"]) for k, e in self._runs.get(run_id, {}).items()
                    if e["written_by"] == writer}

    def snapshot(self, run_id: str) -> Dict[str, Any]:
        """Return ``{"run_id", "entries": {key: entry}}`` (JSON-compatible copy)."""
        with self._lock:
            return {"run_id": run_id, "entries": copy.deepcopy(self._runs.get(run_id, {}))}

    def delete(self, run_id: str, key: str, agent: str, overwrite: bool = False) -> bool:
        """Delete a key (owner only unless ``overwrite=True``); True if removed."""
        writer = normalize_agent(agent)
        with self._lock:
            entry = self._runs.get(run_id, {}).get(key)
            if entry is None:
                return False
            if entry["written_by"] != writer and not overwrite:
                raise SharedMemoryConflict(f"'{key}' is owned by '{entry['written_by']}'.")
            del self._runs[run_id][key]
            return True

    def clear_run(self, run_id: str) -> None:
        """Remove all shared state of a run (e.g. when the workflow ends)."""
        with self._lock:
            self._runs.pop(run_id, None)

    # -- optional durability via workflow_runs.context -------------------
    def persist_to_workflow(self, run_id: str) -> bool:
        """Copy the run's shared state into ``workflow_runs.context``.

        Best effort: returns False (never raises) if PostgreSQL or the
        workflow record is unavailable.
        """
        try:
            from db.repository import Workflowruns  # local import: avoids import-time DB deps

            Workflowruns.update_workflow_context(run_id, {_PERSIST_KEY: self.snapshot(run_id)["entries"]})
            return True
        except DatabaseError:
            return False

    def restore_from_workflow(self, run_id: str) -> int:
        """Reload persisted entries that are not already in memory; returns count loaded."""
        try:
            from db.repository import Workflowruns

            run = Workflowruns.get_workflow_run(run_id)
        except DatabaseError:
            return 0
        stored = ((run or {}).get("context") or {}).get(_PERSIST_KEY) or {}
        loaded = 0
        with self._lock:
            entries = self._runs.setdefault(run_id, {})
            for key, entry in stored.items():
                if key not in entries:
                    entries[key] = copy.deepcopy(entry)
                    loaded += 1
        return loaded


# Module-level default instance + function API -------------------------------
_default = SharedMemory()


def get_shared_memory() -> SharedMemory:
    """Return the process-wide default ``SharedMemory``."""
    return _default


def write_shared(run_id: str, key: str, value: Any, agent: str,
                 overwrite: bool = False, expected_version: Optional[int] = None) -> Dict[str, Any]:
    """See ``SharedMemory.write``."""
    return _default.write(run_id, key, value, agent, overwrite, expected_version)


def read_shared(run_id: str, key: str, default: Any = None) -> Any:
    """See ``SharedMemory.read``."""
    return _default.read(run_id, key, default)


def read_all_shared(run_id: str) -> Dict[str, Any]:
    """See ``SharedMemory.read_all``."""
    return _default.read_all(run_id)


def clear_shared(run_id: str) -> None:
    """See ``SharedMemory.clear_run``."""
    _default.clear_run(run_id)


if __name__ == "__main__":  # basic verification only (no database needed)
    rid = "VERIFY-SHARED"
    write_shared(rid, "plan", {"steps": ["research", "analysis"]}, "Planning Agent")
    write_shared(rid, "customer", {"customer_id": "CUST-1001"}, "research")
    try:
        write_shared(rid, "plan", {"steps": []}, "execution")
    except SharedMemoryConflict as exc:
        print("overwrite blocked:", exc)
    print("all:", read_all_shared(rid))
    clear_shared(rid)
