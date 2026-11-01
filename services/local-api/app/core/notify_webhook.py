"""行情分析结果通知（飞书 webhook / PushPlus），仅后端调用（US-304）。

env 开关默认关（``SELENE_ANALYSIS_NOTIFY_ENABLED`` 置 "1" 才启用）：
  SELENE_FEISHU_WEBHOOK_URL   飞书自定义机器人 webhook URL
  SELENE_PUSHPLUS_TOKEN       PushPlus token

webhook 密钥只存在于后端环境变量，绝不下发前端；前端仅调用本后端端点
（services/local-api/app/api/v1/market_analysis.py 的 POST /notify）。
"""
from __future__ import annotations

import json
import logging
import os

import httpx

from selene_space_core.resilience import OutboundGateway

logger = logging.getLogger(__name__)

_MAX_FEISHU_CHARS = 3900
_MAX_PUSHPLUS_CHARS = 10000


def _bool_env(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def render_analysis_brief(record: dict) -> str:
    """把分析记录压缩为通知文本（不含任何参考实现命名）。"""
    meta = record.get("meta") or {}
    input_data = record.get("input_data") or {}
    stage1 = record.get("stage1") or {}
    stage2 = record.get("stage2") or {}
    lines = [
        "【行情分析】",
        f"{input_data.get('symbol')} {input_data.get('interval')} "
        f"({input_data.get('exchange')}/{input_data.get('market_type')})",
        f"市场状态: {stage1.get('market_regime', '-')}",
        f"入场门: {stage1.get('entry_gate', '-')}",
        f"交易决策: {stage2.get('decision', '-')}",
    ]
    if meta.get("record_id"):
        lines.append(f"记录: {meta['record_id']}")
    summary = stage2.get("decision_summary") or stage1.get("diagnosis_summary") or {}
    if isinstance(summary, dict) and summary:
        lines.append(f"摘要: {json.dumps(summary, ensure_ascii=False)[:400]}")
    lines.append("仅供研究参考，不构成投资建议。")
    return "\n".join(lines)


class AnalysisNotifier:
    """按 env 开关与渠道配置发送分析结果通知（失败记日志不抛）。"""

    def __init__(self, client: OutboundGateway | None = None) -> None:
        self._client = client or OutboundGateway(
            default_timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
            default_circuit=True,
        )
        self._owns_client = client is None

    @property
    def enabled(self) -> bool:
        return _bool_env("SELENE_ANALYSIS_NOTIFY_ENABLED")

    async def send(self, text: str) -> dict[str, str]:
        """发送通知，返回各渠道投递状态。未开启/未配置渠道时返回说明。"""
        if not self.enabled:
            return {
                "status": "disabled",
                "reason": "SELENE_ANALYSIS_NOTIFY_ENABLED is not enabled",
            }
        results: dict[str, str] = {}
        feishu_url = os.environ.get("SELENE_FEISHU_WEBHOOK_URL", "").strip()
        if feishu_url:
            results["feishu"] = await self._send_feishu(feishu_url, text)
        pushplus_token = os.environ.get("SELENE_PUSHPLUS_TOKEN", "").strip()
        if pushplus_token:
            results["pushplus"] = await self._send_pushplus(pushplus_token, text)
        if not results:
            return {"status": "skipped", "reason": "no notification channel configured"}
        results["status"] = "sent"
        return results

    async def _send_feishu(self, url: str, text: str) -> str:
        try:
            resp = await self._client.post(
                url,
                json={"msg_type": "text", "content": {"text": text[:_MAX_FEISHU_CHARS]}},
            )
            resp.raise_for_status()
            return f"sent:{resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("feishu notify failed: %s", exc)
            return f"failed:{type(exc).__name__}"

    async def _send_pushplus(self, token: str, text: str) -> str:
        try:
            resp = await self._client.post(
                "https://www.pushplus.plus/send",
                json={
                    "token": token,
                    "title": "行情分析",
                    "content": text[:_MAX_PUSHPLUS_CHARS],
                },
            )
            resp.raise_for_status()
            return f"sent:{resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("pushplus notify failed: %s", exc)
            return f"failed:{type(exc).__name__}"

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()


__all__ = ["AnalysisNotifier", "render_analysis_brief"]
