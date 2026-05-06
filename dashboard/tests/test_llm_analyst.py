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
    """Test _llm_audit anomaly detection (dual-layer: deterministic + optional LLM)"""

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
        assert len(result["anomalies"]) > 0
        assert "data_quality_score" in result
        assert "audit_method" in result

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_audit_handles_llm_failure_with_deterministic_fallback(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = None

        engine = LLMAnalystEngine()
        ctx = {"currency": "BTC", "spot": 100000,
               "dvol": {"current": 55, "z_score": 0.5, "signal": "neutral"},
               "contracts": [{"strike": 95000, "dte": 30}],
               "onchain": {"mvrv": 1.5, "nupl": 0.4, "convergence_score": 60},
               "derivatives": {"sharpe_7d": 0.5},
               "macro": {"fear_greed": {"value": 50, "classification": "Neutral"}},
               "iv_term": {"state": "contango", "slope": 0.02},
               "large_trades": [], "max_pain": 0, "risk": {},
               "errors": [], "strategy_summary": {}}
        rule_reports = {"reports": [], "synthesis": {}, "market_summary": {}}
        synthesis = {"success": True}

        result = engine._llm_audit(ctx, rule_reports, synthesis)

        assert result["success"] is True  # deterministic fallback always succeeds
        assert "data_quality_score" in result
        assert result["audit_method"].startswith("deterministic")  # fallback without LLM


class TestDeterministicAudit:
    """Test _deterministic_audit — programmatic data quality checks"""

    def test_all_data_present_scores_high(self):
        from services.llm_analyst import LLMAnalystEngine

        engine = LLMAnalystEngine()
        ctx = {
            "currency": "BTC", "spot": 100000,
            "dvol": {"current": 55.0, "z_score": 0.5, "signal": "neutral"},
            "contracts": [{"strike": 95000, "dte": 30}, {"strike": 100000, "dte": 60}, {"strike": 105000, "dte": 90}, {"strike": 110000, "dte": 120}, {"strike": 90000, "dte": 14}, {"strike": 85000, "dte": 7}],
            "onchain": {"mvrv": 1.5, "nupl": 0.4, "convergence_score": 60},
            "derivatives": {"sharpe_7d": 0.5, "vol_ratio": 1.2},
            "macro": {"fear_greed": {"value": 50, "classification": "Neutral"}, "funding_rate": {"current_rate": 0.01}},
            "iv_term": {"state": "contango", "slope": 0.02},
            "large_trades": [{"side": "buy", "notional_usd": 500000}],
            "max_pain": 98000, "risk": {}, "errors": [],
        }

        result = engine._deterministic_audit(ctx)

        assert result["success"] is True
        assert result["data_quality_score"] >= 80
        assert result["audit_method"] == "deterministic"
        assert result["checks_detail"]["completeness"]["spot"] is True
        assert result["checks_detail"]["completeness"]["contracts"] is True

    def test_missing_spot_detected(self):
        from services.llm_analyst import LLMAnalystEngine

        engine = LLMAnalystEngine()
        ctx = {
            "currency": "BTC", "spot": 0,
            "dvol": {}, "contracts": [],
            "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
            "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
        }

        result = engine._deterministic_audit(ctx)

        assert result["data_quality_score"] <= 30  # core data missing
        anomalies_desc = [a["description"] for a in result["anomalies"]]
        assert any("现货价格缺失" in d for d in anomalies_desc)
        assert any("期权合约列表为空" in d for d in anomalies_desc)

    def test_missing_data_sources_detected(self):
        from services.llm_analyst import LLMAnalystEngine

        engine = LLMAnalystEngine()
        ctx = {
            "currency": "BTC", "spot": 100000,
            "dvol": {},
            "contracts": [{"strike": 95000}],
            "onchain": {},
            "derivatives": {},
            "macro": {},
            "iv_term": {},
            "large_trades": [],
            "max_pain": 0, "risk": {}, "errors": [],
        }

        result = engine._deterministic_audit(ctx)

        # Should detect missing DVOL, onchain, derivatives, macro, iv_term
        anomalies_sources = [a["source"] for a in result["anomalies"]]
        assert "dvol" in anomalies_sources or any("dvol" in str(a).lower() for a in result["anomalies"])
        assert result["checks_detail"]["completeness"]["dvol"] is False
        assert result["checks_detail"]["completeness"]["onchain"] is False

    def test_data_errors_reported_as_anomalies(self):
        from services.llm_analyst import LLMAnalystEngine

        engine = LLMAnalystEngine()
        ctx = {
            "currency": "BTC", "spot": 100000,
            "dvol": {"current": 55}, "contracts": [{"strike": 95000}],
            "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
            "large_trades": [], "max_pain": 0, "risk": {},
            "errors": ["API timeout: deribit", "CoinGecko 429 rate limit"],
        }

        result = engine._deterministic_audit(ctx)

        error_sources = [a["source"] for a in result["anomalies"]]
        assert "data_collection" in error_sources
        assert result["data_quality_score"] < 90  # errors should reduce score

    def test_dvol_iv_consistency_check(self):
        from services.llm_analyst import LLMAnalystEngine

        engine = LLMAnalystEngine()

        # Conflicting: DVOL says high, IV says contango (low)
        ctx = {
            "currency": "BTC", "spot": 100000,
            "dvol": {"current": 80, "z_score": 2.5, "signal": "elevated"},
            "contracts": [{"strike": 95000}],
            "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {"state": "contango", "slope": 0.02},
            "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
        }

        result = engine._deterministic_audit(ctx)

        anomalies_sources = [a["source"] for a in result["anomalies"]]
        assert "dvol_vs_iv" in anomalies_sources

    def test_btc_spot_range_check(self):
        from services.llm_analyst import LLMAnalystEngine

        engine = LLMAnalystEngine()
        ctx = {
            "currency": "BTC", "spot": 50,  # absurdly low
            "dvol": {}, "contracts": [], "onchain": {}, "derivatives": {},
            "macro": {}, "iv_term": {}, "large_trades": [], "max_pain": 0,
            "risk": {}, "errors": [],
        }

        result = engine._deterministic_audit(ctx)

        anomalies_desc = [a["description"] for a in result["anomalies"]]
        assert any("异常" in d for d in anomalies_desc) or any("price" in d.lower() for d in anomalies_desc)

    def test_fear_greed_out_of_range(self):
        from services.llm_analyst import LLMAnalystEngine

        engine = LLMAnalystEngine()
        ctx = {
            "currency": "BTC", "spot": 100000,
            "dvol": {"current": 55}, "contracts": [{"strike": 95000}],
            "onchain": {}, "derivatives": {}, "iv_term": {},
            "macro": {"fear_greed": {"value": 999, "classification": "broken"}},
            "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
        }

        result = engine._deterministic_audit(ctx)

        anomalies_sources = [a["source"] for a in result["anomalies"]]
        assert "fear_greed" in anomalies_sources

    def test_few_contracts_flagged(self):
        from services.llm_analyst import LLMAnalystEngine

        engine = LLMAnalystEngine()
        ctx = {
            "currency": "BTC", "spot": 100000,
            "dvol": {"current": 55},
            "contracts": [{"strike": 95000}],  # only 1 contract
            "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
            "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
        }

        result = engine._deterministic_audit(ctx)

        issue_components = [i["component"] for i in result["logic_issues"]]
        assert "contracts" in issue_components


class TestAuditSafetyGuarantees:
    """Verify audit ALWAYS returns valid structure regardless of failures"""

    def test_llm_audit_never_has_error_field(self):
        """Frontend shows '审计未完成' when audit.error exists — must never happen"""
        from services.llm_analyst import LLMAnalystEngine

        engine = LLMAnalystEngine()
        ctx = {
            "currency": "BTC", "spot": 0,
            "dvol": {}, "contracts": [], "onchain": {}, "derivatives": {},
            "macro": {}, "iv_term": {}, "large_trades": [], "max_pain": 0,
            "risk": {}, "errors": [],
        }

        # No LLM config — should use deterministic only
        result = engine._llm_audit(ctx, {}, {"success": False})

        assert "error" not in result
        assert result["success"] is True
        assert isinstance(result["anomalies"], list)
        assert isinstance(result["logic_issues"], list)
        assert isinstance(result["data_quality_score"], (int, float))
        assert result["data_quality_score"] >= 0

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_llm_audit_recovers_from_crash(self, mock_ai):
        """Even if LLM throws an exception, audit still succeeds"""
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.side_effect = RuntimeError("Simulated LLM crash")

        engine = LLMAnalystEngine()
        ctx = {
            "currency": "BTC", "spot": 100000,
            "dvol": {"current": 55}, "contracts": [{"strike": 95000}],
            "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
            "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
        }

        result = engine._llm_audit(ctx, {}, {"success": False})

        assert "error" not in result
        assert result["success"] is True
        assert result["data_quality_score"] > 0  # should have deterministic score

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_llm_audit_recovers_from_malformed_json(self, mock_ai):
        """Even if LLM returns garbage, audit still succeeds"""
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = "This is not JSON at all, just random text from the model"

        engine = LLMAnalystEngine()
        ctx = {
            "currency": "BTC", "spot": 100000,
            "dvol": {"current": 55}, "contracts": [{"strike": 95000}],
            "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
            "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
        }

        result = engine._llm_audit(ctx, {}, {"success": False})

        assert "error" not in result
        assert result["success"] is True
        assert result["data_quality_score"] > 0

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_llm_audit_handles_empty_llm_response(self, mock_ai):
        """Empty LLM response should fall back to deterministic"""
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = ""

        engine = LLMAnalystEngine()
        ctx = {
            "currency": "BTC", "spot": 100000,
            "dvol": {"current": 55}, "contracts": [{"strike": 95000}],
            "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
            "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
        }

        result = engine._llm_audit(ctx, {}, {"success": False})

        assert "error" not in result
        assert result["success"] is True

    def test_deterministic_audit_always_returns_valid_structure(self):
        """Even with completely empty context, deterministic audit works"""
        from services.llm_analyst import LLMAnalystEngine

        engine = LLMAnalystEngine()

        # Empty context — nothing at all
        result = engine._deterministic_audit({})
        assert result["success"] is True
        assert isinstance(result["anomalies"], list)
        assert isinstance(result["logic_issues"], list)
        assert isinstance(result["data_quality_score"], (int, float))
        assert 0 <= result["data_quality_score"] <= 100
        assert len(result["anomalies"]) > 0  # should detect missing spot + contracts

    def test_deterministic_audit_with_none_values(self):
        """None values in context shouldn't crash"""
        from services.llm_analyst import LLMAnalystEngine

        engine = LLMAnalystEngine()
        ctx = {
            "currency": "BTC", "spot": None,
            "dvol": None, "contracts": None, "onchain": None,
            "derivatives": None, "macro": None, "iv_term": None,
            "large_trades": None, "max_pain": None, "risk": None, "errors": None,
        }

        result = engine._deterministic_audit(ctx)
        assert result["success"] is True
        assert result["data_quality_score"] <= 30  # core missing

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_full_audit_chain_never_produces_error_field(self, mock_ai):
        """Simulate the full run_full_analysis audit path"""
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = None  # LLM completely unavailable

        engine = LLMAnalystEngine()
        ctx = {
            "currency": "BTC", "spot": 100000,
            "dvol": {"current": 55, "z_score": 0.5, "signal": "neutral"},
            "contracts": [{"strike": 95000, "dte": 30}],
            "onchain": {"mvrv": 1.5}, "derivatives": {"sharpe_7d": 0.5},
            "macro": {"fear_greed": {"value": 50}}, "iv_term": {"state": "contango"},
            "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
        }

        audit = engine._llm_audit(ctx, {}, {"success": False})

        # Frontend-safety checks
        assert "error" not in audit, f"audit.error found: {audit.get('error')}"
        assert audit["success"] is True
        assert isinstance(audit["anomalies"], list)
        assert isinstance(audit["logic_issues"], list)
        assert isinstance(audit["data_quality_score"], (int, float))
        assert 0 <= audit["data_quality_score"] <= 100
