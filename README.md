<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Platform-Binance%20%2B%20Deribit-orange?logo=bitcoin" alt="Platform">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/v5.5-APR%E7%A8%B3%E5%81%A5+%E6%B5%81%E5%90%918%E7%B1%BB+MaxPain-blueviolet" alt="Version">
</p>

<h1 align="center">Crypto Options Aggregator</h1>

<p align="center">
  <b>双平台加密期权扫描器 + 实时监控面板</b><br>
  Binance (USDT本位) × Deribit (币本位) 联合分析<br>
  面向 Sell Put / Covered Call 策略的专业工具
</p>

---

## ✨ 核心亮点

| 能力 | 说明 |
|------|------|
| 🔄 **双平台聚合** | 同时扫描 Binance + Deribit，统一排序对比 |
| 📊 **Margin-APR** | 基于保证金占用计算真实年化收益率（默认20%） |
| ⚡ **DVOL 深度分析** | Z-Score、趋势方向(↑↓→)、置信度(高/中/低)、7d分位、动态阈值 |
| 🔍 **大宗异动监控** | 机构流向标签（8类完整分类含未知流向）+ 三级严重程度(大单⚠️/中单🟡/普通✅) |
| 💥 **压力测试** | Delta-Gamma 近似公式，估算 -10% 跌幅亏损 |
| 🛡️ **风险预警** | 自动检测高风险合约，滚仓建议弹窗 |
| 🧮 **倍投修复计算器** | 输入浮亏金额，自动推荐最优修复方案 |
| 📈 **趋势图表** | APR(P75稳健上限+均值) / DVOL 历史 24H / 7天 / 30天 可视化（异常值自动过滤） |
| 🎯 **加权评分排序** | APR(25%) + POP(25%) + 安全垫(20%) + 流动性(15%) + IV中性(15%) 五维综合评分 |
| 📉 **IV期限结构** | 7D/14D/30D/60D/90D 隐含波动率曲线 + Backwardation倒挂检测 |
| 💔 **最大痛点/ Gamma Flip** | Max Pain计算 + GEX(Gamma Exposure)分布图 + Flip点风险预警 |
| ✅ **数据校验** | 自动检测异常字段（IV/OI/Delta），输出校验警告 |

---

## 🖥️ Web 监控面板

基于 FastAPI + 原生 JS 构建，开箱即用。

### 启动方式

```bash
# 安装依赖
pip install -r requirements.txt
pip install -r dashboard/requirements.txt

# 启动面板
cd dashboard && python main.py
# 访问 http://localhost:8080
```

### 功能全景

#### 📊 实时监控大屏
- **宏观指标卡片**：现货价格（动态获取）/ DVOL指数+信号 / 大宗交易数(1h) / 最佳APR
- **合约列表**：21列数据，支持点击表头排序
  - 平台 | 合约 | DTE | Strike | Delta | Gamma | Vega | **IV** | **APR**
  - **POP**(胜率) | **Premium$** | 流动性 | **-10%亏损** | **BE$** | **安全垫%** | **OI** | Spread%
  - **IV Rank** | **⭐评分(_score)** | 风险状态(emoji)
- **自动刷新**：5/10/30 分钟可选，后台静默监控

#### 🎯 加权评分系统 (v5.3)

五维综合评分，帮助快速识别最佳 Sell Put/Call 机会：

| 维度 | 权重 | 说明 |
|------|------|------|
| APR | 25% | 年化收益率（归一化到200%上限）|
| POP | 25% | 概率获利 ≈ 1-\|delta\|（OTM sold options）|
| 安全垫距离% | 20% | (spot-breakeven)/spot（归一化到20%上限）|
| 流动性 | 15% | 综合OI和Spread的流动性评分 |
| IV Rank中性 | 15% | 当前IV在历史分布中的位置，越接近50越好 |

#### 🔥 倍投修复计算器
输入浮亏金额 → 系统自动筛选高 APR 合约 → 计算所需张数和预期净利润 → 推荐最优方案

#### ⚠️ 三级风险预警系统
- **Delta > 0.45** → 高风险红框闪烁 + 浏览器通知
- **价格接近Strike 2%** → 中风险橙色标记
- **点击合约行** → 弹出滚仓建议（推荐更低行权价远期替代）

