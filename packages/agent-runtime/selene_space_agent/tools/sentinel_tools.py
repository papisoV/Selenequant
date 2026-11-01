"""Grid sentinel tools — AI risk patrol, market analysis, daily reports, OOR advice.

Design intent informed by an external reference implementation (upstream untraceable).

Safety design:
  1. AI never enters the trading fast-path — order placement stays rule-based.
  2. AI never executes write operations directly.
  3. All LLM failures degrade to ToolResult(error=...), never exceptions.
"""

from __future__ import annotations

import json
from typing import Any

from selene_space_types.risk import RiskLevel

from selene_space_agent.tools.base import BaseTool, ToolResult
from selene_space_agent.tools.trend_analysis import analyze_trend

_EX_NAMES: dict[str, str] = {"de": "Decibel", "ex": "ExampleX", "rs": "Reactive"}

_GRID_SYSTEM_PROMPT = "你是网格交易风控哨兵，用中文输出结构化 JSON。"

_MAX_KLINES_FOR_TREND = 60


def _extract_json(text: str) -> dict | None:
    """Extract the first JSON object from LLM text.

    Only objects ({...}) qualify — arrays are rejected.  Brace scanning is
    string-aware so `{curly}` inside string values does not break pairing.
    """
    if not isinstance(text, str):
        return None

    # Strip fenced code blocks first.
    if "```" in text:
        parts = text.split("```")
        for part in parts[1::2]:
            stripped = part.strip()
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()
            candidate = _scan_object(stripped)
            if candidate is not None:
                try:
                    parsed = json.loads(candidate)
                    return parsed if isinstance(parsed, dict) else None
                except (json.JSONDecodeError, ValueError):
                    continue

    candidate = _scan_object(text)
    if candidate is None:
        return None
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _scan_object(text: str) -> str | None:
    """Return the first balanced {...} substring, ignoring braces in strings."""
    start = -1
    depth = 0
    in_string = False
    escaped = False

    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    return text[start : i + 1]
    return None


def _state_attr(state: Any, name: str, default: Any = None) -> Any:
    value = getattr(state, name, default)
    return default if value is None else value


def _build_snapshot(engines: dict[str, Any]) -> dict[str, dict]:
    """Compact per-exchange state dict; the three keys are always present."""
    snapshot: dict[str, dict] = {}
    for key in ("de", "ex", "rs"):
        eng = engines.get(key)
        if eng is None:
            snapshot[key] = {
                "running": False,
                "recovery": False,
                "trade_mode": "",
                "last_price": None,
                "equity": None,
                "balance": None,
                "realized_pnl": None,
                "unrealized_pnl": None,
                "return_pct": None,
                "out_of_range": False,
                "open_orders": 0,
                "exchange_open_orders": 0,
                "completed_rungs": 0,
                "volume": None,
                "health_status": "unknown",
                "health_reason": "",
                "gridConfig": {},
            }
            continue

        try:
            state = eng.get_state()
        except Exception:
            state = None
        if state is None:
            state = {}

        config = getattr(state, "config", None) or {}
        stats = getattr(state, "stats", None) or {}
        health = getattr(state, "health", None)

        trade_mode = ""
        try:
            trade_mode = str(eng._ex.trade_mode)
        except AttributeError:
            pass

        snapshot[key] = {
            "running": bool(_state_attr(state, "running", False)),
            "recovery": bool(_state_attr(state, "recovery", False)),
            "trade_mode": trade_mode,
            "last_price": _state_attr(state, "last_price"),
            "equity": _state_attr(state, "equity"),
            "balance": _state_attr(state, "balance"),
            "realized_pnl": _state_attr(state, "realized_pnl"),
            "unrealized_pnl": _state_attr(state, "unrealized_pnl"),
            "return_pct": _state_attr(state, "return_pct"),
            "out_of_range": bool(_state_attr(state, "out_of_range", False)),
            "open_orders": _state_attr(state, "open_orders", 0),
            "exchange_open_orders": _state_attr(state, "exchange_open_orders", 0),
            "completed_rungs": getattr(stats, "completed_rungs", 0),
            "volume": _state_attr(state, "volume"),
            "health_status": getattr(health, "status", "unknown"),
            "health_reason": getattr(health, "reason", ""),
            "gridConfig": {
                "display_name": getattr(config, "display_name", ""),
                "mode": getattr(config, "mode", ""),
                "lower": getattr(config, "lower", None),
                "upper": getattr(config, "upper", None),
                "grid_count": getattr(config, "grid_count", None),
                "size_base": getattr(config, "size_base", None),
                "leverage": getattr(config, "leverage", None),
                "out_of_range_action": getattr(config, "out_of_range_action", ""),
            },
        }
    return snapshot


