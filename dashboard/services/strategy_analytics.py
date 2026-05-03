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

    def _calc_leg_pnl(self, prices: List[float], strike: float, premium: float,
                       is_sell: bool, is_put: bool, qty: float) -> List[float]:
        """计算单腿在价格网格上的 PnL。"""
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
            pnl.append(round(val * qty, 2))
        return pnl

    def _generate_grid(self, spot: float, pct_range: float, steps: int) -> List[float]:
        """生成价格网格，使用 _nice_step 确保步长整齐。"""
        low = spot * (1 - pct_range)
        high = spot * (1 + pct_range)
        step_size = _nice_step(high - low, steps)
        num_points = int(round((high - low) / step_size)) + 1
        return [round(low + i * step_size, 2) for i in range(num_points)]

    def _find_breakevens(self, prices: List[float], pnl: List[float]) -> List[float]:
        """查找所有盈亏平衡点：先精确零点，再线性插值。"""
        breakevens = []
        for i in range(len(prices)):
            if pnl[i] == 0:
                breakevens.append(round(prices[i], 2))
        if not breakevens:
            for i in range(len(prices) - 1):
                if (pnl[i] < 0 and pnl[i + 1] > 0) or (pnl[i] > 0 and pnl[i + 1] < 0):
                    ratio = -pnl[i] / (pnl[i + 1] - pnl[i])
                    breakevens.append(round(prices[i] + (prices[i + 1] - prices[i]) * ratio, 2))
        return breakevens

    def calc_single(self, spot: float, strike: float, premium: float,
                    option_type: str, dte: int, quantity: float = 1,
                    side: str = "sell", pct_range: float = 0.3,
                    steps: int = 100) -> Dict[str, Any]:
        """单腿 payoff 计算"""
        is_put = option_type.upper() in ("P", "PUT")
        is_sell = side.lower() == "sell"

        prices = self._generate_grid(spot, pct_range, steps)
        pnl = self._calc_leg_pnl(prices, strike, premium, is_sell, is_put, quantity)

        max_profit = max(pnl)
        max_loss = min(pnl)

        breakevens = self._find_breakevens(prices, pnl)
        breakeven = breakevens[0] if breakevens else None

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

    def calc_multi_legs(self, spot: float, legs: List[Dict[str, Any]],
                        pct_range: float = 0.3, steps: int = 100) -> Dict[str, Any]:
        """组合策略 payoff"""
        if not legs:
            return {"success": False, "error": "至少需要一条腿"}

        prices = self._generate_grid(spot, pct_range, steps)

        total_pnl = [0.0] * len(prices)
        leg_results = []

        for leg in legs:
            is_put = leg.get("option_type", "P").upper() in ("P", "PUT")
            is_sell = leg.get("side", "sell").lower() == "sell"
            strike = leg.get("strike", spot)
            premium = leg.get("premium", 0)
            qty = leg.get("quantity", 1)

            pnl = self._calc_leg_pnl(prices, strike, premium, is_sell, is_put, qty)

            for i in range(len(total_pnl)):
                total_pnl[i] += pnl[i]

            leg_results.append({
                "strike": strike,
                "premium": premium,
                "option_type": "PUT" if is_put else "CALL",
                "side": "sell" if is_sell else "buy",
                "quantity": qty,
                "pnl": pnl,
                "max_profit": max(pnl),
                "max_loss": min(pnl),
            })

        total_pnl = [round(v, 2) for v in total_pnl]

        breakevens = self._find_breakevens(prices, total_pnl)

        return {
            "success": True,
            "max_profit": max(total_pnl),
            "max_loss": min(total_pnl),
            "breakevens": breakevens,
            "payoff_curve": {"prices": prices, "pnl": total_pnl},
            "legs": leg_results,
        }

    def calc_probability_overlay(self, spot: float, dte: int, iv: float,
                                 strikes: List[float] = None) -> Dict[str, Any]:
        """到期价格概率分布（对数正态）"""
        sigma = iv / 100
        T = dte / 365.0
        if T <= 0 or sigma <= 0:
            return {"density": []}

        mu = math.log(spot) - 0.5 * sigma**2 * T
        std = sigma * math.sqrt(T)

        low = spot * 0.5
        high = spot * 1.5
        n_points = 200
        step = (high - low) / n_points

        density = []
        for i in range(n_points + 1):
            price = low + i * step
            if price <= 0:
                density.append([round(price, 2), 0])
                continue
            ln_price = math.log(price)
            z = (ln_price - mu) / std
            prob = norm_pdf(z) / (price * std)
            density.append([round(price, 2), round(prob * step, 6)])

        return {"density": density, "mean": spot, "dte": dte}

    def calc_time_decay(self, spot: float, strike: float, premium: float,
                        option_type: str, iv: float,
                        dte_max: int = 60) -> Dict[str, Any]:
        """多时间点的期权价值曲线"""
        ot = "P" if option_type.upper() in ("P", "PUT") else "C"
        dte_values = [d for d in [60, 45, 30, 15, 7, 1] if d <= dte_max]

        low = spot * 0.7
        high = spot * 1.3
        n_points = 100
        step = (high - low) / n_points
        prices = [round(low + i * step, 2) for i in range(n_points + 1)]

        curves = []
        for dte in dte_values:
            points = []
            for price in prices:
                bs = black_scholes_price(ot, strike, price, dte, iv)
                points.append([price, bs["premium"]])
            curves.append({"dte": dte, "points": points})

        return {"curves": curves, "prices": prices}

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

    def score_strategy(self, spot: float, strike: float, premium: float,
                       option_type: str, dte: int,
                       delta: float = None) -> Dict[str, Any]:
        """策略评分 — 与 StrategyScorer 对齐"""
        from services.strategy_engine import StrategyScorer
        scorer = StrategyScorer()

        is_put = option_type.upper() in ("P", "PUT")

        if delta is None:
            # 基于虚值距离估算 delta，避免固定值无法区分深 OTM 和近 ATM
            otm_pct = abs(1 - strike / spot) if spot > 0 else 0.5
            est = max(0.003, 0.50 * math.exp(-otm_pct * 50))
            delta = -est if is_put else est

        contract = {
            "option_type": "P" if is_put else "C",
            "strike": strike,
            "premium_usd": premium,
            "dte": dte,
            "delta": delta,
            "apr": (premium / strike * 365 / dte * 100) if dte > 0 and strike > 0 else 0,
            "open_interest": 500,
            "spread_pct": 2.0,
        }

        score = scorer.score(contract, spot)

        # Delta-based risk penalty: 高 delta（近 ATM）策略即使 APR/theta 看起来好，也应降分
        delta_abs = abs(delta)
        risk_factor = max(0.3, 1.0 - delta_abs * 1.5)
        adjusted_total = score.total * risk_factor

        return {
            "total": round(adjusted_total, 4),
            "ev": round(score.ev, 4),
            "apr": round(score.apr, 4),
            "liquidity": round(score.liquidity, 4),
            "theta": round(score.theta, 4),
            "recommendation": scorer._classify_score(adjusted_total),
        }


