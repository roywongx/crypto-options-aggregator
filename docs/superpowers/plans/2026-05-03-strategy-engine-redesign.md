# 策略推荐引擎重设计 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重写策略推荐引擎，默认推荐值能刷出可操作的合约，多因子评分反映真实期权逻辑

**Architecture:** 合并三个策略文件为单一 `services/strategy_engine.py`，包含 ContractFilter → StrategyScorer → StrategyEngine 三类管线。新 API 端点 `POST /api/strategy/recommend`，前端重写策略区域为标签页式布局。

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, 现有 `shared_calculations.py` 纯函数库, Tailwind CSS, ES modules

---

## 文件结构

### 新建文件
| 文件 | 职责 |
|------|------|
| `dashboard/services/strategy_engine.py` | ContractFilter + StrategyScorer + StrategyEngine |
| `dashboard/tests/test_strategy_engine.py` | 策略引擎单元测试 |

### 修改文件
| 文件 | 变更 |
|------|------|
| `dashboard/models/contracts.py` | 新增 `StrategyRecommendRequest` 模型 |
| `dashboard/api/strategy.py` | 新增 `/api/strategy/recommend` 端点，保留旧端点 |
| `dashboard/templates/index.html` | 重写策略区域 HTML |
| `dashboard/static/app.js` | 新增策略推荐相关函数 |
| `dashboard/static/utils.js` | 新增推荐等级颜色映射 |

### 不动文件
| 文件 | 原因 |
|------|------|
| `dashboard/services/shared_calculations.py` | 已有评分函数，直接复用 |
| `dashboard/services/dvol_analyzer.py` | `get_dvol_from_deribit()` 和 `adapt_params_by_dvol()` 直接调用 |
| `dashboard/services/risk_framework.py` | `RiskFramework._get_floors()` 和 `CalculationEngine` 直接调用 |
| `dashboard/services/strategy_calc.py` | 旧文件，保留兼容，后续版本删除 |
| `dashboard/services/unified_strategy_engine.py` | 旧文件，保留兼容，后续版本删除 |
| `dashboard/services/grid_engine.py` | 旧文件，保留兼容，后续版本删除 |

---

## Task 1: Pydantic 模型

**Files:**
- Modify: `dashboard/models/contracts.py`

- [ ] **Step 1: 添加 StrategyRecommendRequest 模型**

在 `contracts.py` 末尾添加：

```python
class StrategyRecommendRequest(BaseModel):
    """策略推荐请求模型"""
    currency: str = Field(default="BTC", pattern="^(BTC|ETH|SOL)$")
    mode: str = Field(default="new", pattern="^(new|roll|wheel|grid)$")
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$")
    capital: float = Field(default=50000, ge=1000, description="可用资金 USDT")
    max_results: int = Field(default=10, ge=1, le=50)
    old_strike: Optional[float] = Field(default=None, description="当前持仓行权价（roll模式必填）")
    old_expiry: Optional[str] = Field(default=None, description="当前持仓到期日（roll模式必填）")
    grid_levels: int = Field(default=5, ge=2, le=20, description="网格层数（grid模式）")
    grid_interval_pct: float = Field(default=3.0, ge=0.5, le=20.0, description="网格间隔百分比（grid模式）")
    overrides: Optional[dict] = Field(default=None, description="覆盖DVOL自适应默认值")

    def model_post_init(self, __context) -> None:
        if self.mode == "roll" and self.old_strike is None:
            raise ValueError("roll 模式必须提供 old_strike")
```

- [ ] **Step 2: 验证模型可导入**

Run: `cd C:/Users/roywa/Documents/trae_projects/BRuce/crypto-options-aggregator/dashboard && python -c "from models.contracts import StrategyRecommendRequest; print(StrategyRecommendRequest(mode='new')); print(StrategyRecommendRequest(mode='roll', old_strike=90000))"`
Expected: 两个模型实例打印成功，无报错

- [ ] **Step 3: 验证 roll 模式校验**

Run: `cd C:/Users/roywa/Documents/trae_projects/BRuce/crypto-options-aggregator/dashboard && python -c "from models.contracts import StrategyRecommendRequest; StrategyRecommendRequest(mode='roll')"`
Expected: ValueError: "roll 模式必须提供 old_strike"

- [ ] **Step 4: Commit**

```bash
cd C:/Users/roywa/Documents/trae_projects/BRuce/crypto-options-aggregator
git add dashboard/models/contracts.py
git commit -m "feat: add StrategyRecommendRequest model for new strategy API"
```

---

## Task 2: ContractFilter 过滤器

**Files:**
- Create: `dashboard/services/strategy_engine.py`
- Create: `dashboard/tests/test_strategy_engine.py`

- [ ] **Step 1: 创建 strategy_engine.py 骨架和数据类**

```python
"""策略推荐引擎 v2 — 统一过滤、评分、推荐"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    """过滤结果"""
    contracts: List[dict]
    total_before: int = 0
    after_hard: int = 0
    after_dvol: int = 0
    after_strategy: int = 0
    dvol_adjustments: Dict[str, str] = field(default_factory=dict)
    dvol_regime: str = "normal"
    empty_reason: str = ""


@dataclass
class ScoreResult:
    """单合约评分结果"""
    total: float = 0.0
    ev: float = 0.0
    apr: float = 0.0
    liquidity: float = 0.0
    theta: float = 0.0
    recommendation: str = "SKIP"


@dataclass
class RecommendationResult:
    """推荐结果"""
    success: bool = False
    currency: str = "BTC"
    spot_price: float = 0.0
    dvol_snapshot: Dict[str, Any] = field(default_factory=dict)
    filter_summary: Dict[str, Any] = field(default_factory=dict)
    recommendations: List[dict] = field(default_factory=list)
    timestamp: str = ""
```

- [ ] **Step 2: 创建测试文件和硬性过滤测试**

```python
# dashboard/tests/test_strategy_engine.py
"""策略引擎单元测试"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.strategy_engine import ContractFilter, FilterResult


def _make_contract(**overrides):
    """创建测试合约"""
    base = {
        "option_type": "P", "strike": 90000, "dte": 30,
        "delta": -0.25, "premium_usd": 500, "open_interest": 500,
        "spread_pct": 2.0, "platform": "Deribit", "expiry": "2026-06-27",
        "apr": 15.0, "mark_iv": 45.0, "volume": 100,
    }
    base.update(overrides)
    return base


class TestHardFilter:
    def test_passes_valid_contract(self):
        f = ContractFilter()
        c = _make_contract()
        result = f._hard_filter([c])
        assert len(result) == 1

    def test_rejects_low_oi(self):
        f = ContractFilter()
        c = _make_contract(open_interest=5)
        result = f._hard_filter([c])
        assert len(result) == 0

    def test_rejects_high_spread(self):
        f = ContractFilter()
        c = _make_contract(spread_pct=30.0)
        result = f._hard_filter([c])
        assert len(result) == 0

    def test_rejects_expired(self):
        f = ContractFilter()
        c = _make_contract(dte=0)
        result = f._hard_filter([c])
        assert len(result) == 0

    def test_rejects_zero_premium(self):
        f = ContractFilter()
        c = _make_contract(premium_usd=0)
        result = f._hard_filter([c])
        assert len(result) == 0
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd C:/Users/roywa/Documents/trae_projects/BRuce/crypto-options-aggregator/dashboard && python -m pytest tests/test_strategy_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.strategy_engine'`

- [ ] **Step 4: 实现 ContractFilter._hard_filter**

在 `strategy_engine.py` 中添加：

