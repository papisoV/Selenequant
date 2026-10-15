"""研究图谱仓储族 —— Question / Revision / Edge / Episode / Evidence /
Hypothesis / Experiment / Decision。

拆成多个模块而非一个巨型仓储,是因为它们被四种不同的不变量管:
Question 管状态轴与回答轴、Edge 管事实/判断分界与撤销、Episode/Evidence 管
"只增不改"、Decision 管 Director 终审与 outcome 三态。混在一起写,迟早
有人用一个 update 方法把 §8③ 的不可变性抹掉。
"""

from __future__ import annotations

from selene_space_storage.repositories.research_graph.changes import (
    collect_changes,
)
from selene_space_storage.repositories.research_graph.edges import (
    ResearchEdgeRepository,
)
from selene_space_storage.repositories.research_graph.integrity import (
    check_graph_integrity,
)
from selene_space_storage.repositories.research_graph.objects import (
    DecisionRepository,
    EpisodeRepository,
    ErrorTypeRepository,
    EvidenceRepository,
    ExperimentRepository,
    HypothesisRepository,
    link_derived_from,
)
from selene_space_storage.repositories.research_graph.error_replay import (
    ERROR_LAYERS,
    Abandoned,
    ErrorReplayResult,
    ErrorPair,
    analyze_error,
    classify_error,
    recall_error_replay,
    render_error_replay,
)
from selene_space_storage.repositories.research_graph.questions import (
    ResearchQuestionRepository,
    is_proposed_question,
)
from selene_space_storage.repositories.research_graph.recall import (
    LEARNED_EXPERIENCE,
    RESEARCH_RECORD,
    filter_known_by,
    parse_as_of,
    recall,
)
from selene_space_storage.repositories.research_graph.relations import (
    list_relations,
)

__all__ = [
    "ERROR_LAYERS",
    "Abandoned",
    "DecisionRepository",
    "EpisodeRepository",
    "ErrorPair",
    "ErrorReplayResult",
    "ErrorTypeRepository",
    "EvidenceRepository",
    "ExperimentRepository",
    "HypothesisRepository",
    "LEARNED_EXPERIENCE",
    "RESEARCH_RECORD",
    "ResearchEdgeRepository",
    "ResearchQuestionRepository",
    "analyze_error",
    "check_graph_integrity",
    "classify_error",
    "collect_changes",
    "filter_known_by",
    "is_proposed_question",
    "link_derived_from",
    "list_relations",
    "parse_as_of",
    "recall",
    "recall_error_replay",
    "render_error_replay",
]
