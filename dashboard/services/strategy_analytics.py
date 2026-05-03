"""
策略分析引擎 v1.0
PayoffEngine: 单腿/组合 payoff 计算、概率叠加、时间衰减
WheelSimulator: 蒙特卡洛 Wheel 模拟
"""
import math
import random
from typing import List, Dict, Any, Optional
from services.shared_calculations import black_scholes_price, norm_cdf, norm_pdf


def _nice_step(range_size: float, target_steps: int) -> float:
    """计算一个"整洁"的步长，使得常见行权价（1000 的倍数等）恰好落在网格上。"""
    raw = range_size / target_steps
    if raw <= 0:
        return 1.0
    exp = int(math.floor(math.log10(raw)))
    base = 10 ** exp
    candidates = [1, 2, 2.5, 5, 10]
    best = min(candidates, key=lambda c: abs(raw - c * base))
    return best * base


class PayoffEngine:

    def calc_single(self, spot: float, strike: float, premium: float,
                    option_type: str, dte: int, quantity: float = 1,
                    side: str = "sell", pct_range: float = 0.3,
                    steps: int = 100) -> Dict[str, Any]:
        """单腿 payoff 计算"""
        is_put = option_type.upper() in ("P", "PUT")
        is_sell = side.lower() == "sell"

        low = spot * (1 - pct_range)
        high = spot * (1 + pct_range)

        step_size = _nice_step(high - low, steps)
        # 生成价格网格
        num_points = int(round((high - low) / step_size)) + 1
        prices = [round(low + i * step_size, 2) for i in range(num_points)]

        pnl = []
        for price in prices:
            if is_sell:
                if is_put:
                    val = premium if price >= strike else premium - (strike - price)
                else:
                    val = premium if price <= strike else premium - (price - strike)
            else:
                if is_put:
                    val = -premium + (strike - price) if price < strike else -premium
                else:
                    val = -premium + (price - strike) if price > strike else -premium
            pnl.append(round(val * quantity, 2))

        max_profit = max(pnl)
        max_loss = min(pnl)

        # --- 盈亏平衡点检测 ---
        breakeven = None
        # 1) 先检查是否有精确零点
        for i in range(len(prices)):
            if pnl[i] == 0:
                breakeven = round(prices[i], 2)
                break
        # 2) 若无精确零点，使用线性插值
        if breakeven is None:
            for i in range(len(prices) - 1):
                if (pnl[i] < 0 and pnl[i + 1] > 0) or (pnl[i] > 0 and pnl[i + 1] < 0):
                    ratio = -pnl[i] / (pnl[i + 1] - pnl[i])
                    breakeven = round(prices[i] + (prices[i + 1] - prices[i]) * ratio, 2)
                    break

        profit_prices = [p for p, v in zip(prices, pnl) if v > 0]
        loss_prices = [p for p, v in zip(prices, pnl) if v < 0]
        zones = {
            "profit_range": [min(profit_prices), max(profit_prices)] if profit_prices else None,
            "loss_range": [min(loss_prices), max(loss_prices)] if loss_prices else None,
        }

        # profit_at_spot: 取最接近 spot 的价格点
        mid_idx = min(range(len(prices)), key=lambda i: abs(prices[i] - spot))

        return {
            "max_profit": max_profit,
            "max_loss": max_loss,
            "breakeven": breakeven,
            "profit_at_spot": pnl[mid_idx],
            "payoff_curve": {"prices": prices, "pnl": pnl},
            "zones": zones,
        }

    def estimate_premium(self, spot: float, strike: float, dte: int,
                         iv: float, option_type: str) -> Dict[str, Any]:
        """BS 估算权利金 + Greeks"""
        ot = "P" if option_type.upper() in ("P", "PUT") else "C"
        bs = black_scholes_price(ot, strike, spot, dte, iv)
        return {
            "premium": bs["premium"],
            "delta": bs["delta"],
            "gamma": bs["gamma"],
            "theta": bs["theta"],
            "vega": bs["vega"],
        }
