# Greeks 风险矩阵重新设计

**日期:** 2026-05-04
**状态:** 已批准

## 问题

当前 Greeks 风险矩阵功能存在以下问题：
- 只展示汇总 Greeks（delta/gamma/theta/vega），无按 strike 或到期日的分布视图
- 无 GEX（Gamma Exposure）分析，无法识别 gamma flip 点和 pin risk
- 无市场状态解读，用户看到 raw numbers 但不知道意味着什么
- 无对冲建议，无法指导实际操作
- 分析逻辑全部内嵌在 `charts.py` router 中（~128 行），无服务层，不可复用
- 未利用已有的 `quant_engine.py` 高级 Greeks 引擎（Vanna、Charm）

## 目标

1. 新增 GEX 按 strike 分布图（Chart.js 柱状图），识别 gamma flip 点
2. 新增 Greeks 按到期日曲线图（Chart.js 折线图），观察 Greeks 期限结构
3. 新增 Pin Risk 分析，识别到期日附近的价格磁吸效应
4. 基于 Greeks 综合指标给出市场状态解读（趋势/震荡/恐慌/狂热）
5. 输出可操作的对冲建议
6. 将分析逻辑提取到独立服务层 `services/greeks_analyzer.py`

## 架构

### 方案：独立 Greeks Analyzer 服务层

```
index.html  →  app.js (loadGreeksSummary)  →  GET /api/charts/greeks-summary
                                                      │
                                             charts.py (router)
                                                      │
                                             GreeksAnalyzer (services/greeks_analyzer.py)
                                                      │
                                             spot_price + DB contracts_data
                                                      │
                                             shared_calculations.black_scholes_price()
```

### 新增文件

- `dashboard/services/greeks_analyzer.py` — GreeksAnalyzer 服务类

### 修改文件

- `dashboard/routers/charts.py` — `/api/charts/greeks-summary` 端点改为调用 GreeksAnalyzer
- `dashboard/static/app.js` — `loadGreeksSummary()` 改用 Chart.js 图表 + 分析面板渲染
- `dashboard/static/index.html` — Greeks 区域增加 GEX 图表、曲线图、分析面板容器

---

## 服务层设计：`services/greeks_analyzer.py`

### 类：`GreeksAnalyzer`

```python
class GreeksAnalyzer:
    @classmethod
    def analyze(cls, contracts_data: list, spot: float, currency: str = "BTC") -> dict:
        """
        完整 Greeks 风险矩阵分析

        Args:
            contracts_data: 合约数据数组（来自 scan_records.contracts_data）
            spot: 当前现货价格
            currency: 币种

        Returns:
            {
                "greeks_summary": { ... },
                "gex": { ... },
                "by_expiry": [ ... ],
                "scenarios": { ... },
                "analysis": { ... }
            }
        """
```

### 处理流程

#### 1. 数据提取与标准化

从 contracts_data 提取每个合约的：
- `strike` — 必须 > 0
- `dte` — 必须 > 0
- `iv` — 优先 `mark_iv`，其次 `iv`；若 < 1.0 则 × 100 转百分比；> 200 跳过
- `option_type` — 首字母大写：P 或 C
- `oi` — 优先 `open_interest`，必须 >= 1
- `premium` — 优先 `premium_usd`，其次 `premium`

#### 2. Greeks 计算

使用 `shared_calculations.black_scholes_price()` 计算每个合约的 Greeks：
- delta, gamma, theta, vega
- OI 加权：`weight = max(1.0, oi)`

#### 3. GEX（Gamma Exposure）按 Strike 聚合

| 指标 | 计算方法 | 含义 |
|------|----------|------|
| `call_gex` | 该 strike 所有 Call 的 gamma × OI × spot² × 0.01 | Call 端 Gamma 敞口 |
| `put_gex` | 该 strike 所有 Put 的 gamma × OI × spot² × 0.01 × (-1) | Put 端 Gamma 散口（负值） |
| `net_gex` | call_gex + put_gex | 净 Gamma 敞口 |
| `flip_strike` | net_gex 由负转正的 strike | Gamma Flip 点 |
| `pin_strike` | 附近 OI 最集中的 strike | Pin Risk 价格 |
| `pin_risk_level` | pin_strike 附近总 OI / 平均 OI | HIGH/MEDIUM/LOW |

**GEX 符号约定：**
- Call GEX 为正（做市商做多 gamma，需要对冲买入）
- Put GEX 为负（做市商做空 gamma，需要对冲卖出）
- Net GEX > 0：做市商净多 gamma → 市场趋于均值回归（低波动）
- Net GEX < 0：做市商净空 gamma → 市场趋于趋势延续（高波动）

#### 4. Greeks 按到期日汇总

每个到期日计算：
- 总 delta、gamma、theta、vega（OI 加权）
- ATM IV（最接近 spot 的 strike 的 IV）
- 合约数量、总 OI

#### 5. 情景分析