#### 🐋 大宗异动面板
- 近1小时大单实时展示（标题 + 严重程度badge + 方向箭头 + flow emoji）
  - 🔴 **大单⚠️**: notional ≥ $2M
  - 🟡 **中单🟡**: notional ≥ $500K
  - 🟢 **普通✅**: notional < $500K
- **情绪总览卡**：五档情绪评分 (🐂偏多 / 📈温和看多 / ➡️中性 / 📉温和看空 / 🐻偏空)
  - 自然语言总结（支撑/阻力位 + 主流行为判断）
  - 买卖比例 / 总名义金额 / 主流行为类型
- **净头寸 Strike 分布图**：每个行权价的净头寸(buy-sell)横向柱状图
  - 现价锚点标记 + 支撑位(黄)/阻力位(橙)自动识别 + 距现价百分比
- **流向分类网格**：**8类 flow_label 完整展示**（含count=0的类型）+ 占比进度条
  - 保护性对冲 / 收权利金 / 看跌投机 / 看涨投机 / 追涨建仓 / 备兑开仓 / 改仓操作 / **未知流向**
- 每条大单附带交易建议（如："机构护冲 ↓ 短期谨慎"、"收取权利金 ↑ 值好环境"）
- 支持按币种(BTC/ETH/SOL)和时间范围(7/30/90天)筛选

#### 📉 DVOL 分析面板
- 当前值 + Z-Score + 信号等级（异常偏高/偏高/正常/偏低/异常偏低）
- **趋势箭头**: ↑上升 / ↓下降 / →震荡
- **置信度**: 高(>70) / 中(40-70) / 低(<40)
- 趋势图表：24H / 7天 / 30天 切换

#### 📈 APR 趋势图表 (v5.4.5)
- **P75 APR(稳健上限)**: 第75百分位数线，过滤极端异常值
- **平均 APR**: 全量合约均值
- **异常值过滤**: 自动排除 APR<1% 或 >500% 的失真数据（Deribit API偶尔返回IV异常值）
- **前值填充**: 数据缺失时使用前一有效值填补，避免图表断裂

#### 📉 IV 期限结构面板
- **5个到期节点**: 7D / 14D / 30D / 60D / 90D 的平均隐含波动率
- **IV曲线图**: Chart.js 渲染的期限结构折线图
- **Backwardation检测**: 近期IV > 远期IV时显示倒挂警告
- **颜色编码**: ≤14D红色(短期高风险), >14D青色

#### 💔 最大痛点 / Gamma Flip (Max Pain & GEX)
- **现货价 vs 最大痛点**: 直观对比 + 距离百分比
- **PCR (Put/Call Ratio)**: 多空持仓比
- **情绪信号**: 中性/看多/看空
- **Pain Curve + GEX 双轴图**:
  - 归一化痛点曲线（橙色线）— 标记最大痛点位置
  - OI净敞口柱状图（绿红双色）— 正=Call主导, 负=Put主导
- **Gamma Flip 预警**: 当现货进入空头Gamma区时显示危险警告
- **多到期日支持**: 展示最近4个到期日的Max Pain和GEX

#### 📊 统计信息
- 总扫描次数 / 今日扫描数 / 大宗交易总数 / 数据库大小

---

## 🛠️ API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/scan` | 执行期权扫描（异步非阻塞）|
| `GET` | `/api/latest?currency=BTC` | 获取最新扫描结果 |
| `GET` | `/api/stats` | 统计概览 |
| `GET` | `/api/health` | **健康检查**（DB WAL模式 + 各API可达性）|
| `POST` | `/api/recovery-calculate` | 倍投修复计算 |
| `GET` | `/api/charts/apr?hours=168` | APR 趋势数据（P75/P90/avg，异常值已过滤）|
| `GET` | `/api/charts/dvol?hours=168` | DVOL 趋势数据 |
| `GET` | `/api/charts/vol-surface?currency=BTC` | **IV期限结构**（term_structure + surface + backwardation）|
| `GET` | `/api/metrics/max-pain?currency=BTC` | **最大痛点/GEX**（pain_curve + gex_curve + flip_point）|
| `GET` | `/api/trades/history?days=7` | 大宗交易历史（支持方向/来源/行权价筛选）|
| `GET` | `/api/trades/strike-distribution?days=30` | Strike 分布数据 |
| `GET` | `/api/trades/wind-analysis?currency=BTC&days=30` | **风向分析 v2**（情绪+Strike分布+8类流向完整展示）|

