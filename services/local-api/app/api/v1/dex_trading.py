"""DEX trading API endpoints — Solana/Jupiter swap operations.

Provides endpoints for:
- Viewing DEX portfolio positions
- Buying/selling tokens via Jupiter aggregator
- Checking SOL balance
- Emergency stop
- AI-powered token scanning
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from selene_space_types.common import APIResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dex-trading", tags=["dex-trading"])

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TradeRequest(BaseModel):
    token_address: str = Field(..., description="Solana token mint address")
    amount_sol: float = Field(..., gt=0, description="Amount in SOL to spend")
    slippage_bps: int = Field(500, ge=50, le=5000, description="Slippage tolerance in basis points")


class SellRequest(BaseModel):
    token_address: str = Field(..., description="Solana token mint address")
    percentage: float = Field(1.0, ge=0.01, le=1.0, description="Fraction of position to sell")
    slippage_bps: int = Field(500, ge=50, le=5000, description="Slippage tolerance in basis points")


class ScanRequest(BaseModel):
    min_score: float = Field(0.85, ge=0.0, le=1.0, description="Minimum AI score threshold")
    max_results: int = Field(20, ge=1, le=100, description="Maximum tokens to return")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_trader = None
_stop_signal = False


async def _get_trader():
    global _trader
    if _trader is None:
        from selene_space_exchange.exchanges.solana import SolanaExecutionConfig

        if not SolanaExecutionConfig.has_private_key():
            raise HTTPException(
                status_code=503,
                detail="Solana private key not configured. Set SOLANA_PRIVATE_KEY in .env",
            )
        from selene_space_exchange.exchanges.solana import SolanaTrader

        _trader = SolanaTrader()
    return _trader


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/balance")
async def get_sol_balance() -> APIResponse:
    """Get current SOL balance."""
    try:
        trader = await _get_trader()
        balance = await trader.rpc.get_balance()
        return APIResponse(success=True, data={"asset": "SOL", "balance": balance})
    except HTTPException:
        raise
    except Exception as e:
        return APIResponse(success=False, message=str(e), data={"balance": 0.0})


@router.get("/portfolio")
async def get_dex_portfolio() -> APIResponse:
    """Get current DEX portfolio positions."""
    # Portfolio is stored locally as JSON
    from pathlib import Path
    import json

    portfolio_path = Path("data/dex_portfolio.json")
    if portfolio_path.exists():
        with open(portfolio_path) as f:
            positions = json.load(f)
    else:
        positions = []

    return APIResponse(success=True, data={"positions": positions})


@router.post("/trade/buy")
async def buy_token(request: TradeRequest) -> APIResponse:
    """Buy a token with SOL via Jupiter aggregator."""
    if _stop_signal:
        raise HTTPException(status_code=403, detail="Trading is stopped (emergency stop active)")

    try:
        trader = await _get_trader()
        sig = await trader.buy(
            token_address=request.token_address,
            amount_sol=request.amount_sol,
            slippage_bps=request.slippage_bps,
        )
        if sig:
            return APIResponse(
                success=True,
                data={"signature": sig, "token": request.token_address, "amount_sol": request.amount_sol},
            )
        return APIResponse(success=False, message="Buy failed — no signature returned")
    except HTTPException:
        raise
    except Exception as e:
        return APIResponse(success=False, message=str(e))


@router.post("/trade/sell")
async def sell_token(request: SellRequest) -> APIResponse:
    """Sell a token for SOL via Jupiter aggregator."""
    if _stop_signal:
        raise HTTPException(status_code=403, detail="Trading is stopped (emergency stop active)")

    try:
        trader = await _get_trader()
        sig = await trader.sell(
            token_address=request.token_address,
            percentage=request.percentage,
            slippage_bps=request.slippage_bps,
        )
        if sig:
            return APIResponse(
                success=True,
                data={"signature": sig, "token": request.token_address, "percentage": request.percentage},
            )
        return APIResponse(success=False, message="Sell failed — no signature returned")
    except HTTPException:
        raise
    except Exception as e:
        return APIResponse(success=False, message=str(e))


@router.post("/emergency-stop")
async def emergency_stop() -> APIResponse:
    """Emergency stop — halts all DEX trading immediately."""
    global _stop_signal
    _stop_signal = True
    logger.warning("Emergency stop activated!")

    # Write stop signal file (compatible with the formula-policy strategy runner)
    from pathlib import Path

    Path("STOP_SIGNAL").write_text("STOP")

    return APIResponse(success=True, data={"stopped_at": time.time()})


@router.post("/emergency-stop/reset")
async def reset_emergency_stop() -> APIResponse:
    """Reset emergency stop — allows trading to resume."""
    global _stop_signal
    _stop_signal = False
    from pathlib import Path

    stop_file = Path("STOP_SIGNAL")
    if stop_file.exists():
        stop_file.unlink()

    return APIResponse(success=True, data={"trading_resumed_at": time.time()})


@router.get("/scan")
async def scan_tokens(min_score: float = 0.85, max_results: int = 20) -> APIResponse:
    """Scan top tokens by AI score from alpha formula."""
    try:
        from selene_space_alpha.data_loader import CryptoDataLoader
        from selene_space_alpha.vm import StackVM
        from selene_space_alpha.vocab import FORMULA_VOCAB
        from pathlib import Path
        import json
        import torch

        # Load best formula
        formula_path = Path("data/alpha_results/best_meme_strategy.json")
        if not formula_path.exists():
            return APIResponse(success=True, data={"tokens": [], "message": "No trained formula found"})

        with open(formula_path) as f:
            formula_tokens = json.load(f)

        loader = CryptoDataLoader()
        loader.load_data(limit_tokens=500)

        vm = StackVM()
        raw_signals = vm.execute(formula_tokens, loader.feat_tensor)
        if raw_signals is None:
            return APIResponse(success=True, data={"tokens": [], "message": "Formula execution failed"})

        latest_signals = raw_signals[:, -1]
        scores = torch.sigmoid(latest_signals).cpu().numpy()

        sorted_indices = scores.argsort()[::-1]
        results = []
        idx_to_addr = {v: k for k, v in enumerate(loader.addresses)}

        for idx in sorted_indices[:max_results]:
            score = float(scores[idx])
            if score < min_score:
                break
            addr = idx_to_addr.get(int(idx), f"token_{idx}")
            results.append({"address": addr, "score": round(score, 4)})

        return APIResponse(success=True, data={"tokens": results, "formula": FORMULA_VOCAB.decode(formula_tokens)})

    except Exception as e:
        return APIResponse(success=False, message=str(e), data={"tokens": []})
