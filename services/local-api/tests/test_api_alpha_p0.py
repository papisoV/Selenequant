"""P0 training-visualization backend tests (alpha.py).

Covers the on_step write-through, real-time status history, logs
ring-buffer endpoint, cooperative stop (StopIteration → 'stopped'),
and the subdir/legacy formula-glob unification.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.api.v1 import alpha


@pytest.fixture(autouse=True)
def _clean_sessions(monkeypatch):
    monkeypatch.setattr(alpha, "_sessions", {})
    yield


def _mk_session(sid: str, status: str = "running") -> None:
    with alpha._sessions_lock:
        alpha._sessions[sid] = {
            "status": status,
            "step": 0,
            "best_score": -float("inf"),
            "train_steps": 100,
            "config": {},
            "started_at": 100.0,
            "history": {
                "step": [],
                "avg_reward": [],
                "best_score": [],
                "stable_rank": [],
            },
            "logs": [],
        }


# ---------------------------------------------------------------------------
# status returns real-time history
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_status_returns_growing_history() -> None:
    _mk_session("alpha_test_status", status="running")
    for i in range(3):
        with alpha._sessions_lock:
            s = alpha._sessions["alpha_test_status"]
            s["step"] = i
            s["best_score"] = float(i)
            s["history"]["step"].append(i)
            s["history"]["avg_reward"].append(0.1 * i)
            s["history"]["best_score"].append(float(i))
            s["logs"].append(f"line {i}")

    resp = await alpha.get_training_status("alpha_test_status")
    data = resp.data
    assert data["status"] == "running"
    assert data["step"] == 2
    assert data["history"]["step"] == [0, 1, 2]
    assert data["history"]["avg_reward"] == [0.0, 0.1, 0.2]
    # Event-loop side gets defensive copies, not live references.
    assert data["history"] is not alpha._sessions["alpha_test_status"]["history"]


@pytest.mark.asyncio
async def test_status_404_for_unknown_session() -> None:
    with pytest.raises(Exception):
        await alpha.get_training_status("nope")


@pytest.mark.asyncio
async def test_status_serializes_inf_best_score_as_null() -> None:
    """best_score=-inf (pre-first-step) must not leak as -Infinity into JSON —
    json.dumps emits `-Infinity`, which frontend JSON.parse cannot decode."""
    _mk_session("alpha_test_inf", status="starting")
    resp = await alpha.get_training_status("alpha_test_inf")
    assert resp.data["best_score"] is None
    # Confirm the payload round-trips through real JSON serialization.
    raw = json.dumps(resp.data)
    assert "-Infinity" not in raw and "Infinity" not in raw
    assert json.loads(raw)["best_score"] is None


# ---------------------------------------------------------------------------
# logs endpoint (ring buffer)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_logs_endpoint_returns_buffered_lines() -> None:
    _mk_session("alpha_test_logs")
    with alpha._sessions_lock:
        alpha._sessions["alpha_test_logs"]["logs"] = ["a", "b", "c"]
    resp = await alpha.get_training_logs("alpha_test_logs")
    assert resp.data["logs"] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_logs_ring_buffer_caps_at_200() -> None:
    _mk_session("alpha_test_ring")
    with alpha._sessions_lock:
        alpha._sessions["alpha_test_ring"]["logs"] = [
            f"l{i}" for i in range(alpha._LOG_RING_SIZE + 50)
        ]
    resp = await alpha.get_training_logs("alpha_test_ring")
    assert len(resp.data["logs"]) == alpha._LOG_RING_SIZE


# ---------------------------------------------------------------------------
# _iter_formula_files unifies subdir + legacy flat layouts
# ---------------------------------------------------------------------------
def test_iter_formula_files_unifies_both_layouts(tmp_path, monkeypatch) -> None:
    res_dir = tmp_path / "alpha_results"
    (res_dir / "alpha_123").mkdir(parents=True)
    (res_dir / "alpha_123" / "best_meme_strategy.json").write_text(
        json.dumps([0, 1, 2]), encoding="utf-8"
    )
    (res_dir / "best_meme_strategy_legacy.json").write_text(
        json.dumps([3, 4, 5]), encoding="utf-8"
    )
    monkeypatch.setattr(alpha, "_results_dir", res_dir)

    found = dict(alpha._iter_formula_files())
    assert "alpha_123" in found
    assert "legacy" in found
    assert len(found) == 2


def test_get_formula_tokens_falls_back_to_subdir(tmp_path, monkeypatch) -> None:
    res_dir = tmp_path / "alpha_results"
    (res_dir / "alpha_9").mkdir(parents=True)
    (res_dir / "alpha_9" / "best_meme_strategy.json").write_text(
        json.dumps([7, 8, 9]), encoding="utf-8"
    )
    monkeypatch.setattr(alpha, "_results_dir", res_dir)
    assert alpha._get_formula_tokens("alpha_9") == [7, 8, 9]
    assert alpha._get_formula_tokens("missing") is None


# ---------------------------------------------------------------------------
# _run_training: on_step write-through + cooperative stop
# ---------------------------------------------------------------------------
class _FakeLoader:
    """Stub CryptoDataLoader: no real synthetic-data generation."""

    def __init__(self, config=None):
        self.config = config

    def _create_synthetic_data(self, n_tokens=300):
        pass

    def load_from_sqlite(self, **kwargs):
        raise FileNotFoundError("no data (stub)")


class _FakeEngine:
    """Minimal AlphaEngine stand-in with a train(on_step) hook."""

    def __init__(self, **kwargs):
        self.best_score = 0.42
        self.best_formula = [0, 6, 3, 1]
        self.training_history = {"step": [0], "avg_reward": [0.1], "best_score": [0.42]}
        self._stop_at = kwargs.get("_stop_at")

    def train(
        self, on_step=None, start_step=0, checkpoint_path=None, checkpoint_interval=10
    ):
        # 3 steps; if _stop_at reached, the session is marked stopping and the
        # callback raises StopIteration (mirrors engine.on_step behavior).
        for i in range(3):
            if on_step is not None:
                on_step(i, 0.1 * i, self.best_score, None, None)
        if self._stop_at is not None:
            with alpha._sessions_lock:
                alpha._sessions[self._stop_at]["status"] = "stopping"
            # Emulate the engine raising from the next on_step call.
            on_step(99, 0.0, self.best_score, None, None)

    def save_checkpoint(self, path, step, extra=None):
        pass

    def load_checkpoint(self, path):
        return 0


@pytest.mark.asyncio
async def test_run_training_completed_sets_status_and_history(
    monkeypatch,
) -> None:
    monkeypatch.setattr("selene_space_alpha.mining.AlphaEngine", _FakeEngine)
    monkeypatch.setattr("selene_space_alpha.data_loader.CryptoDataLoader", _FakeLoader)
    _mk_session("alpha_done")
    req = alpha.TrainRequest(train_steps=10, batch_size=8, use_synthetic_data=True)
    await alpha._run_training("alpha_done", req)
    with alpha._sessions_lock:
        assert alpha._sessions["alpha_done"]["status"] == "completed"
        assert alpha._sessions["alpha_done"]["best_score"] == 0.42


@pytest.mark.asyncio
async def test_run_training_cooperative_stop_sets_stopped(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "selene_space_alpha.mining.AlphaEngine",
        lambda **kw: _FakeEngine(_stop_at="alpha_stop", **kw),
    )
    monkeypatch.setattr("selene_space_alpha.data_loader.CryptoDataLoader", _FakeLoader)
    _mk_session("alpha_stop")
    req = alpha.TrainRequest(train_steps=10, batch_size=8, use_synthetic_data=True)
    await alpha._run_training("alpha_stop", req)
    with alpha._sessions_lock:
        assert alpha._sessions["alpha_stop"]["status"] == "stopped"
        assert alpha._sessions["alpha_stop"]["best_score"] == 0.42


# ---------------------------------------------------------------------------
# P1: explain endpoint (LLM with rule-based fallback)
# ---------------------------------------------------------------------------
def _mk_session_with_history(sid: str, best_score: float = 0.5) -> None:
    _mk_session(sid, status="completed")
    with alpha._sessions_lock:
        s = alpha._sessions[sid]
        s["best_score"] = best_score
        s["best_formula"] = [0, 6, 3, 1]
        s["config"] = {"symbol": "BTC/USDT", "interval": "1h", "train_steps": 100}
        s["data_source"] = "real"
        s["history"] = {
            "step": [0, 1, 2],
            "avg_reward": [0.1, 0.2, 0.3],
            "best_score": [0.1, 0.3, 0.5],
            "stable_rank": [1.0],
        }


@pytest.mark.asyncio
async def test_explain_falls_back_to_rule_when_no_llm(monkeypatch) -> None:
    """No LLM configured → rule-based explanation, endpoint still succeeds."""
    _mk_session_with_history("alpha_exp_rule")

    async def _fake_call_llm(*args, **kwargs):
        return None  # simulate no API key / not configured

    monkeypatch.setattr("app.core.llm_helper.call_llm", _fake_call_llm)
    monkeypatch.setattr("app.core.dependencies.get_settings_service", lambda: object())

    resp = await alpha.explain_training("alpha_exp_rule")
    data = resp.data
    assert data["source"] == "rule"
    assert "已训练 3 步" in data["explanation"]
    assert data["summary"]["step_count"] == 3


@pytest.mark.asyncio
async def test_explain_uses_llm_when_configured(monkeypatch) -> None:
    """LLM returns text → endpoint surfaces it as source='llm'."""
    _mk_session_with_history("alpha_exp_llm")

    async def _fake_call_llm(*args, **kwargs):
        return "这个因子表现良好,建议继续训练到 200 步。"

    monkeypatch.setattr("app.core.llm_helper.call_llm", _fake_call_llm)
    monkeypatch.setattr("app.core.dependencies.get_settings_service", lambda: object())

    resp = await alpha.explain_training("alpha_exp_llm")
    data = resp.data
    assert data["source"] == "llm"
    assert "200 步" in data["explanation"]


@pytest.mark.asyncio
async def test_explain_404_for_unknown_session() -> None:
    with pytest.raises(Exception):
        await alpha.explain_training("nope_explain")


# ---------------------------------------------------------------------------
# P1: backtest depth metrics (annual_return / sortino / rolling_sharpe)
# ---------------------------------------------------------------------------
class _FakeVocab:
    def __init__(self, **kwargs):
        pass

    def decode(self, tokens):
        return "test_formula"


class _FakeVM:
    """PandasStackVM stand-in: execute() returns a deterministic score series."""

    def __init__(self, vocab):
        self._factor_fns = []

    def execute(self, tokens, df, precomputed_features=None):
        import numpy as np

        n = len(df)
        x = np.sin(np.arange(n) / 7.0)  # predictable non-flat signal
        import pandas as pd

        return pd.Series(x, index=df.index)


class _FakeKlineDB:
    def __init__(self, db_path=None):
        pass

    def get_klines(self, exchange, market_type, symbol, interval):
        import pandas as pd
        import numpy as np

        n = 300
        idx = pd.date_range("2024-01-01", periods=n, freq="15min")
        close = np.linspace(100, 130, n) + np.sin(np.arange(n) / 10) * 2
        return pd.DataFrame(
            {
                "timestamp": idx.astype("int64") // 10**6,
                "open": close - 0.1,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": np.abs(np.random.randn(n)) * 1e6,
            }
        )


@pytest.mark.asyncio
async def test_backtest_metrics_include_depth_fields(monkeypatch, tmp_path) -> None:
    """Saved-strategy backtest response carries the 8-metric + rolling_sharpe set."""
    # Patch the strategy dir to a temp path containing a minimal saved strategy.
    strat_dir = tmp_path / "strategies" / "alpha_v5"
    strat_dir.mkdir(parents=True)
    strat_dir.joinpath("alpha_v5_test.json").write_text(
        json.dumps(
            {
                "formula": {"tokens": [0, 6, 3, 1], "readable": "test_formula"},
                "strategies": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(alpha, "_strategies_dir", tmp_path / "strategies")

    monkeypatch.setattr("selene_space_market.kline_db.KlineDB", _FakeKlineDB)
    monkeypatch.setattr(
        "selene_space_alpha.strategy_bridge.AlphaFormulaStrategy",
        lambda tokens: type("S", (), {"formula_readable": "test_formula"})(),
    )
    monkeypatch.setattr("selene_space_alpha.vocab.FormulaVocab", _FakeVocab)
    monkeypatch.setattr("selene_space_alpha.vm.PandasStackVM", _FakeVM)

    from app.api.v1.alpha import BacktestSavedStrategyRequest

    body = BacktestSavedStrategyRequest(
        symbol="BTC/USDT", interval="15m", exchange="binance", market_type="futures"
    )

    resp = await alpha.backtest_saved_strategy("alpha_v5_test", body)
    data = resp.data
    assert data["total_return"] is not None
    assert "annual_return" in data
    assert "sortino_ratio" in data
    assert "rolling_sharpe" in data
    assert isinstance(data["rolling_sharpe"], list)
    assert len(data["rolling_sharpe"]) > 0
    assert data["total_trades"] >= 0


# ---------------------------------------------------------------------------
# P2: artifact management (zip export / import round-trip + listing)
# ---------------------------------------------------------------------------
import io
import zipfile


def _mk_artifact_dir(tmp_path, monkeypatch) -> None:
    res = tmp_path / "alpha_results"
    (res / "alpha_export").mkdir(parents=True)
    (res / "alpha_export" / "best_meme_strategy.json").write_text(
        json.dumps([0, 6, 3, 1]), encoding="utf-8"
    )
    (res / "alpha_export" / "training_history.json").write_text(
        json.dumps({"step": [0, 1, 2], "avg_reward": [0.1, 0.2]}), encoding="utf-8"
    )
    _write_checkpoint(res, "alpha_export")
    (res / "best_meme_strategy_legacy.json").write_text(
        json.dumps([7, 8, 9]), encoding="utf-8"
    )
    monkeypatch.setattr(alpha, "_results_dir", res)


@pytest.mark.asyncio
async def test_list_artifacts_lists_formula_and_history(tmp_path, monkeypatch) -> None:
    _mk_artifact_dir(tmp_path, monkeypatch)
    resp = await alpha.list_artifacts()
    names = sorted(a["name"] for a in resp.data["artifacts"])
    assert "alpha_export/best_meme_strategy.json" in names
    assert "alpha_export/training_history.json" in names
    assert "alpha_export/checkpoint.pt" in names
    assert "best_meme_strategy_legacy.json" in names
    by_name = {a["name"]: a for a in resp.data["artifacts"]}
    assert by_name["alpha_export/training_history.json"]["kind"] == "history"
    assert by_name["alpha_export/best_meme_strategy.json"]["kind"] == "formula"
    assert by_name["alpha_export/checkpoint.pt"]["kind"] == "checkpoint"


@pytest.mark.asyncio
async def test_export_import_round_trip(tmp_path, monkeypatch) -> None:
    _mk_artifact_dir(tmp_path, monkeypatch)
    exp = await alpha.export_artifacts()
    assert exp.data["count"] == 4
    assert "alpha_export/checkpoint.pt" in exp.data["files"]

    # Import into a FRESH results dir (empty) — must round-trip fully.
    fresh = tmp_path / "fresh_results"
    monkeypatch.setattr(alpha, "_results_dir", fresh)
    assert not fresh.exists()

    imp = await alpha.import_artifacts(
        alpha.ArtifactImportRequest(zip_base64=exp.data["zip_base64"])
    )
    assert len(imp.data["imported"]) == 4
    assert imp.data["skipped"] == []

    # Re-list formulas: fresh dir now has both subdir + legacy layouts.
    resp = await alpha.list_formulas()
    ids = sorted(f["formula_id"] for f in resp.data["formulas"])
    assert "alpha_export" in ids
    assert "legacy" in ids

    # Portability proof: the checkpoint lands exactly where resume looks for
    # it (_checkpoint_path) and is a loadable torch checkpoint.
    import torch

    ckpt = torch.load(fresh / "alpha_export" / "checkpoint.pt", weights_only=False)
    assert ckpt["step"] == 3
    assert ckpt["best_score"] == 0.77


@pytest.mark.asyncio
async def test_import_rejects_zip_slip_and_non_json(tmp_path, monkeypatch) -> None:
    fresh = tmp_path / "fresh2"
    monkeypatch.setattr(alpha, "_results_dir", fresh)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.json", json.dumps([1, 2, 3]))
        zf.writestr("report.html", "<html></html>")
        zf.writestr("ok/formula.json", json.dumps([4, 5, 6]))
        zf.writestr("ok/checkpoint.pt", b"\x80\x04fake-torch-bytes")
    zip_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    imp = await alpha.import_artifacts(alpha.ArtifactImportRequest(zip_base64=zip_b64))
    assert imp.data["imported"] == ["ok/formula.json", "ok/checkpoint.pt"]
    assert set(imp.data["skipped"]) == {"../escape.json", "report.html"}
    # escape.json must NOT exist outside results dir
    assert not (tmp_path / "escape.json").exists()
    # .pt member landed on disk (portability round-trip partner of export)
    assert (fresh / "ok" / "checkpoint.pt").read_bytes() == b"\x80\x04fake-torch-bytes"


@pytest.mark.asyncio
async def test_import_invalid_base64_returns_400(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(alpha, "_results_dir", tmp_path / "fresh3")
    with pytest.raises(Exception):
        await alpha.import_artifacts(
            alpha.ArtifactImportRequest(zip_base64="not!!base64")
        )


@pytest.mark.asyncio
async def test_export_empty_results_dir(monkeypatch) -> None:
    monkeypatch.setattr(alpha, "_results_dir", Path("nonexistent_dir_xyz"))
    resp = await alpha.export_artifacts()
    assert resp.data["zip_base64"] is None


# ---------------------------------------------------------------------------
# Follow-up: resume (断点续训) + locked formula reads
# ---------------------------------------------------------------------------
def _write_checkpoint(res_dir: Path, session_id: str, *, step: int = 3) -> Path:
    """Write a real checkpoint.pt so resume_training can torch.load it."""
    import torch

    ckpt_dir = res_dir / session_id
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt = {
        "step": step,
        "best_score": 0.77,
        "best_formula": [0, 6, 3, 1],
        "training_history": {
            "step": list(range(step + 1)),
            "avg_reward": [0.1 * i for i in range(step + 1)],
            "best_score": [0.5] * (step + 1),
        },
        "config": {"TRAIN_STEPS": 100, "BATCH_SIZE": 8, "MAX_FORMULA_LEN": 12},
        "extra": {
            "train_steps": 100,
            "batch_size": 8,
            "max_formula_len": 12,
            "use_lord": True,
            "symbol": "BTC/USDT",
            "exchange": "binance",
            "market_type": "futures",
            "interval": "1h",
            "use_synthetic_data": True,
        },
    }
    torch.save(ckpt, ckpt_dir / "checkpoint.pt")
    return ckpt_dir / "checkpoint.pt"


@pytest.mark.asyncio
async def test_resume_404_no_checkpoint(monkeypatch) -> None:
    monkeypatch.setattr(alpha, "_results_dir", Path("nonexistent_dir_xyz"))
    with pytest.raises(Exception) as excinfo:
        await alpha.resume_training("nope")
    assert getattr(excinfo.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_resume_rejects_running(monkeypatch, tmp_path) -> None:
    res = tmp_path / "alpha_results"
    _write_checkpoint(res, "alpha_run")
    monkeypatch.setattr(alpha, "_results_dir", res)
    _mk_session("alpha_run", status="running")
    with pytest.raises(Exception) as excinfo:
        await alpha.resume_training("alpha_run")
    assert getattr(excinfo.value, "status_code", None) == 400


@pytest.mark.asyncio
async def test_resume_restores_session_and_starts(monkeypatch, tmp_path) -> None:
    """Resume rebuilds the in-memory session from checkpoint state and
    relaunches training from the saved step."""
    res = tmp_path / "alpha_results"
    _write_checkpoint(res, "alpha_ckpt")
    monkeypatch.setattr(alpha, "_results_dir", res)

    calls: list[tuple[str, int]] = []

    async def _fake_run(sid: str, request, start_step: int = 0) -> None:
        calls.append((sid, start_step))

    monkeypatch.setattr(alpha, "_run_training", _fake_run)

    resp = await alpha.resume_training("alpha_ckpt")
    assert resp.data["status"] == "starting"
    assert resp.data["resumed_from_step"] == 3

    with alpha._sessions_lock:
        s = alpha._sessions["alpha_ckpt"]
        assert s["status"] == "starting"
        assert s["step"] == 3
        assert s["best_score"] == 0.77
        assert s["best_formula"] == [0, 6, 3, 1]
        assert s["history"]["step"] == [0, 1, 2, 3]

    # Let the spawned task run one tick so _fake_run records the launch.
    import asyncio

    await asyncio.sleep(0)
    assert calls == [("alpha_ckpt", 3)]


@pytest.mark.asyncio
async def test_get_formula_detail_lock_reads() -> None:
    """get_formula_detail reads _sessions under _sessions_lock (no race with
    the engine thread's write-through callback)."""
    _mk_session("alpha_fd", status="completed")
    with alpha._sessions_lock:
        s = alpha._sessions["alpha_fd"]
        s["best_formula"] = [0, 6, 3, 1]
        s["best_score"] = 0.5
        s["history"] = {
            "step": [0, 1],
            "avg_reward": [0.1, 0.2],
            "best_score": [0.4, 0.5],
        }

    resp = await alpha.get_formula_detail("alpha_fd")
    assert resp.data["formula_tokens"] == [0, 6, 3, 1]
    assert resp.data["best_score"] == 0.5
    assert resp.data["training_history"]["step"] == [0, 1]
    # Event-loop side gets defensive copies, not live references.
    assert resp.data["training_history"] is not alpha._sessions["alpha_fd"]["history"]


# ---------------------------------------------------------------------------
# Deploy verdict gate (user decision item 2, 2026-09-10): deploy_formula
# consumes the T4 validation report's decision — accept passes, other
# decisions get 403 unless SELENE_ALPHA_DEPLOY_ALLOW_HOLD=1. Formulas absent
# from the report deploy unhindered (absence of evidence is not a refusal).
# ---------------------------------------------------------------------------
def _write_t4_report(tmp_path: Path, entries: list[dict]) -> Path:
    # Matches _lookup_t4_decision's path: <repo_root>/internal-archive/.
    report_dir = tmp_path / "实验" / "alpha-mining"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "validation_report.json").write_text(
        json.dumps({"formulas": entries}), encoding="utf-8"
    )
    return report_dir


