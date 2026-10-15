"""Locate, enumerate, and describe the QuantDinger strategy backup library.

The backup directory holds exported ``src*.py`` sources plus an
``_index_sources_*.json`` catalogue. The catalogue is the only place QD's
*reported* backtest numbers live, and they are prose, not data -- every entry
writes its result in whatever shape its author chose.

:func:`extract_qd_metrics` therefore reads conservatively: it extracts only
when the description anchors one unambiguous return figure (an ``->`` chain's
final segment, or the walk-forward out-of-sample block), and otherwise returns
``None`` with the number of competing figures it saw. Guessing a number from
prose would put an invented baseline next to selene's real one.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from selene_space_strategy.qd.describe import QDSourceInfo, describe_qd_source

logger = logging.getLogger(__name__)

QD_STRATEGY_DIR_ENV = "SELENE_QD_STRATEGY_DIR"
DEFAULT_QD_STRATEGY_DIR = Path(r"<local-path-redacted>")

_INDEX_GLOB = "_index_sources_*.json"
_SOURCE_GLOB = "src*.py"
_ID_PREFIX = "qd:"

_NUM = r"[+-]?\d+(?:\.\d+)?"
_UNIT = r"(?:%|pct|pp)"

_RETURN_FIGURE_RE = re.compile(rf"{_NUM}\s*{_UNIT}", re.IGNORECASE)
_DRAWDOWN_RE = re.compile(
    rf"(?:dd|drawdown|回撤)\s*[:\s]*({_NUM})\s*{_UNIT}", re.IGNORECASE
)
_SHARPE_RE = re.compile(rf"sharpe\s*[:\s]*({_NUM})", re.IGNORECASE)
_TRADES_RE = re.compile(rf"({_NUM})\s*(?:笔|trades?)", re.IGNORECASE)
_ANNUAL_RE = re.compile(rf"年化\s*~?\s*({_NUM})\s*{_UNIT}", re.IGNORECASE)

_ARROW_RE = re.compile(r"(?:->|→)")
_WF_OOS_RE = re.compile(r"未见数据")
_WF_MARKER_RE = re.compile(r"(?:Walk-forward|Walk forward|WF✓|WF )", re.IGNORECASE)
_BLOCK_END_RE = re.compile(r"[；;。]")

#: Figures whose immediate left context names them as *not* the strategy's
#: headline return. ``zero-fee`` is excluded because it is the same strategy
#: measured under a different cost assumption, not a second result.
_EXCLUDED_LABELS = (
    "dd",
    "回撤",
    "drawdown",
    "sharpe",
    "夏普",
    "pf",
    "年化",
    "friction",
    "excess",
    "基准",
    "bench",
    "bh",
    "buy-hold",
    "buyhold",
    "buy & hold",
    "zero-fee",
    "zerofee",
    "zero fee",
    "佣金",
    "滑点",
    "comm",
    "slip",
    "胜率",
    "win rate",
    "trades",
    "笔",
    "天",
)
_LABEL_WINDOW = 14
#: A figure's label sits immediately left of the number and is separated from
#: whatever precedes it, so the label is read as the last delimited segment.
_LABEL_SEPARATORS = re.compile(r"[(\[（【,，;；/|:：]")

#: Flags derived from reliable substrings or structured catalogue fields.
_FALSIFIED_TOKENS = ("已证伪", "作废", "建议不使用")
_IN_SAMPLE_TOKENS = ("in-sample", "in_sample", "训练窗口")
_PORTFOLIO_TOKENS = ("portfolio", "equal-weight", "equal weight", "13-leg", "16-leg")


@dataclass(frozen=True)
class QDMetrics:
    """QD's reported result for one strategy, or why it could not be read."""

    metrics: dict[str, float] | None
    candidates: int
    reason: str

    def as_payload(self) -> dict[str, object]:
        return {
            "metrics": dict(self.metrics) if self.metrics else None,
            "candidates": self.candidates,
            "reason": self.reason,
        }


# -- library location --------------------------------------------------------


def qd_strategy_dir() -> Path:
    """Backup-library root, overridable for other installs and tests."""
    override = os.environ.get(QD_STRATEGY_DIR_ENV)
    return Path(override) if override else DEFAULT_QD_STRATEGY_DIR


@lru_cache(maxsize=8)
def _source_stems(root_str: str) -> tuple[Path, ...]:
    return tuple(sorted(Path(root_str).glob(_SOURCE_GLOB)))


