#!/usr/bin/env python
"""运营回环：用真实 LLM 逐窗口灌 next_bar_forecast 分析记录。

每窗口 = 一段连续 K 线（止于目标 bar 之前），运行 MarketAnalysisFlow.analyze
两阶段分析并落盘到分析记录库，供 scripts/eval_next_bar.py 用真实后续 K 线评分。

Usage:
    python scripts/populate_records_operational.py
    python scripts/populate_records_operational.py --windows 45 --window-bars 120
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for pkg in sorted((_REPO_ROOT / "packages").glob("*")):
    if pkg.is_dir() and str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))

from selene_space_llm.base import LLMConfig  # noqa: E402
from selene_space_market_analysis.data_source import KlineSource  # noqa: E402
from selene_space_market_analysis.flow import MarketAnalysisFlow  # noqa: E402
from selene_space_market_analysis.llm_provider import HttpLLMProvider  # noqa: E402
from selene_space_market_analysis.persistence_sqlite import SqliteRecordPersistence  # noqa: E402
from selene_space_security.secrets import (  # noqa: E402
    SecretManager,
    SecretRef,
    SecretSource,
)
from selene_space_storage.migrations import MigrationRunner  # noqa: E402
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS  # noqa: E402
from selene_space_storage.sqlite import AsyncSQLiteManager, DEFAULT_DB_PATH  # noqa: E402

_MAX_KLINES_INPUT = 60


def _downsample(df, max_bars: int):
    """均匀降采样，始终保留最新一根（与 data_source._downsample 同语义）。"""
    n = len(df)
    if n <= max_bars:
        return df
    idx = sorted({(n - 1) * i // (max_bars - 1) for i in range(max_bars)})
    return df.iloc[idx]


async def _resolve_llm_config() -> LLMConfig:
    sm = SecretManager()

    async def _get(field: str) -> str | None:
        try:
            secret = await sm.resolve(
                SecretRef(
                    source=SecretSource.KEYRING,
                    provider="selenespace.llm",
                    id=field,
                )
            )
            return secret.use()
        except Exception:
            return None

    model = (await _get("model")) or "default"
    api_key = (await _get("api_key")) or ""
    base_url = await _get("base_url")
    # v4-flash 为推理模型：reasoning_content 会吃掉大量输出预算，默认 4096
    # max_tokens 在 finish_reason=length 处截断 JSON（实测 reasoning 用掉 3765）。
    # 直接关思考（thinking.type=disabled 实测 reasoning_len 归零、0.9s 返回）。
    return LLMConfig(
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_tokens=8192,
        extra_body={"thinking": {"type": "disabled"}},
    )


async def _main(args: argparse.Namespace) -> None:
    llm_config = await _resolve_llm_config()
    print(f"llm: model={llm_config.model} base_url={llm_config.base_url} "
          f"api_key_set={bool(llm_config.api_key)}")

    source = KlineSource(args.klines_db)
    df = source.load(
        args.exchange,
        args.market_type,
        args.symbol,
        args.interval,
        limit=args.klines_limit,
    )
    n = len(df)
    print(f"klines loaded: {n} bars ({args.symbol} {args.interval})")
    if n < args.window_bars + args.windows + 1:
        print("ERROR: 数据不足，无法构造目标窗口")
        return

    # 目标 bar = 最近的 windows 个（不含最后一根，保证后续 K 线存在）。
    # stride > 1 时窗口不连续，输入重叠降低、预测近独立（连续窗口 99% 重叠无统计意义）。
    last_target = n - 1 - args.stride
    targets = list(
        range(
            last_target - (args.windows - 1) * args.stride,
            last_target + 1,
            args.stride,
        )
    )
    print(f"target bars: {len(targets)} (last target index {targets[-1]}, 后续 bar 存在)")

    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(args.db, migration_runner=runner) as db:
        persistence = SqliteRecordPersistence(db)
        llm = HttpLLMProvider(llm_config)
        flow = MarketAnalysisFlow(llm=llm, persistence=persistence)

        summary: list[dict] = []
        for i, t in enumerate(targets):
            window_df = df.iloc[max(0, t - args.window_bars): t]
            if len(window_df) < 50:
                print(f"  [{i + 1}/{len(targets)}] skip: window too short ({len(window_df)})")
                continue
            klines_df = _downsample(window_df, _MAX_KLINES_INPUT)
            input_data = {
                "exchange": args.exchange,
                "market_type": args.market_type,
                "symbol": args.symbol,
                "interval": args.interval,
                "klines": [
                    {
                        "timestamp": int(row["timestamp"]),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                    }
                    for _, row in klines_df.iterrows()
                ],
                "bar_features": source.compute_bar_features(window_df),
            }
            try:
                record = await flow.analyze(
                    input_data,
                    input_id=f"oploop-{i:04d}",
                    session_id="operational-loop-20260814",
                )
                stage1 = record.get("stage1") or {}
                stage2 = record.get("stage2") or {}
                forecast = (stage2.get("next_bar_forecast") or {}).get("direction")
                usage = record.get("usage_total") or {}
                row = {
                    "idx": i,
                    "record_id": (record.get("meta") or {}).get("record_id"),
                    "target_ts": int(window_df.iloc[-1]["timestamp"]),
                    "gate": stage1.get("entry_gate"),
                    "decision": stage2.get("decision"),
                    "forecast": forecast,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                }
                summary.append(row)
                print(
                    f"  [{i + 1}/{len(targets)}] {row['record_id']} "
                    f"gate={row['gate']} forecast={row['forecast']} "
                    f"tokens={row['prompt_tokens']}/{row['completion_tokens']}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i + 1}/{len(targets)}] ERROR {type(exc).__name__}: {exc}")
                summary.append({"idx": i, "error": f"{type(exc).__name__}: {exc}"})

    ok = [r for r in summary if "record_id" in r]
    with_forecast = [r for r in ok if r.get("forecast")]
    print(f"\nsummary: persisted={len(ok)}/{len(targets)} with_forecast={len(with_forecast)}")
    for r in ok:
        print(f"  {r['record_id']} gate={r['gate']} forecast={r['forecast']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="运营回环灌分析记录")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="分析记录库")
    parser.add_argument("--klines-db", default=str(KlineSource().db_path), help="K 线数据源")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--market-type", default="futures")
    parser.add_argument("--klines-limit", type=int, default=5000, help="K 线源加载条数")
    parser.add_argument("--windows", type=int, default=45, help="目标窗口数")
    parser.add_argument("--stride", type=int, default=1, help="窗口目标 bar 间隔（条，>1 提高独立性）")
    parser.add_argument("--window-bars", type=int, default=120, help="每窗口 K 线数")
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
