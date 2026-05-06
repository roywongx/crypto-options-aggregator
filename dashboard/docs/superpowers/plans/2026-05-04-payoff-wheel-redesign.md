# Payoff 可视化 & Wheel ROI 重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Payoff 计算器和 Wheel ROI 合并为统一的"策略分析中心"，与策略推荐引擎深度联动，采用蒙特卡洛模拟替代简化模型。

**Architecture:** 后端新建 `services/strategy_analytics.py`（PayoffEngine + WheelSimulator），新建 `api/analytics.py` 提供 3 条端点，前端合并为四 Tab 策略分析中心。复用现有 `shared_calculations.py` 的 BS 定价和统计函数。

**Tech Stack:** Python 3.10+, FastAPI, Pydantic, math (stdlib), Chart.js (frontend)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `services/strategy_analytics.py` | Create | PayoffEngine + WheelSimulator 核心计算 |
| `api/analytics.py` | Create | 3 条 API 端点 |
| `tests/test_strategy_analytics.py` | Create | 后端单元测试 |
| `main.py:250-282` | Modify | 注册 analytics_router |
| `static/index.html:564-777` | Modify | 重写为策略分析中心 |
| `static/app.js` | Modify | 新分析函数 + 推荐联动 |

---

### Task 1: PayoffEngine — calc_single + estimate_premium

**Files:**
- Create: `services/strategy_analytics.py`
- Create: `tests/test_strategy_analytics.py`

- [ ] **Step 1: Write failing tests for PayoffEngine.calc_single**

```python
# tests/test_strategy_analytics.py
"""策略分析引擎单元测试"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.strategy_analytics import PayoffEngine


class TestCalcSingle:
    def setup_method(self):
        self.engine = PayoffEngine()

    def test_sell_put_profit_when_above_strike(self):
        """Sell Put: 价格在行权价之上，利润 = 权利金"""
        result = self.engine.calc_single(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", dte=30, quantity=1
        )
        assert result["max_profit"] == 2000
        assert result["breakeven"] == 93000

    def test_sell_put_loss_when_below_strike(self):
        """Sell Put: 价格跌破行权价，亏损递增"""
        result = self.engine.calc_single(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", dte=30, quantity=1
        )
        # 在 90000 时: pnl = 2000 - (95000 - 90000) = -3000
        curve = {p: v for p, v in zip(result["payoff_curve"]["prices"], result["payoff_curve"]["pnl"])}
        assert curve[90000] == -3000

    def test_sell_call_profit_when_below_strike(self):
        """Sell Call: 价格在行权价之下，利润 = 权利金"""
        result = self.engine.calc_single(
            spot=100000, strike=105000, premium=1500,
            option_type="CALL", dte=30, quantity=1
        )
        assert result["max_profit"] == 1500
        assert result["breakeven"] == 106500

    def test_buy_put_profit_when_below_strike(self):
        """Buy Put: 价格跌破行权价盈利"""
        result = self.engine.calc_single(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", dte=30, quantity=1, side="buy"
        )
        # max_loss = premium = 2000
        assert result["max_loss"] == -2000

    def test_quantity_scaling(self):
        """数量缩放"""
        result1 = self.engine.calc_single(spot=100000, strike=95000, premium=2000, option_type="PUT", dte=30, quantity=1)
        result2 = self.engine.calc_single(spot=100000, strike=95000, premium=2000, option_type="PUT", dte=30, quantity=2)
        assert result2["max_profit"] == result1["max_profit"] * 2

    def test_payoff_curve_has_zones(self):
        """返回 profit/loss 区间"""
        result = self.engine.calc_single(spot=100000, strike=95000, premium=2000, option_type="PUT", dte=30)
        assert "profit_range" in result["zones"]
        assert "loss_range" in result["zones"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard" && python -m pytest tests/test_strategy_analytics.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'services.strategy_analytics'"

- [ ] **Step 3: Implement PayoffEngine.calc_single + estimate_premium**

```python
# services/strategy_analytics.py
"""
策略分析引擎 v1.0
PayoffEngine: 单腿/组合 payoff 计算、概率叠加、时间衰减
WheelSimulator: 蒙特卡洛 Wheel 模拟
"""
import math
import random
from typing import List, Dict, Any, Optional
from services.shared_calculations import black_scholes_price, norm_cdf, norm_pdf


