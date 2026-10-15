"""Helper script for tg-analyze skill — fetch messages and write LLM analysis results.

Migrated from internal-archive/tg_analyze_helper.py. Uses selene_space_tg_ingest package.
"""

import json
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))

USAGE = """
Usage:
  python tg_analyze_helper.py preanalyze <db_path> [--count N] [--chat-id ID] [--from DATE] [--to DATE] [--overwrite] [--all]
  python tg_analyze_helper.py fetch <db_path> [--count N] [--chat-id ID] [--from DATE] [--to DATE] [--overwrite] [--all] [--noise-stats]
  python tg_analyze_helper.py write <db_path> <results_json_file> [--mode factor|timeline|theme|deep]
  python tg_analyze_helper.py stats <db_path>
  python tg_analyze_helper.py init <db_path>
"""

NOISE_PATTERNS: dict[str, list[str]] = {
    "price_quote": [
        "突破$", "跌破$", "站上$", "逼近$", "触及$",
        "创历史新高", "创年内新低", "创历史新低",
        "涨幅%", "跌幅%", "涨%", "跌%",
        "24h涨", "24h跌", "24H涨", "24H跌",
        "突破.*美元", "跌破.*美元", "站上.*美元",
        "价格突破", "价格跌破",
        "比特币价格", "BTC价格",
        "价值.*美元的比特币", "价值.*美元的以太",
    ],
    "etf_flow": [
        "ETF流入", "ETF流出", "资金净流入", "资金净流出",
        "ETF净流入", "ETF净流出", "BTC ETF", "ETH ETF",
        "现货ETF", "ETF资金",
    ],
    "exchange_listing": [
        "币安上线", "上线永续", "Launchpool", "新币挖矿",
        "上线合约", "开放交易", "上架交易对",
    ],
    "airdrop": [
        "空投领取", "领取空投", "TGE", "代币生成",
    ],
    "routine_data": [
        "恐惧贪婪指数", "多空比", "资金费率",
        "持仓量变化", "清算数据", "爆仓",
        "未平仓合约", "OI变化",
        "空头头寸被清算", "多头头寸被清算",
        "标普.*指数.*上涨", "标普.*指数.*下跌",
        "纳斯达克指数.*上涨", "纳斯达克指数.*下跌",
        "指数收盘", "指数开盘",
        "原油库存", "制造业指数",
    ],
    "stock_news": [
        "股价.*跌破", "股价.*突破",
        "IPO发行价", "可转换优先",
        "CONV SR", "票据到期",
    ],
}

MACRO_SIGNAL_KEYWORDS: list[str] = [
    "美联储", "Fed", "降息", "加息", "利率", "CPI", "GDP", "非农", "NFP",
    "通胀", "通缩", "量化宽松", "QE", "QT", "缩表",
    "SEC", "监管", "合规", "禁令", "罚款", "起诉", "诉讼", "审查",
    "制裁", "战争", "冲突", "地缘", "贸易战", "关税",
    "特朗普", "Trump", "拜登", "Biden", "白宫", "国会",
    "黑客", "被盗", "漏洞", "攻击", "跑路", "Rug", "闪电贷",
    "法案", "立法", "政策", "合规要求",
    "银行", "破产", "救助", "系统性风险",
    "欧央行", "BOJ", "日本央行", "英国央行",
]

DEFAULT_HALFLIFE = {
    "security": 180,
    "regulatory": 1440,
    "macro": 720,
    "whale": 120,
    "market": 60,
    "listing": 240,
    "partnership": 360,
    "other": 60,
}


def _dedup_tradfin_translation(text: str) -> str:
    if not text or "Tradfin" not in text:
        return text

    text = re.sub(r'—{5,}', '', text)

    parts = re.split(r'\*\*\s*\n(?=Tradfin[：:])', text, maxsplit=1)
    if len(parts) == 2:
        result = parts[0].strip()
        result = re.sub(r'^\*\*', '', result).strip()
        result = re.sub(r'\*\*$', '', result).strip()
        return result

    lines = text.split('\n')
    seen_tradfin = False
    kept_lines = []
    for line in lines:
        if re.match(r'^Tradfin[：:]', line.strip()):
            if seen_tradfin:
                break
            seen_tradfin = True
        kept_lines.append(line)
    return '\n'.join(kept_lines) if len(kept_lines) < len(lines) else text