def list_qd_strategy_files() -> tuple[Path, ...]:
    """The ``src*.py`` exports, sorted.

    ``id*.py`` neighbours are per-user duplicates of these same sources, so
    listing them too would double every card.
    """
    return _source_stems(str(qd_strategy_dir()))


def _strategy_id_of(path: Path) -> str:
    """The handle for one export: ``qd:srcNN``.

    Exports carry a descriptive suffix (``src48_XLMUSDT_gk_vol_trail10_...``)
    that the author is free to change, so the id stops at the first
    underscore. Resolution accepts the full stem too.
    """
    return f"{_ID_PREFIX}{path.stem.split('_', 1)[0]}"


def list_qd_strategy_ids() -> list[str]:
    """Strategy ids (``qd:srcNN``) for the whole backup library."""
    return [_strategy_id_of(path) for path in list_qd_strategy_files()]


def _strategy_key(strategy_id: str) -> str:
    return (
        strategy_id[len(_ID_PREFIX) :]
        if strategy_id.startswith(_ID_PREFIX)
        else strategy_id
    )


def resolve_qd_source(strategy_id: str) -> Path:
    """Map ``qd:src48`` (or a full stem) to its source file.

    QuantDinger exports carry descriptive suffixes, so the id is a prefix:
    ``src48`` matches ``src48_<SYMBOL>_<desc>.py``. An
    id matching several files is refused rather than silently resolved.
    """
    key = _strategy_key(strategy_id).strip()
    if not key:
        raise ValueError("empty qd strategy id")
    files = list_qd_strategy_files()
    exact = [path for path in files if path.stem == key]
    if exact:
        return exact[0]
    prefixed = [path for path in files if path.stem.startswith(f"{key}_")]
    if not prefixed:
        raise ValueError(
            f"unknown qd strategy {strategy_id!r}; {len(files)} sources in {qd_strategy_dir()}"
        )
    if len(prefixed) > 1:
        names = ", ".join(path.stem for path in prefixed)
        raise ValueError(f"ambiguous qd strategy {strategy_id!r}: matches {names}")
    return prefixed[0]


# -- index catalogue ---------------------------------------------------------


@lru_cache(maxsize=4)
def _load_index(root_str: str) -> dict[str, dict[str, Any]]:
    """Read the newest ``_index_sources_*.json``, keyed by source stem."""
    candidates = sorted(Path(root_str).glob(_INDEX_GLOB))
    if not candidates:
        return {}
    path = candidates[-1]
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("qd index %s unreadable: %s", path, exc)
        return {}
    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries if isinstance(entries, list) else []:
        stem = Path(str(entry.get("file", ""))).stem
        if stem:
            indexed[stem] = entry
    return indexed


def qd_index_entry(path: Path) -> dict[str, Any]:
    """Catalogue row for one source, or ``{}`` when it is unindexed."""
    return _load_index(str(qd_strategy_dir())).get(path.stem, {})


# -- prose -> numbers --------------------------------------------------------


def _is_return_figure(text: str, start: int) -> bool:
    """Whether the figure at ``start`` is an *unlabelled* return.

    The label is the last delimited segment to its left, so ``dd-25.2%``
    reads as a drawdown, ``(buy-hold -83pct)`` as a benchmark, and
    ``trail0.10 +94.6%`` as the strategy's own result. Segmenting rather
    than matching a trailing substring keeps a neighbouring figure from
    being mistaken for this one's label.
    """
    window = text[max(0, start - _LABEL_WINDOW) : start].lower()
    segments = [
        segment for segment in _LABEL_SEPARATORS.split(window) if segment.strip()
    ]
    if not segments:
        return True
    return not any(label in segments[-1] for label in _EXCLUDED_LABELS)


def _return_candidates(text: str) -> list[re.Match[str]]:
    return [
        match
        for match in _RETURN_FIGURE_RE.finditer(text)
        if _is_return_figure(text, match.start())
    ]


def _out_of_sample_block(text: str) -> str | None:
    """The slice describing the walk-forward (unseen-data) result."""
    anchor = _WF_OOS_RE.search(text)
    if anchor is None:
        return None
    tail = text[anchor.end() :]
    end = _BLOCK_END_RE.search(tail)
    return tail[: end.start()] if end else tail


def _to_fraction(raw: str) -> float:
    return float(raw) / 100.0


