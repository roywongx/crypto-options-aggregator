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


# ============================================================
# 超参数优化 API (Freqtrade Hyperopt inspired)
# ============================================================

class OptimizeRequest(BaseModel):
    currency: str = Field(default="BTC")
    option_type: str = Field(default="PUT")
    mode: str = Field(default="bayesian")  # bayesian | full | quick
    objective: str = Field(default="sortino_loss")  # sortino_loss | calmar_loss | sharpe_loss | weighted_score
    n_calls: int = Field(default=50)  # Bayesian calls (30-100 recommended)


@router.post("/strategy/optimize")
async def optimize_params(body: OptimizeRequest):
    """网格搜索最优策略参数"""
    from services.param_optimizer import ParamOptimizer
    from services.spot_price import get_spot_price
    from services.trades import fetch_deribit_summaries
    from services.instrument import _parse_inst_name
    from services.dvol_analyzer import calc_delta_bs
    from services.shared_calculations import black_scholes_price
    from fastapi.concurrency import run_in_threadpool

    try:
        spot = await run_in_threadpool(get_spot_price, body.currency)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"现货价格获取失败: {e}")

    if spot <= 0:
        raise HTTPException(status_code=503, detail="无法获取现货价格")

    # 获取全量合约
    try:
        raw_summaries = await run_in_threadpool(fetch_deribit_summaries, body.currency)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"合约数据获取失败: {e}")

    if not raw_summaries:
        raise HTTPException(status_code=503, detail="无合约数据")

    contracts = []
    for s in raw_summaries:
        meta = _parse_inst_name(s.get("instrument_name", ""))
        if not meta or meta.dte < 1:
            continue
        iv = float(s.get("mark_iv") or 0)
        oi = float(s.get("open_interest") or 0)
        if iv <= 0 or oi < 1:
            continue
        strike = meta.strike
        underlying = float(s.get("underlying_price", spot)) or spot
        raw_delta = s.get("delta")
        delta_val = abs(float(raw_delta)) if raw_delta and float(raw_delta or 0) != 0 else abs(calc_delta_bs(strike, underlying, iv, meta.dte, meta.option_type))
        prem = float(s.get("mark_price") or 0)
        prem_usd = prem * underlying
        cv = strike * 0.2
        apr = (prem_usd / cv) * (365 / meta.dte) * 100 if cv > 0 else 0
        bs_greeks = black_scholes_price(meta.option_type, strike, underlying, meta.dte, iv)
        contracts.append({
            "symbol": s.get("instrument_name", ""), "platform": "Deribit",
            "expiry": meta.expiry, "dte": meta.dte, "option_type": meta.option_type,
            "strike": strike, "apr": round(apr, 1), "premium_usd": round(prem_usd, 2),
            "premium": round(prem, 2), "delta": round(delta_val, 3),
            "theta": round(bs_greeks["theta"], 4), "gamma": round(bs_greeks["gamma"], 6),
            "vega": round(bs_greeks["vega"], 4), "iv": round(iv, 1),
            "open_interest": round(oi, 0),
        })

    optimizer = ParamOptimizer()
    if body.mode == "bayesian":
        result = await run_in_threadpool(
            optimizer.bayesian_search, contracts, spot, body.option_type,
            objective=body.objective, n_calls=body.n_calls
        )
    elif body.mode == "full":
        result = await run_in_threadpool(
            optimizer.grid_search, contracts, spot, body.option_type,
            objective=body.objective
        )
    else:
        result = await run_in_threadpool(optimizer.quick_search, contracts, spot, body.option_type)

    return {
        "success": result.success,
        "method": result.method,
        "best_params": result.best_params,
        "best_score": result.best_score,
        "loss_value": result.loss_value,
        "sharpe": result.sharpe,
        "sortino": result.sortino,
        "calmar": result.calmar,
        "max_drawdown_pct": result.max_drawdown_pct,
        "top_n": result.top_n[:10],
        "total_combos_tested": result.total_combos_tested,
        "elapsed_seconds": result.elapsed_seconds,
        "objective": result.objective,
        "note": result.note,
    }