class PayoffEngine:

    def calc_single(self, spot: float, strike: float, premium: float,
                    option_type: str, dte: int, quantity: float = 1,
                    side: str = "sell", pct_range: float = 0.3,
                    steps: int = 100) -> Dict[str, Any]:
        """单腿 payoff 计算"""
        is_put = option_type.upper() in ("P", "PUT")
        is_sell = side.lower() == "sell"

        low = spot * (1 - pct_range)
        high = spot * (1 + pct_range)
        step_size = (high - low) / steps
        prices = [round(low + i * step_size, 2) for i in range(steps + 1)]

        pnl = []
        for price in prices:
            if is_sell:
                if is_put:
                    val = premium if price >= strike else premium - (strike - price)
                else:
                    val = premium if price <= strike else premium - (price - strike)
            else:
                if is_put:
                    val = -premium + (strike - price) if price < strike else -premium
                else:
                    val = -premium + (price - strike) if price > strike else -premium
            pnl.append(round(val * quantity, 2))

        max_profit = max(pnl)
        max_loss = min(pnl)

        # breakeven
        breakeven = None
        for i in range(len(prices) - 1):
            if (pnl[i] <= 0 and pnl[i + 1] > 0) or (pnl[i] >= 0 and pnl[i + 1] < 0):
                breakeven = round((prices[i] + prices[i + 1]) / 2, 2)
                break

        # zones
        profit_prices = [p for p, v in zip(prices, pnl) if v > 0]
        loss_prices = [p for p, v in zip(prices, pnl) if v < 0]
        zones = {
            "profit_range": [min(profit_prices), max(profit_prices)] if profit_prices else None,
            "loss_range": [min(loss_prices), max(loss_prices)] if loss_prices else None,
        }

        return {
            "max_profit": max_profit,
            "max_loss": max_loss,
            "breakeven": breakeven,
            "profit_at_spot": pnl[len(pnl) // 2],
            "payoff_curve": {"prices": prices, "pnl": pnl},
            "zones": zones,
        }

    def estimate_premium(self, spot: float, strike: float, dte: int,
                         iv: float, option_type: str) -> Dict[str, Any]:
        """BS 估算权利金 + Greeks"""
        ot = "P" if option_type.upper() in ("P", "PUT") else "C"
        bs = black_scholes_price(ot, strike, spot, dte, iv)
        return {
            "premium": bs["premium"],
            "delta": bs["delta"],
            "gamma": bs["gamma"],
            "theta": bs["theta"],
            "vega": bs["vega"],
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard" && python -m pytest tests/test_strategy_analytics.py::TestCalcSingle -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add services/strategy_analytics.py tests/test_strategy_analytics.py
git commit -m "feat: add PayoffEngine.calc_single and estimate_premium"
```

---

### Task 2: PayoffEngine — calc_multi_legs

**Files:**
- Modify: `services/strategy_analytics.py`
- Modify: `tests/test_strategy_analytics.py`

- [ ] **Step 1: Write failing tests for calc_multi_legs**

```python
# Append to tests/test_strategy_analytics.py

class TestCalcMultiLegs:
    def setup_method(self):
        self.engine = PayoffEngine()

    def test_bull_put_spread(self):
        """牛市看跌价差: sell 95000P + buy 90000P"""
        legs = [
            {"strike": 95000, "premium": 2000, "option_type": "PUT", "quantity": 1, "side": "sell"},
            {"strike": 90000, "premium": 800, "option_type": "PUT", "quantity": 1, "side": "buy"},
        ]
        result = self.engine.calc_multi_legs(spot=100000, legs=legs)
        # max profit = 2000 - 800 = 1200 (both OTM)
        assert result["max_profit"] == 1200
        # max loss = (95000 - 90000) - 1200 = 3800
        assert result["max_loss"] == -3800
        assert len(result["legs"]) == 2

    def test_short_straddle(self):
        """卖出跨式: sell 100000C + sell 100000P"""
        legs = [
            {"strike": 100000, "premium": 3000, "option_type": "CALL", "quantity": 1, "side": "sell"},
            {"strike": 100000, "premium": 2500, "option_type": "PUT", "quantity": 1, "side": "sell"},
        ]
        result = self.engine.calc_multi_legs(spot=100000, legs=legs)
        # max profit = 3000 + 2500 = 5500 (at strike)
        assert result["max_profit"] == 5500

    def test_empty_legs_returns_error(self):
        """空 legs 返回错误"""
        result = self.engine.calc_multi_legs(spot=100000, legs=[])
        assert result["success"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard" && python -m pytest tests/test_strategy_analytics.py::TestCalcMultiLegs -v`
Expected: FAIL with "AttributeError: 'PayoffEngine' object has no attribute 'calc_multi_legs'"

- [ ] **Step 3: Implement calc_multi_legs**

```python
# Add to PayoffEngine class in services/strategy_analytics.py

    def calc_multi_legs(self, spot: float, legs: List[Dict[str, Any]],
                        pct_range: float = 0.3, steps: int = 100) -> Dict[str, Any]:
        """组合策略 payoff"""
        if not legs:
            return {"success": False, "error": "至少需要一条腿"}

        low = spot * (1 - pct_range)
        high = spot * (1 + pct_range)
        step_size = (high - low) / steps
        prices = [round(low + i * step_size, 2) for i in range(steps + 1)]

        total_pnl = [0.0] * len(prices)
        leg_results = []

        for leg in legs:
            is_put = leg.get("option_type", "P").upper() in ("P", "PUT")
            is_sell = leg.get("side", "sell").lower() == "sell"
            strike = leg.get("strike", spot)
            premium = leg.get("premium", 0)
            qty = leg.get("quantity", 1)

            pnl = []
            for price in prices:
                if is_sell:
                    if is_put:
                        val = premium if price >= strike else premium - (strike - price)
                    else:
                        val = premium if price <= strike else premium - (price - strike)
                else:
                    if is_put:
                        val = -premium + (strike - price) if price < strike else -premium
                    else:
                        val = -premium + (price - strike) if price > strike else -premium
                pnl.append(round(val * qty, 2))

            for i in range(len(total_pnl)):
                total_pnl[i] += pnl[i]

            leg_results.append({
                "strike": strike,
                "premium": premium,
                "option_type": "PUT" if is_put else "CALL",
                "side": "sell" if is_sell else "buy",
                "quantity": qty,
                "pnl": pnl,
                "max_profit": max(pnl),
                "max_loss": min(pnl),
            })

        total_pnl = [round(v, 2) for v in total_pnl]

        breakevens = []
        for i in range(len(prices) - 1):
            if (total_pnl[i] <= 0 and total_pnl[i + 1] > 0) or (total_pnl[i] >= 0 and total_pnl[i + 1] < 0):
                breakevens.append(round((prices[i] + prices[i + 1]) / 2, 2))

        return {
            "success": True,
            "max_profit": max(total_pnl),
            "max_loss": min(total_pnl),
            "breakevens": breakevens,
            "payoff_curve": {"prices": prices, "pnl": total_pnl},
            "legs": leg_results,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard" && python -m pytest tests/test_strategy_analytics.py::TestCalcMultiLegs -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add services/strategy_analytics.py tests/test_strategy_analytics.py
git commit -m "feat: add PayoffEngine.calc_multi_legs for spread/straddle strategies"
```

---

### Task 3: PayoffEngine — calc_probability_overlay + calc_time_decay

**Files:**
- Modify: `services/strategy_analytics.py`
- Modify: `tests/test_strategy_analytics.py`

- [ ] **Step 1: Write failing tests**

```python
# Append to tests/test_strategy_analytics.py

class TestProbabilityAndTimeDecay:
    def setup_method(self):
        self.engine = PayoffEngine()

    def test_probability_overlay_returns_density(self):
        """概率叠加返回概率密度"""
        result = self.engine.calc_probability_overlay(
            spot=100000, dte=30, iv=60, strikes=[95000, 100000, 105000]
        )
        assert "density" in result
        assert len(result["density"]) > 0
        # 概率密度总和应近似为 1（归一化）
        total = sum(v for _, v in result["density"])
        assert 0.9 < total < 1.1

    def test_time_decay_multiple_dte(self):
        """时间衰减返回多个 DTE 的价值曲线"""
        result = self.engine.calc_time_decay(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", iv=60, dte_max=60
        )
        assert "curves" in result
        # 至少有 DTE=60, 30, 15, 7 四条曲线
        assert len(result["curves"]) >= 4
        # 每条曲线有点
        for curve in result["curves"]:
            assert len(curve["points"]) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard" && python -m pytest tests/test_strategy_analytics.py::TestProbabilityAndTimeDecay -v`
Expected: FAIL

- [ ] **Step 3: Implement calc_probability_overlay + calc_time_decay**

```python
# Add to PayoffEngine class in services/strategy_analytics.py

    def calc_probability_overlay(self, spot: float, dte: int, iv: float,
                                 strikes: List[float] = None) -> Dict[str, Any]:
        """到期价格概率分布（对数正态）"""
        sigma = iv / 100
        T = dte / 365.0
        if T <= 0 or sigma <= 0:
            return {"density": []}

        # 对数正态参数
        mu = math.log(spot) - 0.5 * sigma**2 * T
        std = sigma * math.sqrt(T)

        # 生成价格区间
        low = spot * 0.5
        high = spot * 1.5
        n_points = 200
        step = (high - low) / n_points

        density = []
        for i in range(n_points + 1):
            price = low + i * step
            if price <= 0:
                density.append([round(price, 2), 0])
                continue
            ln_price = math.log(price)
            z = (ln_price - mu) / std
            prob = norm_pdf(z) / (price * std)
            density.append([round(price, 2), round(prob * step, 6)])

        return {"density": density, "mean": spot, "dte": dte}

    def calc_time_decay(self, spot: float, strike: float, premium: float,
                        option_type: str, iv: float,
                        dte_max: int = 60) -> Dict[str, Any]:
        """多时间点的期权价值曲线"""
        ot = "P" if option_type.upper() in ("P", "PUT") else "C"
        dte_values = [d for d in [60, 45, 30, 15, 7, 1] if d <= dte_max]

        low = spot * 0.7
        high = spot * 1.3
        n_points = 100
        step = (high - low) / n_points
        prices = [round(low + i * step, 2) for i in range(n_points + 1)]

        curves = []
        for dte in dte_values:
            points = []
            for price in prices:
                bs = black_scholes_price(ot, strike, price, dte, iv)
                points.append([price, bs["premium"]])
            curves.append({"dte": dte, "points": points})

        return {"curves": curves, "prices": prices}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard" && python -m pytest tests/test_strategy_analytics.py::TestProbabilityAndTimeDecay -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add services/strategy_analytics.py tests/test_strategy_analytics.py
git commit -m "feat: add probability overlay and time decay analysis"
```

---

### Task 4: PayoffEngine — score_strategy

**Files:**
- Modify: `services/strategy_analytics.py`
- Modify: `tests/test_strategy_analytics.py`

- [ ] **Step 1: Write failing tests**

```python
# Append to tests/test_strategy_analytics.py

class TestScoreStrategy:
    def setup_method(self):
        self.engine = PayoffEngine()

    def test_sell_put_good_score(self):
        """Sell Put 深度 OTM 应得高分"""
        result = self.engine.score_strategy(
            spot=100000, strike=90000, premium=1500,
            option_type="PUT", dte=30
        )
        assert result["total"] > 0.5
        assert result["recommendation"] in ("BEST", "GOOD")

    def test_sell_put_risky_score(self):
        """Sell Put 接近 spot 应得低分"""
        result = self.engine.score_strategy(
            spot=100000, strike=99000, premium=5000,
            option_type="PUT", dte=7
        )
        assert result["recommendation"] in ("CAUTION", "SKIP", "OK")

    def test_score_has_all_components(self):
        """评分包含所有分量"""
        result = self.engine.score_strategy(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", dte=30
        )
        for key in ("total", "ev", "apr", "liquidity", "theta", "recommendation"):
            assert key in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard" && python -m pytest tests/test_strategy_analytics.py::TestScoreStrategy -v`
Expected: FAIL

- [ ] **Step 3: Implement score_strategy**

```python
# Add to PayoffEngine class in services/strategy_analytics.py

    def score_strategy(self, spot: float, strike: float, premium: float,
                       option_type: str, dte: int,
                       delta: float = None) -> Dict[str, Any]:
        """策略评分 — 与 StrategyScorer 对齐"""
        from services.strategy_engine import StrategyScorer
        scorer = StrategyScorer()

        # 构造 contract dict 供 scorer 使用
        is_put = option_type.upper() in ("P", "PUT")
        contract = {
            "option_type": "P" if is_put else "C",
            "strike": strike,
            "premium_usd": premium,
            "dte": dte,
            "delta": delta if delta is not None else (-0.25 if is_put else 0.25),
            "apr": (premium / strike * 365 / dte * 100) if dte > 0 and strike > 0 else 0,
            "open_interest": 500,
            "spread_pct": 2.0,
        }

        score = scorer.score(contract, spot)
        return {
            "total": round(score.total, 4),
            "ev": round(score.ev, 4),
            "apr": round(score.apr, 4),
            "liquidity": round(score.liquidity, 4),
            "theta": round(score.theta, 4),
            "recommendation": score.recommendation,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard" && python -m pytest tests/test_strategy_analytics.py::TestScoreStrategy -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add services/strategy_analytics.py tests/test_strategy_analytics.py
git commit -m "feat: add PayoffEngine.score_strategy with multi-factor scoring"
```

---

### Task 5: WheelSimulator — Monte Carlo 模拟

**Files:**
- Modify: `services/strategy_analytics.py`
- Modify: `tests/test_strategy_analytics.py`

- [ ] **Step 1: Write failing tests**

```python
# Append to tests/test_strategy_analytics.py

from services.strategy_analytics import WheelSimulator


class TestWheelSimulator:
    def setup_method(self):
        self.sim = WheelSimulator()

    def test_basic_simulation(self):
        """基本模拟返回所有必要字段"""
        result = self.sim.simulate(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", cycles=6, capital=100000,
            simulations=100
        )
        assert result["success"] is True
        assert "summary" in result
        summary = result["summary"]
        for key in ("mean_roi", "median_roi", "p10", "p90", "win_rate", "max_drawdown_mean"):
            assert key in summary

    def test_roi_reasonable_range(self):
        """ROI 在合理范围内"""
        result = self.sim.simulate(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", cycles=6, capital=100000,
            simulations=500
        )
        mean_roi = result["summary"]["mean_roi"]
        # 6 个月 wheel，每 cycle 收 2%，合理范围 -10% ~ +30%
        assert -0.10 < mean_roi < 0.30

    def test_sample_paths_count(self):
        """返回正确数量的样本路径"""
        result = self.sim.simulate(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", cycles=4, capital=100000,
            simulations=200
        )
        assert len(result["sample_paths"]) == 5

    def test_roi_distribution_non_empty(self):
        """ROI 分布直方图数据非空"""
        result = self.sim.simulate(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", cycles=6, capital=100000,
            simulations=200
        )
        assert len(result["roi_distribution"]) > 0

    def test_score_present(self):
        """包含策略评分"""
        result = self.sim.simulate(
            spot=100000, strike=95000, premium=2000,
            option_type="PUT", cycles=6, capital=100000,
            simulations=100
        )
        assert "score" in result
        assert "total" in result["score"]
        assert "recommendation" in result["score"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard" && python -m pytest tests/test_strategy_analytics.py::TestWheelSimulator -v`
Expected: FAIL with "cannot import name 'WheelSimulator'"

- [ ] **Step 3: Implement WheelSimulator**

```python
# Add to services/strategy_analytics.py after PayoffEngine class


class WheelSimulator:

    def simulate(self, spot: float, strike: float, premium: float,
                 option_type: str, cycles: int, capital: float,
                 assigned_pct: float = 0.5, iv: float = 0.6,
                 drift: float = 0.0, simulations: int = 1000) -> Dict[str, Any]:
        """蒙特卡洛 Wheel 模拟"""
        if spot <= 0 or strike <= 0 or capital <= 0 or cycles <= 0:
            return {"success": False, "error": "参数无效"}

        random.seed(42)
        dt = 30 / 365.0  # 每 cycle 30 天

        all_rois = []
        sample_paths = []
        win_count = 0
        drawdowns = []

        for sim_idx in range(simulations):
            price = spot
            total_premium = 0.0
            path = [price]
            was_assigned = False
            max_val = capital
            max_dd = 0.0

            for cycle in range(cycles):
                # Sell Put
                put_itm = price < strike
                total_premium += premium

                if put_itm:
                    # 被行权，买入标的
                    cost = strike - premium
                    was_assigned = True

                    # GBM 价格变动
                    z = random.gauss(0, 1)
                    price = price * math.exp((drift - 0.5 * iv**2) * dt + iv * math.sqrt(dt) * z)
                    path.append(price)

                    # Sell Call
                    call_premium = premium * 0.8  # call premium 略低
                    total_premium += call_premium

                    call_itm = price > strike
                    if call_itm:
                        # 被行权卖出
                        price = strike
                        was_assigned = False
                else:
                    # 未被行权
                    was_assigned = False
                    z = random.gauss(0, 1)
                    price = price * math.exp((drift - 0.5 * iv**2) * dt + iv * math.sqrt(dt) * z)
                    path.append(price)

                # 跟踪回撤
                current_val = capital + total_premium
                if current_val > max_val:
                    max_val = current_val
                dd = (max_val - current_val) / max_val
                if dd > max_dd:
                    max_dd = dd

            roi = total_premium / capital
            all_rois.append(roi)
            if total_premium > 0:
                win_count += 1
            drawdowns.append(max_dd)

            if sim_idx < 5:
                sample_paths.append(path)

        all_rois.sort()
        n = len(all_rois)

        # ROI 分布直方图
        bins = 20
        min_roi = all_rois[0]
        max_roi = all_rois[-1]
        bin_width = (max_roi - min_roi) / bins if max_roi > min_roi else 0.01
        roi_distribution = []
        for i in range(bins):
            lo = min_roi + i * bin_width
            hi = lo + bin_width
            count = sum(1 for r in all_rois if lo <= r < hi)
            roi_distribution.append([round(lo, 4), count])

        # 策略评分
        mean_roi = sum(all_rois) / n
        score = self._score_wheel(mean_roi, win_count / n, sum(drawdowns) / n)

        return {
            "success": True,
            "summary": {
                "mean_roi": round(mean_roi, 4),
                "median_roi": round(all_rois[n // 2], 4),
                "p10": round(all_rois[int(n * 0.1)], 4),
                "p25": round(all_rois[int(n * 0.25)], 4),
                "p75": round(all_rois[int(n * 0.75)], 4),
                "p90": round(all_rois[int(n * 0.9)], 4),
                "win_rate": round(win_count / simulations, 4),
                "max_drawdown_mean": round(sum(drawdowns) / len(drawdowns), 4),
                "simulations": simulations,
                "cycles": cycles,
            },
            "roi_distribution": roi_distribution,
            "sample_paths": sample_paths,
            "score": score,
        }

    def _score_wheel(self, mean_roi: float, win_rate: float,
                     mean_drawdown: float) -> Dict[str, Any]:
        """Wheel 策略评分"""
        roi_score = min(max(mean_roi / 0.20, 0), 1.0)
        wr_score = win_rate
        dd_score = max(1 - mean_drawdown / 0.30, 0)
        total = roi_score * 0.40 + wr_score * 0.35 + dd_score * 0.25

        if total >= 0.75:
            rec = "BEST"
        elif total >= 0.55:
            rec = "GOOD"
        elif total >= 0.40:
            rec = "OK"
        elif total >= 0.25:
            rec = "CAUTION"
        else:
            rec = "SKIP"

        return {"total": round(total, 4), "recommendation": rec}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard" && python -m pytest tests/test_strategy_analytics.py::TestWheelSimulator -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add services/strategy_analytics.py tests/test_strategy_analytics.py
git commit -m "feat: add WheelSimulator with Monte Carlo simulation"
```

---

### Task 6: API 端点 — /api/analytics/*

**Files:**
- Create: `api/analytics.py`
- Modify: `main.py:250-282`

- [ ] **Step 1: Create analytics API with 3 endpoints**

```python
# api/analytics.py
"""策略分析 API — Payoff + Wheel 模拟"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])


class PayoffRequest(BaseModel):
    mode: str = Field(default="single")  # single | multi | probability | time_decay
    spot: float = Field(gt=0)
    strike: float = Field(default=0)
    premium: float = Field(default=0)
    option_type: str = Field(default="PUT")
    dte: int = Field(default=30, ge=1)
    iv: float = Field(default=60, gt=0)
    quantity: float = Field(default=1, gt=0)
    side: str = Field(default="sell")
    legs: Optional[List[Dict[str, Any]]] = None
    pct_range: float = Field(default=0.3, ge=0.1, le=1.0)
    steps: int = Field(default=100, ge=10, le=500)


class WheelRequest(BaseModel):
    spot: float = Field(gt=0)
    strike: float = Field(gt=0)
    premium: float = Field(ge=0)
    option_type: str = Field(default="PUT")
    cycles: int = Field(default=6, ge=1, le=24)
    capital: float = Field(gt=0)
    assigned_pct: float = Field(default=0.5, ge=0, le=1)
    iv: float = Field(default=0.6, gt=0)
    simulations: int = Field(default=1000, ge=100, le=5000)


class EstimateRequest(BaseModel):
    spot: float = Field(gt=0)
    strike: float = Field(gt=0)
    dte: int = Field(default=30, ge=1)
    iv: float = Field(default=60, gt=0)
    option_type: str = Field(default="PUT")


@router.post("/payoff")
async def calc_payoff(req: PayoffRequest):
    """Payoff 计算（单腿/组合/概率/时间衰减）"""
    from services.strategy_analytics import PayoffEngine
    engine = PayoffEngine()

    if req.mode == "single":
        result = engine.calc_single(
            spot=req.spot, strike=req.strike, premium=req.premium,
            option_type=req.option_type, dte=req.dte, quantity=req.quantity,
            side=req.side, pct_range=req.pct_range, steps=req.steps,
        )
        score = engine.score_strategy(
            spot=req.spot, strike=req.strike, premium=req.premium,
            option_type=req.option_type, dte=req.dte,
        )
        result["score"] = score
        return {"success": True, "mode": "single", **result}

    elif req.mode == "multi":
        if not req.legs:
            raise HTTPException(status_code=400, detail="multi 模式需要 legs 参数")
        result = engine.calc_multi_legs(spot=req.spot, legs=req.legs,
                                        pct_range=req.pct_range, steps=req.steps)
        return {"success": result.get("success", True), "mode": "multi", **result}

    elif req.mode == "probability":
        result = engine.calc_probability_overlay(spot=req.spot, dte=req.dte, iv=req.iv)
        return {"success": True, "mode": "probability", **result}

    elif req.mode == "time_decay":
        result = engine.calc_time_decay(
            spot=req.spot, strike=req.strike, premium=req.premium,
            option_type=req.option_type, iv=req.iv, dte_max=req.dte,
        )
        return {"success": True, "mode": "time_decay", **result}

    else:
        raise HTTPException(status_code=400, detail=f"不支持的 mode: {req.mode}")


@router.post("/wheel")
async def calc_wheel(req: WheelRequest):
    """Wheel 蒙特卡洛模拟"""
    from services.strategy_analytics import WheelSimulator
    sim = WheelSimulator()
    result = sim.simulate(
        spot=req.spot, strike=req.strike, premium=req.premium,
        option_type=req.option_type, cycles=req.cycles, capital=req.capital,
        assigned_pct=req.assigned_pct, iv=req.iv, simulations=req.simulations,
    )
    return result


@router.post("/estimate")
async def estimate_premium(req: EstimateRequest):
    """快速权利金估算"""
    from services.strategy_analytics import PayoffEngine
    engine = PayoffEngine()
    result = engine.estimate_premium(
        spot=req.spot, strike=req.strike, dte=req.dte,
        iv=req.iv, option_type=req.option_type,
    )
    return {"success": True, **result}
```

- [ ] **Step 2: Register router in main.py**

在 `main.py` 第 252-255 行的 import 块中添加 `analytics_router`，第 280 行后添加注册行：

```python
# main.py line ~255: add to import
from api import (
    ...
    payoff_router, debate_router, analytics_router
)

# main.py line ~281: add registration
app.include_router(analytics_router, dependencies=protected_dependencies)
```

同时在 `api/__init__.py` 中添加导出：

```python
# api/__init__.py — add this line
from .analytics import router as analytics_router
```

- [ ] **Step 3: Verify server starts without errors**

Run: `cd "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard" && python -c "from api.analytics import router; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add api/analytics.py api/__init__.py main.py
git commit -m "feat: add /api/analytics/* endpoints for payoff and wheel simulation"
```

---

### Task 7: 前端 — 策略分析中心 HTML 重构

**Files:**
- Modify: `static/index.html:564-777`

- [ ] **Step 1: Replace payoffSection with 策略分析中心**

将 `index.html` 第 564-777 行（整个 payoffSection）替换为：

```html
<!-- 策略分析中心 v1.0 -->
<section id="analysisSection" class="card-glass rounded-xl p-5 mb-6 border-l-4 border-cyan-500">
    <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-2">
            <i class="fas fa-chart-area text-cyan-500"></i>
            <h3 class="font-semibold text-lg">策略分析中心</h3>
            <span class="text-xs text-gray-400 ml-2">Payoff · Wheel 模拟 · 概率分析</span>
        </div>
        <div class="flex gap-1" id="analysisModeTabs">
            <button id="anaModePayoff" class="px-3 py-1.5 rounded-lg text-sm font-medium bg-cyan-600 text-white">单腿 Payoff</button>
            <button id="anaModeMulti" class="px-3 py-1.5 rounded-lg text-sm font-medium bg-gray-700 text-gray-300">组合 Payoff</button>
            <button id="anaModeWheel" class="px-3 py-1.5 rounded-lg text-sm font-medium bg-gray-700 text-gray-300">Wheel 模拟</button>
            <button id="anaModeCompare" class="px-3 py-1.5 rounded-lg text-sm font-medium bg-gray-700 text-gray-300">策略对比</button>
        </div>
    </div>

    <!-- 单腿 Payoff -->
    <div id="anaPayoffMode">
        <div class="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
            <div>
                <label class="block text-gray-400 text-xs mb-1">方向</label>
                <select id="anaPayoffSide" class="input-dark w-full rounded-lg px-3 py-2 text-sm">
                    <option value="sell">卖出 (Sell)</option>
                    <option value="buy">买入 (Buy)</option>
                </select>
            </div>
            <div>
                <label class="block text-gray-400 text-xs mb-1">类型</label>
                <select id="anaPayoffType" class="input-dark w-full rounded-lg px-3 py-2 text-sm">
                    <option value="PUT">Put</option>
                    <option value="CALL">Call</option>
                </select>
            </div>
            <div>
                <label class="block text-gray-400 text-xs mb-1">行权价 ($)</label>
                <input type="number" id="anaPayoffStrike" value="95000" class="input-dark w-full rounded-lg px-3 py-2 text-sm">
            </div>
            <div>
                <label class="block text-gray-400 text-xs mb-1">权利金 ($)</label>
                <div class="flex gap-1">
                    <input type="number" id="anaPayoffPremium" value="2000" class="input-dark w-full rounded-lg px-3 py-2 text-sm">
                    <button id="anaEstimateBtn" class="bg-purple-500 hover:bg-purple-600 text-white px-2 py-2 rounded-lg text-sm" title="智能估算">
                        <i class="fas fa-magic"></i>
                    </button>
                </div>
            </div>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
            <div>
                <label class="block text-gray-400 text-xs mb-1">DTE (天)</label>
                <input type="number" id="anaPayoffDTE" value="30" min="1" max="365" class="input-dark w-full rounded-lg px-3 py-2 text-sm">
            </div>
            <div>
                <label class="block text-gray-400 text-xs mb-1">IV (%)</label>
                <input type="number" id="anaPayoffIV" value="60" min="10" max="200" class="input-dark w-full rounded-lg px-3 py-2 text-sm">
            </div>
            <div>
                <label class="block text-gray-400 text-xs mb-1">数量</label>
                <input type="number" id="anaPayoffQty" value="1" min="0.1" step="0.1" class="input-dark w-full rounded-lg px-3 py-2 text-sm">
            </div>
            <div class="flex items-end">
                <button id="anaCalcPayoffBtn" class="bg-cyan-500 hover:bg-cyan-600 text-white px-4 py-2 rounded-lg text-sm font-medium w-full">
                    <i class="fas fa-calculator mr-1"></i>计算 Payoff
                </button>
            </div>
        </div>
    </div>

    <!-- 组合 Payoff -->
    <div id="anaMultiMode" class="hidden">
        <div id="anaLegsList" class="space-y-2 mb-4"></div>
        <div class="flex gap-2 mb-4">
            <button id="anaAddLegBtn" class="bg-gray-700 hover:bg-gray-600 text-white px-3 py-2 rounded-lg text-sm">
                <i class="fas fa-plus mr-1"></i>添加腿
            </button>
            <button id="anaCalcMultiBtn" class="bg-cyan-500 hover:bg-cyan-600 text-white px-4 py-2 rounded-lg text-sm font-medium">
                <i class="fas fa-calculator mr-1"></i>计算组合
            </button>
        </div>
    </div>

    <!-- Wheel 模拟 -->
    <div id="anaWheelMode" class="hidden">
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
            <div>
                <label class="block text-gray-400 text-xs mb-1">行权价 ($)</label>
                <input type="number" id="anaWheelStrike" value="95000" class="input-dark w-full rounded-lg px-3 py-2 text-sm">
            </div>
            <div>
                <label class="block text-gray-400 text-xs mb-1">权利金 ($)</label>
                <input type="number" id="anaWheelPremium" value="2000" class="input-dark w-full rounded-lg px-3 py-2 text-sm">
            </div>
            <div>
                <label class="block text-gray-400 text-xs mb-1">资金 ($)</label>
                <input type="number" id="anaWheelCapital" value="100000" class="input-dark w-full rounded-lg px-3 py-2 text-sm">
            </div>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
            <div>
                <label class="block text-gray-400 text-xs mb-1">Cycles</label>
                <input type="number" id="anaWheelCycles" value="6" min="1" max="24" class="input-dark w-full rounded-lg px-3 py-2 text-sm">
            </div>
            <div>
                <label class="block text-gray-400 text-xs mb-1">IV</label>
                <input type="number" id="anaWheelIV" value="0.6" min="0.1" max="2.0" step="0.05" class="input-dark w-full rounded-lg px-3 py-2 text-sm">
            </div>
            <div>
                <label class="block text-gray-400 text-xs mb-1">模拟次数</label>
                <input type="number" id="anaWheelSims" value="1000" min="100" max="5000" step="100" class="input-dark w-full rounded-lg px-3 py-2 text-sm">
            </div>
            <div class="flex items-end">
                <button id="anaCalcWheelBtn" class="bg-cyan-500 hover:bg-cyan-600 text-white px-4 py-2 rounded-lg text-sm font-medium w-full">
                    <i class="fas fa-sync-alt mr-1"></i>运行模拟
                </button>
            </div>
        </div>
    </div>

    <!-- 策略对比 -->
    <div id="anaCompareMode" class="hidden">
        <div id="anaCompareSlots" class="space-y-2 mb-4"></div>
        <div class="flex gap-2 mb-4">
            <button id="anaAddCompareBtn" class="bg-gray-700 hover:bg-gray-600 text-white px-3 py-2 rounded-lg text-sm">
                <i class="fas fa-plus mr-1"></i>添加策略
            </button>
            <button id="anaCalcCompareBtn" class="bg-cyan-500 hover:bg-cyan-600 text-white px-4 py-2 rounded-lg text-sm font-medium">
                <i class="fas fa-balance-scale mr-1"></i>对比分析
            </button>
        </div>
        <div id="anaCompareResult" class="hidden"></div>
    </div>

    <!-- 指标卡 -->
    <div id="anaMetricsRow" class="hidden grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <div class="p-3 bg-gray-800/40 rounded-lg text-center">
            <div class="text-xs text-gray-400 mb-1">最大盈利</div>
            <div id="anaMaxProfit" class="text-lg font-bold text-green-400">--</div>
        </div>
        <div class="p-3 bg-gray-800/40 rounded-lg text-center">
            <div class="text-xs text-gray-400 mb-1">最大亏损</div>
            <div id="anaMaxLoss" class="text-lg font-bold text-red-400">--</div>
        </div>
        <div class="p-3 bg-gray-800/40 rounded-lg text-center">
            <div class="text-xs text-gray-400 mb-1">盈亏平衡</div>
            <div id="anaBreakeven" class="text-lg font-bold text-yellow-400">--</div>
        </div>
        <div class="p-3 bg-gray-800/40 rounded-lg text-center">
            <div class="text-xs text-gray-400 mb-1">策略评分</div>
            <div id="anaScore" class="text-lg font-bold text-cyan-400">--</div>
        </div>
    </div>

    <!-- Wheel 统计卡 -->
    <div id="anaWheelStats" class="hidden grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
        <div class="p-3 bg-green-500/10 border border-green-500/20 rounded-lg text-center">
            <div class="text-xs text-green-400 mb-1">平均 ROI</div>
            <div id="anaWheelMeanROI" class="text-lg font-bold text-green-400">--</div>
        </div>
        <div class="p-3 bg-blue-500/10 border border-blue-500/20 rounded-lg text-center">
            <div class="text-xs text-blue-400 mb-1">中位 ROI</div>
            <div id="anaWheelMedianROI" class="text-lg font-bold text-blue-400">--</div>
        </div>
        <div class="p-3 bg-purple-500/10 border border-purple-500/20 rounded-lg text-center">
            <div class="text-xs text-purple-400 mb-1">胜率</div>
            <div id="anaWheelWinRate" class="text-lg font-bold text-purple-400">--</div>
        </div>
        <div class="p-3 bg-yellow-500/10 border border-yellow-500/20 rounded-lg text-center">
            <div class="text-xs text-yellow-400 mb-1">P10 / P90</div>
            <div id="anaWheelQuantiles" class="text-lg font-bold text-yellow-400">--</div>
        </div>
        <div class="p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-center">
            <div class="text-xs text-red-400 mb-1">最大回撤</div>
            <div id="anaWheelDrawdown" class="text-lg font-bold text-red-400">--</div>
        </div>
    </div>

    <!-- 图表区 -->
    <div class="h-80 mb-4">
        <canvas id="anaChart"></canvas>
    </div>

    <!-- Wheel 分布图 -->
    <div id="anaWheelDistChart" class="hidden h-64 mb-4">
        <canvas id="anaDistChart"></canvas>
    </div>
</section>
```

- [ ] **Step 2: Verify HTML is valid (no syntax errors)**

在浏览器中打开页面，确认无 JS 控制台报错（新的 section 会隐藏但不应有语法错误）。

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat: replace payoffSection with strategy analysis center HTML"
```

---

### Task 8: 前端 — 策略分析中心 JS 逻辑

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Add analysis center JS functions**

在 `app.js` 中添加以下函数（在 `setupEventListeners` 函数之前）：

```javascript
// ── 策略分析中心 ──────────────────────────────────────────────
let anaChartInstance = null;
let anaDistChartInstance = null;
let anaMultiLegs = [];

window.setAnalysisMode = function(mode) {
    const modes = ['payoff', 'multi', 'wheel', 'compare'];
    const btnIds = ['anaModePayoff', 'anaModeMulti', 'anaModeWheel', 'anaModeCompare'];
    const divIds = ['anaPayoffMode', 'anaMultiMode', 'anaWheelMode', 'anaCompareMode'];

    modes.forEach((m, i) => {
        const btn = document.getElementById(btnIds[i]);
        const div = document.getElementById(divIds[i]);
        if (btn) {
            if (m === mode) {
                btn.className = 'px-3 py-1.5 rounded-lg text-sm font-medium bg-cyan-600 text-white';
            } else {
                btn.className = 'px-3 py-1.5 rounded-lg text-sm font-medium bg-gray-700 text-gray-300';
            }
        }
        if (div) div.classList.toggle('hidden', m !== mode);
    });

    // 隐藏结果区
    const metricsRow = document.getElementById('anaMetricsRow');
    const wheelStats = document.getElementById('anaWheelStats');
    const wheelDist = document.getElementById('anaWheelDistChart');
    if (metricsRow) metricsRow.classList.add('hidden');
    if (wheelStats) wheelStats.classList.add('hidden');
    if (wheelDist) wheelDist.classList.add('hidden');

    localStorage.setItem('analysis_mode', mode);
};

window.anaCalcPayoff = async function() {
    const spot = await getCurrentSpot();
    if (!spot) return;

    const strike = parseFloat(document.getElementById('anaPayoffStrike').value) || 0;
    const premium = parseFloat(document.getElementById('anaPayoffPremium').value) || 0;
    const dte = parseInt(document.getElementById('anaPayoffDTE').value) || 30;
    const iv = parseFloat(document.getElementById('anaPayoffIV').value) || 60;
    const qty = parseFloat(document.getElementById('anaPayoffQty').value) || 1;
    const side = document.getElementById('anaPayoffSide').value;
    const optionType = document.getElementById('anaPayoffType').value;

    try {
        const res = await safeFetch('/api/analytics/payoff', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                mode: 'single', spot, strike, premium,
                option_type: optionType, dte, iv,
                quantity: qty, side
            })
        });
        const data = await res.json();
        if (data.success) {
            renderAnalysisMetrics(data);
            renderPayoffChart(data.payoff_curve, spot, data.breakeven);
        }
    } catch (e) {
        console.error('Payoff calc error:', e);
    }
};