### 扫描参数 (POST /api/scan)

```json
{
  "currency": "BTC",
  "min_dte": 7,
  "max_dte": 90,
  "max_delta": 0.4,
  "margin_ratio": 0.2,
  "option_type": "PUT",
  "strike": null,
  "strike_range": null
}
```

### 返回数据结构（关键字段）

> ⚠️ **重要**: 所有数值字段已过数据校验层过滤。`mark_iv` 和前端显示的IV统一为**百分比格式**（58.8 表示 58.8%）。

```json
{
  "spot_price": 68342,
  "dvol_current": 56.8,
  "dvol_z_score": 0.35,
  "dvol_signal": "正常区间",
  "dvol_raw": {
    "current_dvol": 56.8,
    "z_score_7d": 0.35,
    "trend": "↑",
    "confidence": "高",
    "data_points": 168,
    "percentile_7d": 28.1
  },
  "large_trades_count": 20,
  "large_trades_details": [{
    "type", "severity": "high|medium|info",
    "title", "message", "flow_label", "emoji"
  }],
  "contracts": [{
    "platform": "Deribit",
    "symbol": "BTC-24APR26-65000-P",
    "strike": 65000, "dte": 17,
    "delta": -0.3505, "gamma": 0.00005, "vega": 56.37,
    "mark_iv": 58.8,
    "apr": 210.5,
    "pop": 65.0,
    "premium_usd": 1063.91,
    "breakeven": 63160.66,
    "breakeven_pct": 7.6,
    "open_interest": 2639.1,
    "spread_pct": 1.82,
    "liquidity_score": 95,
    "loss_at_10pct": 3496.35,
    "iv_rank": 48.2,
    "_score": 0.7234
  }],
  "validation_warnings": []
}
```

### 风向分析响应 (/api/trades/wind-analysis)

```json
{
  "summary": {
    "total_trades": 5198,
    "total_notional": 6742138905,
    "buy_ratio": 0.53,
    "sell_ratio": 0.47,
    "sentiment_score": -1,
    "dominant_flow": "speculative_put",
    "key_levels": { "heaviest_strike": 69000, "net_support": 63000, "net_resistance": 71000 },
    "spot_price": 69016
  },
  "flow_breakdown": [
    {"label": "speculative_put", "label_cn": "看跌投机", "count": 2254, "pct": 43.4},
    {"label": "unknown", "label_cn": "未知流向", "count": 0, "pct": 0.0}
  ]
}
```

**流向分类8种定义**:

| Flow Key | 中文名 | 触发条件 |
|----------|--------|---------|
| protective_hedge | 保护性对冲 | Buy Put, delta 0.10-0.35 |
| premium_collect | 收权利金 | Sell Put/Call, 低delta |
| speculative_put | 看跌投机 | Buy/Sell Put, 高delta |
| call_speculative | 看涨投机 | Buy Call, delta < 0.30 |
| call_momentum | 追涨建仓 | Buy Call, delta >= 0.30 |
| covered_call | 备兑开仓 | Sell Call, delta <= 0.40 |
| call_overwrite | 改仓操作 | Sell Call, delta > 0.40 |
| unknown | 未知流向 | direction/option_type 数据缺失时兜底 |

**情绪评分规则**: buy_ratio > 55%→+分, < 45%→-分; dominant_flow 加减/增加 1分

---

## 🏗️ 项目架构