class BacktestRequest(BaseModel):
    currency: str = Field(default="BTC")
    days: int = Field(default=365)
    initial_capital: float = Field(default=100000.0)
    exchange: str = Field(default="binance")


@router.post("/strategy/backtest")
async def run_backtest(body: BacktestRequest):
    """运行策略回测"""
    from services.backtest_engine import BacktestEngine
    from services.exchange_abstraction import registry, ExchangeType
    from fastapi.concurrency import run_in_threadpool

    ex_map = {"binance": ExchangeType.BINANCE, "deribit": ExchangeType.DERIBIT,
              "bybit": ExchangeType.BYBIT, "okx": ExchangeType.OKX}
    ex_type = ex_map.get(body.exchange.lower(), ExchangeType.BINANCE)

    try:
        exchange = registry.get(ex_type)
        klines = await exchange.get_historical_klines(body.currency, limit=body.days)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"K线数据获取失败: {e}")

    if not klines or len(klines) < 30:
        raise HTTPException(status_code=503, detail=f"历史数据不足 (仅 {len(klines) if klines else 0} 天)")

    engine = BacktestEngine(initial_capital=body.initial_capital)
    result = await run_in_threadpool(engine.run, klines)

    if not result.success:
        raise HTTPException(status_code=500, detail="回测失败，无有效交易")

    return {
        "success": True,
        "total_trades": result.total_trades,
        "winning_trades": result.winning_trades,
        "losing_trades": result.losing_trades,
        "win_rate": result.win_rate,
        "total_pnl_usd": result.total_pnl_usd,
        "total_return_pct": result.total_return_pct,
        "avg_return_per_trade": result.avg_return_per_trade,
        "max_drawdown_pct": result.max_drawdown_pct,
        "sharpe_ratio": result.sharpe_ratio,
        "sortino_ratio": result.sortino_ratio,
        "profit_factor": result.profit_factor,
        "avg_dte": result.avg_dte,
        "params": result.params,
        "start_date": result.start_date,
        "end_date": result.end_date,
        "equity_curve": result.equity_curve[-20:],  # last 20 days for chart
        "trade_summary": [
            {"date": t.entry_date, "pnl": t.pnl_usd, "assigned": t.assigned, "dte": t.dte}
            for t in result.trades[-30:]
        ],
    }


class ProtectionsCheckRequest(BaseModel):
    currency: str = Field(default="BTC")
    current_equity: float = Field(default=100000.0)
    peak_equity: float = Field(default=100000.0)


@router.post("/strategy/protections-check")
async def check_protections(body: ProtectionsCheckRequest):
    """运行所有保护守卫检查"""
    from services.protections import ProtectionManager
    from services.spot_price import get_spot_price
    from services.dvol_analyzer import get_dvol_from_deribit
    from services.trades import fetch_deribit_summaries
    from services.instrument import _parse_inst_name
    from fastapi.concurrency import run_in_threadpool

    try:
        spot = await run_in_threadpool(get_spot_price, body.currency)
        dvol_data = await run_in_threadpool(get_dvol_from_deribit, body.currency)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"数据获取失败: {e}")

    dvol = dvol_data.get("current_dvol", 50) if dvol_data else 50
    iv = dvol

    # Get positions (simplified from top contracts)
    positions = []
    try:
        summaries = await run_in_threadpool(fetch_deribit_summaries, body.currency)
        if summaries:
            for s in summaries[:30]:
                meta = _parse_inst_name(s.get("instrument_name", ""))
                if not meta:
                    continue
                positions.append({
                    "strike": meta.strike,
                    "option_type": meta.option_type,
                    "premium_usd": float(s.get("mark_price", 0)) * spot,
                    "delta": abs(float(s.get("delta", 0))),
                    "oi": float(s.get("open_interest", 0)),
                    "qty": 1,
                })
    except Exception:
        pass

    manager = ProtectionManager()
    results = manager.check_all(
        positions=positions,
        spot=spot,
        dvol=dvol,
        iv=iv,
        current_equity=body.current_equity,
        peak_equity=body.peak_equity,
    )

    summary = ProtectionManager.summarize(results)

    return {
        "all_clear": summary["all_clear"],
        "summary": summary,
        "details": {
            name: {
                "tripped": r.tripped,
                "reason": r.reason,
                "suggested_action": r.suggested_action,
                "severity": r.severity,
            }
            for name, r in results.items()
        },
        "market_snapshot": {
            "spot": round(spot, 0),
            "dvol": round(dvol, 1),
            "positions_tracked": len(positions),
        },
    }


