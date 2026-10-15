"""Volume Shock Analysis — does TG signal news cause volume spikes?

Analyzes volume changes around each TG signal event:
  1. Volume ratio: event-bar volume / rolling average volume
  2. Volume spike detection: which events cause >2x normal volume
  3. Volume-direction correlation: does high volume confirm the sentiment direction?
  4. Volume as leading indicator: does volume spike BEFORE price moves?
  5. Per-event-type breakdown: which event types cause the biggest volume shocks?

Usage:
    python scripts/volume_shock_analysis.py
    python scripts/volume_shock_analysis.py --symbol BTC/USDT
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"

# All 42 TG signals
ALL_SIGNALS = [
    {"id": 1,  "ts": "2026-05-01T03:13:57Z", "msg": "China court: AI can't replace employees", "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 2,  "ts": "2026-05-01T04:29:18Z", "msg": "BTC breaks $77,000",                     "sentiment": 4, "type": "市场动态",  "strength": 2, "urgency": 2, "scope": "宏观"},
    {"id": 3,  "ts": "2026-05-01T07:23:27Z", "msg": "Musk: most crypto are scams",              "sentiment": 2, "type": "市场动态",  "strength": 2, "urgency": 2, "scope": "宏观"},
    {"id": 4,  "ts": "2026-05-01T07:29:52Z", "msg": "Crypto is coldest topic on X",             "sentiment": 2, "type": "市场动态",  "strength": 2, "urgency": 1, "scope": "宏观"},
    {"id": 5,  "ts": "2026-05-01T08:31:13Z", "msg": "500+ ETH wallets hacked $800K",            "sentiment": 1, "type": "安全事件",  "strength": 3, "urgency": 4, "scope": "微观"},
    {"id": 6,  "ts": "2026-05-01T09:29:13Z", "msg": "Bhutan gov selling ~$287M BTC",            "sentiment": 2, "type": "市场动态",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 7,  "ts": "2026-05-01T12:07:56Z", "msg": "Brazil CB bans crypto cross-border",       "sentiment": 1, "type": "监管",      "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 8,  "ts": "2026-05-01T12:34:18Z", "msg": "BTC breaks $78,000",                       "sentiment": 4, "type": "市场动态",  "strength": 2, "urgency": 2, "scope": "宏观"},
    {"id": 9,  "ts": "2026-05-01T13:30:47Z", "msg": "S&P500 NASDAQ open +0.4%",                 "sentiment": 4, "type": "宏观经济",  "strength": 1, "urgency": 2, "scope": "宏观"},
    {"id": 10, "ts": "2026-05-01T14:00:17Z", "msg": "US ISM manufacturing misses",              "sentiment": 2, "type": "宏观经济",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 11, "ts": "2026-05-01T14:34:16Z", "msg": "Apple +5% adds $205B",                     "sentiment": 4, "type": "宏观经济",  "strength": 2, "urgency": 2, "scope": "宏观"},
    {"id": 12, "ts": "2026-05-01T14:57:43Z", "msg": "Tether Q1 profit $1.04B",                  "sentiment": 4, "type": "市场动态",  "strength": 2, "urgency": 1, "scope": "微观"},
    {"id": 13, "ts": "2026-05-01T14:59:27Z", "msg": "Spirit Air deal collapses",                 "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 14, "ts": "2026-05-01T15:02:22Z", "msg": "Spirit Air going bankrupt",                 "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 15, "ts": "2026-05-01T15:36:05Z", "msg": "SOL 87 days below $100",                   "sentiment": 1, "type": "市场动态",  "strength": 3, "urgency": 2, "scope": "微观"},
    {"id": 16, "ts": "2026-05-01T15:50:54Z", "msg": "Trump: EU auto tariffs to 25%",             "sentiment": 2, "type": "宏观经济",  "strength": 4, "urgency": 4, "scope": "宏观"},
    {"id": 17, "ts": "2026-05-01T15:53:54Z", "msg": "Trump: EU auto tariffs 25% (repeat)",       "sentiment": 2, "type": "宏观经济",  "strength": 4, "urgency": 4, "scope": "宏观"},
    {"id": 18, "ts": "2026-05-01T16:25:39Z", "msg": "Trump: negotiating, uncertain deal",        "sentiment": 2, "type": "宏观经济",  "strength": 2, "urgency": 3, "scope": "宏观"},
    {"id": 19, "ts": "2026-05-01T16:29:55Z", "msg": "Trump on Iran: not happy",                  "sentiment": 2, "type": "宏观经济",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 20, "ts": "2026-05-01T16:31:09Z", "msg": "Trump on Spirit Air final proposal",        "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 21, "ts": "2026-05-01T16:35:25Z", "msg": "Trump on Iran: bomb or deal",               "sentiment": 1, "type": "宏观经济",  "strength": 4, "urgency": 5, "scope": "宏观"},
    {"id": 22, "ts": "2026-05-01T16:46:41Z", "msg": "Trump net worth tripled via crypto",        "sentiment": 4, "type": "市场动态",  "strength": 2, "urgency": 1, "scope": "宏观"},
    {"id": 23, "ts": "2026-05-01T17:09:55Z", "msg": "DeFi April exploits $635M",                 "sentiment": 1, "type": "安全事件",  "strength": 4, "urgency": 4, "scope": "微观"},
    {"id": 24, "ts": "2026-05-01T17:10:20Z", "msg": "Iran proposes peace talk - CBS",            "sentiment": 4, "type": "宏观经济",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 25, "ts": "2026-05-01T17:11:34Z", "msg": "US gov Intel stake +$40B",                  "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 26, "ts": "2026-05-01T17:11:41Z", "msg": "Tether becomes #17 Treasury holder",        "sentiment": 4, "type": "市场动态",  "strength": 2, "urgency": 1, "scope": "微观"},
    {"id": 27, "ts": "2026-05-01T17:45:36Z", "msg": "ETH Foundation sells 10K ETH",              "sentiment": 2, "type": "市场动态",  "strength": 3, "urgency": 3, "scope": "微观"},
    {"id": 28, "ts": "2026-05-01T17:57:01Z", "msg": "ETH Foundation OTC 10K ETH",                "sentiment": 2, "type": "市场动态",  "strength": 3, "urgency": 3, "scope": "微观"},
    {"id": 29, "ts": "2026-05-01T18:00:31Z", "msg": "Trump Media Truth Social spinoff",          "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 30, "ts": "2026-05-01T18:13:32Z", "msg": "S&P500 accelerates giant IPO",              "sentiment": 4, "type": "宏观经济",  "strength": 2, "urgency": 2, "scope": "宏观"},
    {"id": 31, "ts": "2026-05-01T18:32:37Z", "msg": "Jane Street paid $9.4B",                    "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 32, "ts": "2026-05-01T18:40:02Z", "msg": "Trump: Iran hostilities terminated",        "sentiment": 3, "type": "宏观经济",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 33, "ts": "2026-05-01T20:00:19Z", "msg": "S&P500 close +0.3%, NASDAQ +0.9%",          "sentiment": 4, "type": "宏观经济",  "strength": 1, "urgency": 2, "scope": "宏观"},
    {"id": 34, "ts": "2026-05-01T21:52:19Z", "msg": "GameStop preparing to bid eBay",            "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 35, "ts": "2026-05-02T00:00:14Z", "msg": "Fear & Greed Index: 39 (Fear)",             "sentiment": 2, "type": "市场动态",  "strength": 2, "urgency": 2, "scope": "宏观"},
    {"id": 36, "ts": "2026-05-02T00:16:13Z", "msg": "Trump: take over Cuba",                     "sentiment": 2, "type": "宏观经济",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 37, "ts": "2026-05-02T00:29:43Z", "msg": "Trump on Iran: no deal better",             "sentiment": 2, "type": "宏观经济",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 38, "ts": "2026-05-02T03:50:46Z", "msg": "Trump: take over Cuba (repeat)",            "sentiment": 2, "type": "宏观经济",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 39, "ts": "2026-05-02T06:27:24Z", "msg": "Spirit Air officially bankrupt",            "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 40, "ts": "2026-05-02T15:18:09Z", "msg": "Pelosi stock trading controversy",          "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 41, "ts": "2026-05-02T21:38:23Z", "msg": "BTC breaks $79,000",                       "sentiment": 4, "type": "市场动态",  "strength": 2, "urgency": 2, "scope": "宏观"},
    {"id": 42, "ts": "2026-05-02T21:40:34Z", "msg": "BTC back above $79,000",                    "sentiment": 4, "type": "市场动态",  "strength": 2, "urgency": 2, "scope": "宏观"},
]

# Fix id=10 typo
ALL_SIGNALS[9]["sentiment"] = 2


def parse_ts(ts_str: str) -> int:
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def load_klines(symbol: str, interval: str = "15m") -> pd.DataFrame:
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange='binance' AND interval=? AND market_type='futures' "
        "ORDER BY timestamp ASC",
        conn, params=(symbol, interval),
    )
    conn.close()
    if df.empty:
        return df
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def analyze_volume_shock(symbol: str, interval: str = "15m") -> None:
    """Core analysis: volume ratio around each signal event."""
    df = load_klines(symbol, interval)
    if df.empty:
        print(f"  No {interval} data for {symbol}")
        return

    timestamps = df["timestamp"].values
    volumes = df["volume"].values
    closes = df["close"].values

    # Compute rolling average volume (48 bars = 12h on 15m)
    vol_ma_window = 48
    vol_ma = pd.Series(volumes).rolling(vol_ma_window, min_periods=1).mean().values

    bar_hours = {"15m": 0.25, "1h": 1.0, "4h": 4.0}[interval]

    print(f"\n  {'#':>2}  {'Time(UTC)':>14}  {'Snt':>3}  {'Type':>6}  {'Str':>3}  "
          f"{'VolR@':>6}  {'VolR+1':>6}  {'VolR+2':>6}  {'VolR+4':>6}  "
          f"{'Chg+1':>7}  {'Chg+4':>7}  {'Spike':>5}  {'Msg':>35}")
    print(f"  {'--':>2}  {'----------':>14}  {'---':>3}  {'------':>6}  {'---':>3}  "
          f"{'-----':>6}  {'------':>6}  {'------':>6}  {'------':>6}  "
          f"{'------':>7}  {'------':>7}  {'-----':>5}  {'---':>35}")

    results = []

    for sig in ALL_SIGNALS:
        evt_ts = parse_ts(sig["ts"])
        bar_idx = np.searchsorted(timestamps, evt_ts)
        if bar_idx < vol_ma_window or bar_idx >= len(volumes) - 16:
            continue

        # Volume ratios: event bar and next bars / rolling average
        vol_ratios = []
        for offset in [0, 1, 2, 4]:
            idx = min(bar_idx + offset, len(volumes) - 1)
            ratio = volumes[idx] / vol_ma[idx] if vol_ma[idx] > 0 else 0
            vol_ratios.append(ratio)

        # Price changes
        p_at = closes[bar_idx]
        chg_1 = (closes[min(bar_idx + 1, len(closes) - 1)] - p_at) / p_at * 100
        chg_4 = (closes[min(bar_idx + 4, len(closes) - 1)] - p_at) / p_at * 100

        # Spike: volume ratio > 2.0 at event bar or next bar
        is_spike = any(r > 2.0 for r in vol_ratios[:2])

        # Pre-event volume: was volume already elevated before the signal?
        pre_vol_ratio = volumes[bar_idx - 1] / vol_ma[bar_idx - 1] if vol_ma[bar_idx - 1] > 0 else 0

        dt_str = datetime.fromtimestamp(evt_ts / 1000, tz=timezone.utc).strftime("%m/%d %H:%M")
        sent_marker = "v" if sig["sentiment"] <= 2 else ("^" if sig["sentiment"] >= 4 else "-")
        spike_marker = "YES" if is_spike else ""

        print(f"  {sig['id']:>2}  {dt_str:>14}  {sig['sentiment']}{sent_marker}  {sig['type']:>6}  {sig['strength']:>3}  "
              f"{vol_ratios[0]:>5.2f}x  {vol_ratios[1]:>5.2f}x  {vol_ratios[2]:>5.2f}x  {vol_ratios[3]:>5.2f}x  "
              f"{chg_1:>+6.2f}%  {chg_4:>+6.2f}%  {spike_marker:>5}  {sig['msg'][:35]}")

        results.append({
            "id": sig["id"],
            "sentiment": sig["sentiment"],
            "type": sig["type"],
            "strength": sig["strength"],
            "urgency": sig["urgency"],
            "vol_ratio_at": vol_ratios[0],
            "vol_ratio_next": vol_ratios[1],
            "vol_ratio_4bar": vol_ratios[3],
            "pre_vol_ratio": pre_vol_ratio,
            "is_spike": is_spike,
            "chg_1bar": chg_1,
            "chg_4bar": chg_4,
            "msg": sig["msg"],
        })

    if not results:
        print("  No events in data range")
        return

    # === Summary statistics ===
    print(f"\n  === Volume Shock Summary ({symbol} {interval}) ===")

    # By event type
    print(f"\n  {'Event Type':>10}  {'Count':>5}  {'AvgVolR@':>9}  {'AvgVolR+1':>10}  {'SpikeRate':>9}  {'AvgChg+4':>9}")
    print(f"  {'----------':>10}  {'-----':>5}  {'--------':>9}  {'---------':>10}  {'---------':>9}  {'---------':>9}")
    for evt_type in ["宏观经济", "市场动态", "安全事件", "监管", "其他"]:
        group = [r for r in results if r["type"] == evt_type]
        if not group:
            continue
        avg_at = np.mean([r["vol_ratio_at"] for r in group])
        avg_next = np.mean([r["vol_ratio_next"] for r in group])
        spike_rate = sum(1 for r in group if r["is_spike"]) / len(group) * 100
        avg_chg = np.mean([r["chg_4bar"] for r in group])
        print(f"  {evt_type:>10}  {len(group):>5}  {avg_at:>8.2f}x  {avg_next:>9.2f}x  {spike_rate:>8.0f}%  {avg_chg:>+8.2f}%")

    #")

    # By strength
    print(f"\n  {'Strength':>10}  {'Count':>5}  {'AvgVolR@':>9}  {'AvgVolR+1':>10}  {'SpikeRate':>9}  {'AvgChg+4':>9}")
    print(f"  {'----------':>10}  {'-----':>5}  {'--------':>9}  {'---------':>10}  {'---------':>9}  {'---------':>9}")
    for strength in [1, 2, 3, 4]:
        group = [r for r in results if r["strength"] == strength]
        if not group:
            continue
        avg_at = np.mean([r["vol_ratio_at"] for r in group])
        avg_next = np.mean([r["vol_ratio_next"] for r in group])
        spike_rate = sum(1 for r in group if r["is_spike"]) / len(group) * 100
        avg_chg = np.mean([r["chg_4bar"] for r in group])
        print(f"  {strength:>10}  {len(group):>5}  {avg_at:>8.2f}x  {avg_next:>9.2f}x  {spike_rate:>8.0f}%  {avg_chg:>+8.2f}%")

    # By sentiment
    print(f"\n  {'Sentiment':>10}  {'Count':>5}  {'AvgVolR@':>9}  {'AvgVolR+1':>10}  {'SpikeRate':>9}  {'AvgChg+4':>9}")
    print(f"  {'----------':>10}  {'-----':>5}  {'--------':>9}  {'---------':>10}  {'---------':>9}  {'---------':>9}")
    for sent_label, sent_filter in [("bearish(1-2)", lambda r: r["sentiment"] <= 2),
                                     ("neutral(3)", lambda r: r["sentiment"] == 3),
                                     ("bullish(4-5)", lambda r: r["sentiment"] >= 4)]:
        group = [r for r in results if sent_filter(r)]
        if not group:
            continue
        avg_at = np.mean([r["vol_ratio_at"] for r in group])
        avg_next = np.mean([r["vol_ratio_next"] for r in group])
        spike_rate = sum(1 for r in group if r["is_spike"]) / len(group) * 100
        avg_chg = np.mean([r["chg_4bar"] for r in group])
        print(f"  {sent_label:>10}  {len(group):>5}  {avg_at:>8.2f}x  {avg_next:>9.2f}x  {spike_rate:>8.0f}%  {avg_chg:>+8.2f}%")

    # === Key question: does volume spike predict direction? ===
    print("\n  === Volume Spike as Direction Predictor ===")
    spike_events = [r for r in results if r["is_spike"]]
    no_spike_events = [r for r in results if not r["is_spike"]]

    for label, group in [("WITH spike", spike_events), ("NO spike", no_spike_events)]:
        if not group:
            continue
        bearish = [r for r in group if r["sentiment"] <= 2]
        bullish = [r for r in group if r["sentiment"] >= 4]
        bear_correct = sum(1 for r in bearish if r["chg_4bar"] < 0)
        bull_correct = sum(1 for r in bullish if r["chg_4bar"] > 0)
        bear_acc = bear_correct / len(bearish) * 100 if bearish else 0
        bull_acc = bull_correct / len(bullish) * 100 if bullish else 0
        print(f"  {label:>10}: {len(group)} events, bearish acc={bear_acc:.0f}% ({bear_correct}/{len(bearish)}), "
              f"bullish acc={bull_acc:.0f}% ({bull_correct}/{len(bullish)})")

    # === Pre-event volume: is volume already elevated before signal? ===
    print("\n  === Pre-Event Volume (Signal Leading vs Lagging) ===")
    strong_events = [r for r in results if r["strength"] >= 3]
    if strong_events:
        pre_elevated = sum(1 for r in strong_events if r["pre_vol_ratio"] > 1.5)
        avg_pre = np.mean([r["pre_vol_ratio"] for r in strong_events])
        avg_post = np.mean([r["vol_ratio_at"] for r in strong_events])
        print(f"  Strong events (str>=3): avg pre-event vol ratio = {avg_pre:.2f}x, avg event-bar vol ratio = {avg_post:.2f}x")
        print(f"  Pre-elevated (>1.5x): {pre_elevated}/{len(strong_events)} = {pre_elevated/len(strong_events)*100:.0f}%")
        if avg_pre > avg_post:
            print("  >>> Volume was HIGHER before signal — signal is LAGGING (volume already moved)")
        elif avg_post > avg_pre * 1.3:
            print("  >>> Volume spikes AT signal — signal is TIMELY (news drives volume)")
        else:
            print("  >>> No clear volume pattern around signal")

    # === Volume spike + price move correlation ===
    print("\n  === Volume-Price Correlation ===")
    vol_ratios_all = [r["vol_ratio_at"] for r in results]
    price_moves = [abs(r["chg_4bar"]) for r in results]
    if len(vol_ratios_all) > 3:
        corr = np.corrcoef(vol_ratios_all, price_moves)[0, 1]
        print(f"  Correlation(vol_ratio, |price_change_4bar|) = {corr:.3f}")
        if corr > 0.3:
            print("  >>> Moderate positive: higher volume → bigger price moves")
        elif corr > 0.1:
            print("  >>> Weak positive: volume slightly predicts move size")
        else:
            print("  >>> No correlation: volume doesn't predict move size")


def analyze_volume_profile(symbol: str) -> None:
    """Detailed volume profile: minute-level volume distribution around top events."""
    df = load_klines(symbol, "15m")
    if df.empty:
        return

    timestamps = df["timestamp"].values
    volumes = df["volume"].values
    closes = df["close"].values
    vol_ma = pd.Series(volumes).rolling(48, min_periods=1).mean().values

    # Focus on strong events (strength >= 3)
    strong_signals = [s for s in ALL_SIGNALS if s["strength"] >= 3]

    print(f"\n  === Volume Profile Around Strong Events ({symbol}) ===")
    print("  (Volume ratio relative to 12h rolling average)")

    for sig in strong_signals:
        evt_ts = parse_ts(sig["ts"])
        bar_idx = np.searchsorted(timestamps, evt_ts)
        if bar_idx < 8 or bar_idx >= len(volumes) - 16:
            continue

        dt_str = datetime.fromtimestamp(evt_ts / 1000, tz=timezone.utc).strftime("%m/%d %H:%M")
        sent_marker = "v" if sig["sentiment"] <= 2 else ("^" if sig["sentiment"] >= 4 else "-")

        # Volume profile: -4h to +4h (in 15m bars)
        offsets = list(range(-16, 17))  # -16 to +16 bars = -4h to +4h
        vol_profile = []
        for off in offsets:
            idx = bar_idx + off
            ratio = volumes[idx] / vol_ma[idx] if vol_ma[idx] > 0 else 0
            vol_profile.append(ratio)

        # Find peak volume bar
        peak_idx = np.argmax(vol_profile)
        peak_offset = offsets[peak_idx]
        peak_ratio = vol_profile[peak_idx]
        peak_hours = peak_offset * 0.25

        # Price change at peak volume bar
        p_at = closes[bar_idx]
        p_peak = closes[min(bar_idx + peak_offset, len(closes) - 1)]
        chg_at_peak = (p_peak - p_at) / p_at * 100

        # Volume-weighted price direction (VWAP shift)
        total_vol = sum(volumes[bar_idx + off] for off in range(0, 8) if bar_idx + off < len(volumes))
        if total_vol > 0:
            vwap_shift = sum(
                closes[min(bar_idx + off, len(closes) - 1)] * volumes[bar_idx + off]
                for off in range(0, 8) if bar_idx + off < len(volumes)
            ) / total_vol
            vwap_chg = (vwap_shift - p_at) / p_at * 100
        else:
            vwap_chg = 0

        print(f"\n  #{sig['id']:>2} {dt_str} {sig['sentiment']}{sent_marker} str={sig['strength']} — {sig['msg'][:45]}")
        print(f"     Peak vol: {peak_ratio:.2f}x at {peak_hours:+.1f}h from signal, price at peak: {chg_at_peak:+.2f}%")
        print(f"     VWAP shift (next 2h): {vwap_chg:+.2f}%")

        # Visual bar chart of volume ratio
        bar_chars = []
        for off in range(-4, 9):  # -1h to +2h
            idx = bar_idx + off
            if 0 <= idx < len(volumes) and vol_ma[idx] > 0:
                ratio = volumes[idx] / vol_ma[idx]
                bar_len = min(int(ratio * 5), 20)
                marker = "#" if ratio > 2.0 else ("=" if ratio > 1.0 else ".")
                bar_chars.append(f"{off*0.25:+5.1f}h |{'#' * bar_len}{'.' * (20 - bar_len)}| {ratio:.1f}x")
        for line in bar_chars:
            print(f"     {line}")


def main():
    parser = argparse.ArgumentParser(description="Volume Shock Analysis")
    parser.add_argument("--symbol", type=str, default="all",
                        help="Symbol (BTC/USDT, ETH/USDT, SOL/USDT, or all)")
    args = parser.parse_args()

    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"] if args.symbol == "all" else [args.symbol]

    for symbol in symbols:
        print(f"\n{'='*110}")
        print(f"  {symbol} — Volume Shock Analysis")
        print(f"{'='*110}")

        print("\n  ── Part 1: Volume Ratio Around Signal Events (15m) ──")
        analyze_volume_shock(symbol, "15m")

        print("\n  ── Part 2: Volume Profile Around Strong Events ──")
        analyze_volume_profile(symbol)

    print(f"\n{'='*110}")
    print("  VOLUME SHOCK ANALYSIS COMPLETE")
    print(f"{'='*110}\n")


if __name__ == "__main__":
    main()
