"""Auth endpoints: register, login, redeem a code, view entitlement."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.billing_auth import (
    billing_mode_enabled,
    create_access_token,
    get_billing_repo,
    require_billing_user,
)
from app.core.dependencies import AuthenticatedUser, build_response_meta
from selene_space_types.common import APIResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class RedeemRequest(BaseModel):
    code: str = Field(min_length=4, max_length=64)


def _validate_email(email: str) -> str:
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Invalid email format")
    return email


def _repo_or_503() -> object:
    repo = get_billing_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Billing storage not wired")
    return repo


@router.post("/register")
async def register(
    request: Request,
    body: RegisterRequest,
) -> APIResponse[dict]:
    if not billing_mode_enabled():
        raise HTTPException(status_code=404, detail="Billing mode disabled")
    email = _validate_email(body.email)
    repo = _repo_or_503()
    try:
        user = await repo.create_user(email, body.password)  # type: ignore[attr-defined]
    except ValueError as exc:
        if "already" in str(exc):
            raise HTTPException(status_code=409, detail="Email already registered")
        raise
    token = create_access_token(user["id"], user["email"])
    return APIResponse(
        data={"user": user, "token": token},
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/login")
async def login(
    request: Request,
    body: LoginRequest,
) -> APIResponse[dict]:
    if not billing_mode_enabled():
        raise HTTPException(status_code=404, detail="Billing mode disabled")
    email = _validate_email(body.email)
    repo = _repo_or_503()
    user = await repo.get_user_by_email(email)  # type: ignore[attr-defined]

    from selene_space_storage.repositories.billing import verify_password

    if user is None or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user["id"], user["email"])
    return APIResponse(
        data={
            "user": {"id": user["id"], "email": user["email"], "role": user["role"]},
            "token": token,
        },
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/redeem")
async def redeem(
    request: Request,
    body: RedeemRequest,
    user: AuthenticatedUser = Depends(require_billing_user),
) -> APIResponse[dict]:
    repo = _repo_or_503()
    try:
        result = await repo.redeem_code(body.code, user.id)  # type: ignore[attr-defined]
    except ValueError as exc:
        msg = str(exc)
        if msg in ("invalid code", "code already used"):
            raise HTTPException(status_code=409, detail=msg)
        raise
    return APIResponse(data=result, meta=build_response_meta(request), error=None)


@router.get("/me")
async def entitlement(
    request: Request,
    user: AuthenticatedUser = Depends(require_billing_user),
) -> APIResponse[dict]:
    repo = get_billing_repo()
    expiry = None
    if repo is not None:
        expiry = await repo.entitlement_expiry(user.id)  # type: ignore[attr-defined]
    return APIResponse(
        data={"user_id": user.id, "entitlement_expiry": expiry},
        meta=build_response_meta(request),
        error=None,
    )