window.anaCalcWheel = async function() {
    const spot = await getCurrentSpot();
    if (!spot) return;

    const strike = parseFloat(document.getElementById('anaWheelStrike').value) || 0;
    const premium = parseFloat(document.getElementById('anaWheelPremium').value) || 0;
    const capital = parseFloat(document.getElementById('anaWheelCapital').value) || 100000;
    const cycles = parseInt(document.getElementById('anaWheelCycles').value) || 6;
    const iv = parseFloat(document.getElementById('anaWheelIV').value) || 0.6;
    const sims = parseInt(document.getElementById('anaWheelSims').value) || 1000;

    try {
        const res = await safeFetch('/api/analytics/wheel', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                spot, strike, premium, option_type: 'PUT',
                cycles, capital, iv, simulations: sims
            })
        });
        const data = await res.json();
        if (data.success) {
            renderWheelStats(data.summary, data.score);
            renderWheelDistChart(data.roi_distribution);
        }
    } catch (e) {
        console.error('Wheel simulation error:', e);
    }
};

window.anaEstimatePremium = async function() {
    const spot = await getCurrentSpot();
    if (!spot) return;

    const strike = parseFloat(document.getElementById('anaPayoffStrike').value) || 0;
    const dte = parseInt(document.getElementById('anaPayoffDTE').value) || 30;
    const iv = parseFloat(document.getElementById('anaPayoffIV').value) || 60;
    const optionType = document.getElementById('anaPayoffType').value;

    try {
        const res = await safeFetch('/api/analytics/estimate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ spot, strike, dte, iv, option_type: optionType })
        });
        const data = await res.json();
        if (data.success && data.premium > 0) {
            document.getElementById('anaPayoffPremium').value = Math.round(data.premium);
        }
    } catch (e) {
        console.error('Estimate error:', e);
    }
};

