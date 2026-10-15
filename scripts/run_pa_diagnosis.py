"""US-005 real run: BTC 1h price-action two-stage diagnosis, zero mock.

Real chain: local klines.db (BTC/USDT 1h binance futures) -> bar-summary
prompt -> AsyncBackendToSyncAdapter over the keyring-configured
selene_space_llm endpoint -> TwoStageWorkflow(trading_decision tree,
TRADING_ROUTING_TABLE) -> AnalysisRecord + research_missions/mission_activity
persistence.

Run:  python scripts/run_pa_diagnosis.py [--bars 120] [--symbol BTC/USDT]
Env:  none required — LLM config comes from the OS keyring
      (provider "selenespace.llm": base_url/model/api_key), same store the
      settings UI writes. Fernet key: SELENE_ENCRYPTION_KEY.

Assembly logic lives in the shared package builder
(selene_space_decision_workflow.trading_pipeline); this module is the CLI
front: argument parsing, stance resolution, mission bookkeeping, and the
symbol-specific re-exports the test suite imports.
"""

from __future__ import annotations

import argparse
import httpx
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages" / "decision-workflow"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "storage"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "security-gateway"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "shared-types"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "llm-adapter"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "pulse"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "core"))

from selene_space_core.kline_paths import default_klines_db_path
from selene_space_decision_workflow.continuity import evaluate_continuity
from selene_space_decision_workflow.llm_compat import AsyncBackendToSyncAdapter
from selene_space_decision_workflow.orchestrator import (
    TRADING_STANCES,
    TwoStageWorkflow,  # noqa: F401  (test seam re-export)
)
from selene_space_decision_workflow.trading_pipeline import (
    TERMINAL_RE,  # noqa: F401  (test seam re-export)
    TRADING_TREE_PATH,
    bar_summary,  # noqa: F401  (test seam re-export)
    build_full_input_data,
    build_global_rules,
    build_trading_workflow,
    enrich_input_data,
    htf_summary,
    incremental_input,
    load_bars as _pipeline_load_bars,
)


def resolve_klines_db() -> Path:
    """Klines DB location: env override, else the canonical repo-root DB."""
    # cast: mypy resolves selene_space_core as Any (not on its search path),
    # which would trip warn_return_any; the helper's own ``-> Path`` is exact.
    return cast(Path, default_klines_db_path())


KLINES_DB = resolve_klines_db()
SELENE_DB = Path.home() / ".selenespace" / "data" / "selenespace.db"
TREE_PATH = TRADING_TREE_PATH


def load_bars(
    symbol: str,
    interval: str,
    bars: int,
    market_type: str = "futures",
    *,
    min_bars: int = 50,
) -> list[dict[str, Any]]:
    """Thin wrapper resolving the module-level KLINES_DB at call time so
    tests can monkeypatch rpd.KLINES_DB (test seam preserved)."""
    result: list[dict[str, Any]] = _pipeline_load_bars(
        KLINES_DB,
        symbol,
        interval,
        bars,
        market_type=market_type,
        min_bars=min_bars,
    )
    return result


async def build_async_backend() -> Any:
    from selene_space_llm.openai_compat import OpenAICompatProvider
    from selene_space_llm.base import LLMConfig
    from selene_space_security.secrets import SecretManager, SecretRef, SecretSource

    sm = SecretManager()
    values: dict[str, str] = {}
    for field in ("base_url", "model", "api_key"):
        ref = SecretRef(
            source=SecretSource.KEYRING, provider="selenespace.llm", id=field
        )
        resolved = await sm.resolve(ref)
        values[field] = resolved.use()
    if not values["api_key"]:
        raise RuntimeError(
            "keyring selenespace.llm/api_key empty — configure LLM in settings UI first"
        )
    # OpenAICompatProvider appends /chat/completions itself, so the stored
    # base_url must end at the version segment; tolerate a missing /v1.
    base_url = values["base_url"].rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    config = LLMConfig(
        model=values["model"],
        api_key=values["api_key"],
        base_url=base_url,
        temperature=0.0,
        # DeepSeek-R-family reasoning burns budget before content starts;
        # 2048 left content empty in probes, and 8192 was fully consumed by
        # reasoning alone on the full 120-bar window (finish=cut). 16384
        # yields ~8.4k reasoning + ~1.5k content.
        max_tokens=16384,
    )
    # Reasoning-model gateways can hold a cold 10-15k-token prompt for 2+
    # minutes before the first byte (measured 132s on glm-5.3-flash). The
    # default httpx timeouts kill such a connection mid-flight, surfacing as
    # "Server disconnected without sending a response" — so pin explicit,
    # patient timeouts here. (connect stays tight: a dead proxy should fail
    # fast, it is the read that must wait.)
    return OpenAICompatProvider(
        config,
        timeout=httpx.Timeout(connect=15.0, read=600.0, write=60.0, pool=15.0),
    )