```python
# 硬性过滤常量
MIN_OPEN_INTEREST = 10
MAX_SPREAD_PCT = 25.0
MIN_DTE = 1
MIN_PREMIUM = 0


class ContractFilter:
    """统一合约过滤器"""

    def _hard_filter(self, contracts: List[dict]) -> List[dict]:
        """阶段1：硬性过滤"""
        return [
            c for c in contracts
            if c.get("open_interest", 0) >= MIN_OPEN_INTEREST
            and c.get("spread_pct", 100) <= MAX_SPREAD_PCT
            and c.get("dte", 0) >= MIN_DTE
            and c.get("premium_usd", 0) > MIN_PREMIUM
        ]
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd C:/Users/roywa/Documents/trae_projects/BRuce/crypto-options-aggregator/dashboard && python -m pytest tests/test_strategy_engine.py::TestHardFilter -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add dashboard/services/strategy_engine.py dashboard/tests/test_strategy_engine.py
git commit -m "feat: add ContractFilter with hard filter stage"
```

---

## Task 3: DVOL自适应过滤

**Files:**
- Modify: `dashboard/services/strategy_engine.py`
- Modify: `dashboard/tests/test_strategy_engine.py`

- [ ] **Step 1: 添加 DVOL 自适应过滤测试**

在 `test_strategy_engine.py` 末尾添加：

```python
class TestDvolFilter:
    def test_low_vol_widens_delta(self):
        f = ContractFilter()
        dvol_snapshot = {"z_score": -1.5, "current": 25}
        params = f.get_dvol_adjusted_params({}, dvol_snapshot)
        assert params["max_delta"] == 0.40
        assert params["min_dte"] == 7
        assert params["max_dte"] == 60

    def test_normal_vol_keeps_defaults(self):
        f = ContractFilter()
        dvol_snapshot = {"z_score": 0.0, "current": 45}
        params = f.get_dvol_adjusted_params({}, dvol_snapshot)
        assert params["max_delta"] == 0.30
        assert params["min_dte"] == 14
        assert params["max_dte"] == 45

    def test_high_vol_tightens_delta(self):
        f = ContractFilter()
        dvol_snapshot = {"z_score": 2.0, "current": 80}
        params = f.get_dvol_adjusted_params({}, dvol_snapshot)
        assert params["max_delta"] == 0.25
        assert params["dte_range"] == "7-30"

    def test_overrides_take_precedence(self):
        f = ContractFilter()
        dvol_snapshot = {"z_score": 2.0, "current": 80}
        overrides = {"max_delta": 0.35}
        params = f.get_dvol_adjusted_params(overrides, dvol_snapshot)
        assert params["max_delta"] == 0.35  # 用户覆盖优先

    def test_adjustments_recorded(self):
        f = ContractFilter()
        dvol_snapshot = {"z_score": 2.0, "current": 80}
        result = f.filter([], {}, dvol_snapshot)
        assert "max_delta" in result.dvol_adjustments
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd C:/Users/roywa/Documents/trae_projects/BRuce/crypto-options-aggregator/dashboard && python -m pytest tests/test_strategy_engine.py::TestDvolFilter -v`
Expected: FAIL — `AttributeError: 'ContractFilter' object has no attribute 'get_dvol_adjusted_params'`

- [ ] **Step 3: 实现 DVOL 自适应逻辑**

在 `ContractFilter` 类中添加：

```python
    # DVOL 自适应参数表
    DVOL_PROFILES = {
        "low": {"max_delta": 0.40, "min_dte": 7, "max_dte": 60, "min_apr": 8.0},
        "normal": {"max_delta": 0.30, "min_dte": 14, "max_dte": 45, "min_apr": 10.0},
        "high": {"max_delta": 0.25, "min_dte": 7, "max_dte": 30, "min_apr": 12.0},
    }

    def _classify_dvol(self, z_score: float) -> str:
        if z_score < -1:
            return "low"
        elif z_score > 1:
            return "high"
        return "normal"

    def get_dvol_adjusted_params(self, overrides: dict, dvol_snapshot: dict) -> dict:
        """根据 DVOL z-score 生成过滤参数，用户 overrides 优先"""
        z_score = dvol_snapshot.get("z_score", 0)
        regime = self._classify_dvol(z_score)
        profile = dict(self.DVOL_PROFILES[regime])
        # 用户覆盖优先
        if overrides:
            profile.update({k: v for k, v in overrides.items() if v is not None})
        profile["regime"] = regime
        return profile

    def _dvol_filter(self, contracts: List[dict], params: dict) -> List[dict]:
        """阶段2：DVOL 自适应过滤"""
        max_delta = params.get("max_delta", 0.30)
        min_dte = params.get("min_dte", 14)
        max_dte = params.get("max_dte", 45)
        return [
            c for c in contracts
            if abs(c.get("delta", 0)) <= max_delta
            and min_dte <= c.get("dte", 0) <= max_dte
        ]
```

同时在 `filter` 方法中记录调整：

```python
    def filter(self, contracts: List[dict], overrides: dict, dvol_snapshot: dict) -> FilterResult:
        """执行完整过滤管线"""
        result = FilterResult(contracts=[], total_before=len(contracts))

        # 阶段1
        after_hard = self._hard_filter(contracts)
        result.after_hard = len(after_hard)

        # 阶段2
        params = self.get_dvol_adjusted_params(overrides or {}, dvol_snapshot)
        result.dvol_regime = params.get("regime", "normal")
        # 记录调整
        default = self.DVOL_PROFILES["normal"]
        for key in ("max_delta", "min_dte", "max_dte", "min_apr"):
            if params.get(key) != default.get(key):
                result.dvol_adjustments[key] = f"{default[key]} → {params[key]} ({result.dvol_regime}波动)"
            else:
                result.dvol_adjustments[key] = f"{default[key]} (未变)"

        after_dvol = self._dvol_filter(after_hard, params)
        result.after_dvol = len(after_dvol)

        # 阶段3 留给子类/调用方按模式过滤
        result.contracts = after_dvol
        result.after_strategy = len(after_dvol)
        return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd C:/Users/roywa/Documents/trae_projects/BRuce/crypto-options-aggregator/dashboard && python -m pytest tests/test_strategy_engine.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add dashboard/services/strategy_engine.py dashboard/tests/test_strategy_engine.py
git commit -m "feat: add DVOL-adaptive filtering to ContractFilter"
```

---

## Task 4: 策略专用过滤

**Files:**
- Modify: `dashboard/services/strategy_engine.py`
- Modify: `dashboard/tests/test_strategy_engine.py`

- [ ] **Step 1: 添加策略专用过滤测试**

在 `test_strategy_engine.py` 末尾添加：

