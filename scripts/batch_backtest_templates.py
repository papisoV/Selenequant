#!/usr/bin/env python3
"""Batch backtest all factory templates.

Usage:
    python scripts/batch_backtest_templates.py

Requires:
    - Backend running at http://127.0.0.1:8088
    - Or set SELENE_API_URL environment variable
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

API_BASE = os.environ.get("SELENE_API_URL", "http://127.0.0.1:8088")
TIMEOUT = 120.0  # seconds

# Backtest parameters
SYMBOL = "BTC/USDT"
INTERVAL = "4h"
EXCHANGE = "binance"
MARKET_TYPE = "futures"
START_DATE = "2025-10-01"

POLL_SECONDS = 2
_RULE = "=" * 60


class _BatchAborted(Exception):
    """Raised when setup cannot proceed (fatal, exits the run)."""


def _params() -> dict[str, Any]:
    """Backtest request body (end date is always 'today')."""
    return {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "exchange": EXCHANGE,
        "market_type": MARKET_TYPE,
        "start_date": START_DATE,
        "end_date": datetime.now().strftime("%Y-%m-%d"),
        "initial_capital": 10_000.0,
        "commission_rate": 0.001,
    }


def _banner(params: dict[str, Any]) -> None:
    print(_RULE)
    print("Batch Backtest All Factory Templates")
    print(_RULE)
    print(f"Symbol:      {params['symbol']}")
    print(f"Interval:    {params['interval']}")
    print(f"Exchange:    {params['exchange']}/{params['market_type']}")
    print(f"Date Range:  {params['start_date']} ~ {params['end_date']}")
    print(f"Capital:     {params['initial_capital']:,.0f}")
    print(_RULE)


async def _fetch_templates(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """GET the factory template catalogue. Aborts the run if unavailable."""
    resp = await client.get(f"{API_BASE}/api/v1/strategies/factory-templates")
    if resp.status_code != 200:
        print(f"ERROR: Failed to get templates: {resp.status_code}")
        print(resp.text)
        raise _BatchAborted
    templates: list[dict[str, Any]] = resp.json().get("data", [])
    print(f"Found {len(templates)} templates")
    return templates


async def _instantiate(
    client: httpx.AsyncClient, tmpl: dict[str, Any]
) -> dict[str, Any] | None:
    """Create one strategy from a template. Returns its descriptor or None."""
    tid = tmpl["id"]
    print(f"  Creating: {tmpl['name']} ({tid})...", end=" ", flush=True)

    resp = await client.post(
        f"{API_BASE}/api/v1/strategies/factory-templates/{tid}/create"
    )
    if resp.status_code != 200:
        print(f"FAILED ({resp.status_code})")
        print(f"    {resp.text[:200]}")
        return None

    body = resp.json().get("data", {})
    sid = body.get("id") or body.get("strategy_id")
    if not sid:
        print("FAILED (no strategy_id in response)")
        print(f"    {resp.text[:200]}")
        return None

    print("OK")
    return {
        "id": sid,
        "name": tmpl["name"],
        "category": tmpl["category"],
        "template_id": tid,
    }


async def _await_task(client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
    """Poll a backtest task until it leaves {pending, running}; return its data."""
    started = time.time()
    status = "pending"
    while status in ("pending", "running"):
        if time.time() - started > TIMEOUT:
            print("    TIMEOUT!")
            return {"status": "timeout"}

        await asyncio.sleep(POLL_SECONDS)
        resp = await client.get(f"{API_BASE}/api/v1/backtests/{task_id}")
        if resp.status_code != 200:
            print(f"    Polling error: {resp.status_code}")
            break

        data = resp.json().get("data", {})
        status = data.get("status", "pending")
        print(
            f"    Status: {status} ({int(time.time() - started)}s)",
            end="\r",
            flush=True,
        )

    print(f"\n    Final status: {status}")
    return {"status": status}


def _summarize_row(
    strat: dict[str, Any], task_id: str, metrics: dict[str, Any], trades: int
) -> dict[str, Any]:
    """Flatten one completed backtest into the report row shape."""
    return {
        "name": strat["name"],
        "category": strat["category"],
        "template_id": strat["template_id"],
        "strategy_id": strat["id"],
        "task_id": task_id,
        "total_return": metrics.get("total_return", 0),
        "sharpe_ratio": metrics.get("sharpe_ratio", 0),
        "max_drawdown": metrics.get("max_drawdown", 0),
        "win_rate": metrics.get("win_rate", 0),
        "trades": trades,
    }


async def _run_one(
    client: httpx.AsyncClient, strat: dict[str, Any], params: dict[str, Any]
) -> dict[str, Any] | None:
    """Kick off a backtest for one strategy and collect its metrics."""
    resp = await client.post(
        f"{API_BASE}/api/v1/backtests",
        json={
            "strategy_id": strat["id"],
            **params,
        },
    )
    if resp.status_code not in (200, 202):
        print(f"    FAILED to start backtest: {resp.status_code}")
        print(f"    {resp.text[:200]}")
        return None

    task_id = resp.json().get("data", {}).get("task_id")
    if not task_id:
        print("    FAILED: no task_id in response")
        return None
    print(f"    Task ID: {task_id}")

    outcome = await _await_task(client, task_id)
    if outcome["status"] != "completed":
        if outcome["status"] == "failed":
            resp = await client.get(f"{API_BASE}/api/v1/backtests/{task_id}")
            if resp.status_code == 200:
                print(
                    f"    Error: {resp.json().get('data', {}).get('error', 'Unknown error')}"
                )
        return None

    resp = await client.get(f"{API_BASE}/api/v1/backtests/{task_id}")
    if resp.status_code != 200:
        return None
    payload = resp.json().get("data", {})
    result = payload.get("result", {})
    metrics = result.get("metrics", {})

    for label, key, fmt in (
        ("Return", "total_return", "{:.2f}%"),
        ("Sharpe", "sharpe_ratio", "{:.2f}"),
        ("Max DD", "max_drawdown", "{:.2f}%"),
        ("Win Rate", "win_rate", "{:.1f}%"),
    ):
        value = metrics.get(key, 0)
        print(
            f"    {label}: " + fmt.format(value * 100 if fmt.endswith("%") else value)
        )
    trades = result.get("trades_count", 0)
    print(f"    Trades: {trades}")

    return _summarize_row(strat, task_id, metrics, trades)


def _print_table(rows: list[dict[str, Any]]) -> None:
    header = (
        f"{'Rank':<4} {'Template':<25} {'Category':<12} {'Return':>10} "
        f"{'Sharpe':>8} {'Max DD':>8} {'Win%':>6} {'Trades':>6}"
    )
    print(f"\n{header}")
    print("-" * 85)
    for rank, r in enumerate(rows, 1):
        print(
            f"{rank:<4} {r['name']:<25} {r['category']:<12} "
            f"{r['total_return'] * 100:>9.2f}% {r['sharpe_ratio']:>8.2f} "
            f"{r['max_drawdown'] * 100:>7.2f}% {r['win_rate'] * 100:>5.1f}% "
            f"{r['trades']:>6}"
        )


def _write_report(rows: list[dict[str, Any]], params: dict[str, Any]) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = Path(__file__).parent / f"batch_backtest_{stamp}.json"
    output_file.write_text(
        json.dumps({"params": params, "results": rows}, indent=2), encoding="utf-8"
    )
    return output_file


async def main() -> None:
    params = _params()
    _banner(params)

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        print("\n[1/3] Fetching factory templates...")
        try:
            templates = await _fetch_templates(client)
        except _BatchAborted:
            sys.exit(1)

        print("\n[2/3] Creating strategies from templates...")
        strategies = [
            s for s in [await _instantiate(client, t) for t in templates] if s
        ]
        print(f"\nCreated {len(strategies)} strategies")

        print("\n[3/3] Running backtests...")
        results: list[dict[str, Any]] = []
        for i, strat in enumerate(strategies, 1):
            print(f"\n[{i}/{len(strategies)}] {strat['name']}")
            print(f"    Strategy ID: {strat['id']}")
            row = await _run_one(client, strat, params)
            if row:
                results.append(row)

    print("\n" + _RULE)
    print("BACKTEST SUMMARY")
    print(_RULE)

    if not results:
        print("No successful backtests!")
    else:
        results.sort(key=lambda r: r["total_return"], reverse=True)
        _print_table(results)
        print(f"\nResults saved to: {_write_report(results, params)}")

    print("\n" + _RULE)


if __name__ == "__main__":
    asyncio.run(main())
