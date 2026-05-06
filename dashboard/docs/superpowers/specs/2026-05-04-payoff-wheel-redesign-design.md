# Payoff 可视化 & Wheel ROI 重构设计

## 概述

将现有的 Payoff 计算器和 Wheel ROI 模块合并为统一的"策略分析中心"，与策略推荐引擎深度联动。采用全量重写方案。

**目标：** 提供专业的期权收益分析、多周期 Wheel 模拟，以及与推荐引擎的无缝衔接。

**架构：** 后端新建 `services/strategy_analytics.py`（PayoffEngine + WheelSimulator），前端合并为四 Tab 的策略分析中心，API 三条新端点。

---

## 1. 后端架构

### 1.1 新文件：`services/strategy_analytics.py`

**PayoffEngine 类：**

```python
class PayoffEngine:
    def calc_single(self, spot, strike, premium, option_type, dte, quantity=1):
        """单腿 payoff 计算"""
        # 返回: { breakeven, max_profit, max_loss, profit_at_spot, payoff_curve[], zones }

    def calc_multi_legs(self, spot, legs):
        """组合策略 payoff（跨式、宽跨式、价差等）"""
        # legs: [{strike, premium, option_type, quantity, side}]
        # 返回: 合并后的 payoff 曲线 + 组合指标

    def calc_probability_overlay(self, spot, dte, iv, strikes):
        """概率叠加层 — 正态分布假设下的到期价格分布"""
        # 返回: 每个价格区间的概率密度

    def calc_time_decay(self, spot, strike, premium, option_type, iv, dte_max=60):
        """时间衰减曲线 — 多个时间点的理论价值"""
        # 返回: [{dte, value}, ...] 用于 Theta 可视化

    def estimate_premium(self, spot, strike, dte, iv, option_type):
        """Black-Scholes 估算权利金"""
        # 用于前端"快速估算"场景

    def score_strategy(self, spot, strike, premium, option_type, dte, delta=None):
        """策略评分 — 与 StrategyScorer 对齐"""
        # 返回: {total, ev, apr, liquidity, theta, recommendation}
```

**WheelSimulator 类：**

```python
class WheelSimulator:
    def simulate(self, spot, strike, premium, option_type, cycles, capital,
                 assigned_pct=0.5, iv=0.6, drift=0.0, simulations=1000):
        """蒙特卡洛 Wheel 模拟"""
        # 每个 cycle: sell put → (可能被行权) → sell call → (可能被行权)
        # 价格路径: 几何布朗运动 dS = μ·S·dt + σ·S·dW
        # 返回: { mean_roi, median_roi, p10, p25, p75, p90,
        #          mean_cycles, win_rate, drawdown_stats,
        #          roi_distribution[], sample_paths[5] }
```

**GBM 价格路径生成：**

```python
def _generate_price_paths(self, spot, cycles, iv, drift, simulations, dt=30/365):
    """生成多条几何布朗运动价格路径"""
    # 每步 = 30 天（一个 cycle）
    # S(t+dt) = S(t) * exp((μ - σ²/2)·dt + σ·√dt·Z)
```

---

## 2. API 设计

