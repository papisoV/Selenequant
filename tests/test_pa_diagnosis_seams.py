"""P3-003 tests: KLINES_DB resolution order + incremental input contract.

The script is imported as a module; its module-level KLINES_DB is re-resolved
under monkeypatched env/fs state via importlib.reload.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import run_pa_diagnosis  # noqa: E402


def _reload() -> ModuleType:
    return importlib.reload(run_pa_diagnosis)


def test_env_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "env.db"
    fake.write_bytes(b"x")
    monkeypatch.setenv("SELENE_KLINES_DB", str(fake))
    mod = _reload()
    assert mod.KLINES_DB == fake


def test_repo_root_is_the_default_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SELENE_KLINES_DB", raising=False)
    mod = _reload()
    # Single source of truth: no env → the canonical repo-root DB, always.
    assert mod.resolve_klines_db() == REPO_ROOT / "data" / "klines.db"


def test_no_legacy_absolute_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retired legacy path must never be a fallback again.

    The old resolver fell back to a hardcoded absolute path when the repo
    candidate was missing — the exact behaviour that let two DBs drift apart.
    """
    monkeypatch.delenv("SELENE_KLINES_DB", raising=False)
    mod = _reload()
    assert not hasattr(mod, "LEGACY_KLINES_DB")
    resolved = mod.resolve_klines_db()
    assert "services/local-api" not in str(resolved)


def _bar(ts: int) -> dict:
    return {
        "ts": ts,
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 10.0,
    }


def test_incremental_input_continues_k_numbering() -> None:
    prior = {
        "input_data": {"summary": {"bars_total": 120, "window_end_iso": 1000}},
        "stage1": {"diagnosis_or_decision": {"state": "range"}},
        "stage2": {"diagnosis_or_decision": {"decision": "wait"}},
    }
    bars = [_bar(1001), _bar(1002), _bar(1003)]
    input_data, new_bars, last_decision = run_pa_diagnosis.incremental_input(
        prior, bars, "BTC/USDT", "1h"
    )
    assert len(new_bars) == 3
    ks = [b["k"] for b in input_data["summary"]["bars"]]
    assert ks == [121, 122, 123]
    assert input_data["summary"]["prior_bars_total"] == 120
    assert input_data["summary"]["mode"] == "incremental"
    assert last_decision == {"decision": "wait"}
    # Non-incremental inputs carry no continuity_guard key; the guard is only
    # attached by the incremental branch after evaluate_continuity.
    assert "continuity_guard" not in input_data


def test_pa_question_follows_interval() -> None:
    """The mission question must name the interval being diagnosed.

    find_open_by_question dedupes on (subject, question). With the question
    hardcoded to "1h", a 4h run reused the open 1h mission and filed its 4h
    diagnosis under a mission titled 1h.
    """
    assert run_pa_diagnosis.pa_question_for_interval("4h") != (
        run_pa_diagnosis.pa_question_for_interval("1h")
    )
    assert "4h" in run_pa_diagnosis.pa_question_for_interval("4h")
    assert "1h" in run_pa_diagnosis.pa_question_for_interval("1h")


def test_pa_question_1h_wording_is_unchanged() -> None:
    """Byte-identical to what the live DB already holds, or dedupe misses."""
    assert run_pa_diagnosis.pa_question_for_interval("1h") == (
        "当前 1h 结构是什么状态?是否满足入场质量(gate)?"
    )
