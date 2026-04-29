# Strategy Calculator - roll and new plan calculations
from typing import Dict, Any, List
import logging
import asyncio

logger = logging.getLogger(__name__)


async def calc_roll_plan(current_strike: float, current_qty: float, target_strike: float, target_expiry: str, spot: float, margin_ratio: float, option_type: str = "PUT") -> Dict[str, Any]:
    """滚仓模式计算
    
    Args:
        current_strike: 当前持仓行权价
        current_qty: 当前持仓数量 (合约数)
        target_strike: 目标行权价
        target_expiry: 目标到期日
        spot: 现货价格
        margin_ratio: 保证金比例
        option_type: 期权类型 PUT/CALL
    """
    import math
    from config import config
    from services.risk_framework import RiskFramework

    MIN_NET_CREDIT_USD = config.MIN_NET_CREDIT_USD
    SLIPPAGE_PCT = config.ROLL_SLIPPAGE_PCT
    SAFETY_BUFFER_PCT = config.ROLL_SAFETY_BUFFER_PCT

    # 获取合约数据 - 根据当前持仓类型获取对应期权链
    contracts = []
    try:
        from services.exchange_abstraction import registry, ExchangeType, OptionType
        exchange = registry.get(ExchangeType.DERIBIT)
        option_type_enum = OptionType.CALL if option_type.upper() == 'CALL' else OptionType.PUT
        chain = await exchange.get_options_chain('BTC', option_type_enum)
        contracts = [c.to_dict() for c in chain]
    except Exception as e:
        logger.warning("Error fetching options chain: %s", e)

    plans = []
    break_even_exceeds_cap = 0
    filtered_by_negative_nc = 0
    filtered_by_margin = 0

    for c in contracts:
        c_type = c.get('option_type', 'P').upper()
        c_strike = c.get('strike', 0)
        
        # 过滤：只考虑同类型期权
        if c_type != option_type.upper():
            continue
            
        # 过滤：Roll 到更有利的行权价
        # Put Roll: 当前卖出 Put，Roll 到更低行权价（收取更多权利金）
        # Call Roll: 当前卖出 Call，Roll 到更高行权价
        if c_type == 'PUT' and c_strike >= current_strike:
            continue
        if c_type == 'CALL' and c_strike <= current_strike:
            continue
            
        if c.get('dte', 0) < 7 or c.get('dte', 0) > 45:
            continue

        # Deribit 的 mark_price 是 BTC 计价，需要转换为 USD
        prem_btc = c.get('mark_price', 0)
        prem_usd = prem_btc * spot  # 转换为 USD
        if prem_usd <= 0:
            continue

        effective_prem_usd = prem_usd * (1 - SLIPPAGE_PCT)
        
        # 计算需要卖出多少张新合约才能覆盖当前持仓的成本
        # current_qty 是合约张数，每张合约的价值 = premium_usd
        # 为保持风险中性，新合约数量应与原持仓相近
        new_qty = max(1, int(current_qty))
        
        # 检查是否有足够的净信用（权利金收入 > 平仓成本）
        # 简化：假设当前持仓的平仓成本约为 current_qty * current_premium
        # 这里用目标合约的权利金作为参考
        current_premium_estimate = prem_usd * 0.8  # 假设当前持仓权利金略低于新合约
        close_cost = current_qty * current_premium_estimate
        open_credit = new_qty * effective_prem_usd
        net_credit = open_credit - close_cost

        strike = c['strike']
        margin_req = new_qty * strike * margin_ratio if c_type == 'PUT' else new_qty * prem_usd * 10

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
            "premium_usd": round(prem_usd, 2), "effective_prem_usd": round(effective_prem_usd, 2),
            "new_qty": new_qty,
            "margin_req": round(margin_req, 2), "gross_credit": round(open_credit, 2),
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
            "margin_ratio": margin_ratio,
            "option_type": option_type
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


async def calc_new_plan(currency: str, spot: float, min_dte: int, max_dte: int, margin_ratio: float, option_type: str) -> Dict[str, Any]:
    """新建模式计算"""
    from services.risk_framework import RiskFramework

    # 获取合约数据
    contracts = []
    try:
        from services.exchange_abstraction import registry, ExchangeType, OptionType
        exchange = registry.get(ExchangeType.DERIBIT)
        option_type_enum = OptionType.CALL if option_type.upper() == 'CALL' else OptionType.PUT
        chain = await exchange.get_options_chain(currency, option_type_enum)
        contracts = [c.to_dict() for c in chain]
    except Exception as e:
        logger.warning("Error fetching options chain: %s", e)

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