### 2.1 新端点

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/analytics/payoff` | POST | Payoff 计算（单腿/组合/概率/时间衰减） |
| `/api/analytics/wheel` | POST | Wheel 蒙特卡洛模拟 |
| `/api/analytics/estimate` | POST | 快速权利金估算 |

### 2.2 请求/响应格式

**POST /api/analytics/payoff**
```json
{
  "mode": "single|multi|probability|time_decay",
  "spot": 100000,
  "legs": [
    {"strike": 95000, "premium": 2000, "option_type": "PUT", "quantity": 1, "side": "sell"}
  ],
  "dte": 30,
  "iv": 0.6
}
```

响应（single 模式）：
```json
{
  "success": true,
  "mode": "single",
  "breakeven": 93000,
  "max_profit": 2000,
  "max_loss": 93000,
  "profit_at_spot": 2000,
  "payoff_curve": [[90000, -3000], [95000, 2000], ...],
  "zones": {"profit_range": [93000, 999999], "loss_range": [0, 93000]},
  "score": {"total": 0.68, "recommendation": "GOOD", ...}
}
```

**POST /api/analytics/wheel**
```json
{
  "spot": 100000,
  "strike": 95000,
  "premium": 2000,
  "option_type": "PUT",
  "cycles": 6,
  "capital": 100000,
  "assigned_pct": 0.5,
  "iv": 0.6,
  "simulations": 1000
}
```

响应：
```json
{
  "success": true,
  "summary": {
    "mean_roi": 0.18,
    "median_roi": 0.16,
    "p10": 0.02,
    "p90": 0.35,
    "mean_cycles": 5.2,
    "win_rate": 0.82,
    "max_drawdown_mean": -0.12
  },
  "roi_distribution": [[-0.1, 5], [0.0, 12], ...],
  "sample_paths": [[[100000, 98000, 102000, ...], ...], ...],
  "score": {"total": 0.72, "recommendation": "GOOD"}
}
```

**POST /api/analytics/estimate**
```json
{
  "spot": 100000,
  "strike": 95000,
  "dte": 30,
  "iv": 0.6,
  "option_type": "PUT"
}
```

响应：
```json
{
  "success": true,
  "premium": 1850,
  "delta": -0.28,
  "gamma": 0.00003,
  "theta": -62,
  "vega": 180
}
```

---

## 3. 前端设计

### 3.1 布局：策略分析中心

合并现有 payoffSection 为统一的"策略分析中心"，包含 4 个 Tab：

- **[单腿 Payoff]** — 行权价、权利金、方向、DTE 输入 → 收益曲线 + 指标卡
- **[组合 Payoff]** — 多腿编辑器（添加/删除 legs）→ 组合曲线 + 盈亏分析
- **[Wheel 模拟]** — 参数面板 + 模拟按钮 → ROI 分布直方图 + 统计摘要 + 样本路径
- **[策略对比]** — 选择 2-3 个策略 → 并排指标对比表

### 3.2 图表

- **Payoff 图：** Chart.js 折线图，X 轴 = 到期价格，Y 轴 = 盈亏。零线标注、盈亏区间着色（绿/红）
- **概率叠加：** 半透明面积图叠加在 payoff 图上，显示到期价格概率分布
- **时间衰减：** 多条曲线（DTE=60/30/15/7）展示期权价值随标的变化
- **Wheel ROI 分布：** 直方图 + 累积分布线，标注均值/中位数/分位数
- **Wheel 样本路径：** 5 条随机价格路径折线图

### 3.3 与推荐引擎联动

- 推荐结果每行增加"分析"按钮
- 点击后滚动到策略分析中心，自动填入参数（strike、premium、dte、option_type）
- URL 参数传递：`#analysis?type=payoff&strike=95000&premium=2000&dte=30&option_type=PUT`

### 3.4 指标卡（Payoff 结果区）

```
┌──────────┬──────────┬──────────┬──────────┐
│ 最大盈利  │ 最大亏损  │ 盈亏平衡  │ 策略评分  │
│ $2,000   │ $93,000  │ $93,000  │ GOOD     │
└──────────┴──────────┴──────────┴──────────┘
```

### 3.5 Wheel 统计摘要卡

```
┌──────────┬──────────┬──────────┬──────────┬──────────┐
│ 平均 ROI  │ 中位 ROI  │ 胜率     │ P10/P90  │ 最大回撤  │
│ 18%      │ 16%      │ 82%      │ 2%/35%   │ -12%     │
└──────────┴──────────┴──────────┴──────────┴──────────┘
```

---

## 4. 数据流

```
推荐结果 [分析按钮]
    ↓ 滚动 + URL参数
策略分析中心 [自动填充]
    ↓ 用户确认/调整参数
POST /api/analytics/payoff 或 /wheel
    ↓
PayoffEngine / WheelSimulator 计算
    ↓
返回结果 → Chart.js 渲染 + 指标卡
```

---

## 5. 错误处理

- Spot price 获取失败 → 显示错误提示，不渲染图表
- 参数校验（strike > 0, dte > 0, iv > 0）→ 前端即时校验 + 后端 Pydantic 验证
- 蒙特卡洛模拟超时 → simulations 上限 5000，超时返回已计算的部分结果
- 空 legs 列表 → 返回错误提示"请至少添加一条腿"

---

## 6. 测试策略

### 后端测试
- `test_payoff_engine.py`：单腿 payoff 边界测试、组合 legs 计算、BS 定价精度、概率分布归一化
- `test_wheel_simulator.py`：ROI 范围合理性、样本路径数正确性、极端参数处理

### 前端测试
- Tab 切换功能
- 推荐→分析联动（参数传递）
- 图表渲染（无 JS 报错）

---

## 7. 移除旧代码

重构完成后移除：
- `dashboard/services/payoff_calculator.py`（被 `strategy_analytics.py` 替代）
- `dashboard/api/payoff.py`（被新 `/api/analytics/*` 端点替代）
- 旧的 payoffSection HTML 和相关 JS 函数

保留兼容：
- 旧的 `/api/payoff/*` 端点暂时保留，返回 301 重定向到新端点
- 下个版本再彻底移除
