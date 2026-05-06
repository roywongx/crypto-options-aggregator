# IV Smile Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the IV Smile feature from a raw HTML bar chart into a Chart.js line chart with professional skew metrics, sentiment analysis, and trading strategy recommendations.

**Architecture:** New `services/iv_smile.py` service (IVSmileAnalyzer) handles all analysis logic. The `/api/charts/iv-smile` router delegates to this service. Frontend replaces the HTML bar chart with a Chart.js line chart and adds an analysis panel below.

**Tech Stack:** Python (service), FastAPI (router), Chart.js (frontend chart), vanilla JS (rendering)

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `dashboard/services/iv_smile.py` | IVSmileAnalyzer: data extraction, metrics, classification, recommendations |
| Create | `dashboard/tests/test_iv_smile.py` | Unit tests for IVSmileAnalyzer |
| Modify | `dashboard/routers/charts.py:249-333` | Delegate to IVSmileAnalyzer, return enriched response |
| Modify | `dashboard/static/index.html:388-407` | Add canvas element + analysis panel container |
| Modify | `dashboard/static/app.js:4072-4144` | Replace HTML bar chart with Chart.js line chart + analysis panel |

---

### Task 1: Create IVSmileAnalyzer Service (TDD)

**Files:**
- Create: `dashboard/tests/test_iv_smile.py`
- Create: `dashboard/services/iv_smile.py`

- [ ] **Step 1: Write failing tests for data extraction**

```python
# dashboard/tests/test_iv_smile.py
"""Tests for IV Smile analyzer service."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.iv_smile import IVSmileAnalyzer


def _make_contract(strike, iv, dte, option_type, oi=100, volume=10):
    return {
        "strike": strike, "mark_iv": iv, "dte": dte,
        "option_type": option_type, "oi": oi, "volume": volume
    }


class TestExtractAndNormalize:
    def test_extracts_valid_contracts(self):
        contracts = [
            _make_contract(95000, 45.0, 7, "P"),
            _make_contract(105000, 38.0, 7, "C"),
        ]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        assert result["smiles"] != {}
        assert "dte_7" in result["smiles"]

    def test_normalizes_decimal_iv(self):
        contracts = [_make_contract(100000, 0.45, 7, "P")]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        point = result["smiles"]["dte_7"]["puts"][0]
        assert point["iv"] == 45.0

    def test_filters_invalid_iv(self):
        contracts = [
            _make_contract(100000, 0, 7, "P"),
            _make_contract(100000, -5, 7, "P"),
            _make_contract(100000, 250, 7, "P"),
        ]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        assert result["smiles"] == {}

    def test_filters_zero_strike(self):
        contracts = [_make_contract(0, 45.0, 7, "P")]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        assert result["smiles"] == {}

    def test_filters_zero_dte(self):
        contracts = [_make_contract(100000, 45.0, 0, "P")]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        assert result["smiles"] == {}

    def test_filters_low_oi(self):
        contracts = [_make_contract(100000, 45.0, 7, "P", oi=0)]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        assert result["smiles"] == {}

    def test_computes_moneyness(self):
        contracts = [_make_contract(90000, 45.0, 7, "P")]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        point = result["smiles"]["dte_7"]["puts"][0]
        assert point["moneyness"] == -10.0

    def test_separates_puts_and_calls(self):
        contracts = [
            _make_contract(95000, 45.0, 7, "P"),
            _make_contract(105000, 38.0, 7, "C"),
        ]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        smile = result["smiles"]["dte_7"]
        assert len(smile["puts"]) == 1
        assert len(smile["calls"]) == 1
        assert smile["puts"][0]["type"] == "P"
        assert smile["calls"][0]["type"] == "C"

    def test_takes_nearest_3_expiries(self):
        contracts = []
        for dte in [3, 7, 14, 30, 60]:
            contracts.append(_make_contract(95000, 45.0, dte, "P"))
            contracts.append(_make_contract(105000, 38.0, dte, "C"))
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        assert len(result["smiles"]) == 3
        assert "dte_3" in result["smiles"]
        assert "dte_7" in result["smiles"]
        assert "dte_14" in result["smiles"]

    def test_empty_contracts_returns_empty(self):
        result = IVSmileAnalyzer.analyze([], 100000)
        assert result["smiles"] == {}
        assert result["analysis"] is None

    def test_uses_fallback_iv_field(self):
        contracts = [{"strike": 100000, "iv": 0.45, "dte": 7, "option_type": "P", "oi": 100}]
        result = IVSmileAnalyzer.analyze(contracts, 100000)
        assert result["smiles"]["dte_7"]["puts"][0]["iv"] == 45.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && python -m pytest tests/test_iv_smile.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'services.iv_smile'"

- [ ] **Step 3: Implement IVSmileAnalyzer — data extraction only**

```python
# dashboard/services/iv_smile.py
"""IV Smile Analyzer — skew metrics, form classification, sentiment, strategy recommendations."""
from typing import List, Dict, Optional