TOKEN_PATTERN = re.compile(
    r'\$([A-Z]{2,10})'
    r'|(?<!\w)(BTC|ETH|SOL|BNB|XRP|ADA|DOGE|AVAX|DOT|MATIC|LINK|UNI|AAVE|ARB|OP|APT|SUI|SEI|NEAR|FTM|ATOM|FIL|INJ|TIA|JUP|WIF|PEPE|SHIB|TRUMP|DJT)(?!\w)'
)

CATEGORY_FACTOR_MAP: dict[str, dict] = {
    "event_regulatory": {
        "event_type": "regulatory",
        "direction": -0.5, "magnitude": 0.7, "urgency": 0.7,
        "confidence": 0.7, "halflife_min": 1440,
    },
    "event_macro": {
        "event_type": "macro",
        "direction": 0.0, "magnitude": 0.7, "urgency": 0.5,
        "confidence": 0.7, "halflife_min": 720,
    },
    "event_exploit": {
        "event_type": "security",
        "direction": -0.8, "magnitude": 0.8, "urgency": 0.9,
        "confidence": 0.8, "halflife_min": 180,
    },
    "event_whale": {
        "event_type": "whale",
        "direction": -0.3, "magnitude": 0.6, "urgency": 0.6,
        "confidence": 0.6, "halflife_min": 120,
    },
    "event_listing": {
        "event_type": "listing",
        "direction": 0.5, "magnitude": 0.5, "urgency": 0.4,
        "confidence": 0.7, "halflife_min": 240,
    },
    "event_partnership": {
        "event_type": "partnership",
        "direction": 0.4, "magnitude": 0.4, "urgency": 0.3,
        "confidence": 0.6, "halflife_min": 360,
    },
    "bullish": {
        "event_type": "market",
        "direction": 0.5, "magnitude": 0.4, "urgency": 0.3,
        "confidence": 0.5, "halflife_min": 60,
    },
    "bearish": {
        "event_type": "market",
        "direction": -0.5, "magnitude": 0.4, "urgency": 0.3,
        "confidence": 0.5, "halflife_min": 60,
    },
    "urgency_high": {"urgency": 0.9, "magnitude": 0.7},
}


def _extract_tokens(text: str) -> list[str]:
    tokens: set[str] = set()
    for match in TOKEN_PATTERN.finditer(text):
        token = match.group(1) or match.group(2)
        if token:
            tokens.add(token.upper())
    return sorted(tokens)


def _preanalyze_message(text: str, msg_id: int, chat_id: int) -> dict:
    from selene_space_tg_ingest.signal_filter import KeywordFilter, DEFAULT_KEYWORD_RULES

    clean_text = _dedup_tradfin_translation(text)

    is_noise, noise_reason = _is_likely_noise(clean_text)

    kf = KeywordFilter({"keywords": DEFAULT_KEYWORD_RULES})
    filter_result = kf.filter(clean_text)

    categories = filter_result.preliminary_factors.get("matched_categories", [])
    has_strong_signal = any(c in ("event_regulatory", "event_macro", "event_exploit") for c in categories)

    is_definite_noise = (not filter_result.passed) or (is_noise and not has_strong_signal)

    if is_definite_noise:
        return {
            "message_id": msg_id,
            "chat_id": chat_id,
            "is_signal": False,
            "direction": 0.0,
            "magnitude": 0.1,
            "urgency": 0.1,
            "confidence": 0.9,
            "halflife_min": 30,
            "symbols": "[]",
            "event_type": "market",
            "reasoning": f"noise: {noise_reason}" if is_noise else "noise: no signal keywords matched",
            "matched_categories": [],
            "matched_keywords": filter_result.matched_keywords,
            "llm_model": "rule-engine",
        }

    factors: dict = {
        "message_id": msg_id,
        "chat_id": chat_id,
        "is_signal": True,
        "direction": 0.0,
        "magnitude": 0.5,
        "urgency": 0.5,
        "confidence": 0.3,
        "halflife_min": 60,
        "symbols": json.dumps(_extract_tokens(clean_text), ensure_ascii=False),
        "event_type": "other",
        "reasoning": "",
        "matched_categories": categories,
        "matched_keywords": filter_result.matched_keywords,
    }

    if is_noise:
        factors["confidence"] = 0.2
        factors["reasoning"] = f"borderline: noise pattern ({noise_reason}) but signal keywords present"

    for cat in categories:
        if cat in CATEGORY_FACTOR_MAP:
            for key, val in CATEGORY_FACTOR_MAP[cat].items():
                factors[key] = val

    event_cats = [c for c in categories if c.startswith("event_")]
    if event_cats:
        primary = event_cats[0]
        if primary in CATEGORY_FACTOR_MAP:
            factors["event_type"] = CATEGORY_FACTOR_MAP[primary]["event_type"]

    if "urgency_high" in categories:
        factors["urgency"] = 0.9
        factors["magnitude"] = max(factors.get("magnitude", 0.5), 0.7)

    if factors.get("halflife_min", 60) == 60:
        factors["halflife_min"] = DEFAULT_HALFLIFE.get(factors.get("event_type", "other"), 60)

    if not factors["reasoning"]:
        cat_labels = {
            "event_regulatory": "监管事件",
            "event_macro": "宏观经济",
            "event_exploit": "安全事件",
            "event_whale": "鲸鱼/机构",
            "event_listing": "上线事件",
            "event_partnership": "合作事件",
            "bullish": "利好信号",
            "bearish": "利空信号",
        }
        labels = [cat_labels.get(c, c) for c in categories if c in cat_labels]
        if labels:
            factors["reasoning"] = f"预分类: {'+'.join(labels)}; 关键词: {','.join(filter_result.matched_keywords[:5])}"

    return factors


