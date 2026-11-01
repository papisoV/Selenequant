"""Keltner V2 REVALIDATE — 垂直切片 Approve→真实回测→OOS→Outcome 管线。

用法:python scripts/keltner_revalidate.py [--approve] [--dry-run]

链路(严格不 mock):
  1. 读主库 Mission mis-*(Keltner V2 配置漂移调查,needs_input)
  2. --approve:phase needs_input → validation(user 动作,B1 状态机)
  3. 加载策略声明配置(strat-6395c312)+ BTC/USDT 1h 真实 K 线(klines.db)
  4. 修正配置回声明意图(BTC/USDT 1h,非 SOL 4h),跑真实回测
  5. 时间窗 2/3 in-sample + 1/3 OOS(baseline 之后段),双段各跑一次
  6. Outcome 落库 mission outcome_json + 活动流追记录
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "storage"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "market-data"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "strategy-engine"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "backtest-engine"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "pulse"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "shared-types"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "core"))

from selene_space_backtest.commissions import PercentageCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_market.kline_db import KlineDB
from selene_space_storage.repositories.research_missions import (
    ResearchMissionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager
from selene_space_strategy.templates.presets.keltner_v2 import (
    KeltnerChannelV2Strategy,
)

STRATEGY_ID = "strat-6395c312"
MISSION_QUESTION = "SOL 4h 实跑亏损是配置漂移还是框架失效?"
KLINES_DB = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"


def build_strategy(
    params: dict, symbol: str, initial_cash: float
) -> KeltnerChannelV2Strategy:
    return KeltnerChannelV2Strategy(
        symbol=symbol,
        initial_capital=initial_cash,
        ma_length=int(params["ma_length"]),
        atr_length=int(params["atr_length"]),
        mult=float(params["mult"]),
        adx_length=int(params["adx_length"]),
        adx_threshold=float(params["adx_threshold"]),
        stop_pct=float(params["stop_pct"]),
        tp_pct=float(params["tp_pct"]),
        enable_long=bool(params.get("enable_long", True)),
        enable_short=bool(params.get("enable_short", True)),
    )


def run_backtest(df, params: dict, symbol: str, strategy_id: str, interval: str):
    strategy = build_strategy(params, symbol, 10_000.0)
    config = BacktestConfig(
        initial_cash=10_000.0,
        commission=PercentageCommission(rate=0.001),
        strategy_id=strategy_id,
        symbol=symbol,
        interval=interval,
    )
    return BacktestEngine(config=config).run(strategy, df)


def metrics_to_dict(result) -> dict:
    m = result.metrics
    return {
        "total_return": m.total_return,
        "annual_return": m.annual_return,
        "sharpe_ratio": m.sharpe_ratio,
        "max_drawdown": m.max_drawdown,
        "win_rate": m.win_rate,
        "profit_factor": m.profit_factor,
        "total_trades": m.total_trades,
        "calmar_ratio": m.calmar_ratio,
        "sortino_ratio": m.sortino_ratio,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve", action="store_true", help="user 批准 REVALIDATE")
    parser.add_argument("--dry-run", action="store_true", help="只查不写")
    args = parser.parse_args()

    async with AsyncSQLiteManager() as db:
        repo = ResearchMissionRepository(db)

        # 1. Mission 存在性(Investigate 唯一性:同对象+同问题)
        mission = await repo.find_open_by_question(
            "strategy", STRATEGY_ID, MISSION_QUESTION
        )
        if mission is None:
            print("未找到 open Mission(Keltner 配置漂移调查)")
            return 1
        print(f"Mission: {mission['id']} phase={mission['phase']}")

        if args.dry_run:
            print("dry-run:只查不写,到此为止")
            return 0

        # 2. Approve:user 决策,needs_input → validation
        if args.approve:
            if mission["phase"] != "needs_input":
                print(f"phase={mission['phase']},无需 approve(已过决策点)")
            else:
                await repo.transition(
                    mission["id"],
                    "validation",
                    actor="user",
                    note="Approve REVALIDATE — 修正配置回声明意图后重验证",
                )
                print("Approved: needs_input → validation")

        # 3. 声明配置(已验证在 strategies.config_json)
        rows = await db.execute(
            "SELECT config_json FROM strategies WHERE id = ?", (STRATEGY_ID,)
        )
        if not rows:
            print(f"策略行缺失: {STRATEGY_ID}")
            return 1
        config = json.loads(rows[0][0])
        declared = {
            "symbol": config["symbols"][0],
            "interval": config["interval"],
        }
        params = config["parameters"]
        print(f"声明意图: {declared}")
        print(f"参数: { {k: v for k, v in params.items()} }")

        # 4. 真实 K 线(klines.db,BTC/USDT 1h)
        kdb = KlineDB(db_path=str(KLINES_DB))
        df = kdb.get_klines("binance", "spot", declared["symbol"], declared["interval"])
        if df is None or len(df) < 500:
            print(f"K 线不足: {0 if df is None else len(df)} bars")
            return 1
        print(f"真实 K 线: {declared['symbol']} {declared['interval']} {len(df)} bars")

        # 5. 双段:2/3 IS + 1/3 OOS(Temporal Integrity:OOS 用未来段,不选参)
        split = int(len(df) * 2 / 3)
        is_df, oos_df = df.iloc[:split], df.iloc[split:]
        print(f"时间窗: IS {len(is_df)} bars / OOS {len(oos_df)} bars")

        # baseline 对照:SOL 4h 原漂移窗(仅作对照,不重跑)
        baseline = mission["context"].get("anomaly", {})

        # 6. 真实回测(修正配置 = BTC 1h)
        print("跑 IS(declared intent)...")
        is_result = run_backtest(
            is_df,
            params,
            declared["symbol"],
            f"{STRATEGY_ID}-revalidate",
            declared["interval"],
        )
        print("跑 OOS(declared intent)...")
        oos_result = run_backtest(
            oos_df,
            params,
            declared["symbol"],
            f"{STRATEGY_ID}-revalidate",
            declared["interval"],
        )

        is_m, oos_m = metrics_to_dict(is_result), metrics_to_dict(oos_result)
        print(f"IS : {json.dumps(is_m, ensure_ascii=False)}")
        print(f"OOS: {json.dumps(oos_m, ensure_ascii=False)}")

        # 7. Outcome 落库
        outcome = {
            "verdict": "REVALIDATED"
            if oos_m["total_return"] > 0
            else "FRAMEWORK_SUSPECT",
            "declared_intent": declared,
            "in_sample": is_m,
            "oos": oos_m,
            "baseline_sOL4h_drift": baseline,
            "method": "IS 2/3 + OOS 1/3, declared config BTC/USDT 1h, no re-tuning",
        }
        await repo.record_outcome(mission["id"], outcome, actor="selene")
        await repo.append_activity(
            mission["id"],
            actor="selene",
            kind="revalidate_complete",
            detail={"oos_return": oos_m["total_return"], "verdict": outcome["verdict"]},
        )
        print(f"Outcome 落库: verdict={outcome['verdict']}")

        # P4-A: env-gated notification on the revalidate verdict. Reuses the
        # pulse multi-channel helper directly — this is a strategy revalidate
        # event, not a PA diagnosis record, so no PA brief rendering applies.
        import os as _os

        if _os.environ.get("SELENE_PA_NOTIFY_ENABLED", "").strip() == "1":
            try:
                from selene_space_pulse.notify import send_trade_alert as _alert

                _brief = (
                    f"REVALIDATE {declared['symbol']} {declared['interval']}: "
                    f"verdict={outcome['verdict']} "
                    f"IS {is_m['total_return']:.2%} / OOS {oos_m['total_return']:.2%}"
                )
                _alert(_brief)
            except Exception:  # noqa: BLE001
                print("PA notify failed (non-fatal)")

        # 8. 收口:validation → resolved(结案连锁:Memory/Intelligence 在前端层接)
        if mission["phase"] == "validation":
            await repo.transition(
                mission["id"],
                "resolved",
                actor="selene",
                note=f"REVALIDATE 完成,OOS {oos_m['total_return']:.2%}",
            )
            print("Mission resolved(环闭合)")

        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
