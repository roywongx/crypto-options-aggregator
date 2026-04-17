# Services - Risk Framework
from config import config
from datetime import datetime

class RiskFramework:
    """v7.0: 动态风险框架 - 支持动态支撑位"""

    # 静态回退值
    REGULAR_FLOOR = config.BTC_REGULAR_FLOOR
    EXTREME_FLOOR = config.BTC_EXTREME_FLOOR
    
    # 动态支撑位计算器实例
    _support_calculator = None
    _cached_floors = None
    _cache_timestamp = None

    @classmethod
    def _get_support_calculator(cls):
        """获取支撑位计算器实例"""
        if cls._support_calculator is None:
            from services.support_calculator import DynamicSupportCalculator
            cls._support_calculator = DynamicSupportCalculator()
        return cls._support_calculator

    @classmethod
    def _get_floors(cls) -> dict:
        """获取支撑位，带缓存"""
        now = datetime.now()
        
        # 缓存1小时
        if (cls._cached_floors and cls._cache_timestamp and 
            (now - cls._cache_timestamp).total_seconds() < 3600):
            return cls._cached_floors
        
        # 重新计算
        try:
            calculator = cls._get_support_calculator()
            cls._cached_floors = calculator.get_dynamic_floors()
            cls._cache_timestamp = now
            return cls._cached_floors
        except Exception as e:
            logger.warning(f"获取动态支撑位失败: {e}")
            # 回退到静态值
            return {
                "regular": cls.REGULAR_FLOOR,
                "extreme": cls.EXTREME_FLOOR,
                "fallback": True
            }

    @classmethod
    def get_status(cls, spot: float) -> str:
        """动态风险状态判断"""
        floors = cls._get_floors()
        regular = floors["regular"]
        extreme = floors["extreme"]
        
        if spot > regular * 1.1:
            return "NORMAL"
        elif spot > regular:
            return "NEAR_FLOOR"
        elif spot > extreme:
            return "ADVERSE"
        else:
            return "PANIC"

    @classmethod
    def get_score_modifier(cls, strike: float, spot: float) -> float:
        floors = cls._get_floors()
        extreme = floors["extreme"]
        regular = floors["regular"]
        
        if strike <= extreme:
            return 1.2
        elif strike <= regular:
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
        """
        修正后的 Put 保证金计算
        基于：最大潜在亏损 * 风险系数
        """
        # 计算最大潜在亏损 (假设价格跌到0)
        max_loss = strike - premium_usd
        
        # 基础保证金 = 最大亏损 * 保证金比例
        base_margin = max_loss * margin_ratio
        
        # 最小保证金要求 = 行权价 * 10%
        min_margin = strike * 0.1
        
        # 取较大值，确保保证金为正数
        return max(min_margin, base_margin)

    @staticmethod
    def calc_margin_call(strike: float, spot: float, premium_usd: float, margin_ratio: float = 0.2) -> float:
        """
        修正后的 Call 保证金计算
        """
        # Call 的最大亏损理论上无限，使用行权价作为基准
        base_margin = strike * margin_ratio - premium_usd
        
        # 最小保证金要求
        min_margin = strike * 0.1
        
        # 确保为正数
        return max(min_margin, base_margin)

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

    @staticmethod
    def weighted_score(apr: float, pop: float, breakeven_pct: float,
                       liquidity_score: float, iv_rank: float,
                       strike: float = 0, spot: float = 0) -> float:
        a = min(max(apr, 0) / 200.0, 1.0)
        p = min(max(pop, 0) / 100.0, 1.0)
        b = min(max(breakeven_pct, 0) / 20.0, 1.0)
        l = min(max(liquidity_score, 0) / 100.0, 1.0)
        ir = max(iv_rank, 0)
        iv = 1.0 - abs(ir - 50) / 50.0

        score = a * 0.25 + p * 0.25 + b * 0.20 + l * 0.15 + iv * 0.15

        if spot > 0 and strike > 0:
            score *= RiskFramework.get_score_modifier(strike, spot)

        return round(score, 4)

def _risk_emoji(abs_delta: float) -> str:
    if abs_delta > 0.30:
        return "🔴"
    if abs_delta > 0.20:
        return "🟡"
    return "🟢"
