"""统一保证金计算模块

修复 H-10: 消除 risk_framework / unified_strategy_engine / scan_engine 中
3 种不同的保证金计算公式，统一为单一实现。

公式:
- PUT:  max(strike * 0.1, (strike - premium) * margin_ratio)
- CALL: max(strike * 0.1, strike * margin_ratio - premium)
"""


def calc_margin(strike: float, premium: float, option_type: str, margin_ratio: float = 0.2) -> float:
    """统一保证金计算

    Args:
        strike: 行权价
        premium: 权利金
        option_type: "PUT" 或 "CALL"
        margin_ratio: 保证金比例 (默认 0.2)

    Returns:
        保证金金额 (始终 >= 0)
    """
    opt = option_type.upper()
    min_margin = strike * 0.1

    if opt == "PUT":
        base = (strike - premium) * margin_ratio
    elif opt == "CALL":
        base = strike * margin_ratio - premium
    else:
        base = strike * margin_ratio

    return max(min_margin, base)


def calc_margin_put(strike: float, premium: float, margin_ratio: float = 0.2) -> float:
    """Put 保证金快捷计算"""
    return calc_margin(strike, premium, "PUT", margin_ratio)


def calc_margin_call(strike: float, premium: float, margin_ratio: float = 0.2) -> float:
    """Call 保证金快捷计算"""
    return calc_margin(strike, premium, "CALL", margin_ratio)
