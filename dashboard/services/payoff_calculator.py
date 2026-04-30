"""
Payoff 可视化计算引擎 v2.0
支持 Sell Put / Sell Call / Wheel 策略的盈亏图计算
增强版：策略评分、实操建议、智能估算、对比功能
使用共享计算模块统一核心逻辑
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import math
from services.shared_calculations import (
    norm_cdf, black_scholes_price, calc_win_rate, 
    calc_liquidity_score, score_to_rating
)


@dataclass
class PayoffParams:
    option_type: str
    strike: float
    premium: float
    quantity: float = 1.0
    spot: float = 0.0
    dte: int = 30  # Days to Expiration


class PayoffCalculator:
    
    @staticmethod
    def calc_sell_put(p: PayoffParams, prices: List[float]) -> List[float]:
        return [p.premium * p.quantity if price >= p.strike 
                else (p.premium - (p.strike - price)) * p.quantity 
                for price in prices]

    @staticmethod
    def calc_sell_call(p: PayoffParams, prices: List[float]) -> List[float]:
        return [p.premium * p.quantity if price <= p.strike 
                else (p.premium - (price - p.strike)) * p.quantity 
                for price in prices]

    @staticmethod
    def calc_buy_put(p: PayoffParams, prices: List[float]) -> List[float]:
        return [(-p.premium + (p.strike - price)) * p.quantity if price < p.strike 
                else -p.premium * p.quantity 
                for price in prices]

    @staticmethod
    def calc_buy_call(p: PayoffParams, prices: List[float]) -> List[float]:
        return [(-p.premium + (price - p.strike)) * p.quantity if price > p.strike 
                else -p.premium * p.quantity 
                for price in prices]

    @staticmethod
    def generate_price_range(spot: float, pct_range: float = 0.3, steps: int = 100) -> List[float]:
        low = spot * (1 - pct_range)
        high = spot * (1 + pct_range)
        step = (high - low) / steps
        return [round(low + i * step, 2) for i in range(steps + 1)]

    def calc_payoff(self, legs: List[Dict[str, Any]], spot: float, 
                    pct_range: float = 0.3, steps: int = 100) -> Dict[str, Any]:
        prices = self.generate_price_range(spot, pct_range, steps)
        total_pnl = [0.0] * len(prices)
        leg_results = []

        for leg in legs:
            option_type = self._normalize_option_type(leg.get("option_type", "P"))
            params = PayoffParams(
                option_type=option_type,
                strike=leg.get("strike", spot),
                premium=leg.get("premium", 0) or 0,
                quantity=leg.get("quantity", 1),
                spot=spot
            )

            if leg.get("direction") == "sell":
                if option_type == "P":
                    pnl = self.calc_sell_put(params, prices)
                else:
                    pnl = self.calc_sell_call(params, prices)
            else:
                if option_type == "P":
                    pnl = self.calc_buy_put(params, prices)
                else:
                    pnl = self.calc_buy_call(params, prices)

            for i in range(len(total_pnl)):
                total_pnl[i] += pnl[i]

            breakeven = None
            for i in range(len(prices) - 1):
                if (pnl[i] <= 0 and pnl[i+1] > 0) or (pnl[i] >= 0 and pnl[i+1] < 0):
                    breakeven = round((prices[i] + prices[i+1]) / 2, 2)
                    break

            max_profit = max(pnl)
            max_loss = min(pnl)

            leg_results.append({
                "option_type": option_type,
                "direction": leg.get("direction", "sell"),
                "strike": params.strike,
                "premium": params.premium,
                "quantity": params.quantity,
                "pnl": [round(v, 2) for v in pnl],
                "breakeven": breakeven,
                "max_profit": round(max_profit, 2),
                "max_loss": round(max_loss, 2)
            })

        total_breakevens = []
        for i in range(len(prices) - 1):
            if (total_pnl[i] <= 0 and total_pnl[i+1] > 0) or (total_pnl[i] >= 0 and total_pnl[i+1] < 0):
                total_breakevens.append(round((prices[i] + prices[i+1]) / 2, 2))

        return {
            "prices": prices,
            "total_pnl": [round(v, 2) for v in total_pnl],
            "legs": leg_results,
            "breakevens": total_breakevens,
            "max_profit": round(max(total_pnl), 2),
            "max_loss": round(min(total_pnl), 2),
            "spot": spot
        }
    
    def calc_strategy_score(self, legs: List[Dict[str, Any]], spot: float, 
                           dte: int = 30, iv: float = 50) -> Dict[str, Any]:
        """
        策略评分系统（0-100 分）
        评分维度：
        - 收益性（30%）：APR、ROI
        - 风险性（30%）：最大亏损、下行风险
        - 胜率（25%）：到期盈利的概率
        - 流动性（15%）：基于 IV 和 DTE 的估算
        """
        result = self.calc_payoff(legs, spot)
        
        max_profit = result['max_profit']
        max_loss = abs(result['max_loss'])
        
        leg = legs[0] if legs else {}
        direction = leg.get("direction", "sell")
        option_type = self._normalize_option_type(leg.get("option_type", "P"))
        strike = leg.get("strike", spot)
        
        if direction == "sell":
            capital_at_risk = strike
        else:
            capital_at_risk = max_loss if max_loss > 0 else abs(max_profit)
        
        roi = (max_profit / capital_at_risk * 100) if capital_at_risk > 0 else 0
        apr = (roi * 365 / dte) if dte > 0 else 0
        
        win_rate = self._calc_win_rate(legs, spot, iv, dte)
        
        roi_score = min(100, max(0, roi * 2))
        risk_score = min(100, max(0, 100 - (max_loss / (spot * 0.1) * 100)))
        win_rate_score = win_rate * 100
        liquidity_score = min(100, max(0, 50 + (iv - 30) * 0.5 + (30 - dte) * 0.5))
        
        total_score = (
            roi_score * 0.30 +
            risk_score * 0.30 +
            win_rate_score * 0.25 +
            liquidity_score * 0.15
        )
        
        return {
            "total_score": round(total_score, 1),
            "components": {
                "roi_score": round(roi_score, 1),
                "risk_score": round(risk_score, 1),
                "win_rate_score": round(win_rate_score, 1),
                "liquidity_score": round(liquidity_score, 1)
            },
            "metrics": {
                "roi_pct": round(roi, 2),
                "apr_pct": round(apr, 2),
                "win_rate_pct": round(win_rate * 100, 2),
                "max_profit": round(max_profit, 2),
                "max_loss": round(max_loss, 2),
                "risk_reward_ratio": round(max_profit / max_loss, 2) if max_loss > 0 else 0
            }
        }
    
    def _normalize_option_type(self, option_type: str) -> str:
        """统一期权类型为大写标准格式"""
        if not option_type:
            return "P"
        ot = str(option_type).upper().strip()
        if ot in ("P", "PUT"):
            return "P"
        elif ot in ("C", "CALL"):
            return "C"
        return "P"
    
    def _calc_win_rate(self, legs: List[Dict[str, Any]], spot: float, 
                       iv: float, dte: int) -> float:
        """
        计算策略胜率（基于正态分布近似）
        使用共享计算模块
        """
        if not legs:
            return 0.5
        
        leg = legs[0]
        direction = leg.get("direction", "sell")
        option_type = self._normalize_option_type(leg.get("option_type", "P"))
        strike = leg.get("strike", spot)
        premium = leg.get("premium", 0) or 0
        
        return calc_win_rate(option_type, direction, strike, premium, spot, iv, dte)
    
    @staticmethod
    def _norm_cdf(x: float) -> float:
        """标准正态分布累积分布函数 - 使用共享模块"""
        return norm_cdf(x)
    
    def generate_strategy_advice(self, score_data: Dict[str, Any], 
                                legs: List[Dict[str, Any]], 
                                spot: float) -> Dict[str, Any]:
        """
        生成实操建议（使用共享评分框架）
        """
        total_score = score_data.get("total_score", 0)
        metrics = score_data.get("metrics", {})
        
        # 使用共享评级系统
        rating_data = score_to_rating(total_score)
        rating = rating_data["rating"]
        rating_level = rating_data["level"]
        
        leg = legs[0] if legs else {}
        direction = leg.get("direction", "sell")
        option_type = self._normalize_option_type(leg.get("option_type", "P"))
        
        if direction == "sell" and option_type == "P":
            scenario = "震荡市或温和看涨"
            advice_text = f"该策略胜率 {metrics.get('win_rate_pct', 0):.0f}%，风险回报比 1:{metrics.get('risk_reward_ratio', 0):.1f}。"
            if metrics.get('win_rate_pct', 0) >= 60:
                advice_text += "胜率较高，适合稳健收取权利金。"
            else:
                advice_text += "胜率一般，建议控制仓位。"
            risks = ["价格跌破行权价可能被行权", "极端下跌时亏损放大"]
            optimizations = [
                "可选择更低的行权价提高胜率",
                "设置止损线控制下行风险",
                "考虑配合现货对冲"
            ]
        elif direction == "sell" and option_type in ("C", "CALL"):
            scenario = "震荡市或温和看跌"
            advice_text = f"该策略胜率 {metrics.get('win_rate_pct', 0):.0f}%，风险回报比 1:{metrics.get('risk_reward_ratio', 0):.1f}。"
            if metrics.get('win_rate_pct', 0) >= 60:
                advice_text += "胜率较高，适合在阻力位附近收取权利金。"
            else:
                advice_text += "注意价格上涨风险，建议控制仓位。"
            risks = ["价格突破行权价可能被行权", "极端上涨时亏损理论无限"]
            optimizations = [
                "可选择更高的行权价提高安全边际",
                "配合现货持有形成备兑",
                "设置止损或滚仓计划"
            ]
        else:
            scenario = "方向性博弈"
            advice_text = f"买入期权策略，胜率 {metrics.get('win_rate_pct', 0):.0f}%。"
            advice_text += "时间价值损耗对买方不利，建议快进快出。"
            risks = ["时间价值损耗", "到期归零风险"]
            optimizations = [
                "选择更长的到期日减少时间损耗",
                "考虑价差策略降低成本",
                "设置止盈止损"
            ]
        
        return {
            "rating": rating,
            "rating_level": rating_level,
            "rating_description": rating_data["description"],
            "scenario": scenario,
            "advice_text": advice_text,
            "risks": risks,
            "optimizations": optimizations
        }
    
    def estimate_premium(self, option_type: str, strike: float, spot: float, 
                        dte: int = 30, iv: float = 50) -> Dict[str, Any]:
        """
        智能估算权利金（使用共享 Black-Scholes 公式）
        """
        option_type = self._normalize_option_type(option_type)
        
        if strike <= 0 or spot <= 0 or iv <= 0 or dte <= 0:
            return {"estimated_premium": 0, "error": "Invalid parameters"}
        
        bs_result = black_scholes_price(option_type, strike, spot, dte, iv)
        
        return {
            "estimated_premium": bs_result["premium"],
            "delta": bs_result["delta"],
            "gamma": bs_result["gamma"],
            "theta": bs_result["theta"],
            "vega": bs_result["vega"],
            "dte": dte,
            "iv": iv,
            "intrinsic_value": bs_result["intrinsic_value"],
            "time_value": bs_result["time_value"]
        }
    
    def compare_strategies(self, strategies: List[Dict[str, Any]], spot: float) -> Dict[str, Any]:
        """
        对比多个策略（最多 5 个）
        """
        if len(strategies) > 5:
            strategies = strategies[:5]
        
        results = []
        for i, strat in enumerate(strategies):
            legs = strat.get("legs", [])
            dte = strat.get("dte", 30)
            iv = strat.get("iv", 50)
            
            payoff_result = self.calc_payoff(legs, spot)
            score_result = self.calc_strategy_score(legs, spot, dte, iv)
            advice_result = self.generate_strategy_advice(score_result, legs, spot)
            
            results.append({
                "id": i + 1,
                "name": strat.get("name", f"策略{i + 1}"),
                "payoff": payoff_result,
                "score": score_result,
                "advice": advice_result,
                "params": strat
            })
        
        comparison = {
            "strategies": results,
            "best_roi": max(results, key=lambda x: x["score"]["metrics"]["roi_pct"]),
            "best_score": max(results, key=lambda x: x["score"]["total_score"]),
            "safest": max(results, key=lambda x: x["score"]["metrics"]["win_rate_pct"])
        }
        
        return comparison

    def calc_wheel_roi(self, put_strike: float, put_premium: float,
                       call_strike: float, call_premium: float,
                       spot: float, quantity: float = 1.0,
                       put_dte: int = 30, call_dte: int = 30) -> Dict[str, Any]:
        """
        Wheel 策略 ROI 计算（增强版：包含年化收益）
        """
        capital_at_risk = put_strike * quantity
        put_income = put_premium * quantity
        call_income = call_premium * quantity
        
        assigned_cost = (put_strike - put_premium) * quantity
        call_return = (call_strike - assigned_cost / quantity + call_premium) * quantity
        
        wheel_total_income = put_income + call_income
        wheel_roi_pct = (wheel_total_income / capital_at_risk) * 100 if capital_at_risk > 0 else 0
        
        total_dte = put_dte + call_dte
        annualized_roi = (wheel_roi_pct * 365 / total_dte) if total_dte > 0 else 0
        
        prices = self.generate_price_range(spot, 0.3, 100)
        
        put_leg = PayoffParams(option_type="P", strike=put_strike, premium=put_premium, quantity=quantity)
        put_pnl = self.calc_sell_put(put_leg, prices)
        
        call_leg = PayoffParams(option_type="C", strike=call_strike, premium=call_premium, quantity=quantity)
        call_pnl = self.calc_sell_call(call_leg, prices)
        
        stock_pnl = [(price - put_strike) * quantity for price in prices]
        
        wheel_pnl = [put_pnl[i] + call_pnl[i] + stock_pnl[i] for i in range(len(prices))]
        
        win_rate = self._calc_wheel_win_rate(put_strike, put_premium, call_strike, spot, 50, put_dte)
        
        return {
            "prices": prices,
            "put_pnl": [round(v, 2) for v in put_pnl],
            "call_pnl": [round(v, 2) for v in call_pnl],
            "stock_pnl": [round(v, 2) for v in stock_pnl],
            "wheel_pnl": [round(v, 2) for v in wheel_pnl],
            "summary": {
                "put_income": round(put_income, 2),
                "call_income": round(call_income, 2),
                "total_income": round(wheel_total_income, 2),
                "capital_at_risk": round(capital_at_risk, 2),
                "wheel_roi_pct": round(wheel_roi_pct, 2),
                "annualized_roi_pct": round(annualized_roi, 2),
                "assigned_cost": round(assigned_cost, 2),
                "breakeven_stock": round(put_strike - put_premium, 2),
                "total_dte": total_dte,
                "win_rate_pct": round(win_rate * 100, 1)
            },
            "spot": spot
        }
    
    def _calc_wheel_win_rate(self, put_strike: float, put_premium: float,
                             call_strike: float, spot: float,
                             iv: float = 50, put_dte: int = 30) -> float:
        """
        计算 Wheel 策略胜率（Put 不被行权的概率）
        """
        breakeven = put_strike - put_premium
        if breakeven <= 0 or spot <= 0 or put_dte <= 0 or iv <= 0:
            return 0.5
        
        z = (breakeven - spot) / (spot * iv / 100 * math.sqrt(put_dte / 365))
        prob = 1 - self._norm_cdf(z)
        
        return max(0, min(1, prob))