class _SentinelToolBase(BaseTool):
    engines: dict[str, Any]
    llm: Any

    def __init__(self, engines: dict[str, Any], llm: Any) -> None:
        self._engines = engines
        self._llm = llm

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "exchange": {
                    "type": "string",
                    "enum": ["de", "ex", "rs"],
                    "description": "Target exchange key (optional)",
                },
            },
            "required": [],
        }

    def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            output="",
            error=f"{self.name} is async-only; call async_execute",
        )

    async def _chat(self, messages: list[dict], **kw: Any) -> str:
        return str(await self._llm.chat(messages, **kw))


# ---------------------------------------------------------------------------
# GridSentinelTool — overall + per-engine patrol verdicts
# ---------------------------------------------------------------------------


class GridSentinelTool(_SentinelToolBase):
    def __init__(self, engines: dict[str, Any], llm: Any) -> None:
        super().__init__(engines, llm)

    @property
    def name(self) -> str:
        return "grid_sentinel"

    @property
    def description(self) -> str:
        return "AI 风控巡逻：评估三所网格整体与各所风险等级并给出建议。"

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    async def async_execute(self, args: dict[str, Any]) -> ToolResult:
        snap = _build_snapshot(self._engines)
        prompt = (
            f"{_GRID_SYSTEM_PROMPT}\n"
            "根据以下网格引擎快照，输出 JSON："
            '{"overall":{"level":"ok|warn|critical","summary":"..."},'
            '"per":{"de":{"level","summary","advice"},"ex":{...},"rs":{...}}}\n'
            f"快照：{json.dumps(snap, ensure_ascii=False, default=str)}"
        )
        messages = [
            {"role": "system", "content": _GRID_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        try:
            raw = await self._chat(messages)
        except Exception as exc:
            return ToolResult(output="", error=f"LLM 调用失败: {exc}")

        data = _extract_json(raw)
        if data is None or "overall" not in data:
            return ToolResult(output="", error="LLM 未返回有效 JSON")

        overall = data.get("overall", {})
        level = str(overall.get("level", "ok")).upper()
        summary = overall.get("summary", "")
        lines = [f"【整体】{level}: {summary}"]

        per = data.get("per", {})
        for key in ("de", "ex", "rs"):
            entry = per.get(key)
            if not isinstance(entry, dict):
                continue
            display = _EX_NAMES.get(key, key)
            lines.append(
                f"【{display}】{str(entry.get('level', 'ok')).upper()}: "
                f"{entry.get('summary', '')} {entry.get('advice', '')}".rstrip()
            )

        return ToolResult(output="\n".join(lines))


# ---------------------------------------------------------------------------
# GridMarketAnalysisTool — trend + LLM narrative per exchange
# ---------------------------------------------------------------------------


class GridMarketAnalysisTool(_SentinelToolBase):
    def __init__(self, engines: dict[str, Any], llm: Any) -> None:
        super().__init__(engines, llm)

    @property
    def name(self) -> str:
        return "grid_market_analysis"

    @property
    def description(self) -> str:
        return "AI 市场分析：结合趋势指标与 LLM 给出中文市场报告。"

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    async def async_execute(self, args: dict[str, Any]) -> ToolResult:
        exchange = args.get("exchange")
        if exchange not in self._engines:
            return ToolResult(output="", error=f"Unknown exchange '{exchange}'")

        eng = self._engines[exchange]
        klines: list[Any] = []
        getter = getattr(eng, "get_recent_klines", None) or getattr(
            getattr(eng, "_state_engine", None), "get_recent_klines", None
        )
        if callable(getter):
            try:
                klines = list(getter(limit=_MAX_KLINES_FOR_TREND))[
                    :_MAX_KLINES_FOR_TREND
                ]
            except Exception:
                klines = []

        trend = analyze_trend(klines)
        display = _EX_NAMES.get(exchange, exchange)
        prompt = (
            f"{_GRID_SYSTEM_PROMPT}\n"
            f"交易所 {display} 的趋势数据：trend={trend.trend}, "
            f"strength={trend.strength:.2f}, volatility={trend.volatility}, "
            f"summary={trend.summary}。请输出简短中文市场分析报告。"
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            report = await self._chat(messages)
        except Exception as exc:
            return ToolResult(output="", error=f"LLM 调用失败: {exc}")

        return ToolResult(output=f"[{display}] 趋势: {trend.summary}\n\n{report}")


# ---------------------------------------------------------------------------
# GridDailyReportTool — baseline diff → Chinese daily digest
# ---------------------------------------------------------------------------


class GridDailyReportTool(_SentinelToolBase):
    def __init__(self, engines: dict[str, Any], llm: Any) -> None:
        super().__init__(engines, llm)
        self._baseline: dict | None = None

    @property
    def name(self) -> str:
        return "grid_daily_report"

    @property
    def description(self) -> str:
        return "AI 日报：对比基线快照生成三所网格中文日报。"

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def rebaseline(self) -> None:
        self._baseline = {"per": _build_snapshot(self._engines)}

    async def async_execute(self, args: dict[str, Any]) -> ToolResult:
        current = _build_snapshot(self._engines)
        if self._baseline is None:
            self.rebaseline()
        assert self._baseline is not None  # rebaseline() guarantees it

        prompt = (
            f"{_GRID_SYSTEM_PROMPT}\n"
            "对比以下基线与当前快照，输出中文日报（纯文本即可，无需 JSON）。\n"
            f"基线：{json.dumps(self._baseline['per'], ensure_ascii=False, default=str)}\n"
            f"当前：{json.dumps(current, ensure_ascii=False, default=str)}"
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            report = await self._chat(messages)
        except Exception as exc:
            return ToolResult(output="", error=f"LLM 调用失败: {exc}")

        # Plain-text replies pass through unchanged.
        return ToolResult(output=report)


# ---------------------------------------------------------------------------
# GridOutOfRangeAdviceTool — price left the grid range
# ---------------------------------------------------------------------------


class GridOutOfRangeAdviceTool(_SentinelToolBase):
    def __init__(self, engines: dict[str, Any], llm: Any) -> None:
        super().__init__(engines, llm)

    @property
    def name(self) -> str:
        return "grid_oor_advice"

    @property
    def description(self) -> str:
        return "AI 出界建议：价格离开网格区间时给出中文应对建议。"

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    async def async_execute(self, args: dict[str, Any]) -> ToolResult:
        exchange = args.get("exchange")
        if exchange not in self._engines:
            return ToolResult(output="", error=f"Unknown exchange '{exchange}'")

        snap = _build_snapshot({exchange: self._engines[exchange]})
        engine_snap = snap.get(exchange, {})
        display = _EX_NAMES.get(exchange, exchange)
        prompt = (
            f"{_GRID_SYSTEM_PROMPT}\n"
            f"{display} 价格出界，快照：{json.dumps(engine_snap, ensure_ascii=False, default=str)}。"
            "请输出中文出界应对建议（只建议，不执行）。"
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            advice = await self._chat(messages)
        except Exception as exc:
            return ToolResult(output="", error=f"LLM 调用失败: {exc}")

        return ToolResult(output=advice)
