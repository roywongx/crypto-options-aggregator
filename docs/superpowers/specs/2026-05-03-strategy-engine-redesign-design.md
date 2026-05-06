# 策略推荐引擎重设计

**日期**: 2026-05-03
**状态**: 设计通过，待实现
**范围**: Crypto Options Aggregator Pro v5.7+ 策略中心模块

---

## 1. 问题陈述

当前策略推荐引擎存在6个根本性问题，导致默认推荐值刷不出任何策略：

1. **Roll模式默认 `old_strike=None`** → `current_strike=0` → 过滤掉所有合约
2. **前端发送 `min_apr=8` 但后端忽略**，使用默认值 `15.0`
3. **`target_apr=200`** 抑制所有评分为 SKIP/CAUTION
4. **Scan引擎预过滤过严**（DTE 14-35, DVOL调整delta至0.28）
5. **DTE范围不匹配**：scan用14-35，strategy用7-90，scan是绑定约束
6. **三个文件参数冲突、逻辑重复**（`strategy_calc.py`、`unified_strategy_engine.py`、`grid_engine.py`）

## 2. 设计目标

- 默认推荐值能刷出至少5个可操作的合约
- 多因子评分反映真实的期权交易逻辑（胜率/BS定价/Theta衰减/Greeks）
- 一站式策略中心：新建开仓、滚仓优化、轮转策略、网格配置共享统一引擎
- DVOL自适应调整透明可见，不静默覆盖用户参数

## 3. 模块架构

### 3.1 文件合并

将以下三个文件合并为单一 `services/strategy_engine.py`：

| 原文件 | 行数 | 处理 |
|--------|------|------|
| `services/strategy_calc.py` | ~200 | 合并，逻辑迁入 |
| `services/unified_strategy_engine.py` | ~600 | 合并，逻辑迁入 |
| `services/grid_engine.py` | ~450 | 合并，逻辑迁入 |

新文件结构：

```python
# services/strategy_engine.py

class ContractFilter:
    """统一合约过滤器"""
    def filter(self, contracts, params, dvol_snapshot) -> FilterResult

class StrategyScorer:
    """多因子评分器"""
    def score(self, contract, spot_price, params) -> ScoreResult

class StrategyEngine:
    """策略引擎主类"""
    def recommend(self, params) -> RecommendationResult
    def grid(self, params) -> GridResult
```

### 3.2 类职责

- **ContractFilter**：执行4阶段过滤管线，返回过滤结果 + 调整元数据
- **StrategyScorer**：计算 EV/APR/流动性/Theta 四维评分 + 加权总分
- **StrategyEngine**：编排过滤 → 评分 → 排序 → 格式化输出

## 4. 过滤管线

### 阶段1 — 硬性过滤

```python
HARD_FILTERS = {
    "min_open_interest": 10,
    "max_spread_pct": 25.0,
    "min_dte": 1,
    "min_premium": 0,
}
```

### 阶段2 — DVOL自适应窗口

根据 DVOL z-score 动态调整 delta 和 DTE 范围：

| 波动率区间 | z-score | max_delta | DTE范围 |
|-----------|---------|-----------|---------|
| 低波动 | z < -1 | 0.40 | 7-60 |
| 正常 | -1 ≤ z ≤ 1 | 0.30 | 14-45 |
| 高波动 | z > 1 | 0.25 | 7-30 |

**关键**：DVOL 调整作为元数据返回给前端显示，不静默覆盖用户 overrides。

### 阶段3 — 策略专用过滤

| 模式 | 期权方向 | 过滤条件 |
|------|---------|---------|
| 滚仓 PUT | P | strike < 当前行权价 × 0.95 |
| 滚仓 CALL | C | strike > 当前行权价 × 1.05 |
| 新建 PUT | P | strike ≤ 现货 × 0.90 |
| 新建 CALL | C | strike ≥ 现货 × 1.10 |
| 轮转策略 | P或C | strike 在现货 ±20% 范围内 |
| 网格策略 | P | 现货下方等间隔多档行权价 |

### 跨平台处理

各平台合约独立保留，不做合并去重，作为多平台参考。

### 空结果回退

如果阶段3过滤后无结果，放松一个 DVOL 等级重试一次。仍为空则返回空结果并附带解释信息。

## 5. 评分框架

### 5.1 加权公式

```
score = EV × 0.40 + APR × 0.25 + LIQ × 0.20 + THETA × 0.15
```

