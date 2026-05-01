"""策略计算 API"""
import logging
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from typing import Optional, List

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["strategy"])


class StrategyCalcRequest(BaseModel):
    mode: str = Field(default="roll")
    currency: str = Field(default="BTC")
    current_strike: float = Field(default=0)
    current_qty: float = Field(default=1)
    old_strike: Optional[float] = Field(default=None)  # 前端兼容
    old_qty: Optional[float] = Field(default=None)  # 前端兼容
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

    try:
        spot = await run_in_threadpool(get_spot_price, params.currency)
    except (RuntimeError, ValueError) as e:
        logger.warning("Strategy calc spot price failed: %s", e)
        spot = 0
    
    if spot <= 0:
        raise HTTPException(status_code=503, detail="无法获取现货价格，请稍后重试")
    
    mode = params.mode.lower()

    if mode == "roll":
        # 兼容前端参数名 old_strike / old_qty
        current_strike = params.old_strike if params.old_strike is not None else params.current_strike
        current_qty = params.old_qty if params.old_qty is not None else params.current_qty
        # target_strike 和 target_expiry 可选，不提供时系统会自动寻找最佳方案
        target_strike = params.target_strike or current_strike
        target_expiry = params.target_expiry or ""
        result = await calc_roll_plan(
            current_strike=current_strike,
            current_qty=current_qty,
            target_strike=target_strike,
            target_expiry=target_expiry,
            spot=spot,
            margin_ratio=params.margin_ratio,
            option_type=params.option_type
        )
    elif mode == "new":
        result = await calc_new_plan(
            currency=params.currency,
            spot=spot,
            min_dte=params.min_dte,
            max_dte=params.max_dte,
            margin_ratio=params.margin_ratio,
            option_type=params.option_type
        )
    elif mode == "grid":
        from services.unified_strategy_engine import UnifiedStrategyEngine, StrategyMode, OptionType, StrategyParams
        engine = UnifiedStrategyEngine()
        strategy_params = StrategyParams(
            currency=params.currency,
            mode=StrategyMode.GRID,
            option_type=OptionType.PUT if params.option_type == "PUT" else OptionType.CALL,
            margin_ratio=params.margin_ratio,
            min_dte=params.min_dte,
            max_dte=params.max_dte,
        )
        # 获取合约数据
        from services.exchange_abstraction import registry, ExchangeType
        from services.monitors import get_deribit_monitor
        mon = get_deribit_monitor()
        
        # 支持 BTC/ETH/SOL 等多种币种
        currency = params.currency.upper()
        summaries = mon._get_book_summaries(currency)
        
        # _get_book_summaries 返回已结构化的合约数据
        contracts = [s for s in summaries if s]
        result = engine.execute(contracts, strategy_params, spot)
    else:
        raise HTTPException(status_code=400, detail=f"不支持的模式: {mode}")

    result["spot"] = spot
    result["risk_status"] = RiskFramework.get_status(spot)
    return result


@router.post("/calculator/roll")
async def calculator_roll(params: StrategyCalcRequest):
    """滚仓计算器"""
    return await strategy_calc(params)
