"""Migration registry: import every revision module, collect in order."""

from __future__ import annotations

from selene_space_storage.migrations import Migration
from selene_space_storage.migrations_pkg.v001_initial import migration as v001
from selene_space_storage.migrations_pkg.v002_strategy_versions import migration as v002
from selene_space_storage.migrations_pkg.v003_chat_sessions import migration as v003
from selene_space_storage.migrations_pkg.v004_backtest_curves import migration as v004
from selene_space_storage.migrations_pkg.v005_backtest_symbol_interval import (
    migration as v005,
)
from selene_space_storage.migrations_pkg.v006_user_id import migration as v006
from selene_space_storage.migrations_pkg.v007_live_trading import migration as v007
from selene_space_storage.migrations_pkg.v008_events_system_logs import (
    migration as v008,
)
from selene_space_storage.migrations_pkg.v009_analysis_records import migration as v009
from selene_space_storage.migrations_pkg.v010_experiments import migration as v010
from selene_space_storage.migrations_pkg.v011_paper_plans import migration as v011
from selene_space_storage.migrations_pkg.v012_experiment_verification import (
    migration as v012,
)
from selene_space_storage.migrations_pkg.v013_worker_heartbeats import migration as v013
from selene_space_storage.migrations_pkg.v014_backtest_tasks import migration as v014
from selene_space_storage.migrations_pkg.v015_research_missions import (
    migration as v015,
)
from selene_space_storage.migrations_pkg.v016_research_graph import (
    migration as v016,
)
from selene_space_storage.migrations_pkg.v017_error_replay import (
    migration as v017,
)
from selene_space_storage.migrations_pkg.v018_error_layer import (
    migration as v018,
)
from selene_space_storage.migrations_pkg.v019_mission_thesis import (
    migration as v019,
)
from selene_space_storage.migrations_pkg.v020_backtest_exposure import (
    migration as v020,
)
from selene_space_storage.migrations_pkg.v021_billing import migration as v021
from selene_space_storage.migrations_pkg.v022_episodes import migration as v022
from selene_space_storage.migrations_pkg.v023_failure_taxonomy import (
    migration as v023,
)
from selene_space_storage.migrations_pkg.v024_tuning_tasks import (
    migration as v024,
)
from selene_space_storage.migrations_pkg.v025_question_revisit import (
    migration as v025,
)
from selene_space_storage.migrations_pkg.v026_edge_temporal_validity import (
    migration as v026,
)
from selene_space_storage.migrations_pkg.v027_forward_validation_indexes import (
    migration as v027,
)
from selene_space_storage.migrations_pkg.v028_decision_signals import (
    migration as v028,
)

ALL_MIGRATIONS: list[Migration] = [
    v001,
    v002,
    v003,
    v004,
    v005,
    v006,
    v007,
    v008,
    v009,
    v010,
    v011,
    v012,
    v013,
    v014,
    v015,
    v016,
    v017,
    v018,
    v019,
    v020,
    v021,
    v022,
    v023,
    v024,
    v025,
    v026,
    v027,
    v028,
]
