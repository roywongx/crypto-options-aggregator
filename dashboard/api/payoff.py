"""Payoff 计算 API"""
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["payoff"])


@router.post("/payoff/calc")
async def calc_payoff(data: dict):
    """计算策略Payoff图"""
    from services.payoff_calculator import PayoffCalculator

    calc = PayoffCalculator()
    legs = data.get("legs", [])
    spot = data.get("spot", 0)
    pct_range = data.get("pct_range", 0.3)
    steps = data.get("steps", 100)

    if not legs or not spot:
        return {"error": "缺少legs或spot参数"}

    return calc.calc_payoff(legs, spot, pct_range, steps)


@router.post("/payoff/score")
async def calc_strategy_score(data: dict):
    """策略评分和实操建议"""
    from services.payoff_calculator import PayoffCalculator

    calc = PayoffCalculator()
    legs = data.get("legs", [])
    spot = data.get("spot", 0)
    dte = data.get("dte", 30)
    iv = data.get("iv", 50)

    if not legs or not spot:
        return {"error": "缺少 legs 或 spot 参数"}

    score_data = calc.calc_strategy_score(legs, spot, dte, iv)
    advice_data = calc.generate_strategy_advice(score_data, legs, spot)

    return {
        "score": score_data,
        "advice": advice_data
    }


@router.post("/payoff/estimate")
async def estimate_premium(data: dict):
    """智能估算权利金"""
    from services.payoff_calculator import PayoffCalculator

    calc = PayoffCalculator()
    option_type = data.get("option_type", "P")
    strike = data.get("strike", 0)
    spot = data.get("spot", 0)
    dte = data.get("dte", 30)
    iv = data.get("iv", 50)

    if not strike or not spot:
        return {"error": "缺少 strike 或 spot 参数"}

    return calc.estimate_premium(option_type, strike, spot, dte, iv)


@router.post("/payoff/compare")
async def compare_strategies(data: dict):
    """对比多个策略（最多 5 个）"""
    from services.payoff_calculator import PayoffCalculator

    calc = PayoffCalculator()
    strategies = data.get("strategies", [])
    spot = data.get("spot", 0)

    if not strategies or not spot:
        return {"error": "缺少 strategies 或 spot 参数"}

    return calc.compare_strategies(strategies, spot)


@router.post("/payoff/wheel")
async def calc_wheel_roi(data: dict):
    """计算 Wheel 策略 ROI（增强版）"""
    from services.payoff_calculator import PayoffCalculator

    calc = PayoffCalculator()
    put_strike = data.get("put_strike", 0)
    put_premium = data.get("put_premium", 0)
    call_strike = data.get("call_strike", 0)
    call_premium = data.get("call_premium", 0)
    spot = data.get("spot", 0)
    quantity = data.get("quantity", 1)
    put_dte = data.get("put_dte", 30)
    call_dte = data.get("call_dte", 30)

    if not put_strike or not spot:
        return {"error": "缺少 put_strike 或 spot 参数"}

    return calc.calc_wheel_roi(put_strike, put_premium, call_strike, call_premium, spot, quantity, put_dte, call_dte)
