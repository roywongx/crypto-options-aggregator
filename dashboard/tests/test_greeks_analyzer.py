"""Tests for Greeks Analyzer service."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.greeks_analyzer import GreeksAnalyzer


def _make_contract(strike, iv, dte, option_type, oi=100, premium=500):
    return {
        "strike": strike, "mark_iv": iv, "dte": dte,
        "option_type": option_type, "oi": oi, "premium_usd": premium
    }


class TestExtractContracts:
    def test_extracts_valid_contracts(self):
        contracts = [
            _make_contract(95000, 45.0, 7, "P"),
            _make_contract(105000, 38.0, 7, "C"),
        ]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["contract_count"] == 2

    def test_normalizes_decimal_iv(self):
        contracts = [_make_contract(100000, 0.45, 7, "P")]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["contract_count"] == 1

    def test_filters_invalid_iv(self):
        contracts = [
            _make_contract(100000, 0, 7, "P"),
            _make_contract(100000, -5, 7, "P"),
            _make_contract(100000, 250, 7, "P"),
        ]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["contract_count"] == 0

    def test_filters_zero_strike(self):
        contracts = [_make_contract(0, 45.0, 7, "P")]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["contract_count"] == 0

    def test_filters_zero_dte(self):
        contracts = [_make_contract(100000, 45.0, 0, "P")]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["contract_count"] == 0

    def test_filters_low_oi(self):
        contracts = [_make_contract(100000, 45.0, 7, "P", oi=0)]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["contract_count"] == 0

    def test_counts_puts_and_calls(self):
        contracts = [
            _make_contract(95000, 45.0, 7, "P"),
            _make_contract(105000, 38.0, 7, "C"),
            _make_contract(90000, 50.0, 7, "P"),
        ]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["put_count"] == 2
        assert result["call_count"] == 1

    def test_uses_fallback_iv_field(self):
        contracts = [{"strike": 100000, "iv": 0.45, "dte": 7, "option_type": "P", "oi": 100}]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["contract_count"] == 1

    def test_uses_fallback_oi_field(self):
        contracts = [{"strike": 100000, "mark_iv": 45.0, "dte": 7, "option_type": "P", "open_interest": 200}]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["contract_count"] == 1

    def test_empty_contracts_returns_empty(self):
        result = GreeksAnalyzer.analyze([], 100000)
        assert result["contract_count"] == 0
        assert result["analysis"] is None


class TestGreeksCalculation:
    def _make_contracts(self):
        """Generate contracts across 2 expiries with known structure."""
        contracts = []
        spot = 100000
        for dte in [7, 14]:
            for strike_pct in [-10, -5, 0, 5, 10]:
                strike = spot * (1 + strike_pct / 100)
                contracts.append(_make_contract(strike, 45.0, dte, "P"))
                contracts.append(_make_contract(strike, 38.0, dte, "C"))
        return contracts

    def test_greeks_summary_has_per_contract(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        gs = result["greeks_summary"]
        assert "per_contract" in gs
        for key in ["delta", "gamma", "theta", "vega"]:
            assert key in gs["per_contract"]

    def test_greeks_summary_has_total_exposure(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        gs = result["greeks_summary"]
        assert "total_exposure" in gs
        for key in ["delta", "gamma", "theta", "vega"]:
            assert key in gs["total_exposure"]

    def test_total_delta_nonzero(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        total = result["greeks_summary"]["total_exposure"]
        assert total["delta"] != 0

    def test_total_theta_negative(self):
        """Theta should be negative (time decay)."""
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        total = result["greeks_summary"]["total_exposure"]
        assert total["theta"] < 0

    def test_by_expiry_populated(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        assert len(result["by_expiry"]) == 2
        for entry in result["by_expiry"]:
            assert "dte" in entry
            assert "delta" in entry
            assert "gamma" in entry
            assert "theta" in entry
            assert "vega" in entry
            assert "atm_iv" in entry
            assert "contract_count" in entry
            assert "total_oi" in entry

    def test_by_expiry_sorted_by_dte(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        dtes = [e["dte"] for e in result["by_expiry"]]
        assert dtes == sorted(dtes)


class TestGEX:
    def _make_contracts(self):
        """Generate contracts with high OI at specific strikes for GEX testing."""
        contracts = []
        spot = 100000
        for dte in [7, 14]:
            for strike_pct in [-10, -5, 0, 5, 10]:
                strike = spot * (1 + strike_pct / 100)
                # Higher OI near ATM for pin risk testing
                oi = 500 if abs(strike_pct) <= 5 else 100
                contracts.append(_make_contract(strike, 45.0, dte, "P", oi=oi))
                contracts.append(_make_contract(strike, 38.0, dte, "C", oi=oi))
        return contracts

    def test_gex_by_strike_populated(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        gex = result["gex"]
        assert "by_strike" in gex
        assert len(gex["by_strike"]) > 0

    def test_gex_by_strike_has_required_fields(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        for entry in result["gex"]["by_strike"]:
            assert "strike" in entry
            assert "call_gex" in entry
            assert "put_gex" in entry
            assert "net_gex" in entry

    def test_gex_totals_present(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        gex = result["gex"]
        assert "total_gex" in gex
        assert "flip_strike" in gex
        assert "pin_strike" in gex
        assert "pin_risk_level" in gex

    def test_pin_strike_near_high_oi(self):
        """Pin strike should be near the highest OI concentration."""
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        pin = result["gex"]["pin_strike"]
        # Highest OI is at 0% and +-5% strikes (100000, 95000, 105000)
        assert pin in [95000, 100000, 105000]

    def test_flip_strike_is_number(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        flip = result["gex"]["flip_strike"]
        assert isinstance(flip, (int, float))

    def test_pin_risk_level_valid(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        assert result["gex"]["pin_risk_level"] in ["HIGH", "MEDIUM", "LOW"]


class TestScenariosAndRisk:
    def _make_contracts(self):
        contracts = []
        spot = 100000
        for dte in [7, 14]:
            for strike_pct in [-10, -5, 0, 5, 10]:
                strike = spot * (1 + strike_pct / 100)
                contracts.append(_make_contract(strike, 45.0, dte, "P"))
                contracts.append(_make_contract(strike, 38.0, dte, "C"))
        return contracts

    def test_scenarios_populated(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        s = result["scenarios"]
        for key in ["down_10pct", "up_10pct", "iv_up_5pct", "iv_down_5pct", "pin_scenario"]:
            assert key in s

    def test_down_10pct_negative(self):
        """Down scenario sign should be opposite to total delta sign."""
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        down = result["scenarios"]["down_10pct"]
        total_delta = result["greeks_summary"]["total_exposure"]["delta"]
        assert isinstance(down, (int, float))
        if total_delta > 0:
            assert down < 0
        elif total_delta < 0:
            assert down > 0

    def test_pin_scenario_has_fields(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        ps = result["scenarios"]["pin_scenario"]
        assert "pin_strike" in ps
        assert "pin_oi" in ps
        assert "avg_oi" in ps
        assert "concentration" in ps

    def test_risk_ratings_populated(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        ps = result["scenarios"]["pin_scenario"]
        assert ps["concentration"] >= 1.0, "Pin strike OI should be at least average"
        assert ps["pin_oi"] >= ps["avg_oi"], "Pin OI should be >= average OI"
        assert result["gex"]["pin_risk_level"] in ["HIGH", "MEDIUM", "LOW"]


class TestAnalysis:
    def _make_contracts(self):
        contracts = []
        spot = 100000
        for dte in [7, 14]:
            for strike_pct in [-10, -5, 0, 5, 10]:
                strike = spot * (1 + strike_pct / 100)
                contracts.append(_make_contract(strike, 45.0, dte, "P"))
                contracts.append(_make_contract(strike, 38.0, dte, "C"))
        return contracts

    def test_analysis_populated(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        assert result["analysis"] is not None

    def test_analysis_has_gex_regime(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        a = result["analysis"]
        assert "gex_regime" in a
        assert "state" in a["gex_regime"]
        assert "label" in a["gex_regime"]

    def test_analysis_has_pin_risk(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        a = result["analysis"]
        assert "pin_risk" in a
        assert "level" in a["pin_risk"]

    def test_analysis_has_market_state(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        a = result["analysis"]
        assert "market_state" in a
        assert "state" in a["market_state"]
        assert a["market_state"]["state"] in [
            "TRENDING_UP", "TRENDING_DOWN", "MEAN_REVERTING",
            "PIN_RISK", "VOLATILE", "CALM"
        ]

    def test_analysis_has_risk_ratings(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        a = result["analysis"]
        assert "risk_ratings" in a
        for greek in ["delta", "gamma", "theta", "vega"]:
            assert greek in a["risk_ratings"]
            assert "level" in a["risk_ratings"][greek]
            assert a["risk_ratings"][greek]["level"] in ["HIGH", "MEDIUM", "LOW"]

    def test_analysis_has_interpretation(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        a = result["analysis"]
        assert "interpretation" in a
        assert isinstance(a["interpretation"], list)
        assert len(a["interpretation"]) > 0

    def test_analysis_has_hedge_suggestions(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        a = result["analysis"]
        assert "hedge_suggestions" in a
        assert isinstance(a["hedge_suggestions"], list)

    def test_hedge_suggestion_has_fields(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        for s in result["analysis"]["hedge_suggestions"]:
            assert "type" in s
            assert "title" in s
            assert "body" in s
            assert "action" in s
            assert "confidence" in s

    def test_atm_iv_from_nearest_strike(self):
        """ATM IV should come from the strike closest to spot."""
        contracts = [
            _make_contract(90000, 60.0, 7, "P"),   # far OTM put, high IV
            _make_contract(100000, 40.0, 7, "P"),   # ATM put
            _make_contract(100000, 38.0, 7, "C"),   # ATM call
            _make_contract(110000, 55.0, 7, "C"),   # far OTM call, high IV
        ]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        # Market state should use ATM IV (~38-40), not far OTM IV (~55-60)
        # If ATM IV was wrong, market state could be VOLATILE instead of CALM/MEAN_REVERTING
        a = result["analysis"]
        assert a is not None
        # With IV ~38-40 and GEX likely positive, should not be VOLATILE (requires atm_iv > 40)
        assert a["market_state"]["state"] != "VOLATILE"
