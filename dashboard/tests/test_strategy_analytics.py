"""策略分析引擎单元测试"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.strategy_analytics import PayoffEngine


class TestCalcSingle:
    def setup_method(self):
        self.engine = PayoffEngine()

    def test_sell_put_profit_when_above_strike(self):
        """Sell Put: 价格在行权价之上，利润 = 权利金"""
        result = self.engine.calc_single(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", dte=30, quantity=1
        )
        assert result["max_profit"] == 2000
        assert result["breakeven"] == 93000

    def test_sell_put_loss_when_below_strike(self):
        """Sell Put: 价格跌破行权价，亏损递增"""
        result = self.engine.calc_single(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", dte=30, quantity=1
        )
        curve = {p: v for p, v in zip(result["payoff_curve"]["prices"], result["payoff_curve"]["pnl"])}
        assert curve[90000] == -3000

    def test_sell_call_profit_when_below_strike(self):
        """Sell Call: 价格在行权价之下，利润 = 权利金"""
        result = self.engine.calc_single(
            spot=100000, strike=105000, premium=1500,
            option_type="CALL", dte=30, quantity=1
        )
        assert result["max_profit"] == 1500
        assert result["breakeven"] == 106500

    def test_buy_put_profit_when_below_strike(self):
        """Buy Put: 价格跌破行权价盈利"""
        result = self.engine.calc_single(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", dte=30, quantity=1, side="buy"
        )
        assert result["max_loss"] == -2000

    def test_quantity_scaling(self):
        """数量缩放"""
        result1 = self.engine.calc_single(spot=100000, strike=95000, premium=2000, option_type="PUT", dte=30, quantity=1)
        result2 = self.engine.calc_single(spot=100000, strike=95000, premium=2000, option_type="PUT", dte=30, quantity=2)
        assert result2["max_profit"] == result1["max_profit"] * 2

    def test_payoff_curve_has_zones(self):
        """返回 profit/loss 区间"""
        result = self.engine.calc_single(spot=100000, strike=95000, premium=2000, option_type="PUT", dte=30)
        assert "profit_range" in result["zones"]
        assert "loss_range" in result["zones"]
