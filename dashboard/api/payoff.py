"""Payoff 计算 API (兼容层 — 委托给 analytics engine)"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["payoff"])


class PayoffCalcRequest(BaseModel):
    legs: list
    spot: float = Field(gt=0)
    pct_range: float = Field(default=0.3, ge=0.1, le=1.0)
    steps: int = Field(default=100, ge=1, le=1000)


class PayoffEstimateRequest(BaseModel):
    option_type: str = "P"
    strike: float = Field(gt=0)
    spot: float = Field(gt=0)
    dte: int = Field(default=30, ge=1)
    iv: float = Field(default=50, gt=0)


class PayoffWheelRequest(BaseModel):
    put_strike: float = Field(gt=0)
    put_premium: float = Field(ge=0)
    call_strike: float = Field(gt=0)
    call_premium: float = Field(ge=0)
    spot: float = Field(gt=0)
    quantity: int = Field(default=1, ge=1)
    put_dte: int = Field(default=30, ge=1)
    call_dte: int = Field(default=30, ge=1)


@router.post("/payoff/calc")
async def calc_payoff(data: PayoffCalcRequest):
    """计算策略Payoff图 (兼容层)"""
    from services.strategy_analytics import PayoffEngine
    engine = PayoffEngine()
    if not data.legs:
        raise HTTPException(status_code=400, detail="缺少 legs 参数")
    leg = data.legs[0] if data.legs else {}
    result = engine.calc_single(
        spot=data.spot,
        strike=leg.get("strike", data.spot),
        premium=leg.get("premium", 0),
        option_type=leg.get("option_type", "P"),
        dte=leg.get("dte", 30),
        quantity=leg.get("quantity", 1),
        side=leg.get("direction", "sell"),
        pct_range=data.pct_range,
        steps=data.steps,
    )
    return {
        "prices": result["payoff_curve"]["prices"],
        "total_pnl": result["payoff_curve"]["pnl"],
        "legs": [{"pnl": result["payoff_curve"]["pnl"], **leg}],
        "breakevens": [result["breakeven"]] if result["breakeven"] else [],
        "max_profit": result["max_profit"],
        "max_loss": result["max_loss"],
        "spot": data.spot,
    }


@router.post("/payoff/estimate")
async def estimate_premium(data: PayoffEstimateRequest):
    """智能估算权利金 (兼容层)"""
    from services.strategy_analytics import PayoffEngine
    engine = PayoffEngine()
    result = engine.estimate_premium(
        spot=data.spot, strike=data.strike, dte=data.dte,
        iv=data.iv, option_type=data.option_type,
    )
    return {"estimated_premium": result["premium"], **result}


@router.post("/payoff/wheel")
async def calc_wheel_roi(data: PayoffWheelRequest):
    """计算 Wheel ROI (兼容层)"""
    from services.strategy_analytics import WheelSimulator
    sim = WheelSimulator()
    return sim.simulate(
        spot=data.spot, strike=data.put_strike, premium=data.put_premium,
        option_type="PUT", cycles=3, capital=data.put_strike * data.quantity,
        simulations=500,
    )


@router.post("/payoff/score")
async def calc_strategy_score():
    raise HTTPException(status_code=410, detail="已迁移至 /api/analytics/payoff?mode=single")


@router.post("/payoff/compare")
async def compare_strategies():
    raise HTTPException(status_code=410, detail="已迁移至 /api/analytics/payoff?mode=multi")
