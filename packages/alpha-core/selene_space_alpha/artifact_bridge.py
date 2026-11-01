"""External artifact bridge — resolve producer-declared names to selene tokens.

External artifacts (JSON mining exports, generated Python factors) declare their
formula as a list of *names* in RPN order, e.g. ``"CHAIKIN_3_10 -> AD_LINE_SLOPE
-> ..."``. Their token ids are **not** portable: an id indexes the *producer's*
vocab, so the same integer means different features under different
vocabularies (producers observed up to id 143, selene tops out at 124). Names are
the only portable key, and this module is the single place that maps them onto
selene's token ids.

Windowless language
-------------------
selene's formula language has no parameter pool: ``vm_pandas.py`` dispatches a
token to either a feature lookup or ``func(*args)`` with ``arity`` taken from a
static map. A window therefore has to be a literal inside the operator body, and
the body is the only truth. :func:`operator_windows` reads those literals out of
the implementation with :func:`inspect.getsource` rather than keeping a parallel
hand-maintained table — a second copy of a number that already lives in code is
a drift waiting to happen.

A producer name like ``TS_STD_20`` is only usable if the operator named
``TS_STD`` already rolls over 20 bars. When it does not, the name is reported as
``WINDOW_MISMATCH`` and never silently substituted: a 5-bar std answered for a
20-bar request is a wrong number that looks right.
"""

from __future__ import annotations

import ast
import inspect
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Mapping

from .ops.ops_pandas import OPS_CONFIG_PANDAS
from .vocab import FORMULA_VOCAB, FormulaVocab

__all__ = [
    "KIND_FEATURE",
    "KIND_MISSING",
    "KIND_OPERATOR",
    "NameVerdict",
    "UnsupportedArtifact",
    "classify",
    "names_from_json",
    "names_from_py_header",
    "operator_windows",
    "resolve",
    "to_tokens",
]

KIND_FEATURE: Literal["feature"] = "feature"
KIND_OPERATOR: Literal["operator"] = "operator"
KIND_MISSING: Literal["missing"] = "missing"

Kind = Literal["feature", "operator", "missing"]
Status = Literal["CONFIRMED", "WINDOW_OK", "WINDOW_MISMATCH", "MISSING"]

#: Name suffix carrying a window request, e.g. ``TS_STD_20`` / ``SLOPE20``.
#: Greedy prefix + trailing digits so ``CHAIKIN_3_10`` yields stem
#: ``CHAIKIN_3`` (which is not an operator, hence MISSING) rather than a
#: bogus window match.
_WINDOWED_NAME_RE = re.compile(r"^(?P<stem>.+?)_?(?P<window>\d+)$")

_ROLLING_RE = re.compile(
    r"rolling\(\s*window\s*=\s*(?P<window>\d+)\s*,\s*min_periods\s*=\s*(?P<min_periods>\d+)"
)

#: Ops whose span is not a literal ``rolling(window=...)`` call.
_EXPLICIT_WINDOWS: dict[str, int] = {"TS_DELTA": 1}


class UnsupportedArtifact(Exception):
    """An external artifact cannot be expressed in selene's vocabulary.

    Raised instead of degrading: a partially-resolved formula that still
    "runs" would report numbers computed from the wrong features.
    """


def operator_windows() -> dict[str, int]:
    """Rolling window per operator, read from the operator implementations.

    Only operators whose body contains ``rolling(window=N, min_periods=M)``
    report a window; non-rolling operators (``ADD``, ``RANK``, ...) are simply
    absent. Raises ``RuntimeError`` if an operator that *should* roll no longer
    matches — a silent miss here would turn every windowed name into a
    ``WINDOW_MISMATCH`` and quietly block artifacts that are actually fine.
    """
    return dict(_operator_windows_cached())


@lru_cache(maxsize=1)
def _operator_windows_cached() -> tuple[tuple[str, int], ...]:
    windows: dict[str, int] = {}
    for name, index in _operator_indices().items():
        func = OPS_CONFIG_PANDAS[index][1]
        try:
            source = inspect.getsource(func)  # type: ignore[arg-type]
        except (OSError, TypeError):  # frozen/compiled callables — no window
            continue
        match = _ROLLING_RE.search(source)
        if match is not None:
            windows[name] = int(match["window"])
    windows.update({k: v for k, v in _EXPLICIT_WINDOWS.items() if k not in windows})
    for stem in ("TS_MEAN", "TS_STD", "TS_RANK", "TS_CORR", "TS_MAX", "TS_MIN"):
        if stem not in windows:
            raise RuntimeError(
                f"{stem} declares no rolling window; operator_windows() would "
                "silently report every windowed producer name as a mismatch"
            )
    return tuple(sorted(windows.items()))


def _operator_indices() -> dict[str, int]:
    return {cfg[0]: i for i, cfg in enumerate(OPS_CONFIG_PANDAS)}


def _token_of(name: str, vocab: FormulaVocab) -> int | None:
    try:
        return vocab.token_names.index(name)
    except ValueError:
        return None


@dataclass(frozen=True)
class NameVerdict:
    """What selene can make of one producer-declared name."""

    name: str
    kind: Kind
    token: int | None
    status: Status
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.status in ("CONFIRMED", "WINDOW_OK")


