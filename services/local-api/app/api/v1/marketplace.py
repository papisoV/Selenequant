"""Marketplace endpoints — strategy listings, purchases, and publishing.

Community edition stores all data in-memory.  No real payment processing
or encryption; seller public-key endpoint returns a placeholder.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.core.dependencies import AuthenticatedUser, build_response_meta, optional_user
from selene_space_types.common import APIResponse, Pagination
from selene_space_types.errors import ErrorCode, NotFoundError, SeleneSpaceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/marketplace", tags=["marketplace"])

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------

_listings: dict[str, dict] = {}
# id -> {id, seller_user_id, strategy_config, price, tags, description,
#        status, purchase_count, rating_avg, created_at}

_purchases: dict[str, list[str]] = {}
# user_id -> list of listing_ids


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CreateListingRequest(BaseModel):
    """Body for POST /marketplace/listings."""

    strategy_config: dict[str, Any] = Field(..., description="Strategy configuration to list")
    price: float = Field(0, ge=0, description="Listing price (0 = free)")
    tags: list[str] = Field(default_factory=list, description="Tags for discoverability")
    description: str = Field("", description="Listing description")


class BuyListingRequest(BaseModel):
    """Body for POST /marketplace/listings/{listing_id}/buy."""

    device_fingerprint: str | None = Field(None, description="Device fingerprint for license binding")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/listings")
async def list_listings(
    request: Request,
    tag: str | None = Query(None, description="Filter by tag"),
    search: str | None = Query(None, description="Search in description and tags"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """Browse marketplace listings with optional tag/search filtering and pagination."""
    items = list(_listings.values())

    # Only show published listings to buyers
    items = [l for l in items if l.get("status") == "published"]

    if tag is not None:
        items = [l for l in items if tag in l.get("tags", [])]

    if search is not None:
        search_lower = search.lower()
        items = [
            l for l in items
            if search_lower in l.get("description", "").lower()
            or any(search_lower in t.lower() for t in l.get("tags", []))
        ]

    total = len(items)
    page = items[offset : offset + limit]

    return APIResponse(
        data=page,
        meta=build_response_meta(
            request,
            pagination=Pagination(offset=offset, limit=limit, total=total),
        ),
        error=None,
    )


@router.get("/listings/{listing_id}")
async def get_listing(
    listing_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Get a single listing by ID."""
    listing = _listings.get(listing_id)
    if listing is None:
        raise NotFoundError(f"Listing '{listing_id}' not found")

    return APIResponse(
        data=listing,
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/my-purchases")
async def my_purchases(
    request: Request,
    status: str | None = Query(None, description="Filter by purchase status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """List the current user's purchases."""
    purchased_ids = _purchases.get(user.id, [])
    purchased_listings = []
    for lid in purchased_ids:
        listing = _listings.get(lid)
        if listing is not None:
            entry = dict(listing)
            entry["purchase_status"] = "active"
            if status is None or entry.get("purchase_status") == status:
                purchased_listings.append(entry)

    total = len(purchased_listings)
    page = purchased_listings[offset : offset + limit]

    return APIResponse(
        data=page,
        meta=build_response_meta(
            request,
            pagination=Pagination(offset=offset, limit=limit, total=total),
        ),
        error=None,
    )


@router.get("/seller/public-key")
async def seller_public_key(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Get the seller's public key for encrypted strategy transfer.

    Community edition does not support encryption.
    """
    return APIResponse(
        data={
            "public_key": "community-edition",
            "note": "No encryption in community edition",
        },
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/listings")
async def create_listing(
    request: Request,
    body: CreateListingRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Create a new marketplace listing (starts as draft)."""
    listing_id = uuid.uuid4().hex[:12]
    now = int(time.time() * 1000)

    listing: dict[str, Any] = {
        "id": listing_id,
        "seller_user_id": user.id,
        "strategy_config": body.strategy_config,
        "price": body.price,
        "tags": body.tags,
        "description": body.description,
        "status": "draft",
        "purchase_count": 0,
        "rating_avg": 0.0,
        "created_at": now,
    }
    _listings[listing_id] = listing

    return APIResponse(
        data=listing,
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/listings/{listing_id}/buy")
async def buy_listing(
    listing_id: str,
    request: Request,
    body: BuyListingRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Purchase a listing.  Copies the strategy config to the buyer's purchases."""
    listing = _listings.get(listing_id)
    if listing is None:
        raise NotFoundError(f"Listing '{listing_id}' not found")

    if listing.get("status") != "published":
        raise SeleneSpaceError(
            code=ErrorCode.VALIDATION_ERROR,
            message=f"Listing '{listing_id}' is not published",
        )

    # Record purchase
    user_purchases = _purchases.setdefault(user.id, [])
    if listing_id not in user_purchases:
        user_purchases.append(listing_id)
        listing["purchase_count"] = listing.get("purchase_count", 0) + 1

    return APIResponse(
        data={"purchased": True, "listing_id": listing_id},
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/listings/{listing_id}/publish")
async def publish_listing(
    listing_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Publish a draft listing, making it visible to buyers."""
    listing = _listings.get(listing_id)
    if listing is None:
        raise NotFoundError(f"Listing '{listing_id}' not found")

    if listing.get("seller_user_id") != user.id and user.id != "local":
        raise SeleneSpaceError(
            code=ErrorCode.PERMISSION_DENIED,
            message="Only the seller can publish this listing",
        )

    listing["status"] = "published"

    return APIResponse(
        data={"published": True, "listing_id": listing_id},
        meta=build_response_meta(request),
        error=None,
    )
