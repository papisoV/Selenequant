"""Pro feature helpers — shared utilities for Pro API endpoints.

Provides:
- call_llm_structured: LLM call with JSON schema enforcement and response parsing
- require_pro_feature: Feature gate stub (no-op in community edition)
- parse_llm_list: Parse line-delimited LLM output into structured records
- parse_llm_kv: Parse key:value LLM output into dict
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def call_llm_structured(
    settings_service: Any,
    prompt: str,
    system: str = "You are a financial analysis assistant. Be concise and factual.",
    max_tokens: int = 800,
    output_schema: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Call LLM and enforce structured JSON output.

    If output_schema is provided, appends schema instruction to prompt.
    Returns parsed dict on success, None on failure or when LLM not configured.
    """
    from app.core.llm_helper import call_llm

    if output_schema:
        schema_str = json.dumps(output_schema, indent=2)
        prompt += (
            "\n\nYou MUST respond with valid JSON matching this schema:\n"
            f"{schema_str}\n"
            "Respond with ONLY the JSON object, no other text."
        )

    response = await call_llm(settings_service, prompt, system=system, max_tokens=max_tokens)
    if not response:
        return None

    # Try to extract JSON from response (may be wrapped in markdown code block)
    text = response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        logger.warning("Failed to parse LLM structured output as JSON")
        return None


def require_pro_feature(feature_name: str) -> None:
    """Feature gate stub — no-op in community edition.

    In a future Pro edition, this would check license/subscription status
    and raise SeleneSpaceError if the feature is not entitled.
    """
    pass


def parse_llm_list(text: str, separator: str = "|") -> list[dict[str, str]]:
    """Parse pipe-separated LLM output into list of dicts.

    Expects first line to be headers, subsequent lines to be values.
    """
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    if len(lines) < 2:
        return []

    headers = [h.strip() for h in lines[0].split(separator)]
    results = []
    for line in lines[1:]:
        values = [v.strip() for v in line.split(separator)]
        if len(values) == len(headers):
            results.append(dict(zip(headers, values)))
    return results


def parse_llm_kv(text: str, separator: str = ":") -> dict[str, str]:
    """Parse key:value LLM output into dict."""
    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if separator in line:
            key, _, value = line.partition(separator)
            result[key.strip()] = value.strip()
    return result