| 情景 | 计算方法 | 含义 |
|------|----------|------|
| `down_10pct` | total_delta × spot × (-0.1) | 价格下跌 10% 的 PnL |
| `up_10pct` | total_delta × spot × 0.1 | 价格上涨 10% 的 PnL |
| `iv_up_5pct` | total_vega × 5 | IV 上升 5 个百分点的 PnL |
| `iv_down_5pct` | total_vega × (-5) | IV 下降 5 个百分点的 PnL |
| `pin_scenario` | pin_strike 附近的 OI 集中度 | 到期日价格磁吸概率 |

#### 6. 风险评级

| Greek | 高风险阈值 | 中风险阈值 | 评级逻辑 |
|-------|-----------|-----------|----------|
| Delta | abs > 0.5 | abs > 0.2 | 方向性风险 |
| Gamma | abs > 0.01 | abs > 0.005 | 凸性风险（加速） |
| Theta | abs > 100 | abs > 50 | 时间衰减速度 |
| Vega | abs > 500 | abs > 200 | 波动率敏感度 |

#### 7. 市场状态判断

基于 GEX regime + Greeks 综合指标：

| 状态 | 条件 | 含义 |
|------|------|------|
| `TRENDING_UP` | net_gex < 0 且 total_delta > 0 | 做市商空 gamma + 市场多头 → 趋势加速上行 |
| `TRENDING_DOWN` | net_gex < 0 且 total_delta < 0 | 做市商空 gamma + 市场空头 → 趋势加速下行 |
| `MEAN_REVERTING` | net_gex > 0 且 abs(skew_25d) < 5 | 做市商多 gamma → 价格被拉回 |
| `PIN_RISK` | pin_risk_level == HIGH | 到期日附近价格被钉住 |
| `VOLATILE` | net_gex < 0 且 atm_iv > 40 | 双向大幅波动预期 |
| `CALM` | net_gex > 0 且 atm_iv < 25 | 低波动环境 |

#### 8. 对冲建议

基于市场状态 + Greeks 风险：

| 条件 | 建议 | confidence |
|------|------|------------|
| abs(delta) > 0.5 | 买入反向期权对冲方向风险 | HIGH |
| gamma > 0.01 且 net_gex < 0 | 卖出跨式收取 gamma 费用 | MEDIUM |
| theta > 100（日衰减大） | 考虑卖出期权收取时间价值 | MEDIUM |
| vega > 500 且预期 IV 下降 | 卖出宽跨式做空波动率 | HIGH |
| pin_risk == HIGH | 到期前减仓或移仓到下一到期日 | HIGH |
| TRENDING 状态 | 顺势加仓或买入期权跟趋势 | MEDIUM |
| MEAN_REVERTING 状态 | 卖出宽跨式收取均值回归收益 | MEDIUM |

---

## API 端点设计

### `GET /api/charts/greeks-summary?currency=BTC`

**成功响应：**

```json
{
  "currency": "BTC",
  "spot": 103500,
  "contract_count": 156,
  "put_count": 78,
  "call_count": 78,
  "total_oi": 125000,
  "greeks_summary": {
    "per_contract": {
      "delta": 0.0234,
      "gamma": 0.000156,
      "theta": -45.67,
      "vega": 234.56
    },
    "total_exposure": {
      "delta": 2925.0,
      "gamma": 19.5,
      "theta": -5708.75,
      "vega": 29320.0
    }
  },
  "gex": {
    "by_strike": [
      {"strike": 95000, "call_gex": 1200000, "put_gex": -800000, "net_gex": 400000},
      {"strike": 100000, "call_gex": 2500000, "put_gex": -1800000, "net_gex": 700000},
      {"strike": 105000, "call_gex": 1800000, "put_gex": -2200000, "net_gex": -400000}
    ],
    "total_gex": 1500000,
    "flip_strike": 102000,
    "pin_strike": 103000,
    "pin_risk_level": "HIGH"
  },
  "by_expiry": [
    {
      "dte": 7,
      "delta": 1200.5,
      "gamma": 12.3,
      "theta": -3500.0,
      "vega": 8500.0,
      "atm_iv": 45.2,
      "contract_count": 42,
      "total_oi": 35000
    },
    {
      "dte": 14,
      "delta": 950.0,
      "gamma": 5.1,
      "theta": -1500.0,
      "vega": 12000.0,
      "atm_iv": 42.0,
      "contract_count": 48,
      "total_oi": 42000
    }
  ],
  "scenarios": {
    "down_10pct": -3037500,
    "up_10pct": 3037500,
    "iv_up_5pct": 146600,
    "iv_down_5pct": -146600,
    "pin_scenario": {
      "pin_strike": 103000,
      "pin_oi": 18500,
      "avg_oi": 800,
      "concentration": 23.1
    }
  },
  "analysis": {
    "gex_regime": {
      "state": "POSITIVE",
      "label": "正 Gamma",
      "icon": "🛡️",
      "description": "做市商净多 gamma，价格趋于均值回归"
    },
    "pin_risk": {
      "level": "HIGH",
      "label": "高 Pin Risk",
      "icon": "📌",
      "description": "103000 strike OI 集中度 23.1x，到期日价格可能被钉住"
    },
    "market_state": {
      "state": "MEAN_REVERTING",
      "label": "均值回归",
      "icon": "🔄",
      "color": "#3b82f6"
    },
    "risk_ratings": {
      "delta": {"level": "LOW", "label": "🟢 低", "value": 0.0234},
      "gamma": {"level": "LOW", "label": "🟢 低", "value": 0.000156},
      "theta": {"level": "MEDIUM", "label": "🟡 中", "value": -45.67},
      "vega": {"level": "HIGH", "label": "🔴 高", "value": 234.56}
    },
    "interpretation": [
      "GEX 为正，做市商处于多 gamma 位置，市场倾向于均值回归",
      "Pin Risk 高，103000 附近 OI 集中，到期前价格可能被吸附",
      "Vega 敞口大，IV 变动会显著影响持仓价值"
    ],
    "hedge_suggestions": [
      {
        "type": "reduce_position",
        "title": "到期前减仓",
        "body": "Pin Risk 高，建议在到期前 2-3 天减仓或移仓到下一到期日",
        "action": "将 7D 仓位移至 14D 或更远到期日",
        "confidence": "HIGH"
      },
      {
        "type": "sell_straddle",
        "title": "卖出跨式",
        "body": "正 Gamma 环境适合卖出跨式收取时间价值",
        "action": "在 103000 strike 卖出跨式，Delta 中性",
        "confidence": "MEDIUM"
      }
    ]
  }
}
```