def _is_likely_noise(text: str) -> tuple[bool, str]:
    if not text:
        return False, ""

    text_lower = text.lower()
    for kw in MACRO_SIGNAL_KEYWORDS:
        if kw.lower() in text_lower:
            return False, ""

    for category, patterns in NOISE_PATTERNS.items():
        for pattern in patterns:
            if "$" in pattern:
                regex_pattern = pattern.replace("$", r"\d")
                if re.search(regex_pattern, text):
                    return True, f"noise:{category}"
            elif ".*" in pattern or "%" in pattern:
                try:
                    if re.search(pattern, text):
                        return True, f"noise:{category}"
                except re.error:
                    if pattern.lower() in text_lower:
                        return True, f"noise:{category}"
            else:
                if pattern.lower() in text_lower:
                    return True, f"noise:{category}"

    return False, ""


def _get_storage(db_path: str):
    """Import and return Storage instance."""
    from selene_space_tg_ingest.storage import Storage
    return Storage(db_path)


def cmd_preanalyze(db_path: str, args: list[str]):
    from sqlalchemy import text
    storage = _get_storage(db_path)

    count = 100
    chat_id = None
    date_from = None
    date_to = None
    overwrite = False
    include_all = False

    i = 0
    while i < len(args):
        if args[i] == "--count" and i + 1 < len(args):
            count = int(args[i + 1]); i += 2
        elif args[i] == "--chat-id" and i + 1 < len(args):
            chat_id = int(args[i + 1]); i += 2
        elif args[i] == "--from" and i + 1 < len(args):
            date_from = args[i + 1]; i += 2
        elif args[i] == "--to" and i + 1 < len(args):
            date_to = args[i + 1]; i += 2
        elif args[i] == "--overwrite":
            overwrite = True; i += 1
        elif args[i] == "--all":
            include_all = True; i += 1
        else:
            i += 1

    from datetime import datetime
    from selene_space_tg_ingest.tz_utils import local_to_utc, utc_to_local
    df = local_to_utc(datetime.fromisoformat(date_from)) if date_from else None
    dt = None
    if date_to:
        dt_local = datetime.fromisoformat(date_to)
        if dt_local.hour == 0 and dt_local.minute == 0 and dt_local.second == 0:
            dt_local = dt_local.replace(hour=23, minute=59, second=59, microsecond=999999)
        dt = local_to_utc(dt_local)

    fetch_count = count * 2
    result = storage.query_messages(
        chat_id=chat_id, date_from=df, date_to=dt,
        page=1, page_size=fetch_count,
    )
    messages = result["messages"]

    if not overwrite and messages:
        with storage.engine.connect() as conn:
            pairs = [(m["message_id"], m["chat_id"]) for m in messages]
            chunk_size = 900
            existing_ids: set[tuple[int, int]] = set()
            for c_start in range(0, len(pairs), chunk_size):
                chunk = pairs[c_start:c_start + chunk_size]
                placeholders = ", ".join(f"(:mid{j}, :cid{j})" for j in range(len(chunk)))
                params = {}
                for j, (mid, cid) in enumerate(chunk):
                    params[f"mid{j}"] = mid
                    params[f"cid{j}"] = cid
                rows = conn.execute(text(
                    f"SELECT message_id, chat_id FROM signal_factors "
                    f"WHERE (message_id, chat_id) IN ({placeholders}) "
                    f"AND llm_status='completed'"
                ), params).fetchall()
                for row in rows:
                    existing_ids.add((row[0], row[1]))
        messages = [m for m in messages if (m["message_id"], m["chat_id"]) not in existing_ids]

    messages = messages[:count]

    noise_results: list[dict] = []
    signal_candidates: list[dict] = []

    for m in messages:
        text = m.get("text") or ""
        pre = _preanalyze_message(text, m["message_id"], m["chat_id"])

        date_val = m.get("date")
        if date_val:
            try:
                pre["date"] = utc_to_local(datetime.fromisoformat(date_val)).isoformat()[:16]
            except (ValueError, TypeError):
                pre["date"] = date_val[:16]
        else:
            pre["date"] = ""
        pre["chat_title"] = m.get("chat_title") or ""
        pre["text_compressed"] = _dedup_tradfin_translation(text[:300])
        pre["text_original_length"] = len(text)

        if not pre["is_signal"] and not include_all:
            noise_results.append(pre)
        else:
            signal_candidates.append(pre)

    signal_lines = []
    for idx, s in enumerate(signal_candidates):
        symbols = json.loads(s['symbols']) if isinstance(s['symbols'], str) else s['symbols']
        line = (
            f"[{idx}] msg={s['message_id']} chat={s['chat_id']} | "
            f"{s['date']} | {s['chat_title']}\n"
            f"  Text: {s['text_compressed']}\n"
            f"  Pre: dir={s['direction']} mag={s['magnitude']} urg={s['urgency']} "
            f"conf={s['confidence']} half={s['halflife_min']}min "
            f"event={s['event_type']} symbols={symbols}\n"
            f"  Reason: {s['reasoning']}"
        )
        signal_lines.append(line)

    output = {
        "signal_candidates": signal_candidates,
        "noise_count": len(noise_results),
        "noise_results": noise_results,
        "signal_display": "\n".join(signal_lines),
        "total_fetched": len(messages),
        "total_available": result["total"],
    }

    print(json.dumps(output, ensure_ascii=False, default=str))