class IVSmileAnalyzer:
    @classmethod
    def analyze(cls, contracts_data: list, spot: float, currency: str = "BTC") -> dict:
        smiles = cls._extract_smiles(contracts_data, spot)
        analysis = cls._build_analysis(smiles, spot) if len(smiles) >= 2 else None
        return {"currency": currency, "spot": round(spot, 2), "smiles": smiles, "analysis": analysis}

    @classmethod
    def _extract_smiles(cls, contracts_data: list, spot: float) -> dict:
        by_expiry = {}
        for c in contracts_data:
            iv = c.get("mark_iv") or c.get("iv") or 0
            strike = c.get("strike", 0)
            dte = c.get("dte", 0)
            option_type = c.get("option_type", "")
            oi = c.get("oi") if c.get("oi") is not None else c.get("open_interest", 0)
            volume = c.get("volume") if c.get("volume") is not None else 0

            iv_float = float(iv) if iv else 0
            if 0 < iv_float < 1.0:
                iv_float *= 100
            elif iv_float > 200:
                continue

            if iv_float <= 0 or float(strike) <= 0 or float(dte) <= 0:
                continue
            if float(oi) < 1:
                continue

            exp_key = int(float(dte))
            if exp_key not in by_expiry:
                by_expiry[exp_key] = []
            by_expiry[exp_key].append({
                "strike": float(strike),
                "iv": round(iv_float, 2),
                "type": option_type.upper()[0] if option_type else "?",
                "oi": float(oi),
                "volume": float(volume) if volume else 0,
                "moneyness": round((float(strike) - spot) / spot * 100, 2) if spot > 0 else 0,
            })

        sorted_expiries = sorted(by_expiry.keys())
        result = {}
        for exp_dte in sorted_expiries[:3]:
            points = sorted(by_expiry[exp_dte], key=lambda x: x["strike"])
            puts = [p for p in points if p["type"] == "P"]
            calls = [p for p in points if p["type"] == "C"]
            result[f"dte_{exp_dte}"] = {"dte": exp_dte, "puts": puts, "calls": calls, "all": points}
        return result

    @classmethod
    def _build_analysis(cls, smiles: dict, spot: float) -> Optional[dict]:
        return None  # placeholder — implemented in Task 2
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && python -m pytest tests/test_iv_smile.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/services/iv_smile.py dashboard/tests/test_iv_smile.py
git commit -m "feat(iv-smile): add IVSmileAnalyzer with data extraction"
```

---

### Task 2: Add Metrics Calculation (TDD)

**Files:**
- Modify: `dashboard/tests/test_iv_smile.py`
- Modify: `dashboard/services/iv_smile.py`

- [ ] **Step 1: Write failing tests for metrics**

Append to `dashboard/tests/test_iv_smile.py`:

```python
class TestMetrics:
    def _make_smile_data(self, skew=0):
        """Generate synthetic smile data with controllable skew."""
        contracts = []
        spot = 100000
        for strike_pct in [-10, -7, -5, -3, 0, 3, 5, 7, 10]:
            strike = spot * (1 + strike_pct / 100)
            # Put IV increases as strike decreases (positive skew)
            put_iv = 40 + skew * abs(strike_pct) / 10 + (0 if strike_pct >= 0 else 5)
            call_iv = 40 + (0 if strike_pct <= 0 else 3)
            contracts.append(_make_contract(strike, put_iv, 14, "P"))
            contracts.append(_make_contract(strike, call_iv, 14, "C"))
        return IVSmileAnalyzer.analyze(contracts, spot)

    def test_atm_iv_present(self):
        result = self._make_smile_data()
        assert result["analysis"] is not None
        assert "atm_iv" in result["analysis"]["metrics"]
        assert result["analysis"]["metrics"]["atm_iv"] > 0

    def test_skew_25d_positive_for_put_heavy(self):
        result = self._make_smile_data(skew=2)
        assert result["analysis"]["metrics"]["skew_25d"] > 0

    def test_put_skew_pct_calculated(self):
        result = self._make_smile_data(skew=2)
        assert result["analysis"]["metrics"]["put_skew_pct"] > 0

    def test_call_skew_pct_calculated(self):
        result = self._make_smile_data(skew=0)
        assert "call_skew_pct" in result["analysis"]["metrics"]

    def test_skew_slope_present(self):
        result = self._make_smile_data()
        assert "skew_slope" in result["analysis"]["metrics"]

    def test_curvature_present(self):
        result = self._make_smile_data()
        assert "curvature" in result["analysis"]["metrics"]

    def test_by_expiry_has_metrics(self):
        result = self._make_smile_data()
        by_expiry = result["analysis"]["by_expiry"]
        assert len(by_expiry) > 0
        for entry in by_expiry:
            assert "atm_iv" in entry
            assert "skew_25d" in entry
            assert "form" in entry
            assert "point_count" in entry
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && python -m pytest tests/test_iv_smile.py::TestMetrics -v`
Expected: FAIL — `_build_analysis` returns None, so `analysis` is None

- [ ] **Step 3: Implement metrics calculation**

Replace the `_build_analysis` placeholder in `dashboard/services/iv_smile.py`:

```python
    @classmethod
    def _build_analysis(cls, smiles: dict, spot: float) -> Optional[dict]:
        if not smiles or not spot:
            return None

        expiry_metrics = []
        for key, smile in smiles.items():
            all_pts = smile.get("all", [])
            if len(all_pts) < 3:
                continue

            atm_iv = cls._find_atm_iv(all_pts, spot)
            skew_25d = cls._calc_25d_skew(all_pts, spot)
            put_skew_pct = cls._calc_side_skew(all_pts, spot, "P", atm_iv)
            call_skew_pct = cls._calc_side_skew(all_pts, spot, "C", atm_iv)
            skew_slope = cls._calc_skew_slope(all_pts, spot)
            curvature = cls._calc_curvature(all_pts, spot, atm_iv)
            form = cls._classify_form(put_skew_pct, call_skew_pct)

            expiry_metrics.append({
                "dte": smile["dte"],
                "atm_iv": round(atm_iv, 2),
                "skew_25d": round(skew_25d, 2),
                "put_skew_pct": round(put_skew_pct, 2),
                "call_skew_pct": round(call_skew_pct, 2),
                "skew_slope": round(skew_slope, 4),
                "curvature": round(curvature, 2),
                "form": form,
                "form_label": cls._FORM_LABELS.get(form, form),
                "point_count": len(all_pts),
            })

        if not expiry_metrics:
            return None

        # Aggregate metrics (weighted by 1/dte — near-term matters more)
        total_weight = sum(1.0 / m["dte"] for m in expiry_metrics)
        agg = {}
        for key in ["atm_iv", "skew_25d", "put_skew_pct", "call_skew_pct", "skew_slope", "curvature"]:
            agg[key] = round(sum(m[key] / m["dte"] for m in expiry_metrics) / total_weight, 2)

        form = cls._classify_form(agg["put_skew_pct"], agg["call_skew_pct"])
        sentiment = cls._assess_sentiment(agg["skew_25d"], agg["put_skew_pct"], agg["call_skew_pct"])
        recommendations = cls._build_recommendations(form, agg, sentiment)

        return {
            "form": form,
            "form_label": cls._FORM_LABELS.get(form, form),
            "form_icon": cls._FORM_ICONS.get(form, ""),
            "sentiment": sentiment,
            "metrics": agg,
            "by_expiry": expiry_metrics,
            "recommendations": recommendations,
        }
