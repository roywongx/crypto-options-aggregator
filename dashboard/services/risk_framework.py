# Services - Risk Framework
import logging
import threading
from config import config
from datetime import datetime

logger = logging.getLogger(__name__)

class RiskFramework:
    """v7.0: 动态风险框架 - 支持动态支撑位"""

    # 静态回退值
    REGULAR_FLOOR = config.BTC_REGULAR_FLOOR
    EXTREME_FLOOR = config.BTC_EXTREME_FLOOR

    # 动态支撑位计算器实例
    _support_calculator = None
    _cached_floors = None
    _cache_timestamp = None
    _cache_lock = threading.Lock()

    @classmethod
    def _get_support_calculator(cls):
        """获取支撑位计算器实例"""
        if cls._support_calculator is None:
            from services.support_calculator import DynamicSupportCalculator
            cls._support_calculator = DynamicSupportCalculator()
        return cls._support_calculator

    @classmethod
    def _get_floors(cls) -> dict:
        """获取支撑位，带缓存（缓存时间延长至4小时）"""
        now = datetime.now()

        with cls._cache_lock:
            # 缓存4小时
            if (cls._cached_floors and cls._cache_timestamp and
                (now - cls._cache_timestamp).total_seconds() < config.RISK_CACHE_TTL_SECONDS):
                return cls._cached_floors

        # 重新计算
        try:
            calculator = cls._get_support_calculator()
            floors = calculator.get_dynamic_floors()
            with cls._cache_lock:
                cls._cached_floors = floors
                cls._cache_timestamp = now
            return floors
        except (RuntimeError, ConnectionError, TimeoutError) as e:
            logger.warning("获取动态支撑位失败: %s", e)
            # 失败也更新时间戳，避免每次调用都重试
            with cls._cache_lock:
                cls._cache_timestamp = now
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
        multiplier = config.RISK_FLOOR_MULTIPLIER

        if spot >= regular * multiplier:
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
            return 0.70
        elif strike <= regular:
            return 0.85
        elif strike > spot:
            return 0.80
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

    @classmethod
    def check_circuit_breaker(
        cls,
        current_equity: float,
        peak_equity: float,
        consecutive_losses: int = 0,
        open_positions: int = 0,
        dvol: float = 50.0,
    ) -> dict:
        """投资组合级熔断检查 (Freqtrade Protections 风格)

        Returns:
            dict with tripped (bool), reason (str), suggested_action (str)
        """
        from config import config
        reasons = []

        # 1. Max drawdown check
        if peak_equity > 0:
            drawdown = (peak_equity - current_equity) / peak_equity
            if drawdown >= config.MAX_DRAWDOWN_THRESHOLD:
                reasons.append(
                    f"回撤 {drawdown:.1%} 超过熔断线 {config.MAX_DRAWDOWN_THRESHOLD:.0%}，"
                    f"当前 ${current_equity:,.0f} vs 峰值 ${peak_equity:,.0f}"
                )

        # 2. Consecutive losses guard
        if consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
            reasons.append(f"连续亏损 {consecutive_losses} 次，触发熔断")

        # 3. Overtrading guard
        if open_positions > config.MAX_POSITIONS_OPEN:
            reasons.append(f"持仓数 {open_positions} 超过上限 {config.MAX_POSITIONS_OPEN}")

        # 4. DVOL panic
        if dvol >= config.DVOL_PANIC_THRESHOLD:
            reasons.append(f"DVOL {dvol:.0f} 触发恐慌阈值 {config.DVOL_PANIC_THRESHOLD}")

        if reasons:
            return {
                "tripped": True,
                "reason": "; ".join(reasons),
                "suggested_action": "暂停新开仓，考虑减仓或对冲",
                "drawdown": round((peak_equity - current_equity) / peak_equity, 4) if peak_equity > 0 else 0.0,
                "consecutive_losses": consecutive_losses,
                "open_positions": open_positions,
            }
        return {"tripped": False, "reason": "", "suggested_action": "正常操作"}

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
        """Put 保证金计算（委托给统一实现）"""
        from services.margin_calculator import calc_margin_put
        return calc_margin_put(strike, premium_usd, margin_ratio)

    @staticmethod
    def calc_margin_call(strike: float, spot: float, premium_usd: float, margin_ratio: float = 0.2) -> float:
        """Call 保证金计算（委托给统一实现）"""
        from services.margin_calculator import calc_margin_call
        return calc_margin_call(strike, premium_usd, margin_ratio)

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
        a = min(max(apr, 0) / 100.0, 1.0)
        p = min(max(pop, 0), 1.0)
        b = min(max(breakeven_pct, 0) / config.CALC_BREAKEVEN_MAX, 1.0)
        l = min(max(liquidity_score, 0) / config.CALC_LIQUIDITY_MAX, 1.0)
        ir = max(iv_rank, 0)
        iv = 0.5 + (ir - 50) / 100.0

        score = (a * config.CALC_WEIGHT_APR +
                 p * config.CALC_WEIGHT_POP +
                 b * config.CALC_WEIGHT_BREAKEVEN +
                 l * config.CALC_WEIGHT_LIQUIDITY +
                 iv * config.CALC_WEIGHT_IV)

        if spot > 0 and strike > 0:
            score *= RiskFramework.get_score_modifier(strike, spot)

        return round(score, 4)

def _risk_emoji(abs_delta: float) -> str:
    if abs_delta > 0.30:
        return "🔴"
    if abs_delta > 0.20:
        return "🟡"
    return "🟢"
