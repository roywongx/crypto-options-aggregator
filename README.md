<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Platform-Binance%20%2B%20Deribit-orange?logo=bitcoin" alt="Platform">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
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
| ⚡ **DVOL 深度分析** | Z-Score、趋势方向、7d/24h分位、置信度、动态阈值 |
| 🔍 **大宗异动监控** | 机构流向标签（保护性对冲/收权利金/Call追涨等8类） |
| 💥 **压力测试** | Delta-Gamma 近似公式，估算 -10% 跌幅亏损 |
| 🛡️ **风险预警** | 自动检测高风险合约，滚仓建议弹窗 |
| 🧮 **倍投修复计算器** | 输入浮亏金额，自动推荐最优修复方案 |
| 📈 **趋势图表** | APR/DVOL 历史 24H / 7天 / 30天 可视化 |
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
- **合约列表**：14列数据，支持点击表头排序
  - 平台 | 合约 | DTE | Strike | Delta | Gamma | Vega | **IV** | **APR**
  - 流动性评分 | **-10%亏损** | **盈亏平衡** | **OI** | **Spread%** | 风险状态
- **自动刷新**：5/10/30 分钟可选，后台静默监控

#### 🔥 倍投修复计算器
输入浮亏金额 → 系统自动筛选高 APR 合约 → 计算所需张数和预期净利润 → 推荐最优方案

#### ⚠️ 三级风险预警系统
- **Delta > 0.45** → 高风险红框闪烁 + 浏览器通知
- **价格接近Strike 2%** → 中风险橙色标记
- **点击合约行** → 弹出滚仓建议（推荐更低行权价远期替代）

#### 🐋 大宗异动面板
- 近1小时大单实时展示（标题 + 严重程度badge + 方向箭头）
- **情绪总览卡**：五档情绪评分 (🐂偏多 / 📈温和看多 / ➡️中性 / 📉温和看空 / 🐻偏空)
  - 自然语言总结（支撑/阻力位 + 主流行为判断）
  - 买卖比例 / 总名义金额 / 主流行为类型
- **净头寸 Strike 分布图**：每个行权价的净头寸(buy-sell)横向柱状图
  - 现价锚点标记 + 支撑位(黄)/阻力位(橙)自动识别 + 距现价百分比
- **流向分类网格**：8类 flow_label 中文展示 + 占比进度条
  - 保护性对冲 / 收权利金 / 看跌投机 / 看涨投机 / 追涨建仓 / 备兑开仓 / 改仓操作
- 支持按币种(BTC/ETH/SOL)和时间范围(7/30/90天)筛选

#### 📉 DVOL 分析面板
- 当前值 + Z-Score + 信号等级（异常偏高/偏高/正常/偏低/异常偏低）
- 趋势图表：24H / 7天 / 30天 切换

#### 📊 统计信息
- 总扫描次数 / 今日扫描数 / 大宗交易总数 / 数据库大小

---

## 🛠️ API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/scan` | 执行期权扫描（异步非阻塞） |
| `GET` | `/api/latest?currency=BTC` | 获取最新扫描结果 |
| `GET` | `/api/stats` | 统计概览 |
| `GET` | `/api/health` | **健康检查**（DB WAL模式 + 各API可达性） |
| `POST` | `/api/recovery-calculate` | 倍投修复计算 |
| `GET` | `/api/charts/apr?hours=168` | APR 趋势数据 |
| `GET` | `/api/charts/dvol?hours=168` | DVOL 趋势数据 |
| `GET` | `/api/trades/history?days=7` | 大宗交易历史（支持方向/来源/行权价筛选） |
| `GET` | `/api/trades/strike-distribution?days=30` | Strike 分布数据 |
| `GET` | `/api/trades/wind-analysis?currency=BTC&days=30` | **风向分析 v2**（情绪+Strike分布+流向分类） |

### 扫描参数 (POST /api/scan)