class MissionPersistence:
    """Bridges AnalysisRecord -> research_missions.context_json + mission_activity."""

    def __init__(self, repo: Any, mission_id: str) -> None:
        self._repo = repo
        self._mission_id = mission_id

    async def save(self, record: Any) -> None:
        if isinstance(record, dict):
            rec = record
        else:
            rec = json.loads(record.model_dump_json())
        stage1_diag = rec["stage1"]["diagnosis_or_decision"]
        stage2_diag = rec["stage2"]["diagnosis_or_decision"]
        mode = rec.get("input_data", {}).get("summary", {}).get("mode", "full")
        await self._repo.append_activity(
            self._mission_id,
            actor="selene",
            kind="pa_diagnosis_record",
            detail={
                "input_id": rec["meta"]["input_id"],
                "mode": mode,
                "gate_result": stage1_diag.get("gate_result"),
                "terminal_outcome": stage2_diag.get("terminal_outcome"),
                "usage_total": rec.get("usage_total"),
                "usage_last_call": rec.get("real_usage_last_call"),
            },
        )
        await self._repo._db.execute(
            "UPDATE research_missions SET context_json = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps({"record": rec}, ensure_ascii=False), self._mission_id),
        )


class TeePersistence:
    """RecordPersistence Protocol impl: file + audit table + mission activity.

    The audit-table arm is what makes CLI runs visible to the Phase A
    emit-yield report (`scripts/eval_confidence_calibration.py` reads
    ``analysis_records``): without it, CLI-written records landed only in
    ``research_missions.context_json`` + the JSON file, and the audit corpus
    never grew. Same async-loop hop as the mission arm — wf.analyze runs on a
    worker thread, so every arm must hop back before touching aiosqlite.
    """

    def __init__(
        self,
        out_path: Path,
        mission_persister: MissionPersistence,
        backend: Any = None,
        record_repo: Any = None,
    ) -> None:
        self._out_path = out_path
        self._mission = mission_persister
        self._backend = backend
        self._record_repo = record_repo
        self.loop: asyncio.AbstractEventLoop | None = None
        self.last_record_id: str | None = None

    def save(self, record: dict) -> str:
        payload = record
        # Orchestrator's usage_total is a v1 placeholder (always {}); record
        # real per-call usage from the adapter's last response alongside it.
        last_usage = getattr(self._backend, "last_usage", None)
        if last_usage:
            payload["real_usage_last_call"] = last_usage
        self._out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if self.loop is not None:
            if self._record_repo is not None:
                self.last_record_id = asyncio.run_coroutine_threadsafe(
                    self._record_repo.save(record),
                    self.loop,
                ).result()
            asyncio.run_coroutine_threadsafe(
                self._mission.save(payload), self.loop
            ).result()
        return str(self._out_path)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=120)
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--interval", default="1h")
    ap.add_argument(
        "--incremental",
        action="store_true",
        help="diagnose only bars arrived since the last record (prior "
        "Stage1 summary + new bars, global K-numbering)",
    )
    ap.add_argument(
        "--stance",
        default=None,
        choices=list(TRADING_STANCES),
        help="trading stance for the Stage 2 decision prompt (default: "
        "SELENE_PA_STANCE env if set, else none; Stage 1 diagnosis stays "
        "stance-neutral; explicit flag wins over env)",
    )
    ap.add_argument(
        "--htf-interval",
        default=None,
        help="higher-timeframe interval for Stage 2 context, e.g. 4h for a "
        "1h diagnosis (default: none; Stage 1 stays HTF-blind)",
    )
    return ap


