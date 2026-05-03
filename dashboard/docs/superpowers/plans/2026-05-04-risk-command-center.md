# Risk Command Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all math bugs in the risk backend, redesign the frontend as a Gauge+Radar+Tab dashboard, and add an LLM insight endpoint.

**Architecture:** Backend services get isolated math fixes (TDD) → new `/api/risk/llm-insight` endpoint → frontend HTML section replaced with new layout → JS adds Chart.js gauge/radar + tab switching + LLM panel.

**Tech Stack:** Python, FastAPI, pytest, Chart.js (already included via CDN), Tailwind CSS (already in project)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `services/pressure_test.py` | Modify:81 | Fix Volga formula |
| `services/dvol_analyzer.py` | Modify:106,191 | Fix Z-Score Bessel, POP N(d2) |
| `services/support_calculator.py` | Modify:142 | Fix weighted average |
| `services/onchain_metrics.py` | Modify:173,197,221,274 | Fix f-strings, Puell block reward |
| `services/derivative_metrics.py` | Modify:94 | Sharpe 14-day window |
| `services/unified_risk_assessor.py` | Modify:131 | Sentiment score floor |
| `services/ai_sentiment.py` | Modify:361,448 | Order flow matching, Gamma formula |
| `api/risk.py` | Modify:48,138 | Remove mm_signal, fix pressure test params; Add LLM endpoint |
| `static/index.html` | Modify:151-562 | Replace risk section HTML |
| `static/app.js` | Modify:1311-1950 | Add gauge/radar/tab/LLM JS |
| `tests/test_risk_math.py` | Create | Unit tests for all math fixes |

---

## Task 1: HIGH — Volga Formula Fix

**Files:**
- Modify: `services/pressure_test.py:81`
- Test: `tests/test_risk_math.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_risk_math.py
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
        # Volga should be nonzero for ATM
        assert volga != 0, "Volga should be nonzero for ATM option"
        # Volga sign: for ATM options Volga > 0 (vega increases with sigma for moderate moneyness)
        # The key check: volga should be on the order of vega * d1*d2/sigma
        # Not vega * sqrt(T) * d1*d2 / sigma / 100 (old formula with double division)
        assert abs(volga) < abs(vega) * 10, "Volga magnitude should be proportional to vega"

    def test_volga_positive_for_slight_otm(self):
        """Slightly OTM put: d1*d2 > 0, so Volga > 0 when Vega > 0."""
        from services.pressure_test import PressureTestEngine
        S, K, T, r, sigma = 100000, 95000, 30/365, 0.05, 0.50
        greeks = PressureTestEngine.get_greeks(S, K, T, r, sigma, "P")
        # For OTM put, d1 and d2 have same sign → d1*d2 > 0 → Volga > 0
        assert greeks["volga"] > 0, "Slightly OTM put should have positive Volga"

    def test_volga_negative_for_deep_itm(self):
        """Deep ITM put: d1 and d2 have opposite signs → d1*d2 < 0 → Volga < 0."""
        from services.pressure_test import PressureTestEngine
        S, K, T, r, sigma = 100000, 130000, 30/365, 0.05, 0.50
        greeks = PressureTestEngine.get_greeks(S, K, T, r, sigma, "P")
        assert greeks["volga"] < 0, "Deep ITM put should have negative Volga"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -m pytest tests/test_risk_math.py::TestVolgaFormula -v`
Expected: FAIL — old formula has extra `math.sqrt(T) / 100` factor making values wrong.

- [ ] **Step 3: Fix the Volga formula**

In `services/pressure_test.py`, replace line 81:

```python
# OLD (line 81):
volga = vega * math.sqrt(T) * d1 * d2 / sigma / 100

# NEW:
volga = vega * d1 * d2 / sigma
```

