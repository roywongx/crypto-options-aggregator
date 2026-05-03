# api/analytics.py
"""策略分析 API — Payoff + Wheel 模拟"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])


class PayoffRequest(BaseModel):
    mode: str = Field(default="single")
    spot: float = Field(gt=0)
    strike: float = Field(default=0)
    premium: float = Field(default=0)
    option_type: str = Field(default="PUT")
    dte: int = Field(default=30, ge=1)
    iv: float = Field(default=60, gt=0)
    quantity: float = Field(default=1, gt=0)
    side: str = Field(default="sell")
    legs: Optional[List[Dict[str, Any]]] = None
    pct_range: float = Field(default=0.3, ge=0.1, le=1.0)
    steps: int = Field(default=100, ge=10, le=500)


class WheelRequest(BaseModel):
    spot: float = Field(gt=0)
    strike: float = Field(gt=0)
    premium: float = Field(ge=0)
    option_type: str = Field(default="PUT")
    cycles: int = Field(default=6, ge=1, le=24)
    capital: float = Field(gt=0)
    assigned_pct: float = Field(default=0.5, ge=0, le=1)
    iv: float = Field(default=0.6, gt=0)
    simulations: int = Field(default=1000, ge=100, le=5000)


class EstimateRequest(BaseModel):
    spot: float = Field(gt=0)
    strike: float = Field(gt=0)
    dte: int = Field(default=30, ge=1)
    iv: float = Field(default=60, gt=0)
    option_type: str = Field(default="PUT")


@router.post("/payoff")
async def calc_payoff(req: PayoffRequest):
    """Payoff 计算（单腿/组合/概率/时间衰减）"""
    from services.strategy_analytics import PayoffEngine
    engine = PayoffEngine()

    if req.mode == "single":
        result = engine.calc_single(
            spot=req.spot, strike=req.strike, premium=req.premium,
            option_type=req.option_type, dte=req.dte, quantity=req.quantity,
            side=req.side, pct_range=req.pct_range, steps=req.steps,
        )
        score = engine.score_strategy(
            spot=req.spot, strike=req.strike, premium=req.premium,
            option_type=req.option_type, dte=req.dte,
        )
        result["score"] = score
        return {"success": True, "mode": "single", **result}

    elif req.mode == "multi":
        if not req.legs:
            raise HTTPException(status_code=400, detail="multi 模式需要 legs 参数")
        result = engine.calc_multi_legs(spot=req.spot, legs=req.legs,
                                        pct_range=req.pct_range, steps=req.steps)
        return {"success": result.get("success", True), "mode": "multi", **result}

    elif req.mode == "probability":
        result = engine.calc_probability_overlay(spot=req.spot, dte=req.dte, iv=req.iv)
        return {"success": True, "mode": "probability", **result}

    elif req.mode == "time_decay":
        result = engine.calc_time_decay(
            spot=req.spot, strike=req.strike, premium=req.premium,
            option_type=req.option_type, iv=req.iv, dte_max=req.dte,
        )
        return {"success": True, "mode": "time_decay", **result}

    else:
        raise HTTPException(status_code=400, detail=f"不支持的 mode: {req.mode}")


@router.post("/wheel")
async def calc_wheel(req: WheelRequest):
    """Wheel 蒙特卡洛模拟"""
    from services.strategy_analytics import WheelSimulator
    sim = WheelSimulator()
    result = sim.simulate(
        spot=req.spot, strike=req.strike, premium=req.premium,
        option_type=req.option_type, cycles=req.cycles, capital=req.capital,
        assigned_pct=req.assigned_pct, iv=req.iv, simulations=req.simulations,
    )
    return result


@router.post("/estimate")
async def estimate_premium(req: EstimateRequest):
    """快速权利金估算"""
    from services.strategy_analytics import PayoffEngine
    engine = PayoffEngine()
    result = engine.estimate_premium(
        spot=req.spot, strike=req.strike, dte=req.dte,
        iv=req.iv, option_type=req.option_type,
    )
    return {"success": True, **result}
