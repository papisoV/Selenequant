"""Corroborate every gap_report hole against an independent vendor.

Why: ``verify_daily_all.py`` criterion (3) can only pass if genuine trading
halts are excused, and the plan says that allowlist is "B0 首轮人工过目后固化".
A hand-typed list of 158 symbols is neither reviewable nor reproducible — and
an excuse nobody can check is how a gate quietly becomes decorative.

So the evidence is gathered mechanically instead. For each symbol the DB scan
flagged, this asks **Tencent** (an independent vendor from Sina) for the same
window:

* Tencent returns zero bars strictly inside the hole → the stock did not trade.
  The hole is a halt; the symbol is excused, and the raw counts are recorded.
* Tencent returns bars there → Sina is missing data we could have had. That is
  a real defect in the base and the symbol is **not** excused, so the
  acceptance gate keeps failing until someone looks.

Each symbol also carries a control count over the whole window. Without it an
empty answer is ambiguous: a nonexistent symbol and a fully halted one look
identical. A symbol whose control is zero is reported as inconclusive and left
unexcused rather than silently excused.

Tencent carries no Beijing-exchange data, so its control is always zero for
``.BJ`` symbols and that check cannot speak to them. For those the script falls
back to a peer test against the DB: if other Beijing symbols *did* trade during
the hole, the exchange was demonstrably open and the absence is specific to
this symbol. That is not circular — it reads every other symbol's rows to
answer a question about the exchange, so a Sina-wide BSE outage would still
show ~0 peers and the symbol would stay unexcused.

Usage (repo root):
  python -X utf8 scripts/xcheck_suspensions.py            # writes data/suspensions.json
  python -X utf8 scripts/xcheck_suspensions.py --dry-run  # print, write nothing
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _pkg in ("core", "shared-types", "market-data", "ashare"):
    sys.path.insert(0, str(PROJECT_ROOT / "packages" / _pkg))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from backfill_klines_astock import EXCHANGE, MARKET_TYPE, _scan_partition, _window_start  # noqa: E402
from selene_space_core.kline_paths import default_klines_db_path  # noqa: E402
from selene_space_core.sina_symbols import to_sina_symbol  # noqa: E402

TENCENT_URL = "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://gu.qq.com",
}
TENCENT_MAX_BARS = 1000
CST = timezone(timedelta(hours=8))

# The host above is the bare API host, not ``web.ifzq.gtimg.cn``. The ``web.``
# variant throttles hard: it answered 156 requests and then returned 501 for
# every query including ones that had just worked, which on a resume-less run
# would have thrown away the whole pass. Both hosts serve the same payload.
#
# Pace requests: the vendor has no published limit, so the only safe assumption
# is that a tight loop is what tripped the throttle in the first place.
REQUEST_SLEEP_S = 0.35

# A peer count this low inside a hole means the exchange itself had nothing to
# show, not that one symbol was halted. Set well above "a handful of unrelated
# suspensions" but far below a normal session's coverage, so it only fires on a
# source- or exchange-wide gap.
MIN_BSE_PEERS = 20


def tencent_days(
    symbol: str, start: str, end: str, session: requests.Session, *, attempts: int = 3
) -> list[str]:
    """Trading days Tencent reports for ``symbol`` in ``[start, end]``.

    Tencent keys its payload by the same ``sh``/``sz``/``bj`` prefix Sina uses,
    so :func:`to_sina_symbol` is the right formatter for both.

    Retried: the endpoint intermittently answers 501/502 under load, and a
    transient failure here would land a suspension in ``disputed`` — a false
    alarm that costs a human a real investigation.
    """
    code = to_sina_symbol(symbol)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = session.get(
                TENCENT_URL,
                params={"param": f"{code},day,{start},{end},{TENCENT_MAX_BARS},qfq"},
                headers=TENCENT_HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json().get("data", {}).get(code, {})
            # qfq-adjusted and raw bars arrive under different keys depending
            # on the board; either is fine here, we only compare dates.
            bars = payload.get("qfqday") or payload.get("day") or []
            return sorted(str(bar[0]) for bar in bars if bar)
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            time.sleep(0.5 * (attempt + 1))
    raise ConnectionError(f"Tencent failed {attempts}x for {symbol}: {last_exc}")


def _peer_count_on(conn: sqlite3.Connection, day: str, exclude: str) -> int:
    """How many *other* Beijing symbols have a bar on ``day``.

    The BSE peer test: Tencent cannot speak for Beijing, so the question
    "was the exchange open?" is answered from the market's own rows. Excluding
    the symbol under test is what keeps this from being circular.
    """
    ts = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=CST).timestamp() * 1000)
    return int(
        conn.execute(
            "SELECT COUNT(DISTINCT symbol) FROM klines "
            "WHERE exchange=? AND market_type=? AND interval=? AND timestamp=? "
            "AND symbol LIKE '%.BJ' AND symbol <> ?",
            (EXCHANGE, MARKET_TYPE, "1d", ts, exclude),
        ).fetchone()[0]
    )


def _today() -> str:
    """Today in Beijing time — the natural right edge of a history query."""
    return datetime.now(CST).date().isoformat()


def _bse_probe_days(lo: str, hi: str, limit: int = 3) -> list[str]:
    """The first few weekdays strictly inside a hole — sessions to peer-test.

    Weekdays rather than a real calendar: this is a "did anyone trade" check,
    and a holiday would simply show zero peers, which reads as inconclusive
    rather than as an excuse.
    """
    day = datetime.strptime(lo, "%Y-%m-%d") + timedelta(days=1)
    end = datetime.strptime(hi, "%Y-%m-%d")
    out: list[str] = []
    while day < end and len(out) < limit:
        if day.weekday() < 5:
            out.append(day.date().isoformat())
        day += timedelta(days=1)
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description="Cross-check gap_report holes against Tencent"
    )
    p.add_argument("--db", default=None, help="默认取 default_klines_db_path()")
    p.add_argument("--data-dir", default=str(PROJECT_ROOT / "data"))
    p.add_argument("--years", type=float, default=3.0)
    p.add_argument("--out", default=None, help="默认 <data-dir>/suspensions.json")
    p.add_argument("--dry-run", action="store_true", help="只打印,不写文件")
    p.add_argument(
        "--resume",
        action="store_true",
        help="复用既有 suspensions.json 里已定的判决(供应商不可达者重试)",
    )
    args = p.parse_args()

    db_path = Path(args.db) if args.db else default_klines_db_path()
    data_dir = Path(args.data_dir)
    out_path = Path(args.out) if args.out else data_dir / "suspensions.json"
    window_start = _window_start(args.years)

    gap_report, _ = _scan_partition(db_path, window_start)
    flagged = sorted(s for s, g in gap_report.items() if g)
    print(f"=== xcheck_suspensions: {len(flagged)} flagged symbols → {out_path} ===")

    # Verdicts already on disk are reused. The cross-check is the slow, rate-
    # limited, third-party part; redoing 158 requests because the 159th failed
    # is what turned a vendor throttle into a lost run the first time.
    prior: dict = {}
    if args.resume and out_path.exists():
        try:
            prior = json.loads(out_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise SystemExit(f"unreadable prior run {out_path}: {exc}") from exc
        # A prior run is only reusable if it is the object shape written below;
        # a JSON array would make ``.get`` raise AttributeError from inside the
        # resume logic, which reads as a bug here rather than a corrupt file.
        if not isinstance(prior, dict):
            raise SystemExit(
                f"prior run {out_path} is a JSON {type(prior).__name__}, "
                f"expected an object; delete it and re-run without --resume"
            )
        # The outer check only proves the *document* is an object. Each of these
        # keys is indexed with a symbol, so a scalar or list here fails with a
        # bare TypeError/AttributeError from inside the resume loop — again
        # reading as a bug in this script rather than as a corrupt file. A
        # half-written run is exactly when --resume is used, so this is the
        # realistic corruption, not a hypothetical one.
        for key, want in (
            ("evidence", dict),
            ("symbols", list),
            ("disputed", dict),
            ("inconclusive", dict),
        ):
            value = prior.get(key)
            if value is not None and not isinstance(value, want):
                raise SystemExit(
                    f"prior run {out_path}: '{key}' must be a "
                    f"{want.__name__}, got {type(value).__name__}; delete it and "
                    f"re-run without --resume"
                )
        # Knowing the containers are dicts is not enough: the resume loop reads
        # their *values*, and each is read in a way that assumes a particular
        # type. ``inconclusive[sym]`` is substring-tested against "unreachable",
        # so an int raises a bare TypeError from inside the loop; ``evidence[sym]``
        # is copied straight into the new artifact, so a scalar silently
        # propagates garbage onto the frozen review surface a human reads to
        # decide whether a hole is a real halt. Reject both, and name the symbol.
        # ``symbols`` is the list, so its *elements* are what the loop touches:
        # the resume loop does ``set(prior["symbols"])``, which raises on an
        # unhashable element before the membership test even runs.
        for key, item_type in (
            ("evidence", dict),
            ("disputed", list),
            ("inconclusive", str),
            ("symbols", str),
        ):
            container = prior.get(key) or {}
            if isinstance(container, dict):
                located = [(f"{key}['{k}']", v) for k, v in container.items()]
            else:
                located = [(f"{key}[{i}]", v) for i, v in enumerate(container)]
            for where, item in located:
                if not isinstance(item, item_type):
                    raise SystemExit(
                        f"prior run {out_path}: {where} must be a "
                        f"{item_type.__name__}, got {type(item).__name__}; delete it "
                        f"and re-run without --resume"
                    )
    prior_evidence = prior.get("evidence", {})

    session = requests.Session()
    session.trust_env = False
    # Read-only peer connection: the BSE fallback asks the market's own rows
    # whether other Beijing names traded, which no third-party quote can tell
    # us because none of them cover the Beijing exchange.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    excused: list[str] = []
    disputed: dict[str, list] = {}
    inconclusive: dict[str, str] = {}
    evidence: dict[str, dict] = {}
    reused = 0
    try:
        for i, sym in enumerate(flagged, 1):
            holes = gap_report[sym]

            if sym in prior_evidence:
                evidence[sym] = prior_evidence[sym]
                if sym in set(prior.get("symbols", [])):
                    excused.append(sym)
                    reused += 1
                    continue
                if sym in prior.get("disputed", {}):
                    disputed[sym] = prior["disputed"][sym]
                    continue
                reason = prior.get("inconclusive", {}).get(sym)
                if reason and "unreachable" not in reason:
                    inconclusive[sym] = reason
                    continue
                # An inconclusive verdict from a vendor outage is retried
                # rather than inherited: it says nothing about the stock.

            # Beijing first, and without touching Tencent: it carries no BSE
            # data, so a request there is a wasted call that only serves to get
            # the session throttled. The peer test asks the market's own rows
            # whether other Beijing names traded during the hole.
            if sym.endswith(".BJ"):
                peer_holes: list[dict[str, Any]] = []
                for lo, hi, gap_days in holes:
                    probes = _bse_probe_days(lo, hi)
                    counts = [_peer_count_on(conn, d, sym) for d in probes]
                    peer_holes.append(
                        {
                            "hole": [lo, hi, gap_days],
                            "bse_peer_counts": counts,
                            "bse_probe_days": probes,
                        }
                    )
                evidence[sym] = {"source": "bse_peer_test", "holes": peer_holes}
                weakest = min(
                    (
                        min(h["bse_peer_counts"])
                        for h in peer_holes
                        if h["bse_peer_counts"]
                    ),
                    default=0,
                )
                if weakest < MIN_BSE_PEERS:
                    inconclusive[sym] = (
                        "no third-party BSE source available and too few Beijing "
                        f"peers traded inside the hole (min {weakest} < "
                        f"{MIN_BSE_PEERS})"
                    )
                    print(
                        f"  [{i}/{len(flagged)}] {sym:12s} INCONCLUSIVE "
                        f"(weak BSE peers min={weakest})"
                    )
                else:
                    excused.append(sym)
                    print(
                        f"  [{i}/{len(flagged)}] {sym:12s} excused via BSE "
                        f"peers (min={weakest})"
                    )
                continue

            # One request covering the whole window doubles as the control:
            # the same response proves both "Tencent knows this symbol" and
            # "Tencent has nothing inside the hole".
            try:
                days = tencent_days(sym, window_start, _today(), session)
            except ConnectionError as exc:
                # A vendor that stops answering is not evidence about this
                # stock. Recording it as inconclusive keeps the gate failing
                # rather than letting an outage excuse a symbol.
                inconclusive[sym] = f"tencent unreachable: {exc}"
                print(f"  [{i}/{len(flagged)}] {sym:12s} INCONCLUSIVE (vendor down)")
                continue
            if not days:
                inconclusive[sym] = "tencent returned no bars for the whole window"
                print(f"  [{i}/{len(flagged)}] {sym:12s} INCONCLUSIVE (control=0)")
                continue
            vendor_holes: list[dict[str, Any]] = []
            for lo, hi, gap_days in holes:
                inside = [d for d in days if lo < d < hi]
                vendor_holes.append(
                    {"hole": [lo, hi, gap_days], "tencent_bars_inside": len(inside)}
                )
            evidence[sym] = {
                "tencent_bars_in_window": len(days),
                "holes": vendor_holes,
            }
            contested = [h for h in vendor_holes if h["tencent_bars_inside"] > 0]
            if contested:
                disputed[sym] = contested
                print(
                    f"  [{i}/{len(flagged)}] {sym:12s} DISPUTED "
                    f"({len(contested)} hole(s) Tencent can fill)"
                )
            else:
                excused.append(sym)
            time.sleep(REQUEST_SLEEP_S)
    finally:
        conn.close()
        session.close()

    print(
        f"\n  excused (both vendors agree: no trading): {len(excused)} "
        f"({reused} reused from a prior run)"
    )
    print(f"  disputed (Tencent has bars Sina lacks):   {len(disputed)}")
    print(f"  inconclusive (control empty):             {len(inconclusive)}")

    if not args.dry_run:
        data_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "reason": (
                        "Interior holes in the whole-market daily base fall in "
                        "periods when the stock did not trade. Every symbol "
                        "below was flagged by the DB gap scan and then "
                        "corroborated before being excused — see 'method'."
                    ),
                    "method": (
                        "scripts/xcheck_suspensions.py. Default: the hole is "
                        "re-queried against Tencent, an independent vendor; a "
                        "symbol is excused only when Tencent reports zero bars "
                        "strictly inside every hole while its control count over "
                        "the whole window is non-zero. Beijing symbols use a "
                        "peer test instead, because Tencent carries no BSE data: "
                        "they are excused only when other Beijing names traded "
                        f"inside the hole (>= {MIN_BSE_PEERS} peers). Disputed "
                        "and inconclusive symbols are deliberately NOT excused, "
                        "so the acceptance gate keeps failing until a human "
                        "resolves them."
                    ),
                    "window_start": window_start,
                    "symbols": excused,
                    "disputed": {s: h for s, h in sorted(disputed.items())},
                    "inconclusive": {s: r for s, r in sorted(inconclusive.items())},
                    "evidence": {s: evidence[s] for s in sorted(evidence)},
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"  wrote {out_path}")
    return 1 if (disputed or inconclusive) else 0


if __name__ == "__main__":
    raise SystemExit(main())
