"""Navigation manifest endpoint.

Returns the page manifest for the qianduan demo HTML frontend.
Demo fetches `/api/v1/navigation/manifest` at startup (see demo HTML L4098-L4119);
if this endpoint fails, demo silently falls back to inline manifest.

This is a minimal implementation — returns the 22+5 page manifest hardcoded
in the demo. Future work: RBAC scope filtering per user.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from selene_space_types.common import APIResponse

router = APIRouter(tags=["navigation"])


# 22 base pages — mirrors PAGE_MANIFEST_BASE in demo HTML L3991-L4022
_BASE_PAGES: list[dict[str, Any]] = [
    {"id": "kf", "scene": "watch", "category": "主力", "title": "主力监控",
     "dataSource": {"rest": "/api/v1/markets/{symbol}/kline", "ws": "markets"}, "renderMode": "symbol"},
    {"id": "kfi", "scene": "watch", "category": "主力", "title": "主力指数",
     "dataSource": {"rest": "/api/v1/whale-flow", "ws": "markets"}, "renderMode": "symbol"},
    {"id": "mk", "scene": "watch", "category": "主力", "title": "行情总览",
     "dataSource": {"rest": None, "ws": "markets"}, "renderMode": "symbol"},
    {"id": "liqmap", "scene": "watch", "category": "数据", "title": "爆仓地图",
     "dataSource": {"rest": "/api/v1/liqmap", "ws": "intelligence"}, "renderMode": "symbol"},
    {"id": "liqratio", "scene": "watch", "category": "数据", "title": "多空比",
     "dataSource": {"rest": None, "ws": "intelligence"}, "renderMode": "symbol"},
    {"id": "gex", "scene": "watch", "category": "数据", "title": "Gamma 暴露",
     "dataSource": {"rest": "/api/v1/gex", "ws": "intelligence"}, "renderMode": "symbol"},
    {"id": "agg", "scene": "watch", "category": "数据", "title": "聚合盘口",
     "dataSource": {"rest": None, "ws": "markets"}, "renderMode": "symbol"},
    {"id": "tac", "scene": "watch", "category": "数据", "title": "资金费率",
     "dataSource": {"rest": None, "ws": "intelligence"}, "renderMode": "symbol"},
    {"id": "sig", "scene": "watch", "category": "信号", "title": "信号中心",
     "dataSource": {"rest": None, "ws": "intelligence"}, "renderMode": "symbol"},
    {"id": "vchart", "scene": "watch", "category": "信号", "title": "专业图表",
     "dataSource": {"rest": None, "ws": "markets"}, "renderMode": "symbol"},
    {"id": "poly", "scene": "watch", "category": "其他", "title": "预测市场",
     "dataSource": {"rest": "/api/v1/polymarket", "ws": "polymarket"}, "renderMode": "symbol"},
    {"id": "dex", "scene": "watch", "category": "其他", "title": "链上 DEX",
     "dataSource": {"rest": None, "ws": "intelligence"}, "renderMode": "symbol"},
    {"id": "beta", "scene": "watch", "category": "其他", "title": "对冲策略",
     "dataSource": {"rest": "/api/v1/beta-play", "ws": None}, "renderMode": "symbol"},
    {"id": "rsch", "scene": "research", "category": "研究", "title": "研究中心",
     "dataSource": {"rest": "/api/v1/research", "ws": None}, "renderMode": "research"},
    {"id": "strat", "scene": "research", "category": "策略", "title": "策略工厂",
     "dataSource": {"rest": "/api/v1/strategies/factory-templates", "ws": None}, "renderMode": "research"},
    {"id": "wid", "scene": "research", "category": "策略", "title": "组件库",
     "dataSource": {"rest": "/api/v1/widgets", "ws": None}, "renderMode": "research"},
    {"id": "pos", "scene": "trade", "category": "交易", "title": "我的持仓",
     "dataSource": {"rest": "/api/v1/trading/positions", "ws": "trading"}, "renderMode": "list"},
    {"id": "paper", "scene": "trade", "category": "交易", "title": "模拟交易",
     "dataSource": {"rest": "/api/v1/paper", "ws": "paper"}, "renderMode": "list"},
    {"id": "trd", "scene": "trade", "category": "交易", "title": "实盘下单",
     "dataSource": {"rest": "/api/v1/trading/orders", "ws": "trading"}, "renderMode": "list"},
    {"id": "grid", "scene": "trade", "category": "策略", "title": "网格策略",
     "dataSource": {"rest": "/api/v1/grid", "ws": "trading"}, "renderMode": "list"},
    {"id": "tof", "scene": "trade", "category": "策略", "title": "交易运维",
     "dataSource": {"rest": "/api/v1/tof", "ws": None}, "renderMode": "list"},
    {"id": "dash", "scene": "system", "category": "系统", "title": "总览",
     "dataSource": {"rest": "/api/v1/dashboard", "ws": None}, "renderMode": "chat"},
    {"id": "skill", "scene": "system", "category": "系统", "title": "技能市场",
     "dataSource": {"rest": "/api/v1/skills_api", "ws": None}, "renderMode": "chat"},
    {"id": "mcp", "scene": "system", "category": "系统", "title": "MCP 管理",
     "dataSource": {"rest": "/api/v1/mcp", "ws": None}, "renderMode": "chat"},
    {"id": "set", "scene": "system", "category": "系统", "title": "设置",
     "dataSource": {"rest": "/api/v1/settings", "ws": None}, "renderMode": "chat"},
]

# 5 extended pages — PAGE_MANIFEST_EXTENDED in demo HTML L4025-L4036
_EXTENDED_PAGES: list[dict[str, Any]] = [
    {"id": "vol_surface", "scene": "watch", "category": "数据", "title": "波动率曲面",
     "dataSource": {"rest": None, "ws": "markets"}, "renderMode": "symbol"},
    {"id": "smart_money", "scene": "watch", "category": "主力", "title": "聪明钱",
     "dataSource": {"rest": None, "ws": "intelligence"}, "renderMode": "symbol"},
    {"id": "alpha_mine", "scene": "research", "category": "研究", "title": "Alpha 挖掘",
     "dataSource": {"rest": "/api/v1/alpha", "ws": None}, "renderMode": "research"},
    {"id": "portfolio", "scene": "research", "category": "策略", "title": "组合管理",
     "dataSource": {"rest": "/api/v1/portfolio", "ws": None}, "renderMode": "research"},
    {"id": "live_log", "scene": "trade", "category": "交易", "title": "实盘日志",
     "dataSource": {"rest": "/api/v1/trading/live-log", "ws": "trading"}, "renderMode": "list"},
]


@router.get("/navigation/manifest")
async def get_navigation_manifest() -> APIResponse[dict[str, Any]]:
    """Return page manifest for the demo frontend.

    Returns all 22+5=27 pages with their dataSource.rest / dataSource.ws / renderMode.
    Demo uses this to populate sidebar and route pages.
    """
    return APIResponse(
        success=True,
        data={
            "pages": _BASE_PAGES + _EXTENDED_PAGES,
            "total": len(_BASE_PAGES) + len(_EXTENDED_PAGES),
        },
    )