class WheelSimulator:

    def simulate(self, spot: float, strike: float, premium: float,
                 option_type: str, cycles: int, capital: float,
                 assigned_pct: float = 0.5, iv: float = 0.6,
                 drift: float = 0.0, simulations: int = 1000) -> Dict[str, Any]:
        """蒙特卡洛 Wheel 模拟"""
        if spot <= 0 or strike <= 0 or capital <= 0 or cycles <= 0:
            return {"success": False, "error": "参数无效"}

        random.seed(42)
        dt = 30 / 365.0

        all_rois = []
        sample_paths = []
        win_count = 0
        drawdowns = []

        for sim_idx in range(simulations):
            price = spot
            total_premium = 0.0
            path = [price]
            was_assigned = False
            holding_stock = False
            stock_cost_basis = 0.0
            max_val = capital
            max_dd = 0.0

            for cycle in range(cycles):
                put_itm = price < strike

                if holding_stock:
                    # 已持仓：卖 Covered Call，不再卖 Put
                    call_premium = premium * 0.8
                    total_premium += call_premium
                    z = random.gauss(0, 1)
                    price = price * math.exp((drift - 0.5 * iv**2) * dt + iv * math.sqrt(dt) * z)
                    path.append(price)
                    call_itm = price > strike
                    if call_itm:
                        # 被 Call 走：以 strike 卖出，结算持仓盈亏
                        total_premium += (strike - stock_cost_basis)
                        price = strike
                        holding_stock = False
                        was_assigned = False
                else:
                    total_premium += premium
                    if put_itm:
                        # 被行权：以 strike 买入标的
                        cost = strike - premium
                        was_assigned = True
                        holding_stock = True
                        stock_cost_basis = strike
                        z = random.gauss(0, 1)
                        price = price * math.exp((drift - 0.5 * iv**2) * dt + iv * math.sqrt(dt) * z)
                        path.append(price)
                        call_premium = premium * 0.8
                        total_premium += call_premium
                        call_itm = price > strike
                        if call_itm:
                            total_premium += (strike - stock_cost_basis)
                            price = strike
                            was_assigned = False
                            holding_stock = False
                    else:
                        was_assigned = False
                        z = random.gauss(0, 1)
                        price = price * math.exp((drift - 0.5 * iv**2) * dt + iv * math.sqrt(dt) * z)
                        path.append(price)

                current_val = capital + total_premium
                if current_val > max_val:
                    max_val = current_val
                dd = (max_val - current_val) / max_val
                if dd > max_dd:
                    max_dd = dd

            # 结算未平仓持仓的市值
            if holding_stock:
                total_premium += (price - stock_cost_basis)

            roi = total_premium / capital
            all_rois.append(roi)
            if roi > 0:
                win_count += 1
            drawdowns.append(max_dd)

            if sim_idx < 5:
                sample_paths.append(path)

        all_rois.sort()
        n = len(all_rois)

        bins = 20
        min_roi = all_rois[0]
        max_roi = all_rois[-1]
        bin_width = (max_roi - min_roi) / bins if max_roi > min_roi else 0.01
        roi_distribution = []
        for i in range(bins):
            lo = min_roi + i * bin_width
            hi = lo + bin_width
            count = sum(1 for r in all_rois if lo <= r < hi)
            roi_distribution.append([round(lo, 4), count])

        mean_roi = sum(all_rois) / n
        score = self._score_wheel(mean_roi, win_count / n, sum(drawdowns) / n)

        return {
            "success": True,
            "summary": {
                "mean_roi": round(mean_roi, 4),
                "median_roi": round(all_rois[n // 2], 4),
                "p10": round(all_rois[int(n * 0.1)], 4),
                "p25": round(all_rois[int(n * 0.25)], 4),
                "p75": round(all_rois[int(n * 0.75)], 4),
                "p90": round(all_rois[int(n * 0.9)], 4),
                "win_rate": round(win_count / simulations, 4),
                "max_drawdown_mean": round(sum(drawdowns) / len(drawdowns), 4),
                "simulations": simulations,
                "cycles": cycles,
            },
            "roi_distribution": roi_distribution,
            "sample_paths": sample_paths,
            "score": score,
        }

    def _score_wheel(self, mean_roi: float, win_rate: float,
                     mean_drawdown: float) -> Dict[str, Any]:
        """Wheel 策略评分"""
        roi_score = min(max(mean_roi / 0.20, 0), 1.0)
        wr_score = win_rate
        dd_score = max(1 - mean_drawdown / 0.30, 0)
        total = roi_score * 0.40 + wr_score * 0.35 + dd_score * 0.25

        if total >= 0.75:
            rec = "BEST"
        elif total >= 0.55:
            rec = "GOOD"
        elif total >= 0.40:
            rec = "OK"
        elif total >= 0.25:
            rec = "CAUTION"
        else:
            rec = "SKIP"

        return {"total": round(total, 4), "recommendation": rec}
