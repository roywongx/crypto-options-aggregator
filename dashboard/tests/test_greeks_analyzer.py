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
