"""Storage package for TGWatcher.

Public API: `from tgwatcher.storage import Storage`

The `Storage` class lives in `tgwatcher/storage/facade.py` and delegates
schema migration to `tgwatcher.storage.repositories.migration.MigrationRunner`.

Future phases (see internal-plan) will add more
repositories (message_repo, signal_repo, stats_repo, chat_repo) which the
facade will compose.
"""
import sqlite3
from datetime import datetime

# Python 3.12+ deprecated the default sqlite3 datetime adapters/converters.
# Register explicit adapters so SQLAlchemy-written datetime values remain
# round-trippable and to silence the DeprecationWarning.
sqlite3.register_adapter(datetime, lambda dt: dt.isoformat())
sqlite3.register_converter("TIMESTAMP", lambda b: datetime.fromisoformat(b.decode()))

from tgwatcher.storage.facade import Storage
from tgwatcher.storage.repositories.migration import SCHEMA_VERSION, MigrationRunner

__all__ = ["Storage", "SCHEMA_VERSION", "MigrationRunner"]
