"""Stock market signal scoring and daily report endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from selene_space_types.common import APIResponse
from selene_space_types.stock import AssetAnalysis, DailyReport, SignalScore, StockAsset

router = APIRouter(prefix="/stock", tags=["stock"])


@router.get("/score/{symbol}", response_model=APIResponse[SignalScore])
async def get_stock_score(symbol: str) -> APIResponse[SignalScore]:
    """Get 0-100 signal score for a stock symbol."""
    from selene_space_stock.indicators import calculate_indicators
    from selene_space_stock.scoring import score_asset
    from selene_space_stock.market_data import fetch_history

    asset = StockAsset(symbol=symbol)
    try:
        _, _, history = fetch_history(asset, lookback_days=200)
        metrics = calculate_indicators(history)
        signal = score_asset(metrics)
        return APIResponse(data=signal)
    except Exception as exc:
        return APIResponse(error={"code": "FETCH_ERROR", "message": str(exc)})


@router.get("/analysis/{symbol}", response_model=APIResponse[AssetAnalysis])
async def get_stock_analysis(symbol: str) -> APIResponse[AssetAnalysis]:
    """Get full analysis for a stock symbol."""
    from selene_space_stock.engine import run_analysis
    from selene_space_stock.config import StockConfig, AnalysisSettings, DataSettings

    cfg = StockConfig(
        assets=[StockAsset(symbol=symbol)],
        analysis=AnalysisSettings(),
        data=DataSettings(),
    )
    try:
        report = run_analysis(cfg)
        if report.analyses:
            return APIResponse(data=report.analyses[0])
        return APIResponse(error={"code": "NO_DATA", "message": f"No analysis for {symbol}"})
    except Exception as exc:
        return APIResponse(error={"code": "ANALYSIS_ERROR", "message": str(exc)})


@router.post("/report", response_model=APIResponse[DailyReport])
async def generate_report(symbols: str = "AAPL,MSFT") -> APIResponse[DailyReport]:
    """Generate a daily report for comma-separated symbols."""
    from selene_space_stock.config import StockConfig, AnalysisSettings, DataSettings
    from selene_space_stock.engine import run_analysis

    assets = [StockAsset(symbol=s.strip()) for s in symbols.split(",")]
    cfg = StockConfig(assets=assets, analysis=AnalysisSettings(), data=DataSettings())
    try:
        report = run_analysis(cfg)
        return APIResponse(data=report)
    except Exception as exc:
        return APIResponse(error={"code": "REPORT_ERROR", "message": str(exc)})
