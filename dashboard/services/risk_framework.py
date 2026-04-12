# Services - Risk Framework
from config import config

class RiskFramework:
    """v6.0: BTC 风险框架 - $55k 常规底, $45k 极限底"""

    REGULAR_FLOOR = config.BTC_REGULAR_FLOOR
    EXTREME_FLOOR = config.BTC_EXTREME_FLOOR

    @classmethod
    def get_status(cls, spot: float) -> str:
        if spot > cls.REGULAR_FLOOR * 1.1:
            return "NORMAL"
        elif spot > cls.REGULAR_FLOOR:
            return "NEAR_FLOOR"
        elif spot > cls.EXTREME_FLOOR:
            return "ADVERSE"
        else:
            return "PANIC"

    @classmethod
    def get_score_modifier(cls, strike: float, spot: float) -> float:
        if strike <= cls.EXTREME_FLOOR:
            return 1.2
        elif strike <= cls.REGULAR_FLOOR:
            return 1.1
        elif strike > spot:
            return 0.8
        return 1.0

    @classmethod
    def get_risk_label(cls, spot: float) -> tuple:
        status = cls.get_status(spot)
        labels = {
            "NORMAL": ("🟢 正常", "市场健康，可积极操作"),
            "NEAR_FLOOR": ("🟡 接近底线", "BTC 接近关键支撑，谨慎操作"),
            "ADVERSE": ("🔴 逆境", "BTC 跌破常规底线，减少风险暴露"),
            "PANIC": ("🚨 极恐/止损区", "极端行情，强烈建议止损或对冲")
        }
        return labels.get(status, ("⚪ 未知", ""))

class CalculationEngine:
    """v5.6: 统一计算引擎"""

    @staticmethod
    def calc_apr(premium_usd: float, strike: float, dte: int, margin_ratio: float = 0.2) -> float:
        cv = strike * margin_ratio
        if cv <= 0 or dte <= 0:
            return 0.0
        annual_factor = 365.0 / dte
        return (premium_usd / cv) * annual_factor * 100

    @staticmethod
    def calc_margin_put(strike: float, spot: float, premium_usd: float, margin_ratio: float = 0.2) -> float:
        if strike >= spot:
            return strike * margin_ratio - premium_usd + max(0, (spot - strike * 0.9) * 0.1)
        return max(strike * 0.1, strike * margin_ratio - premium_usd)

    @staticmethod
    def calc_margin_call(strike: float, spot: float, premium_usd: float, margin_ratio: float = 0.2) -> float:
        if strike <= spot:
            return spot * margin_ratio - premium_usd + max(0, (spot * 1.1 - strike) * 0.1)
        return max(strike * 0.1, strike * margin_ratio - premium_usd)

    @staticmethod
    def calc_breakeven_pct(strike: float, premium_usd: float, option_type: str, spot: float) -> float:
        if option_type.upper() == "CALL":
            return ((strike + premium_usd) / spot - 1) * 100 if spot > 0 else 0
        else:
            return ((strike - premium_usd) / spot - 1) * 100 if spot > 0 else 0

    @staticmethod
    def calc_spread_pct(buy_price: float, sell_price: float) -> float:
        if buy_price <= 0 or sell_price <= 0:
            return 0.0
        return ((sell_price - buy_price) / ((sell_price + buy_price) / 2)) * 100

def _risk_emoji(abs_delta: float) -> str:
    if abs_delta > 0.30:
        return "🔴"
    if abs_delta > 0.20:
        return "🟡"
    return "🟢"
