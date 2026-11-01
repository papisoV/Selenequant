from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from tenacity import (
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from selene_space_stock.config import LLMSettings
from selene_space_types.market_context import render_market_guidance
from selene_space_types.stock import AssetAnalysis, DailyReport

_PLACEHOLDER_PATTERN = re.compile(r"\{(\w+)\}")

logger = logging.getLogger(__name__)

# Retry policy: transient network errors (URLError/TimeoutError/ConnectionError)
# and HTTP 5xx are retried; HTTP 4xx (client errors such as 401/403/404) are
# not retried because they are not recoverable by repeating the same request.
_RETRY_NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError)


def _is_retryable_llm_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500
    return isinstance(exc, _RETRY_NETWORK_ERRORS)


_ASSET_SYSTEM_FALLBACK = (
    "You are a cautious market research analyst. Write concise research "
    "notes, avoid promises, avoid financial advice, and emphasize risk "
    "controls."
)


def _asset_system_prompt(analysis: AssetAnalysis, settings: LLMSettings, language: str) -> str:
    """Asset system prompt plus the market-specific focus/guardrail block.

    The market comes from ``asset.market`` ("US"/"CN"/"HK"/"CRYPTO"/...);
    an unrecognized market renders as an empty block, so the base prompt is
    returned unchanged (fail-open).
    """
    base = _system_prompt(settings, "asset_system.md", _ASSET_SYSTEM_FALLBACK)
    guidance = render_market_guidance(analysis.asset.market, language)
    if not guidance:
        return base
    return f"{base}\n\n{guidance}"


def summarize_with_llm(analysis: AssetAnalysis, settings: LLMSettings, language: str) -> str | None:
    user_prompt = _render_prompt_template(
        settings,
        "asset.md",
        _asset_prompt(analysis, language),
        {
            "context": _asset_context(analysis, language),
        },
    )
    messages = [
        {
            "role": "system",
            "content": _asset_system_prompt(analysis, settings, language),
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]
    return _chat_completion(messages, settings)


def summarize_portfolio_with_llm(report: DailyReport, settings: LLMSettings) -> str | None:
    user_prompt = _render_prompt_template(
        settings,
        "portfolio.md",
        _portfolio_prompt(report),
        {
            "context": _portfolio_context(report),
        },
    )
    messages = [
        {
            "role": "system",
            "content": _system_prompt(settings, "portfolio_system.md", "You are a cautious portfolio research analyst. Your job is to summarize risk, position impact, and watch items. Do not give buy/sell instructions."),
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]
    return _chat_completion(messages, settings)


def _chat_completion(messages: list[dict[str, str]], settings: LLMSettings) -> str | None:
    if not settings.enabled:
        return None
    api_key = os.getenv(settings.api_key_env)
    if not api_key or not settings.model:
        return None

    payload = {
        "model": settings.model,
        "temperature": settings.temperature,
        "messages": messages,
    }
    cached = _read_cache(payload, settings)
    if cached is not None:
        return cached
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        settings.base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "selene-space-pulse/0.1",
        },
        method="POST",
    )
    retrying = Retrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.1, min=0.1, max=1.0),
        retry=retry_if_exception(_is_retryable_llm_error),
        reraise=True,
    )
    try:
        result = retrying(
            lambda: _do_urlopen(request, settings.timeout_seconds)
        )
    except Exception:
        logger.warning(
            "LLM chat completion failed after retries",
            exc_info=True,
        )
        return None

    content = _extract_message(result)
    if content:
        _write_cache(payload, settings, content)
    return content


def _do_urlopen(request: urllib.request.Request, timeout: int) -> dict[str, Any]:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _asset_context(analysis: AssetAnalysis, language: str) -> dict[str, Any]:
    return {
        "language": language,
        "asset": analysis.asset.model_dump(),
        "snapshot": analysis.snapshot.model_dump(),
        "metrics": analysis.metrics,
        "signal": analysis.signal.model_dump(),
        "position": analysis.position.model_dump() if analysis.position else None,
        "benchmark": analysis.benchmark.model_dump() if analysis.benchmark else None,
        "freshness": analysis.freshness.model_dump() if analysis.freshness else None,
        "news": [item.model_dump() for item in analysis.news[:5]],
        "warnings": analysis.warnings,
    }