@pytest.fixture
def _deploy_env(tmp_path, monkeypatch):
    results = tmp_path / "results"
    results.mkdir()
    (results / "best_meme_strategy_alpha_gate.json").write_text(
        json.dumps([0, 6, 3, 1]), encoding="utf-8"
    )
    monkeypatch.setattr(alpha, "_results_dir", results)
    monkeypatch.setattr(
        alpha, "_deploy_archive_dir", tmp_path / "strategies" / "alpha_v_deploy"
    )
    monkeypatch.setattr(alpha, "_repo_root", lambda: tmp_path)
    monkeypatch.delenv("SELENE_ALPHA_DEPLOY_ALLOW_HOLD", raising=False)
    return tmp_path


@pytest.mark.asyncio
async def test_deploy_accept_decision_passes(_deploy_env) -> None:
    _write_t4_report(
        _deploy_env,
        [{"tokens": [0, 6, 3, 1], "decision": "accept", "reason": "all pass"}],
    )
    with patch(
        "selene_space_alpha.strategy_bridge.AlphaFormulaStrategy",
        lambda formula_tokens: type(
            "S", (), {"formula_readable": "f", "to_dict": lambda self: {}}
        )(),
    ):
        resp = await alpha.deploy_formula("alpha_gate")
    assert resp.data["status"] == "deployed"
    assert (_deploy_env / "results" / "deployed" / "alpha_gate.json").exists()


