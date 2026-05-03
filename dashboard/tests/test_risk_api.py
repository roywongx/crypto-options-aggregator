"""Integration tests for risk API endpoints."""
import pytest
from unittest.mock import patch, MagicMock


MOCK_RISK_OVERVIEW = {
    "currency": "BTC",
    "spot": 100000,
    "status": "NORMAL",
    "composite_score": 35,
    "risk_level": "LOW",
    "components": {"price_position": 30, "volatility": 40, "onchain": 35, "sentiment": 35},
    "recommendations": ["保持低杠杆"],
    "floors": {"regular": 80000, "extreme": 60000},
    "advice": ["当前价格处于常规区间"],
    "recommended_actions": ["卖出 OTM Put"],
    "position_guidance": {"max_position_pct": 30, "suggested_delta_range": "0.15-0.25"},
    "timestamp": "2026-05-04T00:00:00",
    "put_wall": None,
    "gamma_flip": None,
    "max_pain": None,
    "onchain_metrics": {},
    "derivative_metrics": {},
    "pressure_test": {"base_greeks": {"delta": -0.5, "gamma": 0.001, "vega": 0.1, "theta": -0.01, "vanna": 0.01, "volga": 0.02}},
    "ai_sentiment": {"score": 50, "label": "中性"},
}


class TestRiskOverviewStructure:
    """Test /api/risk/overview returns correct structure."""

    @patch("api.risk.get_risk_overview_sync")
    def test_overview_returns_expected_keys(self, mock_sync):
        mock_sync.return_value = MOCK_RISK_OVERVIEW
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/risk/overview?currency=BTC")
        assert resp.status_code == 200
        data = resp.json()
        assert "composite_score" in data
        assert "status" in data
        assert "floors" in data
        assert "components" in data

    @patch("api.risk.get_risk_overview_sync")
    def test_mm_signal_removed(self, mock_sync):
        """mm_signal should no longer be in the response."""
        mock_sync.return_value = MOCK_RISK_OVERVIEW
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/risk/overview?currency=BTC")
        data = resp.json()
        assert "mm_signal" not in data, "mm_signal should have been removed"

    @patch("api.risk.get_risk_overview_sync")
    def test_overview_has_pressure_test(self, mock_sync):
        """Pressure test data should be included in overview."""
        mock_sync.return_value = MOCK_RISK_OVERVIEW
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/risk/overview?currency=BTC")
        data = resp.json()
        assert "pressure_test" in data
        assert "base_greeks" in data["pressure_test"]

    @patch("api.risk.get_risk_overview_sync")
    def test_overview_has_onchain_and_derivative(self, mock_sync):
        """On-chain and derivative metrics should be included."""
        mock_sync.return_value = MOCK_RISK_OVERVIEW
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/risk/overview?currency=BTC")
        data = resp.json()
        assert "onchain_metrics" in data
        assert "derivative_metrics" in data


