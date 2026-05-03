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


class TestPOPCalculation:
    """POP (seller-side) uses N(-d2) for calls and N(d2) for puts.
    Seller POP = P(option expires worthless) = probability of keeping premium.
    Call seller: P(S_T ≤ K) = N(-d2).  Put seller: P(S_T ≥ K) = N(d2).
    """

    def test_call_pop_otm(self):
        """OTM Call (spot < strike): seller POP = N(d2) should be > 0.5 (likely to keep premium)."""
        from services.dvol_analyzer import calc_pop
        pop = calc_pop(delta_val=0.30, option_type="CALL", spot=100000, strike=105000, iv=50, dte=30)
        assert pop > 0.5, f"OTM call seller POP should be > 0.5, got {pop}"

    def test_put_pop_otm(self):
        """OTM Put (spot > strike): seller POP = N(-d2) should be > 0.5."""
        from services.dvol_analyzer import calc_pop
        pop = calc_pop(delta_val=-0.30, option_type="PUT", spot=100000, strike=95000, iv=50, dte=30)
        assert pop > 0.5, f"OTM put seller POP should be > 0.5, got {pop}"

    def test_put_pop_itm(self):
        """ITM Put (spot < strike): seller POP = N(-d2) should be < 0.5."""
        from services.dvol_analyzer import calc_pop
        pop = calc_pop(delta_val=-0.70, option_type="PUT", spot=100000, strike=110000, iv=50, dte=30)
        assert pop < 0.5, f"ITM put seller POP should be < 0.5, got {pop}"

    def test_pop_exact_value(self):
        """Verify POP matches exact BS formula: call seller = N(-d2), put seller = N(d2)."""
        from services.dvol_analyzer import calc_pop
        from scipy.stats import norm
        import math
        S, K, iv_pct, dte = 100000, 100000, 50, 30
        iv = iv_pct / 100.0
        T = dte / 365.0
        d1 = (math.log(S / K) + (0.5 * iv ** 2) * T) / (iv * math.sqrt(T))
        d2 = d1 - iv * math.sqrt(T)
        for ot, expected in [("CALL", norm.cdf(-d2)), ("PUT", norm.cdf(d2))]:
            pop = calc_pop(delta_val=0.5, option_type=ot, spot=S, strike=K, iv=iv_pct, dte=dte)
            assert abs(pop - expected) < 1e-6, f"{ot} POP {pop} != expected {expected}"

    def test_pop_bounds(self):
        """POP should always be in [0, 1]."""
        from services.dvol_analyzer import calc_pop
        for ot in ("CALL", "PUT"):
            for delta in (0.1, 0.5, 0.9, -0.1, -0.5, -0.9):
                pop = calc_pop(delta_val=delta, option_type=ot, spot=100000, strike=100000, iv=50, dte=30)
                assert 0 <= pop <= 1, f"POP out of bounds: {pop} for {ot} delta={delta}"