async def main() -> int:
    args = build_parser().parse_args()

    # Stance config (transitional until a settings UI exists): explicit
    # --stance flag wins; otherwise SELENE_PA_STANCE env is the default.
    stance = args.stance
    if stance is None:
        env_stance = os.environ.get("SELENE_PA_STANCE", "").strip().lower()
        if env_stance:
            if env_stance not in TRADING_STANCES:
                raise SystemExit(
                    f"SELENE_PA_STANCE={env_stance!r} invalid — "
                    f"expected one of {', '.join(TRADING_STANCES)}"
                )
            stance = env_stance

    global_rules, pa_stats_meta = build_global_rules(args.symbol, args.interval)
    backend = await build_async_backend()
    adapter = AsyncBackendToSyncAdapter(backend)

    from selene_space_storage.repositories.research_missions import (
        ResearchMissionRepository,
    )
    from selene_space_storage.sqlite import AsyncSQLiteManager

    db = AsyncSQLiteManager(SELENE_DB)
    await db.connect()
    try:
        repo = ResearchMissionRepository(db)
        all_bars = load_bars(args.symbol, args.interval, args.bars)

        # Higher-timeframe context (B2): aggregated only, Stage 2 user prompt.
        # Degraded (None + warning) when the HTF series is thin — a 4-bar
        # regime anchor is noise, never abort the primary diagnosis for it.
        htf: dict[str, Any] | None = None
        if args.htf_interval:
            # min_bars=0: the HTF anchor is optional context — a thin series
            # must degrade to "omitted", never abort the primary diagnosis.
            htf_bars = load_bars(
                args.symbol,
                args.htf_interval,
                args.bars,
                market_type="futures",
                min_bars=0,
            )
            if len(htf_bars) >= 50:
                htf = htf_summary(htf_bars, args.symbol, args.htf_interval)
            else:
                print(
                    json.dumps(
                        {
                            "htf_warning": (
                                f"insufficient {args.htf_interval} bars: "
                                f"{len(htf_bars)} < 50 — HTF context omitted"
                            )
                        },
                        ensure_ascii=False,
                    )
                )
        guard_print: dict[str, Any] | None = None
        if args.incremental:
            record_path = REPO_ROOT / "data" / "pa_diagnosis_last_record.json"
            if not record_path.is_file():
                raise RuntimeError(
                    "no prior record at data/pa_diagnosis_last_record.json — "
                    "run a full diagnosis first"
                )
            prior_record = json.loads(record_path.read_text(encoding="utf-8"))
            input_data, new_bars, last_decision = incremental_input(
                prior_record, all_bars, args.symbol, args.interval
            )
            # Deterministic pre-LLM guard (P2-001 rules) over the last
            # decision + newly arrived bars. Injected into input_data so
            # the LLM sees it (strategy txt §5.3 references the guard).
            if last_decision is not None:
                guard = evaluate_continuity(last_decision, new_bars)
                guard_print = {
                    "allowed": guard.allowed,
                    "rule": guard.rule,
                    "reason": guard.reason,
                }
                input_data["continuity_guard"] = guard_print
                print(json.dumps({"continuity_guard": guard_print}, ensure_ascii=False))
            # Shared material blocks (windows/overlays/experiences) over the
            # full bar list — same enrichment the full branch gets.
            input_data, overlays_hit = enrich_input_data(
                input_data, all_bars, args.symbol, args.interval
            )
            summary = input_data["summary"]
        else:
            input_data, overlays_hit = build_full_input_data(
                all_bars, args.symbol, args.interval
            )
            summary = input_data["summary"]

        # Create mission first (investigation phase), then run workflow.
        mission_id = await repo.create(
            title=f"PA diagnosis {args.symbol} {args.interval}",
            question="当前 1h 结构是什么状态?是否满足入场质量(gate)?",
            subject_type="market",
            subject_id=args.symbol,
            created_by="selene",
            context={
                "pipeline": "pa-two-stage",
                "tree": "trading_decision",
                "pa_stats_source": pa_stats_meta["source"],
                "pa_stats_version": pa_stats_meta["version"],
                "trading_stance": stance,
                "stance_source": (
                    "flag" if args.stance else ("env" if stance else None)
                ),
                "htf_interval": args.htf_interval,
                "overlay_hits": sorted(overlays_hit) if overlays_hit else [],
                # A2 observation item (non-blocking): after a real run,
                # review stage2 decision_summary.next_period for base-rate
                # abuse (probability numbers / unconditioned rates quoted
                # as conditional). Reserve stays unfilled until calibration.
                "next_period_watch": "observe-base-rate-abuse",
                **({"mode": "incremental"} if args.incremental else {}),
            },
        )

        # Wire the async loop into the sync TeePersistence via thread handoff.
        loop = asyncio.get_running_loop()

        def make_persister(mission_id: str) -> TeePersistence:
            mp = MissionPersistence(repo, mission_id)
            from selene_space_storage.repositories.analysis_records import (
                AnalysisRecordRepository,
            )

            record_repo = AnalysisRecordRepository(db)
            tp = TeePersistence(
                REPO_ROOT / "data" / "pa_diagnosis_last_record.json",
                mp,
                backend=adapter,
                record_repo=record_repo,
            )
            tp.loop = loop
            return tp

        persister = make_persister(mission_id)
        wf, _ = build_trading_workflow(
            llm=adapter,
            persistence=persister,
            symbol=args.symbol,
            interval=args.interval,
            trading_stance=stance,
            htf_summary_data=htf,
            overlay_patterns=overlays_hit if overlays_hit else None,
        )
        # Orchestrator builds its own system prompt from tree+rules.
        # wf.analyze is synchronous and blocks; run it off the loop thread so
        # TeePersistence.save can hop back to this loop via
        # run_coroutine_threadsafe (analyze-on-loop would deadlock both).
        mode_prefix = "pa-incr" if args.incremental else f"pa-{args.interval}"
        record = await asyncio.to_thread(
            wf.analyze,
            input_data,
            input_id=f"{mode_prefix}-{summary['window_end_iso']}",
            session_id=mission_id,
        )

        gate = record.stage1.diagnosis_or_decision.get("gate_result")
        # Gate short-circuit guarantees stage2 exists (wait record), but the
        # dataclass types it optional — narrow explicitly.
        stage2 = record.stage2
        outcome = (
            stage2.diagnosis_or_decision.get("terminal_outcome")
            if stage2 is not None
            else None
        )
        print(
            json.dumps(
                {
                    "mission_id": mission_id,
                    "record_id": getattr(persister, "last_record_id", None),
                    "input_id": record.meta.input_id,
                    "incremental": bool(args.incremental),
                    "continuity_guard": guard_print,
                    "gate_result": gate,
                    "terminal_outcome": outcome,
                    "usage_total": record.usage_total,
                    "real_usage_last_call": getattr(adapter, "last_usage", None),
                    "output_file": str(
                        REPO_ROOT / "data" / "pa_diagnosis_last_record.json"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        # P4-A: env-gated brief notification (SELENE_PA_NOTIFY_ENABLED=1).
        # Record dict is what TeePersistence already wrote — same shape.
        record_dict = json.loads(record.model_dump_json())
        from pa_research.pa_notify import maybe_notify_pa

        notify_result = maybe_notify_pa(record_dict)
        if notify_result is not None:
            print(
                json.dumps(
                    {"pa_notify": notify_result},
                    ensure_ascii=False,
                )
            )

        # Gate short-circuit evidence requirement: if this run proceeded, also
        # run a deliberately starved-data diagnosis to exercise the wait path.
        if gate == "proceed":
            starved = dict(input_data)
            starved["summary"] = dict(summary)
            starved["summary"]["bars"] = []
            starved["summary"]["bars_total"] = 0
            starved["summary"]["note"] = "data feed intentionally empty for gate test"
            record2 = await asyncio.to_thread(
                wf.analyze,
                starved,
                input_id=f"pa-gate-test-{summary['window_end_iso']}",
                session_id=mission_id,
            )
            print(
                json.dumps(
                    {
                        "gate_test_input_id": record2.meta.input_id,
                        "gate_test_gate_result": record2.stage1.diagnosis_or_decision.get(
                            "gate_result"
                        ),
                    },
                    ensure_ascii=False,
                )
            )
        return 0
    finally:
        await db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