```python
class TestStrategyFilter:
    def test_new_put_filters_otm_only(self):
        f = ContractFilter()
        spot = 100000
        contracts = [
            _make_contract(strike=90000, option_type="P"),   # OTM, pass
            _make_contract(strike=95000, option_type="P"),   # OTM but < 10%, pass
            _make_contract(strike=105000, option_type="P"),  # ITM, fail
        ]
        result = f.strategy_filter(contracts, "new", "PUT", spot, None)
        assert len(result) == 2
        assert all(c["strike"] <= spot * 0.95 for c in result)

    def test_new_call_filters_otm_only(self):
        f = ContractFilter()
        spot = 100000
        contracts = [
            _make_contract(strike=110000, option_type="C"),  # OTM, pass
            _make_contract(strike=95000, option_type="C"),   # ITM, fail
        ]
        result = f.strategy_filter(contracts, "new", "CALL", spot, None)
        assert len(result) == 1

    def test_roll_put_filters_below_current(self):
        f = ContractFilter()
        spot = 100000
        old_strike = 95000
        contracts = [
            _make_contract(strike=90000, option_type="P"),  # below old, pass
            _make_contract(strike=96000, option_type="P"),  # above old, fail
        ]
        result = f.strategy_filter(contracts, "roll", "PUT", spot, old_strike)
        assert len(result) == 1
        assert result[0]["strike"] < old_strike

    def test_wheel_filters_near_spot(self):
        f = ContractFilter()
        spot = 100000
        contracts = [
            _make_contract(strike=85000, option_type="P"),   # 15% below, pass
            _make_contract(strike=70000, option_type="P"),   # 30% below, fail
            _make_contract(strike=115000, option_type="C"),  # 15% above, pass
        ]
        result = f.strategy_filter(contracts, "wheel", "PUT", spot, None)
        assert all(spot * 0.8 <= c["strike"] <= spot * 1.2 for c in result)

    def test_empty_result_has_reason(self):
        f = ContractFilter()
        result = f.filter([], {}, {"z_score": 0, "current": 45})
        assert result.empty_reason != ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd C:/Users/roywa/Documents/trae_projects/BRuce/crypto-options-aggregator/dashboard && python -m pytest tests/test_strategy_engine.py::TestStrategyFilter -v`
Expected: FAIL

- [ ] **Step 3: 实现策略专用过滤**

在 `ContractFilter` 类中添加：

```python
    def strategy_filter(self, contracts: List[dict], mode: str, option_type: str,
                        spot: float, old_strike: Optional[float]) -> List[dict]:
        """阶段3：策略专用过滤"""
        if mode == "new":
            if option_type == "PUT":
                return [c for c in contracts if c.get("strike", 0) <= spot * 0.95]
            else:
                return [c for c in contracts if c.get("strike", 0) >= spot * 1.05]

        elif mode == "roll":
            if old_strike is None:
                return []
            if option_type == "PUT":
                return [c for c in contracts if c.get("strike", 0) < old_strike * 0.98]
            else:
                return [c for c in contracts if c.get("strike", 0) > old_strike * 1.02]

        elif mode == "wheel":
            return [c for c in contracts if spot * 0.8 <= c.get("strike", 0) <= spot * 1.2]

        elif mode == "grid":
            # 网格模式不过滤，由 StrategyEngine 分配层级
            return contracts

        return contracts
```

同时更新 `filter` 方法，加入空结果回退逻辑：

```python
    def filter(self, contracts: List[dict], overrides: dict, dvol_snapshot: dict,
               mode: str = "new", option_type: str = "PUT", spot: float = 0,
               old_strike: Optional[float] = None) -> FilterResult:
        """执行完整过滤管线"""
        result = FilterResult(contracts=[], total_before=len(contracts))

        # 阶段1
        after_hard = self._hard_filter(contracts)
        result.after_hard = len(after_hard)

        # 阶段2
        params = self.get_dvol_adjusted_params(overrides or {}, dvol_snapshot)
        result.dvol_regime = params.get("regime", "normal")
        default = self.DVOL_PROFILES["normal"]
        for key in ("max_delta", "min_dte", "max_dte", "min_apr"):
            if params.get(key) != default.get(key):
                result.dvol_adjustments[key] = f"{default[key]} → {params[key]} ({result.dvol_regime}波动)"
            else:
                result.dvol_adjustments[key] = f"{params[key]} (未变)"

        after_dvol = self._dvol_filter(after_hard, params)
        result.after_dvol = len(after_dvol)

        # 阶段3
        after_strategy = self.strategy_filter(after_dvol, mode, option_type, spot, old_strike)
        result.after_strategy = len(after_strategy)

        # 空结果回退：放松一个 DVOL 等级重试
        if not after_strategy and after_dvol:
            fallback_params = self._fallback_dvol(params, dvol_snapshot)
            after_dvol_fallback = self._dvol_filter(after_hard, fallback_params)
            after_strategy = self.strategy_filter(after_dvol_fallback, mode, option_type, spot, old_strike)
            if after_strategy:
                result.dvol_adjustments["_fallback"] = "已放松DVOL一个等级"
                result.after_strategy = len(after_strategy)

        if not after_strategy:
            result.empty_reason = f"当前{result.dvol_regime}波动环境下无符合条件的{option_type}合约"

        result.contracts = after_strategy
        return result

    def _fallback_dvol(self, params: dict, dvol_snapshot: dict) -> dict:
        """放松一个 DVOL 等级"""
        regime = params.get("regime", "normal")
        fallback_map = {"high": "normal", "normal": "low", "low": "low"}
        fallback_regime = fallback_map[regime]
        fallback = dict(self.DVOL_PROFILES[fallback_regime])
        fallback["regime"] = fallback_regime
        return fallback
```

- [ ] **Step 4: 运行全部测试**

Run: `cd C:/Users/roywa/Documents/trae_projects/BRuce/crypto-options-aggregator/dashboard && python -m pytest tests/test_strategy_engine.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add dashboard/services/strategy_engine.py dashboard/tests/test_strategy_engine.py
git commit -m "feat: add strategy-specific filters with empty-result fallback"
```

---

## Task 5: StrategyScorer 评分器

**Files:**
- Modify: `dashboard/services/strategy_engine.py`
- Modify: `dashboard/tests/test_strategy_engine.py`

- [ ] **Step 1: 添加评分测试**

在 `test_strategy_engine.py` 末尾添加：

```python
from services.strategy_engine import StrategyScorer, ScoreResult


class TestStrategyScorer:
    def test_ev_put_positive(self):
        s = StrategyScorer()
        c = _make_contract(strike=90000, premium_usd=500, delta=-0.22,
                           open_interest=1000, spread_pct=1.5, dte=30,
                           mark_iv=45.0, volume=200)
        score = s.score(c, spot_price=100000, margin_ratio=0.2)
        assert 0 <= score.ev <= 1
        assert 0 <= score.total <= 1
        assert score.recommendation in ("BEST", "GOOD", "OK", "CAUTION", "SKIP")

    def test_high_apr_gets_high_apr_score(self):
        s = StrategyScorer()
        c = _make_contract(apr=80.0)
        score = s.score(c, spot_price=100000, margin_ratio=0.2)
        assert score.apr >= 0.8

    def test_zero_oi_gets_zero_liquidity(self):
        s = StrategyScorer()
        c = _make_contract(open_interest=0, spread_pct=50.0)
        score = s.score(c, spot_price=100000, margin_ratio=0.2)
        assert score.liquidity == 0.0

    def test_recommendation_thresholds(self):
        s = StrategyScorer()
        # BEST >= 0.75, GOOD >= 0.55, OK >= 0.40, CAUTION >= 0.25
        assert s._classify_score(0.80) == "BEST"
        assert s._classify_score(0.60) == "GOOD"
        assert s._classify_score(0.45) == "OK"
        assert s._classify_score(0.30) == "CAUTION"
        assert s._classify_score(0.10) == "SKIP"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd C:/Users/roywa/Documents/trae_projects/BRuce/crypto-options-aggregator/dashboard && python -m pytest tests/test_strategy_engine.py::TestStrategyScorer -v`
Expected: FAIL

- [ ] **Step 3: 实现 StrategyScorer**

在 `strategy_engine.py` 中添加：

