"""Settings endpoints with secure secret handling via OS keychain."""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.crypto import KeyPairManager
from app.core.dependencies import (
    AuthenticatedUser,
    build_response_meta,
    get_key_pair_manager,
    get_settings_service,
    optional_user,
)
from app.core.settings_service import SettingsService
from selene_space_security.secrets import SecretResolutionError
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


def _ok(request: Request, data: dict[str, Any]) -> APIResponse[dict[str, Any]]:
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


def _uid(user: AuthenticatedUser) -> str | None:
    """None 表示本机单用户模式（不使用按用户分区的存储）。"""
    return user.id if user.id != "local" else None


class ExchangeSettingsUpdate(BaseModel):
    provider: Literal["binance", "okx"] | None = None
    market_type: Literal["spot", "futures"] | None = None
    testnet: bool | None = None
    api_key: str | None = None
    api_secret: str | None = None
    clear_api_key: bool = False
    clear_api_secret: bool = False


class LLMSettingsUpdate(BaseModel):
    provider: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    base_url: str | None = None
    model: str | None = None
    smart_mode: bool | None = None
    smart_models: dict[str, str] | None = None


class NetworkSettingsUpdate(BaseModel):
    proxy_url: str | None = None


class NetworkTestRequest(BaseModel):
    # 缺省(None)= 测「当前保存的代理」;显式空串 = 测直连。两者必须可区分 ——
    # 否则页面上清空输入框点「测试连接」永远测不到直连那一组。
    proxy_url: str | None = None


class WorkspaceLayoutsUpdate(BaseModel):
    """工作台布局同步 — 客户端整份推上来,服务端只校验形状(见 settings_service)。

    单独建模而非 ``dict[str, Any]``:形状错误(把 layouts 推成对象)在请求校验层
    就拒掉,而不是落到服务端后静默当成「未提交」。
    """

    layouts: list[dict[str, Any]] | None = Field(
        default=None,
        description="Workspace layouts to persist (replaces the stored set)",
    )
    tombstones: dict[str, str] | None = Field(
        default=None,
        description="Deleted layout ids → deletion timestamp, so they are not resurrected",
    )


class SettingsUpdateRequest(BaseModel):
    general: dict[str, Any] | None = Field(default=None)
    exchange: ExchangeSettingsUpdate | None = Field(default=None)
    llm: LLMSettingsUpdate | None = Field(default=None)
    risk: dict[str, Any] | None = Field(default=None)
    risk_alerts: dict[str, Any] | None = Field(
        default=None, description="Risk alert popup settings: enabled, min_level, rules"
    )
    network: NetworkSettingsUpdate | None = Field(default=None)
    grid: dict[str, Any] | None = Field(default=None)
    dex: dict[str, Any] | None = Field(default=None)
    sentinel: dict[str, Any] | None = Field(default=None)
    pa_heartbeat: dict[str, Any] | None = Field(
        default=None,
        description="PA heartbeat: enabled, daily_max (0 = no cap), interval, hour_utc",
    )
    llm_budget: dict[str, Any] | None = Field(
        default=None,
        description="Daily LLM token ceiling: daily_tokens (0 = no cap)",
    )
    mission_worker: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Mission worker: enabled, interval_minutes (1-1440), "
            "max_rounds_per_mission (1-20)"
        ),
    )
    research_autorenew: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Auto-renew settlement-derived questions (default OFF): enabled, "
            "interval_minutes (1-1440), max_per_round (1-20), auto_promote"
        ),
    )
    research_sensor: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Research sensor, read-only market scan (default OFF): enabled, "
            "interval_minutes (1-1440), question_limit (1-2000)"
        ),
    )
    workspace_layouts: WorkspaceLayoutsUpdate | None = Field(
        default=None,
        description="Workspace layouts + tombstones, synced across devices",
    )


