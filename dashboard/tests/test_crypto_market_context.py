"""Test crypto market context builder"""
import pytest
import services.crypto_market_context as cmc
from services.crypto_market_context import CryptoMarketContext


def _clear_cache():
    """Reset module-level cache so each test gets a fresh build."""
    cmc._context_cache = {}
    cmc._cache_time = None


class TestMarketContext:
    def test_build_basic(self):
        _clear_cache()
        ctx = CryptoMarketContext.build({"spot": 90000, "dvol": 65, "dvol_z": 1.2, "fear_greed": 45})
        assert ctx["cycle"]["phase"] in [
            "熊市早期", "横盘整理（方向不明）", "牛市早期/积累", "牛市中期", "牛市顶部（高估值风险）"
        ]
        assert ctx["cycle"]["dvol_regime"] in ["恐慌波动", "高波动", "中波动", "低波动", "未知"]
        assert ctx["structure"]["perp_dominance"] is True
        assert len(ctx["structural_knowledge"]) == 7

    def test_to_prompt_text(self):
        _clear_cache()
        ctx = CryptoMarketContext.build({"spot": 90000, "dvol": 65, "dvol_z": 1.2})
        text = CryptoMarketContext.to_prompt_text(ctx)
        assert "加密市场结构背景" in text
        assert "永续合约" in text
        assert "BTC市占率" in text

    def test_cache_returns_same_object(self):
        _clear_cache()
        ctx1 = CryptoMarketContext.build({"spot": 90000, "dvol": 50, "dvol_z": 0})
        ctx2 = CryptoMarketContext.build({"spot": 91000, "dvol": 50, "dvol_z": 0})
        assert ctx1 is ctx2

    def test_warnings_on_high_basis(self):
        _clear_cache()
        ctx = CryptoMarketContext.build({
            "spot": 90000, "dvol": 65, "dvol_z": 1.2,
            "perp_basis": {"basis_annualized": 20, "hybrid_assessment": {"percentile": {"pct": 92}}},
        })
        warnings = ctx.get("warnings", [])
        assert any("永续基差" in w for w in warnings)

    def test_warnings_on_divergence(self):
        _clear_cache()
        ctx = CryptoMarketContext.build({
            "spot": 90000, "dvol": 65, "dvol_z": 1.2,
            "oi_price_divergence": {"divergence": "bearish", "divergence_label": "OI↑价格↓（空头加仓=看空）"},
        })
        warnings = ctx.get("warnings", [])
        assert any("背离" in w for w in warnings)

    def test_empty_data_sensible_defaults(self):
        _clear_cache()
        ctx = CryptoMarketContext.build({})
        assert ctx["cycle"]["phase"] is not None
        assert ctx["cycle"]["dvol_regime"] == "未知"
        assert ctx["warnings"] == []

    def test_market_cycle_phases(self):
        # 牛市顶部
        assert "牛市顶部" in CryptoMarketContext._determine_cycle_phase(4.0, 0.8, 100000)
        # 熊市底部
        assert "熊市底部" in CryptoMarketContext._determine_cycle_phase(-3.0, -0.6, 20000)
        # 横盘：mvrv_z=-1.5, nupl=-0.5 恰好落在横盘区间
        assert "横盘" in CryptoMarketContext._determine_cycle_phase(-1.5, -0.5, 30000)
