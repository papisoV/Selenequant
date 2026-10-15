"""selene_space_storage -- Local persistence (SQLite).

Public API
----------
.. autoclass:: AsyncSQLiteManager
.. autoclass:: Migration
.. autoclass:: MigrationRunner
.. autoclass:: StrategyRepository
.. autoclass:: BacktestRepository
.. autoclass:: PaperAccountRepository
.. autoclass:: AuditLogRepository

Repositories for chat sessions, experiments, paper plans, analysis records,
and strategy versions are importable from their submodules.
"""

from selene_space_storage.migrations import Migration, MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.audit_logs import AuditLogRepository
from selene_space_storage.repositories.backtests import BacktestRepository
from selene_space_storage.repositories.paper_accounts import PaperAccountRepository
from selene_space_storage.repositories.strategies import StrategyRepository
from selene_space_storage.sqlite import AsyncSQLiteManager

__all__ = [
    "ALL_MIGRATIONS",
    "AsyncSQLiteManager",
    "AuditLogRepository",
    "BacktestRepository",
    "Migration",
    "MigrationRunner",
    "PaperAccountRepository",
    "StrategyRepository",
]