```
crypto-options-aggregator/
├── dashboard/                        # Web 监控面板 (FastAPI)
│   ├── main.py                      # 后端 API + SQLite(WAL)
│   │   ├── _weighted_score()         # v5.3: 五维加权评分
│   │   ├── _calc_pop()              # v5.3: 概率获利计算
│   │   ├── _calc_breakeven_pct()     # v5.3: 安全垫距离%
│   │   ├── _calc_iv_rank()          # v5.3: IV Rank历史分位
│   │   ├── _classify_flow_heuristic() # v5.4: 8类流向分类
│   │   ├── get_vol_surface()        # v5.5: IV期限结构
│   │   └── get_max_pain()           # v5.5: 最大痛点/GEX
│   ├── static/
│   │   ├── index.html               # 前端 (TailwindCSS + Chart.js)
│   │   └── app.js                   # 前端逻辑 (21列表格/评分/图表/风向)
│   └── data/
│       └── monitor.db               # SQLite (WAL模式, 自动清理)
├── deribit-options-monitor/          # Deribit 引擎 (核心)
│   ├── deribit_options_monitor.py   # DVOL/大宗/SellPut/报告生成
│   └── SKILL.md                     # Skill 定义文档
├── options_aggregator.py            # 双平台聚合入口 + 数据校验层
├── binance_options.py               # Binance E-API 封装 (IV/OI/APR)
├── CHANGELOG.md                     # 变更日志
├── SKILL.md                         # 项目级 Skill 定义
├── requirements.txt                 # 核心依赖
└── dashboard/requirements.txt       # 面板额外依赖
```

---

## 🧪 技术栈

| 层级 | 技术 |
|------|------|
| **后端** | Python 3.13+ / FastAPI / SQLite (WAL) |
| **前端** | 原生 JavaScript / TailwindCSS CDN / Chart.js |
| **数据源** | Deribit Public API / Binance E-API / Binance Spot API |
| **并发** | ThreadPoolExecutor (4线程并行请求) |
| **缓存** | Order Book TTL 60s / Instrument 解析缓存 |

---

## 📦 依赖安装

```bash
pip install -r requirements.txt
# requests urllib3 pyyaml

pip install -r dashboard/requirements.txt
# fastapi uvicorn pydantic python-multipart
```

无需 Node.js / Webpack — 前端全部使用 CDN 引入。

---

## 📋 版本历史 (v5.x)

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| **v5.5** | 2026-04 | APR趋势稳健统计(P75/P90)+异常值过滤; 流向分类始终显示8种; 缓存破坏修复 |
| **v5.4.5** | 2026-04 | flow_breakdown补全全部8类型(含unknown count=0); APR chart改用P75稳健上限 |
| **v5.4.4** | 2026-04 | 流向分类8-type前后端对齐; FLOW_LABEL_MAP增加unknown/unclassified |
| **v5.4.3** | 2026-04 | 全面审计修复: colspan=21; flowNames补全unknown; trade suggestion补全 |
| **v5.4.2** | 2026-04 | 列表顺序修正: 安全垫%不再显示OI值(2177bug); data行<th>严格对齐 |
| **v5.4.1** | 2026-04 | IV显示100x bug修复: 后端存百分比(58.8), 前端不再乘100 |
| **v5.4** | 2026-04 | UI增强: _score加权排序列; DVOL趋势箭头+置信度; 大单三级severity+emoji |
| **v5.3** | 2026-04 | 策略增强: POP概率获利; Breakeven%安全垫; IV Rank历史分位; Weighted Score五维评分; Severity分级 |

---

## ⚠️ 免责声明

> 期权交易有风险，本工具仅供信息参考，不构成投资建议。
> Margin-APR 基于 20% 保证金估算。压力测试使用 Delta-Gamma 一阶近似，实际盈亏可能因波动率变化而偏离。
> 加权评分仅供参考，不保证收益。请根据自身风险承受能力谨慎操作。

---

## 🙏 致谢

- [lianyanshe-ai/deribit-options-monitor](https://github.com/lianyanshe-ai/deribit-options-monitor) — Deribit API 封装、Greeks 计算、DVOL/大宗交易核心引擎、8类流向分类体系
- [ccxt](https://github.com/ccxt/ccxt) — 统一交易所 API 库（用于交叉验证 Binance/Deribit 字段格式）
- [Deribit](https://www.deribit.com/) — 公共 API 数据源
- [Binance](https://www.binance.com/) — 期权与现货数据源

---

<p align="center">
  <b>Made with coffee for crypto options traders</b><br>
  <i>Sell puts like a pro</i>
</p>
