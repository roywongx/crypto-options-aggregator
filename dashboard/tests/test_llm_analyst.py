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


class TestLlmDebate:
    """Test _llm_debate Bull/Bear LLM calls"""

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_debate_parses_bull_bear_judge(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        # 3 calls: bull, bear, judge
        mock_ai.side_effect = [
            json.dumps({"bullish_case": "链上数据显示底部", "key_drivers": ["MVRV低位", "资金流入"], "target_scenarios": ["120000"], "confidence": 70}),
            json.dumps({"bearish_case": "宏观风险加大", "key_risks": ["利率上升", "流动性收紧"], "downside_scenarios": ["85000"], "confidence": 60}),
            json.dumps({"judge_verdict": "多头略占优", "winner": "bull", "bull_confidence": 70, "bear_confidence": 60, "reasoning": "链上数据支撑更强"}),
        ]

        engine = LLMAnalystEngine()
        ctx = {"currency": "BTC", "spot": 100000, "dvol": {}, "contracts": [],
               "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
               "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
               "strategy_summary": {}}
        synthesis = {"success": True, "market_assessment": "中性偏多"}

        result = engine._llm_debate(ctx, synthesis)

        assert result["success"] is True
        assert result["bull"]["bullish_case"] == "链上数据显示底部"
        assert result["bear"]["bearish_case"] == "宏观风险加大"
        assert result["judge"]["winner"] == "bull"
        assert mock_ai.call_count == 3

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_debate_handles_partial_failure(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        # bull succeeds, bear fails, judge uses partial data
        mock_ai.side_effect = [
            json.dumps({"bullish_case": "理由", "key_drivers": [], "target_scenarios": [], "confidence": 60}),
            None,  # bear fails
            json.dumps({"judge_verdict": "数据不足", "winner": "inconclusive", "bull_confidence": 60, "bear_confidence": 0, "reasoning": "空头分析失败"}),
        ]

        engine = LLMAnalystEngine()
        ctx = {"currency": "BTC", "spot": 100000, "dvol": {}, "contracts": [],
               "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
               "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
               "strategy_summary": {}}
        synthesis = {"success": True, "market_assessment": "中性"}

        result = engine._llm_debate(ctx, synthesis)

        assert result["success"] is True
        assert result["bull"]["success"] is True
        assert result["bear"]["success"] is False


class TestLLMConfig:
    """Test LLM config save/load/test"""

    def test_save_and_load_config(self):
        from services.llm_analyst import LLMAnalystEngine

        engine = LLMAnalystEngine()

        # Save
        engine.save_config("sk-test123", "https://api.example.com/v1", "gpt-4o")

        # Load
        config = engine.load_config()
        assert config["api_key"] == "sk-test123"
        assert config["base_url"] == "https://api.example.com/v1"
        assert config["model"] == "gpt-4o"

    def test_load_config_empty(self):
        from services.llm_analyst import LLMAnalystEngine
        from db.connection import execute_write

        # Clear config
        execute_write("DELETE FROM llm_config WHERE id=1")

        engine = LLMAnalystEngine()
        config = engine.load_config()

        assert config["api_key"] == ""
        assert config["base_url"] == ""
        assert config["model"] == ""

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_test_connection_success(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = "OK"

        engine = LLMAnalystEngine()
        result = engine.test_connection({"api_key": "sk-test", "model": "gpt-4o-mini"})

        assert result["success"] is True
        assert "latency_ms" in result

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_test_connection_failure(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = None

        engine = LLMAnalystEngine()
        result = engine.test_connection({"api_key": "sk-bad"})

        assert result["success"] is False
        assert "error" in result


class TestLlmAudit:
    """Test _llm_audit anomaly detection"""

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_audit_parses_anomalies(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = json.dumps({
            "anomalies": [
                {"severity": "warning", "source": "DVOL", "description": "DVOL与IV不一致", "suggestion": "检查数据源"}
            ],
            "logic_issues": [],
            "data_quality_score": 85,
        })

        engine = LLMAnalystEngine()
        ctx = {"currency": "BTC", "spot": 100000, "dvol": {}, "contracts": [],
               "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
               "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
               "strategy_summary": {}}
        rule_reports = {"reports": [], "synthesis": {}, "market_summary": {}}
        synthesis = {"success": True}

        result = engine._llm_audit(ctx, rule_reports, synthesis)

        assert result["success"] is True
        assert len(result["anomalies"]) == 1
        assert result["anomalies"][0]["severity"] == "warning"
        assert result["data_quality_score"] == 85

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_audit_handles_llm_failure(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = None

        engine = LLMAnalystEngine()
        ctx = {"currency": "BTC", "spot": 100000, "dvol": {}, "contracts": [],
               "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
               "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
               "strategy_summary": {}}
        rule_reports = {"reports": [], "synthesis": {}, "market_summary": {}}
        synthesis = {"success": True}

        result = engine._llm_audit(ctx, rule_reports, synthesis)

        assert result["success"] is False