### 5.2 EV（期望值）— 权重40%

```
ev = (win_rate × max_profit) − (loss_rate × max_loss)
ev_normalized = ev / capital_at_risk
```

- **PUT**：win_rate = 1 − delta绝对值，max_profit = 权利金，max_loss = strike − 权利金
- **CALL**：win_rate = delta，max_profit = 权利金，max_loss = 无上限（用 strike × 2 估算）
- **capital_at_risk** = margin_required
- 归一化：`ev_score = clamp(ev_normalized / 0.10, 0, 1)`（10% EV归一化为满分）

### 5.3 APR — 权重25%

```
apr_score = min(apr / 100, 1.0)
```

- 旧设计用 `target_apr=200` 导致所有评分被压制
- 新设计：100% APR = 满分，超过不再加分

### 5.4 流动性 — 权重20%

```
oi_score = min(open_interest / 500, 1.0)      # OI阈值从1000降到500
spread_score = max(1 − spread_pct / 10, 0)    # 10%价差=0分
liquidity = oi_score × 0.6 + spread_score × 0.4
```

- OI阈值从1000降到500，因为加密期权市场深度有限
- 价差阈值10%，超过则流动性为0

### 5.5 Theta效率 — 权重15%

```
theta_eff = (premium / dte) / margin_required × 365
theta_score = min(theta_eff / 0.50, 1.0)      # 50%年化theta效率=满分
```

- 衡量每日权利金收入相对于保证金占用的效率
- 年化后与50%基准比较

### 5.6 推荐等级

| 等级 | 总分范围 | 含义 |
|------|---------|------|
| BEST | >= 0.75 | 强烈推荐 |
| GOOD | >= 0.55 | 推荐 |
| OK | >= 0.40 | 可考虑 |
| CAUTION | >= 0.25 | 谨慎 |
| SKIP | < 0.25 | 不推荐 |

旧阈值（BEST>=0.85等）过高导致几乎无合约达标。

## 6. API设计

### 6.1 `POST /api/strategy/recommend`

**请求体**：

```json
{
  "currency": "BTC",
  "mode": "new",
  "option_type": "PUT",
  "capital": 50000,
  "max_results": 10,
  "old_strike": null,
  "old_expiry": null,
  "grid_levels": 5,
  "grid_interval_pct": 3.0,
  "overrides": {
    "max_delta": 0.30,
    "min_dte": 14,
    "max_dte": 45,
    "min_apr": 10.0
  }
}
```

- `currency`: BTC / ETH / SOL
- `mode`: new / roll / wheel / grid
- `option_type`: PUT / CALL
- `capital`: 可用资金（USDT），用于计算仓位和资本效率
- `max_results`: 返回前N个最优合约，默认10
- `old_strike`: 当前持仓行权价（roll模式必填，其他模式忽略）
- `old_expiry`: 当前持仓到期日（roll模式必填，其他模式忽略）
- `grid_levels`: 网格层数（grid模式使用，默认5）
- `grid_interval_pct`: 网格间隔百分比（grid模式使用，默认3%）
- `overrides`: 可选，覆盖DVOL自适应的默认值

**响应**：

```json
{
  "success": true,
  "currency": "BTC",
  "spot_price": 103500,
  "dvol_snapshot": {
    "current": 45.2,
    "z_score": 0.8,
    "regime": "normal"
  },
  "filter_summary": {
    "total_contracts": 150,
    "after_hard_filter": 82,
    "after_dvol_filter": 34,
    "after_strategy_filter": 12,
    "dvol_adjustments": {
      "max_delta": "0.30 → 0.28 (高波动微调)",
      "dte_range": "14-45 (未变)"
    }
  },
  "recommendations": [
    {
      "rank": 1,
      "platform": "Deribit",
      "option_type": "P",
      "strike": 90000,
      "expiry": "2026-06-27",
      "dte": 55,
      "delta": -0.22,
      "premium_usd": 850,
      "premium_pct": 0.94,
      "apr": 18.5,
      "open_interest": 1200,
      "spread_pct": 1.2,
      "margin_required": 9500,
      "capital_efficiency": 8.9,
      "scores": {
        "total": 0.82,
        "ev": 0.78,
        "apr": 0.85,
        "liquidity": 0.90,
        "theta": 0.72,
        "recommendation": "BEST"
      },
      "risk": {
        "max_loss": 8150,
        "breakeven": 89150,
        "prob_profit": 0.72,
        "loss_at_10pct_drop": -1200
      }
    }
  ],
  "timestamp": "2026-05-03T12:00:00Z"
}
```