@pytest.mark.asyncio
async def test_deploy_mirrors_backtest_archive(_deploy_env) -> None:
    """The deploy -> backtest seam: deploy_formula also writes the ``alpha_v*``
    archive that backtests.py's ``alpha:{id}`` branch resolves, carrying the
    strategy's own vocab block. No stub here — the archived vocab is the whole
    point (the reader's CEX_V4 fallback would reindex mined tokens)."""
    from selene_space_alpha.vocab import FORMULA_VOCAB

    _write_t4_report(
        _deploy_env,
        [{"tokens": [0, 6, 3, 1], "decision": "accept", "reason": "all pass"}],
    )
    resp = await alpha.deploy_formula("alpha_gate")
    assert resp.data["status"] == "deployed"

    archive = json.loads(
        (alpha._deploy_archive_dir / "alpha_gate.json").read_text(encoding="utf-8")
    )
    assert archive["id"] == "alpha:alpha_gate"
    assert archive["formula"]["tokens"] == [0, 6, 3, 1]
    assert archive["vocab"]["feature_names"] == list(FORMULA_VOCAB.feature_names)
    assert archive["vocab"]["operator_names"] == list(FORMULA_VOCAB.operator_names)


@pytest.mark.asyncio
async def test_deploy_hold_decision_refused_403(_deploy_env) -> None:
    _write_t4_report(
        _deploy_env,
        [{"tokens": [0, 6, 3, 1], "decision": "hold", "reason": "solo UNI: mdd"}],
    )
    with patch(
        "selene_space_alpha.strategy_bridge.AlphaFormulaStrategy",
        lambda formula_tokens: type(
            "S", (), {"formula_readable": "f", "to_dict": lambda self: {}}
        )(),
    ):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await alpha.deploy_formula("alpha_gate")
    assert exc.value.status_code == 403
    assert "hold" in exc.value.detail
    assert "solo UNI" in exc.value.detail


