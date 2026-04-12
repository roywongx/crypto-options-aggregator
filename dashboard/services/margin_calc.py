# Services - Margin Calculator

def calc_margin_put(strike: float, spot: float, premium_usd: float, margin_ratio: float = 0.2) -> float:
    """
    计算 PUT 期权保证金

    Args:
        strike: 行权价格
        spot: 现货价格
        premium_usd: 权利金(USD)
        margin_ratio: 保证金比率

    Returns:
        float: 保证金金额
    """
    if strike >= spot:
        return strike * margin_ratio - premium_usd + max(0, (spot - strike * 0.9) * 0.1)
    return max(strike * 0.1, strike * margin_ratio - premium_usd)

def calc_margin_call(strike: float, spot: float, premium_usd: float, margin_ratio: float = 0.2) -> float:
    """
    计算 CALL 期权保证金

    Args:
        strike: 行权价格
        spot: 现货价格
        premium_usd: 权利金(USD)
        margin_ratio: 保证金比率

    Returns:
        float: 保证金金额
    """
    if strike <= spot:
        return spot * margin_ratio - premium_usd + max(0, (spot * 1.1 - strike) * 0.1)
    return max(strike * 0.1, strike * margin_ratio - premium_usd)

def calc_margin(option_type: str, strike: float, spot: float, premium_usd: float, margin_ratio: float = 0.2) -> float:
    """统一保证金计算入口"""
    if option_type.upper() in ("PUT", "P"):
        return calc_margin_put(strike, spot, premium_usd, margin_ratio)
    else:
        return calc_margin_call(strike, spot, premium_usd, margin_ratio)