@router.get("")
async def get_settings(
    request: Request,
    service: SettingsService = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    uid = _uid(user)
    data = await service.get_settings(user_id=uid)
    return _ok(request, data)


@router.put("")
async def update_settings(
    request: Request,
    body: SettingsUpdateRequest,
    service: SettingsService = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    uid = _uid(user)
    payload = body.model_dump(exclude_none=True)
    try:
        data = await service.update_settings(payload, user_id=uid)
    except SecretResolutionError as exc:
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Secure secret storage is unavailable on this system",
            details={"reason": str(exc)},
        ) from exc

    # The agent runtime builds its httpx client (with the network proxy) at
    # construction time, so a proxy change is just as load-bearing as an LLM
    # change for the next chat turn.
    if "llm" in payload or "network" in payload:
        await _refresh_agent_runtime(service, user_id=uid)

    from app.core.audit import audit_event

    changed_sections = [k for k in payload if k not in ("_",)]
    await audit_event(
        event_type="settings.update",
        actor=user.id,
        action="update_settings",
        resource=",".join(changed_sections),
        details={"sections": changed_sections},
    )

    return _ok(request, data)


async def _refresh_agent_runtime(
    service: SettingsService, *, user_id: str | None = None
) -> None:
    """Invalidate cached runtime so it will be rebuilt with new LLM settings."""
    import logging

    from app.api.v1.agent import invalidate_user_runtime

    log = logging.getLogger(__name__)
    invalidate_user_runtime(user_id)
    log.info("Invalidated agent runtime cache for user %s", user_id or "local")


@router.get("/llm-usage")
async def get_llm_usage(
    request: Request,
    service: SettingsService = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Today's measured LLM token usage next to the configured ceiling.

    Read-only and side-effect free (it never dumps the in-memory state to the
    ledger), so the panel can poll it while the user is editing.
    """
    from app.core.llm_usage import budget_state

    return _ok(request, budget_state(service))


@router.get("/public-key")
async def get_public_key(
    request: Request,
    manager: KeyPairManager = Depends(get_key_pair_manager),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Return the ephemeral RSA public key (JWK) for encrypting secrets."""
    if manager is None:
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Encryption key pair is not available",
        )
    return _ok(request, manager.public_key_jwk())


@router.post("/exchange/test")
async def test_exchange_credentials(
    request: Request,
    service: SettingsService = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Probe stored exchange credentials against the venue (read-only call)."""
    uid = _uid(user)
    config = await service.resolve_exchange_config(user_id=uid)
    if not config["api_key"] or not config["api_secret"]:
        raise SeleneSpaceError(
            code=ErrorCode.VALIDATION_ERROR,
            message="Exchange API key/secret not configured — save credentials first",
        )

    import contextlib

    from selene_space_exchange.base.auth import ExchangeCredentials
    from selene_space_exchange.exceptions import ExchangeError

    credentials = ExchangeCredentials(
        api_key=config["api_key"], api_secret=config["api_secret"]
    )
    provider = str(config["provider"])
    market_type = str(config["market_type"])
    testnet = bool(config["testnet"])
    account_summary: dict[str, Any] = {}

    client: Any = None
    try:
        if provider == "binance":
            if market_type == "futures":
                from selene_space_exchange.exchanges.binance.futures_client import (
                    BinanceFuturesRESTClient,
                )

                client = BinanceFuturesRESTClient(credentials, testnet=testnet)
                account = await client.get_account()
                account_summary = {
                    "balances": len(account.get("assets", []) or []),
                    "total_wallet_balance": account.get("totalWalletBalance"),
                }
            else:
                from selene_space_exchange.exchanges.binance.rest_client import (
                    BinanceRESTClient,
                )

                client = BinanceRESTClient(credentials, testnet=testnet)
                account = await client.get_account(omit_zero_balances=True)
                account_summary = {
                    "can_trade": bool(account.get("canTrade")),
                    "balances": len(account.get("balances", []) or []),
                }
        elif provider == "okx":
            from selene_space_exchange.exchanges.okx.rest_client import OKXRESTClient

            client = OKXRESTClient(credentials, demo=testnet)
            balance = await client.get_balance()
            data = balance.get("data") or [{}]
            account_summary = {
                "details": len((data[0] or {}).get("details", []) or []),
            }
        else:
            raise SeleneSpaceError(
                code=ErrorCode.VALIDATION_ERROR,
                message=f"Unsupported provider: {provider}",
            )

        logger.info(
            "Exchange credential probe OK: provider=%s market=%s testnet=%s",
            provider,
            market_type,
            testnet,
        )
        data = {
            "tested": True,
            "provider": provider,
            "market_type": market_type,
            "testnet": testnet,
            "account_summary": account_summary,
            "message": "Credentials valid — account reachable",
        }
        return _ok(request, data)
    except ExchangeError as exc:
        logger.warning("Exchange credential probe rejected: %s", exc)
        raise SeleneSpaceError(
            code=ErrorCode.AUTHENTICATION_ERROR,
            message=f"Exchange rejected the credentials: {exc}",
        ) from exc
    except SeleneSpaceError:
        raise
    except Exception as exc:
        logger.warning("Exchange credential probe failed: %s", exc)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=f"Could not reach {provider} ({type(exc).__name__}) — check network/proxy",
        ) from exc
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()


@router.get("/llm/models")
async def list_llm_models(
    request: Request,
    service: SettingsService = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Fetch available models from the configured LLM provider."""
    import logging

    from selene_space_llm.base import LLMAuthError, LLMConfig, LLMConnectionError
    from selene_space_llm.openai_compat import OpenAICompatProvider

    log = logging.getLogger(__name__)

    uid = _uid(user)
    llm_full = await service.resolve_llm_full_config(user_id=uid)

    api_key = llm_full.get("api_key")
    if not api_key:
        raise SeleneSpaceError(
            code=ErrorCode.VALIDATION_ERROR,
            message="LLM API key not configured",
        )

    # Load LLM timeout from environment (default 120 seconds)
    llm_timeout = float(os.getenv("SELENE_LLM_TIMEOUT_SECONDS", "120"))

    config = LLMConfig(
        model=llm_full.get("model") or "default",
        api_key=api_key,
        base_url=llm_full.get("base_url") or None,
    )

    provider = OpenAICompatProvider(config, timeout=llm_timeout)
    try:
        models_data = await provider.list_models()
        seen: set[str] = set()
        models = []
        for m in models_data:
            mid = m.get("id", "")
            if mid and mid not in seen:
                seen.add(mid)
                models.append(
                    {
                        "id": mid,
                        "name": mid,
                        "owned_by": m.get("owned_by", ""),
                        "created": m.get("created", 0),
                    }
                )
        current_model = llm_full.get("model", "")
        return _ok(
            request,
            {"models": models, "current_model": current_model, "total": len(models)},
        )
    except LLMAuthError as exc:
        log.warning("LLM authentication failed: %s", exc)
        raise SeleneSpaceError(
            code=ErrorCode.AUTHENTICATION_ERROR,
            message="Invalid API key or authentication failed",
        ) from exc
    except LLMConnectionError as exc:
        log.warning("LLM connection failed: %s", exc)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Failed to connect to LLM provider",
        ) from exc
    except Exception as exc:
        log.error("Failed to list models: %s", exc, exc_info=True)
        raise SeleneSpaceError(
            code=ErrorCode.INTERNAL_ERROR,
            message="Failed to fetch models",
        ) from exc
    finally:
        await provider.aclose()


def _effective_proxy(request: Request) -> tuple[str, bool]:
    """What the running process built its fetchers with, and whether we know.

    ``main.py::lifespan`` publishes it after ``_init_market_data``. Absent state
    means either an older build or a failed startup — either way reporting a
    confident value would be inventing one.
    """
    state = request.app.state
    if not hasattr(state, "effective_proxy_url"):
        return "", False
    return (state.effective_proxy_url or ""), True


@router.get("/network/status")
async def network_status(
    request: Request,
    service: SettingsService = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Which proxy layer is in effect, and whether the saved value is stale.

    The settings page cannot work this out alone: the frontend only knows what
    is on disk, while what matters is what startup handed to the fetchers.
    """
    from app.core.proxy_probe import describe_status

    uid = _uid(user)
    settings = await service.get_settings(user_id=uid)
    saved = (settings.get("network") or {}).get("proxy_url") or ""
    effective, known = _effective_proxy(request)
    return _ok(
        request,
        describe_status(saved=saved, effective=effective, effective_known=known),
    )


@router.post("/network/detect")
async def network_detect(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Scan localhost for a proxy the user already has running.

    Replaces the old "type a URL" box with no guidance: on most machines the
    proxy is already listening and only its port is a mystery.
    """
    from app.core.proxy_probe import detect_candidates

    candidates = await detect_candidates()
    usable = [c for c in candidates if c["reachable"]]
    logger.info(
        "Proxy detect: %d listening, %d usable (%s)",
        len(candidates),
        len(usable),
        ", ".join(c["url"] for c in usable) or "none",
    )
    return _ok(
        request,
        {
            "candidates": candidates,
            "recommended": usable[0]["url"] if usable else "",
        },
    )


@router.post("/network/test")
async def network_test(
    request: Request,
    body: NetworkTestRequest,
    service: SettingsService = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Probe the venues the backend actually uses, through a given proxy.

    Deliberately server-side: the browser has its own network stack, so a
    browser fetch through the proxy can succeed while httpx in the backend
    fails, which is the failure mode users were left guessing about.
    Empty ``proxy_url`` tests the direct connection as a control.
    """
    from app.core.proxy_probe import normalize_proxy, probe_targets

    uid = _uid(user)
    settings = await service.get_settings(user_id=uid)
    saved = (settings.get("network") or {}).get("proxy_url") or ""
    # proxy_url=None → 用保存值;proxy_url="" → 直连对照组。
    requested = saved if body.proxy_url is None else body.proxy_url
    proxy = normalize_proxy(requested) or None

    results = await probe_targets(proxy)
    reachable = sum(1 for r in results if r["ok"])
    logger.info(
        "Proxy test: proxy=%s reachable=%d/%d",
        "direct" if proxy is None else proxy,
        reachable,
        len(results),
    )
    return _ok(
        request,
        {
            "proxy": proxy or "",
            "direct": proxy is None,
            "results": results,
            "reachable": reachable,
            "total": len(results),
        },
    )
