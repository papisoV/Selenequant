"""溢价监控 loop — LOF/ETF 折溢价超阈值告警。

L3-B 吸收自 stock 项目 LOF 溢价监控策略形态(TARGET_PREMIUM=4);
循环模式复刻 news-ingest detector_loop:默认不启动(SELENE_FI_ALERT_ENABLED
显式开启),单轮失败只记日志不 crash loop。
告警走 selene_space_pulse.notify.send_trade_alert(fee_monitor.py:116 先例,
fire-and-forget,无 pulse 依赖时静默)。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from selene_space_fund_ingest.config import LOF_PREMIUM_THRESHOLD

logger = logging.getLogger(__name__)

#: 轮询间隔秒(告警 loop 空转成本低;阈值判断纯本地读库)
ALERT_INTERVAL_SECONDS = 600


def find_premium_breaches(
    rows: list[dict[str, Any]],
    threshold: float = LOF_PREMIUM_THRESHOLD,
) -> list[dict[str, Any]]:
    """筛选 |premium_pct| > threshold 的行(nav 未配对行排除)。"""
    return [
        r
        for r in rows
        if r.get("premium_pct") is not None and abs(r["premium_pct"]) > threshold
    ]


def format_alert(breaches: list[dict[str, Any]]) -> str:
    """告警文案:每行 `代码 名称 溢价%(nav 日期)`,限前 10 条。"""
    lines = ["[fund-ingest] LOF/ETF 折溢价超阈值:"]
    for r in breaches[:10]:
        nav_date = r.get("nav_date") or "N/A"
        lines.append(
            f"  {r.get('code')} {r.get('name')} "
            f"溢价 {r['premium_pct']:+.2f}% (nav {nav_date})"
        )
    if len(breaches) > 10:
        lines.append(f"  ...另有 {len(breaches) - 10} 只超阈值")
    return "\n".join(lines)


def _send_alert(text: str) -> None:
    """pulse notify 隔离层:未装/失败只 log,不抛(loop 不因告警挂)。"""
    try:
        from selene_space_pulse.notify import send_trade_alert

        send_trade_alert(text)
    except Exception:
        logger.warning("premium alert dispatch failed", exc_info=True)


async def alert_loop(interval: int = ALERT_INTERVAL_SECONDS) -> None:
    """周期扫描 lof_etf_spot 超阈值行并发告警;异常吞掉保循环。"""
    from selene_space_fund_ingest import store

    while True:
        try:
            conn = store.connect()
            try:
                rows = store.fetch_all(conn, "lof_etf_spot")
            finally:
                conn.close()
            breaches = find_premium_breaches(rows)
            if breaches:
                logger.info(
                    "premium breaches: %d (threshold %.1f%%)",
                    len(breaches),
                    LOF_PREMIUM_THRESHOLD,
                )
                _send_alert(format_alert(breaches))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("premium alert round failed", exc_info=True)
        await asyncio.sleep(interval)