function renderAnalysisMetrics(data) {
    const metricsRow = document.getElementById('anaMetricsRow');
    if (metricsRow) metricsRow.classList.remove('hidden');

    setText('anaMaxProfit', '$' + formatNum(data.max_profit));
    setText('anaMaxLoss', '$' + formatNum(data.max_loss));
    setText('anaBreakeven', data.breakeven ? '$' + formatNum(data.breakeven) : '--');

    if (data.score) {
        const rec = data.score.recommendation || 'SKIP';
        setText('anaScore', getRecommendationLabel(rec));
    }
}

function renderWheelStats(summary, score) {
    const wheelStats = document.getElementById('anaWheelStats');
    if (wheelStats) wheelStats.classList.remove('hidden');
    const wheelDist = document.getElementById('anaWheelDistChart');
    if (wheelDist) wheelDist.classList.remove('hidden');

    const metricsRow = document.getElementById('anaMetricsRow');
    if (metricsRow) metricsRow.classList.add('hidden');

    setText('anaWheelMeanROI', (summary.mean_roi * 100).toFixed(1) + '%');
    setText('anaWheelMedianROI', (summary.median_roi * 100).toFixed(1) + '%');
    setText('anaWheelWinRate', (summary.win_rate * 100).toFixed(0) + '%');
    setText('anaWheelQuantiles', (summary.p10 * 100).toFixed(1) + '% / ' + (summary.p90 * 100).toFixed(1) + '%');
    setText('anaWheelDrawdown', (summary.max_drawdown_mean * 100).toFixed(1) + '%');
}