class PortfolioRiskRequest(BaseModel):
    currency: str = Field(default="BTC")


@router.post("/portfolio-risk")
async def portfolio_risk(body: PortfolioRiskRequest):
    """计算投资组合风险: VaR, CVaR, 集中度, 回撤, 动态止损"""
    from services.portfolio_risk import PortfolioRisk
    from services.spot_price import get_spot_price
    from services.dvol_analyzer import get_dvol_from_deribit
    from services.trades import fetch_deribit_summaries
    from services.instrument import _parse_inst_name
    from fastapi.concurrency import run_in_threadpool

    try:
        spot = await run_in_threadpool(get_spot_price, body.currency)
        dvol_data = await run_in_threadpool(get_dvol_from_deribit, body.currency)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"数据获取失败: {e}")

    dvol = dvol_data.get("current_dvol", 50) if dvol_data else 50
    iv = dvol

    positions = []
    try:
        summaries = await run_in_threadpool(fetch_deribit_summaries, body.currency)
        if summaries:
            for s in summaries[:30]:
                meta = _parse_inst_name(s.get("instrument_name", ""))
                if not meta or meta.dte < 1:
                    continue
                delta_val = abs(float(s.get("delta", 0)) or 0.3)
                premium_unit = float(s.get("mark_price", 0)) or 0
                positions.append({
                    "strike": meta.strike,
                    "option_type": meta.option_type,
                    "premium_usd": round(premium_unit * spot, 2),
                    "delta": delta_val,
                    "qty": 1,
                    "margin_required": meta.strike * 0.2,
                })
    except Exception:
        pass

    risk = PortfolioRisk.calc_var(positions, spot, iv, confidence="95")

    # Estimate equity from total margin (proxy when real portfolio unavailable)
    estimated_equity = risk.total_margin_used * 2.5 if risk.total_margin_used > 0 else spot * 5
    peak_multiplier = 1.15  # assume peak was 15% higher
    peak_est = estimated_equity * peak_multiplier
    drawdown_tripped, drawdown_val, drawdown_reason = PortfolioRisk.check_drawdown(
        estimated_equity, peak_est
    )
    stop_loss = PortfolioRisk.calc_dynamic_stop_loss(spot, dvol, drawdown_val)

    return {
        "success": True,
        "var_95": risk.var_95,
        "cvar_95": risk.cvar_95,
        "var_95_pct": risk.var_95_pct,
        "cvar_95_pct": risk.cvar_95_pct,
        "concentration_risk": risk.concentration_risk,
        "max_strike_band_ratio": risk.max_strike_band_ratio,
        "drawdown_from_peak": round(drawdown_val, 4),
        "circuit_breaker_tripped": drawdown_tripped,
        "circuit_breaker_reason": drawdown_reason,
        "stop_loss_price": stop_loss,
        "total_margin_used": risk.total_margin_used,
        "total_premium": risk.total_premium,
        "position_count": risk.position_count,
        "risk_level": risk.risk_level,
        "market_snapshot": {"spot": round(spot, 0), "dvol": round(dvol, 1)},
    }
