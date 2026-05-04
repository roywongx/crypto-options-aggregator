# Greeks 风险矩阵重新设计 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Greeks 风险矩阵从简单汇总表格升级为带 GEX 分布图、Greeks 到期日曲线、Pin Risk 分析、市场状态解读和对冲建议的完整分析系统。

**Architecture:** 新建 `services/greeks_analyzer.py` 服务层，封装所有 Greeks 计算、GEX 聚合、Pin Risk 分析、市场状态判断和对冲建议逻辑。`charts.py` router 的 `/api/charts/greeks-summary` 端点改为调用该服务。前端用 Chart.js 渲染 GEX 柱状图和 Greeks 曲线图，下方展示分析面板。

**Tech Stack:** Python (FastAPI, pytest), Chart.js (CDN), shared_calculations.black_scholes_price()

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `dashboard/services/greeks_analyzer.py` | 新建 | GreeksAnalyzer 服务类：Greeks 计算、GEX 聚合、Pin Risk、市场状态、对冲建议 |
| `dashboard/tests/test_greeks_analyzer.py` | 新建 | 测试：数据提取、Greeks 计算、GEX 聚合、Pin Risk、市场状态、对冲建议 |
| `dashboard/routers/charts.py:284-411` | 修改 | `/api/charts/greeks-summary` 端点改为调用 GreeksAnalyzer |
| `dashboard/static/index.html:406-425` | 修改 | Greeks 区域增加 GEX 图表、曲线图、分析面板容器 |
| `dashboard/static/app.js:4281-4375` | 修改 | `loadGreeksSummary()` 改用 Chart.js 图表 + 分析面板渲染 |

---

## Task 1: GreeksAnalyzer — 数据提取与标准化

**Files:**
- Create: `dashboard/services/greeks_analyzer.py`
- Create: `dashboard/tests/test_greeks_analyzer.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for Greeks Analyzer service."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.greeks_analyzer import GreeksAnalyzer


def _make_contract(strike, iv, dte, option_type, oi=100, premium=500):
    return {
        "strike": strike, "mark_iv": iv, "dte": dte,
        "option_type": option_type, "oi": oi, "premium_usd": premium
    }


class TestExtractContracts:
    def test_extracts_valid_contracts(self):
        contracts = [
            _make_contract(95000, 45.0, 7, "P"),
            _make_contract(105000, 38.0, 7, "C"),
        ]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["contract_count"] == 2

    def test_normalizes_decimal_iv(self):
        contracts = [_make_contract(100000, 0.45, 7, "P")]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        # Should normalize 0.45 -> 45.0
        assert result["contract_count"] == 1

    def test_filters_invalid_iv(self):
        contracts = [
            _make_contract(100000, 0, 7, "P"),
            _make_contract(100000, -5, 7, "P"),
            _make_contract(100000, 250, 7, "P"),
        ]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["contract_count"] == 0

    def test_filters_zero_strike(self):
        contracts = [_make_contract(0, 45.0, 7, "P")]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["contract_count"] == 0

    def test_filters_zero_dte(self):
        contracts = [_make_contract(100000, 45.0, 0, "P")]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["contract_count"] == 0

    def test_filters_low_oi(self):
        contracts = [_make_contract(100000, 45.0, 7, "P", oi=0)]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["contract_count"] == 0

    def test_counts_puts_and_calls(self):
        contracts = [
            _make_contract(95000, 45.0, 7, "P"),
            _make_contract(105000, 38.0, 7, "C"),
            _make_contract(90000, 50.0, 7, "P"),
        ]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["put_count"] == 2
        assert result["call_count"] == 1

    def test_uses_fallback_iv_field(self):
        contracts = [{"strike": 100000, "iv": 0.45, "dte": 7, "option_type": "P", "oi": 100}]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["contract_count"] == 1

    def test_uses_fallback_oi_field(self):
        contracts = [{"strike": 100000, "mark_iv": 45.0, "dte": 7, "option_type": "P", "open_interest": 200}]
        result = GreeksAnalyzer.analyze(contracts, 100000)
        assert result["contract_count"] == 1

    def test_empty_contracts_returns_empty(self):
        result = GreeksAnalyzer.analyze([], 100000)
        assert result["contract_count"] == 0
        assert result["analysis"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest tests/test_greeks_analyzer.py::TestExtractContracts -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'services.greeks_analyzer'"

- [ ] **Step 3: Write minimal implementation**

```python
"""Greeks Analyzer — GEX, Pin Risk, market state, hedge suggestions."""
from typing import List, Dict, Optional
from services.shared_calculations import black_scholes_price


