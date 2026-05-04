"""tests/test_unified_recommendation.py"""
import pytest
from services.unified_recommendation_engine import (
    RuleResult,
    SignalCalculator,
    ReportBuilder,
    UnifiedRecommendationEngine,
    LLMPromptBuilder,
)


class TestRuleResult:
    def test_create_rule_result(self):
        r = RuleResult(name="test", score=85, max_score=100, verdict="正面", reasoning=["理由1"])
        assert r.name == "test"
        assert r.score == 85
        assert r.verdict == "正面"

    def test_rule_result_defaults(self):
        r = RuleResult(name="minimal")
        assert r.score == 0
        assert r.verdict == ""
        assert r.reasoning == []


class TestSignalCalculator:
    def test_weighted_score_bullish(self):
        results = [
            RuleResult(name="r1", score=80, verdict="正面"),
            RuleResult(name="r2", score=70, verdict="正面"),
        ]
        weights = {"r1": 0.6, "r2": 0.4}
        signal = SignalCalculator.weighted(results, weights)
        assert signal["signal"] == "bullish"
        assert signal["signal_emoji"] == "🟢"
        assert 70 <= signal["confidence"] <= 90

    def test_weighted_score_caution_override(self):
        results = [
            RuleResult(name="r1", score=20, verdict="负面"),
            RuleResult(name="r2", score=15, verdict="负面"),
        ]
        signal = SignalCalculator.weighted(results)
        # 15 <= CAUTION_EXTREME, so weighted returns "caution"
        assert signal["signal"] == "caution"
        assert signal["signal_emoji"] == "⚠️"

    def test_weighted_score_neutral(self):
        results = [
            RuleResult(name="r1", score=55, verdict="中性"),
        ]
        signal = SignalCalculator.weighted(results)
        assert signal["signal"] == "neutral"
        assert signal["signal_emoji"] == "🟡"

    def test_worst_case_picks_min(self):
        results = [
            RuleResult(name="r1", score=80, verdict="正面"),
            RuleResult(name="r2", score=25, verdict="高风险"),
        ]
        signal = SignalCalculator.worst_case(results)
        assert signal["signal"] in ("bearish", "caution")

    def test_majority(self):
        results = [
            RuleResult(name="r1", score=70),
            RuleResult(name="r2", score=65),
            RuleResult(name="r3", score=30),
        ]
        signal = SignalCalculator.majority(results)
        assert signal["signal"] == "bullish"

    def test_signal_threshold_boundaries(self):
        boundaries = [
            (75, "bullish"),
            (60, "bullish"),
            (59, "neutral"),
            (45, "neutral"),
            (40, "neutral"),
            (39, "bearish"),
            (20, "bearish"),
        ]
        for score, expected in boundaries:
            results = [RuleResult(name="t", score=score)]
            s = SignalCalculator.weighted(results)
            assert s["signal"] == expected, f"score={score} expected {expected} got {s['signal']}"

    def test_caution_when_any_extreme_low(self):
        results = [
            RuleResult(name="r1", score=75, verdict="正常"),
            RuleResult(name="r2", score=10, verdict="极度危险"),
        ]
        signal = SignalCalculator.worst_case(results)
        assert signal["signal"] == "caution"

    def test_empty_results_weighted(self):
        signal = SignalCalculator.weighted([])
        assert signal["signal"] == "neutral"
        assert signal["confidence"] == 0

    def test_empty_results_worst_case(self):
        signal = SignalCalculator.worst_case([])
        assert signal["signal"] == "neutral"

    def test_empty_results_majority(self):
        signal = SignalCalculator.majority([])
        assert signal["signal"] == "neutral"


class TestReportBuilder:
    def test_build_report_with_factors(self):
        results = [
            RuleResult(name="因子A", score=80, max_score=100, verdict="良好", reasoning=["A1", "A2"]),
            RuleResult(name="因子B", score=60, max_score=100, verdict="适中", reasoning=["B1"]),
        ]
        report = ReportBuilder.build(results, action="测试建议")
        assert "因子A" in report["summary"]
        assert "因子B" in report["summary"]
        assert len(report["factors"]) == 2
        assert report["factors"][0]["name"] == "因子A"
        assert report["factors"][0]["score"] == 80
        assert report["suggested_action"] == "测试建议"
        assert len(report["logic_chain"]) >= 2
        assert isinstance(report["risk_flags"], list)

    def test_build_report_auto_action(self):
        results = [RuleResult(name="A", score=75, verdict="好")]
        report = ReportBuilder.build(results)
        assert "积极" in report["suggested_action"]

    def test_build_report_bearish_auto_action(self):
        results = [RuleResult(name="A", score=25, verdict="差")]
        report = ReportBuilder.build(results)
        assert "谨慎" in report["suggested_action"] or "观望" in report["suggested_action"]

    def test_build_report_empty_results(self):
        report = ReportBuilder.build([])
        assert report["summary"] == ""
        assert report["factors"] == []