```python
from services.shared_calculations import (
    norm_cdf, calc_win_rate, calc_liquidity_score, calc_theta_decay,
    score_to_recommendation_level
)


class StrategyScorer:
    """多因子评分器"""

    # 权重
    W_EV = 0.40
    W_APR = 0.25
    W_LIQ = 0.20
    W_THETA = 0.15

    def score(self, contract: dict, spot_price: float, margin_ratio: float = 0.2) -> ScoreResult:
        """计算单合约综合评分"""
        result = ScoreResult()

        strike = contract.get("strike", 0)
        premium = contract.get("premium_usd", 0) or contract.get("premium", 0)
        dte = contract.get("dte", 30)
        delta = abs(contract.get("delta", 0))
        apr = contract.get("apr", 0)
        oi = contract.get("open_interest", 0)
        spread = contract.get("spread_pct", 100)
        iv = contract.get("mark_iv", 50)
        option_type = contract.get("option_type", "P")
        volume = contract.get("volume", 0)

        margin = max(strike * 0.1, (strike - premium) * margin_ratio)

        # EV
        result.ev = self._calc_ev(option_type, strike, premium, delta, margin, spot_price)

        # APR
        result.apr = min(apr / 100.0, 1.0)

        # 流动性
        result.liquidity = self._calc_liquidity(oi, spread)

        # Theta 效率
        result.theta = self._calc_theta_efficiency(premium, dte, margin)

        # 加权总分
        result.total = (
            result.ev * self.W_EV
            + result.apr * self.W_APR
            + result.liquidity * self.W_LIQ
            + result.theta * self.W_THETA
        )

        result.recommendation = self._classify_score(result.total)
        return result

    def _calc_ev(self, option_type: str, strike: float, premium: float,
                 delta: float, margin: float, spot: float) -> float:
        """计算归一化 EV"""
        if option_type in ("P", "PUT"):
            win_rate = 1 - delta
            max_profit = premium
            max_loss = strike - premium
        else:
            win_rate = delta
            max_profit = premium
            max_loss = spot * 2 - strike  # 估算

        ev = (win_rate * max_profit) - ((1 - win_rate) * max_loss)
        ev_normalized = ev / margin if margin > 0 else 0
        return max(min(ev_normalized / 0.10, 1.0), 0.0)

    def _calc_liquidity(self, oi: int, spread_pct: float) -> float:
        """计算流动性评分 (0-1)"""
        oi_score = min(oi / 500.0, 1.0)
        spread_score = max(1 - spread_pct / 10.0, 0.0)
        return oi_score * 0.6 + spread_score * 0.4

    def _calc_theta_efficiency(self, premium: float, dte: int, margin: float) -> float:
        """计算 Theta 效率 (0-1)"""
        if dte <= 0 or margin <= 0:
            return 0.0
        daily_theta = premium / dte
        annualized = daily_theta * 365 / margin
        return min(annualized / 0.50, 1.0)

    def _classify_score(self, score: float) -> str:
        """评分分类"""
        if score >= 0.75:
            return "BEST"
        elif score >= 0.55:
            return "GOOD"
        elif score >= 0.40:
            return "OK"
        elif score >= 0.25:
            return "CAUTION"
        return "SKIP"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd C:/Users/roywa/Documents/trae_projects/BRuce/crypto-options-aggregator/dashboard && python -m pytest tests/test_strategy_engine.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add dashboard/services/strategy_engine.py dashboard/tests/test_strategy_engine.py
git commit -m "feat: add StrategyScorer with EV/APR/liquidity/theta scoring"
```

---

## Task 6: StrategyEngine 主类

**Files:**
- Modify: `dashboard/services/strategy_engine.py`
- Modify: `dashboard/tests/test_strategy_engine.py`

- [ ] **Step 1: 添加集成测试**

在 `test_strategy_engine.py` 末尾添加：

```python
from services.strategy_engine import StrategyEngine, RecommendationResult


class TestStrategyEngine:
    def _make_contracts(self, n=10):
        """生成测试合约集"""
        contracts = []
        for i in range(n):
            contracts.append(_make_contract(
                strike=90000 - i * 2000,
                premium_usd=500 + i * 100,
                delta=-0.15 - i * 0.03,
                dte=20 + i * 5,
                open_interest=300 + i * 200,
                spread_pct=2.0 + i * 0.5,
                apr=10.0 + i * 3.0,
                mark_iv=40.0 + i * 2.0,
                volume=100 + i * 50,
            ))
        return contracts

    def test_recommend_returns_sorted_results(self):
        engine = StrategyEngine()
        contracts = self._make_contracts(10)
        result = engine.recommend(
            contracts=contracts,
            currency="BTC",
            mode="new",
            option_type="PUT",
            spot_price=100000,
            capital=50000,
            max_results=5,
            dvol_snapshot={"z_score": 0, "current": 45},
            overrides={},
        )
        assert result.success is True
        assert len(result.recommendations) <= 5
        # 验证排序
        scores = [r["scores"]["total"] for r in result.recommendations]
        assert scores == sorted(scores, reverse=True)

    def test_recommend_empty_contracts(self):
        engine = StrategyEngine()
        result = engine.recommend(
            contracts=[], currency="BTC", mode="new", option_type="PUT",
            spot_price=100000, capital=50000, max_results=10,
            dvol_snapshot={"z_score": 0, "current": 45}, overrides={},
        )
        assert result.success is False
        assert result.filter_summary.get("reason") == "no_contracts"

    def test_grid_generates_levels(self):
        engine = StrategyEngine()
        contracts = self._make_contracts(20)
        result = engine.grid(
            contracts=contracts, currency="BTC", spot_price=100000,
            capital=50000, levels=5, interval_pct=3.0,
            dvol_snapshot={"z_score": 0, "current": 45},
        )
        assert result.success is True
        assert len(result.recommendations) <= 5
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd C:/Users/roywa/Documents/trae_projects/BRuce/crypto-options-aggregator/dashboard && python -m pytest tests/test_strategy_engine.py::TestStrategyEngine -v`
Expected: FAIL

- [ ] **Step 3: 实现 StrategyEngine**

在 `strategy_engine.py` 中添加：