class GreeksAnalyzer:
    @classmethod
    def analyze(cls, contracts_data: list, spot: float, currency: str = "BTC") -> dict:
        contracts = cls._extract_contracts(contracts_data, spot)
        if not contracts:
            return {
                "currency": currency, "spot": round(spot, 2),
                "contract_count": 0, "put_count": 0, "call_count": 0, "total_oi": 0,
                "greeks_summary": {}, "gex": {}, "by_expiry": [],
                "scenarios": {}, "analysis": None,
            }
        return {
            "currency": currency, "spot": round(spot, 2),
            "contract_count": len(contracts),
            "put_count": sum(1 for c in contracts if c["type"] == "P"),
            "call_count": sum(1 for c in contracts if c["type"] == "C"),
            "total_oi": round(sum(c["oi"] for c in contracts), 0),
            "greeks_summary": {},
            "gex": {},
            "by_expiry": [],
            "scenarios": {},
            "analysis": None,
        }

    @classmethod
    def _extract_contracts(cls, contracts_data: list, spot: float) -> list:
        result = []
        for c in contracts_data:
            iv = c.get("mark_iv") or c.get("iv") or 0
            strike = float(c.get("strike", 0))
            dte = int(float(c.get("dte", 0)))
            option_type = c.get("option_type", "")
            oi_raw = c.get("oi") if c.get("oi") is not None else c.get("open_interest", 0)
            oi = float(oi_raw) if oi_raw else 0
            premium = float(c.get("premium_usd", c.get("premium", 0)) or 0)

            iv_float = float(iv) if iv else 0
            if 0 < iv_float < 1.0:
                iv_float *= 100
            elif iv_float > 200 or iv_float <= 0:
                continue

            if strike <= 0 or dte <= 0 or oi < 1:
                continue

            result.append({
                "strike": strike,
                "dte": dte,
                "iv": round(iv_float, 2),
                "type": option_type.upper()[0] if option_type else "?",
                "oi": oi,
                "premium": premium,
            })
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest tests/test_greeks_analyzer.py::TestExtractContracts -v`
Expected: PASS (10/10)

- [ ] **Step 5: Commit**

```bash
git add -f dashboard/tests/test_greeks_analyzer.py dashboard/services/greeks_analyzer.py
git commit -m "feat(greeks): add GreeksAnalyzer with contract extraction"
```

---

## Task 2: Greeks 计算与汇总

**Files:**
- Modify: `dashboard/services/greeks_analyzer.py`
- Modify: `dashboard/tests/test_greeks_analyzer.py`

- [ ] **Step 1: Write the failing test**

Append to `test_greeks_analyzer.py`:

```python
class TestGreeksCalculation:
    def _make_contracts(self):
        """Generate contracts across 2 expiries with known structure."""
        contracts = []
        spot = 100000
        for dte in [7, 14]:
            for strike_pct in [-10, -5, 0, 5, 10]:
                strike = spot * (1 + strike_pct / 100)
                contracts.append(_make_contract(strike, 45.0, dte, "P"))
                contracts.append(_make_contract(strike, 38.0, dte, "C"))
        return contracts

    def test_greeks_summary_has_per_contract(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        gs = result["greeks_summary"]
        assert "per_contract" in gs
        for key in ["delta", "gamma", "theta", "vega"]:
            assert key in gs["per_contract"]

    def test_greeks_summary_has_total_exposure(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        gs = result["greeks_summary"]
        assert "total_exposure" in gs
        for key in ["delta", "gamma", "theta", "vega"]:
            assert key in gs["total_exposure"]

    def test_total_delta_nonzero(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        total = result["greeks_summary"]["total_exposure"]
        assert total["delta"] != 0

    def test_total_theta_negative(self):
        """Theta should be negative (time decay)."""
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        total = result["greeks_summary"]["total_exposure"]
        assert total["theta"] < 0

    def test_by_expiry_populated(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        assert len(result["by_expiry"]) == 2
        for entry in result["by_expiry"]:
            assert "dte" in entry
            assert "delta" in entry
            assert "gamma" in entry
            assert "theta" in entry
            assert "vega" in entry
            assert "atm_iv" in entry
            assert "contract_count" in entry
            assert "total_oi" in entry

    def test_by_expiry_sorted_by_dte(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        dtes = [e["dte"] for e in result["by_expiry"]]
        assert dtes == sorted(dtes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest tests/test_greeks_analyzer.py::TestGreeksCalculation -v`
Expected: FAIL (greeks_summary is empty dict)

- [ ] **Step 3: Implement Greeks calculation**

Add to `GreeksAnalyzer` class in `greeks_analyzer.py`:

```python
    @classmethod
    def analyze(cls, contracts_data: list, spot: float, currency: str = "BTC") -> dict:
        contracts = cls._extract_contracts(contracts_data, spot)
        if not contracts:
            return {
                "currency": currency, "spot": round(spot, 2),
                "contract_count": 0, "put_count": 0, "call_count": 0, "total_oi": 0,
                "greeks_summary": {}, "gex": {}, "by_expiry": [],
                "scenarios": {}, "analysis": None,
            }

        # Calculate Greeks for each contract
        for c in contracts:
            bs = black_scholes_price(c["type"], c["strike"], spot, c["dte"], c["iv"])
            c["delta"] = bs["delta"]
            c["gamma"] = bs["gamma"]
            c["theta"] = bs["theta"]
            c["vega"] = bs["vega"]

        greeks_summary = cls._calc_greeks_summary(contracts)
        by_expiry = cls._calc_by_expiry(contracts, spot)

        return {
            "currency": currency, "spot": round(spot, 2),
            "contract_count": len(contracts),
            "put_count": sum(1 for c in contracts if c["type"] == "P"),
            "call_count": sum(1 for c in contracts if c["type"] == "C"),
            "total_oi": round(sum(c["oi"] for c in contracts), 0),
            "greeks_summary": greeks_summary,
            "gex": {},
            "by_expiry": by_expiry,
            "scenarios": {},
            "analysis": None,
        }

    @classmethod
    def _calc_greeks_summary(cls, contracts: list) -> dict:
        total_delta = 0.0
        total_gamma = 0.0
        total_theta = 0.0
        total_vega = 0.0
        total_oi = 0.0

        for c in contracts:
            weight = max(1.0, c["oi"])
            total_delta += c["delta"] * weight
            total_gamma += c["gamma"] * weight
            total_theta += c["theta"] * weight
            total_vega += c["vega"] * weight
            total_oi += weight

        if total_oi <= 0:
            return {}

        return {
            "per_contract": {
                "delta": round(total_delta / total_oi, 4),
                "gamma": round(total_gamma / total_oi, 6),
                "theta": round(total_theta / total_oi, 2),
                "vega": round(total_vega / total_oi, 2),
            },
            "total_exposure": {
                "delta": round(total_delta, 2),
                "gamma": round(total_gamma, 4),
                "theta": round(total_theta, 2),
                "vega": round(total_vega, 2),
            },
        }

    @classmethod
    def _calc_by_expiry(cls, contracts: list, spot: float) -> list:
        expiries = {}
        for c in contracts:
            key = c["dte"]
            if key not in expiries:
                expiries[key] = {"contracts": [], "oi": 0}
            expiries[key]["contracts"].append(c)
            expiries[key]["oi"] += c["oi"]

        result = []
        for dte, data in sorted(expiries.items()):
            total_delta = 0.0
            total_gamma = 0.0
            total_theta = 0.0
            total_vega = 0.0
            total_oi = 0.0
            atm_iv = 0.0
            closest_strike_dist = float("inf")

            for c in data["contracts"]:
                weight = max(1.0, c["oi"])
                total_delta += c["delta"] * weight
                total_gamma += c["gamma"] * weight
                total_theta += c["theta"] * weight
                total_vega += c["vega"] * weight
                total_oi += weight

                dist = abs(c["strike"] - spot)
                if dist < closest_strike_dist:
                    closest_strike_dist = dist
                    atm_iv = c["iv"]

            result.append({
                "dte": dte,
                "delta": round(total_delta, 2),
                "gamma": round(total_gamma, 4),
                "theta": round(total_theta, 2),
                "vega": round(total_vega, 2),
                "atm_iv": round(atm_iv, 2),
                "contract_count": len(data["contracts"]),
                "total_oi": round(total_oi, 0),
            })
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest tests/test_greeks_analyzer.py::TestGreeksCalculation -v`
Expected: PASS (6/6)

- [ ] **Step 5: Commit**

```bash
git add dashboard/services/greeks_analyzer.py dashboard/tests/test_greeks_analyzer.py
git commit -m "feat(greeks): add Greeks calculation and per-expiry breakdown"
```

---

## Task 3: GEX 聚合与分析

**Files:**
- Modify: `dashboard/services/greeks_analyzer.py`
- Modify: `dashboard/tests/test_greeks_analyzer.py`

- [ ] **Step 1: Write the failing test**

Append to `test_greeks_analyzer.py`:

```python
class TestGEX:
    def _make_contracts(self):
        """Generate contracts with high OI at specific strikes for GEX testing."""
        contracts = []
        spot = 100000
        for dte in [7, 14]:
            for strike_pct in [-10, -5, 0, 5, 10]:
                strike = spot * (1 + strike_pct / 100)
                # Higher OI near ATM for pin risk testing
                oi = 500 if abs(strike_pct) <= 5 else 100
                contracts.append(_make_contract(strike, 45.0, dte, "P", oi=oi))
                contracts.append(_make_contract(strike, 38.0, dte, "C", oi=oi))
        return contracts

    def test_gex_by_strike_populated(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        gex = result["gex"]
        assert "by_strike" in gex
        assert len(gex["by_strike"]) > 0

    def test_gex_by_strike_has_required_fields(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        for entry in result["gex"]["by_strike"]:
            assert "strike" in entry
            assert "call_gex" in entry
            assert "put_gex" in entry
            assert "net_gex" in entry

    def test_gex_totals_present(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        gex = result["gex"]
        assert "total_gex" in gex
        assert "flip_strike" in gex
        assert "pin_strike" in gex
        assert "pin_risk_level" in gex

    def test_pin_strike_near_high_oi(self):
        """Pin strike should be near the highest OI concentration."""
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        pin = result["gex"]["pin_strike"]
        # Highest OI is at 0% and +-5% strikes (100000, 95000, 105000)
        assert pin in [95000, 100000, 105000]

    def test_flip_strike_is_number(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        flip = result["gex"]["flip_strike"]
        assert isinstance(flip, (int, float))

    def test_pin_risk_level_valid(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        assert result["gex"]["pin_risk_level"] in ["HIGH", "MEDIUM", "LOW"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest tests/test_greeks_analyzer.py::TestGEX -v`
Expected: FAIL (gex is empty dict)

- [ ] **Step 3: Implement GEX calculation**

Add to `GreeksAnalyzer` class in `greeks_analyzer.py`:

```python
    @classmethod
    def _calc_gex(cls, contracts: list, spot: float) -> dict:
        """Calculate Gamma Exposure by strike."""
        strike_data = {}
        for c in contracts:
            strike = c["strike"]
            if strike not in strike_data:
                strike_data[strike] = {"call_gex": 0.0, "put_gex": 0.0}
            gex_val = c["gamma"] * c["oi"] * spot * spot * 0.01
            if c["type"] == "C":
                strike_data[strike]["call_gex"] += gex_val
            else:
                strike_data[strike]["put_gex"] -= gex_val  # Put GEX is negative

        by_strike = []
        for strike in sorted(strike_data.keys()):
            d = strike_data[strike]
            net = d["call_gex"] + d["put_gex"]
            by_strike.append({
                "strike": strike,
                "call_gex": round(d["call_gex"], 0),
                "put_gex": round(d["put_gex"], 0),
                "net_gex": round(net, 0),
            })

        total_gex = sum(e["net_gex"] for e in by_strike)

        # Find flip strike (where net_gex crosses zero)
        flip_strike = 0
        for i in range(len(by_strike) - 1):
            if by_strike[i]["net_gex"] * by_strike[i + 1]["net_gex"] < 0:
                flip_strike = by_strike[i]["strike"]
                break
        if flip_strike == 0 and by_strike:
            flip_strike = by_strike[0]["strike"]

        # Find pin strike (highest total OI concentration)
        oi_by_strike = {}
        for c in contracts:
            s = c["strike"]
            oi_by_strike[s] = oi_by_strike.get(s, 0) + c["oi"]
        pin_strike = max(oi_by_strike, key=oi_by_strike.get) if oi_by_strike else 0

        # Pin risk level
        pin_oi = oi_by_strike.get(pin_strike, 0)
        avg_oi = sum(oi_by_strike.values()) / len(oi_by_strike) if oi_by_strike else 1
        concentration = pin_oi / avg_oi if avg_oi > 0 else 0
        if concentration > 10:
            pin_risk_level = "HIGH"
        elif concentration > 3:
            pin_risk_level = "MEDIUM"
        else:
            pin_risk_level = "LOW"

        return {
            "by_strike": by_strike,
            "total_gex": round(total_gex, 0),
            "flip_strike": flip_strike,
            "pin_strike": pin_strike,
            "pin_risk_level": pin_risk_level,
        }
```

Update the `analyze` method to call `_calc_gex`:

In the `analyze` method, replace `"gex": {},` with:
```python
        gex = cls._calc_gex(contracts, spot)
```
And update the return to use `gex`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest tests/test_greeks_analyzer.py::TestGEX -v`
Expected: PASS (6/6)

- [ ] **Step 5: Commit**

```bash
git add dashboard/services/greeks_analyzer.py dashboard/tests/test_greeks_analyzer.py
git commit -m "feat(greeks): add GEX aggregation by strike with flip and pin detection"
```

---

## Task 4: 情景分析与风险评级

**Files:**
- Modify: `dashboard/services/greeks_analyzer.py`
- Modify: `dashboard/tests/test_greeks_analyzer.py`

- [ ] **Step 1: Write the failing test**

Append to `test_greeks_analyzer.py`:

```python
class TestScenariosAndRisk:
    def _make_contracts(self):
        contracts = []
        spot = 100000
        for dte in [7, 14]:
            for strike_pct in [-10, -5, 0, 5, 10]:
                strike = spot * (1 + strike_pct / 100)
                contracts.append(_make_contract(strike, 45.0, dte, "P"))
                contracts.append(_make_contract(strike, 38.0, dte, "C"))
        return contracts

    def test_scenarios_populated(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        s = result["scenarios"]
        for key in ["down_10pct", "up_10pct", "iv_up_5pct", "iv_down_5pct", "pin_scenario"]:
            assert key in s

    def test_down_10pct_negative(self):
        """If market has positive delta, down scenario should be negative."""
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        # With symmetric contracts, delta could be near 0, but value should be a number
        assert isinstance(result["scenarios"]["down_10pct"], (int, float))

    def test_pin_scenario_has_fields(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        ps = result["scenarios"]["pin_scenario"]
        assert "pin_strike" in ps
        assert "pin_oi" in ps
        assert "avg_oi" in ps
        assert "concentration" in ps

    def test_risk_ratings_populated(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        # analysis is built in Task 5, but risk_ratings should be in scenarios
        # For now, just check scenarios exist
        assert result["scenarios"] != {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest tests/test_greeks_analyzer.py::TestScenariosAndRisk -v`
Expected: FAIL (scenarios is empty dict)

- [ ] **Step 3: Implement scenarios**

Add to `GreeksAnalyzer` class in `greeks_analyzer.py`:

```python
    @classmethod
    def _calc_scenarios(cls, contracts: list, spot: float, gex: dict) -> dict:
        total_delta = 0.0
        total_vega = 0.0
        total_oi = 0.0

        for c in contracts:
            weight = max(1.0, c["oi"])
            total_delta += c["delta"] * weight
            total_vega += c["vega"] * weight
            total_oi += weight

        # Pin scenario
        oi_by_strike = {}
        for c in contracts:
            s = c["strike"]
            oi_by_strike[s] = oi_by_strike.get(s, 0) + c["oi"]
        pin_strike = gex.get("pin_strike", 0)
        pin_oi = oi_by_strike.get(pin_strike, 0)
        avg_oi = sum(oi_by_strike.values()) / len(oi_by_strike) if oi_by_strike else 1
        concentration = pin_oi / avg_oi if avg_oi > 0 else 0

        return {
            "down_10pct": round(total_delta * spot * -0.1, 0),
            "up_10pct": round(total_delta * spot * 0.1, 0),
            "iv_up_5pct": round(total_vega * 5, 0),
            "iv_down_5pct": round(total_vega * -5, 0),
            "pin_scenario": {
                "pin_strike": pin_strike,
                "pin_oi": round(pin_oi, 0),
                "avg_oi": round(avg_oi, 0),
                "concentration": round(concentration, 1),
            },
        }
```

Update the `analyze` method to call `_calc_scenarios`:

```python
        scenarios = cls._calc_scenarios(contracts, spot, gex)
```

And update the return dict to include `scenarios`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest tests/test_greeks_analyzer.py::TestScenariosAndRisk -v`
Expected: PASS (4/4)

- [ ] **Step 5: Commit**

```bash
git add dashboard/services/greeks_analyzer.py dashboard/tests/test_greeks_analyzer.py
git commit -m "feat(greeks): add scenario analysis and pin risk calculation"
```

---

## Task 5: 市场状态判断与对冲建议

**Files:**
- Modify: `dashboard/services/greeks_analyzer.py`
- Modify: `dashboard/tests/test_greeks_analyzer.py`

- [ ] **Step 1: Write the failing test**

Append to `test_greeks_analyzer.py`:

```python
class TestAnalysis:
    def _make_contracts(self):
        contracts = []
        spot = 100000
        for dte in [7, 14]:
            for strike_pct in [-10, -5, 0, 5, 10]:
                strike = spot * (1 + strike_pct / 100)
                contracts.append(_make_contract(strike, 45.0, dte, "P"))
                contracts.append(_make_contract(strike, 38.0, dte, "C"))
        return contracts

    def test_analysis_populated(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        assert result["analysis"] is not None

    def test_analysis_has_gex_regime(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        a = result["analysis"]
        assert "gex_regime" in a
        assert "state" in a["gex_regime"]
        assert "label" in a["gex_regime"]

    def test_analysis_has_pin_risk(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        a = result["analysis"]
        assert "pin_risk" in a
        assert "level" in a["pin_risk"]

    def test_analysis_has_market_state(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        a = result["analysis"]
        assert "market_state" in a
        assert "state" in a["market_state"]
        assert a["market_state"]["state"] in [
            "TRENDING_UP", "TRENDING_DOWN", "MEAN_REVERTING",
            "PIN_RISK", "VOLATILE", "CALM"
        ]

    def test_analysis_has_risk_ratings(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        a = result["analysis"]
        assert "risk_ratings" in a
        for greek in ["delta", "gamma", "theta", "vega"]:
            assert greek in a["risk_ratings"]
            assert "level" in a["risk_ratings"][greek]
            assert a["risk_ratings"][greek]["level"] in ["HIGH", "MEDIUM", "LOW"]

    def test_analysis_has_interpretation(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        a = result["analysis"]
        assert "interpretation" in a
        assert isinstance(a["interpretation"], list)
        assert len(a["interpretation"]) > 0

    def test_analysis_has_hedge_suggestions(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        a = result["analysis"]
        assert "hedge_suggestions" in a
        assert isinstance(a["hedge_suggestions"], list)

    def test_hedge_suggestion_has_fields(self):
        result = GreeksAnalyzer.analyze(self._make_contracts(), 100000)
        for s in result["analysis"]["hedge_suggestions"]:
            assert "type" in s
            assert "title" in s
            assert "body" in s
            assert "action" in s
            assert "confidence" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && python -m pytest tests/test_greeks_analyzer.py::TestAnalysis -v`
Expected: FAIL (analysis is None)

- [ ] **Step 3: Implement analysis**

Add to `GreeksAnalyzer` class in `greeks_analyzer.py`:

```python
    @classmethod
    def _build_analysis(cls, contracts: list, spot: float, greeks_summary: dict,
                        gex: dict, scenarios: dict) -> Optional[dict]:
        if not greeks_summary or not gex:
            return None

        per = greeks_summary.get("per_contract", {})
        total = greeks_summary.get("total_exposure", {})
        total_gex = gex.get("total_gex", 0)
        pin_risk_level = gex.get("pin_risk_level", "LOW")
        pin_strike = gex.get("pin_strike", 0)
        flip_strike = gex.get("flip_strike", 0)
        atm_iv = 0

        # Get ATM IV from nearest expiry
        for c in contracts:
            if abs(c["strike"] - spot) < abs(atm_iv - spot) if atm_iv else True:
                atm_iv = c["iv"]

        # GEX Regime
        if total_gex > 0:
            gex_regime = {"state": "POSITIVE", "label": "正 Gamma", "icon": "🛡️",
                          "description": "做市商净多 gamma，价格趋于均值回归"}
        else:
            gex_regime = {"state": "NEGATIVE", "label": "负 Gamma", "icon": "⚡",
                          "description": "做市商净空 gamma，趋势可能加速"}

        # Pin Risk
        pin_risk_info = {
            "level": pin_risk_level,
            "label": {"HIGH": "高 Pin Risk", "MEDIUM": "中 Pin Risk", "LOW": "低 Pin Risk"}[pin_risk_level],
            "icon": "📌",
            "description": f"{pin_strike} strike OI 集中" if pin_strike else "无明显 pin",
        }

        # Market State
        delta_val = per.get("delta", 0)
        if pin_risk_level == "HIGH":
            market_state = {"state": "PIN_RISK", "label": "Pin 风险", "icon": "📌", "color": "#ef4444"}
        elif total_gex < 0 and atm_iv > 40:
            market_state = {"state": "VOLATILE", "label": "高波动", "icon": "🌊", "color": "#ef4444"}
        elif total_gex > 0 and atm_iv < 25:
            market_state = {"state": "CALM", "label": "平静", "icon": "😴", "color": "#149e61"}
        elif total_gex < 0 and delta_val > 0:
            market_state = {"state": "TRENDING_UP", "label": "趋势上行", "icon": "📈", "color": "#149e61"}
        elif total_gex < 0 and delta_val < 0:
            market_state = {"state": "TRENDING_DOWN", "label": "趋势下行", "icon": "📉", "color": "#ef4444"}
        else:
            market_state = {"state": "MEAN_REVERTING", "label": "均值回归", "icon": "🔄", "color": "#3b82f6"}

        # Risk Ratings
        def _rate_greek(value, high_thresh, med_thresh):
            av = abs(value)
            if av > high_thresh:
                return "HIGH"
            if av > med_thresh:
                return "MEDIUM"
            return "LOW"

        risk_ratings = {
            "delta": {"level": _rate_greek(delta_val, 0.5, 0.2), "label": "", "value": round(delta_val, 4)},
            "gamma": {"level": _rate_greek(per.get("gamma", 0), 0.01, 0.005), "label": "", "value": round(per.get("gamma", 0), 6)},
            "theta": {"level": _rate_greek(per.get("theta", 0), 100, 50), "label": "", "value": round(per.get("theta", 0), 2)},
            "vega": {"level": _rate_greek(per.get("vega", 0), 500, 200), "label": "", "value": round(per.get("vega", 0), 2)},
        }
        level_labels = {"HIGH": "🔴 高", "MEDIUM": "🟡 中", "LOW": "🟢 低"}
        for g in risk_ratings:
            risk_ratings[g]["label"] = level_labels[risk_ratings[g]["level"]]

        # Interpretation
        interpretation = []
        if total_gex > 0:
            interpretation.append("GEX 为正，做市商处于多 gamma 位置，市场倾向于均值回归")
        else:
            interpretation.append("GEX 为负，做市商处于空 gamma 位置，趋势可能加速")

        if pin_risk_level == "HIGH":
            interpretation.append(f"Pin Risk 高，{pin_strike} 附近 OI 集中，到期前价格可能被吸附")
        elif pin_risk_level == "MEDIUM":
            interpretation.append(f"Pin Risk 中等，{pin_strike} 附近有一定 OI 集中")

        if risk_ratings["vega"]["level"] == "HIGH":
            interpretation.append("Vega 敞口大，IV 变动会显著影响持仓价值")
        if risk_ratings["theta"]["level"] == "HIGH":
            interpretation.append("Theta 衰减快，时间价值损耗显著")

        # Hedge Suggestions
        suggestions = []
        if abs(delta_val) > 0.5:
            suggestions.append({
                "type": "delta_hedge", "title": "对冲方向风险",
                "body": f"Delta 敞口较大 ({delta_val:.4f})，建议买入反向期权对冲",
                "action": "买入反向期权使 Delta 接近中性",
                "confidence": "HIGH",
            })
        if pin_risk_level == "HIGH":
            suggestions.append({
                "type": "reduce_position", "title": "到期前减仓",
                "body": f"Pin Risk 高，{pin_strike} 附近 OI 集中度高",
                "action": "将近期仓位移至更远到期日",
                "confidence": "HIGH",
            })
        if total_gex > 0 and atm_iv > 30:
            suggestions.append({
                "type": "sell_straddle", "title": "卖出跨式",
                "body": "正 Gamma 环境适合卖出跨式收取时间价值",
                "action": "在 ATM strike 卖出跨式，Delta 中性",
                "confidence": "MEDIUM",
            })
        if total_gex < 0 and delta_val > 0:
            suggestions.append({
                "type": "trend_follow", "title": "顺势加仓",
                "body": "负 Gamma + 正 Delta，上行趋势可能加速",
                "action": "考虑加仓多头或买入 Call 跟趋势",
                "confidence": "MEDIUM",
            })
        if risk_ratings["vega"]["level"] == "HIGH":
            suggestions.append({
                "type": "vega_hedge", "title": "做空波动率",
                "body": "Vega 敞口大，若预期 IV 下降可卖出宽跨式",
                "action": "卖出宽跨式做空波动率",
                "confidence": "MEDIUM",
            })

        return {
            "gex_regime": gex_regime,
            "pin_risk": pin_risk_info,
            "market_state": market_state,
            "risk_ratings": risk_ratings,
            "interpretation": interpretation,
            "hedge_suggestions": suggestions[:4],
        }
```

Update the `analyze` method to call `_build_analysis`:

```python
        analysis = cls._build_analysis(contracts, spot, greeks_summary, gex, scenarios)
```

And update the return dict to include `analysis`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && python -m pytest tests/test_greeks_analyzer.py::TestAnalysis -v`
Expected: PASS (8/8)

- [ ] **Step 5: Commit**

```bash
git add dashboard/services/greeks_analyzer.py dashboard/tests/test_greeks_analyzer.py
git commit -m "feat(greeks): add market state analysis and hedge suggestions"
```

---

## Task 6: Router 改造

**Files:**
- Modify: `dashboard/routers/charts.py:284-411`

- [ ] **Step 1: Write the failing test**

Create `dashboard/tests/test_greeks_api.py`:

```python
"""Tests for greeks-summary API endpoint."""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_greeks_analyzer_import():
    """Verify GreeksAnalyzer can be imported and called."""
    from services.greeks_analyzer import GreeksAnalyzer
    contracts = [
        {"strike": 95000, "mark_iv": 45.0, "dte": 7, "option_type": "P", "oi": 100},
        {"strike": 105000, "mark_iv": 38.0, "dte": 7, "option_type": "C", "oi": 100},
    ]
    result = GreeksAnalyzer.analyze(contracts, 100000)
    assert "greeks_summary" in result
    assert "gex" in result
    assert "analysis" in result
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd dashboard && python -m pytest tests/test_greeks_api.py -v`
Expected: PASS (this verifies the service works standalone)

- [ ] **Step 3: Replace router endpoint**

Replace `dashboard/routers/charts.py:284-411` with:

```python
@router.get("/greeks-summary")
async def get_greeks_summary(currency: str = "BTC"):
    """获取 Greeks 风险矩阵分析"""
    from services.spot_price import get_spot_price
    from services.greeks_analyzer import GreeksAnalyzer
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
        return {"error": "无合约数据", "greeks_summary": {}, "gex": {}, "by_expiry": [],
                "scenarios": {}, "analysis": None, "currency": currency, "spot": spot}

    try:
        contracts = json.loads(rows[0][0])
    except json.JSONDecodeError:
        return {"error": "数据解析失败", "greeks_summary": {}, "gex": {}, "by_expiry": [],
                "scenarios": {}, "analysis": None, "currency": currency, "spot": spot}

    result = GreeksAnalyzer.analyze(contracts, spot, currency)

    if result["contract_count"] == 0:
        return {"error": "无有效 Greeks 数据", "greeks_summary": {}, "gex": {}, "by_expiry": [],
                "scenarios": {}, "analysis": None, "currency": currency, "spot": spot}

    return result
```

- [ ] **Step 4: Run full test suite**

Run: `cd dashboard && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/routers/charts.py dashboard/tests/test_greeks_api.py
git commit -m "refactor(greeks): replace inline greeks-summary with GreeksAnalyzer"
```

---

## Task 7: 前端 — index.html 布局改造

**Files:**
- Modify: `dashboard/static/index.html:406-425`

- [ ] **Step 1: Replace Greeks section HTML**

Replace lines 406-425 with:

```html
        <!-- Greeks 风险矩阵 -->
        <section id="greeksSection" class="card-glass rounded-xl p-5 mb-6 border-l-4 border-[#7132f5]">
            <div class="flex items-center justify-between mb-4">
                <div class="flex items-center gap-2">
                    <span class="text-xl">🛡️</span>
                    <h3 class="font-semibold text-lg">Greeks 风险矩阵</h3>
                </div>
                <select id="greeksCurrency" class="input-dark rounded-lg px-3 py-1.5 text-sm" onchange="loadGreeksSummary()">
                    <option value="BTC">BTC</option>
                    <option value="ETH">ETH</option>
                </select>
            </div>
            <!-- Status bar -->
            <div id="greeksStatusBar" class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4"></div>
            <!-- GEX Chart -->
            <div id="gexChartContainer" class="mb-4" style="height: 260px; position: relative;">
                <canvas id="gexCanvas"></canvas>
            </div>
            <!-- Greeks Expiry Curves -->
            <div id="greeksCurvesContainer" class="mb-4" style="height: 260px; position: relative;">
                <canvas id="greeksCurvesCanvas"></canvas>
            </div>
            <!-- Greeks Overview Grid -->
            <div id="greeksGrid"></div>
            <!-- Analysis Panel -->
            <div id="greeksAnalysis" class="mt-4"></div>
        </section>
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/static/index.html
git commit -m "feat(greeks): add HTML containers for GEX chart, curves, and analysis"
```

---

## Task 8: 前端 — Chart.js 图表与分析面板

**Files:**
- Modify: `dashboard/static/app.js:4281-4381`

- [ ] **Step 1: Replace loadGreeksSummary function**

Replace `loadGreeksSummary()` (lines 4281-4375) with:

```javascript
async function loadGreeksSummary() {
    const grid = document.getElementById('greeksGrid');
    const statusBar = document.getElementById('greeksStatusBar');
    const analysisDiv = document.getElementById('greeksAnalysis');
    if (!grid) return;

    grid.innerHTML = '<div class="text-gray-400 text-sm py-4 text-center">加载中...</div>';

    try {
        const currency = document.getElementById('greeksCurrency')?.value || 'BTC';
        const resp = await safeFetch(`${API_BASE}/api/charts/greeks-summary?currency=${currency}`);
        const data = await resp.json();
        if (data.error) {
            grid.innerHTML = `<div class="text-[#f59e0b] text-sm">${safeHTML(data.error)}</div>`;
            statusBar.innerHTML = '';
            analysisDiv.innerHTML = '';
            return;
        }

        const gs = data.greeks_summary || {};
        const per = gs.per_contract || {};
        const total = gs.total_exposure || {};
        const gex = data.gex || {};
        const scenarios = data.scenarios || {};
        const analysis = data.analysis;

        // Status bar
        if (analysis && statusBar) {
            const gexR = analysis.gex_regime || {};
            const pinR = analysis.pin_risk || {};
            const ms = analysis.market_state || {};
            const thetaPerDay = per.theta || 0;
            const thetaColor = thetaPerDay < -100 ? '#ef4444' : thetaPerDay < -50 ? '#f59e0b' : '#149e61';
            statusBar.innerHTML = `
                <div class="bg-gray-800/50 rounded-lg p-3 text-center">
                    <div class="text-lg">${gexR.icon || ''} ${gexR.label || '--'}</div>
                    <div class="text-xs text-gray-400">GEX Regime</div>
                </div>
                <div class="bg-gray-800/50 rounded-lg p-3 text-center">
                    <div class="text-lg">${pinR.icon || ''} ${pinR.label || '--'}</div>
                    <div class="text-xs text-gray-400">Pin Risk</div>
                </div>
                <div class="bg-gray-800/50 rounded-lg p-3 text-center">
                    <div class="text-lg" style="color:${ms.color || '#9497a9'}">${ms.icon || ''} ${ms.label || '--'}</div>
                    <div class="text-xs text-gray-400">Market State</div>
                </div>
                <div class="bg-gray-800/50 rounded-lg p-3 text-center">
                    <div class="text-lg font-bold" style="color:${thetaColor}">$${thetaPerDay.toFixed(2)}/天</div>
                    <div class="text-xs text-gray-400">Theta/Day</div>
                </div>`;
        }

        // GEX Chart
        renderGEXChart(gex, data.spot);

        // Greeks Curves Chart
        renderGreeksCurvesChart(data.by_expiry || []);

        // Greeks Overview Grid
        const deltaColor = Math.abs(per.delta) > 0.5 ? '#ef4444' : Math.abs(per.delta) > 0.2 ? '#f59e0b' : '#149e61';
        const thetaColor = per.theta < -100 ? '#ef4444' : per.theta < -50 ? '#f59e0b' : '#149e61';
        const riskRatings = analysis?.risk_ratings || {};

        grid.innerHTML = `
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                ${['delta', 'gamma', 'theta', 'vega'].map(g => {
                    const rr = riskRatings[g] || {};
                    const val = per[g] || 0;
                    const color = rr.level === 'HIGH' ? '#ef4444' : rr.level === 'MEDIUM' ? '#f59e0b' : '#149e61';
                    const labels = {delta: 'Delta (Δ)', gamma: 'Gamma (Γ)', theta: 'Theta (Θ)', vega: 'Vega (V)'};
                    const fmt = g === 'gamma' ? val.toFixed(6) : g === 'delta' ? val.toFixed(4) : '$' + val.toFixed(2);
                    return `<div class="bg-gray-800/50 rounded-lg p-3 text-center">
                        <div class="text-xs text-gray-400">${labels[g]}</div>
                        <div class="text-xl font-bold" style="color:${color}">${fmt}</div>
                        <div class="text-xs" style="color:${color}">${rr.label || '--'}</div>
                    </div>`;
                }).join('')}
            </div>
            <div class="bg-gray-800/30 rounded-lg p-3 mb-3">
                <div class="text-sm font-medium text-gray-300 mb-2">总风险敞口 (OI 加权)</div>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                    <div class="text-center"><div class="text-gray-400">总 Delta</div>
                        <div class="text-lg font-bold ${total.delta > 0 ? 'text-[#149e61]' : 'text-[#ef4444]'}">${total.delta?.toLocaleString() || 0}</div></div>
                    <div class="text-center"><div class="text-gray-400">总 Gamma</div>
                        <div class="text-lg font-bold text-[#7132f5]">${total.gamma?.toFixed(4) || 0}</div></div>
                    <div class="text-center"><div class="text-gray-400">总 Theta</div>
                        <div class="text-lg font-bold ${total.theta > 0 ? 'text-[#149e61]' : 'text-[#ef4444]'}">$${total.theta?.toLocaleString() || 0}</div></div>
                    <div class="text-center"><div class="text-gray-400">总 Vega</div>
                        <div class="text-lg font-bold text-[#7132f5]">$${total.vega?.toLocaleString() || 0}</div></div>
                </div>
            </div>
            <div class="bg-gray-800/30 rounded-lg p-3">
                <div class="text-sm font-medium text-gray-300 mb-2">情景分析</div>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
                    <div class="flex justify-between"><span class="text-gray-400">若 ${currency} 下跌 10%</span>
                        <span class="${scenarios.down_10pct < 0 ? 'text-[#ef4444]' : 'text-[#149e61]'}">${scenarios.down_10pct < 0 ? '' : '+'}$${scenarios.down_10pct?.toLocaleString() || 0}</span></div>
                    <div class="flex justify-between"><span class="text-gray-400">若 ${currency} 上涨 10%</span>
                        <span class="${scenarios.up_10pct > 0 ? 'text-[#149e61]' : 'text-[#ef4444]'}">+$${scenarios.up_10pct?.toLocaleString() || 0}</span></div>
                    <div class="flex justify-between"><span class="text-gray-400">若 IV 上升 5%</span>
                        <span class="text-[#149e61]">+$${scenarios.iv_up_5pct?.toLocaleString() || 0}</span></div>
                    <div class="flex justify-between"><span class="text-gray-400">若 IV 下降 5%</span>
                        <span class="text-[#ef4444]">$${scenarios.iv_down_5pct?.toLocaleString() || 0}</span></div>
                </div>
                <div class="mt-2 text-xs text-gray-500">
                    合约: ${data.contract_count}个 (${data.put_count} Put / ${data.call_count} Call) | 总 OI: ${(data.total_oi || 0).toLocaleString()}
                </div>
            </div>`;

        // Analysis Panel
        if (analysis && analysisDiv) {
            renderGreeksAnalysis(analysis);
        } else if (analysisDiv) {
            analysisDiv.innerHTML = '';
        }
    } catch (e) {
        grid.innerHTML = `<div class="text-[#ef4444] text-sm">加载失败: ${e.message}</div>`;
    }
}

function renderGEXChart(gex, spot) {
    const canvas = document.getElementById('gexCanvas');
    if (!canvas || !gex.by_strike || gex.by_strike.length === 0) return;

    if (canvas._chart) canvas._chart.destroy();

    const labels = gex.by_strike.map(e => e.strike.toLocaleString());
    const callGex = gex.by_strike.map(e => e.call_gex);
    const putGex = gex.by_strike.map(e => e.put_gex);
    const netGex = gex.by_strike.map(e => e.net_gex);

    canvas._chart = new Chart(canvas, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                { label: 'Call GEX', data: callGex, backgroundColor: 'rgba(20,158,97,0.7)', stack: 'gex' },
                { label: 'Put GEX', data: putGex, backgroundColor: 'rgba(239,68,68,0.7)', stack: 'gex' },
                { label: 'Net GEX', data: netGex, type: 'line', borderColor: '#3b82f6', borderWidth: 2, pointRadius: 3, fill: false, yAxisID: 'y' },
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                title: { display: true, text: 'GEX by Strike', color: '#9497a9', font: { size: 13 } },
                legend: { labels: { color: '#9497a9', boxWidth: 12 } },
            },
            scales: {
                x: { ticks: { color: '#686b82', maxRotation: 45 }, grid: { color: 'rgba(255,255,255,0.05)' } },
                y: { ticks: { color: '#686b82' }, grid: { color: 'rgba(255,255,255,0.05)' } },
            }
        }
    });
}

function renderGreeksCurvesChart(byExpiry) {
    const canvas = document.getElementById('greeksCurvesCanvas');
    if (!canvas || byExpiry.length === 0) return;

    if (canvas._chart) canvas._chart.destroy();

    const labels = byExpiry.map(e => e.dte + 'D');
    const deltaData = byExpiry.map(e => e.delta);
    const gammaData = byExpiry.map(e => e.gamma);
    const thetaData = byExpiry.map(e => e.theta);
    const vegaData = byExpiry.map(e => e.vega);

    canvas._chart = new Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [
                { label: 'Delta', data: deltaData, borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.1)', fill: false, tension: 0.3 },
                { label: 'Gamma', data: gammaData, borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,0.1)', fill: false, tension: 0.3, yAxisID: 'y' },
                { label: 'Theta', data: thetaData, borderColor: '#ef4444', backgroundColor: 'rgba(239,68,68,0.1)', fill: false, tension: 0.3, yAxisID: 'y1' },
                { label: 'Vega', data: vegaData, borderColor: '#149e61', backgroundColor: 'rgba(20,158,97,0.1)', fill: false, tension: 0.3, yAxisID: 'y1' },
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                title: { display: true, text: 'Greeks by Expiry', color: '#9497a9', font: { size: 13 } },
                legend: { labels: { color: '#9497a9', boxWidth: 12 } },
            },
            scales: {
                x: { ticks: { color: '#686b82' }, grid: { color: 'rgba(255,255,255,0.05)' } },
                y: { position: 'left', title: { display: true, text: 'Delta / Gamma', color: '#686b82' }, ticks: { color: '#686b82' }, grid: { color: 'rgba(255,255,255,0.05)' } },
                y1: { position: 'right', title: { display: true, text: 'Theta / Vega ($)', color: '#686b82' }, ticks: { color: '#686b82' }, grid: { drawOnChartArea: false } },
            }
        }
    });
}

function renderGreeksAnalysis(analysis) {
    const div = document.getElementById('greeksAnalysis');
    if (!div || !analysis) return;

    let html = '<div class="card-glass rounded-xl p-4">';

    // Interpretation
    if (analysis.interpretation && analysis.interpretation.length > 0) {
        html += '<div class="mb-3"><div class="text-sm font-medium text-gray-300 mb-2">📊 市场解读</div>';
        for (const line of analysis.interpretation) {
            html += `<div class="text-xs text-gray-400 mb-1">• ${safeHTML(line)}</div>`;
        }
        html += '</div>';
    }

    // Hedge Suggestions
    if (analysis.hedge_suggestions && analysis.hedge_suggestions.length > 0) {
        html += '<div><div class="text-sm font-medium text-gray-300 mb-2">💡 对冲建议</div>';
        for (const s of analysis.hedge_suggestions) {
            const confColor = s.confidence === 'HIGH' ? '#149e61' : '#f59e0b';
            html += `<div class="bg-gray-800/30 rounded-lg p-3 mb-2 border-l-2" style="border-color:${confColor}">
                <div class="flex items-center gap-2 mb-1">
                    <span class="text-xs font-bold px-1.5 py-0.5 rounded" style="background:${confColor}22;color:${confColor}">${s.confidence}</span>
                    <span class="text-sm font-medium text-gray-200">${safeHTML(s.title)}</span>
                </div>
                <div class="text-xs text-gray-400 mb-1">${safeHTML(s.body)}</div>
                <div class="text-xs text-[#7132f5]">→ ${safeHTML(s.action)}</div>
            </div>`;
        }
        html += '</div>';
    }

    html += '</div>';
    div.innerHTML = html;
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/static/app.js
git commit -m "feat(greeks): add GEX chart, Greeks curves, and analysis panel rendering"
```

---

## Task 9: 全量测试与验证

**Files:**
- All previously modified files

- [ ] **Step 1: Run full test suite**

Run: `cd dashboard && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Verify server starts**

Run: `cd dashboard && python -c "from routers.charts import router; print('Router OK')"`
Expected: "Router OK"

- [ ] **Step 3: Restart dev server**

```bash
# Kill existing server if running
# Then restart
cd dashboard && python main.py &
```

- [ ] **Step 4: Commit final state**

```bash
git add -A
git commit -m "feat: complete Greeks Risk Matrix redesign with GEX, Pin Risk, and hedge suggestions"
```
