"""策略分析引擎单元测试"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.strategy_analytics import PayoffEngine, WheelSimulator


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


class TestCalcMultiLegs:
    def setup_method(self):
        self.engine = PayoffEngine()

    def test_bull_put_spread(self):
        """牛市看跌价差: sell 95000P + buy 90000P"""
        legs = [
            {"strike": 95000, "premium": 2000, "option_type": "PUT", "quantity": 1, "side": "sell"},
            {"strike": 90000, "premium": 800, "option_type": "PUT", "quantity": 1, "side": "buy"},
        ]
        result = self.engine.calc_multi_legs(spot=100000, legs=legs)
        assert result["max_profit"] == 1200
        assert result["max_loss"] == -3800
        assert len(result["legs"]) == 2

    def test_short_straddle(self):
        """卖出跨式: sell 100000C + sell 100000P"""
        legs = [
            {"strike": 100000, "premium": 3000, "option_type": "CALL", "quantity": 1, "side": "sell"},
            {"strike": 100000, "premium": 2500, "option_type": "PUT", "quantity": 1, "side": "sell"},
        ]
        result = self.engine.calc_multi_legs(spot=100000, legs=legs)
        assert result["max_profit"] == 5500

    def test_empty_legs_returns_error(self):
        """空 legs 返回错误"""
        result = self.engine.calc_multi_legs(spot=100000, legs=[])
        assert result["success"] is False


class TestProbabilityAndTimeDecay:
    def setup_method(self):
        self.engine = PayoffEngine()

    def test_probability_overlay_returns_density(self):
        """概率叠加返回概率密度"""
        result = self.engine.calc_probability_overlay(
            spot=100000, dte=30, iv=60, strikes=[95000, 100000, 105000]
        )
        assert "density" in result
        assert len(result["density"]) > 0
        total = sum(v for _, v in result["density"])
        assert 0.9 < total < 1.1

    def test_time_decay_multiple_dte(self):
        """时间衰减返回多个 DTE 的价值曲线"""
        result = self.engine.calc_time_decay(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", iv=60, dte_max=60
        )
        assert "curves" in result
        assert len(result["curves"]) >= 4
        for curve in result["curves"]:
            assert len(curve["points"]) > 0


class TestScoreStrategy:
    def setup_method(self):
        self.engine = PayoffEngine()

    def test_sell_put_good_score(self):
        """Sell Put 深度 OTM 应得高分"""
        result = self.engine.score_strategy(
            spot=100000, strike=90000, premium=1500,
            option_type="PUT", dte=30
        )
        assert result["total"] > 0.5
        assert result["recommendation"] in ("BEST", "GOOD")

    def test_sell_put_risky_score(self):
        """Sell Put 接近 spot 应得低分"""
        result = self.engine.score_strategy(
            spot=100000, strike=99000, premium=5000,
            option_type="PUT", dte=7
        )
        assert result["recommendation"] in ("CAUTION", "SKIP", "OK")

    def test_score_has_all_components(self):
        """评分包含所有分量"""
        result = self.engine.score_strategy(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", dte=30
        )
        for key in ("total", "ev", "apr", "liquidity", "theta", "recommendation"):
            assert key in result


class TestWheelSimulator:
    def setup_method(self):
        self.sim = WheelSimulator()

    def test_basic_simulation(self):
        """基本模拟返回所有必要字段"""
        result = self.sim.simulate(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", cycles=6, capital=100000,
            simulations=100
        )
        assert result["success"] is True
        assert "summary" in result
        summary = result["summary"]
        for key in ("mean_roi", "median_roi", "p10", "p90", "win_rate", "max_drawdown_mean"):
            assert key in summary

    def test_roi_reasonable_range(self):
        """ROI 在合理范围内"""
        result = self.sim.simulate(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", cycles=6, capital=100000,
            simulations=500
        )
        mean_roi = result["summary"]["mean_roi"]
        assert -0.10 < mean_roi < 0.30

    def test_sample_paths_count(self):
        """返回正确数量的样本路径"""
        result = self.sim.simulate(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", cycles=4, capital=100000,
            simulations=200
        )
        assert len(result["sample_paths"]) == 5

    def test_roi_distribution_non_empty(self):
        """ROI 分布直方图数据非空"""
        result = self.sim.simulate(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", cycles=6, capital=100000,
            simulations=200
        )
        assert len(result["roi_distribution"]) > 0

    def test_score_present(self):
        """包含策略评分"""
        result = self.sim.simulate(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", cycles=6, capital=100000,
            simulations=100
        )
        assert "score" in result
        assert "total" in result["score"]
        assert "recommendation" in result["score"]
