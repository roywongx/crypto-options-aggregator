"""Tests for greeks-summary API endpoint."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_greeks_analyzer_import():
    """Verify GreeksAnalyzer can be imported and called."""
    from services.greeks_analyzer import GreeksAnalyzer
    contracts = [
        {"strike": 95000, "mark_iv": 45.0, "dte": 7, "option_type": "P", "oi": 100},
        {"strike": 105000, "mark_iv": 38.0, "dte": 7, "option_type": "C", "oi": 100},
    ]
    result = GreeksAnalyzer.analyze(contracts, 100000)
    assert "greeks_summary" in result
    assert "gex" in result
    assert "analysis" in result