```

Add helper methods to the class:

```python
    _FORM_LABELS = {
        "smile": "对称微笑型", "put_skew": "下行恐慌型",
        "call_skew": "上行狂热型", "flat": "平坦型",
    }
    _FORM_ICONS = {"smile": "😐", "put_skew": "📉", "call_skew": "📈", "flat": "➡️"}

    @staticmethod
    def _find_atm_iv(points: list, spot: float) -> float:
        closest = min(points, key=lambda p: abs(p["strike"] - spot))
        return closest["iv"]

    @staticmethod
    def _calc_25d_skew(points: list, spot: float) -> float:
        put_candidates = [p for p in points if p["type"] == "P" and p["moneyness"] < -5 and p["moneyness"] > -15]
        call_candidates = [p for p in points if p["type"] == "C" and p["moneyness"] > 5 and p["moneyness"] < 15]
        put_iv = sum(p["iv"] for p in put_candidates) / len(put_candidates) if put_candidates else 0
        call_iv = sum(p["iv"] for p in call_candidates) / len(call_candidates) if call_candidates else 0
        return put_iv - call_iv

    @staticmethod
    def _calc_side_skew(points: list, spot: float, side: str, atm_iv: float) -> float:
        if atm_iv <= 0:
            return 0
        if side == "P":
            otm = [p for p in points if p["type"] == "P" and p["moneyness"] < -3]
        else:
            otm = [p for p in points if p["type"] == "C" and p["moneyness"] > 3]
        if not otm:
            return 0
        avg_otm_iv = sum(p["iv"] for p in otm) / len(otm)
        return (avg_otm_iv - atm_iv) / atm_iv * 100

    @staticmethod
    def _calc_skew_slope(points: list, spot: float) -> float:
        valid = [(p["moneyness"], p["iv"]) for p in points if p["moneyness"] != 0]
        if len(valid) < 3:
            return 0
        x_mean = sum(v[0] for v in valid) / len(valid)
        y_mean = sum(v[1] for v in valid) / len(valid)
        num = sum((x - x_mean) * (y - y_mean) for x, y in valid)
        den = sum((x - x_mean) ** 2 for x, _ in valid)
        return num / den if den > 0 else 0

    @staticmethod
    def _calc_curvature(points: list, spot: float, atm_iv: float) -> float:
        if atm_iv <= 0:
            return 0
        wings = [p["iv"] for p in points if abs(p["moneyness"]) > 7]
        if not wings:
            return 0
        wing_avg = sum(wings) / len(wings)
        return (wing_avg - atm_iv) / atm_iv * 100

    @staticmethod
    def _classify_form(put_skew_pct: float, call_skew_pct: float) -> str:
        if put_skew_pct > 5 and call_skew_pct > 5:
            return "smile"
        if put_skew_pct > 5:
            return "put_skew"
        if call_skew_pct > 5:
            return "call_skew"
        return "flat"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && python -m pytest tests/test_iv_smile.py::TestMetrics -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/services/iv_smile.py dashboard/tests/test_iv_smile.py
