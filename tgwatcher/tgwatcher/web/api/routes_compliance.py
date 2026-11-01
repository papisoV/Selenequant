"""Compliance routes — public legal documents and data retention info.

Routes:
  GET /api/compliance/privacy  — privacy policy
  GET /api/compliance/tos      — terms of service
No auth required (public legal documents).
"""
import logging
from pathlib import Path

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

bp = Blueprint("compliance", __name__, url_prefix="")

_DEFAULT_PRIVACY_POLICY = """# 隐私政策 / Privacy Policy

最后更新 / Last Updated: 2026-07-30

## 数据收集 / Data Collection

TGWatcher 收集以下数据用于信号分析服务：
- Telegram 群组消息内容（仅用户配置的群组）
- 信号分析结果（方向、强度、紧急度等）
- Bot 推送订阅信息（chat_id、过滤偏好）

## 数据使用 / Data Usage

所有数据仅用于：
1. 加密货币市场信号分析
2. 向订阅用户推送信号通知
3. 生成市场摘要报告

## 数据保留 / Data Retention

数据保留期限由管理员配置（默认 365 天）。超过保留期的数据自动删除。

## 数据安全 / Data Security

- 所有币种名称在输出中使用代号替换（标的A、标的B...）
- 数据存储在本地 SQLite 数据库，不传输至第三方
- API 访问需要认证 Token

## 联系方式 / Contact

如有隐私相关问题，请联系管理员。
"""

_DEFAULT_TOS = """# 服务条款 / Terms of Service

最后更新 / Last Updated: 2026-07-30

## 服务描述 / Service Description

TGWatcher 是一个加密货币市场信号分析工具，通过 Telegram Bot API 提供信号推送服务。

## 免责声明 / Disclaimer

1. 本服务提供的所有信号仅供参考，不构成投资建议
2. 信号基于 AI 分析，可能存在误判
3. 用户应自行判断并承担投资决策的全部责任
4. 过往信号表现不代表未来收益

## 服务等级 / Service Tiers

- Free: 每日 10 条信号，代号化输出
- Pro: 每日 100 条信号，含市场摘要
- Enterprise: 无限制信号，含 Webhook 和 API 访问

## 使用限制 / Usage Limits

- 用户不得将信号用于非法用途
- 用户不得转售或分发信号内容
- 服务可能因维护或升级暂时中断

## 终止 / Termination

管理员保留随时终止或修改服务的权利。
"""


@bp.route("/api/compliance/privacy", methods=["GET"])
def privacy_policy():
    """Return privacy policy text."""
    from ._legacy import _app_state
    config = _app_state.config or {}
    compliance_cfg = config.get("compliance", {})
    policy_path = compliance_cfg.get("privacy_policy_path")

    if policy_path:
        path = Path(policy_path)
        if path.exists():
            return jsonify({"content": path.read_text(encoding="utf-8")})

    return jsonify({"content": _DEFAULT_PRIVACY_POLICY})


@bp.route("/api/compliance/tos", methods=["GET"])
def terms_of_service():
    """Return terms of service text."""
    from ._legacy import _app_state
    config = _app_state.config or {}
    compliance_cfg = config.get("compliance", {})
    tos_path = compliance_cfg.get("tos_path")

    if tos_path:
        path = Path(tos_path)
        if path.exists():
            return jsonify({"content": path.read_text(encoding="utf-8")})

    return jsonify({"content": _DEFAULT_TOS})