```python
from datetime import datetime, timezone


class StrategyEngine:
    """策略引擎主类"""

    def __init__(self):
        self.filter = ContractFilter()
        self.scorer = StrategyScorer()

    def recommend(self, contracts: List[dict], currency: str, mode: str,
                  option_type: str, spot_price: float, capital: float,
                  max_results: int, dvol_snapshot: dict,
                  overrides: dict = None, old_strike: float = None) -> RecommendationResult:
        """主推荐接口"""
        result = RecommendationResult(currency=currency, spot_price=spot_price)

        # DVOL 快照
        z_score = dvol_snapshot.get("z_score", 0)
        result.dvol_snapshot = {
            "current": dvol_snapshot.get("current", 0),
            "z_score": z_score,
            "regime": self.filter._classify_dvol(z_score),
        }

        # 过滤
        filter_result = self.filter.filter(
            contracts, overrides or {}, dvol_snapshot,
            mode=mode, option_type=option_type, spot=spot_price, old_strike=old_strike,
        )
        result.filter_summary = {
            "total_contracts": filter_result.total_before,
            "after_hard_filter": filter_result.after_hard,
            "after_dvol_filter": filter_result.after_dvol,
            "after_strategy_filter": filter_result.after_strategy,
            "dvol_adjustments": filter_result.dvol_adjustments,
        }

        if not filter_result.contracts:
            result.success = False
            result.filter_summary["reason"] = "no_contracts"
            result.filter_summary["message"] = filter_result.empty_reason or "当前条件下无可用合约"
            return result

        # 评分
        margin_ratio = (overrides or {}).get("margin_ratio", 0.2)
        scored = []
        for c in filter_result.contracts:
            score = self.scorer.score(c, spot_price, margin_ratio)
            scored.append(self._build_recommendation(c, score, spot_price, capital))

        # 排序
        scored.sort(key=lambda x: x["scores"]["total"], reverse=True)
        result.recommendations = scored[:max_results]

        result.success = True
        result.timestamp = datetime.now(timezone.utc).isoformat()
        return result

    def grid(self, contracts: List[dict], currency: str, spot_price: float,
             capital: float, levels: int, interval_pct: float,
             dvol_snapshot: dict, overrides: dict = None) -> RecommendationResult:
        """网格策略"""
        result = RecommendationResult(currency=currency, spot_price=spot_price)
        result.dvol_snapshot = {
            "current": dvol_snapshot.get("current", 0),
            "z_score": dvol_snapshot.get("z_score", 0),
            "regime": self.filter._classify_dvol(dvol_snapshot.get("z_score", 0)),
        }

        # 过滤（网格模式用 wheel 过滤）
        filter_result = self.filter.filter(
            contracts, overrides or {}, dvol_snapshot,
            mode="wheel", option_type="PUT", spot=spot_price,
        )
        result.filter_summary = {
            "total_contracts": filter_result.total_before,
            "after_hard_filter": filter_result.after_hard,
            "after_dvol_filter": filter_result.after_dvol,
            "after_strategy_filter": filter_result.after_strategy,
            "dvol_adjustments": filter_result.dvol_adjustments,
        }

        if not filter_result.contracts:
            result.success = False
            result.filter_summary["reason"] = "no_contracts"
            return result

        # 生成网格层级目标行权价
        grid_strikes = []
        for i in range(1, levels + 1):
            target_strike = spot_price * (1 - interval_pct * i / 100)
            grid_strikes.append(target_strike)

        # 为每层找最近合约
        margin_ratio = (overrides or {}).get("margin_ratio", 0.2)
        grid_results = []
        for target_strike in grid_strikes:
            best = self._find_nearest(filter_result.contracts, target_strike)
            if best:
                score = self.scorer.score(best, spot_price, margin_ratio)
                rec = self._build_recommendation(best, score, spot_price, capital)
                rec["grid_level"] = len(grid_results) + 1
                rec["target_strike"] = round(target_strike)
                grid_results.append(rec)

        grid_results.sort(key=lambda x: x["scores"]["total"], reverse=True)
        result.recommendations = grid_results
        result.success = True
        result.timestamp = datetime.now(timezone.utc).isoformat()
        return result

    def _build_recommendation(self, contract: dict, score: ScoreResult,
                              spot: float, capital: float) -> dict:
        """构建单条推荐"""
        strike = contract.get("strike", 0)
        premium = contract.get("premium_usd", 0) or contract.get("premium", 0)
        margin_ratio = 0.2
        margin = max(strike * 0.1, (strike - premium) * margin_ratio)

        return {
            "platform": contract.get("platform", ""),
            "option_type": contract.get("option_type", "P"),
            "strike": strike,
            "expiry": contract.get("expiry", ""),
            "dte": contract.get("dte", 0),
            "delta": contract.get("delta", 0),
            "premium_usd": premium,
            "premium_pct": round(premium / strike * 100, 2) if strike > 0 else 0,
            "apr": contract.get("apr", 0),
            "open_interest": contract.get("open_interest", 0),
            "spread_pct": contract.get("spread_pct", 0),
            "margin_required": round(margin, 2),
            "capital_efficiency": round(premium / margin * 100, 1) if margin > 0 else 0,
            "scores": {
                "total": round(score.total, 4),
                "ev": round(score.ev, 4),
                "apr": round(score.apr, 4),
                "liquidity": round(score.liquidity, 4),
                "theta": round(score.theta, 4),
                "recommendation": score.recommendation,
            },
            "risk": {
                "max_loss": round(margin - premium, 2),
                "breakeven": round(strike - premium, 2) if contract.get("option_type") in ("P", "PUT") else round(strike + premium, 2),
                "prob_profit": round(1 - abs(contract.get("delta", 0)), 2),
            },
        }

    def _find_nearest(self, contracts: List[dict], target: float) -> Optional[dict]:
        """找最接近目标行权价的合约"""
        if not contracts:
            return None
        return min(contracts, key=lambda c: abs(c.get("strike", 0) - target))
```

- [ ] **Step 4: 运行全部测试**

Run: `cd C:/Users/roywa/Documents/trae_projects/BRuce/crypto-options-aggregator/dashboard && python -m pytest tests/test_strategy_engine.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add dashboard/services/strategy_engine.py dashboard/tests/test_strategy_engine.py
git commit -m "feat: add StrategyEngine with recommend and grid methods"
```

---

## Task 7: API 端点

**Files:**
- Modify: `dashboard/api/strategy.py`

- [ ] **Step 1: 添加新端点**

在 `api/strategy.py` 中添加新端点（保留旧端点不变）：

```python
@router.post("/strategy/recommend")
async def strategy_recommend(params: StrategyRecommendRequest):
    """策略推荐端点"""
    from services.strategy_engine import StrategyEngine
    from services.dvol_analyzer import get_dvol_from_deribit
    from constants import get_dynamic_spot_price
    from db.connection import execute_read
    import json as _json

    # 获取现货价格
    spot = get_dynamic_spot_price(params.currency)

    # 获取 DVOL 数据
    dvol_raw = await run_in_threadpool(get_dvol_from_deribit, params.currency)
    dvol_snapshot = {
        "current": dvol_raw.get("current", 0),
        "z_score": dvol_raw.get("z_score", 0),
    }

    # 从数据库读取最新合约
    rows = execute_read(
        "SELECT contracts_data FROM scan_records WHERE currency = ? ORDER BY timestamp DESC LIMIT 1",
        (params.currency,)
    )
    contracts = []
    if rows and rows[0][0]:
        try:
            contracts = _json.loads(rows[0][0])
        except _json.JSONDecodeError:
            pass

    if not contracts:
        return {
            "success": False,
            "currency": params.currency,
            "spot_price": spot,
            "filter_summary": {"reason": "no_contracts", "message": "暂无扫描数据，请先执行扫描"},
            "recommendations": [],
        }

    engine = StrategyEngine()

    if params.mode == "grid":
        result = await run_in_threadpool(
            engine.grid, contracts, params.currency, spot, params.capital,
            params.grid_levels, params.grid_interval_pct, dvol_snapshot, params.overrides,
        )
    else:
        result = await run_in_threadpool(
            engine.recommend, contracts, params.currency, params.mode,
            params.option_type, spot, params.capital, params.max_results,
            dvol_snapshot, params.overrides, params.old_strike,
        )

    return {
        "success": result.success,
        "currency": result.currency,
        "spot_price": result.spot_price,
        "dvol_snapshot": result.dvol_snapshot,
        "filter_summary": result.filter_summary,
        "recommendations": result.recommendations,
        "timestamp": result.timestamp,
    }
```

- [ ] **Step 2: 验证服务启动**

Run: `cd C:/Users/roywa/Documents/trae_projects/BRuce/crypto-options-aggregator/dashboard && timeout 5 python -c "from api.strategy import router; print('Router loaded:', [r.path for r in router.routes])"`
Expected: 打印路由列表包含 `/api/strategy/recommend`

- [ ] **Step 3: Commit**

```bash
git add dashboard/api/strategy.py
git commit -m "feat: add POST /api/strategy/recommend endpoint"
```

---

## Task 8: 前端 — 控制面板

**Files:**
- Modify: `dashboard/templates/index.html`
- Modify: `dashboard/static/app.js`
- Modify: `dashboard/static/utils.js`

- [ ] **Step 1: 在 utils.js 添加推荐等级颜色映射**

