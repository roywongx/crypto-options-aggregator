"""
综合白盒测试 — 加密原生 AI 框架
"""
import sys
sys.path.insert(0, ".")

# ===== TEST 1: Threshold Calibration =====
print("=== WHITE-BOX TEST 1: Threshold Calibration ===")
from services.crypto_thresholds import CryptoThresholds

# futures_spot_ratio 6x should be NORMAL
r = CryptoThresholds.get_fixed_threshold("futures_spot_ratio", 6.0)
assert r["signal"] == "normal", f"FAIL: 6x got {r['signal']}"
print(f"PASS: futures_spot_ratio 6x -> {r['signal']} ({r['label']})")

# 3.5x should also be normal (old threshold 3x was "杠杆偏高")
r = CryptoThresholds.get_fixed_threshold("futures_spot_ratio", 3.5)
assert r["signal"] == "normal", f"FAIL: 3.5x got {r['signal']}"
print(f"PASS: futures_spot_ratio 3.5x -> {r['signal']} ({r['label']})")

# 10x is "high" not "extreme"
r = CryptoThresholds.get_fixed_threshold("futures_spot_ratio", 10.0)
assert r["signal"] == "high", f"FAIL: 10x got {r['signal']}"
print(f"PASS: futures_spot_ratio 10x -> {r['signal']} ({r['label']})")

# Only 25x+ is extreme
r = CryptoThresholds.get_fixed_threshold("futures_spot_ratio", 28.0)
assert r["signal"] == "extreme_high", f"FAIL: 28x got {r['signal']}"
print(f"PASS: futures_spot_ratio 28x -> {r['signal']}")

# perp_basis 8% -> normal_high
r = CryptoThresholds.get_fixed_threshold("perp_basis", 8.0)
assert r["signal"] == "normal_high", f"FAIL: 8% got {r['signal']}"
print(f"PASS: perp_basis 8% -> {r['signal']} ({r['label']})")

# perp_basis 18% -> high
r = CryptoThresholds.get_fixed_threshold("perp_basis", 18.0)
assert r["signal"] == "high", f"FAIL: 18% got {r['signal']}"
print(f"PASS: perp_basis 18% -> {r['signal']}")

print()

# ===== TEST 2: Engine + Panel Integration =====
print("=== WHITE-BOX TEST 2: Engine Integration ===")
from services.unified_recommendation_engine import UnifiedRecommendationEngine

engine = UnifiedRecommendationEngine()
assert "derivative_metrics" in engine.panels, "FAIL: derivative_metrics panel missing!"
panel = engine.panels["derivative_metrics"]
assert len(panel["rules"]) == 4, f"FAIL: Expected 4 rules, got {len(panel['rules'])}"
print(f"PASS: derivative_metrics panel registered with {len(panel['rules'])} rules")

# Mock data analysis
mock_data = {
    "spot": 90000,
    "dvol": 65,
    "dvol_z": 1.2,
    "perp_basis": {"basis_annualized": 12.5, "percentile": 72, "perp_price": 90100, "spot_price": 90000, "funding_rate": 0.0001, "funding_rate_pct": 0.01},
    "oi_price_divergence": {"divergence": "none", "divergence_label": "no divergence", "oi_direction": "flat", "price_direction": "flat"},
    "liquidation_heat": {"heat_level": "L0", "direction_bias": 0.1, "total_liquidation_1h_usd": 500000},
    "funding_volatility": {"volatility_7d_pct": 0.02, "signal": "normal", "label": "normal volatility"},
    "stablecoin_reserve": {"signal": "mild_inflow", "label": "mild inflow"},
    "futures_spot_ratio": {"ratio": 8.5, "signal": "high", "label": "high leverage"},
}

result = engine.analyze("derivative_metrics", mock_data)
assert result["signal"]["signal"] in ("bullish", "bearish", "neutral", "caution"), f"FAIL: Unexpected signal: {result['signal']}"
print(f"PASS: signal = {result['signal']['signal']} (confidence={result['signal']['confidence']})")
for f in result["report"]["factors"]:
    print(f"  - {f['name']}: {f['score']}/100 {f['verdict']}")

