"""统一保证金计算模块

修复 H-10: 消除 risk_framework / unified_strategy_engine / scan_engine 中
3 种不同的保证金计算公式，统一为单一实现。

v6.4: 支持 Deribit / Binance 阶梯保证金模型
- Deribit: OTM 抵扣算法 (深度 OTM 保证金更低)
- Binance: 阶梯保证金 (根据 OTM 比例调整)
"""

from typing import Literal


def calc_margin(
    strike: float,
    premium: float,
    option_type: str,
    margin_ratio: float = 0.2,
    spot: float = 0,
    exchange: Literal["deribit", "binance", "generic"] = "generic"
) -> float:
    """统一保证金计算（支持多交易所阶梯保证金模型）

    Args:
        strike: 行权价
        premium: 权利金 (USD)
        option_type: "PUT" 或 "CALL"
        margin_ratio: 基础保证金比例 (默认 0.2)
        spot: 现货价格 (用于 OTM 计算，默认为0使用简化模型)
        exchange: 交易所模型 ("deribit" | "binance" | "generic")

    Returns:
        保证金金额 (始终 >= 0)
    """
    opt = option_type.upper()
    min_margin = strike * 0.1  # 最低保证金为行权价 10%

    if exchange == "deribit" and spot > 0:
        # Deribit 阶梯保证金模型
        # 深度 OTM 时保证金大幅降低
        if opt == "PUT":
            otm_amount = max(0, spot - strike)  # OTM 金额
            otm_ratio = otm_amount / spot if spot > 0 else 0
            # Deribit 规则: OTM 部分可抵扣，但最低保留 10%
            base = max(strike * 0.1, (strike - premium) * margin_ratio * (1 - otm_ratio * 0.5))
        else:  # CALL
            otm_amount = max(0, strike - spot)
            otm_ratio = otm_amount / spot if spot > 0 else 0
            base = max(strike * 0.1, (strike * margin_ratio - premium) * (1 - otm_ratio * 0.5))
        return max(min_margin, base)

    elif exchange == "binance" and spot > 0:
        # Binance 阶梯保证金模型
        if opt == "PUT":
            otm_ratio = max(0, (spot - strike) / spot) if spot > 0 else 0
            # Binance: OTM > 10% 时保证金大幅降低
            if otm_ratio > 0.1:
                discount = min(0.5, otm_ratio)  # 最大抵扣 50%
                base = (strike - premium) * margin_ratio * (1 - discount)
            else:
                base = (strike - premium) * margin_ratio
        else:  # CALL
            otm_ratio = max(0, (strike - spot) / spot) if spot > 0 else 0
            if otm_ratio > 0.1:
                discount = min(0.5, otm_ratio)
                base = (strike * margin_ratio - premium) * (1 - discount)
            else:
                base = strike * margin_ratio - premium
        return max(min_margin, base)

    else:
        # 通用简化模型
        if opt == "PUT":
            base = (strike - premium) * margin_ratio
        elif opt == "CALL":
            base = strike * margin_ratio - premium
        else:
            base = strike * margin_ratio

        return max(min_margin, base)


def calc_margin_put(strike: float, premium: float, margin_ratio: float = 0.2, spot: float = 0, exchange: str = "generic") -> float:
    """Put 保证金快捷计算"""
    return calc_margin(strike, premium, "PUT", margin_ratio, spot, exchange)


def calc_margin_call(strike: float, premium: float, margin_ratio: float = 0.2, spot: float = 0, exchange: str = "generic") -> float:
    """Call 保证金快捷计算"""
    return calc_margin(strike, premium, "CALL", margin_ratio, spot, exchange)