function renderPayoffChart(curve, spot, breakeven) {
    if (!curve || !curve.prices) return;
    const canvas = document.getElementById('anaChart');
    if (!canvas) return;

    if (anaChartInstance) anaChartInstance.destroy();

    const prices = curve.prices;
    const pnl = curve.pnl;
    const colors = pnl.map(v => v >= 0 ? 'rgba(34, 197, 94, 0.8)' : 'rgba(239, 68, 68, 0.8)');

    anaChartInstance = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: prices.map(p => formatNum(p)),
            datasets: [{
                label: '盈亏 ($)',
                data: pnl,
                backgroundColor: colors,
                borderWidth: 0,
                barPercentage: 1.0,
                categoryPercentage: 1.0,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                annotation: breakeven ? {
                    annotations: {
                        breakevenLine: {
                            type: 'line',
                            xMin: prices.findIndex(p => p >= breakeven),
                            xMax: prices.findIndex(p => p >= breakeven),
                            borderColor: 'yellow',
                            borderWidth: 2,
                            label: { display: true, content: 'BE: $' + formatNum(breakeven), color: 'yellow' }
                        }
                    }
                } : undefined,
            },
            scales: {
                x: {
                    display: true,
                    ticks: { maxTicksLimit: 10, color: '#9ca3af', font: { size: 10 } },
                    grid: { color: 'rgba(75, 85, 99, 0.3)' }
                },
                y: {
                    ticks: { color: '#9ca3af', callback: v => '$' + formatNum(v) },
                    grid: { color: 'rgba(75, 85, 99, 0.3)' }
                }
            }
        }
    });
}

