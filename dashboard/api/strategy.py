"""策略计算 API"""
import logging
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from typing import Optional, List
from models.contracts import StrategyRecommendRequest

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
    put_count: Optional[int] = Field(default=None)
    call_count: Optional[int] = Field(default=None)
    min_apr: Optional[float] = Field(default=None)


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
            option_type=OptionType.PUT if params.option_type.upper() == "PUT" else OptionType.CALL,
            margin_ratio=params.margin_ratio,
            min_dte=params.min_dte,
            max_dte=params.max_dte,
            put_count=params.put_count or 5,
            call_count=params.call_count or 0,
            min_apr=params.min_apr or 8.0,
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


@router.post("/strategy/recommend")
async def strategy_recommend(params: StrategyRecommendRequest):
    """统一策略推荐 - 基于最新扫描数据的策略建议"""
    from services.unified_strategy_engine import UnifiedStrategyEngine, StrategyMode, OptionType, StrategyParams
    from services.spot_price import get_spot_price
    from services.risk_framework import RiskFramework
    from services.dvol_analyzer import get_dvol_from_deribit
    from db.connection import execute_read

    try:
        spot = await run_in_threadpool(get_spot_price, params.currency)
    except (RuntimeError, ValueError) as e:
        logger.warning("Strategy recommend spot price failed: %s", e)
        spot = 0

    if spot <= 0:
        raise HTTPException(status_code=503, detail="无法获取现货价格，请稍后重试")

    # 从最新扫描记录获取合约数据
    rows = execute_read(
        "SELECT contracts_data, top_contracts_data, dvol_current, dvol_z_score, dvol_signal "
        "FROM scan_records WHERE currency=? AND contracts_data IS NOT NULL "
        "ORDER BY timestamp DESC LIMIT 1",
        (params.currency,)
    )
    if not rows:
        raise HTTPException(status_code=503, detail="暂无扫描数据，请等待后台扫描完成")

    import json
    contracts_json = rows[0][0] or "[]"
    dvol_current = rows[0][2] or 50
    dvol_z = rows[0][3] or 0
    dvol_signal = rows[0][4] or "normal"

    try:
        all_contracts = json.loads(contracts_json) if isinstance(contracts_json, str) else contracts_json
    except (json.JSONDecodeError, TypeError):
        all_contracts = []

    total_contracts = len(all_contracts)

    # DVOL 自适应参数
    from services.dvol_analyzer import adapt_params_by_dvol
    base_params = {
        "max_delta": params.overrides.get("max_delta", 0.30) if params.overrides else 0.30,
        "min_dte": params.overrides.get("min_dte", 7) if params.overrides else 7,
        "max_dte": params.overrides.get("max_dte", 90) if params.overrides else 90,
        "min_apr": params.overrides.get("min_apr", 10.0) if params.overrides else 10.0,
        "margin_ratio": params.overrides.get("margin_ratio", 0.20) if params.overrides else 0.20,
    }
    dvol_data = {"current": dvol_current, "z_score": dvol_z, "signal": dvol_signal}
    adapted = adapt_params_by_dvol(base_params, dvol_data)

    # 硬性过滤
    hard_filtered = [
        c for c in all_contracts
        if c.get("iv", 0) > 0 and c.get("open_interest", 0) >= 10
    ]

    # DVOL 自适应过滤
    dvol_filtered = [
        c for c in hard_filtered
        if adapted.get("min_dte", 7) <= c.get("dte", 0) <= adapted.get("max_dte", 90)
        and abs(c.get("delta", 1)) <= adapted.get("max_delta", 0.30)
    ]

    engine = UnifiedStrategyEngine()
    mode_map = {"new": StrategyMode.NEW, "roll": StrategyMode.ROLL, "grid": StrategyMode.GRID, "wheel": StrategyMode.NEW}
    strategy_mode = mode_map.get(params.mode, StrategyMode.NEW)
    opt_type = OptionType.PUT if params.option_type == "PUT" else OptionType.CALL

    is_grid = params.mode == "grid"
    if is_grid:
        _pc = params.grid_levels if params.option_type == "PUT" else 0
        _cc = params.grid_levels if params.option_type == "CALL" else 0
    else:
        _pc, _cc = 5, 0
    strategy_params = StrategyParams(
        currency=params.currency,
        mode=strategy_mode,
        option_type=opt_type,
        reserve_capital=params.capital,
        target_max_delta=adapted.get("max_delta", 0.30),
        min_dte=adapted.get("min_dte", 7),
        max_dte=adapted.get("max_dte", 90),
        margin_ratio=adapted.get("margin_ratio", 0.20),
        target_apr=200.0,
        old_strike=params.old_strike,
        put_count=_pc,
        call_count=_cc,
    )

    result = engine.execute(dvol_filtered, strategy_params, spot)

    recommendations = []
    if params.mode == "grid":
        raw_recs = result.get("put_levels", []) + result.get("call_levels", [])
    else:
        raw_recs = result.get("plans", [])

    for p in raw_recs:
        metrics = p.get("metrics", {}) if isinstance(p, dict) else {}
        if hasattr(p, 'metrics'):
            metrics = p.metrics
            p = engine._rec_to_dict(p)

        scores = {
            "apr": round(metrics.get("apr", 0) / 100, 4) if isinstance(metrics, dict) else round(getattr(metrics, "apr", 0) / 100, 4),
            "pop": round(metrics.get("win_rate", 50) / 100, 4) if isinstance(metrics, dict) else round(getattr(metrics, "win_rate", 50) / 100, 4),
            "breakeven": round(metrics.get("distance_pct", 0) / 20, 4) if isinstance(metrics, dict) else round(getattr(metrics, "distance_pct", 0) / 20, 4),
            "liquidity": round(metrics.get("liquidity_score", 50) / 100, 4) if isinstance(metrics, dict) else round(getattr(metrics, "liquidity_score", 50) / 100, 4),
            "iv_rank": 0.5,
            "total": round(p.get("score", 0), 3),
            "recommendation": (metrics.get("recommendation_level", "OK") if isinstance(metrics, dict) else getattr(metrics, "recommendation_level", "OK")),
        }
        recommendations.append({
            "platform": p.get("platform", "Deribit"),
            "symbol": p.get("symbol", ""),
            "option_type": p.get("option_type", params.option_type),
            "strike": p.get("strike", 0),
            "expiry": p.get("expiry", ""),
            "dte": p.get("dte", 0),
            "delta": metrics.get("delta", 0) if isinstance(metrics, dict) else getattr(metrics, "delta", 0),
            "gamma": metrics.get("gamma", 0) if isinstance(metrics, dict) else getattr(metrics, "gamma", 0),
            "theta": metrics.get("theta", 0) if isinstance(metrics, dict) else getattr(metrics, "theta", 0),
            "vega": metrics.get("vega", 0) if isinstance(metrics, dict) else getattr(metrics, "vega", 0),
            "premium_usd": p.get("premium_usd", 0),
            "apr": metrics.get("apr", 0) if isinstance(metrics, dict) else getattr(metrics, "apr", 0),
            "open_interest": p.get("open_interest", 0),
            "spread_pct": 0.1,
            "iv": p.get("iv", 0),
            "volume": p.get("volume", 0),
            "scores": scores,
            "metrics": metrics if isinstance(metrics, dict) else {},
            "risk_assessment": {},
        })

    return {
        "success": True,
        "mode": params.mode,
        "currency": params.currency,
        "spot_price": spot,
        "recommendations": recommendations[:params.max_results],
        "grid_extra": {"put_levels": result.get("put_levels", []), "call_levels": result.get("call_levels", []), "vol_signal": result.get("vol_signal", {})} if params.mode == "grid" else None,
        "filter_summary": {
            "total_contracts": total_contracts,
            "after_hard_filter": len(hard_filtered),
            "after_dvol_filter": len(dvol_filtered),
            "after_strategy_filter": len(recommendations),
            "dvol_adjustments": adapted,
            "message": f"共 {total_contracts} 个合约，筛选后 {len(recommendations)} 个推荐" if recommendations else "当前条件下无可用合约",
        },
        "dvol_snapshot": {
            "current": dvol_current,
            "z_score": dvol_z,
            "signal": dvol_signal,
        },
    }


@router.post("/calculator/roll")
async def calculator_roll(params: StrategyCalcRequest):
    """滚仓计算器"""
    return await strategy_calc(params)