```json
{
  "currency": "BTC",
  "min_dte": 14,
  "max_dte": 25,
  "max_delta": 0.4,
  "margin_ratio": 0.2,
  "option_type": "PUT",
  "strike": null,
  "strike_range": null
}
```

### 返回数据结构（关键字段）

> ⚠️ **重要**: 所有数值字段已过数据校验层过滤。`mark_iv` 统一为**小数格式**（0.xx），前端显示时乘以100转为百分比。

```json
{
  "spot_price": 67670.42,
  "dvol_current": 58.42,
  "dvol_z_score": 0.35,
  "dvol_signal": "正常区间",
  "dvol_raw": {
    "current_dvol": 58.42,
    "z_score_7d": 0.35,
    "trend": "震荡",
    "confidence": 53.8,
    "iv_percentile_24h": 45.2,
    "iv_percentile_7d": 28.1,
    "recommendation": "...",
    "dynamic_thresholds": { ... }
  },
  "large_trades_count": 2,
  "large_trades_details": [{ "type", "severity", "title", "message" }],
  "large_trades_detail": [{ "instrument_name", "direction", "strike", "delta", "flow_label", "underlying_notional_usd" }],
  "contracts": [{
    "platform": "Deribit",
    "symbol": "BTC-24APR26-65000-P",
    "strike": 65000, "dte": 19,
    "delta": -0.3505, "gamma": 0.00005, "vega": 56.37,
    "mark_iv": 0.4823,
    "apr": 271.81,
    "breakeven": 63160.66, "open_interest": 2639.1, "spread_pct": 1.82,
    "liquidity_score": 100, "loss_at_10pct": 3496.35
  }],
  "validation_warnings": []
}

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
    "spot_price": 69016,
    "time_range": "近30天"
  },
  "sentiment_text": "支撑$63K(-8.7%)/阻力$71K(+2.9%) | 主流行为:看跌投机",
  "strike_flows": [
    { "strike": 69000, "buys": 455, "sells": 322, "net": 133, "notional": 777000, "dist_from_spot_pct": 1.3 }
  ],
  "flow_breakdown": [
    { "label": "speculative_put", "label_cn": "看跌投机", "count": 2254, "pct": 43.4 }
  ]
}
```

**情绪评分规则**: buy_ratio > 55%→+分, < 45%→-分; dominant_flow 加减/增加 1分
```

**字段格式约定**:

| 字段 | 格式 | 说明 |
|------|------|------|
| `mark_iv` | 小数 (0.xx) | Binance: `0.47`, Deribit: `0.48`（已除以100）。前端 `(iv*100)%` 显示 |
| `open_interest` | 张数 | Binance: 来自 `/eapi/v1/openInterest` 的 `sumOpenInterest`; Deribit: orderbook 的 `open_interest` |
| `premium_usd` / `premium_usdt` | USDT | 标记价 × 数量（Binance）或 mark_price × underlying_price（Deribit） |
| `delta` | (-1, 1) | 负值 = Put |
| `spot_price` | 动态获取 | 从 Deribit underlying_price 或 Binance APR 反推，不再硬编码 |

---

## 🚀 CLI 使用

```bash
# 默认扫描（DTE 3-30, Max Delta 0.5）
python options_aggregator.py

# JSON 模式（供程序调用）
python options_aggregator.py --json

# 特定行权价
python options_aggregator.py --strike 64000

# 行权价范围
python options_aggregator.py --strike-range 60000-65000

# 备兑看涨
python options_aggregator.py --option-type CALL

# 自定义参数
python options_aggregator.py \
  --currency ETH \
  --min-dte 7 --max-dte 45 \
  --max-delta 0.25 \
  --margin-ratio 0.15 \
  --option-type PUT \
  --json
