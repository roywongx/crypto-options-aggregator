# IV Smile 重新设计

**日期:** 2026-05-04
**状态:** 已批准

## 问题

当前 IV Smile 功能存在以下问题：
- 纯 HTML/CSS 柱状图，视觉粗糙，与 dashboard 其他 Chart.js 图表风格不统一
- 只展示原始 IV 数据，没有 skew 指标、形态分类、情绪判断
- 没有交易策略建议，用户无法从中获得可操作的信息
- 分析逻辑全部内嵌在 `charts.py` router 中，无服务层，不可复用
- 与 `/api/charts/vol-surface` 端点存在数据提取和 IV 标准化的重复逻辑

## 目标

1. 用 Chart.js 折线图替代 HTML 柱状图，与 dashboard 其他图表风格统一
2. 新增 skew 指标计算（25-delta skew、put/call skew 百分比、skew slope、curvature）
3. 自动分类微笑形态（smile / put_skew / call_skew / flat）
4. 基于形态和 IV 水平给出市场情绪判断
5. 输出可操作的交易策略建议
6. 将分析逻辑提取到独立服务层 `services/iv_smile.py`

## 架构

### 方案：独立 IV Smile 服务层

```
index.html  →  app.js (loadIVSmile)  →  GET /api/charts/iv-smile
                                              │
                                     charts.py (router)
                                              │
                                     IVSmileAnalyzer (services/iv_smile.py)
                                              │
                                     spot_price + DB contracts_data
```

### 新增文件

- `dashboard/services/iv_smile.py` — IVSmileAnalyzer 服务类

### 修改文件

- `dashboard/routers/charts.py` — `/api/charts/iv-smile` 端点改为调用 IVSmileAnalyzer
- `dashboard/static/app.js` — `loadIVSmile()` 改用 Chart.js 折线图 + 分析面板渲染
- `dashboard/static/index.html` — IV Smile 区域增加分析面板容器

---

## 服务层设计：`services/iv_smile.py`

### 类：`IVSmileAnalyzer`

```python
class IVSmileAnalyzer:
    @classmethod
    def analyze(cls, contracts_data: list, spot: float, currency: str = "BTC") -> dict:
        """
        完整 IV Smile 分析

        Args:
            contracts_data: 合约数据数组（来自 scan_records.contracts_data）
            spot: 当前现货价格
            currency: 币种

        Returns:
            {
                "smiles": { "dte_7": {...}, "dte_14": {...}, ... },
                "analysis": { ... } 或 None（数据不足时）
            }
        """
```

### 处理流程

#### 1. 数据提取与标准化

从 contracts_data 提取每个合约的：
- `strike` — 必须 > 0
- `iv` — 优先 `mark_iv`，其次 `iv`；若 < 1.0 则 × 100 转百分比；> 200 跳过
- `dte` — 必须 > 0
- `option_type` — 首字母大写：P 或 C
- `oi` — 优先 `open_interest`，必须 >= 1
- `volume` — 默认 0

#### 2. 按到期日分组

- 按 `dte` 整数分组
- 取最近 3-4 个到期日
- 每个到期日内按 strike 排序

#### 3. 指标计算（每个到期日）

| 指标 | 计算方法 | 含义 |
|------|----------|------|
| `atm_iv` | 最接近 spot 的 strike 的 IV | ATM 隐含波动率 |
| `skew_25d` | OTM Put ~25Δ IV − OTM Call ~25Δ IV | 25-delta 偏度，正值=下行恐慌 |
| `put_skew_pct` | (OTM Put 平均 IV − ATM IV) / ATM IV × 100 | Put 端偏度百分比 |
| `call_skew_pct` | (OTM Call 平均 IV − ATM IV) / ATM IV × 100 | Call 端偏度百分比 |
| `skew_slope` | IV 对 moneyness 的线性回归斜率 | 偏斜陡峭程度 |
| `curvature` | (两端 IV 均值 − 中间 IV) / 中间 IV × 100 | 微笑曲率 |

**25Δ 近似方法：** 由于数据库中没有 delta 字段，用 moneyness 近似：
- OTM Put 25Δ ≈ strike 在 spot 的 90-95% 范围内（约 -10% to -5% moneyness）
- OTM Call 25Δ ≈ strike 在 spot 的 105-110% 范围内

#### 4. 形态分类

| 形态 | 条件 | 含义 |
|------|------|------|
| `smile` | put_skew_pct > 5% 且 call_skew_pct > 5% | 两端高、中间低，市场不确定性高 |
| `put_skew` | put_skew_pct > 5% 且 call_skew_pct <= 5% | 左高右低，典型下行恐慌 |
| `call_skew` | call_skew_pct > 5% 且 put_skew_pct <= 5% | 右高左低，牛市狂热或 gamma squeeze |
| `flat` | abs(put_skew_pct) <= 5% 且 abs(call_skew_pct) <= 5% | 各 strike IV 接近，市场平静 |

#### 5. 市场情绪判断

基于综合 skew 指标（跨到期日加权平均）：

| 情绪 | 条件 |
|------|------|
| `PANIC` | skew_25d > 15 或 put_skew_pct > 30% |
| `FEAR` | skew_25d > 8 或 put_skew_pct > 15% |
| `CAUTIOUS` | skew_25d > 3 或 put_skew_pct > 5% |
| `NEUTRAL` | abs(skew_25d) <= 3 |
| `GREED` | skew_25d < -3 或 call_skew_pct > 5% |
| `EUPHORIA` | skew_25d < -8 或 call_skew_pct > 15% |

