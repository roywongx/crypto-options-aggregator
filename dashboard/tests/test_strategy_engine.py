"""策略引擎单元测试"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.strategy_engine import (
    ContractFilter,
    FilterResult,
    StrategyScorer,
    ScoreResult,
    StrategyEngine,
    RecommendationResult,
)


def _make_contract(**overrides):
    base = {
        "option_type": "P",
        "strike": 90000,
        "dte": 30,
        "delta": -0.25,
        "premium_usd": 500,
        "open_interest": 500,
        "spread_pct": 2.0,
        "platform": "Deribit",
        "expiry": "2026-06-27",
        "apr": 15.0,
        "mark_iv": 45.0,
        "volume": 100,
    }
    base.update(overrides)
    return base


# ── Hard Filter ──────────────────────────────────────────────────────────────


class TestHardFilter:
    def test_passes_valid_contract(self):
        f = ContractFilter()
        assert len(f._hard_filter([_make_contract()])) == 1

    def test_rejects_low_oi(self):
        f = ContractFilter()
        assert len(f._hard_filter([_make_contract(open_interest=5)])) == 0

    def test_rejects_high_spread(self):
        f = ContractFilter()
        assert len(f._hard_filter([_make_contract(spread_pct=30.0)])) == 0

    def test_rejects_expired(self):
        f = ContractFilter()
        assert len(f._hard_filter([_make_contract(dte=0)])) == 0

    def test_rejects_zero_premium(self):
        f = ContractFilter()
        assert len(f._hard_filter([_make_contract(premium_usd=0)])) == 0


# ── DVOL Filter ──────────────────────────────────────────────────────────────


class TestDvolFilter:
    def test_low_vol_widens_delta(self):
        f = ContractFilter()
        params = f.get_dvol_adjusted_params({}, {"z_score": -1.5, "current": 25})
        assert params["max_delta"] == 0.40
        assert params["min_dte"] == 7
        assert params["max_dte"] == 60

    def test_normal_vol_keeps_defaults(self):
        f = ContractFilter()
        params = f.get_dvol_adjusted_params({}, {"z_score": 0.0, "current": 45})
        assert params["max_delta"] == 0.30

    def test_high_vol_tightens_delta(self):
        f = ContractFilter()
        params = f.get_dvol_adjusted_params({}, {"z_score": 2.0, "current": 80})
        assert params["max_delta"] == 0.25

    def test_overrides_take_precedence(self):
        f = ContractFilter()
        params = f.get_dvol_adjusted_params({"max_delta": 0.35}, {"z_score": 2.0, "current": 80})
        assert params["max_delta"] == 0.35

    def test_adjustments_recorded(self):
        f = ContractFilter()
        result = f.filter([], {}, {"z_score": 2.0, "current": 80})
        assert "max_delta" in result.dvol_adjustments


# ── Strategy Filter ──────────────────────────────────────────────────────────


class TestStrategyFilter:
    def test_new_put_filters_otm_only(self):
        f = ContractFilter()
        contracts = [_make_contract(strike=90000), _make_contract(strike=105000)]
        result = f.strategy_filter(contracts, "new", "PUT", 100000, None)
        assert len(result) == 1

    def test_roll_put_filters_below_current(self):
        f = ContractFilter()
        contracts = [_make_contract(strike=90000), _make_contract(strike=96000)]
        result = f.strategy_filter(contracts, "roll", "PUT", 100000, 95000)
        assert len(result) == 1

    def test_empty_result_has_reason(self):
        f = ContractFilter()
        result = f.filter([], {}, {"z_score": 0, "current": 45})
        assert result.empty_reason != ""


# ── Strategy Scorer ──────────────────────────────────────────────────────────


class TestStrategyScorer:
    def test_ev_put_positive(self):
        s = StrategyScorer()
        c = _make_contract()
        score = s.score(c, 100000, 0.2)
        assert 0 <= score.ev <= 1
        assert 0 <= score.total <= 1
        assert score.recommendation in ("BEST", "GOOD", "OK", "CAUTION", "SKIP")

    def test_high_apr_gets_high_apr_score(self):
        s = StrategyScorer()
        score = s.score(_make_contract(apr=80.0), 100000, 0.2)
        assert score.apr >= 0.8

    def test_recommendation_thresholds(self):
        s = StrategyScorer()
        assert s._classify_score(0.80) == "BEST"
        assert s._classify_score(0.60) == "GOOD"
        assert s._classify_score(0.45) == "OK"
        assert s._classify_score(0.30) == "CAUTION"
        assert s._classify_score(0.10) == "SKIP"


# ── Strategy Engine ──────────────────────────────────────────────────────────


class TestStrategyEngine:
    def _make_contracts(self, n=10):
        return [
            _make_contract(
                strike=90000 - i * 2000,
                premium_usd=500 + i * 100,
                delta=-0.15 - i * 0.03,
                dte=20 + i * 5,
                open_interest=300 + i * 200,
                spread_pct=2.0 + i * 0.5,
                apr=10.0 + i * 3.0,
                mark_iv=40.0 + i * 2.0,
                volume=100 + i * 50,
            )
            for i in range(n)
        ]

    def test_recommend_returns_sorted_results(self):
        engine = StrategyEngine()
        result = engine.recommend(
            self._make_contracts(10),
            "BTC",
            "new",
            "PUT",
            100000,
            50000,
            5,
            {"z_score": 0, "current": 45},
        )
        assert result.success is True
        assert len(result.recommendations) <= 5
        scores = [r["scores"]["total"] for r in result.recommendations]
        assert scores == sorted(scores, reverse=True)

    def test_recommend_empty_contracts(self):
        engine = StrategyEngine()
        result = engine.recommend(
            [], "BTC", "new", "PUT", 100000, 50000, 10, {"z_score": 0, "current": 45}
        )
        assert result.success is False

    def test_grid_generates_levels(self):
        engine = StrategyEngine()
        result = engine.grid(
            self._make_contracts(20),
            "BTC",
            100000,
            50000,
            5,
            3.0,
            {"z_score": 0, "current": 45},
        )
        assert result.success is True
        assert len(result.recommendations) <= 5
