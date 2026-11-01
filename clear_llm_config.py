"""清掉 keyring 里的 LLM 配置, 让服务回落到 .env 的值。

来源: 最初移植自作者本人的既有项目 binance-quant(无第三方许可问题)。
2026-10-05 cleanroom 重写: provider 前缀改常量、逐字段结果用统一的 `_clear()`
收口; 保留"逐字段报 OK/FAIL 后提示重启"的交互形状。

用法: call activate.bat && python clear_llm_config.py
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

from selene_space_security.secrets import SecretManager, SecretRef, SecretSource

#: keyring 里 LLM 配置的 provider 前缀(与 settings_service 的命名一致)。
LLM_PROVIDER = "selenespace.llm"
#: 需要清空的字段。
LLM_FIELDS = ("base_url", "model", "api_key")


async def _clear(manager: SecretManager, field: str) -> bool:
    """删一个字段; 返回是否成功(失败只打印, 不中断其余字段)。"""
    ref = SecretRef(source=SecretSource.KEYRING, provider=LLM_PROVIDER, id=field)
    try:
        await manager.delete(ref)
    except Exception as exc:
        print(f"FAIL: Failed to clear {field}: {exc}")
        return False
    print(f"OK: Cleared keyring field: {field}")
    return True


async def main() -> None:
    """把 LLM 的三个字段逐个从 keyring 清掉。"""
    manager = SecretManager()
    for field in LLM_FIELDS:
        await _clear(manager, field)
    print("\nPlease restart the local-api service for changes to take effect.")
    print("The service will now use values from .env file.")


if __name__ == "__main__":
    asyncio.run(main())
