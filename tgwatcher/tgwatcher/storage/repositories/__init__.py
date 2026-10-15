"""Repository implementations for the Storage layer.

Each module here encapsulates a bounded subset of Storage responsibilities:
- `migration`: schema version bookkeeping + vN->vN+1 migration functions.

Repositories are instantiated by the Storage facade and given access to the
shared SQLAlchemy engine. They do not own sessions — they use the engine
directly or borrow the facade's session factory.
"""