git commit -m "feat(iv-smile): add skew metrics and form classification"
```

---

### Task 3: Add Sentiment and Recommendations (TDD)

**Files:**
- Modify: `dashboard/tests/test_iv_smile.py`
- Modify: `dashboard/services/iv_smile.py`

- [ ] **Step 1: Write failing tests for sentiment and recommendations**

Append to `dashboard/tests/test_iv_smile.py`:

```python
class TestSentiment:
    def test_panic_on_extreme_put_skew(self):
        sentiment = IVSmileAnalyzer._assess_sentiment(20, 35, -2)
        assert sentiment["state"] == "PANIC"

    def test_fear_on_high_put_skew(self):
        sentiment = IVSmileAnalyzer._assess_sentiment(10, 18, -1)
        assert sentiment["state"] == "FEAR"

    def test_neutral_on_low_skew(self):
        sentiment = IVSmileAnalyzer._assess_sentiment(1, 2, 1)
        assert sentiment["state"] == "NEUTRAL"

    def test_greed_on_negative_skew(self):
        sentiment = IVSmileAnalyzer._assess_sentiment(-5, -2, 8)
        assert sentiment["state"] == "GREED"

    def test_euphoria_on_extreme_negative_skew(self):
        sentiment = IVSmileAnalyzer._assess_sentiment(-12, -3, 20)
        assert sentiment["state"] == "EUPHORIA"

    def test_sentiment_has_required_fields(self):
        sentiment = IVSmileAnalyzer._assess_sentiment(0, 0, 0)
        for field in ["state", "label", "icon", "color"]:
            assert field in sentiment