function renderWheelDistChart(distribution) {
    if (!distribution || !distribution.length) return;
    const canvas = document.getElementById('anaDistChart');
    if (!canvas) return;

    if (anaDistChartInstance) anaDistChartInstance.destroy();

    const labels = distribution.map(d => (d[0] * 100).toFixed(1) + '%');
    const counts = distribution.map(d => d[1]);

    anaDistChartInstance = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: '模拟次数',
                data: counts,
                backgroundColor: 'rgba(34, 211, 238, 0.6)',
                borderColor: 'rgba(34, 211, 238, 1)',
                borderWidth: 1,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: {
                    ticks: { maxTicksLimit: 10, color: '#9ca3af', font: { size: 10 } },
                    grid: { color: 'rgba(75, 85, 99, 0.3)' }
                },
                y: {
                    ticks: { color: '#9ca3af' },
                    grid: { color: 'rgba(75, 85, 99, 0.3)' }
                }
            }
        }
    });
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

function formatNum(n) {
    if (n == null) return '--';
    return Number(n).toLocaleString('en-US', { maximumFractionDigits: 0 });
}

async function getCurrentSpot() {
    const strCurrency = document.getElementById('strCurrency');
    const currency = strCurrency ? strCurrency.value : 'BTC';
    try {
        const res = await safeFetch('/api/spot/' + currency);
        const data = await res.json();
        return data.spot || data.price || 0;
    } catch (_) {
        return 0;
    }
}

