"""Database package (Role 5 - Memory and Database Developer).

Submodules (import them explicitly; nothing is imported eagerly here so the
package stays free of circular imports and heavy driver imports):

* ``db.models``   - exceptions, id/time helpers, document builders, sample data
* ``db.mongo``    - MongoDB (PyMongo) connection and generic CRUD helpers
* ``db.postgre``  - PostgreSQL connection pool, schema, decision_trace helpers
* ``db.repository`` - per-entity repositories used by the agents
"""