@pytest.mark.asyncio
async def test_deploy_hold_refusal_escape_valve(_deploy_env, monkeypatch) -> None:
    _write_t4_report(
        _deploy_env,
        [{"tokens": [0, 6, 3, 1], "decision": "hold", "reason": "x"}],
    )
    monkeypatch.setenv("SELENE_ALPHA_DEPLOY_ALLOW_HOLD", "1")
    with patch(
        "selene_space_alpha.strategy_bridge.AlphaFormulaStrategy",
        lambda formula_tokens: type(
            "S", (), {"formula_readable": "f", "to_dict": lambda self: {}}
        )(),
    ):
        resp = await alpha.deploy_formula("alpha_gate")
    assert resp.data["status"] == "deployed"


@pytest.mark.asyncio
async def test_deploy_no_report_deploys_unhindered(_deploy_env) -> None:
    # No validation_report.json at all: the gate is silent.
    with patch(
        "selene_space_alpha.strategy_bridge.AlphaFormulaStrategy",
        lambda formula_tokens: type(
            "S", (), {"formula_readable": "f", "to_dict": lambda self: {}}
        )(),
    ):
        resp = await alpha.deploy_formula("alpha_gate")
    assert resp.data["status"] == "deployed"


@pytest.mark.asyncio
async def test_deploy_unknown_tokens_deploys_unhindered(_deploy_env) -> None:
    # Report exists but has no matching entry: absence of evidence is not a refusal.
    _write_t4_report(
        _deploy_env,
        [{"tokens": [9, 9, 9], "decision": "reject", "reason": "bad"}],
    )
    with patch(
        "selene_space_alpha.strategy_bridge.AlphaFormulaStrategy",
        lambda formula_tokens: type(
            "S", (), {"formula_readable": "f", "to_dict": lambda self: {}}
        )(),
    ):
        resp = await alpha.deploy_formula("alpha_gate")
    assert resp.data["status"] == "deployed"


