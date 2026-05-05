"""Test crypto-calibrated threshold system"""
import pytest
from services.crypto_thresholds import CryptoThresholds


class TestFixedThresholds:
    def test_perp_basis_normal(self):
        r = CryptoThresholds.get_fixed_threshold("perp_basis", 5.0)
        assert r["signal"] == "normal"

    def test_perp_basis_high(self):
        r = CryptoThresholds.get_fixed_threshold("perp_basis", 18.0)
        assert r["signal"] == "high"

    def test_perp_basis_extreme(self):
        r = CryptoThresholds.get_fixed_threshold("perp_basis", 35.0)
        assert r["signal"] == "extreme_high"

    def test_perp_basis_negative(self):
        r = CryptoThresholds.get_fixed_threshold("perp_basis", -5.0)
        assert r["signal"] == "negative"

    def test_futures_spot_ratio_crypto_normal(self):
        """加密市场 6x 是正常的（传统金融标准会认为过热）"""
        r = CryptoThresholds.get_fixed_threshold("futures_spot_ratio", 6.0)
        assert r["signal"] == "normal"

    def test_futures_spot_ratio_high(self):
        r = CryptoThresholds.get_fixed_threshold("futures_spot_ratio", 20.0)
        assert r["signal"] == "very_high"

    def test_liquidation_l0(self):
        r = CryptoThresholds.get_fixed_threshold("liquidation_heat", 500_000)
        assert r["signal"] == "L0"

    def test_liquidation_l2(self):
        r = CryptoThresholds.get_fixed_threshold("liquidation_heat", 8_000_000)
        assert r["signal"] == "L2"

    def test_funding_volatility_stable(self):
        r = CryptoThresholds.get_fixed_threshold("funding_volatility", 0.005)
        assert r["signal"] == "stable"

    def test_funding_volatility_extreme(self):
        r = CryptoThresholds.get_fixed_threshold("funding_volatility", 0.15)
        assert r["signal"] == "extreme"

    def test_stablecoin_inflow_strong(self):
        r = CryptoThresholds.get_fixed_threshold("stablecoin_flow", 7.0)
        assert r["signal"] == "strong_inflow"

    def test_stablecoin_outflow(self):
        r = CryptoThresholds.get_fixed_threshold("stablecoin_flow", -7.0)
        assert r["signal"] == "strong_outflow"

    def test_unknown_metric(self):
        r = CryptoThresholds.get_fixed_threshold("nonexistent", 100)
        assert r["signal"] == "unknown"


class TestHybridAssess:
    def test_hybrid_returns_all_fields(self):
        r = CryptoThresholds.hybrid_assess("perp_basis", 12.5, "BTC")
        assert "metric" in r
        assert "value" in r
        assert "percentile" in r
        assert "fixed_threshold" in r
        assert "confidence" in r
        assert "verdict" in r

    def test_hybrid_confidence_levels(self):
        r = CryptoThresholds.hybrid_assess("futures_spot_ratio", 10.0, "BTC")
        assert r["confidence"] in ("high", "medium", "low")