def cmd_init(db_path: str):
    storage = _get_storage(db_path)
    storage.init_db()
    print("OK: signal_factors table ready (new schema)")


def cmd_fetch(db_path: str, args: list[str]):
    from sqlalchemy import text
    storage = _get_storage(db_path)

    count = 50
    chat_id = None
    date_from = None
    date_to = None
    overwrite = False
    include_all = False
    noise_stats = False

    i = 0
    while i < len(args):
        if args[i] == "--count" and i + 1 < len(args):
            count = int(args[i + 1]); i += 2
        elif args[i] == "--chat-id" and i + 1 < len(args):
            chat_id = int(args[i + 1]); i += 2
        elif args[i] == "--from" and i + 1 < len(args):
            date_from = args[i + 1]; i += 2
        elif args[i] == "--to" and i + 1 < len(args):
            date_to = args[i + 1]; i += 2
        elif args[i] == "--overwrite":
            overwrite = True; i += 1
        elif args[i] == "--all":
            include_all = True; i += 1
        elif args[i] == "--noise-stats":
            noise_stats = True; i += 1
        else:
            i += 1

    fetch_count = count * 3 if not include_all else count
    from datetime import datetime
    from selene_space_tg_ingest.tz_utils import local_to_utc, utc_to_local
    df = local_to_utc(datetime.fromisoformat(date_from)) if date_from else None
    dt = None
    if date_to:
        dt_local = datetime.fromisoformat(date_to)
        if dt_local.hour == 0 and dt_local.minute == 0 and dt_local.second == 0:
            dt_local = dt_local.replace(hour=23, minute=59, second=59, microsecond=999999)
        dt = local_to_utc(dt_local)

    result = storage.query_messages(
        chat_id=chat_id, date_from=df, date_to=dt,
        page=1, page_size=fetch_count,
    )
    messages = result["messages"]

    if not overwrite and messages:
        with storage.engine.connect() as conn:
            pairs = [(m["message_id"], m["chat_id"]) for m in messages]
            chunk_size = 900
            existing_ids: set[tuple[int, int]] = set()
            for c_start in range(0, len(pairs), chunk_size):
                chunk = pairs[c_start:c_start + chunk_size]
                placeholders = ", ".join(f"(:mid{j}, :cid{j})" for j in range(len(chunk)))
                params = {}
                for j, (mid, cid) in enumerate(chunk):
                    params[f"mid{j}"] = mid
                    params[f"cid{j}"] = cid
                rows = conn.execute(text(
                    f"SELECT message_id, chat_id FROM signal_factors "
                    f"WHERE (message_id, chat_id) IN ({placeholders}) "
                    f"AND llm_status='completed'"
                ), params).fetchall()
                for row in rows:
                    existing_ids.add((row[0], row[1]))
        messages = [m for m in messages if (m["message_id"], m["chat_id"]) not in existing_ids]

    noise_counts: dict[str, int] = {}
    total_before_filter = len(messages)
    if not include_all:
        filtered = []
        for m in messages:
            text = m.get("text") or ""
            is_noise, reason = _is_likely_noise(text)
            if is_noise:
                noise_counts[reason] = noise_counts.get(reason, 0) + 1
            else:
                filtered.append(m)
        messages = filtered

    messages = messages[:count]

    formatted_lines = []
    for i, m in enumerate(messages):
        text = m.get("text") or ""
        if len(text) > 500:
            text = text[:500] + "..."
        date_val = m.get("date")
        if date_val:
            try:
                date_str = utc_to_local(datetime.fromisoformat(date_val)).isoformat()[:16]
            except (ValueError, TypeError):
                date_str = date_val[:16]
        else:
            date_str = ""
        chat_title = m.get("chat_title") or ""
        formatted_lines.append(
            f"[{i}] msg_id={m['message_id']} chat_id={m['chat_id']} | "
            f"[{chat_title}] | {date_str} | {text}"
        )

    output = {
        "messages": messages,
        "formatted": "\n".join(formatted_lines),
        "total_available": result["total"],
        "fetched": len(messages),
        "noise_filter": {
            "enabled": not include_all,
            "total_before_filter": total_before_filter,
            "filtered_out": total_before_filter - len(messages),
            "noise_breakdown": noise_counts,
        },
    }

    if noise_stats:
        print("Noise Filter Statistics:")
        print(f"  Messages before filter: {total_before_filter}")
        print(f"  Filtered as noise:      {total_before_filter - len(messages)}")
        print(f"  Kept for analysis:      {len(messages)}")
        print(f"  Filter ratio:           {(total_before_filter - len(messages)) * 100 // max(total_before_filter, 1)}%")
        if noise_counts:
            print("  Breakdown:")
            for reason, cnt in sorted(noise_counts.items(), key=lambda x: -x[1]):
                print(f"    {reason}: {cnt}")
        print()

    print(json.dumps(output, ensure_ascii=False, default=str))


