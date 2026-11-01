"""手工冒烟: 打 local-api 的 paper 权益曲线端点, 打印状态与条数。

来源: 最初移植自作者本人的既有项目 binance-quant(无第三方许可问题)。
2026-10-05 cleanroom 重写: BASE_URL 走 `SELENE_API_URL` 环境变量(原硬编码上游
端口 8080, 本仓默认 8088); 统一超时与异常出口。

用法: call activate.bat && python scripts/test_api_equity.py
"""

from __future__ import annotations

import json
import os

import requests

#: 本仓 local-api 默认端口; 部署换端口时用环境变量覆盖。
BASE_URL = os.getenv("SELENE_API_URL", "http://127.0.0.1:8088") + "/api/v1/paper"
ACCOUNT_ID = "paper-default"


def test_equity_history() -> None:
    """GET 一次权益曲线端点并打印结果(手工冒烟, 不做断言)。"""
    url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/equity-history"
    print(f"Testing: {url}")
    try:
        resp = requests.get(url, timeout=10)
    except requests.RequestException as exc:
        print(f"Connection failed: {exc}")
        return

    print(f"Status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"Error: {resp.text}")
        return

    points = resp.json().get("data") or []
    print(f"Data received: {len(points)} points")
    print(json.dumps(points, indent=2))


if __name__ == "__main__":
    test_equity_history()