class TestRecommendations:
    def test_sell_put_on_fear(self):
        sentiment = {"state": "FEAR", "label": "市场恐慌", "icon": "😰", "color": "#ef4444"}
        metrics = {"atm_iv": 50, "skew_25d": 10, "put_skew_pct": 20, "call_skew_pct": -2, "skew_slope": 0.1, "curvature": 3}
        recs = IVSmileAnalyzer._build_recommendations("put_skew", metrics, sentiment)
        assert len(recs) > 0
        assert any(r["type"] == "sell_put" for r in recs)

    def test_high_confidence_on_extreme_skew(self):
        sentiment = {"state": "FEAR", "label": "市场恐慌", "icon": "😰", "color": "#ef4444"}
        metrics = {"atm_iv": 50, "skew_25d": 10, "put_skew_pct": 20, "call_skew_pct": -2, "skew_slope": 0.1, "curvature": 3}
        recs = IVSmileAnalyzer._build_recommendations("put_skew", metrics, sentiment)
        high_recs = [r for r in recs if r["confidence"] == "HIGH"]
        assert len(high_recs) > 0

    def test_recommendation_has_required_fields(self):
        sentiment = {"state": "NEUTRAL", "label": "中性", "icon": "😐", "color": "#9497a9"}
        metrics = {"atm_iv": 30, "skew_25d": 1, "put_skew_pct": 2, "call_skew_pct": 1, "skew_slope": 0.01, "curvature": 1}
        recs = IVSmileAnalyzer._build_recommendations("flat", metrics, sentiment)
        for r in recs:
            for field in ["type", "title", "body", "action", "confidence"]:
                assert field in r

    def test_iron_condor_on_flat_high_iv(self):
        sentiment = {"state": "NEUTRAL", "label": "中性", "icon": "😐", "color": "#9497a9"}
        metrics = {"atm_iv": 55, "skew_25d": 1, "put_skew_pct": 2, "call_skew_pct": 1, "skew_slope": 0.01, "curvature": 1}
        recs = IVSmileAnalyzer._build_recommendations("flat", metrics, sentiment)
        assert any(r["type"] == "iron_condor" for r in recs)

    def test_long_straddle_on_flat_low_iv(self):
        sentiment = {"state": "NEUTRAL", "label": "中性", "icon": "😐", "color": "#9497a9"}
        metrics = {"atm_iv": 20, "skew_25d": 0, "put_skew_pct": 1, "call_skew_pct": 1, "skew_slope": 0.005, "curvature": 0.5}
        recs = IVSmileAnalyzer._build_recommendations("flat", metrics, sentiment)
        assert any(r["type"] == "long_straddle" for r in recs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && python -m pytest tests/test_iv_smile.py::TestSentiment tests/test_iv_smile.py::TestRecommendations -v`
Expected: FAIL — methods don't exist yet

- [ ] **Step 3: Implement sentiment and recommendations**

Add to `dashboard/services/iv_smile.py`:

```python
    @staticmethod
    def _assess_sentiment(skew_25d: float, put_skew_pct: float, call_skew_pct: float) -> dict:
        if skew_25d > 15 or put_skew_pct > 30:
            return {"state": "PANIC", "label": "极度恐慌", "icon": "😱", "color": "#ef4444"}
        if skew_25d > 8 or put_skew_pct > 15:
            return {"state": "FEAR", "label": "市场恐慌", "icon": "😰", "color": "#ef4444"}
        if skew_25d > 3 or put_skew_pct > 5:
            return {"state": "CAUTIOUS", "label": "偏谨慎", "icon": "🤔", "color": "#f59e0b"}
        if skew_25d < -8 or call_skew_pct > 15:
            return {"state": "EUPHORIA", "label": "极度狂热", "icon": "🚀", "color": "#7132f5"}
        if skew_25d < -3 or call_skew_pct > 5:
            return {"state": "GREED", "label": "市场贪婪", "icon": "🤑", "color": "#7132f5"}
        return {"state": "NEUTRAL", "label": "中性", "icon": "😐", "color": "#9497a9"}

    @classmethod
    def _build_recommendations(cls, form: str, metrics: dict, sentiment: dict) -> list:
        recs = []
        atm_iv = metrics["atm_iv"]
        put_skew = metrics["put_skew_pct"]
        call_skew = metrics["call_skew_pct"]
        state = sentiment["state"]

        if form == "put_skew" and atm_iv > 40 and state in ("FEAR", "PANIC"):
            recs.append({
                "type": "sell_put", "title": "卖 OTM Put",
                "body": f"下行 IV 显著偏高 ({put_skew:.1f}%)，卖出虚值 Put 可收取超额恐慌溢价",
                "action": "Delta 0.15-0.25，DTE 7-14",
                "confidence": "HIGH",
            })
        elif form == "put_skew" and atm_iv > 30:
            recs.append({
                "type": "put_spread", "title": "卖 Put Spread",
                "body": f"下行 IV 偏高 ({put_skew:.1f}%)，用价差策略限制风险",
                "action": "卖 Put Delta 0.20 / 买 Put Delta 0.10",
                "confidence": "MEDIUM",
            })

        if form == "call_skew" and atm_iv > 40 and state in ("GREED", "EUPHORIA"):
            recs.append({
                "type": "sell_call", "title": "卖 OTM Call",
                "body": f"上行 IV 显著偏高 ({call_skew:.1f}%)，卖出虚值 Call 收取狂热溢价",
                "action": "Delta 0.15-0.25，DTE 7-14",
                "confidence": "HIGH",
            })

        if form == "flat" and atm_iv > 45:
            recs.append({
                "type": "iron_condor", "title": "铁鹰策略",
                "body": f"微笑平坦且 IV 偏高 ({atm_iv:.1f}%)，适合同时卖出虚值 Put 和 Call",
                "action": "Put Delta 0.15 / Call Delta 0.10，DTE 14-30",
                "confidence": "HIGH",
            })
        elif form == "flat" and atm_iv < 25:
            recs.append({
                "type": "long_straddle", "title": "买跨式",
                "body": f"微笑平坦且 IV 偏低 ({atm_iv:.1f}%)，买入跨式赌波动率上升",
                "action": "ATM Call + ATM Put，DTE 30+",
                "confidence": "MEDIUM",
            })

        if form == "smile" and atm_iv > 40:
            recs.append({
                "type": "sell_strangle", "title": "卖宽跨式",
                "body": "两端 IV 偏高，同时卖出虚值 Put 和 Call 收取双端溢价",
                "action": "Put Delta 0.15 / Call Delta 0.15",
                "confidence": "MEDIUM",
            })

        # Extreme skew → risk reversal
        if abs(metrics["skew_25d"]) > 15:
            if metrics["skew_25d"] > 0:
                recs.append({
                    "type": "risk_reversal", "title": "Risk Reversal (卖Put买Call)",
                    "body": f"极端 put skew ({metrics['skew_25d']:.1f})，卖高IV端买低IV端",
                    "action": "卖 OTM Put + 买 OTM Call",
                    "confidence": "HIGH",
                })
            else:
                recs.append({
                    "type": "risk_reversal", "title": "Risk Reversal (卖Call买Put)",
                    "body": f"极端 call skew ({metrics['skew_25d']:.1f})，卖高IV端买低IV端",
                    "action": "卖 OTM Call + 买 OTM Put",
                    "confidence": "HIGH",
                })

        return recs[:3]  # max 3 recommendations
```

- [ ] **Step 4: Run all tests**

Run: `cd dashboard && python -m pytest tests/test_iv_smile.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/services/iv_smile.py dashboard/tests/test_iv_smile.py
git commit -m "feat(iv-smile): add sentiment analysis and strategy recommendations"
```

---

### Task 4: Update Router to Use IVSmileAnalyzer

**Files:**
- Modify: `dashboard/routers/charts.py:249-333`

- [ ] **Step 1: Replace the get_iv_smile endpoint**

Replace lines 249-333 of `dashboard/routers/charts.py`:

```python
@router.get("/iv-smile")
async def get_iv_smile(currency: str = "BTC"):
    """获取波动率微笑数据 + 分析"""
    from services.spot_price import get_spot_price
    from services.iv_smile import IVSmileAnalyzer
    from db.async_connection import execute_read_async

    try:
        spot = get_spot_price(currency)
    except (RuntimeError, ValueError):
        from constants import get_spot_fallback
        spot = get_spot_fallback(currency)

    rows = await execute_read_async("""
        SELECT contracts_data FROM scan_records
        WHERE currency = ? AND contracts_data IS NOT NULL
        ORDER BY timestamp DESC LIMIT 1
    """, (currency,))

    if not rows or not rows[0][0]:
        return {"error": "无合约数据", "smiles": {}, "analysis": None, "currency": currency, "spot": spot}

    try:
        contracts = json.loads(rows[0][0])
    except json.JSONDecodeError:
        return {"error": "数据解析失败", "smiles": {}, "analysis": None, "currency": currency, "spot": spot}

    result = IVSmileAnalyzer.analyze(contracts, spot, currency)

    if not result["smiles"]:
        return {"error": "无有效 IV 数据", "smiles": {}, "analysis": None, "currency": currency, "spot": spot}

    return result
```

- [ ] **Step 2: Restart server and verify API response**

Run: restart the server, then `curl http://localhost:8000/api/charts/iv-smile?currency=BTC`
Expected: JSON with `smiles`, `analysis` (with `metrics`, `sentiment`, `recommendations`)

- [ ] **Step 3: Commit**

```bash
git add dashboard/routers/charts.py
git commit -m "feat(iv-smile): delegate to IVSmileAnalyzer, return analysis"
```

---

### Task 5: Update Frontend — Chart.js + Analysis Panel

**Files:**
- Modify: `dashboard/static/index.html:388-407`
- Modify: `dashboard/static/app.js:4072-4144`

- [ ] **Step 1: Update HTML — add canvas + analysis panel container**

Replace lines 388-407 of `dashboard/static/index.html`:

```html
        <!-- IV 波动率微笑 -->
        <section id="ivSmileSection" class="card-glass rounded-xl p-5 mb-6 border-l-4 border-[#7132f5]">
            <div class="flex items-center justify-between mb-4">
                <div class="flex items-center gap-2">
                    <span class="text-xl">📈</span>
                    <h3 class="font-semibold text-lg">波动率微笑 (IV Smile)</h3>
                </div>
                <select id="ivSmileCurrency" class="input-dark rounded-lg px-3 py-1.5 text-sm" onchange="loadIVSmile()">
                    <option value="BTC">BTC</option>
                    <option value="ETH">ETH</option>
                </select>
            </div>
            <div id="ivSmileChart" style="height: 280px; position: relative;">
                <canvas id="ivSmileCanvas"></canvas>
            </div>
            <div id="ivSmileAnalysis" class="mt-4"></div>
        </section>
```

- [ ] **Step 2: Rewrite loadIVSmile with Chart.js + analysis panel**

Replace the `loadIVSmile` function in `dashboard/static/app.js` (lines 4072-4144):

```javascript
let _ivSmileChart = null;

async function loadIVSmile() {
    const canvas = document.getElementById('ivSmileCanvas');
    const analysisDiv = document.getElementById('ivSmileAnalysis');
    if (!canvas) return;

    if (analysisDiv) analysisDiv.innerHTML = '';

    try {
        const currency = document.getElementById('ivSmileCurrency')?.value || 'BTC';
        const resp = await safeFetch(`${API_BASE}/api/charts/iv-smile?currency=${currency}`);
        const data = await resp.json();

        if (data.error) {
            if (_ivSmileChart) { _ivSmileChart.destroy(); _ivSmileChart = null; }
            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            if (analysisDiv) analysisDiv.innerHTML = `<div class="text-[#f59e0b] text-sm py-4 text-center">${safeHTML(data.error)}</div>`;
            return;
        }

        const smiles = data.smiles || {};
        const spot = data.spot || 0;
        const expiryKeys = Object.keys(smiles).sort((a, b) => smiles[a].dte - smiles[b].dte);

        if (expiryKeys.length === 0) return;

        const colors = ['#ef4444', '#f59e0b', '#3b82f6'];
        const dashPatterns = [[], [5, 5], [10, 5]];
        const datasets = [];

        expiryKeys.forEach((key, i) => {
            const smile = smiles[key];
            const all = (smile.all || []).sort((a, b) => a.strike - b.strike);
            if (all.length === 0) return;

            datasets.push({
                label: `${smile.dte}D`,
                data: all.map(p => ({ x: p.strike, y: p.iv })),
                borderColor: colors[i % colors.length],
                backgroundColor: colors[i % colors.length] + '20',
                borderDash: dashPatterns[i % dashPatterns.length],
                tension: 0.3,
                borderWidth: 2,
                pointRadius: 3,
                pointHoverRadius: 6,
                fill: false,
            });
        });

        if (_ivSmileChart) _ivSmileChart.destroy();

        _ivSmileChart = new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: { datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'nearest', intersect: false },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        labels: { color: '#9497a9', usePointStyle: true, pointStyle: 'line', padding: 15, font: { size: 11 } }
                    },
                    tooltip: {
                        backgroundColor: '#1a1b23',
                        titleColor: '#e4e4e7',
                        bodyColor: '#9497a9',
                        borderColor: '#333',
                        borderWidth: 1,
                        callbacks: {
                            title: (items) => `$${items[0].parsed.x.toLocaleString()}`,
                            label: (item) => `${item.dataset.label}: IV ${item.parsed.y.toFixed(2)}%`,
                        }
                    },
                    annotation: spot > 0 ? {
                        annotations: {
                            atmLine: {
                                type: 'line',
                                xMin: spot,
                                xMax: spot,
                                borderColor: '#f59e0b',
                                borderWidth: 1,
                                borderDash: [4, 4],
                                label: {
                                    display: true,
                                    content: `ATM $${spot.toLocaleString()}`,
                                    position: 'start',
                                    backgroundColor: '#f59e0b20',
                                    color: '#f59e0b',
                                    font: { size: 10 },
                                }
                            }
                        }
                    } : undefined,
                },
                scales: {
                    x: {
                        type: 'linear',
                        title: { display: true, text: 'Strike', color: '#686b82', font: { size: 11 } },
                        ticks: {
                            color: '#686b82',
                            callback: (v) => '$' + v.toLocaleString(),
                            maxTicksLimit: 10,
                        },
                        grid: { color: 'rgba(255,255,255,0.05)' },
                    },
                    y: {
                        title: { display: true, text: 'IV %', color: '#686b82', font: { size: 11 } },
                        ticks: {
                            color: '#686b82',
                            callback: (v) => v.toFixed(0) + '%',
                        },
                        grid: { color: 'rgba(255,255,255,0.05)' },
                    },
                },
            },
        });

        // Render analysis panel
        if (analysisDiv && data.analysis) {
            renderIVSmileAnalysis(analysisDiv, data.analysis, spot);
        }
    } catch (e) {
        if (_ivSmileChart) { _ivSmileChart.destroy(); _ivSmileChart = null; }
        if (analysisDiv) analysisDiv.innerHTML = `<div class="text-[#ef4444] text-sm py-4 text-center">加载失败: ${safeHTML(e.message)}</div>`;
    }
}

function renderIVSmileAnalysis(container, analysis, spot) {
    const sent = analysis.sentiment || {};
    const met = analysis.metrics || {};
    const formIcon = analysis.form_icon || '';
    const formLabel = analysis.form_label || '';

    let html = `<div class="bg-[#22232e]/50 rounded-xl border border-[rgba(71,73,85,0.3)] p-4 space-y-4">`;

    // Row 1: Form + Sentiment + ATM IV
    html += `<div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3">
            <span class="text-lg">${formIcon}</span>
            <span class="text-sm font-medium text-[#e4e4e7]">${safeHTML(formLabel)}</span>
        </div>
        <div class="flex items-center gap-2">
            <span class="text-sm" style="color:${sent.color || '#9497a9'}">${sent.icon || ''} ${safeHTML(sent.label || '')}</span>
        </div>
        <div class="text-sm text-[#9497a9]">ATM IV: <span class="text-[#e4e4e7] font-bold">${met.atm_iv?.toFixed(1) || '--'}%</span></div>
    </div>`;

    // Row 2: Key metrics
    html += `<div class="grid grid-cols-3 gap-3 text-center">
        <div class="bg-[#1a1b23]/50 rounded-lg p-2">
            <div class="text-[10px] text-[#686b82]">25Δ Skew</div>
            <div class="text-sm font-bold ${(met.skew_25d || 0) > 0 ? 'text-[#ef4444]' : 'text-[#149e61]'}">${met.skew_25d > 0 ? '+' : ''}${met.skew_25d?.toFixed(1) || '--'}</div>
        </div>
        <div class="bg-[#1a1b23]/50 rounded-lg p-2">
            <div class="text-[10px] text-[#686b82]">Put 偏度</div>
            <div class="text-sm font-bold ${(met.put_skew_pct || 0) > 0 ? 'text-[#ef4444]' : 'text-[#149e61]'}">${met.put_skew_pct > 0 ? '+' : ''}${met.put_skew_pct?.toFixed(1) || '--'}%</div>
        </div>
        <div class="bg-[#1a1b23]/50 rounded-lg p-2">
            <div class="text-[10px] text-[#686b82]">曲度</div>
            <div class="text-sm font-bold text-[#e4e4e7]">${met.curvature?.toFixed(1) || '--'}%</div>
        </div>
    </div>`;

    // Row 3: By-expiry table
    const byExpiry = analysis.by_expiry || [];
    if (byExpiry.length > 0) {
        html += `<div class="overflow-x-auto"><table class="w-full text-xs">
            <thead><tr class="text-[#686b82] border-b border-gray-700/50">
                <th class="text-left py-1.5 px-2">到期</th>
                <th class="text-right py-1.5 px-2">ATM IV</th>
                <th class="text-right py-1.5 px-2">25Δ Skew</th>
                <th class="text-center py-1.5 px-2">形态</th>
                <th class="text-right py-1.5 px-2">点数</th>
            </tr></thead><tbody>`;
        for (const e of byExpiry) {
            html += `<tr class="border-b border-gray-800/30">
                <td class="py-1.5 px-2 text-[#e4e4e7]">${e.dte}D</td>
                <td class="py-1.5 px-2 text-right text-[#e4e4e7]">${e.atm_iv?.toFixed(1)}%</td>
                <td class="py-1.5 px-2 text-right ${(e.skew_25d || 0) > 0 ? 'text-[#ef4444]' : 'text-[#149e61]'}">${e.skew_25d > 0 ? '+' : ''}${e.skew_25d?.toFixed(1)}</td>
                <td class="py-1.5 px-2 text-center text-[#9497a9]">${safeHTML(e.form_label || e.form)}</td>
                <td class="py-1.5 px-2 text-right text-[#9497a9]">${e.point_count}</td>
            </tr>`;
        }
        html += `</tbody></table></div>`;
    }

    // Row 4: Recommendations
    const recs = analysis.recommendations || [];
    if (recs.length > 0) {
        html += `<div class="space-y-2">
            <div class="text-xs font-semibold text-[#7132f5]"><i class="fas fa-lightbulb mr-1"></i>策略建议</div>`;
        for (const r of recs) {
            const confColor = r.confidence === 'HIGH' ? '#149e61' : '#f59e0b';
            html += `<div class="flex items-start gap-2 bg-[#1a1b23]/40 rounded-lg p-2.5 border-l-2" style="border-color:${confColor}">
                <span class="text-xs font-bold px-1.5 py-0.5 rounded" style="color:${confColor}; background:${confColor}15">${r.confidence}</span>
                <div class="flex-1 min-w-0">
                    <div class="text-xs font-medium text-[#e4e4e7]">${safeHTML(r.title)}</div>
                    <div class="text-[11px] text-[#9497a9] mt-0.5">${safeHTML(r.body)}</div>
                    <div class="text-[11px] text-[#686b82] mt-0.5"><i class="fas fa-crosshairs mr-1"></i>${safeHTML(r.action)}</div>
                </div>
            </div>`;
        }
        html += `</div>`;
    }

    html += `</div>`;
    container.innerHTML = html;
}
```

- [ ] **Step 3: Check if chart.js annotation plugin is needed**

The ATM vertical line uses `chartjs-plugin-annotation`. Check if it's loaded. If not, either add the CDN script or remove the annotation config and use a simpler approach (vertical line dataset).

If the annotation plugin is NOT loaded (check index.html for the script tag), replace the annotation config with `undefined` and add a vertical line dataset instead:

```javascript
// Add this as the last dataset in the datasets array
if (spot > 0) {
    const allIvs = datasets.flatMap(d => d.data.map(p => p.y));
    const minY = Math.min(...allIvs);
    const maxY = Math.max(...allIvs);
    datasets.push({
        label: 'ATM',
        data: [{ x: spot, y: minY }, { x: spot, y: maxY }],
        borderColor: '#f59e0b',
        borderWidth: 1,
        borderDash: [4, 4],
        pointRadius: 0,
        fill: false,
    });
}
```

- [ ] **Step 4: Refresh browser and verify**

Open http://localhost:8000/ — IV Smile section should show:
- Chart.js line chart with 1-3 curves (per expiry)
- ATM vertical line
- Analysis panel with form, sentiment, metrics, expiry table, recommendations

- [ ] **Step 5: Commit**

```bash
git add dashboard/static/index.html dashboard/static/app.js
git commit -m "feat(iv-smile): Chart.js line chart + analysis panel"
```

---

### Task 6: Final Verification

- [ ] **Step 1: Run all unit tests**

Run: `cd dashboard && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Manual browser verification**

Open http://localhost:8000/, scroll to IV Smile section, verify:
- Chart renders with lines per expiry
- ATM marker visible
- Analysis panel shows: form label, sentiment, metrics grid, expiry table, recommendations
- Change currency dropdown — chart updates
- Hover on chart points — tooltip shows strike, IV, expiry

- [ ] **Step 3: Final commit if any fixes needed**

```bash
git add -A && git commit -m "fix(iv-smile): minor adjustments from manual testing"
```