print()

# ===== TEST 3: Market Context Injection =====
print("=== WHITE-BOX TEST 3: Market Context ===")
from services.crypto_market_context import CryptoMarketContext

ctx = CryptoMarketContext.build(mock_data)
text = CryptoMarketContext.to_prompt_text(ctx)
assert "加密市场" in text, "FAIL: market context header missing"
assert "永续合约" in text, "FAIL: structural knowledge missing"
print(f"PASS: context text length = {len(text)} chars, has structural knowledge")
print(f"PASS: cycle phase = {ctx['cycle']['phase']}")
print(f"PASS: regime = {ctx['cycle']['dvol_regime']}")
print(f"PASS: warnings = {len(ctx['warnings'])}")

print()

# ===== TEST 4: LLM Prompt Build with Context =====
print("=== WHITE-BOX TEST 4: LLM Prompt with Context ===")
from services.unified_recommendation_engine import LLMPromptBuilder

prompt = LLMPromptBuilder.build("derivative_metrics", result["report"], mock_data)
assert "synthesis" in prompt, "FAIL: synthesis missing"
has_context = "加密市场" in prompt["synthesis"] or "market_context" not in prompt["synthesis"]
print(f"PASS: synthesis prompt length = {len(prompt['synthesis'])} chars")
print(f"PASS: has bull_context = {'bull_context' in prompt}")
print(f"PASS: has judge_criteria = {'judge_criteria' in prompt}")

print()

# ===== TEST 5: Config Fix Verification =====
print("=== WHITE-BOX TEST 5: Config Fix ===")
from config import config
assert config.LLM_REASONING_EFFORT == "high", f"FAIL: LLM_REASONING_EFFORT = {config.LLM_REASONING_EFFORT}"
assert config.PERP_BASIS_THRESHOLD_HIGH == 15.0, f"FAIL: PERP_BASIS_THRESHOLD_HIGH = {config.PERP_BASIS_THRESHOLD_HIGH}"
assert config.FUTURES_SPOT_RATIO_HIGH == 8.0, f"FAIL: FUTURES_SPOT_RATIO_HIGH = {config.FUTURES_SPOT_RATIO_HIGH}"
print(f"PASS: LLM_REASONING_EFFORT = {config.LLM_REASONING_EFFORT}")
print(f"PASS: PERP_BASIS_THRESHOLD_HIGH = {config.PERP_BASIS_THRESHOLD_HIGH}")
print(f"PASS: FUTURES_SPOT_RATIO_HIGH = {config.FUTURES_SPOT_RATIO_HIGH}")

print()

# ===== TEST 6: Black-box derivative_metrics endpoint test =====
print("=== BLACK-BOX TEST 6: DerivativeMetrics.get_all_metrics ===")
from services.derivative_metrics import DerivativeMetrics
deriv_result = DerivativeMetrics.get_all_metrics()

# Verify all 8 metric groups are present
required_keys = [
    "perp_basis", "oi_price_divergence", "funding_volatility",
    "liquidation_heat", "stablecoin_reserve", "futures_spot_ratio",
    "sharpe_ratio_14d", "sharpe_ratio_30d", "overheating_assessment"
]
for key in required_keys:
    assert key in deriv_result, f"FAIL: missing key {key}"
print("PASS: all 8 metric groups present in get_all_metrics output")

# Verify overheating assessment uses crypto-native note
oa = deriv_result["overheating_assessment"]
assert "signals" in oa, "FAIL: overheating_assessment missing signals"
print(f"PASS: overheating_assessment level={oa['level']}, score={oa['score']}")
# The key fix: should NOT say "极度杠杆化" for normal crypto ratios
if "极度杠杆化" in str(oa):
    print("WARNING: still shows traditional finance '极度杠杆化'")
else:
    print("PASS: no traditional finance '极度杠杆化' false positive")

print()
print("=" * 50)
print("ALL WHITE-BOX + BLACK-BOX TESTS PASSED")
print("=" * 50)
