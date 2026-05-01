"""Grid Router - 网格策略 API

修复:
- H-1: 错误处理返回 HTTPException 而非 HTTP 200
- H-2: 从 services.spot_price 导入，避免从 main.py 循环导入
"""
import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from models.grid import GridDirection
from constants import get_spot_fallback, get_dynamic_spot_price
from services.spot_price import get_spot_price

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/grid", tags=["grid"])


class GridCreateRequest(BaseModel):
    currency: str = "BTC"
    direction: str = "SHORT_PUT"
    strike: float = Field(..., gt=0)
    expiry: str = ""
    margin_ratio: float = Field(0.2, gt=0, le=1.0)
    grid_count: int = Field(4, ge=2, le=10)
    grid_range_pct: float = Field(0.15, gt=0, le=0.5)
    total_capital: float = Field(..., gt=0)


class GridAdjustRequest(BaseModel):
    position_id: int = Field(..., gt=0)
    new_strike: float = Field(..., gt=0)
    new_expiry: str = ""
    reason: str = ""


class GridCloseRequest(BaseModel):
    position_id: int = Field(..., gt=0)
    close_reason: str = "manual"


@router.post("/create")
async def create_grid(req: GridCreateRequest):
    """创建网格策略"""
    from services.unified_strategy_engine import UnifiedStrategyEngine
    from services.spot_price import get_spot_price

    try:
        spot = get_spot_price(req.currency)
        if not spot:
            spot = get_spot_fallback(req.currency)
    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
        logger.warning("Grid create: spot price failed: %s", e)
        spot = get_spot_fallback(req.currency)

    try:
        engine = UnifiedStrategyEngine()
        result = engine.generate_grid_strategy(
            currency=req.currency,
            direction=GridDirection(req.direction),
            strike=req.strike,
            expiry=req.expiry,
            margin_ratio=req.margin_ratio,
            grid_count=req.grid_count,
            grid_range_pct=req.grid_range_pct,
            total_capital=req.total_capital,
            spot_price=spot
        )
        if result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
        logger.error("Grid create failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"创建网格策略失败: {str(e)}")


@router.get("/list")
async def list_grid_positions(currency: str = "BTC"):
    """获取网格持仓列表"""
    from services.grid_manager import GridManager
    try:
        gm = GridManager()
        return gm.list_positions(currency)
    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
        logger.error("Grid list failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取网格列表失败: {str(e)}")


@router.get("/detail")
async def get_grid_detail(position_id: int):
    """获取网格详情"""
    from services.grid_manager import GridManager
    try:
        gm = GridManager()
        detail = gm.get_position_detail(position_id)
        if not detail:
            raise HTTPException(status_code=404, detail="网格持仓不存在")
        return detail
    except HTTPException:
        raise
    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
        logger.error("Grid detail failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取网格详情失败: {str(e)}")


@router.post("/adjust")
async def adjust_grid(req: GridAdjustRequest):
    """调整网格参数（滚仓）"""
    from services.grid_manager import GridManager
    try:
        gm = GridManager()
        result = gm.adjust_position(
            req.position_id, req.new_strike, req.new_expiry, req.reason
        )
        if result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
        logger.error("Grid adjust failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"调整网格失败: {str(e)}")


@router.post("/close")
async def close_grid(req: GridCloseRequest):
    """关闭网格持仓"""
    from services.grid_manager import GridManager
    try:
        gm = GridManager()
        result = gm.close_position(req.position_id, req.close_reason)
        if result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
        logger.error("Grid close failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"关闭网格失败: {str(e)}")


@router.get("/backtest")
async def backtest_grid(
    currency: str = "BTC",
    direction: str = "SHORT_PUT",
    strike: float = Query(..., gt=0),
    expiry: str = "",
    margin_ratio: float = Query(0.2, gt=0, le=1.0),
    grid_count: int = Query(4, ge=2, le=10),
    grid_range_pct: float = Query(0.15, gt=0, le=0.5),
    total_capital: float = Query(..., gt=0),
    days: int = Query(30, ge=1, le=365)
):
    """网格策略回测"""
    from services.unified_strategy_engine import UnifiedStrategyEngine

    try:
        spot = get_spot_price(currency)
        if not spot:
            spot = get_spot_fallback(currency)
    except (RuntimeError, ValueError, TypeError) as e:
        logger.warning("Grid backtest: spot price failed: %s", e)
        spot = get_spot_fallback(currency)

    try:
        engine = UnifiedStrategyEngine()
        result = engine.backtest_grid_strategy(
            currency=currency,
            direction=GridDirection(direction),
            strike=strike,
            expiry=expiry,
            margin_ratio=margin_ratio,
            grid_count=grid_count,
            grid_range_pct=grid_range_pct,
            total_capital=total_capital,
            spot_price=spot,
            days=days
        )
        if result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
        logger.error("Grid backtest failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"回测失败: {str(e)}")