在 `utils.js` 末尾添加：

```javascript
export function getRecommendationColor(rec) {
    const colors = {
        'BEST': 'text-green-400 bg-green-900/30',
        'GOOD': 'text-blue-400 bg-blue-900/30',
        'OK': 'text-gray-400 bg-gray-800/30',
        'CAUTION': 'text-orange-400 bg-orange-900/30',
        'SKIP': 'text-red-400 bg-red-900/30',
    };
    return colors[rec] || colors['SKIP'];
}

export function getRecommendationLabel(rec) {
    const labels = {
        'BEST': '强烈推荐',
        'GOOD': '推荐',
        'OK': '可考虑',
        'CAUTION': '谨慎',
        'SKIP': '不推荐',
    };
    return labels[rec] || rec;
}
```

- [ ] **Step 2: 重写 index.html 策略区域**

找到策略推荐引擎 section（约 line 780-911），替换为新结构：

```html
<!-- 策略推荐中心 -->
<section class="card-glass border-l-4 border-orange-500 mt-4">
    <div class="flex items-center justify-between mb-4">
        <h2 class="text-xl font-bold text-orange-400">
            <i class="fas fa-chess-queen mr-2"></i>策略推荐中心
        </h2>
        <div class="flex gap-1" id="strategyModeTabs">
            <button onclick="setStrategyMode('new')" id="modeNewBtn"
                class="px-3 py-1 rounded text-sm bg-blue-600 text-white">新建开仓</button>
            <button onclick="setStrategyMode('roll')" id="modeRollBtn"
                class="px-3 py-1 rounded text-sm bg-gray-700 text-gray-300">滚仓优化</button>
            <button onclick="setStrategyMode('wheel')" id="modeWheelBtn"
                class="px-3 py-1 rounded text-sm bg-gray-700 text-gray-300">轮转策略</button>
            <button onclick="setStrategyMode('grid')" id="modeGridBtn"
                class="px-3 py-1 rounded text-sm bg-gray-700 text-gray-300">网格配置</button>
        </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <!-- 左侧控制面板 -->
        <div class="lg:col-span-1 space-y-3">
            <!-- 基础参数 -->
            <div class="bg-gray-800/50 rounded-lg p-3">
                <h3 class="text-sm font-semibold text-gray-400 mb-2">基础参数</h3>
                <div class="space-y-2">
                    <div>
                        <label class="text-xs text-gray-500">币种</label>
                        <select id="strCurrency" class="w-full bg-gray-700 rounded px-2 py-1 text-sm">
                            <option value="BTC" selected>BTC</option>
                            <option value="ETH">ETH</option>
                            <option value="SOL">SOL</option>
                        </select>
                    </div>
                    <div>
                        <label class="text-xs text-gray-500">方向</label>
                        <div class="flex gap-1" id="strDirectionBtns">
                            <button onclick="setStrategyDirection('PUT')" id="strDirPut"
                                class="flex-1 px-2 py-1 rounded text-sm bg-green-600 text-white">PUT</button>
                            <button onclick="setStrategyDirection('CALL')" id="strDirCall"
                                class="flex-1 px-2 py-1 rounded text-sm bg-gray-700 text-gray-300">CALL</button>
                        </div>
                    </div>
                    <div>
                        <label class="text-xs text-gray-500">可用资金 (USDT)</label>
                        <input type="number" id="strCapital" value="50000"
                            class="w-full bg-gray-700 rounded px-2 py-1 text-sm">
                    </div>
                </div>
            </div>

            <!-- Roll 专用字段 -->
            <div id="strRollFields" class="hidden bg-gray-800/50 rounded-lg p-3">
                <h3 class="text-sm font-semibold text-orange-400 mb-2">滚仓参数</h3>
                <div class="space-y-2">
                    <div>
                        <label class="text-xs text-gray-500">当前行权价</label>
                        <input type="number" id="strOldStrike" placeholder="如 90000"
                            class="w-full bg-gray-700 rounded px-2 py-1 text-sm">
                    </div>
                    <div>
                        <label class="text-xs text-gray-500">当前到期日</label>
                        <input type="date" id="strOldExpiry"
                            class="w-full bg-gray-700 rounded px-2 py-1 text-sm">
                    </div>
                </div>
            </div>

            <!-- Grid 专用字段 -->
            <div id="strGridFields" class="hidden bg-gray-800/50 rounded-lg p-3">
                <h3 class="text-sm font-semibold text-purple-400 mb-2">网格参数</h3>
                <div class="space-y-2">
                    <div>
                        <label class="text-xs text-gray-500">网格层数</label>
                        <input type="number" id="strGridLevels" value="5" min="2" max="20"
                            class="w-full bg-gray-700 rounded px-2 py-1 text-sm">
                    </div>
                    <div>
                        <label class="text-xs text-gray-500">间隔百分比 (%)</label>
                        <input type="number" id="strGridInterval" value="3" min="0.5" max="20" step="0.5"
                            class="w-full bg-gray-700 rounded px-2 py-1 text-sm">
                    </div>
                </div>
            </div>

            <!-- 高级筛选（折叠） -->
            <details class="bg-gray-800/50 rounded-lg">
                <summary class="p-3 text-sm font-semibold text-gray-400 cursor-pointer">高级筛选</summary>
                <div class="px-3 pb-3 space-y-2">
                    <div>
                        <label class="text-xs text-gray-500">最大 Delta</label>
                        <input type="number" id="strMaxDelta" value="0.30" step="0.05" min="0.1" max="0.5"
                            class="w-full bg-gray-700 rounded px-2 py-1 text-sm">
                    </div>
                    <div>
                        <label class="text-xs text-gray-500">DTE 范围</label>
                        <div class="flex gap-1">
                            <input type="number" id="strMinDte" value="14" min="1"
                                class="w-1/2 bg-gray-700 rounded px-2 py-1 text-sm" placeholder="最小">
                            <input type="number" id="strMaxDte" value="45" min="1"
                                class="w-1/2 bg-gray-700 rounded px-2 py-1 text-sm" placeholder="最大">
                        </div>
                    </div>
                    <div>
                        <label class="text-xs text-gray-500">最低 APR (%)</label>
                        <input type="number" id="strMinApr" value="10" min="0" step="1"
                            class="w-full bg-gray-700 rounded px-2 py-1 text-sm">
                    </div>
                </div>
            </details>

            <button onclick="fetchStrategyRecommend()" id="strSubmitBtn"
                class="w-full py-2 bg-orange-600 hover:bg-orange-700 rounded text-sm font-semibold transition">
                <i class="fas fa-search mr-1"></i>获取推荐
            </button>
        </div>

        <!-- 右侧结果区 -->
        <div class="lg:col-span-3">
            <!-- DVOL 警告条 -->
            <div id="strDvolWarning" class="hidden mb-3 p-2 bg-yellow-900/30 border border-yellow-600 rounded text-sm text-yellow-300">
            </div>

            <!-- 加载状态 -->
            <div id="strLoading" class="hidden text-center py-8">
                <div class="animate-spin inline-block w-8 h-8 border-4 border-orange-500 border-t-transparent rounded-full"></div>
                <p class="mt-2 text-gray-400" id="strLoadingText">正在分析合约...</p>
            </div>

            <!-- 空结果 -->
            <div id="strEmpty" class="hidden text-center py-8 text-gray-500">
                <i class="fas fa-inbox text-3xl mb-2"></i>
                <p id="strEmptyMessage">暂无推荐结果</p>
            </div>

            <!-- 推荐结果表 -->
            <div id="strResultsWrapper">
                <div class="text-center py-12 text-gray-500">
                    <i class="fas fa-chess-queen text-4xl mb-3 opacity-30"></i>
                    <p>选择参数后点击"获取推荐"</p>
                </div>
            </div>
        </div>
    </div>
</section>
```

