"""Unit tests for risk math fixes — Volga, POP, Z-Score, weights."""
import math
import pytest


class TestVolgaFormula:
    """Volga = dVega/dSigma = Vega * d1 * d2 / sigma"""

    def test_volga_atm(self):
        """ATM options: d1 ≈ 0.5*sigma*sqrt(T), d2 ≈ -0.5*sigma*sqrt(T), Volga small but nonzero."""
        from services.pressure_test import PressureTestEngine
        S, K, T, r, sigma = 100000, 100000, 30/365, 0.05, 0.50
        greeks = PressureTestEngine.get_greeks(S, K, T, r, sigma, "P")
        vega = greeks["vega"]
        volga = greeks["volga"]
        assert volga != 0, "Volga should be nonzero for ATM option"
        assert abs(volga) < abs(vega) * 10, "Volga magnitude should be proportional to vega"

    def test_volga_positive_for_slight_otm(self):
        """Slightly OTM put: d1*d2 > 0, so Volga > 0 when Vega > 0."""
        from services.pressure_test import PressureTestEngine
        S, K, T, r, sigma = 100000, 95000, 30/365, 0.05, 0.50
        greeks = PressureTestEngine.get_greeks(S, K, T, r, sigma, "P")
        assert greeks["volga"] > 0, "Slightly OTM put should have positive Volga"

    def test_volga_negative_near_atm(self):
        """Near-ATM put: d1 > 0, d2 < 0 -> d1*d2 < 0 -> Volga < 0."""
        from services.pressure_test import PressureTestEngine
        S, K, T, r, sigma = 100000, 100000, 30/365, 0.05, 0.50
        greeks = PressureTestEngine.get_greeks(S, K, T, r, sigma, "P")
        assert greeks["volga"] < 0, "Near-ATM put should have negative Volga"
