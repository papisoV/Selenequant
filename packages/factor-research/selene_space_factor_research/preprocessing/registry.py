"""METHOD_REGISTRY for the preprocessing toolbox — 42 methods, 1:1 with defs.

Registry composition (Ported from AlphaPurify, MIT):
    winsorize    12  (ransac/huber/tanh/zscore/rankgauss/boxcox_compress/
                      rolling_quantile/quantile/iqr/volatility/mad/mean_std)
    neutralize   15  (multiOLS/lasso/ridge/elasticnet/polynomial/kernelridge/
                      huber/ransac/theilsen/random_forest/GBDT/ICA/PCA/
                      bayesianridge/partialcorrelation)
    standardize  15  (zscore/robust_zscore/minmax/rank/rank_gaussianize/
                      rolling/rolling_robust/rolling_minmax/volatility_scaling/
                      EWMA/normal_scores/quantile_binning/log_zscore/
                      yeo_johnson/boxcox)

Bare keys are not globally unique across categories (e.g. ``zscore`` exists in
winsorize and standardize), so public method names are ``"{category}_{key}"``.
The three Selene legacy methods (true-rank/cluster/industry) are NOT registered
here — they are exposed through the ``factors/neutralize.py`` shim only.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Literal

import pandas as pd

from . import neutralize as _neutralize
from . import standardize as _standardize
from . import winsorize as _winsorize
from .errors import PreprocessMethodError, PreprocessParamError

Category = Literal["winsorize", "neutralize", "standardize"]

_WINSORIZE_KEYS: tuple[str, ...] = (
    "ransac",
    "huber",
    "tanh",
    "zscore",
    "rankgauss",
    "boxcox_compress",
    "rolling_quantile",
    "quantile",
    "iqr",
    "volatility",
    "mad",
    "mean_std",
)

_NEUTRALIZE_KEYS: tuple[str, ...] = (
    "multiOLS",
    "lasso",
    "ridge",
    "elasticnet",
    "polynomial",
    "kernelridge",
    "huber",
    "ransac",
    "theilsen",
    "random_forest",
    "GBDT",
    "ICA",
    "PCA",
    "bayesianridge",
    "partialcorrelation",
)

_STANDARDIZE_KEYS: tuple[str, ...] = (
    "zscore",
    "robust_zscore",
    "minmax",
    "rank",
    "rank_gaussianize",
    "rolling",
    "rolling_robust",
    "rolling_minmax",
    "volatility_scaling",
    "EWMA",
    "normal_scores",
    "quantile_binning",
    "log_zscore",
    "yeo_johnson",
    "boxcox",
)

# Key → function-name overrides where the def name differs from "{key}_{category}".
_FUNC_NAME_OVERRIDES: dict[Category, dict[str, str]] = {
    "neutralize": {"random_forest": "randomforest_neutralize"},
}

_CATEGORY_MODULES: dict[Category, tuple[Any, tuple[str, ...]]] = {
    "winsorize": (_winsorize, _WINSORIZE_KEYS),
    "neutralize": (_neutralize, _NEUTRALIZE_KEYS),
    "standardize": (_standardize, _STANDARDIZE_KEYS),
}


@dataclass(frozen=True)
class PreprocessMethod:
    """A registered preprocessing method.

    Attributes:
        name: Public registry key, ``"{category}_{key}"``.
        category: Which toolbox the method belongs to.
        func: Callable ``func(df, **params) -> df``.
        required_params: Parameters (besides ``df``) with no default.
        optional_params: Parameters with defaults.
    """

    name: str
    category: Category
    func: Callable[..., pd.DataFrame]
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()

    def __call__(self, df: pd.DataFrame, **params: Any) -> pd.DataFrame:
        _validate_params(self, params)
        return self.func(df, **params)


def _func_name_for(category: Category, key: str) -> str:
    overrides = _FUNC_NAME_OVERRIDES.get(category, {})
    return overrides.get(key, f"{key}_{category}")


def _make_method(category: Category, key: str) -> PreprocessMethod:
    module, _ = _CATEGORY_MODULES[category]
    func = getattr(module, _func_name_for(category, key))
    sig = inspect.signature(func)
    # The DataFrame is always the first positional parameter; AlphaPurify names
    # it `df` or `base_df` interchangeably, so skip by position, not by name.
    required: list[str] = []
    optional: list[str] = []
    first_positional_skipped = False
    for p in sig.parameters.values():
        if not first_positional_skipped and p.kind in (
            p.POSITIONAL_ONLY,
            p.POSITIONAL_OR_KEYWORD,
        ):
            first_positional_skipped = True
            continue
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.default is inspect.Parameter.empty:
            required.append(p.name)
        else:
            optional.append(p.name)
    return PreprocessMethod(
        name=f"{category}_{key}",
        category=category,
        func=func,
        required_params=tuple(required),
        optional_params=tuple(optional),
    )


METHOD_REGISTRY: dict[str, PreprocessMethod] = {
    f"{category}_{key}": _make_method(category, key)
    for category, (_, keys) in _CATEGORY_MODULES.items()
    for key in keys
}


def list_methods(category: Category | None = None) -> list[str]:
    """List registered method names.

    Args:
        category: Restrict to ``"winsorize"``/``"neutralize"``/``"standardize"``.

    Returns:
        Sorted public method names. With no category this is exactly 42 names.
    """
    if category is None:
        return sorted(METHOD_REGISTRY)
    return sorted(name for name, m in METHOD_REGISTRY.items() if m.category == category)


def _resolve(method: str) -> PreprocessMethod:
    if method in METHOD_REGISTRY:
        return METHOD_REGISTRY[method]
    matches = [m for m in METHOD_REGISTRY.values() if m.name.endswith(f"_{method}")]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise PreprocessMethodError(
            f"bare method key {method!r} is ambiguous ({[m.name for m in matches]}); "
            "use the full 'category_key' name"
        )
    raise PreprocessMethodError(f"unknown preprocessing method {method!r}")


def resolve_method(method: str) -> PreprocessMethod:
    """Resolve a registry key (full ``category_key`` or unambiguous bare key)."""
    return _resolve(method)


def _validate_params(method: PreprocessMethod, params: dict[str, Any]) -> None:
    valid = set(method.required_params) | set(method.optional_params)
    missing = [p for p in method.required_params if p not in params]
    unknown = [k for k in params if k not in valid]
    if missing:
        raise PreprocessParamError(
            f"{method.name} missing required params: {missing}"
        )
    if unknown:
        raise PreprocessParamError(
            f"{method.name} got unknown params: {unknown}"
        )


def apply(df: pd.DataFrame, steps: list[Any]) -> pd.DataFrame:
    """Apply a chain of :class:`PreprocessConfig` steps in order.

    Args:
        df: Input DataFrame.
        steps: Sequence of ``PreprocessConfig`` (or objects with ``method``
            and ``params`` attributes).

    Returns:
        The DataFrame after every step has been applied.
    """
    out = df
    for step in steps:
        method = _resolve(step.method)
        params = dict(step.params or {})
        out = method(out, **params)
    return out
