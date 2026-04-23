# Strategy Calculator - roll and new plan calculations
from typing import Dict, Any, List
from models.contracts import StrategyCalcParams
from services.risk_framework import RiskFramework


def calc_roll_plan(current_strike: float, current_qty: float, target_strike: float, target_expiry: str, spot: float, margin_ratio: float) -> Dict[str, Any]:
    """滚仓模式计算"""
    import math
    from config import config
    from services.exchange_abstraction import registry, ExchangeType

    MIN_NET_CREDIT_USD = config.MIN_NET_CREDIT_USD
    SLIPPAGE_PCT = config.ROLL_SLIPPAGE_PCT
    SAFETY_BUFFER_PCT = config.ROLL_SAFETY_BUFFER_PCT

    # 获取合约数据
    contracts = []
    try:
        import asyncio
        from services.exchange_abstraction import registry, ExchangeType
        exchange = registry.get(ExchangeType.DERIBIT)
        chain = asyncio.run(exchange.get_options_chain('BTC', ExchangeType.CALL))
        contracts = [c.to_dict() for c in chain]
    except Exception as e:
        print(f"Error fetching options chain: {e}")

    plans = []
    break_even_exceeds_cap = 0
    filtered_by_negative_nc = 0
    filtered_by_margin = 0

    for c in contracts:
        c_type = c.get('option_type', 'P').upper()
        c_strike = c.get('strike', 0)
        if c_type == 'P' and c_strike >= current_strike:
            continue
        if c_type == 'C' and c_strike <= current_strike:
            continue
        if c.get('dte', 0) < 7 or c.get('dte', 0) > 45:
            continue

        prem_usd = c.get('premium_usd') or c.get('premium', 0)
        if prem_usd <= 0:
            continue

        effective_prem_usd = prem_usd * (1 - SLIPPAGE_PCT)
        break_even_qty = math.ceil(current_qty * 1000 / effective_prem_usd)  # 简化计算
        min_qty_for_profit = math.ceil(current_qty * 1000 / effective_prem_usd * (1 + SAFETY_BUFFER_PCT))
        max_allowed_qty = int(current_qty * 2)  # 简化计算

        if break_even_qty > max_allowed_qty:
            break_even_exceeds_cap += 1
            continue

        new_qty = max(min_qty_for_profit, break_even_qty)
        strike = c['strike']
        margin_req = new_qty * strike * margin_ratio if c_type == 'PUT' else new_qty * prem_usd * 10

        gross_credit = new_qty * effective_prem_usd
        net_credit = gross_credit - current_qty * 1000  # 简化计算

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
        "params": {
            "current_strike": current_strike,
            "current_qty": current_qty,
            "target_strike": target_strike,
            "target_expiry": target_expiry,
            "margin_ratio": margin_ratio
        },
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


def calc_new_plan(currency: str, spot: float, min_dte: int, max_dte: int, margin_ratio: float, option_type: str) -> Dict[str, Any]:
    """新建模式计算"""
    # 获取合约数据
    contracts = []
    try:
        import asyncio
        from services.exchange_abstraction import registry, ExchangeType
        from services.exchange_abstraction import OptionType as ExchangeOptionType
        exchange = registry.get(ExchangeType.DERIBIT)
        option_type_enum = ExchangeOptionType.CALL if option_type.upper() == 'CALL' else ExchangeOptionType.PUT
        chain = asyncio.run(exchange.get_options_chain(currency, option_type_enum))
        contracts = [c.to_dict() for c in chain]
    except Exception as e:
        print(f"Error fetching options chain: {e}")

    plans = []
    for c in contracts:
        c_type = c.get('option_type', 'P').upper()
        if c_type != option_type.upper():
            continue
        c_strike = c.get('strike', 0)
        if c.get('dte', 0) < min_dte or c.get('dte', 0) > max_dte:
            continue

        prem_usd = c.get('premium_usd') or c.get('premium', 0)
        if prem_usd <= 0:
            continue

        strike = c['strike']
        margin_req = strike * margin_ratio if option_type == 'PUT' else prem_usd * 10

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
        "params": {
            "currency": currency,
            "min_dte": min_dte,
            "max_dte": max_dte,
            "margin_ratio": margin_ratio,
            "option_type": option_type
        },
        "plans": plans[:15],
        "meta": {"total_contracts_scanned": len(contracts), "plans_found": len(plans)}
    }
