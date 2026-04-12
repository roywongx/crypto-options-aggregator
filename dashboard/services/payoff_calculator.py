"""
Payoff 可视化计算引擎
支持 Sell Put / Sell Call / Wheel 策略的盈亏图计算
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class PayoffParams:
    option_type: str
    strike: float
    premium: float
    quantity: float = 1.0
    spot: float = 0.0


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
            params = PayoffParams(
                option_type=leg.get("option_type", "P"),
                strike=leg.get("strike", spot),
                premium=leg.get("premium", 0),
                quantity=leg.get("quantity", 1),
                spot=spot
            )

            if leg.get("direction") == "sell":
                if params.option_type in ("P", "PUT"):
                    pnl = self.calc_sell_put(params, prices)
                else:
                    pnl = self.calc_sell_call(params, prices)
            else:
                if params.option_type in ("P", "PUT"):
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
                "option_type": params.option_type,
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

    def calc_wheel_roi(self, put_strike: float, put_premium: float,
                       call_strike: float, call_premium: float,
                       spot: float, quantity: float = 1.0) -> Dict[str, Any]:
        capital_at_risk = put_strike * quantity
        put_income = put_premium * quantity
        call_income = call_premium * quantity

        assigned_cost = (put_strike - put_premium) * quantity
        call_return = (call_strike - assigned_cost / quantity + call_premium) * quantity

        wheel_total_income = put_income + call_income
        wheel_roi_pct = (wheel_total_income / capital_at_risk) * 100 if capital_at_risk > 0 else 0

        prices = self.generate_price_range(spot, 0.3, 100)

        put_leg = PayoffParams(option_type="P", strike=put_strike, premium=put_premium, quantity=quantity)
        put_pnl = self.calc_sell_put(put_leg, prices)

        call_leg = PayoffParams(option_type="C", strike=call_strike, premium=call_premium, quantity=quantity)
        call_pnl = self.calc_sell_call(call_leg, prices)

        stock_pnl = [(price - put_strike) * quantity for price in prices]

        wheel_pnl = [put_pnl[i] + call_pnl[i] + stock_pnl[i] for i in range(len(prices))]

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
                "assigned_cost": round(assigned_cost, 2),
                "breakeven_stock": round(put_strike - put_premium, 2)
            },
            "spot": spot
        }