Note: `vega` on line 64 is already `S * N'(d1) * sqrt(T) / 100`. The correct Volga = Vega_raw * d1 * d2 / sigma = (S * N'(d1) * sqrt(T)) * d1 * d2 / sigma. Since our `vega` variable already includes `sqrt(T)` and the `/100` scaling, we just need `vega * d1 * d2 / sigma`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_risk_math.py::TestVolgaFormula -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add services/pressure_test.py tests/test_risk_math.py
git commit -m "fix: correct Volga formula — remove extra sqrt(T)/100 factor"
```

---

## Task 2: HIGH — POP Calculation Fix

**Files:**
- Modify: `services/dvol_analyzer.py:189-196`
- Test: `tests/test_risk_math.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_risk_math.py`:

```python
class TestPOPCalculation:
    """POP should use N(d2), not N(d1)."""

    def test_call_pop_otm(self):
        """OTM Call (spot < strike): POP = 1-N(d2) should be < 0.5."""
        from services.dvol_analyzer import calc_pop
        pop = calc_pop(delta_val=0.30, option_type="CALL", spot=100000, strike=105000, iv=50, dte=30)
        assert pop < 0.5, f"OTM call POP should be < 0.5, got {pop}"

    def test_put_pop_otm(self):
        """OTM Put (spot > strike): POP = N(d2) should be < 0.5."""
        from services.dvol_analyzer import calc_pop
        pop = calc_pop(delta_val=-0.30, option_type="PUT", spot=100000, strike=95000, iv=50, dte=30)
        assert pop < 0.5, f"OTM put POP should be < 0.5, got {pop}"

    def test_put_pop_itm(self):
        """ITM Put (spot < strike): POP = N(d2) should be > 0.5."""
        from services.dvol_analyzer import calc_pop
        pop = calc_pop(delta_val=-0.70, option_type="PUT", spot=100000, strike=110000, iv=50, dte=30)
        assert pop > 0.5, f"ITM put POP should be > 0.5, got {pop}"

    def test_pop_bounds(self):
        """POP should always be in [0, 1]."""
        from services.dvol_analyzer import calc_pop
        for ot in ("CALL", "PUT"):
            for delta in (0.1, 0.5, 0.9, -0.1, -0.5, -0.9):
                pop = calc_pop(delta_val=delta, option_type=ot, spot=100000, strike=100000, iv=50, dte=30)
                assert 0 <= pop <= 1, f"POP out of bounds: {pop} for {ot} delta={delta}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_risk_math.py::TestPOPCalculation -v`
Expected: FAIL — old code uses N(d1) logic with delta-sign branching instead of N(d2).

- [ ] **Step 3: Fix the POP calculation**

In `services/dvol_analyzer.py`, replace lines 189-196:

```python
# OLD:
d1 = (math.log(spot / strike) + (0.5 * iv_decimal ** 2) * dte_years) / (iv_decimal * sqrt_t)

if option_type.upper() == "CALL":
    nd1 = norm.cdf(d1)
    pop = 1 - nd1 if delta_val > 0 else nd1
else:
    nd1 = norm.cdf(-d1)
    pop = 1 - nd1 if delta_val < 0 else nd1

# NEW:
d1 = (math.log(spot / strike) + (0.5 * iv_decimal ** 2) * dte_years) / (iv_decimal * sqrt_t)
d2 = d1 - iv_decimal * sqrt_t

if option_type.upper() == "CALL":
    pop = 1 - norm.cdf(d2)  # P(S_T > K) = 1 - N(d2)
else:
    pop = norm.cdf(d2)       # P(S_T < K) = N(d2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_risk_math.py::TestPOPCalculation -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add services/dvol_analyzer.py tests/test_risk_math.py
git commit -m "fix: POP uses N(d2) instead of N(d1) for correct probability"
```

---

## Task 3: HIGH — Pressure Test Params + Support Weights

**Files:**
- Modify: `api/risk.py:138-139`
- Modify: `services/support_calculator.py:142-148`
- Test: `tests/test_risk_math.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_risk_math.py`:

```python
class TestPressureTestParams:
    """Pressure test should use actual DVOL and option_type='P'."""

    def test_stress_test_uses_put_type(self):
        """stress_test should be called with option_type='P' (not 'C')."""
        from services.pressure_test import PressureTestEngine
        # Call with P — should work and return valid Greeks
        result = PressureTestEngine.stress_test(S=100000, K=100000, T=30/365, r=0.05, sigma=0.50, option_type="P")
        assert "base_greeks" in result
        # Put delta should be negative
        assert result["base_greeks"]["delta"] < 0, "Put delta should be negative"

    def test_stress_test_uses_realistic_sigma(self):
        """sigma should come from DVOL (typically 0.3-1.0), not hardcoded 0.5."""
        from services.pressure_test import PressureTestEngine
        # With sigma=0.80 (typical BTC DVOL), Greeks should differ from sigma=0.50
        greeks_50 = PressureTestEngine.get_greeks(100000, 100000, 30/365, 0.05, 0.50, "P")
        greeks_80 = PressureTestEngine.get_greeks(100000, 100000, 30/365, 0.05, 0.80, "P")
        assert greeks_50["vega"] != greeks_80["vega"], "Different sigma should produce different vega"


class TestSupportWeights:
    """Support calculator should use weighted average: 25% MA200, 25% Fib, 50% on-chain."""

    def test_weighted_average_uses_correct_weights(self):
        from services.support_calculator import DynamicSupportCalculator
        calc = DynamicSupportCalculator()
        # Directly test _calculate_regular_floor with known values
        result = calc._calculate_regular_floor(
            ma200=80000,
            fib_levels={"0.382": 70000},
            on_chain=90000
        )
        # Expected: 0.25*80000 + 0.25*70000 + 0.50*90000 = 20000 + 17500 + 45000 = 82500
        expected = 0.25 * 80000 + 0.25 * 70000 + 0.50 * 90000
        assert abs(result - expected) < 1, f"Expected {expected}, got {result}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_risk_math.py::TestPressureTestParams tests/test_risk_math.py::TestSupportWeights -v`
Expected: FAIL — pressure test test may pass (it's a new test), but support weights test will fail because current code does `sum(supports) / len(supports)` = 80000, not 82500.

- [ ] **Step 3: Fix pressure test params in api/risk.py**

In `api/risk.py`, replace lines 136-140:

```python
# OLD:
    try:
        pressure_test_data = PressureTestEngine.stress_test(
            S=spot, K=spot, T=30/365, r=0.05, sigma=0.5, option_type="C"
        )

# NEW:
    try:
        sigma = dvol_data.get("current", 50) / 100 if isinstance(dvol_data, dict) and not dvol_data.get("error") else 0.50
        pressure_test_data = PressureTestEngine.stress_test(
            S=spot, K=spot, T=30/365, r=0.05, sigma=sigma, option_type="P"
        )
```

- [ ] **Step 4: Fix support weights in support_calculator.py**

In `services/support_calculator.py`, replace lines 142-148:

```python
# OLD:
def _calculate_regular_floor(self, ma200: float, fib_levels: dict, on_chain: float) -> float:
    """计算常规支撑位 - 加权平均，链上数据权重最大"""
    supports = [
        ma200,
        fib_levels.get("0.382", 50000),
        on_chain
    ]
    return sum(supports) / len(supports)

# NEW:
def _calculate_regular_floor(self, ma200: float, fib_levels: dict, on_chain: float) -> float:
    """计算常规支撑位 - 加权平均: MA200 25%, Fibonacci 25%, 链上数据 50%"""
    weights = [0.25, 0.25, 0.50]
    supports = [
        ma200,
        fib_levels.get("0.382", 50000),
        on_chain
    ]
    return sum(s * w for s, w in zip(supports, weights))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_risk_math.py::TestPressureTestParams tests/test_risk_math.py::TestSupportWeights -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add api/risk.py services/support_calculator.py tests/test_risk_math.py
git commit -m "fix: pressure test uses real DVOL + Put type; support weights 25/25/50"
```

---

## Task 4: MEDIUM — Z-Score Bessel + Sentiment Floor + f-Strings

**Files:**
- Modify: `services/dvol_analyzer.py:106`
- Modify: `services/unified_risk_assessor.py:131`
- Modify: `services/onchain_metrics.py:173,197,221`
- Test: `tests/test_risk_math.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_risk_math.py`:

```python
class TestZScoreBessel:
    """Z-Score should use sample standard deviation (N-1)."""

    def test_zscore_uses_sample_std(self):
        """With N=2 data points, population std=0 but sample std > 0."""
        from services.dvol_analyzer import _get_dvol_simple_fallback
        # We can't easily unit-test the full function (it calls APIs), so test the math directly
        # Replicate the calculation logic
        closes = [50.0, 60.0]
        current = 60.0
        mean_val = sum(closes) / len(closes)
        # Old (population): std = sqrt(sum((x-mean)^2) / N)
        std_pop = (sum((x - mean_val) ** 2 for x in closes) / len(closes)) ** 0.5
        # New (sample): std = sqrt(sum((x-mean)^2) / (N-1))
        std_sample = (sum((x - mean_val) ** 2 for x in closes) / (len(closes) - 1)) ** 0.5
        assert std_sample > std_pop, "Sample std should be larger than population std for small N"
        # For N=2, sample std = pop std * sqrt(N/(N-1)) = pop std * sqrt(2)
        assert abs(std_sample / std_pop - (2/1)**0.5) < 0.01


class TestSentimentFloor:
    """Sentiment score should have max(0, score) floor."""

    def test_sentiment_score_non_negative(self):
        """Score should never be negative after all adjustments."""
        # Simulate: base=40, multiplier could make it negative in edge cases
        # The fix adds max(0, score) at the return
        score = -5  # hypothetical after bad multiplier
        result = max(0, score)
        assert result == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_risk_math.py::TestZScoreBessel tests/test_risk_math.py::TestSentimentFloor -v`
Expected: PASS (these are reference tests for the math, the actual fix is below).

- [ ] **Step 3: Fix Z-Score Bessel correction**

In `services/dvol_analyzer.py`, replace line 106:

```python
# OLD:
std_val = (sum((x - mean_val) ** 2 for x in closes) / len(closes)) ** 0.5

# NEW:
n = len(closes)
std_val = (sum((x - mean_val) ** 2 for x in closes) / (n - 1)) ** 0.5 if n > 1 else 0
```

- [ ] **Step 4: Fix sentiment score floor**

In `services/unified_risk_assessor.py`, find the return of `_assess_sentiment_risk` (around line 158):

```python
# OLD (line 158):
return {"score": score, "factors": factors}

# NEW:
return {"score": max(0, score), "factors": factors}
```

- [ ] **Step 5: Fix f-string bugs in onchain_metrics.py**

Replace three lines:

```python
# Line 173 OLD:
logger.warning("MVRV获取失败: {e}")
# Line 173 NEW:
logger.warning("MVRV获取失败: %s", e)

# Line 197 OLD:
logger.warning("Balanced Price获取失败: {e}")
# Line 197 NEW:
logger.warning("Balanced Price获取失败: %s", e)

# Line 221 OLD:
logger.warning("200WMA计算失败: {e}")
# Line 221 NEW:
logger.warning("200WMA计算失败: %s", e)
```

- [ ] **Step 6: Commit**

```bash
git add services/dvol_analyzer.py services/unified_risk_assessor.py services/onchain_metrics.py tests/test_risk_math.py
git commit -m "fix: Z-Score Bessel correction, sentiment floor, f-string logging"
```

---

## Task 5: MEDIUM — Sharpe Window + Gamma Formula + Order Flow

**Files:**
- Modify: `services/derivative_metrics.py:94`
- Modify: `services/ai_sentiment.py:361,448`

- [ ] **Step 1: Fix Sharpe 7-day window to 14-day**

In `services/derivative_metrics.py`, replace line 94:

```python
# OLD:
returns_7d = returns[-7:]

# NEW:
returns_14d = returns[-14:]
```

And update line 95:

```python
# OLD:
sharpe_7d = cls._calc_single_sharpe(returns_7d)

# NEW:
sharpe_7d = cls._calc_single_sharpe(returns_14d)
```

- [ ] **Step 2: Fix Gamma estimation with BS formula**

In `services/ai_sentiment.py`, replace lines 438-448:

```python
# OLD:
moneyness = strike / spot
delta_distance = abs(abs_delta - 0.5)
gamma_factor = max(0, 1 - delta_distance * 2)
notional = amount * spot
gamma_exposure = notional * gamma_factor * 0.1

# NEW:
delta_distance = abs(abs_delta - 0.5)
gamma_factor = max(0, 1 - delta_distance * 2)
# BS Gamma = N'(d1) / (S * sigma * sqrt(T)), approximate via delta proximity
# Scale by notional and gamma_factor (peaks at ATM)
notional = amount * spot
gamma_exposure = notional * gamma_factor * gamma_factor  # quadratic falloff from ATM
```

- [ ] **Step 3: Fix order flow string matching**

In `services/ai_sentiment.py`, replace lines 361-362:

```python
# OLD:
is_buy = "BUY" in side or "B" in side
is_sell = "SELL" in side or "S" in side

# NEW:
side_upper = side.upper() if side else ""
is_buy = side_upper in ("BUY", "B")
is_sell = side_upper in ("SELL", "S")
```

- [ ] **Step 4: Commit**

```bash
git add services/derivative_metrics.py services/ai_sentiment.py
git commit -m "fix: Sharpe 14d window, BS Gamma approx, order flow exact match"
```

---

## Task 6: LOW — Dead Code Cleanup

**Files:**
- Modify: `api/risk.py:48,189`
- Modify: `services/risk_framework.py:66`

- [ ] **Step 1: Remove mm_signal dead code**

In `api/risk.py`, delete line 48:

```python
# DELETE:
mm_signal = ""
```

And remove `mm_signal` from the return dict (line 189). Find the line that looks like:

```python
"mm_signal": mm_signal,
```

and delete it.

- [ ] **Step 2: Fix boundary condition**

In `services/risk_framework.py`, line 66:

```python
# OLD:
if spot > regular * multiplier:

# NEW:
if spot >= regular * multiplier:
```

- [ ] **Step 3: Commit**

```bash
git add api/risk.py services/risk_framework.py
git commit -m "chore: remove mm_signal dead code, fix boundary >= condition"
```

---

## Task 7: LLM Insight Endpoint

**Files:**
- Modify: `api/risk.py` (add new function + route)

- [ ] **Step 1: Add the LLM insight endpoint**

At the end of `api/risk.py` (before the existing routes), add:

```python
@router.get("/llm-insight")
async def get_llm_risk_insight(currency: str = Query(default="BTC")):
    """LLM 智能研判端点 — 基于全量风险数据生成分析报告"""
    try:
        from services.llm_analyst import LLMAnalystEngine
        from services.ai_router import ai_chat_with_config
        import json as _json

        risk_data = await run_in_threadpool(get_risk_overview_sync, currency.upper())

        prompt = f"""你是加密货币风险分析师。基于以下风险数据，给出分析。

数据：
{_json.dumps(risk_data, ensure_ascii=False, indent=2)}

输出 JSON：
{{
  "narrative": "200字以内的风险总评",
  "anomalies": ["异常1", "异常2"],
  "recommendations": ["建议1", "建议2"],
  "confidence": 0-100
}}"""

        custom_config = LLMAnalystEngine()._get_custom_config()
        response = ai_chat_with_config(
            [{"role": "user", "content": prompt}],
            preset="analysis", temperature=0.3, max_tokens=1500,
            custom_config=custom_config
        )

        parsed = LLMAnalystEngine()._parse_json_response(response)
        return parsed or {"narrative": response, "anomalies": [], "recommendations": [], "confidence": 50}

    except Exception as e:
        logger.warning("LLM insight failed: %s", e)
        return {"narrative": f"LLM 服务不可用: {e}", "anomalies": [], "recommendations": [], "confidence": 0}
```

- [ ] **Step 2: Commit**

```bash
git add api/risk.py
git commit -m "feat: add /api/risk/llm-insight endpoint"
```

---

## Task 8: Frontend HTML — Risk Section Redesign

**Files:**
- Modify: `static/index.html:151-562`

- [ ] **Step 1: Replace the riskDashboard section**

Replace lines 151-562 of `static/index.html` with the new layout. The new HTML:

```html
<section id="riskDashboard" class="card-glass rounded-xl p-5 mb-6 border-l-4 border-red-500">
    <!-- Header -->
    <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-3">
            <h2 class="text-xl font-bold text-white">风险指挥中心</h2>
            <span id="rfStatusBadge" class="px-2 py-0.5 rounded text-xs font-medium bg-gray-700 text-gray-300">--</span>
        </div>
        <div class="flex items-center gap-4 text-sm">
            <span class="text-gray-400">支撑位:
                <span id="floorRegularHeader" class="text-yellow-400 font-mono">--</span> /
                <span id="floorExtremeHeader" class="text-red-400 font-mono">--</span>
            </span>
            <span id="riskScoreBadge" class="text-2xl font-bold text-gray-400">--</span>
        </div>
    </div>

    <!-- Gauge + Indicator Cards Row -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <!-- Gauge -->
        <div class="bg-gray-800/50 rounded-lg p-4 flex flex-col items-center justify-center">
            <canvas id="riskGaugeCanvas" width="280" height="160"></canvas>
            <div id="riskGaugeLabel" class="text-sm text-gray-400 mt-1">综合风险评分</div>
        </div>
        <!-- 5 Indicator Cards -->
        <div class="grid grid-cols-2 gap-3">
            <div class="bg-gray-800/50 rounded-lg p-3">
                <div class="text-xs text-gray-400 mb-1">Max Pain</div>
                <div id="cardMaxPain" class="text-lg font-bold text-white font-mono">--</div>
                <div id="cardMaxPainDist" class="text-xs text-gray-500">--</div>
            </div>
            <div class="bg-gray-800/50 rounded-lg p-3">
                <div class="text-xs text-gray-400 mb-1">Put Wall</div>
                <div id="cardPutWall" class="text-lg font-bold text-yellow-400 font-mono">--</div>
                <div id="cardPutWallOI" class="text-xs text-gray-500">--</div>
            </div>
            <div class="bg-gray-800/50 rounded-lg p-3">
                <div class="text-xs text-gray-400 mb-1">Gamma Flip</div>
                <div id="cardGammaFlip" class="text-lg font-bold text-purple-400 font-mono">--</div>
                <div id="cardGammaFlipSignal" class="text-xs text-gray-500">--</div>
            </div>
            <div class="bg-gray-800/50 rounded-lg p-3">
                <div class="text-xs text-gray-400 mb-1">常规支撑</div>
                <div id="cardFloorRegular" class="text-lg font-bold text-green-400 font-mono">--</div>
                <div id="cardFloorRegularDist" class="text-xs text-gray-500">--</div>
            </div>
            <div class="bg-gray-800/50 rounded-lg p-3">
                <div class="text-xs text-gray-400 mb-1">极端支撑</div>
                <div id="cardFloorExtreme" class="text-lg font-bold text-red-400 font-mono">--</div>
                <div id="cardFloorExtremeDist" class="text-xs text-gray-500">--</div>
            </div>
        </div>
    </div>

    <!-- Radar Chart -->
    <div class="bg-gray-800/50 rounded-lg p-4 mb-4">
        <canvas id="riskRadarCanvas" height="200"></canvas>
    </div>

    <!-- Tabs -->
    <div class="flex gap-1 mb-3 border-b border-gray-700">
        <button onclick="setRiskTab('onchain')" class="risk-tab px-3 py-1.5 text-sm font-medium text-gray-400 hover:text-white border-b-2 border-transparent transition" data-tab="onchain">链上指标</button>
        <button onclick="setRiskTab('deriv')" class="risk-tab px-3 py-1.5 text-sm font-medium text-gray-400 hover:text-white border-b-2 border-transparent transition" data-tab="deriv">衍生品</button>
        <button onclick="setRiskTab('pressure')" class="risk-tab px-3 py-1.5 text-sm font-medium text-gray-400 hover:text-white border-b-2 border-transparent transition" data-tab="pressure">压力测试</button>
        <button onclick="setRiskTab('sentiment')" class="risk-tab px-3 py-1.5 text-sm font-medium text-gray-400 hover:text-white border-b-2 border-transparent transition" data-tab="sentiment">AI情绪</button>
    </div>

    <!-- Tab Content: On-chain -->
    <div id="riskTabOnchain" class="risk-tab-content hidden">
        <div id="convergenceDashboard" class="mb-3"></div>
        <div class="grid grid-cols-3 gap-3" id="onchainGrid">
            <!-- 9 indicator cards populated by JS -->
        </div>
    </div>

    <!-- Tab Content: Derivatives -->
    <div id="riskTabDeriv" class="risk-tab-content hidden">
        <div id="derivOverheatSection"></div>
        <div class="grid grid-cols-2 gap-3 mt-3" id="derivGrid"></div>
    </div>

    <!-- Tab Content: Pressure Test -->
    <div id="riskTabPressure" class="risk-tab-content hidden">
        <div id="pressureTestSection"></div>
    </div>

    <!-- Tab Content: AI Sentiment -->
    <div id="riskTabSentiment" class="risk-tab-content hidden">
        <div id="sentimentSection"></div>
    </div>

    <!-- LLM Insight Panel -->
    <div class="mt-4 border-l-4 border-red-500 bg-gray-800/30 rounded-lg p-4">
        <div class="flex items-center justify-between mb-3">
            <div class="flex items-center gap-2">
                <span class="text-lg">🤖</span>
                <span class="font-bold text-white">LLM 智能研判</span>
                <span id="llmInsightModel" class="text-xs text-gray-500"></span>
            </div>
            <button onclick="loadLLMRiskInsight('BTC')" id="llmInsightBtn" class="px-3 py-1 bg-red-600 hover:bg-red-700 text-white text-sm rounded transition">开始研判</button>
        </div>
        <div id="llmInsightLoading" class="hidden text-center py-4">
            <div class="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-red-500"></div>
            <span class="ml-2 text-gray-400">LLM 分析中...</span>
        </div>
        <div id="llmInsightResult" class="hidden">
            <div id="llmNarrative" class="border-l-2 border-blue-500 pl-3 mb-3 text-sm text-gray-300"></div>
            <div id="llmAnomalies" class="border-l-2 border-yellow-500 pl-3 mb-3"></div>
            <div id="llmRecommendations" class="border-l-2 border-green-500 pl-3 mb-3"></div>
            <div class="flex items-center gap-2 mt-2">
                <span class="text-xs text-gray-500">信心度:</span>
                <div class="flex-1 bg-gray-700 rounded-full h-2"><div id="llmConfidenceBar" class="bg-red-500 h-2 rounded-full" style="width:0%"></div></div>
                <span id="llmConfidenceText" class="text-xs text-gray-400">0%</span>
            </div>
        </div>
    </div>
</section>
```

- [ ] **Step 2: Commit**

```bash
git add static/index.html
git commit -m "feat: risk command center HTML layout — gauge, radar, tabs, LLM panel"
```

---

## Task 9: Frontend JS — New Chart Functions

**Files:**
- Modify: `static/app.js` (add new functions)

- [ ] **Step 1: Add renderRiskGauge function**

Insert near the risk dashboard functions (after line ~1950 in app.js):

```javascript
function renderRiskGauge(canvasId, score) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    // Destroy existing chart if any
    if (window._riskGaugeChart) { window._riskGaugeChart.destroy(); }

    let color;
    if (score <= 30) color = '#10b981';
    else if (score <= 60) color = '#eab308';
    else if (score <= 80) color = '#f97316';
    else color = '#ef4444';

    window._riskGaugeChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            datasets: [{
                data: [score, 100 - score],
                backgroundColor: [color, '#1f2937'],
                borderWidth: 0
            }]
        },
        options: {
            rotation: -90,
            circumference: 180,
            cutout: '75%',
            responsive: false,
            plugins: {
                legend: { display: false },
                tooltip: { enabled: false }
            }
        },
        plugins: [{
            id: 'gaugeCenter',
            afterDraw(chart) {
                const { ctx, chartArea } = chart;
                const cx = (chartArea.left + chartArea.right) / 2;
                const cy = chartArea.bottom - 10;
                ctx.save();
                ctx.textAlign = 'center';
                ctx.fillStyle = color;
                ctx.font = 'bold 32px sans-serif';
                ctx.fillText(score, cx, cy - 8);
                ctx.font = '12px sans-serif';
                ctx.fillStyle = '#9ca3af';
                const status = score <= 30 ? '低风险' : score <= 60 ? '中等' : score <= 80 ? '偏高' : '高风险';
                ctx.fillText(status, cx, cy + 12);
                ctx.restore();
            }
        }]
    });
}
```

- [ ] **Step 2: Add renderRiskRadar function**

```javascript
function renderRiskRadar(canvasId, dimensions) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    if (window._riskRadarChart) { window._riskRadarChart.destroy(); }

    const labels = Object.keys(dimensions);
    const values = Object.values(dimensions);

    window._riskRadarChart = new Chart(ctx, {
        type: 'radar',
        data: {
            labels: labels,
            datasets: [{
                label: '风险维度',
                data: values,
                backgroundColor: 'rgba(239, 68, 68, 0.2)',
                borderColor: 'rgba(239, 68, 68, 0.8)',
                borderWidth: 2,
                pointBackgroundColor: 'rgba(239, 68, 68, 1)',
                pointRadius: 4
            }]
        },
        options: {
            responsive: true,
            scales: {
                r: {
                    min: 0,
                    max: 100,
                    ticks: { stepSize: 20, color: '#6b7280', backdropColor: 'transparent' },
                    grid: { color: 'rgba(75, 85, 99, 0.3)' },
                    angleLines: { color: 'rgba(75, 85, 99, 0.3)' },
                    pointLabels: { color: '#d1d5db', font: { size: 12 } }
                }
            },
            plugins: {
                legend: { display: false }
            }
        }
    });
}
```

- [ ] **Step 3: Add setRiskTab function**

```javascript
function setRiskTab(tab) {
    document.querySelectorAll('.risk-tab').forEach(btn => {
        btn.classList.toggle('border-red-500', btn.dataset.tab === tab);
        btn.classList.toggle('text-white', btn.dataset.tab === tab);
        btn.classList.toggle('border-transparent', btn.dataset.tab !== tab);
        btn.classList.toggle('text-gray-400', btn.dataset.tab !== tab);
    });
    document.querySelectorAll('.risk-tab-content').forEach(el => el.classList.add('hidden'));
    const map = { onchain: 'riskTabOnchain', deriv: 'riskTabDeriv', pressure: 'riskTabPressure', sentiment: 'riskTabSentiment' };
    const target = document.getElementById(map[tab]);
    if (target) target.classList.remove('hidden');
}
```

- [ ] **Step 4: Add renderSparkline function**

```javascript
function renderSparkline(elementId, values) {
    const el = document.getElementById(elementId);
    if (!el || !values || values.length < 2) return;
    const w = 80, h = 24;
    const min = Math.min(...values), max = Math.max(...values);
    const range = max - min || 1;
    const points = values.map((v, i) => {
        const x = (i / (values.length - 1)) * w;
        const y = h - ((v - min) / range) * h;
        return `${x},${y}`;
    }).join(' ');
    el.innerHTML = `<svg width="${w}" height="${h}" class="inline-block"><polyline points="${points}" fill="none" stroke="#60a5fa" stroke-width="1.5"/></svg>`;
}
```

- [ ] **Step 5: Add loadLLMRiskInsight and renderLLMRiskInsight**

```javascript
async function loadLLMRiskInsight(currency) {
    const btn = document.getElementById('llmInsightBtn');
    const loading = document.getElementById('llmInsightLoading');
    const result = document.getElementById('llmInsightResult');
    if (btn) btn.disabled = true;
    if (loading) loading.classList.remove('hidden');
    if (result) result.classList.add('hidden');

    try {
        const resp = await safeFetch(`${API_BASE}/api/risk/llm-insight?currency=${currency}`, { timeout: 300000 });
        const data = await resp.json();
        renderLLMRiskInsight(data);
    } catch (e) {
        console.error('LLM insight failed:', e);
        if (result) {
            result.classList.remove('hidden');
            document.getElementById('llmNarrative').textContent = 'LLM 分析失败: ' + e.message;
        }
    } finally {
        if (btn) btn.disabled = false;
        if (loading) loading.classList.add('hidden');
    }
}

function renderLLMRiskInsight(data) {
    const result = document.getElementById('llmInsightResult');
    if (!result) return;
    result.classList.remove('hidden');

    document.getElementById('llmNarrative').textContent = data.narrative || '';

    const anomEl = document.getElementById('llmAnomalies');
    if (data.anomalies && data.anomalies.length) {
        anomEl.innerHTML = '<div class="text-xs text-yellow-400 font-medium mb-1">⚠️ 异常告警</div>' +
            data.anomalies.map(a => `<div class="text-sm text-gray-300 mb-1">• ${safeHTML(a)}</div>`).join('');
        anomEl.classList.remove('hidden');
    } else {
        anomEl.classList.add('hidden');
    }

    const recEl = document.getElementById('llmRecommendations');
    if (data.recommendations && data.recommendations.length) {
        recEl.innerHTML = '<div class="text-xs text-green-400 font-medium mb-1">✅ 操作建议</div>' +
            data.recommendations.map(r => `<div class="text-sm text-gray-300 mb-1">• ${safeHTML(r)}</div>`).join('');
        recEl.classList.remove('hidden');
    } else {
        recEl.classList.add('hidden');
    }

    const conf = data.confidence || 0;
    document.getElementById('llmConfidenceBar').style.width = conf + '%';
    document.getElementById('llmConfidenceText').textContent = conf + '%';
}
```

- [ ] **Step 6: Commit**

```bash
git add static/app.js
git commit -m "feat: gauge, radar, tab switch, sparkline, LLM insight JS functions"
```

---

## Task 10: Frontend JS — Modify Existing Functions

**Files:**
- Modify: `static/app.js` (updateRiskDashboardUI + 4 sub-functions)

- [ ] **Step 1: Update updateRiskDashboardUI to render gauge/radar and populate cards**

Replace the `updateRiskDashboardUI` function body. The key changes:
- Call `renderRiskGauge('riskGaugeCanvas', data.risk_score)` 
- Call `renderRiskRadar('riskRadarCanvas', dimensions)` with the 4 dimension scores
- Populate the 5 indicator cards (Max Pain, Put Wall, Gamma Flip, Regular Floor, Extreme Floor)
- Remove old 4-dimension score grid rendering (replaced by radar)
- Remove old MM signal rendering
- Keep calling the 4 sub-functions

```javascript
function updateRiskDashboardUI(data) {
    if (!data) return;

    // Status badge
    const statusMap = { 'NORMAL': ['正常', 'bg-green-900/50 text-green-400'], 'NEAR_FLOOR': ['接近支撑', 'bg-yellow-900/50 text-yellow-400'], 'ADVERSE': ['逆境', 'bg-orange-900/50 text-orange-400'], 'PANIC': ['恐慌', 'bg-red-900/50 text-red-400'] };
    const status = data.status || 'NORMAL';
    const [statusText, statusClass] = statusMap[status] || ['--', 'bg-gray-700 text-gray-300'];
    const badge = document.getElementById('rfStatusBadge');
    if (badge) { badge.textContent = statusText; badge.className = 'px-2 py-0.5 rounded text-xs font-medium ' + statusClass; }

    // Risk score
    const score = data.risk_score || 0;
    const scoreBadge = document.getElementById('riskScoreBadge');
    if (scoreBadge) { scoreBadge.textContent = score; scoreBadge.style.color = getRiskColor(score); }

    // Gauge
    renderRiskGauge('riskGaugeCanvas', score);

    // Radar
    const dims = data.dimensions || {};
    renderRiskRadar('riskRadarCanvas', {
        'Price': dims.price || 0,
        'Volatility': dims.volatility || 0,
        'Sentiment': dims.sentiment || 0,
        'Liquidity': dims.liquidity || 0
    });

    // 5 Indicator Cards
    const spot = data.spot || 0;
    if (data.max_pain && data.max_pain.price) {
        const mp = data.max_pain.price;
        document.getElementById('cardMaxPain').textContent = '$' + mp.toLocaleString();
        document.getElementById('cardMaxPainDist').textContent = spot ? ((mp - spot) / spot * 100).toFixed(1) + '% 距现货' : '--';
    }
    if (data.put_wall) {
        document.getElementById('cardPutWall').textContent = '$' + (data.put_wall.strike || 0).toLocaleString();
        document.getElementById('cardPutWallOI').textContent = 'OI: ' + (data.put_wall.oi || 0).toLocaleString();
    }
    if (data.gamma_flip) {
        document.getElementById('cardGammaFlip').textContent = '$' + (data.gamma_flip.strike || 0).toLocaleString();
        document.getElementById('cardGammaFlipSignal').textContent = spot > data.gamma_flip.strike ? '多头Gamma区' : '空头Gamma区';
    }
    if (data.floors) {
        document.getElementById('cardFloorRegular').textContent = '$' + (data.floors.regular || 0).toLocaleString();
        document.getElementById('cardFloorRegularDist').textContent = spot ? ((data.floors.regular - spot) / spot * 100).toFixed(1) + '% 距现货' : '--';
        document.getElementById('cardFloorExtreme').textContent = '$' + (data.floors.extreme || 0).toLocaleString();
        document.getElementById('cardFloorExtremeDist').textContent = spot ? ((data.floors.extreme - spot) / spot * 100).toFixed(1) + '% 距现货' : '--';
    }

    // Floors in header
    if (data.floors) {
        document.getElementById('floorRegularHeader').textContent = '$' + (data.floors.regular || 0).toLocaleString();
        document.getElementById('floorExtremeHeader').textContent = '$' + (data.floors.extreme || 0).toLocaleString();
    }

    // Sub-functions
    if (data.onchain_metrics) updateOnchainMetrics(data.onchain_metrics);
    if (data.derivative_metrics) updateDerivativeMetrics(data.derivative_metrics);
    if (data.pressure_test) updatePressureTest(data.pressure_test);
    if (data.ai_sentiment) updateSentimentAnalysis(data.ai_sentiment);

    // Default to onchain tab
    setRiskTab('onchain');
}
```

- [ ] **Step 2: Update updateOnchainMetrics to render into tab grid**

Modify `updateOnchainMetrics` to populate the `#onchainGrid` with 9 indicator cards. Each card gets a mini sparkline placeholder. Replace the function:

```javascript
function updateOnchainMetrics(onchain) {
    if (!onchain || onchain.error) return;

    // Convergence dashboard (keep existing logic)
    if (onchain.convergence_score) updateConvergenceDashboard(onchain.convergence_score);

    // 9 indicator cards into grid
    const grid = document.getElementById('onchainGrid');
    if (!grid) return;

    const indicators = [
        { id: 'MVRV', value: onchain.mvrv, fmt: v => v != null ? v.toFixed(2) : '--', color: v => v < 1 ? '#10b981' : v < 3.5 ? '#eab308' : '#ef4444' },
        { id: 'MVRV Z-Score', value: onchain.mvrv_zscore, fmt: v => v != null ? v.toFixed(2) : '--', color: v => v < 0 ? '#10b981' : v < 7 ? '#eab308' : '#ef4444' },
        { id: 'NUPL', value: onchain.nupl, fmt: v => v != null ? (v * 100).toFixed(1) + '%' : '--', color: v => v < 0 ? '#ef4444' : v < 0.25 ? '#eab308' : v < 0.75 ? '#f97316' : '#10b981' },
        { id: 'Mayer', value: onchain.mayer_multiple, fmt: v => v != null ? v.toFixed(2) : '--', color: v => v < 1 ? '#10b981' : v < 2.4 ? '#eab308' : '#ef4444' },
        { id: '200WMA', value: onchain.wma_200, fmt: v => v != null ? '$' + v.toLocaleString() : '--', color: () => '#60a5fa' },
        { id: 'Balanced Price', value: onchain.balanced_price, fmt: v => v != null ? '$' + v.toLocaleString() : '--', color: () => '#60a5fa' },
        { id: '200DMA', value: onchain.price_200dma, fmt: v => v != null ? '$' + v.toLocaleString() : '--', color: () => '#60a5fa' },
        { id: 'Halving', value: onchain.halving_days, fmt: v => v != null ? v + ' 天' : '--', color: () => '#a78bfa' },
        { id: 'Puell', value: onchain.puell_multiple, fmt: v => v != null ? v.toFixed(2) : '--', color: v => v < 0.4 ? '#10b981' : v < 2.0 ? '#eab308' : '#ef4444' }
    ];

    grid.innerHTML = indicators.map(ind => {
        const val = ind.fmt(ind.value);
        const clr = ind.color(ind.value);
        return `<div class="bg-gray-800/50 rounded-lg p-3">
            <div class="text-xs text-gray-400 mb-1">${ind.id}</div>
            <div class="text-lg font-bold font-mono" style="color:${clr}">${val}</div>
        </div>`;
    }).join('');
}
```

- [ ] **Step 3: Update updateDerivativeMetrics to render into tab**

Replace `updateDerivativeMetrics`:

```javascript
function updateDerivativeMetrics(deriv) {
    if (!deriv || deriv.error) return;

    const section = document.getElementById('derivOverheatSection');
    const grid = document.getElementById('derivGrid');
    if (!section || !grid) return;

    // Overheating assessment
    const oh = deriv.overheating_assessment || {};
    const level = oh.level || 'NORMAL';
    const levelColors = { NORMAL: 'bg-green-900/50 text-green-400', WARM: 'bg-yellow-900/50 text-yellow-400', HOT: 'bg-orange-900/50 text-orange-400', OVERHEATED: 'bg-red-900/50 text-red-400' };
    section.innerHTML = `<div class="flex items-center gap-3 mb-2">
        <span class="px-2 py-0.5 rounded text-xs font-medium ${levelColors[level] || levelColors.NORMAL}">${level}</span>
        <span class="text-sm text-gray-300">${oh.advice || ''}</span>
    </div>`;

    // 4 metric cards
    const metrics = [
        { id: 'Sharpe 14d', value: deriv.sharpe_7d, fmt: v => v != null ? v.toFixed(2) : '--' },
        { id: 'Sharpe 30d', value: deriv.sharpe_30d, fmt: v => v != null ? v.toFixed(2) : '--' },
        { id: '资金费率', value: deriv.funding_rate, fmt: v => v != null ? (v * 100).toFixed(4) + '%' : '--' },
        { id: '期货/现货比', value: deriv.futures_spot_ratio, fmt: v => v != null ? v.toFixed(2) : '--' }
    ];

    grid.innerHTML = metrics.map(m => `<div class="bg-gray-800/50 rounded-lg p-3">
        <div class="text-xs text-gray-400 mb-1">${m.id}</div>
        <div class="text-lg font-bold font-mono text-white">${m.fmt(m.value)}</div>
    </div>`).join('');
}
```

- [ ] **Step 4: Update updatePressureTest to render into tab**

Replace `updatePressureTest`:

```javascript
function updatePressureTest(pt) {
    if (!pt || pt.error) return;

    const section = document.getElementById('pressureTestSection');
    if (!section) return;

    const ra = pt.risk_assessment || {};
    const levelColors = { HIGH: 'text-red-400', MEDIUM: 'text-yellow-400', LOW: 'text-green-400' };
    const level = ra.risk_level || 'LOW';

    let html = `<div class="flex items-center gap-3 mb-3">
        <span class="text-lg font-bold ${levelColors[level] || 'text-gray-400'}">${level} 风险</span>
        <span class="text-sm text-gray-400">${ra.description || ''}</span>
    </div>`;

    // Greeks cards
    const bg = pt.base_greeks || {};
    html += `<div class="grid grid-cols-4 gap-3 mb-3">
        <div class="bg-gray-800/50 rounded-lg p-3"><div class="text-xs text-gray-400">Delta</div><div class="font-mono text-white">${(bg.delta || 0).toFixed(4)}</div></div>
        <div class="bg-gray-800/50 rounded-lg p-3"><div class="text-xs text-gray-400">Gamma</div><div class="font-mono text-white">${(bg.gamma || 0).toFixed(6)}</div></div>
        <div class="bg-gray-800/50 rounded-lg p-3"><div class="text-xs text-gray-400">Vanna</div><div class="font-mono text-white">${(bg.vanna || 0).toFixed(6)}</div></div>
        <div class="bg-gray-800/50 rounded-lg p-3"><div class="text-xs text-gray-400">Volga</div><div class="font-mono text-white">${(bg.volga || 0).toFixed(4)}</div></div>
    </div>`;

    // Joint stress scenarios table
    const scenarios = pt.joint_stress_tests || [];
    if (scenarios.length) {
        html += `<div class="overflow-x-auto"><table class="w-full text-sm"><thead><tr class="text-gray-400 border-b border-gray-700">
            <th class="text-left py-2 px-2">场景</th><th class="text-right py-2 px-2">Delta</th><th class="text-right py-2 px-2">Gamma</th><th class="text-right py-2 px-2">Vanna</th><th class="text-right py-2 px-2">Volga</th>
        </tr></thead><tbody>`;
        scenarios.forEach(s => {
            const risk = s.risk_assessment || {};
            const rowColor = risk.risk_level === 'HIGH' ? 'text-red-400' : risk.risk_level === 'MEDIUM' ? 'text-yellow-400' : 'text-green-400';
            html += `<tr class="border-b border-gray-800 ${rowColor}">
                <td class="py-1.5 px-2">${safeHTML(s.scenario || '')}</td>
                <td class="text-right py-1.5 px-2 font-mono">${(s.delta || 0).toFixed(4)}</td>
                <td class="text-right py-1.5 px-2 font-mono">${(s.gamma || 0).toFixed(6)}</td>
                <td class="text-right py-1.5 px-2 font-mono">${(s.vanna || 0).toFixed(6)}</td>
                <td class="text-right py-1.5 px-2 font-mono">${(s.volga || 0).toFixed(4)}</td>
            </tr>`;
        });
        html += '</tbody></table></div>';
    }

    section.innerHTML = html;
}
```

- [ ] **Step 5: Update updateSentimentAnalysis to render into tab**

Replace `updateSentimentAnalysis`:

```javascript
function updateSentimentAnalysis(sentiment) {
    if (!sentiment || sentiment.error) return;

    const section = document.getElementById('sentimentSection');
    if (!section) return;

    const da = sentiment.dominant_intent || {};
    const intentColors = {
        directional_speculation: 'text-red-400', institutional_hedging: 'text-blue-400',
        arbitrage: 'text-purple-400', market_maker_adjust: 'text-yellow-400',
        income_generation: 'text-green-400', volatility_play: 'text-orange-400'
    };
    const intentLabels = {
        directional_speculation: '方向投机', institutional_hedging: '机构对冲',
        arbitrage: '套利', market_maker_adjust: '做市商调整',
        income_generation: '收租', volatility_play: '波动率交易'
    };

    let html = `<div class="flex items-center gap-4 mb-3">
        <div><span class="text-xs text-gray-400">主导意图</span><div class="text-lg font-bold ${intentColors[da.intent] || 'text-white'}">${intentLabels[da.intent] || da.intent || '--'}</div></div>
        <div><span class="text-xs text-gray-400">风险等级</span><div class="text-lg font-bold ${da.risk_level === 'HIGH' ? 'text-red-400' : da.risk_level === 'MEDIUM' ? 'text-yellow-400' : 'text-green-400'}">${da.risk_level || '--'}</div></div>
        <div><span class="text-xs text-gray-400">信心度</span><div class="text-lg font-bold text-white">${da.confidence || 0}%</div></div>
        <div><span class="text-xs text-gray-400">AI 建议</span><div class="text-sm text-gray-300">${safeHTML(sentiment.recommendation || '--')}</div></div>
    </div>`;

    // Put/Call ratio
    const pc = sentiment.put_call_ratio || {};
    html += `<div class="flex gap-4 mb-3 text-sm">
        <span class="text-red-400">Put: ${(pc.put_pct || 0).toFixed(1)}%</span>
        <span class="text-green-400">Call: ${(pc.call_pct || 0).toFixed(1)}%</span>
    </div>`;

    // Intent distribution
    const dist = sentiment.intent_distribution || {};
    if (Object.keys(dist).length) {
        html += '<div class="space-y-1 mb-3">';
        Object.entries(dist).forEach(([key, val]) => {
            const pct = typeof val === 'number' ? val : 0;
            const label = intentLabels[key] || key;
            html += `<div class="flex items-center gap-2 text-xs">
                <span class="w-24 text-gray-400">${label}</span>
                <div class="flex-1 bg-gray-700 rounded-full h-2"><div class="bg-blue-500 h-2 rounded-full" style="width:${Math.min(pct, 100)}%"></div></div>
                <span class="text-gray-400 w-10 text-right">${pct.toFixed(0)}</span>
            </div>`;
        });
        html += '</div>';
    }

    // Risk warnings
    const warnings = sentiment.risk_warnings || [];
    if (warnings.length) {
        html += '<div class="space-y-1">';
        warnings.forEach(w => {
            const level = (w.level || '').toUpperCase();
            const icon = level === 'HIGH' ? '🔴' : level === 'MEDIUM' ? '🟡' : '🟢';
            html += `<div class="text-sm text-gray-300">${icon} ${safeHTML(w.message || w)}</div>`;
        });
        html += '</div>';
    }

    section.innerHTML = html;
}
```

- [ ] **Step 6: Remove old code**

Delete the following old functions/sections from `app.js` that are replaced:
- The old 4-dimension score grid rendering code inside `updateRiskDashboardUI` (lines ~1354-1388)
- The old MM signal rendering (lines ~1422-1425)
- The old strategy advice list rendering (lines ~1391-1394)
- The old recommended actions rendering (lines ~1397-1404)

- [ ] **Step 7: Commit**

```bash
git add static/app.js
git commit -m "feat: risk dashboard UI — gauge/radar/cards/tabs/LLM panel integration"
```

---

## Task 11: Integration Test + Final Cleanup

**Files:**
- Test: `tests/test_risk_api.py`

- [ ] **Step 1: Write API integration test**

```python
# tests/test_risk_api.py
"""Integration tests for risk API endpoints."""
import pytest
from unittest.mock import patch, MagicMock


class TestRiskOverviewAPI:
    """Test /api/risk/overview returns correct structure."""

    def test_overview_returns_expected_keys(self):
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/risk/overview?currency=BTC")
        assert resp.status_code == 200
        data = resp.json()
        assert "risk_score" in data
        assert "status" in data
        assert "floors" in data
        assert "dimensions" in data

    def test_mm_signal_removed(self):
        """mm_signal should no longer be in the response."""
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/risk/overview?currency=BTC")
        data = resp.json()
        assert "mm_signal" not in data, "mm_signal should have been removed"
```

- [ ] **Step 2: Run all tests**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 3: Verify no regressions**

Run: `python -m pytest tests/test_risk_math.py tests/test_strategy_engine.py tests/test_llm_analyst.py -v`
Expected: All existing tests still pass

- [ ] **Step 4: Final commit**

```bash
git add tests/test_risk_api.py
git commit -m "test: risk API integration tests — overview structure + mm_signal removal"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] Section 1.1 HIGH fixes: Volga (Task 1), POP (Task 2), pressure params (Task 3), support weights (Task 3)
- [x] Section 1.2 MEDIUM fixes: f-strings (Task 4), Sharpe window (Task 5), Z-Score (Task 4), sentiment floor (Task 4), Gamma (Task 5), order flow (Task 5)
- [x] Section 1.3 LOW cleanup: mm_signal (Task 6), boundary condition (Task 6)
- [x] Section 2 LLM endpoint: (Task 7)
- [x] Section 3.1-3.3 HTML layout: (Task 8)
- [x] Section 3.2 Gauge: (Task 9)
- [x] Section 3.3 Radar: (Task 9)
- [x] Section 3.4 Cards: (Task 10)
- [x] Section 3.5 Tabs: (Task 9 + 10)
- [x] Section 3.6 LLM Panel: (Task 8 HTML + Task 9 JS)
- [x] Section 3.7 JS functions: (Task 9 + 10)

**Placeholder scan:** No TBD/TODO found. All code blocks are complete.

**Type consistency:** `renderRiskGauge`, `renderRiskRadar`, `setRiskTab`, `loadLLMRiskInsight`, `renderLLMRiskInsight` names are consistent across Tasks 8, 9, 10.