// 推荐→分析联动
window.linkToAnalysis = function(rec) {
    const section = document.getElementById('analysisSection');
    if (section) section.scrollIntoView({ behavior: 'smooth' });

    window.setAnalysisMode('payoff');

    if (rec.strike) document.getElementById('anaPayoffStrike').value = rec.strike;
    if (rec.premium_usd) document.getElementById('anaPayoffPremium').value = Math.round(rec.premium_usd);
    if (rec.dte) document.getElementById('anaPayoffDTE').value = rec.dte;
    if (rec.option_type) {
        const isPut = rec.option_type === 'P' || rec.option_type === 'PUT';
        document.getElementById('anaPayoffType').value = isPut ? 'PUT' : 'CALL';
    }
};
```

- [ ] **Step 2: Add event listeners in setupEventListeners**

在 `setupEventListeners` 函数末尾添加：

```javascript
    // 策略分析中心事件绑定
    const anaModePayoff = document.getElementById('anaModePayoff');
    if (anaModePayoff) anaModePayoff.addEventListener('click', () => setAnalysisMode('payoff'));
    const anaModeMulti = document.getElementById('anaModeMulti');
    if (anaModeMulti) anaModeMulti.addEventListener('click', () => setAnalysisMode('multi'));
    const anaModeWheel = document.getElementById('anaModeWheel');
    if (anaModeWheel) anaModeWheel.addEventListener('click', () => setAnalysisMode('wheel'));
    const anaModeCompare = document.getElementById('anaModeCompare');
    if (anaModeCompare) anaModeCompare.addEventListener('click', () => setAnalysisMode('compare'));

    const anaCalcPayoffBtn = document.getElementById('anaCalcPayoffBtn');
    if (anaCalcPayoffBtn) anaCalcPayoffBtn.addEventListener('click', anaCalcPayoff);
    const anaCalcWheelBtn = document.getElementById('anaCalcWheelBtn');
    if (anaCalcWheelBtn) anaCalcWheelBtn.addEventListener('click', anaCalcWheel);
    const anaEstimateBtn = document.getElementById('anaEstimateBtn');
    if (anaEstimateBtn) anaEstimateBtn.addEventListener('click', anaEstimatePremium);
