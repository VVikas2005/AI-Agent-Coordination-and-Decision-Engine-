"""Repository package: one module per entity, used by the agents.

MongoDB-backed : ``coustomers``, ``customer_history``, ``orders``, ``payments``
PostgreSQL-backed : ``approval``, ``trace``, ``Workflowruns``

(File names are kept exactly as in the team repository, including the
``coustomers`` spelling and the ``Workflowruns`` capitalisation.)

Modules are not imported eagerly here; use e.g.
``from db.repository import orders`` or ``from db.repository import Workflowruns``.
"""

__all__ = [
    "approval",
    "coustomers",
    "customer_history",
    "orders",
    "payments",
    "trace",
    "Workflowruns",
]
