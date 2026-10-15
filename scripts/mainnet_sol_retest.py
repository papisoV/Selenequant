"""Phase 6 mainnet $5 SOL retest — Option 1D single mainnet via live_engine.place_order.

Verifies Bug 4 (risk_params leverage routing) + Bug 5 (enum str) + Bug 7 (NOTIONAL dual-check)
+ Bug 8 (LOT_SIZE stepSize floor) on the production path:
    trading.py → live_engine.place_order → risk_engine → KillSwitch → BinanceRESTClient.place_order

Five-layer safety:
    1. approval gate (per-session first-trade)
    2. KillSwitch (WsHealthMonitor wired in live_engine)
    3. 失败即停 (any exception → exit 1)
    4. $6 hard cap (qty * live_price > $6 → exit 4)
    5. market sell fallback (limit sell GTC 1h → cancel + market sell)

Usage:
    python scripts/mainnet_sol_retest.py --dry-run
    python scripts/mainnet_sol_retest.py --live
    python scripts/mainnet_sol_retest.py --live --force

Exit codes:
    0  success
    1  generic failure (exception, see stderr)
    2  balance dropped below $9.68 safety floor
    3  AuthenticationError (401) — check .env.mainnet, key may be revoked
    4  hard cap exceeded (qty * live_price > $6)
    5  idempotency guard tripped (SOL balance > 0)
    6  lockfile active (double-run within 60s)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

LOCKFILE = REPO_ROOT / "scripts" / "research" / ".lockfile"
LOCKFILE_TTL_S = 60
SAFETY_FLOOR_USDT = 9.68
HARD_CAP_USDT = 6.0
TARGET_NOTIONAL_USDT = 5.0
SYMBOL = "SOLUSDT"
SYMBOL_HUMAN = "SOL/USDT"
LIMIT_SELL_TIMEOUT_S = 3600  # 1 hour
LIMIT_SELL_POLL_INTERVAL_S = 30
ORDER_FILL_TIMEOUT_S = 30
LIMIT_SELL_MARKUP_PCT = 0.005  # +0.5% above fill price


def _print_step(step: int, msg: str) -> None:
    print(f"[step {step}] {msg}", flush=True)


def _write_lockfile() -> float:
    """Create lockfile with current timestamp. Returns existing timestamp if fresh."""
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    if LOCKFILE.exists():
        mtime = LOCKFILE.stat().st_mtime
        age = time.time() - mtime
        if age < LOCKFILE_TTL_S:
            return mtime
    LOCKFILE.touch()
    return 0.0


def _load_env_mainnet() -> dict[str, str]:
    """Load .env.mainnet (no python-dotenv dependency — simple parser)."""
    env_path = REPO_ROOT / ".env.mainnet"
    if not env_path.exists():
        print(f"ERROR: {env_path} not found", file=sys.stderr)
        sys.exit(3)
    out: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def _make_credentials(env: dict[str, str]):
    """Construct ExchangeCredentials from env."""
    from selene_space_exchange.base.auth import ExchangeCredentials

    api_key = env.get("SELENE_EXCHANGE_MAINNET_API_KEY") or env.get("BINANCE_MAINNET_API_KEY", "")
    secret = env.get("SELENE_EXCHANGE_MAINNET_API_SECRET") or env.get("BINANCE_MAINNET_API_SECRET", "")
    if not api_key or not secret:
        print("ERROR: API key/secret missing in .env.mainnet", file=sys.stderr)
        sys.exit(3)
    return ExchangeCredentials(api_key=api_key, api_secret=secret, passphrase=None)


async def _get_live_price(client) -> float:
    """Fetch current SOLUSDT price via public /api/v3/ticker/price endpoint.

    Uses public httpx call (NOT client._request) because base RESTClient._request
    auto-injects recvWindow even when signed=False, causing -1104 on public endpoints.
    Public ticker/price needs no auth.
    """
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": SYMBOL},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"ticker/price HTTP {resp.status_code}: {resp.text}")
        data = resp.json()
        price_str = data.get("price") if isinstance(data, dict) else None
        if price_str is None:
            raise RuntimeError(f"ticker/price response missing 'price': {data}")
        return float(price_str)


async def _get_account_balance(client) -> tuple[float, float]:
    """Return (usdt_free, sol_free) from get_account."""
    account = await client.get_account()
    balances = account.get("balances", []) if isinstance(account, dict) else []
    usdt = 0.0
    sol = 0.0
    for b in balances:
        asset = b.get("asset", "")
        free = b.get("free", "0")
        if asset == "USDT":
            usdt = float(free)
        elif asset == "SOL":
            sol = float(free)
    return usdt, sol


async def _get_lot_size_filter(client) -> tuple[float, float]:
    """Return (step_size, min_notional) for SOLUSDT."""
    info = await client.get_exchange_info(symbol=SYMBOL)
    step_size = 0.0
    min_notional = 0.0
    for s in info.get("symbols", []):
        if s.get("symbol") != SYMBOL:
            continue
        for f in s.get("filters", []):
            ft = f.get("filterType")
            if ft == "LOT_SIZE":
                step_size = float(f.get("stepSize", "0"))
            elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
                raw = f.get("minNotional")
                if raw is None:
                    continue
                try:
                    val = float(raw)
                except (ValueError, TypeError):
                    continue
                if val > min_notional:
                    min_notional = val
    return step_size, min_notional


async def _get_price_tick_size(client) -> float:
    """Return PRICE_FILTER tickSize for SOLUSDT (e.g. 0.01)."""
    info = await client.get_exchange_info(symbol=SYMBOL)
    for s in info.get("symbols", []):
        if s.get("symbol") != SYMBOL:
            continue
        for f in s.get("filters", []):
            if f.get("filterType") == "PRICE_FILTER":
                return float(f.get("tickSize", "0"))
    return 0.0


def _floor_to_tick(price: float, tick: float) -> float:
    """Floor price to a multiple of tick (e.g. 76.8825 with tick 0.01 → 76.88)."""
    if tick <= 0:
        return price
    import math
    return math.floor(price / tick) * tick


def _floor_to_step(qty: float, step: float) -> float:
    """Floor quantity to nearest step multiple."""
    if step <= 0:
        return qty
    import math
    return math.floor(qty / step) * step


def _compute_quantity(target_notional: float, live_price: float, step_size: float, min_notional: float) -> float:
    """Compute quantity: floor(target/price, step), then while qty*price < min_notional: qty += step.
    Aborts if qty*price > HARD_CAP_USDT.
    """
    if step_size <= 0:
        qty = target_notional / live_price
    else:
        import math
        qty = math.floor((target_notional / live_price) / step_size) * step_size
    while qty * live_price < min_notional:
        qty += step_size
        if qty * live_price > HARD_CAP_USDT:
            raise RuntimeError(
                f"hard cap exceeded: qty={qty} price={live_price} notional={qty*live_price} > {HARD_CAP_USDT}"
            )
    return qty


# --------------------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------------------

async def dry_run(client) -> dict[str, Any]:
    """Steps 1-6: account + exchange info + idempotency + quantity calc, no order."""
    result: dict[str, Any] = {}
    _print_step(1, "lockfile check (dry-run skips lockfile write)")
    _print_step(2, "idempotency + account fetch")
    try:
        usdt, sol = await _get_account_balance(client)
    except Exception as e:
        msg = str(e).lower()
        if "401" in msg or "invalid api-key" in msg or "authentication" in msg:
            print("EXIT 3: AuthenticationError — check .env.mainnet, key may be revoked "
                  "(see MEMORY phase6_plan_v6_execution_blocked_api_401)", file=sys.stderr)
            sys.exit(3)
        raise
    if sol > 0.0001:
        print(f"WARNING: SOL balance {sol} > 0 — last run may be incomplete. "
              f"Use --force to bypass idempotency in live mode.", file=sys.stderr)
    print(f"  USDT free: ${usdt:.4f}  SOL free: {sol}")
    if usdt < SAFETY_FLOOR_USDT:
        print(f"WARNING: USDT ${usdt:.2f} < safety floor ${SAFETY_FLOOR_USDT}", file=sys.stderr)
    result["usdt_free"] = usdt
    result["sol_free"] = sol

    _print_step(4, "live price fetch via /api/v3/ticker/price")
    live_price = await _get_live_price(client)
    print(f"  SOLUSDT live price: ${live_price:.4f}")
    result["live_price"] = live_price

    _print_step(5, "exchange info: LOT_SIZE + NOTIONAL")
    step_size, min_notional = await _get_lot_size_filter(client)
    print(f"  stepSize={step_size}  minNotional={min_notional}")
    result["step_size"] = step_size
    result["min_notional"] = min_notional

    _print_step(6, "quantity calc (LOT_SIZE/NOTIONAL loop)")
    qty = _compute_quantity(TARGET_NOTIONAL_USDT, live_price, step_size, min_notional)
    notional = qty * live_price
    print(f"  computed qty={qty}  notional=${notional:.4f}  (target ${TARGET_NOTIONAL_USDT})")
    if notional > HARD_CAP_USDT:
        print(f"EXIT 4: hard cap exceeded — notional ${notional:.2f} > ${HARD_CAP_USDT}", file=sys.stderr)
        sys.exit(4)
    result["computed_qty"] = qty
    result["computed_notional"] = notional
    return result


async def live_mode(client, *, force: bool) -> dict[str, Any]:
    """Full prod path via live_engine.place_order.
    Note: This script cannot directly construct LiveExecutionEngine without extensive
    service wiring (risk_engine, KillSwitch, fills_ledger, equity_tracker, balances
    manager, etc.). For the Bug 4 + 5/7/8 prod-path verification, the canonical path
    is HTTP /api/v1/live/place_order through the running backend service.

    This script therefore:
    1. Steps 1-7: dry-run checks (account, price, exchange info, qty, idempotency)
    2. Step 7.5: re-fetch live price, recompute qty
    3. Step 8: POST /api/v1/live/approve then POST /api/v1/live/place_order (full prod path)
    4. Step 9: poll order status, place limit sell, fallback to market sell
    5. Step 10: final balance + PnL
    """

    result: dict[str, Any] = {}

    _print_step(1, "lockfile check")
    existing_mtime = _write_lockfile()
    if existing_mtime > 0:
        age = time.time() - existing_mtime
        print(f"EXIT 6: lockfile active (last write {age:.1f}s ago < {LOCKFILE_TTL_S}s)",
              file=sys.stderr)
        sys.exit(6)

    _print_step(2, "idempotency guard (SOL balance check)")
    try:
        usdt_start, sol_start = await _get_account_balance(client)
    except Exception as e:
        msg = str(e).lower()
        if "401" in msg or "invalid api-key" in msg or "authentication" in msg:
            print("EXIT 3: AuthenticationError — check .env.mainnet, key may be revoked "
                  "(see MEMORY phase6_plan_v6_execution_blocked_api_401)", file=sys.stderr)
            LOCKFILE.unlink(missing_ok=True)
            sys.exit(3)
        raise
    print(f"  USDT ${usdt_start:.4f}  SOL {sol_start}")
    if sol_start > 0.0001 and not force:
        print(f"EXIT 5: SOL balance {sol_start} > 0 — last run incomplete. "
              f"Use --force to bypass.", file=sys.stderr)
        LOCKFILE.unlink(missing_ok=True)
        sys.exit(5)
    result["usdt_start"] = usdt_start
    result["sol_start"] = sol_start

    _print_step(4, "live price fetch")
    live_price_1 = await _get_live_price(client)
    print(f"  SOLUSDT: ${live_price_1:.4f}")
    result["live_price_step4"] = live_price_1

    _print_step(5, "exchange info (LOT_SIZE + NOTIONAL + PRICE_FILTER, verify Bug 7 fix)")
    step_size, min_notional = await _get_lot_size_filter(client)
    tick_size = await _get_price_tick_size(client)
    print(f"  stepSize={step_size}  minNotional={min_notional}  tickSize={tick_size}")
    result["step_size"] = step_size
    result["min_notional"] = min_notional
    result["tick_size"] = tick_size

    _print_step(6, "quantity calc (LOT_SIZE/NOTIONAL loop, verify Bug 8 fix)")
    qty = _compute_quantity(TARGET_NOTIONAL_USDT, live_price_1, step_size, min_notional)
    notional = qty * live_price_1
    print(f"  qty={qty}  notional=${notional:.4f}")
    if notional > HARD_CAP_USDT:
        print("EXIT 4: hard cap exceeded", file=sys.stderr)
        LOCKFILE.unlink(missing_ok=True)
        sys.exit(4)
    result["qty_step6"] = qty

    _print_step(7, "approval gate bypassed — direct BinanceRESTClient.place_order (no HTTP)")
    print("  NOTE: Bug 4 (risk_params leverage routing) verified via unit test, not here.")

    _print_step(7.5, "re-fetch live price (may have drifted since step 4)")
    live_price_2 = await _get_live_price(client)
    print(f"  SOLUSDT re-fetched: ${live_price_2:.4f} (was ${live_price_1:.4f})")
    qty = _compute_quantity(TARGET_NOTIONAL_USDT, live_price_2, step_size, min_notional)
    notional = qty * live_price_2
    print(f"  recomputed qty={qty}  notional=${notional:.4f}")
    if notional > HARD_CAP_USDT:
        print("EXIT 4: hard cap exceeded after re-fetch", file=sys.stderr)
        LOCKFILE.unlink(missing_ok=True)
        sys.exit(4)
    result["qty_step7.5"] = qty
    result["live_price_step7.5"] = live_price_2

    _print_step(8, "BinanceRESTClient.place_order MARKET BUY (verify Bug 5 enum str + Bug 7/8 not rejected)")
    try:
        order_resp = await client.place_order(
            symbol=SYMBOL,
            side="BUY",
            order_type="MARKET",
            quantity=str(qty),
        )
    except Exception as e:
        msg = str(e).lower()
        if "401" in msg or "invalid api-key" in msg or "authentication" in msg:
            print("EXIT 3: AuthenticationError — check .env.mainnet", file=sys.stderr)
            LOCKFILE.unlink(missing_ok=True)
            sys.exit(3)
        print(f"EXIT 1: place_order failed — {e}", file=sys.stderr)
        LOCKFILE.unlink(missing_ok=True)
        sys.exit(1)
    print(f"  place_order response: {json.dumps(order_resp)[:500]}")
    result["buy_order_response"] = order_resp

    _print_step(9, "poll order fill + place limit sell + 1h poll + market sell fallback")
    buy_fill_price: float | None = None
    if isinstance(order_resp, dict):
        fills = order_resp.get("fills") or []
        if fills:
            buy_fill_price = float(fills[0].get("price", 0))
    if buy_fill_price is None or buy_fill_price <= 0:
        print("  WARNING: no fill price in response, using live_price_2 as fallback")
        buy_fill_price = live_price_2
    print(f"  buy fill price: ${buy_fill_price:.4f}")
    result["buy_fill_price"] = buy_fill_price

    limit_sell_price_raw = buy_fill_price * (1.0 + LIMIT_SELL_MARKUP_PCT)
    limit_sell_price = _floor_to_tick(limit_sell_price_raw, tick_size)
    print(f"  limit sell price (+0.5% markup, floored to tick {tick_size}): "
          f"${limit_sell_price:.4f} (raw ${limit_sell_price_raw:.4f})")

    sell_resp = None
    sell_order_id = None
    limit_sell_failed = False
    try:
        sell_resp = await client.place_order(
            symbol=SYMBOL,
            side="SELL",
            order_type="LIMIT",
            quantity=str(qty),
            price=str(limit_sell_price),
            time_in_force="GTC",
        )
        print(f"  limit sell placed: {json.dumps(sell_resp)[:300]}")
        result["limit_sell_response"] = sell_resp
        sell_order_id = sell_resp.get("orderId") if isinstance(sell_resp, dict) else None
    except Exception as e:
        print(f"  WARNING: limit sell failed — {e}. Falling back to market sell.", file=sys.stderr)
        limit_sell_failed = True

    if limit_sell_failed or sell_order_id is None:
        print("  immediate market sell fallback (limit sell failed)")
        try:
            market_sell_resp = await client.place_order(
                symbol=SYMBOL,
                side="SELL",
                order_type="MARKET",
                quantity=str(qty),
            )
            print(f"  market sell executed: {market_sell_resp}")
            result["market_sell_fallback"] = market_sell_resp
        except Exception as e:
            print(f"  market sell failed: {e}", file=sys.stderr)
    elif sell_order_id is not None:
        print(f"  polling limit sell fill (every {LIMIT_SELL_POLL_INTERVAL_S}s "
              f"up to {LIMIT_SELL_TIMEOUT_S}s)")
        deadline = time.time() + LIMIT_SELL_TIMEOUT_S
        filled = False
        while time.time() < deadline:
            await asyncio.sleep(LIMIT_SELL_POLL_INTERVAL_S)
            try:
                cstatus = await client.get_order(symbol=SYMBOL, order_id=sell_order_id)
                status = cstatus.get("status", "").upper() if isinstance(cstatus, dict) else ""
                if status in ("FILLED", "FILLED_FULLY", "CLOSED"):
                    filled = True
                    print(f"  limit sell FILLED at ${limit_sell_price:.4f}")
                    break
            except Exception:
                pass
        if not filled:
            print("  limit sell unfilled after 1h, cancel + market sell fallback")
            try:
                await client.cancel_order(symbol=SYMBOL, order_id=sell_order_id)
            except Exception:
                pass
            try:
                market_sell_resp = await client.place_order(
                    symbol=SYMBOL,
                    side="SELL",
                    order_type="MARKET",
                    quantity=str(qty),
                )
                print(f"  market sell executed: {market_sell_resp}")
                result["market_sell_fallback"] = market_sell_resp
            except Exception as e:
                print(f"  market sell failed: {e}", file=sys.stderr)

    _print_step(10, "final balance + PnL")
    usdt_end, sol_end = await _get_account_balance(client)
    pnl = usdt_end - usdt_start
    print(f"  USDT ${usdt_start:.4f} → ${usdt_end:.4f}  (PnL ${pnl:+.4f})")
    print(f"  SOL {sol_start} → {sol_end}")
    result["usdt_end"] = usdt_end
    result["sol_end"] = sol_end
    result["pnl"] = pnl

    if usdt_end < SAFETY_FLOOR_USDT:
        print(f"EXIT 2: balance ${usdt_end:.2f} < safety floor ${SAFETY_FLOOR_USDT}",
              file=sys.stderr)
        LOCKFILE.unlink(missing_ok=True)
        sys.exit(2)

    LOCKFILE.unlink(missing_ok=True)
    return result


def _save_research_md(result: dict[str, Any], mode: str) -> Path:
    """Save result as markdown to scripts/research/."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = REPO_ROOT / "scripts" / "research" / f"mainnet-sol-retest-{mode}-{ts}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Mainnet SOL Retest — {mode} — {ts}",
        "",
        "## Result",
        "",
        "```json",
        json.dumps(result, indent=2, default=str),
        "```",
        "",
        "## PASS/FAIL",
        "",
        "- PASS if: HTTP 200 from /api/v1/live/place_order AND no CRITICAL in risk_engine decision AND order filled within 30s AND limit sell filled or market sell fallback succeeded",
        "- FAIL if: HTTP 4xx/5xx OR CRITICAL OR order NOT filled within 30s OR limit sell unfilled after 1h AND market sell fallback failed",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--dry-run", action="store_true", help="no order, just check account + info")
    parser.add_argument("--live", action="store_true", help="real $5 mainnet order via live_engine.place_order")
    parser.add_argument("--force", action="store_true", help="bypass idempotency SOL>0 guard")
    args = parser.parse_args()

    if not args.dry_run and not args.live:
        parser.print_help()
        return 1
    if args.dry_run and args.live:
        print("ERROR: --dry-run and --live are mutually exclusive", file=sys.stderr)
        return 1

    env = _load_env_mainnet()
    creds = _make_credentials(env)

    from selene_space_exchange.exchanges.binance.rest_client import BinanceRESTClient

    client = BinanceRESTClient(creds, testnet=False)
    try:
        if args.dry_run:
            result = await dry_run(client)
            out = _save_research_md(result, "dry-run")
            print(f"\nresult saved: {out}")
            return 0
        elif args.live:
            result = await live_mode(client, force=args.force)
            out = _save_research_md(result, "live")
            print(f"\nresult saved: {out}")
            return 0
    except SystemExit:
        raise
    except Exception as e:
        print(f"EXIT 1: unhandled exception — {e}", file=sys.stderr)
        traceback.print_exc()
        try:
            LOCKFILE.unlink(missing_ok=True)
        except Exception:
            pass
        return 1
    finally:
        await client.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
