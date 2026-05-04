"""services/panel_analyzers.py - Minimal stub for Task 1 testing"""
from typing import Dict, Any


def _make_result(name, score, verdict, reasoning=None):
    """Lazy factory for RuleResult to avoid circular import at module level."""
    from services.unified_recommendation_engine import RuleResult
    return RuleResult(name=name, score=score, verdict=verdict, reasoning=reasoning or [])


def get_llm_prompt(panel_id: str) -> Dict[str, str]:
    return {
        "synthesis": "Test synthesis for {currency} at spot {spot}",
        "bull_context": "Test bull",
        "bear_context": "Test bear",
        "judge_criteria": "Test judge criteria",
    }


# Minimal test panel configs for engine testing
PANEL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "test_panel": {
        "name": "测试面板",
        "rules": [
            {"id": "r1", "name": "规则1", "fn": lambda d, c: _make_result(name="规则1", score=75, verdict="好", reasoning=["t1"]), "weight": 0.6},
            {"id": "r2", "name": "规则2", "fn": lambda d, c: _make_result(name="规则2", score=55, verdict="中", reasoning=["t2"]), "weight": 0.4},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "risk_test_panel": {
        "name": "风险测试面板",
        "rules": [
            {"id": "r1", "name": "风险1", "fn": lambda d, c: _make_result(name="风险1", score=25, verdict="高风险", reasoning=["r1"]), "weight": 1.0},
        ],
        "signal_formula": "worst_case",
        "default_action": "",
    },
}