@pytest.mark.asyncio
async def test_deploy_gate_matches_latest_entry_last_wins(_deploy_env) -> None:
    # The report accumulates runs; the most recent (last) matching entry wins.
    _write_t4_report(
        _deploy_env,
        [
            {"tokens": [0, 6, 3, 1], "decision": "hold", "reason": "old run"},
            {"tokens": [0, 6, 3, 1], "decision": "accept", "reason": "revalidated"},
        ],
    )
    with patch(
        "selene_space_alpha.strategy_bridge.AlphaFormulaStrategy",
        lambda formula_tokens: type(
            "S", (), {"formula_readable": "f", "to_dict": lambda self: {}}
        )(),
    ):
        resp = await alpha.deploy_formula("alpha_gate")
    assert resp.data["status"] == "deployed"


@pytest.mark.asyncio
async def test_import_raw_pt_lands_at_session_dir(tmp_path, monkeypatch) -> None:
    """Bare .pt upload (no zip) lands at {session}/checkpoint.pt — resume path."""
    fresh = tmp_path / "fresh_raw"
    monkeypatch.setattr(alpha, "_results_dir", fresh)

    payload = b"\x80\x04fake-torch-bytes"
    imp = await alpha.import_artifacts(
        alpha.ArtifactImportRequest(
            raw_name="checkpoint.pt",
            raw_base64=base64.b64encode(payload).decode("ascii"),
        )
    )
    assert imp.data["imported"] == ["checkpoint.pt"]
    target = fresh / "checkpoint" / "checkpoint.pt"
    assert target.read_bytes() == payload


@pytest.mark.asyncio
async def test_import_raw_rejects_bad_suffix(tmp_path, monkeypatch) -> None:
    fresh = tmp_path / "fresh_raw2"
    monkeypatch.setattr(alpha, "_results_dir", fresh)

    with pytest.raises(HTTPException) as exc_info:
        await alpha.import_artifacts(
            alpha.ArtifactImportRequest(
                raw_name="evil.html",
                raw_base64=base64.b64encode(b"<html>").decode("ascii"),
            )
        )
    assert exc_info.value.status_code == 400
    # Endpoint ensures the results dir exists before validating; nothing
    # hostile may be written into it.
    assert list(fresh.iterdir()) == []
