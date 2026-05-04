"""Tests for IV Smile analyzer service."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.iv_smile import IVSmileAnalyzer


def _make_contract(strike, iv, dte, option_type, oi=100, volume=10):
    return {
        "strike": strike, "mark_iv": iv, "dte": dte,
        "option_type": option_type, "oi": oi, "volume": volume
    }


class TestExtractAndNormalize:
    def test_extracts_valid_contracts(self):
        contracts = [
            _make_contract(95000, 45.0, 7, "P"),
            _make_contract(105000, 38.0, 7, "C"),
        ]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        assert result["smiles"] != {}
        assert "dte_7" in result["smiles"]

    def test_normalizes_decimal_iv(self):
        contracts = [_make_contract(100000, 0.45, 7, "P")]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        point = result["smiles"]["dte_7"]["puts"][0]
        assert point["iv"] == 45.0

    def test_filters_invalid_iv(self):
        contracts = [
            _make_contract(100000, 0, 7, "P"),
            _make_contract(100000, -5, 7, "P"),
            _make_contract(100000, 250, 7, "P"),
        ]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        assert result["smiles"] == {}

    def test_filters_zero_strike(self):
        contracts = [_make_contract(0, 45.0, 7, "P")]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        assert result["smiles"] == {}

    def test_filters_zero_dte(self):
        contracts = [_make_contract(100000, 45.0, 0, "P")]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        assert result["smiles"] == {}

    def test_filters_low_oi(self):
        contracts = [_make_contract(100000, 45.0, 7, "P", oi=0)]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        assert result["smiles"] == {}

    def test_computes_moneyness(self):
        contracts = [_make_contract(90000, 45.0, 7, "P")]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        point = result["smiles"]["dte_7"]["puts"][0]
        assert point["moneyness"] == -10.0

    def test_separates_puts_and_calls(self):
        contracts = [
            _make_contract(95000, 45.0, 7, "P"),
            _make_contract(105000, 38.0, 7, "C"),
        ]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        smile = result["smiles"]["dte_7"]
        assert len(smile["puts"]) == 1
        assert len(smile["calls"]) == 1
        assert smile["puts"][0]["type"] == "P"
        assert smile["calls"][0]["type"] == "C"

    def test_takes_nearest_3_expiries(self):
        contracts = []
        for dte in [3, 7, 14, 30, 60]:
            contracts.append(_make_contract(95000, 45.0, dte, "P"))
            contracts.append(_make_contract(105000, 38.0, dte, "C"))
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        assert len(result["smiles"]) == 3
        assert "dte_3" in result["smiles"]
        assert "dte_7" in result["smiles"]
        assert "dte_14" in result["smiles"]

    def test_empty_contracts_returns_empty(self):
        result = IVSmileAnalyzer.analyze([], 100000)
        assert result["smiles"] == {}
        assert result["analysis"] is None

    def test_uses_fallback_iv_field(self):
        contracts = [{"strike": 100000, "iv": 0.45, "dte": 7, "option_type": "P", "oi": 100}]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        assert result["smiles"]["dte_7"]["puts"][0]["iv"] == 45.0


class TestMetrics:
    def _make_smile_data(self, skew=0):
        """Generate synthetic smile data with controllable skew."""
        contracts = []
        spot = 100000
        for dte in [7, 14]:
            for strike_pct in [-10, -7, -5, -3, 0, 3, 5, 7, 10]:
                strike = spot * (1 + strike_pct / 100)
                # Put IV increases as strike decreases (positive skew)
                put_iv = 40 + skew * abs(strike_pct) / 10 + (0 if strike_pct >= 0 else 5)
                call_iv = 40 + (0 if strike_pct <= 0 else 3)
                contracts.append(_make_contract(strike, put_iv, dte, "P"))
                contracts.append(_make_contract(strike, call_iv, dte, "C"))
        return IVSmileAnalyzer.analyze(contracts, spot)

    def test_atm_iv_present(self):
        result = self._make_smile_data()
        assert result["analysis"] is not None
        assert "atm_iv" in result["analysis"]["metrics"]
        assert result["analysis"]["metrics"]["atm_iv"] > 0

    def test_skew_25d_positive_for_put_heavy(self):
        result = self._make_smile_data(skew=2)
        assert result["analysis"]["metrics"]["skew_25d"] > 0

    def test_put_skew_pct_calculated(self):
        result = self._make_smile_data(skew=2)
        assert result["analysis"]["metrics"]["put_skew_pct"] > 0

    def test_call_skew_pct_calculated(self):
        result = self._make_smile_data(skew=0)
        assert "call_skew_pct" in result["analysis"]["metrics"]

    def test_skew_slope_present(self):
        result = self._make_smile_data()
        assert "skew_slope" in result["analysis"]["metrics"]

    def test_curvature_present(self):
        result = self._make_smile_data()
        assert "curvature" in result["analysis"]["metrics"]

    def test_by_expiry_has_metrics(self):
        result = self._make_smile_data()
        by_expiry = result["analysis"]["by_expiry"]
        assert len(by_expiry) > 0
        for entry in by_expiry:
            assert "atm_iv" in entry
            assert "skew_25d" in entry
            assert "form" in entry
            assert "point_count" in entry


class TestSentiment:
    def test_panic_on_extreme_put_skew(self):
        sentiment = IVSmileAnalyzer._assess_sentiment(20, 35, -2)
        assert sentiment["state"] == "PANIC"

    def test_fear_on_high_put_skew(self):
        sentiment = IVSmileAnalyzer._assess_sentiment(10, 18, -1)
        assert sentiment["state"] == "FEAR"

    def test_neutral_on_low_skew(self):
        sentiment = IVSmileAnalyzer._assess_sentiment(1, 2, 1)
        assert sentiment["state"] == "NEUTRAL"

    def test_greed_on_negative_skew(self):
        sentiment = IVSmileAnalyzer._assess_sentiment(-5, -2, 8)
        assert sentiment["state"] == "GREED"

    def test_euphoria_on_extreme_negative_skew(self):
        sentiment = IVSmileAnalyzer._assess_sentiment(-12, -3, 20)
        assert sentiment["state"] == "EUPHORIA"

    def test_sentiment_has_required_fields(self):
        sentiment = IVSmileAnalyzer._assess_sentiment(0, 0, 0)
        for field in ["state", "label", "icon", "color"]:
            assert field in sentiment


class TestRecommendations:
    def test_sell_put_on_fear(self):
        sentiment = {"state": "FEAR", "label": "市场恐慌", "icon": "😰", "color": "#ef4444"}
        metrics = {"atm_iv": 50, "skew_25d": 10, "put_skew_pct": 20, "call_skew_pct": -2, "skew_slope": 0.1, "curvature": 3}
        recs = IVSmileAnalyzer._build_recommendations("put_skew", metrics, sentiment)
        assert len(recs) > 0
        assert any(r["type"] == "sell_put" for r in recs)

    def test_high_confidence_on_extreme_skew(self):
        sentiment = {"state": "FEAR", "label": "市场恐慌", "icon": "😰", "color": "#ef4444"}
        metrics = {"atm_iv": 50, "skew_25d": 10, "put_skew_pct": 20, "call_skew_pct": -2, "skew_slope": 0.1, "curvature": 3}
        recs = IVSmileAnalyzer._build_recommendations("put_skew", metrics, sentiment)
        high_recs = [r for r in recs if r["confidence"] == "HIGH"]
        assert len(high_recs) > 0

    def test_recommendation_has_required_fields(self):
        sentiment = {"state": "NEUTRAL", "label": "中性", "icon": "😐", "color": "#9497a9"}
        metrics = {"atm_iv": 30, "skew_25d": 1, "put_skew_pct": 2, "call_skew_pct": 1, "skew_slope": 0.01, "curvature": 1}
        recs = IVSmileAnalyzer._build_recommendations("flat", metrics, sentiment)
        for r in recs:
            for field in ["type", "title", "body", "action", "confidence"]:
                assert field in r

    def test_iron_condor_on_flat_high_iv(self):
        sentiment = {"state": "NEUTRAL", "label": "中性", "icon": "😐", "color": "#9497a9"}
        metrics = {"atm_iv": 55, "skew_25d": 1, "put_skew_pct": 2, "call_skew_pct": 1, "skew_slope": 0.01, "curvature": 1}
        recs = IVSmileAnalyzer._build_recommendations("flat", metrics, sentiment)
        assert any(r["type"] == "iron_condor" for r in recs)

    def test_long_straddle_on_flat_low_iv(self):
        sentiment = {"state": "NEUTRAL", "label": "中性", "icon": "😐", "color": "#9497a9"}
        metrics = {"atm_iv": 20, "skew_25d": 0, "put_skew_pct": 1, "call_skew_pct": 1, "skew_slope": 0.005, "curvature": 0.5}
        recs = IVSmileAnalyzer._build_recommendations("flat", metrics, sentiment)
        assert any(r["type"] == "long_straddle" for r in recs)