#### 6. 策略建议

基于形态 + IV 水平 + 情绪组合：

| 条件 | 建议 | confidence |
|------|------|------------|
| put_skew + 高 IV (atm > 40) + FEAR/PANIC | 卖 OTM Put，收取恐慌溢价 | HIGH |
| put_skew + 中等 IV | 卖 Put Spread，限制风险 | MEDIUM |
| call_skew + 高 IV | 卖 OTM Call | HIGH |
| flat + 高 IV | 卖铁鹰 / 铁蝶 | HIGH |
| flat + 低 IV (atm < 25) | 买跨式 / 宽跨式，赌波动 | MEDIUM |
| smile + 高 IV | 卖 Strangle（两端收租） | MEDIUM |
| 任何形态 + 极端 skew (>20%) | Risk Reversal（卖高IV端，买低IV端） | HIGH |

---

## API 端点设计

### `GET /api/charts/iv-smile?currency=BTC`

**成功响应：**

```json
{
  "currency": "BTC",
  "spot": 103500,
  "smiles": {
    "dte_7": {
      "dte": 7,
      "puts": [{"strike": 95000, "iv": 45.32, "type": "P", "oi": 1200, "volume": 50, "moneyness": -8.21}],
      "calls": [{"strike": 105000, "iv": 38.10, "type": "C", "oi": 800, "volume": 30, "moneyness": 1.45}],
      "all": [...]
    },
    "dte_14": {...},
    "dte_30": {...}
  },
  "analysis": {
    "form": "put_skew",
    "form_label": "下行恐慌型",
    "form_icon": "📉",
    "sentiment": {
      "state": "FEAR",
      "label": "市场恐慌",
      "icon": "😰",
      "color": "#ef4444"
    },
    "metrics": {
      "atm_iv": 42.5,
      "skew_25d": 8.3,
      "put_skew_pct": 19.6,
      "call_skew_pct": -3.2,
      "skew_slope": 0.15,
      "curvature": 2.1
    },
    "by_expiry": [
      {"dte": 7, "atm_iv": 45.2, "skew_25d": 10.1, "form": "put_skew", "point_count": 24},
      {"dte": 14, "atm_iv": 42.0, "skew_25d": 7.5, "form": "put_skew", "point_count": 30},
      {"dte": 30, "atm_iv": 39.8, "skew_25d": 5.2, "form": "put_skew", "point_count": 36}
    ],
    "recommendations": [
      {
        "type": "sell_put",
        "title": "卖 OTM Put",
        "body": "下行 IV 显著偏高 (19.6%)，卖出 95000 Put 可收取超额恐慌溢价",
        "action": "Delta 0.15-0.25，DTE 7-14",
        "confidence": "HIGH"
      },
      {
        "type": "iron_condor",
        "title": "铁鹰策略",
        "body": "微笑曲度适中，可同时卖出虚值 Put 和 Call",
        "action": "Put Delta 0.15 / Call Delta 0.10",
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
  "smiles": {},
  "analysis": null,
  "error": "数据不足，至少需要 2 个到期日的合约数据"
}
```

---

## 前端设计

### 图表：Chart.js 折线图

- **X 轴：** Strike 价格（带千分位格式化）
- **Y 轴：** IV (%)，带 % 后缀
- **数据系列：** 每个到期日一条线
  - 颜色方案：近到期=实线暖色，远到期=虚线冷色
  - 7D: `#ef4444` (红) 实线
  - 14D: `#f59e0b` (黄) 实线
  - 30D: `#3b82f6` (蓝) 虚线
- **ATM 标记：** 垂直虚线标记 spot 价格位置
- **交互：** hover 显示 Strike、IV%、Type、OI、Moneyness

### 分析面板（图表下方）

```
┌─────────────────────────────────────────────────────────┐
│  📉 下行恐慌型    │    😰 市场恐慌    │    ATM IV: 42.5%  │
├─────────────────────────────────────────────────────────┤
│  25Δ Skew: +8.3   │  Put偏度: +19.6%  │  曲度: 2.1        │
├─────────────────────────────────────────────────────────┤
│  📊 到期日对比:                                          │
│  7D   IV=45.2%   Skew=+10.1   形态=恐慌型               │
│  14D  IV=42.0%   Skew=+7.5    形态=恐慌型               │
│  30D  IV=39.8%   Skew=+5.2    形态=轻微偏斜              │
├─────────────────────────────────────────────────────────┤
│  💡 策略建议:                                            │
│  [HIGH] 卖 OTM Put — 下行 IV 偏高 19.6%，...            │
│  [MED]  铁鹰策略 — 微笑曲度适中，...                      │
└─────────────────────────────────────────────────────────┘
```

**样式：**
- 分析面板用 `card-glass` 风格
- 形态标签和情绪标签带颜色和图标
- 到期日对比表用紧凑表格
- 策略建议卡片带 confidence 色条（HIGH=绿，MEDIUM=黄）
- `analysis` 为 null 时只显示图表，分析面板隐藏

---

## 实现顺序

1. 新建 `services/iv_smile.py` — IVSmileAnalyzer 服务
2. 改造 `routers/charts.py` — 端点调用 IVSmileAnalyzer
3. 改造 `static/app.js` — Chart.js 折线图 + 分析面板渲染
4. 调整 `static/index.html` — 分析面板容器
5. 测试验证
