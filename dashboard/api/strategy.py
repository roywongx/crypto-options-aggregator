"""策略计算 API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List

router = APIRouter(prefix="/api", tags=["strategy"])


class StrategyCalcRequest(BaseModel):
    mode: str = Field(default="roll")
    currency: str = Field(default="BTC")
    current_strike: float = Field(default=0)
    current_qty: float = Field(default=1)
    target_strike: Optional[float] = Field(default=None)
    target_expiry: Optional[str] = Field(default=None)
    margin_ratio: float = Field(default=0.2)
    min_dte: int = Field(default=7)
    max_dte: int = Field(default=45)
    option_type: str = Field(default="PUT")


@router.post("/strategy-calc")
async def strategy_calc(params: StrategyCalcRequest):
    """统一策略推荐引擎 - Roll/New/Grid 三种模式"""
    from services.strategy_calc import calc_roll_plan, calc_new_plan
    from services.spot_price import get_spot_price
    from services.risk_framework import RiskFramework

    spot = get_spot_price(params.currency)
    mode = params.mode.lower()

    if mode == "roll":
        if not params.target_strike or not params.target_expiry:
            raise HTTPException(status_code=400, detail="滚仓模式需要提供 target_strike 和 target_expiry")
        result = calc_roll_plan(
            current_strike=params.current_strike,
            current_qty=params.current_qty,
            target_strike=params.target_strike,
            target_expiry=params.target_expiry,
            spot=spot,
            margin_ratio=params.margin_ratio
        )
    elif mode == "new":
        result = calc_new_plan(
            currency=params.currency,
            spot=spot,
            min_dte=params.min_dte,
            max_dte=params.max_dte,
            margin_ratio=params.margin_ratio,
            option_type=params.option_type
        )
    elif mode == "grid":
        result = {"mode": "grid", "message": "网格策略计算待实现", "spot": spot}
    else:
        raise HTTPException(status_code=400, detail=f"不支持的模式: {mode}")

    result["spot"] = spot
    result["risk_status"] = RiskFramework.get_status(spot)
    return result


@router.post("/calculator/roll")
async def calculator_roll(params: StrategyCalcRequest):
    """滚仓计算器"""
    return await strategy_calc(params)