class TestUnifiedRecommendationEngine:
    def test_engine_init_loads_panels(self):
        engine = UnifiedRecommendationEngine()
        assert len(engine.panels) >= 16
        assert "metric_cards" in engine.panels
        assert "risk_command_center" in engine.panels
        assert "iv_term_structure" in engine.panels

    def test_analyze_returns_valid_structure(self):
        engine = UnifiedRecommendationEngine()
        result = engine.analyze("metric_cards", {"spot": 90000, "dvol": 55, "fear_greed": 40, "trend_strength": 0.2})
        assert result["panel_id"] == "metric_cards"
        assert "signal" in result
        assert "report" in result
        assert result["llm_analysis"] is None
        assert "meta" in result
        signal = result["signal"]
        assert signal["signal"] in ("bullish", "bearish", "neutral", "caution")
        assert 0 <= signal["confidence"] <= 100

    def test_analyze_unknown_panel_raises(self):
        engine = UnifiedRecommendationEngine()
        with pytest.raises(ValueError, match="Unknown panel"):
            engine.analyze("nonexistent_panel", {})

    def test_analyze_all_returns_all_panels(self):
        engine = UnifiedRecommendationEngine()
        results = engine.analyze_all({"spot": 90000})
        assert len(results) >= 2
        for pid, r in results.items():
            assert r["panel_id"] == pid
            assert "signal" in r
            assert "report" in r

    def test_engine_rule_error_handling(self):
        """Rule that raises an exception produces a fallback RuleResult."""
        from services.unified_recommendation_engine import UnifiedRecommendationEngine
        engine = UnifiedRecommendationEngine()
        # Test via a real panel with valid data
        result = engine.analyze("strategy_center", {"spot": 90000, "contracts": []})
        assert result["report"] is not None

        # Test error handling via a custom panel entry
        engine.panels["error_test"] = {
            "name": "错误测试",
            "rules": [
                {"id": "e1", "name": "异常规则", "fn": lambda d, c: (_ for _ in ()).throw(Exception("BOOM")), "weight": 1.0},
            ],
            "signal_formula": "weighted_score",
            "default_action": "",
        }
        result = engine.analyze("error_test", {"spot": 90000})
        assert result["signal"]["signal"] == "caution"  # score=0 triggers extreme
        assert "规则执行异常" in result["report"]["summary"]

    def test_engine_majority_formula_path(self):
        """Engine uses majority signal formula when panel config says 'majority'."""
        from services.unified_recommendation_engine import UnifiedRecommendationEngine
        engine = UnifiedRecommendationEngine()
        engine.panels["majority_test"] = {
            "name": "多数测试",
            "rules": [
                {"id": "m1", "name": "多数1", "fn": lambda d, c: __import__('services.unified_recommendation_engine', fromlist=['RuleResult']).RuleResult(name="多数1", score=70, verdict="正面"), "weight": 0.33},
                {"id": "m2", "name": "多数2", "fn": lambda d, c: __import__('services.unified_recommendation_engine', fromlist=['RuleResult']).RuleResult(name="多数2", score=65, verdict="正面"), "weight": 0.33},
                {"id": "m3", "name": "多数3", "fn": lambda d, c: __import__('services.unified_recommendation_engine', fromlist=['RuleResult']).RuleResult(name="多数3", score=30, verdict="负面"), "weight": 0.34},
            ],
            "signal_formula": "majority",
            "default_action": "",
        }
        result = engine.analyze("majority_test", {"spot": 90000})
        assert result["signal"]["signal"] == "bullish"  # 2 bulls > 1 bear

    def test_llm_prompt_builder(self):
        report = {
            "summary": "测试摘要",
            "factors": [{"name": "F1", "score": 80, "verdict": "好"}],
            "logic_chain": ["L1"],
            "suggested_action": "建议",
            "risk_flags": ["风险1"],
        }
        prompt = LLMPromptBuilder.build(
            panel_id="metric_cards",
            rule_report=report,
            data_snapshot={"spot": 90000, "dvol": 62, "dvol_z": -0.5, "fear_greed": 45},
            currency="BTC",
        )
        assert "BTC" in prompt["synthesis"]
        assert "90000" in prompt["synthesis"]
        assert "bull_context" in prompt
        assert "bear_context" in prompt