### 6.2 `GET /api/strategy/grid`

便捷端点，等同于 `POST /api/strategy/recommend` 且 `mode: "grid"`。

请求参数：
```
currency=BTC&grid_levels=5&grid_interval_pct=3&capital=50000
```

响应额外包含 `grid_levels` 数组，每层一个合约。内部调用 `StrategyEngine.grid()`。

### 6.3 旧端点兼容

- `POST /api/strategy/calc` 保留，内部转发到新引擎
- 旧响应格式字段保留，新字段追加而非替换

### 6.4 错误处理

无数据时返回：
```json
{
  "success": false,
  "reason": "no_contracts",
  "message": "当前条件下无可用合约，建议放宽筛选参数"
}
```

## 7. 前端设计

### 7.1 页面布局

**顶部 — 控制栏**：
- 币种选择（BTC/ETH/SOL）
- 模式切换标签页：[新建开仓] [滚仓优化] [轮转策略] [网格配置]
- 资金输入框（USDT）
- 方向切换：PUT / CALL 按钮组
- "高级筛选" 折叠面板（overrides 参数）
- "获取推荐"按钮

**中部 — 推荐结果表**：

| 列 | 说明 |
|----|------|
| 排名 | 1, 2, 3... |
| 平台 | Deribit / Binance |
| 方向 | PUT / CALL |
| 行权价 | strike |
| 到期日 | expiry |
| DTE | 到期天数 |
| Delta | 希腊字母 |
| 权利金 | premium_usd |
| APR | 年化收益率 |
| 持仓量 | open_interest |
| 价差 | spread_pct |
| 综合评分 | 0.00-1.00 |
| 推荐 | BEST/GOOD/OK/CAUTION/SKIP |

- 支持按列排序
- 推荐等级颜色：BEST=绿, GOOD=蓝, OK=灰, CAUTION=橙, SKIP=红
- 点击行展开详情：风险指标、各分项得分、DVOL调整说明
- 每行右侧"模拟下单"按钮，跳转沙盒模块

**底部 — 信息面板（两栏）**：

左栏：筛选摘要
- 各阶段过滤后的合约数量变化
- DVOL 调整明细
- 当前 DVOL 状态

右栏：网格视图（仅网格模式）
- 各档位行权价在当前价格下方的可视化分布
- 每档：行权价、权利金、APR、评分

### 7.2 交互细节

1. **加载状态**：骨架屏 + "正在分析 X 个合约..."
2. **空结果**：显示具体原因 + 调整建议
3. **DVOL警告**：z > 2 或 z < -2 时顶部黄色警告条
4. **自动刷新**：每60秒自动获取推荐（可关闭）
5. **参数记忆**：模式和参数存入 localStorage

### 7.3 与现有页面关系

- 替换当前 `/strategy` 页面全部内容
- `/grid-strategy` 内容合并到新页面网格标签页
- 沙盒模块（`/sandbox`）保持独立

## 8. 实现要点

### 8.1 数据来源

- 合约数据：从最新扫描的 `scan_records.contracts_data` 读取（JSON）
- DVOL数据：从最新扫描的 `scan_records.raw_output` 读取（JSON）
- 现货价格：`constants.get_dynamic_spot_price()` 带多级回退
- 风控底线：`RiskFramework._get_floors()` 读取

### 8.2 依赖关系

```
strategy_engine.py
  ├── services/spot_price.py        # 现货价格
  ├── services/risk_framework.py    # 风控底线
  ├── services/dvol_analyzer.py     # DVOL适配参数
  ├── services/shared_calculations.py  # BS定价、Greeks、norm_cdf
  └── constants.py                  # 默认值
```

### 8.3 测试策略

- 单元测试：ContractFilter、StrategyScorer 独立测试
- 集成测试：StrategyEngine 端到端测试，mock 扫描数据
- 回归测试：确保默认参数能刷出至少5个合约（覆盖6个根因场景）

## 9. 迁移计划

1. 创建 `services/strategy_engine.py`，实现三个类
2. 创建 `api/strategy.py` 新端点（保留旧端点）
3. 前端重写策略页面
4. 验证默认参数能刷出结果
5. 标记旧端点为 deprecated
6. 后续版本删除旧文件

---

*此文档由 brainstorming 流程生成，经用户逐节确认通过。*
