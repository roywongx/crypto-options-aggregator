"""Tests for LLM Analyst Engine"""
import json
import pytest
from unittest.mock import patch, MagicMock


class TestPrepareContext:
    """Test _prepare_context data gathering"""

    @patch("services.llm_analyst._gather_market_data")
    @patch("services.llm_analyst.OnChainMetrics")
    @patch("services.llm_analyst.DerivativeMetrics")
    @patch("services.llm_analyst.get_all_macro_data")
    @patch("services.llm_analyst.IVTermStructureAnalyzer")
    def test_prepare_context_returns_all_sections(
        self, mock_iv, mock_macro, mock_deriv, mock_onchain, mock_gather
    ):
        from services.llm_analyst import LLMAnalystEngine

        mock_gather.return_value = {
            "currency": "BTC",
            "spot": 100000,
            "dvol": {"current": 55.0, "z_score": 0.5, "percentile": 60, "signal": "neutral", "trend": "stable"},
            "large_trades": [{"side": "buy", "notional_usd": 500000}],
            "contracts": [{"strike": 95000, "premium_usd": 2000, "option_type": "P", "dte": 30, "delta": -0.25, "open_interest": 100, "spread_pct": 2.0, "apr": 25.0}],
            "max_pain": 98000,
            "risk_status": "GREEN",
            "risk_label": "🟢 安全",
            "risk_desc": "正常",
            "errors": [],
        }
        mock_onchain.get_all_metrics.return_value = {"mvrv": 1.5, "nupl": 0.4, "convergence_score": 60}
        mock_deriv.get_all_metrics.return_value = {"sharpe_7d": 0.5, "vol_ratio": 1.2, "overheating": False}
        mock_macro.return_value = {"fear_greed": {"value": 50, "classification": "Neutral"}, "funding_rate": {"current_rate": 0.01}}
        mock_iv.return_value.analyze.return_value = {"state": "contango", "slope": 0.02, "curvature": 0.01, "vrp": 5.0}

        engine = LLMAnalystEngine()
        ctx = engine._prepare_context("BTC")

        assert ctx["currency"] == "BTC"
        assert ctx["spot"] == 100000
        assert "dvol" in ctx
        assert "onchain" in ctx
        assert "derivatives" in ctx
        assert "macro" in ctx
        assert "iv_term" in ctx
        assert "contracts" in ctx
        assert "large_trades" in ctx
        assert "max_pain" in ctx
        assert "risk" in ctx

    @patch("services.llm_analyst._gather_market_data")
    @patch("services.llm_analyst.OnChainMetrics")
    @patch("services.llm_analyst.DerivativeMetrics")
    @patch("services.llm_analyst.get_all_macro_data")
    @patch("services.llm_analyst.IVTermStructureAnalyzer")
    def test_prepare_context_handles_missing_data(self, mock_iv, mock_macro, mock_deriv, mock_onchain, mock_gather):
        from services.llm_analyst import LLMAnalystEngine

        mock_gather.return_value = {
            "currency": "BTC", "spot": 0, "dvol": {}, "large_trades": [],
            "contracts": [], "max_pain": 0, "risk_status": "UNKNOWN",
            "risk_label": "", "risk_desc": "", "errors": ["spot failed"],
        }
        mock_onchain.get_all_metrics.side_effect = ConnectionError("timeout")
        mock_deriv.get_all_metrics.side_effect = ConnectionError("api down")
        mock_macro.return_value = {}
        mock_iv.return_value.analyze.return_value = {}

        engine = LLMAnalystEngine()
        ctx = engine._prepare_context("BTC")

        assert ctx["currency"] == "BTC"
        assert ctx["spot"] == 0
        assert ctx["onchain"] == {}
        assert ctx["derivatives"] == {}
        assert isinstance(ctx["macro"], dict)
        assert isinstance(ctx["iv_term"], dict)
        assert "spot failed" in ctx["errors"]
        assert any("onchain" in e for e in ctx["errors"])
        assert any("derivatives" in e for e in ctx["errors"])


class TestLlmSynthesize:
    """Test _llm_synthesize LLM call"""

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_synthesize_parses_valid_json(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = json.dumps({
            "market_assessment": "BTC 在关键支撑位",
            "strategy_recommendation": "Sell Put 95000",
            "risk_warning": "注意波动率上升",
            "confidence": 75,
        })

        engine = LLMAnalystEngine()
        ctx = {"currency": "BTC", "spot": 100000, "dvol": {}, "contracts": [],
               "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
               "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
               "strategy_summary": {}}
        rule_reports = {"reports": [], "synthesis": {}, "market_summary": {}}

        result = engine._llm_synthesize(ctx, rule_reports)

        assert result["success"] is True
        assert result["market_assessment"] == "BTC 在关键支撑位"
        assert result["confidence"] == 75
        mock_ai.assert_called_once()

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_synthesize_handles_llm_failure(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = None

        engine = LLMAnalystEngine()
        ctx = {"currency": "BTC", "spot": 100000, "dvol": {}, "contracts": [],
               "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
               "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
               "strategy_summary": {}}
        rule_reports = {"reports": [], "synthesis": {}, "market_summary": {}}

        result = engine._llm_synthesize(ctx, rule_reports)

        assert result["success"] is False
        assert "error" in result

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_synthesize_handles_malformed_json(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = "这不是 JSON"

        engine = LLMAnalystEngine()
        ctx = {"currency": "BTC", "spot": 100000, "dvol": {}, "contracts": [],
               "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
               "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
               "strategy_summary": {}}
        rule_reports = {"reports": [], "synthesis": {}, "market_summary": {}}

        result = engine._llm_synthesize(ctx, rule_reports)

        assert result["success"] is False
        assert "raw_response" in result
