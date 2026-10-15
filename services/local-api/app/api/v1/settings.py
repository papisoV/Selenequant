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

    if "llm" in payload:
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