class TestRiskOverviewSync:
    """Test get_risk_overview_sync return structure with mocked dependencies."""

    @patch("services.ai_sentiment.AISentimentAnalyzer")
    @patch("db.connection.execute_read")
    @patch("services.pressure_test.PressureTestEngine")
    @patch("services.derivative_metrics.DerivativeMetrics")
    @patch("services.onchain_metrics.OnChainMetrics")
    @patch("services.unified_risk_assessor.UnifiedRiskAssessor")
    @patch("services.risk_framework.RiskFramework")
    @patch("services.spot_price.get_spot_price")
    @patch("api.risk._calc_max_pain_sync")
    def test_sync_returns_all_required_keys(
        self, mock_mp, mock_spot, mock_rf, mock_assessor,
        mock_onchain, mock_deriv, mock_pt, mock_exec, mock_ai
    ):
        mock_spot.return_value = 100000
        mock_rf.get_status.return_value = "NORMAL"
        mock_rf._get_floors.return_value = {"regular": 80000, "extreme": 60000}
        mock_assessor.return_value.assess_comprehensive_risk.return_value = {
            "composite_score": 35, "risk_level": "LOW",
            "components": {}, "recommendations": [], "timestamp": "2026-05-04"
        }
        mock_onchain.get_all_metrics.return_value = {}
        mock_deriv.get_all_metrics.return_value = {}
        mock_pt.stress_test.return_value = {"base_greeks": {}}
        mock_exec.return_value = []
        mock_ai.analyze_market_sentiment.return_value = {"score": 50}
        mock_mp.return_value = None

        from api.risk import get_risk_overview_sync
        with patch("services.dvol_analyzer.get_dvol_from_deribit", return_value={}):
            result = get_risk_overview_sync("BTC")

        required_keys = [
            "currency", "spot", "status", "composite_score", "risk_level",
            "components", "floors", "advice", "recommended_actions",
            "position_guidance", "pressure_test", "onchain_metrics",
            "derivative_metrics", "ai_sentiment"
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    @patch("services.ai_sentiment.AISentimentAnalyzer")
    @patch("db.connection.execute_read")
    @patch("services.pressure_test.PressureTestEngine")
    @patch("services.derivative_metrics.DerivativeMetrics")
    @patch("services.onchain_metrics.OnChainMetrics")
    @patch("services.unified_risk_assessor.UnifiedRiskAssessor")
    @patch("services.risk_framework.RiskFramework")
    @patch("services.spot_price.get_spot_price")
    @patch("api.risk._calc_max_pain_sync")
    def test_sync_no_mm_signal(
        self, mock_mp, mock_spot, mock_rf, mock_assessor,
        mock_onchain, mock_deriv, mock_pt, mock_exec, mock_ai
    ):
        mock_spot.return_value = 100000
        mock_rf.get_status.return_value = "NORMAL"
        mock_rf._get_floors.return_value = {"regular": 80000, "extreme": 60000}
        mock_assessor.return_value.assess_comprehensive_risk.return_value = {
            "composite_score": 35, "risk_level": "LOW",
            "components": {}, "recommendations": [], "timestamp": "2026-05-04"
        }
        mock_onchain.get_all_metrics.return_value = {}
        mock_deriv.get_all_metrics.return_value = {}
        mock_pt.stress_test.return_value = {"base_greeks": {}}
        mock_exec.return_value = []
        mock_ai.analyze_market_sentiment.return_value = {"score": 50}
        mock_mp.return_value = None

        from api.risk import get_risk_overview_sync
        with patch("services.dvol_analyzer.get_dvol_from_deribit", return_value={}):
            result = get_risk_overview_sync("BTC")

        assert "mm_signal" not in result, "mm_signal should have been removed"


class TestLLMInsightEndpoint:
    """Test /api/risk/llm-insight endpoint."""

    @patch("services.ai_router.ai_chat_with_config")
    @patch("services.llm_analyst.LLMAnalystEngine")
    @patch("api.risk.get_risk_overview_sync")
    def test_llm_insight_returns_expected_keys(self, mock_sync, mock_engine, mock_chat):
        mock_sync.return_value = MOCK_RISK_OVERVIEW
        mock_engine.return_value._get_custom_config.return_value = {}
        mock_engine.return_value._parse_json_response.return_value = {
            "narrative": "市场风险较低",
            "anomalies": [],
            "recommendations": ["保持当前策略"],
            "confidence": 75
        }
        mock_chat.return_value = '{"narrative": "市场风险较低", "anomalies": [], "recommendations": ["保持当前策略"], "confidence": 75}'

        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/risk/llm-insight?currency=BTC")
        assert resp.status_code == 200
        data = resp.json()
        assert "narrative" in data
        assert "anomalies" in data
        assert "recommendations" in data
        assert "confidence" in data

    @patch("api.risk.get_risk_overview_sync")
    def test_llm_insight_graceful_failure(self, mock_sync):
        mock_sync.side_effect = RuntimeError("service down")

        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/risk/llm-insight?currency=BTC")
        assert resp.status_code == 200
        data = resp.json()
        assert "narrative" in data
        assert data["confidence"] == 0