- [ ] **Step 3: 在 app.js 添加控制面板逻辑**

在 `app.js` 中添加（找到策略相关函数附近）：

```javascript
// ========== 策略推荐中心 v2 ==========
let _strMode = 'new';
let _strDirection = 'PUT';

window.setStrategyMode = function(mode) {
    _strMode = mode;
    // 重置按钮样式
    const btns = {new: 'modeNewBtn', roll: 'modeRollBtn', wheel: 'modeWheelBtn', grid: 'modeGridBtn'};
    const colors = {new: 'bg-blue-600', roll: 'bg-orange-600', wheel: 'bg-green-600', grid: 'bg-purple-600'};
    Object.entries(btns).forEach(([k, id]) => {
        const el = document.getElementById(id);
        if (el) el.className = `px-3 py-1 rounded text-sm ${k === mode ? colors[k] + ' text-white' : 'bg-gray-700 text-gray-300'}`;
    });
    // 显示/隐藏专用字段
    const rollFields = document.getElementById('strRollFields');
    const gridFields = document.getElementById('strGridFields');
    if (rollFields) rollFields.classList.toggle('hidden', mode !== 'roll');
    if (gridFields) gridFields.classList.toggle('hidden', mode !== 'grid');
    // 保存偏好
    try { localStorage.setItem('strategy_mode', mode); } catch(_) {}
};

window.setStrategyDirection = function(dir) {
    _strDirection = dir;
    const putBtn = document.getElementById('strDirPut');
    const callBtn = document.getElementById('strDirCall');
    if (putBtn) putBtn.className = `flex-1 px-2 py-1 rounded text-sm ${dir === 'PUT' ? 'bg-green-600 text-white' : 'bg-gray-700 text-gray-300'}`;
    if (callBtn) callBtn.className = `flex-1 px-2 py-1 rounded text-sm ${dir === 'CALL' ? 'bg-red-600 text-white' : 'bg-gray-700 text-gray-300'}`;
    try { localStorage.setItem('strategy_direction', dir); } catch(_) {}
};

window.fetchStrategyRecommend = async function() {
    const loading = document.getElementById('strLoading');
    const empty = document.getElementById('strEmpty');
    const wrapper = document.getElementById('strResultsWrapper');
    const dvolWarn = document.getElementById('strDvolWarning');

    if (loading) loading.classList.remove('hidden');
    if (empty) empty.classList.add('hidden');
    if (wrapper) wrapper.innerHTML = '';
    if (dvolWarn) dvolWarn.classList.add('hidden');

    const body = {
        currency: document.getElementById('strCurrency')?.value || 'BTC',
        mode: _strMode,
        option_type: _strDirection,
        capital: parseFloat(document.getElementById('strCapital')?.value) || 50000,
        max_results: 10,
        grid_levels: parseInt(document.getElementById('strGridLevels')?.value) || 5,
        grid_interval_pct: parseFloat(document.getElementById('strGridInterval')?.value) || 3.0,
        overrides: {},
    };

    // 高级筛选
    const maxDelta = parseFloat(document.getElementById('strMaxDelta')?.value);
    const minDte = parseInt(document.getElementById('strMinDte')?.value);
    const maxDte = parseInt(document.getElementById('strMaxDte')?.value);
    const minApr = parseFloat(document.getElementById('strMinApr')?.value);
    if (!isNaN(maxDelta)) body.overrides.max_delta = maxDelta;
    if (!isNaN(minDte)) body.overrides.min_dte = minDte;
    if (!isNaN(maxDte)) body.overrides.max_dte = maxDte;
    if (!isNaN(minApr)) body.overrides.min_apr = minApr;
    if (Object.keys(body.overrides).length === 0) body.overrides = null;

    // Roll 专用参数
    if (_strMode === 'roll') {
        body.old_strike = parseFloat(document.getElementById('strOldStrike')?.value) || null;
        body.old_expiry = document.getElementById('strOldExpiry')?.value || null;
        if (!body.old_strike) {
            if (loading) loading.classList.add('hidden');
            showAlert('滚仓模式必须填写当前行权价', 'error');
            return;
        }
    }

    try {
        const res = await safeFetch('/api/strategy/recommend', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (loading) loading.classList.add('hidden');

        if (!data.success || !data.recommendations?.length) {
            if (empty) empty.classList.remove('hidden');
            const msg = document.getElementById('strEmptyMessage');
            if (msg) msg.textContent = data.filter_summary?.message || '当前条件下无可用合约';
            renderFilterSummary(data.filter_summary);
            return;
        }

        // DVOL 警告
        const z = data.dvol_snapshot?.z_score || 0;
        if (Math.abs(z) > 2 && dvolWarn) {
            dvolWarn.classList.remove('hidden');
            dvolWarn.textContent = `⚠️ DVOL z-score ${z.toFixed(1)} — 当前处于极端波动区间，建议谨慎操作`;
        }

        renderStrategyResults(data);
    } catch (e) {
        if (loading) loading.classList.add('hidden');
        showAlert('策略推荐请求失败: ' + e.message, 'error');
    }
};
```

- [ ] **Step 4: 验证页面加载无报错**

启动服务并检查浏览器控制台无 JS 错误。

- [ ] **Step 5: Commit**

```bash
git add dashboard/templates/index.html dashboard/static/app.js dashboard/static/utils.js
git commit -m "feat: add strategy recommendation control panel UI"
```

---

## Task 9: 前端 — 结果渲染

**Files:**
- Modify: `dashboard/static/app.js`

- [ ] **Step 1: 添加结果渲染函数**

在 `app.js` 中添加：