**错误/数据不足响应：**

```json
{
  "currency": "BTC",
  "spot": 103500,
  "greeks_summary": {},
  "gex": {},
  "by_expiry": [],
  "scenarios": {},
  "analysis": null,
  "error": "数据不足，至少需要 2 个到期日的合约数据"
}
```

---

## 前端设计

### 状态栏（4 个指标卡）

```
┌──────────────┬──────────────┬──────────────┬──────────────┐
│  🛡️ 正 Gamma  │  📌 高 Pin Risk │  🔄 均值回归   │  θ -45.67/天  │
│  GEX Regime   │  Pin Risk     │  Market State │  Theta/Day    │
└──────────────┴──────────────┴──────────────┴──────────────┘
```

- 每个卡片带图标、状态标签、颜色
- GEX Regime: 正=蓝，负=红
- Pin Risk: HIGH=红，MEDIUM=黄，LOW=绿
- Market State: 根据状态着色
- Theta: 负值红色（衰减），正值绿色

### GEX 柱状图（Chart.js）

- **X 轴：** Strike 价格
- **Y 轴：** GEX 值
- **数据系列：**
  - Call GEX：绿色柱
  - Put GEX：红色柱
  - Net GEX：蓝色折线叠加
- **标记线：**
  - Spot 价格垂直虚线
  - Gamma Flip 水平虚线
  - Pin Strike 标记

### Greeks 到期日曲线（Chart.js 折线图）

- **X 轴：** DTE（到期天数）
- **Y 轴：** Greeks 值（双 Y 轴：左=Delta/Gamma，右=Theta/Vega）
- **数据系列：**
  - Delta：蓝色折线
  - Gamma：黄色折线
  - Theta：红色折线
  - Vega：绿色折线
- **交互：** hover 显示具体数值

### Greeks 概览网格

```
┌────────────┬────────────┬────────────┬────────────┐
│ Delta      │ Gamma      │ Theta      │ Vega       │
│ 0.0234     │ 0.000156   │ -45.67     │ 234.56     │
│ 🟢 低风险   │ 🟢 低风险   │ 🟡 中风险   │ 🔴 高风险   │
└────────────┴────────────┴────────────┴────────────┘
```

### 情景分析

```
┌─────────────────────────────────────────────────────────┐
│  📊 情景分析:                                            │
│  价格 -10%: -$3,037,500   价格 +10%: +$3,037,500        │
│  IV +5%: +$146,600        IV -5%: -$146,600             │
│  Pin 情景: 103000 strike OI 集中度 23.1x                 │
└─────────────────────────────────────────────────────────┘
```

### 对冲建议面板

```
┌─────────────────────────────────────────────────────────┐
│  💡 对冲建议:                                            │
│  [HIGH] 到期前减仓 — Pin Risk 高，...                     │
│  [MED]  卖出跨式 — 正 Gamma 环境适合...                   │
└─────────────────────────────────────────────────────────┘
```

**样式：**
- 分析面板用 `card-glass` 风格
- 对冲建议卡片带 confidence 色条（HIGH=绿，MEDIUM=黄）
- `analysis` 为 null 时只显示基础 Greeks，分析面板隐藏

---

## 实现顺序

1. 新建 `services/greeks_analyzer.py` — GreeksAnalyzer 服务
2. 改造 `routers/charts.py` — 端点调用 GreeksAnalyzer
3. 改造 `static/app.js` — Chart.js 图表 + 分析面板渲染
4. 调整 `static/index.html` — 新增图表和分析面板容器
5. 测试验证
