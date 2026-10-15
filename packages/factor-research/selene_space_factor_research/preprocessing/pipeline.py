"""Config-driven preprocessing pipeline with provenance tracking.

AlphaPurify's chainable cleaner (``AlphaPurifier``) dispatches through a
hand-written if/elif chain — not declarative, auditable, or reproducible. This
module replaces that with a config-driven pipeline: a YAML/JSON config of steps
is applied in order, and every step records provenance (method, params, in/out
shapes, duration, prior-content hash) so the same input plus the same config
always reproduces the same ``trace_hash``.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .config import PreprocessConfig
from .errors import PreprocessMethodError
from .registry import resolve_method

__all__ = ["PipelineResult", "PreprocessingPipeline", "StepProvenance"]


@dataclass(frozen=True)
class StepProvenance:
    """Record of one applied preprocessing step."""

    method: str
    params: dict[str, Any]
    in_shape: tuple[int, int]
    out_shape: tuple[int, int]
    duration_ms: float
    prior_hash: str


@dataclass
class PipelineResult:
    """Output of :meth:`PreprocessingPipeline.run`."""

    df: pd.DataFrame
    provenance: list[StepProvenance]
    trace_hash: str


def _fingerprint(df: pd.DataFrame) -> str:
    """Deterministic content hash for a DataFrame.

    Used for per-step ``prior_hash`` and the input half of ``trace_hash``, so a
    pipeline result is reproducible for the same input + config.
    """
    if df.empty:
        payload = b"__empty__"
    else:
        try:
            payload = pd.util.hash_pandas_object(df, index=True).values.tobytes()
        except TypeError:  # unhashable cell dtype (e.g. object columns)
            payload = repr(
                (df.shape, list(df.columns), {c: str(t) for c, t in df.dtypes.items()})
            ).encode()
    return hashlib.sha256(payload).hexdigest()


def _as_step(item: Any) -> PreprocessConfig:
    if isinstance(item, PreprocessConfig):
        return item
    if isinstance(item, dict):
        if "method" not in item:
            raise PreprocessMethodError(f"step dict missing 'method': {item!r}")
        return PreprocessConfig(item["method"], dict(item.get("params") or {}))
    raise TypeError(f"unsupported step type: {type(item)!r}")


def _coerce_steps(config: Any) -> list[PreprocessConfig]:
    """Normalize ``config`` into a list of steps.

    Accepts a single :class:`PreprocessConfig`, a single-step dict, a list of
    steps, or a ``{"pipeline": [...]}`` mapping (dict/list parsed from YAML/JSON).
    """
    if isinstance(config, PreprocessConfig):
        return [config]
    if isinstance(config, list):
        return [_as_step(item) for item in config]
    if isinstance(config, dict):
        if "pipeline" in config:
            return _coerce_steps(config["pipeline"])
        if "method" in config:
            return [_as_step(config)]
        raise ValueError("config dict must contain 'pipeline' (list) or a single 'method' step")
    raise TypeError(f"unsupported config type: {type(config)!r}")


class PreprocessingPipeline:
    """A reproducible chain of preprocessing steps driven by declarative config."""

    def __init__(self, steps: list[PreprocessConfig]) -> None:
        self.steps = list(steps)

    @classmethod
    def from_config(cls, config: Any) -> "PreprocessingPipeline":
        """Build a pipeline from a dict, a step list, or a YAML/JSON file path."""
        if isinstance(config, (str, pathlib.Path)):
            path = pathlib.Path(config)
            if not path.exists():
                raise FileNotFoundError(f"config file not found: {path}")
            suffix = path.suffix.lower()
            if suffix in (".yaml", ".yml"):
                try:
                    import yaml
                except ImportError as exc:  # pragma: no cover - soft dep
                    raise ImportError("PyYAML is required to read .yaml configs") from exc
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            elif suffix == ".json":
                data = json.loads(path.read_text(encoding="utf-8"))
            else:
                raise ValueError(f"unsupported config file suffix: {suffix!r}")
        else:
            data = config
        return cls(_coerce_steps(data))

    def run(self, df: pd.DataFrame) -> PipelineResult:
        """Apply every step in order, recording provenance."""
        out = df
        provenance: list[StepProvenance] = []
        prior_hash = _fingerprint(df)
        for step in self.steps:
            method = resolve_method(step.method)
            params = dict(step.params or {})
            in_shape = out.shape
            started = time.perf_counter()
            out = method(out, **params)
            duration_ms = (time.perf_counter() - started) * 1000.0
            provenance.append(
                StepProvenance(
                    method=method.name,
                    params=params,
                    in_shape=in_shape,
                    out_shape=out.shape,
                    duration_ms=duration_ms,
                    prior_hash=prior_hash,
                )
            )
            prior_hash = _fingerprint(out)
        return PipelineResult(df=out, provenance=provenance, trace_hash=_trace_hash(provenance))


def _trace_hash(provenance: list[StepProvenance]) -> str:
    """Input + config fingerprint (excludes timing, stays reproducible).

    Seeded with the first step's ``prior_hash`` (the input fingerprint) so the
    same input + config reproduces the hash, while a different input does not.
    """
    digest = hashlib.sha256()
    if provenance:
        digest.update(provenance[0].prior_hash.encode())
    for step in provenance:
        digest.update(step.method.encode())
        digest.update(json.dumps(step.params, sort_keys=True, default=str).encode())
    return digest.hexdigest()