```

- [ ] **Step 3: Add "分析" button to recommendation results**

在 `renderStrategyResults` 函数中，每行的末尾操作列里添加分析按钮。找到生成表格行的代码，在最后的 `</td>` 前添加：

```javascript
html += '<button class="ana-link-btn text-cyan-400 hover:text-cyan-300 text-xs ml-2" data-strike="' + r.strike + '" data-premium="' + (r.premium_usd||0) + '" data-dte="' + r.dte + '" data-type="' + r.option_type + '"><i class="fas fa-chart-area"></i> 分析</button>';
```

然后在 `setupEventListeners` 中用事件委托绑定：

```javascript
    // 推荐结果"分析"按钮事件委托
    document.addEventListener('click', function(e) {
        const btn = e.target.closest('.ana-link-btn');
        if (!btn) return;
        const rec = {
            strike: parseFloat(btn.dataset.strike),
            premium_usd: parseFloat(btn.dataset.premium),
            dte: parseInt(btn.dataset.dte),
            option_type: btn.dataset.type,
        };
        linkToAnalysis(rec);
    });
```

- [ ] **Step 4: Verify in browser**

打开页面，确认：
1. 策略分析中心 4 个 Tab 能正常切换
2. 单腿 Payoff 输入参数后点击"计算"能显示图表
3. Wheel 模拟点击"运行模拟"能显示统计和分布图
4. 推荐结果的"分析"按钮能跳转并填入参数

- [ ] **Step 5: Commit**

```bash
git add static/app.js
git commit -m "feat: add strategy analysis center JS with payoff chart and wheel simulation"
```

---

### Task 9: 旧端点兼容重定向

**Files:**
- Modify: `api/payoff.py`

- [ ] **Step 1: Add redirect wrappers to old endpoints**

在 `api/payoff.py` 的每个端点函数中，将实际计算逻辑替换为重定向到新端点。保留旧端点 URL 不变，内部委托给新 engine：

```python
# api/payoff.py — 替换所有端点实现

"""Payoff 计算 API (兼容层 — 委托给 analytics engine)"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["payoff"])


class PayoffCalcRequest(BaseModel):
    legs: list
    spot: float = Field(gt=0)
    pct_range: float = Field(default=0.3, ge=0.1, le=1.0)
    steps: int = Field(default=100, ge=1, le=1000)


class PayoffEstimateRequest(BaseModel):
    option_type: str = "P"
    strike: float = Field(gt=0)
    spot: float = Field(gt=0)
    dte: int = Field(default=30, ge=1)
    iv: float = Field(default=50, gt=0)


class PayoffWheelRequest(BaseModel):
    put_strike: float = Field(gt=0)
    put_premium: float = Field(ge=0)
    call_strike: float = Field(gt=0)
    call_premium: float = Field(ge=0)
    spot: float = Field(gt=0)
    quantity: int = Field(default=1, ge=1)
    put_dte: int = Field(default=30, ge=1)
    call_dte: int = Field(default=30, ge=1)


@router.post("/payoff/calc")
async def calc_payoff(data: PayoffCalcRequest):
    """计算策略Payoff图 (兼容层)"""
    from services.strategy_analytics import PayoffEngine
    engine = PayoffEngine()
    if not data.legs:
        raise HTTPException(status_code=400, detail="缺少 legs 参数")
    leg = data.legs[0] if data.legs else {}
    result = engine.calc_single(
        spot=data.spot,
        strike=leg.get("strike", data.spot),
        premium=leg.get("premium", 0),
        option_type=leg.get("option_type", "P"),
        dte=leg.get("dte", 30),
        quantity=leg.get("quantity", 1),
        side=leg.get("direction", "sell"),
        pct_range=data.pct_range,
        steps=data.steps,
    )
    # 保持旧格式兼容
    return {
        "prices": result["payoff_curve"]["prices"],
        "total_pnl": result["payoff_curve"]["pnl"],
        "legs": [{"pnl": result["payoff_curve"]["pnl"], **leg}],
        "breakevens": [result["breakeven"]] if result["breakeven"] else [],
        "max_profit": result["max_profit"],
        "max_loss": result["max_loss"],
        "spot": data.spot,
    }


@router.post("/payoff/estimate")
async def estimate_premium(data: PayoffEstimateRequest):
    """智能估算权利金 (兼容层)"""
    from services.strategy_analytics import PayoffEngine
    engine = PayoffEngine()
    result = engine.estimate_premium(
        spot=data.spot, strike=data.strike, dte=data.dte,
        iv=data.iv, option_type=data.option_type,
    )
    return {"estimated_premium": result["premium"], **result}


@router.post("/payoff/wheel")
async def calc_wheel_roi(data: PayoffWheelRequest):
    """计算 Wheel ROI (兼容层)"""
    from services.strategy_analytics import WheelSimulator
    sim = WheelSimulator()
    return sim.simulate(
        spot=data.spot, strike=data.put_strike, premium=data.put_premium,
        option_type="PUT", cycles=3, capital=data.put_strike * data.quantity,
        simulations=500,
    )


# 保留旧端点 stub（score 和 compare 已不再使用，返回 410 Gone）
@router.post("/payoff/score")
async def calc_strategy_score():
    raise HTTPException(status_code=410, detail="已迁移至 /api/analytics/payoff?mode=single")


@router.post("/payoff/compare")
async def compare_strategies():
    raise HTTPException(status_code=410, detail="已迁移至 /api/analytics/payoff?mode=multi")
```

- [ ] **Step 2: Verify old endpoints still work**

Run: `cd "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard" && python -c "from api.payoff import router; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add api/payoff.py
git commit -m "refactor: delegate old payoff endpoints to analytics engine"
```

---

### Task 10: 全量测试 + 清理

**Files:**
- Modify: `tests/test_strategy_analytics.py` (final check)
- Modify: `static/app.js` (remove debug logs)

- [ ] **Step 1: Run all strategy analytics tests**

Run: `cd "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard" && python -m pytest tests/test_strategy_analytics.py -v`
Expected: ALL PASS

- [ ] **Step 2: Run existing strategy engine tests (no regression)**

Run: `cd "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard" && python -m pytest tests/test_strategy_engine.py -v`
Expected: ALL PASS (no regression)

- [ ] **Step 3: Remove debug console.log from fetchStrategyRecommend**

在 `app.js` 的 `fetchStrategyRecommend` 函数中，删除所有 `console.log(...)` 调试语句。

- [ ] **Step 4: Final browser verification**

启动服务器，在浏览器中测试：
1. 策略推荐中心 → 点击"分析"按钮 → 跳转到分析中心并自动填充
2. 单腿 Payoff → 计算 → 图表正确显示
3. Wheel 模拟 → 运行 → 统计卡 + 分布图正确显示
4. 旧的 /payoff/calc 端点仍可访问（兼容性）

- [ ] **Step 5: Commit**

```bash
git add tests/test_strategy_analytics.py static/app.js
git commit -m "chore: full test pass, remove debug logs, verify integration"
```