```

### 全部参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--currency` | BTC | BTC / ETH / SOL / XRP / BNB / DOGE |
| `--min-dte` | 3 | 最小到期天数 |
| `--max-dte` | 30 | 最大到期天数 |
| `--max-delta` | 0.5 | 最大 Delta 绝对值 |
| `--strike` | - | 特定行权价 |
| `--strike-range` | - | 行权价范围（如 60000-65000）|
| `--margin-ratio` | 0.2 | 保证金比率（SPAN约15-30%）|
| `--option-type` | PUT | PUT / CALL |
| `--json` | false | JSON 格式输出 |

---

## 🏗️ 项目架构

```
crypto-options-aggregator/
├── dashboard/                        # Web 监控面板 (FastAPI)
│   ├── main.py                      # 后端 API + SQLite(WAL)
│   ├── static/
│   │   ├── index.html               # 前端 (TailwindCSS + Chart.js)
│   │   └── app.js                   # 前端逻辑 (排序/预警/图表)
│   └── data/
│       └── monitor.db               # SQLite (WAL模式, 90天自动清理)
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

### 数据流

```
[Deribit Monitor]                    [Binance Options]
  DVOL 信号 (Z-Score/trend/conf/分位)   合约扫描 (Greeks/APR/OI/Spread)
  大宗异动 (flow_label/severity/Greeks)  IV: markIV(大写) / OI: openInterest API(YYMMDD)
       │                                      │
       └──────────┬───────────────────────────┘
                  ▼
      [options_aggregator.py]  v4.1
         ├─ 字段映射 (oi→open_interest, premium_usdt→premium_usd)
         ├─ 动态 spot_price 提取 (多级回退)
         ├─ validate_contract() 数据校验层
         └─ validation_warnings 异常告警
                  │
                  ▼
           [main.py] FastAPI 后端
         ├─ 透传 dvol_raw (完整DVOL分析)
         ├─ 透传 large_trades_detail (富化交易)
         ├─ SQLite 存储 (WAL模式, 并发安全)
         │   └─ large_trades_history: flow_label/notional_usd/delta/instrument_name
         ├─ /api/trades/wind-analysis: 情绪评分 + 净头寸Strike + 流向分类
         └─ REST API
                  │
        ┌─────────┼─────────┐
        ▼         ▼         ▼
   [index.html]  [app.js]  [Chart.js]
   表格/面板     交互/排序   图表渲染
   (iv*100)%     mark_iv     渲染
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

## 📋 更新日志

> 详细变更记录见 [CHANGELOG.md](./CHANGELOG.md)

### v5.1 (2026-04-07) — 全面审计修复版
- **fix**: 大宗异动金额门槛：Deribit fallback 添加 `$10,000` 最小名义值过滤
- **fix**: Wind Analysis PUT/CALL 标签判定 Bug（`optType==='P'` → 完整匹配 `'PUT'`）
- **fix**: Wind Analysis SQL 过滤 option_type=NULL 垃圾记录
- **fix**: 数据库清理：删除 7,879 条垃圾数据（60%），保留 5,150 条有效记录
- **fix**: 大宗交易前端渲染重写，使用实际 API 字段
- **fix**: Flow 分类算法重写，匹配原始项目逻辑

---

## ⚠️ 免责声明

> 期权交易有风险，本工具仅供信息参考，不构成投资建议。
> Margin-APR 基于 20% 保证金估算。压力测试使用 Delta-Gamma 一阶近似，实际盈亏可能因波动率变化而偏离。
> 请根据自身风险承受能力谨慎操作。

---

## 🙏 致谢

- [lianyanshe-ai/deribit-options-monitor](https://github.com/lianyanshe-ai/deribit-options-monitor) — Deribit API 封装、Greeks 计算、DVOL/大宗交易核心引擎
- [ccxt](https://github.com/ccxt/ccxt) — 统一交易所 API 库（用于交叉验证 Binance/Deribit 字段格式）
- [Deribit](https://www.deribit.com/) — 公共 API 数据源
- [Binance](https://www.binance.com/) — 期权与现货数据源

---

<p align="center">
  <b>Made with coffee for crypto options traders</b>
</p>
