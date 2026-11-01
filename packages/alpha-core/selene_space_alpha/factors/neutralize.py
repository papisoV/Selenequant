"""Legacy industry neutralization API (thin re-export shim).

The implementations live in
:mod:`selene_space_factor_research.preprocessing.neutralize` (ported from
AlphaPurify); this module only re-exports the legacy public surface so existing
callers keep working. The preprocessing toolbox never imports back from here.

Note: ``rank_neutralize`` here is the *true* cross-sectional rank
standardization. AlphaPurify's ``rank`` registry key was RANSAC and is exposed
as ``ransac_neutralize`` — the two coexist without shadowing.
"""
from __future__ import annotations

from selene_space_factor_research.preprocessing.neutralize import (
    _demean_by_group,
    _kmeans_labels,
    _numpy_kmeans,
    apply_ind_neutralize,
    cluster_neutralize,
    rank_neutralize,
)

__all__ = [
    "apply_ind_neutralize",
    "cluster_neutralize",
    "rank_neutralize",
    "_demean_by_group",
    "_kmeans_labels",
    "_numpy_kmeans",
]
