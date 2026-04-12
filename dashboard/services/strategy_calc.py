# Strategy Calculator - roll and new plan calculations
from typing import Dict, Any, List
from models.contracts import StrategyCalcParams
from services.risk_framework import RiskFramework


def calc_roll_plan(contracts: List[Dict], params: StrategyCalcParams, spot: float) -> Dict[str, Any]:
    """滚仓模式计算"""
    import math
    from config import config

    MIN_NET_CREDIT_USD = config.MIN_NET_CREDIT_USD
    SLIPPAGE_PCT = config.ROLL_SLIPPAGE_PCT
    SAFETY_BUFFER_PCT = config.ROLL_SAFETY_BUFFER_PCT

    plans = []
    break_even_exceeds_cap = 0
    filtered_by_negative_nc = 0
    filtered_by_margin = 0

    for c in contracts:
        c_type = c.get('option_type', 'P').upper()
        if c_type != params.option_type.upper():
            continue
        c_strike = c.get('strike', 0)
        if c_type == 'P' and c_strike >= params.old_strike:
            continue
        if c_type == 'C' and c_strike <= params.old_strike:
            continue
        if c.get('dte', 0) < params.min_dte or c.get('dte', 0) > params.max_dte:
            continue
        if abs(c.get('delta', 1)) > params.target_max_delta:
            continue

        prem_usd = c.get('premium_usd') or c.get('premium', 0)
        if prem_usd <= 0:
            continue

        effective_prem_usd = prem_usd * (1 - SLIPPAGE_PCT)
        break_even_qty = math.ceil(params.close_cost_total / effective_prem_usd)
        min_qty_for_profit = math.ceil(params.close_cost_total / effective_prem_usd * (1 + SAFETY_BUFFER_PCT))
        max_allowed_qty = int(params.old_qty * params.max_qty_multiplier)

        if break_even_qty > max_allowed_qty:
            break_even_exceeds_cap += 1
            continue

        new_qty = max(min_qty_for_profit, break_even_qty)
        strike = c['strike']
        margin_req = new_qty * strike * params.margin_ratio if params.option_type == 'PUT' else new_qty * prem_usd * 10
        if margin_req > params.reserve_capital:
            filtered_by_margin += 1
            continue

        gross_credit = new_qty * effective_prem_usd
        net_credit = gross_credit - params.close_cost_total

        if net_credit < MIN_NET_CREDIT_USD:
            filtered_by_negative_nc += 1
            continue

        delta_val = abs(c.get('delta', 0))
        dte_val = c.get('dte', 30)
        apr_val = c.get('apr', 0)

        capital_efficiency = net_credit / margin_req if margin_req > 0 else 0
        delta_penalty = max(0, (delta_val - 0.25) * 2)
        dte_weight = min(1.0, dte_val / 45.0)
        rf_modifier = RiskFramework.get_score_modifier(strike, spot)
        risk_adjusted_score = capital_efficiency * (1 - delta_penalty) * (0.5 + 0.5 * dte_weight) * rf_modifier
        annualized_roi = (net_credit / margin_req * 365 / max(dte_val, 1)) if margin_req > 0 else 0

        plans.append({
            "symbol": c.get('symbol', 'N/A'),
            "platform": c.get('platform', 'N/A'),
            "strike": strike, "dte": dte_val, "delta": delta_val, "apr": apr_val,
            "premium_usd": prem_usd, "effective_prem_usd": round(effective_prem_usd, 2),
            "new_qty": new_qty, "break_even_qty": break_even_qty,
            "margin_req": round(margin_req, 2), "gross_credit": round(gross_credit, 2),
            "net_credit": round(net_credit, 2), "roi_pct": round(annualized_roi, 1),
            "score": round(risk_adjusted_score, 4), "capital_efficiency": round(capital_efficiency, 4)
        })

    plans.sort(key=lambda x: (x['score'], x['net_credit'], -x['delta']), reverse=True)

    return {
        "success": True, "mode": "roll",
        "params": params.model_dump(),
        "plans": plans[:15],
        "meta": {
            "total_contracts_scanned": len(contracts), "plans_found": len(plans),
            "filtered": {
                "break_even_exceeded_cap": break_even_exceeds_cap,
                "negative_net_credit": filtered_by_negative_nc,
                "insufficient_margin": filtered_by_margin
            }
        }
    }


def calc_new_plan(contracts: List[Dict], params: StrategyCalcParams, spot: float) -> Dict[str, Any]:
    """新建模式计算"""
    plans = []
    for c in contracts:
        c_type = c.get('option_type', 'P').upper()
        if c_type != params.option_type.upper():
            continue
        c_strike = c.get('strike', 0)
        if c.get('dte', 0) < params.min_dte or c.get('dte', 0) > params.max_dte:
            continue
        if abs(c.get('delta', 0)) > params.target_max_delta:
            continue

        prem_usd = c.get('premium_usd') or c.get('premium', 0)
        if prem_usd <= 0:
            continue

        strike = c['strike']
        margin_req = strike * params.margin_ratio if params.option_type == 'PUT' else prem_usd * 10
        if margin_req > params.reserve_capital:
            continue

        gross_credit = prem_usd
        apr_val = c.get('apr', 0)
        dte_val = c.get('dte', 30)
        annualized_roi = (gross_credit / margin_req * 365 / max(dte_val, 1)) if margin_req > 0 else 0
        delta_val = abs(c.get('delta', 0))
        capital_efficiency = gross_credit / margin_req if margin_req > 0 else 0
        rf_modifier = RiskFramework.get_score_modifier(strike, spot)
        risk_adjusted_score = capital_efficiency * rf_modifier

        plans.append({
            "symbol": c.get('symbol', 'N/A'), "platform": c.get('platform', 'N/A'),
            "strike": strike, "dte": dte_val, "delta": delta_val, "apr": apr_val,
            "premium_usd": prem_usd, "margin_req": round(margin_req, 2),
            "gross_credit": round(gross_credit, 2), "roi_pct": round(annualized_roi, 1),
            "score": round(risk_adjusted_score, 4), "capital_efficiency": round(capital_efficiency, 4)
        })

    plans.sort(key=lambda x: (x['score'], x['roi_pct']), reverse=True)

    return {
        "success": True, "mode": "new",
        "params": params.model_dump(),
        "plans": plans[:15],
        "meta": {"total_contracts_scanned": len(contracts), "plans_found": len(plans)}
    }