def _asset_prompt(analysis: AssetAnalysis, language: str) -> str:
    context = _asset_context(analysis, language)
    return (
        "Create a daily market note from the JSON context below. "
        "Use 4 short bullets: key move, technical setup, news/risk, watch plan. "
        "Do not tell the reader to buy or sell.\n\n"
        + json.dumps(context, ensure_ascii=False)
    )


def _portfolio_context(report: DailyReport) -> dict[str, Any]:
    return {
        "language": report.language,
        "generated_at": report.generated_at.isoformat(),
        "market_brief": report.market_brief,
        "portfolio": [item.model_dump() for item in report.portfolio],
        "benchmarks": [
            {
                **item.model_dump(),
            }
            for item in report.benchmarks
        ],
        "insights": {
            "attention": [item.model_dump() for item in report.insights.attention],
            "risk_findings": [item.model_dump() for item in report.insights.risk_findings[:20]],
            "day_contributors": [item.model_dump() for item in report.insights.day_contributors],
            "unrealized_contributors": [item.model_dump() for item in report.insights.unrealized_contributors],
            "checklist": [item.model_dump() for item in report.insights.checklist],
        },
        "assets": [
            {
                "symbol": analysis.asset.symbol,
                "name": analysis.snapshot.name,
                "currency": analysis.snapshot.currency,
                "close": analysis.snapshot.last_close,
                "change_pct": analysis.snapshot.change_pct,
                "data_source": analysis.snapshot.source,
                "score": analysis.signal.score,
                "stance": analysis.signal.stance,
                "risk_level": analysis.signal.risk_level,
                "position": analysis.position.model_dump() if analysis.position else None,
                "benchmark": analysis.benchmark.model_dump() if analysis.benchmark else None,
                "freshness": analysis.freshness.model_dump() if analysis.freshness else None,
                "key_reasons": analysis.signal.reasons[:4],
                "warnings": analysis.warnings,
                "news": [item.model_dump() for item in analysis.news[:3]],
            }
            for analysis in report.analyses
        ],
    }


def _portfolio_prompt(report: DailyReport) -> str:
    context = _portfolio_context(report)
    return (
        "Create a portfolio-level daily research brief from the JSON context below. "
        "Use these sections with short bullets: Portfolio state, Largest risks, Notable opportunities, "
        "Data points to verify, Tomorrow watchlist. Avoid buy/sell instructions and avoid guaranteed outcomes.\n\n"
        + json.dumps(context, ensure_ascii=False)
    )


def _system_prompt(settings: LLMSettings, file_name: str, fallback: str) -> str:
    if settings.prompts_dir:
        path = Path(settings.prompts_dir) / file_name
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return fallback


def _render_prompt_template(
    settings: LLMSettings,
    file_name: str,
    fallback: str,
    values: dict[str, Any],
) -> str:
    if not settings.prompts_dir:
        return fallback
    path = Path(settings.prompts_dir) / file_name
    if not path.exists():
        return fallback
    template = path.read_text(encoding="utf-8")
    rendered_values = {
        key: json.dumps(value, ensure_ascii=False, indent=2) if not isinstance(value, str) else value
        for key, value in values.items()
    }
    # A plain str.format/format_map call aborts on the first ill-formed format
    # spec it hits (e.g. a literal JSON example like {"foo": 1}), discarding
    # every other valid substitution in the template. Matching only simple
    # {identifier} placeholders and leaving anything else untouched means one
    # stray brace can't take down substitutions elsewhere in the template.
    return _PLACEHOLDER_PATTERN.sub(
        lambda match: rendered_values.get(match.group(1), match.group(0)),
        template,
    )


def _cache_key(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_cache(payload: dict[str, Any], settings: LLMSettings) -> str | None:
    if not settings.cache_enabled:
        return None
    path = Path(settings.cache_dir) / f"{_cache_key(payload)}.txt"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _write_cache(payload: dict[str, Any], settings: LLMSettings, content: str) -> None:
    if not settings.cache_enabled:
        return
    path = Path(settings.cache_dir) / f"{_cache_key(payload)}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _extract_message(result: dict[str, Any]) -> str | None:
    choices = result.get("choices") or []
    if not choices:
        return None
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content:
        return None
    return str(content).strip()