```javascript
function renderStrategyResults(data) {
    const wrapper = document.getElementById('strResultsWrapper');
    if (!wrapper) return;

    // 筛选摘要
    let html = renderFilterSummary(data.filter_summary);

    // 推荐表格
    html += `<div class="overflow-x-auto"><table class="w-full text-sm">
        <thead><tr class="text-gray-400 border-b border-gray-700">
            <th class="py-2 px-2 text-left">#</th>
            <th class="py-2 px-2 text-left">平台</th>
            <th class="py-2 px-2 text-left">方向</th>
            <th class="py-2 px-2 text-right">行权价</th>
            <th class="py-2 px-2 text-left">到期日</th>
            <th class="py-2 px-2 text-right">DTE</th>
            <th class="py-2 px-2 text-right">Delta</th>
            <th class="py-2 px-2 text-right">权利金</th>
            <th class="py-2 px-2 text-right">APR</th>
            <th class="py-2 px-2 text-right">持仓量</th>
            <th class="py-2 px-2 text-right">价差</th>
            <th class="py-2 px-2 text-right">评分</th>
            <th class="py-2 px-2 text-center">推荐</th>
        </tr></thead><tbody>`;

    data.recommendations.forEach((r, i) => {
        const sc = r.scores || {};
        const recColor = window.getRecommendationColor
            ? window.getRecommendationColor(sc.recommendation) : 'text-gray-400';
        const recLabel = window.getRecommendationLabel
            ? window.getRecommendationLabel(sc.recommendation) : sc.recommendation;
        const rowBg = i === 0 ? 'bg-green-900/10' : 'hover:bg-gray-800/50';

        html += `<tr class="border-b border-gray-800 ${rowBg} cursor-pointer" onclick="toggleStrategyDetail(this)">
            <td class="py-2 px-2">${i === 0 ? '👑' : i + 1}</td>
            <td class="py-2 px-2">${safeHTML(r.platform)}</td>
            <td class="py-2 px-2">${r.option_type === 'PUT' ? '🟢 PUT' : '🔴 CALL'}</td>
            <td class="py-2 px-2 text-right font-mono">${(r.strike || 0).toLocaleString()}</td>
            <td class="py-2 px-2">${safeHTML(r.expiry)}</td>
            <td class="py-2 px-2 text-right">${r.dte}</td>
            <td class="py-2 px-2 text-right">${(r.delta || 0).toFixed(3)}</td>
            <td class="py-2 px-2 text-right">$${(r.premium_usd || 0).toLocaleString()}</td>
            <td class="py-2 px-2 text-right">${(r.apr || 0).toFixed(1)}%</td>
            <td class="py-2 px-2 text-right">${(r.open_interest || 0).toLocaleString()}</td>
            <td class="py-2 px-2 text-right">${(r.spread_pct || 0).toFixed(1)}%</td>
            <td class="py-2 px-2 text-right font-mono">${(sc.total || 0).toFixed(3)}</td>
            <td class="py-2 px-2 text-center"><span class="px-2 py-0.5 rounded text-xs ${recColor}">${recLabel}</span></td>
        </tr>
        <tr class="hidden detail-row"><td colspan="13" class="px-4 py-3 bg-gray-900/50">
            <div class="grid grid-cols-4 gap-4 text-xs">
                <div><span class="text-gray-500">EV评分:</span> ${(sc.ev || 0).toFixed(3)}</div>
                <div><span class="text-gray-500">APR评分:</span> ${(sc.apr || 0).toFixed(3)}</div>
                <div><span class="text-gray-500">流动性:</span> ${(sc.liquidity || 0).toFixed(3)}</div>
                <div><span class="text-gray-500">Theta:</span> ${(sc.theta || 0).toFixed(3)}</div>
                <div><span class="text-gray-500">保证金:</span> $${(r.margin_required || 0).toLocaleString()}</div>
                <div><span class="text-gray-500">资本效率:</span> ${r.capital_efficiency || 0}%</div>
                <div><span class="text-gray-500">最大亏损:</span> $${(r.risk?.max_loss || 0).toLocaleString()}</div>
                <div><span class="text-gray-500">盈亏平衡:</span> ${(r.risk?.breakeven || 0).toLocaleString()}</div>
            </div>
        </td></tr>`;
    });

    html += '</tbody></table></div>';
    wrapper.innerHTML = html;
}

function renderFilterSummary(summary) {
    if (!summary) return '';
    const el = document.getElementById('strResultsWrapper');
    // 返回HTML片段，由调用方拼接
    let html = '<div class="mb-3 p-3 bg-gray-800/50 rounded-lg text-xs text-gray-400">';
    html += '<div class="flex flex-wrap gap-4 mb-2">';
    html += `<span>总合约: <b class="text-white">${summary.total_contracts || 0}</b></span>`;
    html += `<span>→ 硬性过滤: <b class="text-white">${summary.after_hard_filter || 0}</b></span>`;
    html += `<span>→ DVOL过滤: <b class="text-white">${summary.after_dvol_filter || 0}</b></span>`;
    html += `<span>→ 策略过滤: <b class="text-orange-400">${summary.after_strategy_filter || 0}</b></span>`;
    html += '</div>';

    // DVOL 调整明细
    const adj = summary.dvol_adjustments || {};
    const adjEntries = Object.entries(adj).filter(([k]) => !k.startsWith('_'));
    if (adjEntries.length) {
        html += '<div class="text-gray-500">DVOL调整: ';
        html += adjEntries.map(([k, v]) => `${k}: ${v}`).join(' | ');
        html += '</div>';
    }
    if (adj._fallback) {
        html += `<div class="text-yellow-500 mt-1">⚠️ ${adj._fallback}</div>`;
    }
    html += '</div>';
    return html;
}

window.toggleStrategyDetail = function(row) {
    const detail = row.nextElementSibling;
    if (detail && detail.classList.contains('detail-row')) {
        detail.classList.toggle('hidden');
    }
};
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/static/app.js
git commit -m "feat: add strategy results table rendering with expandable details"
```

---

## Task 10: 自动刷新和参数记忆

**Files:**
- Modify: `dashboard/static/app.js`

- [ ] **Step 1: 添加参数记忆和自动恢复**

在 `app.js` 的初始化逻辑中添加（`DOMContentLoaded` 回调内）：

```javascript
// 恢复策略偏好
try {
    const savedMode = localStorage.getItem('strategy_mode');
    if (savedMode && ['new', 'roll', 'wheel', 'grid'].includes(savedMode)) {
        setStrategyMode(savedMode);
    }
    const savedDir = localStorage.getItem('strategy_direction');
    if (savedDir && ['PUT', 'CALL'].includes(savedDir)) {
        setStrategyDirection(savedDir);
    }
} catch(_) {}
```

- [ ] **Step 2: 添加自动刷新（可选）**

在 `fetchStrategyRecommend` 成功后：

```javascript
// 设置自动刷新
if (window._strAutoRefreshTimer) clearInterval(window._strAutoRefreshTimer);
window._strAutoRefreshTimer = setInterval(() => {
    if (!document.getElementById('strResultsWrapper')?.querySelector('table')) {
        clearInterval(window._strAutoRefreshTimer);
        return;
    }
    fetchStrategyRecommend();
}, 60000);
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/static/app.js
git commit -m "feat: add strategy parameter persistence and auto-refresh"
```

---

## Task 11: 端到端验证

**Files:**
- 无新文件，验证性任务

- [ ] **Step 1: 启动服务**

Run: `cd C:/Users/roywa/Documents/trae_projects/BRuce/crypto-options-aggregator/dashboard && python main.py`
Expected: 服务启动，无报错

- [ ] **Step 2: 调用推荐 API 测试**

Run: `curl -s -X POST http://localhost:8000/api/strategy/recommend -H "Content-Type: application/json" -d '{"currency":"BTC","mode":"new","option_type":"PUT","capital":50000}' | python -m json.tool`
Expected: 返回 success=true，recommendations 非空

- [ ] **Step 3: 浏览器测试**

打开 http://localhost:8000，切换到策略推荐中心，点击"获取推荐"，验证：
1. 结果表格显示推荐合约
2. 点击行展开详情
3. 模式切换（新建/滚仓/轮转/网格）正常
4. 方向切换（PUT/CALL）正常
5. DVOL z-score 极端时显示警告

- [ ] **Step 4: 验证默认参数出结果**

确认使用默认参数（不修改任何筛选条件）能刷出至少5个合约。

- [ ] **Step 5: Final Commit**

```bash
git add -A
git commit -m "feat: strategy engine v2 complete - unified filter/scorer/recommend"
```

---

## 任务依赖

```
Task 1 (模型) ─────────────────────────────────────┐
Task 2 (硬性过滤) ──┐                               │
Task 3 (DVOL过滤) ──┤                               │
Task 4 (策略过滤) ──┼── Task 6 (StrategyEngine) ────┤
Task 5 (评分器) ────┘                               │
                                                     ├── Task 11 (端到端验证)
Task 7 (API端点) ───────────────────────────────────┤
Task 8 (前端控制面板) ──┐                            │
Task 9 (前端结果渲染) ──┼────────────────────────────┘
Task 10 (自动刷新) ────┘
```

Task 1-6 可按顺序串行（后端），Task 7 依赖 Task 1+6，Task 8-10 可并行（前端），Task 11 最后执行。
