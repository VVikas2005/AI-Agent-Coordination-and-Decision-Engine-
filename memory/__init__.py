
"""Memory package (Role 5 - Memory and Database Developer)."""

from importlib import import_module

_EXPORTS = {
    "get_past_cases": "longterm",
    "get_relevant_history": "longterm",
    "retrieve_policy": "longterm",
    "search_past_cases": "longterm",
    "store_past_case": "longterm",
    "SharedMemoryConflict": "shared_memory",
    "clear_shared": "shared_memory",
    "read_all_shared": "shared_memory",
    "read_shared": "shared_memory",
    "write_shared": "shared_memory",
    "add_message": "shortterm",
    "clear_memory": "shortterm",
    "get_recent_messages": "shortterm",
    "get_workflow_context": "shortterm",
    "set_workflow_context": "shortterm",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = import_module(f".{_EXPORTS[name]}", __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