def _extract(block: str) -> dict[str, float]:
    """Pull the metric set out of a block already known to hold one result."""
    candidate = _return_candidates(block)[0]
    metrics: dict[str, float] = {
        "total_return": _to_fraction(candidate.group(0).rstrip("%pct")),
    }
    drawdown = _DRAWDOWN_RE.search(block)
    if drawdown:
        metrics["max_drawdown"] = _to_fraction(drawdown.group(1))
    sharpe = _SHARPE_RE.search(block)
    if sharpe:
        metrics["sharpe"] = float(sharpe.group(1))
    trades = _TRADES_RE.search(block)
    if trades:
        metrics["trades"] = float(trades.group(1))
    annual = _ANNUAL_RE.search(block)
    if annual:
        metrics["annual_return"] = _to_fraction(annual.group(1))
    return metrics


def extract_qd_metrics(description: str) -> QDMetrics:
    """Read QD's reported result from one prose description.

    Anchors, in order: the walk-forward unseen-data block (the explicit
    out-of-sample result), then the segment after the final ``->`` (a
    baseline-to-final chain states its result last). The walk-forward check
    comes first because its ``train->test`` arrow is structural, not a
    baseline-to-final chain. Without an anchor a lone return figure is
    accepted; several competing figures yield ``None`` plus their count.
    """
    text = (description or "").strip()
    if not text:
        return QDMetrics(None, 0, "no description")

    if _WF_OOS_RE.search(text):
        block = _out_of_sample_block(text) or ""
        found = _return_candidates(block)
        if len(found) == 1:
            return QDMetrics(
                _extract(block), len(found), "walk-forward unseen-data block"
            )
        return QDMetrics(
            None,
            len(found),
            f"{len(found)} return figures in the walk-forward block; ambiguous",
        )

    if _WF_MARKER_RE.search(text):
        # A walk-forward claim whose only figure is the full-window one. The
        # validated number is not stated, so neither is the total return.
        return QDMetrics(
            None,
            len(_return_candidates(text)),
            "walk-forward claim without an unseen-data figure; stated result is not out-of-sample",
        )

    arrows = list(_ARROW_RE.finditer(text))
    if arrows:
        block = text[arrows[-1].end() :]
        found = _return_candidates(block)
        if len(found) == 1:
            return QDMetrics(_extract(block), len(found), "final segment after '->'")
        return QDMetrics(
            None,
            len(found),
            f"{len(found)} return figures after the final '->'; ambiguous",
        )

    found = _return_candidates(text)
    if len(found) == 1:
        return QDMetrics(_extract(text), 1, "single return figure")
    if not found:
        return QDMetrics(None, 0, "no return figure reported")
    return QDMetrics(
        None, len(found), f"{len(found)} competing return figures; ambiguous"
    )


# -- entries -----------------------------------------------------------------


def _entry_flags(
    entry: dict[str, Any], info: QDSourceInfo, description: str
) -> list[str]:
    """Availability / provenance caveats, each from a reliable signal."""
    flags: list[str] = []
    if str(entry.get("strategy_manifest", "")).lower() == "falsified" or any(
        token in description for token in _FALSIFIED_TOKENS
    ):
        flags.append("falsified")
    lowered = description.lower()
    if any(token in lowered for token in _IN_SAMPLE_TOKENS):
        flags.append("in_sample_only")
    if any(token in lowered for token in _PORTFOLIO_TOKENS):
        flags.append("portfolio")
    if len(info.instruments) > 1:
        flags.append("multi_instrument")
    return flags


def load_qd_source_info(strategy_id: str) -> QDSourceInfo:
    """Describe one library strategy by id, with its catalogue name."""
    path = resolve_qd_source(strategy_id)
    return _describe_path(path, path.stat().st_mtime_ns)


@lru_cache(maxsize=64)
def _describe_path(path: Path, mtime_ns: int) -> QDSourceInfo:
    name = qd_index_entry(path).get("name") or path.stem
    return describe_qd_source(
        path.read_text(encoding="utf-8"),
        source_name=path.stem,
        name=str(name),
    )


def qd_entry(strategy_id: str) -> dict[str, object]:
    """Full listing payload for one strategy: declaration + baseline + flags."""
    path = resolve_qd_source(strategy_id)
    info = _describe_path(path, path.stat().st_mtime_ns)
    entry = qd_index_entry(path)
    description = str(entry.get("description") or "")
    return {
        "id": _strategy_id_of(path),
        "name": info.name,
        "file": path.name,
        **info.as_payload(),
        "params_schema": dict(entry.get("param_schema") or {}),
        "qd_baseline": extract_qd_metrics(description).as_payload(),
        "flags": _entry_flags(entry, info, description),
    }


def list_qd_entries() -> list[dict[str, object]]:
    """Every library strategy, in id order."""
    return [qd_entry(_strategy_id_of(path)) for path in list_qd_strategy_files()]
