"""selene 两阶段行情分析包（market-analysis）。

独立重实现 + 无复制声明，详见包内 PROVENANCE.md。
工作流概念源自 selene 自有 MIT decision-workflow（Stage1 诊断 → gate 短路 → Stage2 决策）
与 Al Brooks 公开出版物；命名按 PROVENANCE.md 隔离表映射。
"""

from .chat_service import (
    ChatLogStore,
    ChatService,
    DEFAULT_CHAT_LOG_DIR,
    MAX_HISTORY_TURNS,
    SNAPSHOT_TOKEN_CAP,
)
from .core import (
    cheat_anchor,
    gate_merge,
    make_meta,
    normalize_llm_json,
    parse_json_response,
    record_assembly,
    retry_loop,
)
from .data_source import (
    DEFAULT_KLINES_DB,
    DOWNSAMPLE_MAX,
    KlineSource,
    WINDOW_BARS,
)
from .exceptions import (
    CheatError,
    MarketAnalysisError,
    ProviderError,
    SemanticError,
    ValidationError,
)
from .experience_library import ExperienceLibrary
from .flow import MarketAnalysisFlow
from .llm_provider import (
    HttpLLMProvider,
    LLMMessage,
    LLMResponse,
)
from .models import (
    Evidence,
    NextBarForecast,
    NextCycleForecast,
    SignalPattern,
    Stage1Diagnosis,
    Stage2Decision,
    ValidationErrorModel,
)
from .persistence import RecordPersistence, InMemoryPersistence
from .persistence_sqlite import SqliteRecordPersistence
from .routing import (
    MARKET_PLAYBOOK_TABLE,
    PlaybookRoutingEntry,
    PlaybookRoutingTable,
    route_playbooks,
)
from .structure_features import STRUCTURE_FEATURE_KEYS, compute_structure_features
from .theme_layer import (
    MarketStructureDataQuality,
    MarketThemeContext,
    MarketThemeItem,
    PrimaryTheme,
    RankedThemeItem,
    StockBoardPosition,
    ThemeBreadth,
    build_active_themes,
    build_theme_context,
    infer_stock_role,
    normalize_ranking,
    phase_from_change,
    strength_from_change,
)
from .validator import (
    MarketValidationReport,
    ValidatorConfig,
    validate_stage1,
    validate_stage2,
)

__all__ = [
    # Free Chat
    "ChatLogStore",
    "ChatService",
    "DEFAULT_CHAT_LOG_DIR",
    "MAX_HISTORY_TURNS",
    "SNAPSHOT_TOKEN_CAP",
    # core 编排原语
    "cheat_anchor",
    "gate_merge",
    "make_meta",
    "normalize_llm_json",
    "parse_json_response",
    "record_assembly",
    "retry_loop",
    # 数据源 + 指标预计算
    "DEFAULT_KLINES_DB",
    "DOWNSAMPLE_MAX",
    "KlineSource",
    "WINDOW_BARS",
    # 异常
    "CheatError",
    "MarketAnalysisError",
    "ProviderError",
    "SemanticError",
    "ValidationError",
    # 经验库
    "ExperienceLibrary",
    # 编排器
    "MarketAnalysisFlow",
    # LLM provider
    "HttpLLMProvider",
    "LLMMessage",
    "LLMResponse",
    # 持久化
    "InMemoryPersistence",
    "RecordPersistence",
    "SqliteRecordPersistence",
    # 契约模型
    "Evidence",
    "NextBarForecast",
    "NextCycleForecast",
    "SignalPattern",
    "Stage1Diagnosis",
    "Stage2Decision",
    "ValidationErrorModel",
    # 结构特征（US-301）
    "STRUCTURE_FEATURE_KEYS",
    "compute_structure_features",
    # 路由
    "MARKET_PLAYBOOK_TABLE",
    "PlaybookRoutingEntry",
    "PlaybookRoutingTable",
    "route_playbooks",
    # 校验
    "MarketValidationReport",
    "ValidatorConfig",
    "validate_stage1",
    "validate_stage2",
    # 行情热点/题材层（📋5，吸收自 daily_stock_analysis）
    "MarketStructureDataQuality",
    "MarketThemeContext",
    "MarketThemeItem",
    "PrimaryTheme",
    "RankedThemeItem",
    "StockBoardPosition",
    "ThemeBreadth",
    "build_active_themes",
    "build_theme_context",
    "infer_stock_role",
    "normalize_ranking",
    "phase_from_change",
    "strength_from_change",
]