def cmd_write(db_path: str, results_file: str, args: list[str]):
    from sqlalchemy import text
    storage = _get_storage(db_path)

    mode = "factor"
    i = 0
    while i < len(args):
        if args[i] == "--mode" and i + 1 < len(args):
            mode = args[i + 1]; i += 2
        else:
            i += 1

    with open(results_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        items = data.get("per_message", [data])
    else:
        items = data

    written = 0
    with storage.engine.connect() as conn:
        for item in items:
            symbols = item.get("symbols", [])
            if isinstance(symbols, list):
                symbols = json.dumps(symbols, ensure_ascii=False)

            is_signal = item.get("is_signal", True)
            row = {
                "message_id": item["message_id"],
                "chat_id": item["chat_id"],
                "is_signal": is_signal,
                "direction": item.get("direction"),
                "magnitude": item.get("magnitude"),
                "urgency": item.get("urgency"),
                "confidence": item.get("confidence"),
                "halflife_min": item.get("halflife_min"),
                "symbols": symbols,
                "event_type": item.get("event_type"),
                "reasoning": item.get("reasoning"),
                "llm_status": "completed",
                "llm_model": item.get("llm_model", "claude"),
                "llm_raw": json.dumps(item, ensure_ascii=False),
                "factor_version": 2,
            }
            existing = conn.execute(text(
                "SELECT id FROM signal_factors WHERE message_id=:mid AND chat_id=:cid"
            ), {"mid": row["message_id"], "cid": row["chat_id"]}).fetchone()
            if existing:
                set_parts = [f"{k}=:{k}" for k in row.keys() if k not in ("message_id", "chat_id")]
                set_clause = ", ".join(set_parts)
                conn.execute(text(
                    f"UPDATE signal_factors SET {set_clause}, updated_at=datetime('now') "
                    f"WHERE message_id=:message_id AND chat_id=:chat_id"
                ), row)
            else:
                cols = ", ".join(row.keys())
                vals = ", ".join(f":{k}" for k in row.keys())
                conn.execute(text(f"INSERT INTO signal_factors ({cols}) VALUES ({vals})"), row)
            written += 1
        conn.commit()

    print(f"OK: wrote {written} results to signal_factors (mode={mode})")


def cmd_stats(db_path: str):
    from sqlalchemy import text
    storage = _get_storage(db_path)
    with storage.engine.connect() as conn:
        total = conn.execute(text(
            "SELECT COUNT(*) FROM messages WHERE is_deleted=0 AND text IS NOT NULL"
        )).scalar()
        ds_done = conn.execute(text(
            "SELECT COUNT(*) FROM signal_factors WHERE llm_status='completed'"
        )).scalar()
        bullish = conn.execute(text(
            "SELECT COUNT(*) FROM signal_factors WHERE direction > 0 AND llm_status='completed'"
        )).scalar()
        bearish = conn.execute(text(
            "SELECT COUNT(*) FROM signal_factors WHERE direction < 0 AND llm_status='completed'"
        )).scalar()
        neutral = conn.execute(text(
            "SELECT COUNT(*) FROM signal_factors WHERE direction = 0 AND llm_status='completed'"
        )).scalar()
        event_dist = {}
        for row in conn.execute(text(
            "SELECT event_type, COUNT(*) as cnt FROM signal_factors "
            "WHERE event_type IS NOT NULL AND llm_status='completed' GROUP BY event_type"
        )):
            event_dist[row[0]] = row[1]
        avg_direction = conn.execute(text(
            "SELECT AVG(direction) FROM signal_factors WHERE direction IS NOT NULL AND llm_status='completed'"
        )).scalar()
        avg_magnitude = conn.execute(text(
            "SELECT AVG(magnitude) FROM signal_factors WHERE magnitude IS NOT NULL AND llm_status='completed'"
        )).scalar()
        avg_confidence = conn.execute(text(
            "SELECT AVG(confidence) FROM signal_factors WHERE confidence IS NOT NULL AND llm_status='completed'"
        )).scalar()
        avg_halflife = conn.execute(text(
            "SELECT AVG(halflife_min) FROM signal_factors WHERE halflife_min IS NOT NULL AND llm_status='completed'"
        )).scalar()
        token_counts: dict[str, int] = {}
        for row in conn.execute(text(
            "SELECT symbols FROM signal_factors "
            "WHERE symbols IS NOT NULL AND llm_status='completed'"
        )):
            try:
                tokens = json.loads(row[0])
                for t in tokens:
                    token_counts[t] = token_counts.get(t, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass
        top_tokens = sorted(token_counts.items(), key=lambda x: -x[1])[:10]

    pct = ds_done * 100 // max(total, 1)
    print("TGWatcher Analysis Coverage (v2 schema):")
    print(f"  Total messages:       {total}")
    print(f"  Analyzed:             {ds_done} ({pct}%)")
    print(f"  Direction:            bullish={bullish}, neutral={neutral}, bearish={bearish}")
    print(f"  Event types:          {event_dist}")
    print(f"  Avg direction:        {avg_direction:.3f}" if avg_direction else "  Avg direction:        -")
    print(f"  Avg magnitude:        {avg_magnitude:.3f}" if avg_magnitude else "  Avg magnitude:        -")
    print(f"  Avg confidence:       {avg_confidence:.3f}" if avg_confidence else "  Avg confidence:       -")
    print(f"  Avg halflife (min):   {avg_halflife:.1f}" if avg_halflife else "  Avg halflife (min):   -")
    print(f"  Top symbols:          {top_tokens}")


def main():
    from selene_space_tg_ingest.tz_utils import set_tz_offset
    set_tz_offset(8)

    if len(sys.argv) < 3:
        print(USAGE)
        sys.exit(1)

    command = sys.argv[1]
    db_path = sys.argv[2]
    rest_args = sys.argv[3:]

    if command == "init":
        cmd_init(db_path)
    elif command == "preanalyze":
        cmd_preanalyze(db_path, rest_args)
    elif command == "fetch":
        cmd_fetch(db_path, rest_args)
    elif command == "write":
        if not rest_args:
            print("Error: write command requires a results JSON file path")
            sys.exit(1)
        results_file = rest_args[0]
        cmd_write(db_path, results_file, rest_args[1:])
    elif command == "stats":
        cmd_stats(db_path)
    else:
        print(f"Unknown command: {command}")
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
