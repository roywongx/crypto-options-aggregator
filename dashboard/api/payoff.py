"""Payoff 计算 API

修复 H-4: 所有端点使用 Pydantic 输入验证
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["payoff"])


class PayoffCalcRequest(BaseModel):
    legs: list
    spot: float = Field(gt=0)
    pct_range: float = Field(default=0.3, ge=0.1, le=1.0)
    steps: int = Field(default=100, ge=1, le=1000)


class PayoffScoreRequest(BaseModel):
    legs: list
    spot: float = Field(gt=0)
    dte: int = Field(default=30, ge=1)
    iv: float = Field(default=50, gt=0)


class PayoffEstimateRequest(BaseModel):
    option_type: str = "P"
    strike: float = Field(gt=0)
    spot: float = Field(gt=0)
    dte: int = Field(default=30, ge=1)
    iv: float = Field(default=50, gt=0)


class PayoffCompareRequest(BaseModel):
    strategies: list
    spot: float = Field(gt=0)


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
    """计算策略Payoff图"""
    from services.payoff_calculator import PayoffCalculator

    calc = PayoffCalculator()

    if not data.legs or not data.spot:
        raise HTTPException(status_code=400, detail="缺少legs或spot参数")

    return calc.calc_payoff(data.legs, data.spot, data.pct_range, data.steps)


@router.post("/payoff/score")
async def calc_strategy_score(data: PayoffScoreRequest):
    """策略评分和实操建议"""
    from services.payoff_calculator import PayoffCalculator

    calc = PayoffCalculator()
    score_data = calc.calc_strategy_score(data.legs, data.spot, data.dte, data.iv)
    advice_data = calc.generate_strategy_advice(score_data, data.legs, data.spot)

    return {
        "score": score_data,
        "advice": advice_data
    }


@router.post("/payoff/estimate")
async def estimate_premium(data: PayoffEstimateRequest):
    """智能估算权利金"""
    from services.payoff_calculator import PayoffCalculator

    calc = PayoffCalculator()
    return calc.estimate_premium(data.option_type, data.strike, data.spot, data.dte, data.iv)


@router.post("/payoff/compare")
async def compare_strategies(data: PayoffCompareRequest):
    """对比多个策略（最多 5 个）"""
    from services.payoff_calculator import PayoffCalculator

    calc = PayoffCalculator()
    return calc.compare_strategies(data.strategies, data.spot)


@router.post("/payoff/wheel")
async def calc_wheel_roi(data: PayoffWheelRequest):
    """计算 Wheel 策略 ROI（增强版）"""
    from services.payoff_calculator import PayoffCalculator

    calc = PayoffCalculator()
    return calc.calc_wheel_roi(
        data.put_strike, data.put_premium, data.call_strike, data.call_premium,
        data.spot, data.quantity, data.put_dte, data.call_dte
    )