def classify(
    name: str,
    *,
    vocab: FormulaVocab = FORMULA_VOCAB,
    windows: dict[str, int] | None = None,
) -> NameVerdict:
    """Place one producer name against selene's vocabulary."""
    if name in vocab.feature_names:
        return NameVerdict(name, KIND_FEATURE, _token_of(name, vocab), "CONFIRMED")
    if name in vocab.operator_names:
        return NameVerdict(name, KIND_OPERATOR, _token_of(name, vocab), "CONFIRMED")

    match = _WINDOWED_NAME_RE.match(name)
    if match is not None:
        stem = match["stem"]
        requested = int(match["window"])
        windows = windows if windows is not None else operator_windows()
        if stem in vocab.operator_names or stem in windows:
            actual = windows.get(stem)
            if actual is None:
                return NameVerdict(
                    name,
                    KIND_MISSING,
                    None,
                    "MISSING",
                    f"operator {stem} declares no window, so _{requested} cannot be honored",
                )
            if actual == requested:
                return NameVerdict(
                    name,
                    KIND_OPERATOR,
                    _token_of(stem, vocab),
                    "WINDOW_OK",
                    f"{stem} already rolls {actual} bars",
                )
            return NameVerdict(
                name,
                KIND_MISSING,
                None,
                "WINDOW_MISMATCH",
                f"{stem} is burned in at window={actual}, artifact asks {requested}",
            )

    return NameVerdict(
        name, KIND_MISSING, None, "MISSING", "not in selene's vocabulary"
    )


def resolve(
    names: list[str],
    *,
    vocab: FormulaVocab = FORMULA_VOCAB,
    windows: dict[str, int] | None = None,
) -> list[NameVerdict]:
    """Classify a whole RPN name chain against one vocabulary snapshot."""
    windows = windows if windows is not None else operator_windows()
    return [classify(n, vocab=vocab, windows=windows) for n in names]


def to_tokens(verdicts: list[NameVerdict]) -> list[int]:
    """Convert a fully-resolved RPN chain into selene token ids.

    Raises:
        UnsupportedArtifact: if any name is missing or window-mismatched.
    """
    blocked = [v for v in verdicts if not v.usable]
    if blocked:
        reasons = "; ".join(f"{v.name} ({v.status}: {v.detail})" for v in blocked)
        raise UnsupportedArtifact(f"{len(blocked)} name(s) unresolved: {reasons}")
    tokens: list[int] = []
    for verdict in verdicts:
        if verdict.token is None:
            raise UnsupportedArtifact(f"{verdict.name} resolved without a token id")
        tokens.append(verdict.token)
    return tokens


def _split_chain(chain: str) -> list[str]:
    return [part.strip() for part in re.split(r"→|->", chain) if part.strip()]


def names_from_json(payload: Mapping[str, object]) -> list[str]:
    """RPN names of a JSON mining artifact.

    ``formula_decoded`` is the readable chain; ``formula`` holds the producer's
    own ids, which index a foreign vocabulary and are deliberately ignored.
    """
    decoded = payload.get("formula_decoded")
    if not isinstance(decoded, str) or not decoded.strip():
        raise UnsupportedArtifact(
            "JSON artifact has no non-empty 'formula_decoded'; refusing to guess "
            "names from foreign token ids"
        )
    return _split_chain(decoded)


_PY_NAMES_LINE_RE = re.compile(
    r"^\s*formula\s*\(names\)\s*:\s*(?P<chain>.+?)\s*$", re.MULTILINE
)
_PY_TEMPLATE_RE = re.compile(r"v2gen-template-(?P<version>[\w.\-]+)")


@dataclass(frozen=True)
class PyHeader:
    """The declarative header a generated Python factor carries in its docstring."""

    names: list[str]
    template: str | None
    vocab: str | None
    formula_sha: str | None


def names_from_py_header(source: str) -> list[str]:
    return parse_py_header(source).names


def parse_py_header(source: str) -> PyHeader:
    """Read the formula header out of a generated factor's module docstring.

    Raises:
        UnsupportedArtifact: when the docstring or its ``formula (names)`` line
            is absent. The generated Python body is deliberately ignored — it
            delegates to a producer-side operator library that selene neither
            has nor executes.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise UnsupportedArtifact(f"artifact is not valid Python: {exc}") from exc
    docstring = ast.get_docstring(tree)
    if not docstring:
        raise UnsupportedArtifact(
            "artifact has no module docstring to read a header from"
        )

    match = _PY_NAMES_LINE_RE.search(docstring)
    if match is None:
        raise UnsupportedArtifact(
            "module docstring declares no 'formula (names):' line; this is not a "
            "generated formula artifact"
        )
    template = _PY_TEMPLATE_RE.search(docstring)
    return PyHeader(
        names=_split_chain(match["chain"]),
        template=template["version"] if template else None,
        vocab=_labelled(docstring, "vocab"),
        formula_sha=_labelled(docstring, "formula_sha"),
    )


def _labelled(docstring: str, label: str) -> str | None:
    match = re.search(rf"\b{label}\s*:\s*(?P<value>[\w.\-]+)", docstring)
    return match["value"] if match else None
