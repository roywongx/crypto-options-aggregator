<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Platform-Binance%20%2B%20Deribit-orange?logo=bitcoin" alt="Platform">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/v5.10-BugFix+%E6%B5%81%E5%90%91%E5%88%86%E7%B1%BB+PCR+MaxPain-blueviolet" alt="Version">
</p>

<h1 align="center">Crypto Options Aggregator</h1>

<p align="center">
  <b>双平台加密期权扫描器 + 实时监控面板 + 大单风向标</b><br>
  Binance (USDT本位) × Deribit (币本位) 联合分析<br>
  面向 Sell Put / Covered Call 策略的专业工具
</p>

---

## ✨ 核心亮点

| 能力 | 说明 |
|------|------|
| 🔄 **双平台聚合** | 同时扫描 Binance + Deribit，统一排序对比 |
| 📊 **Margin-APR** | 基于保证金占用计算真实年化收益率（默认20%） |
| ⚡ **DVOL 深度分析** | Z-Score、趋势方向(↑↓→)、置信度(高/中/低)、7d分位、动态参数自适应 |
| 🐋 **大单风向标** | 机构流向标签（8类核心分类）+ 三级严重程度(大单⚠️/中单🟡/普通✅) + 情绪评分 |
| 💥 **压力测试** | Delta-Gamma 近似公式，估算 -10% 跌幅亏损，Martingale 沙盒模拟 |
| 🛡️ **风险预警** | 自动检测高风险合约，滚仓建议弹窗 |
| 🧮 **倍投修复计算器** | 输入浮亏金额，自动推荐最优修复方案（含净信用/保证金检查） |
| 📈 **趋势图表** | APR(P75稳健上限+均值) / DVOL / PCR 比率 历史 24H / 7天 / 30天 可视化 |
| 🎯 **加权评分排序** | APR(25%) + POP(25%) + 安全垫(20%) + 流动性(15%) + IV中性(15%) 五维综合评分 |
| 📉 **IV期限结构** | 7D/14D/30D/60D/90D 隐含波动率曲面 + Backwardation倒挂检测 |
| 💔 **最大痛点/ Gamma Flip** | Max Pain计算 + GEX(Gamma Exposure)分布图 + Flip点风险预警 |
| 🎛️ **DVOL自适应参数** | 高波动自动收紧Delta/APR阈值，低波动适当放宽 |

---

## 🖥️ Web 监控面板

基于 FastAPI + 原生 JS 构建，开箱即用。

### 启动方式

```bash
# 安装依赖
pip install -r requirements.txt
pip install -r dashboard/requirements.txt

# 启动面板
cd dashboard && python -m uvicorn main:app --reload --port 8080
# 访问 http://localhost:8080
```

### 功能全景

#### 📊 实时监控大屏

- **宏观指标卡片**：现货价格（多源动态获取）/ DVOL指数+信号 / 大宗交易数 / 最佳APR
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

#### 🐋 大单风向标 (v5.9)

近30天大单实时展示与深度分析：

**大单列表**
- 标题 + 严重程度badge + 方向箭头 + flow emoji
- 🔴 **大单⚠️**: notional ≥ $2M
- 🟡 **中单🟡**: notional ≥ $500K
- 🟢 **普通✅**: notional < $500K

**情绪总览卡**
- 五档情绪评分 (🐂偏多 / 📈温和看多 / ➡️中性 / 📉温和看空 / 🐻偏空)
- 自然语言总结（支撑/阻力位 + 主流行为判断）
- 买卖比例 / 总名义金额 / 主流行为类型

**净头寸 Strike 分布图**
- 每个行权价的净头寸(buy-sell)横向柱状图
- 现价锚点标记 + 支撑位(黄)/阻力位(橙)自动识别 + 距现价百分比

**流向分类网格 — 8类核心分类**

| 中文名 | 含义 | 触发条件 |
|--------|------|---------|
| 保护性对冲 | Deep ITM Sell Put，强烈看涨愿意接货 | \|Δ\|≥0.7 |
| 收权利金 | ATM Sell Put，温和看涨+稳定收权 | 0.4≤\|Δ\|<0.7 |
| 备兑开仓 | OTM Sell Put/Call，纯收权利金 | \|Δ\|<0.4 |
| 保护性买入 | Deep ITM Buy Put，机构对冲防下跌 | \|Δ\|≥0.7 |
| 看跌投机 | Buy Put，短线看跌或投机 | \|Δ\|<0.7 |
| 改仓操作 | ITM Sell Call，改仓操作 | \|Δ\|>0.4 |
| 追涨建仓 | ATM Buy Call，顺势追涨 | \|Δ\|≥0.4 |
| 看涨投机 | OTM Call，低成本博反弹 | \|Δ\|<0.4 |

支持按币种(BTC/ETH/SOL/XRP)和时间范围(7/30/90天)筛选。

#### 📉 DVOL 分析面板

- 当前值 + Z-Score + 信号等级（异常偏高/偏高/正常/偏低/异常偏低）
- **趋势箭头**: ↑上升 / ↓下降 / →震荡
- **置信度**: 高(>70) / 中(40-70) / 低(<40)
- **动态参数建议**: DVOL自适应调整Delta/APR/DTE阈值
- 趋势图表：24H / 7天 / 30天 切换

#### 📈 APR 趋势图表 (v5.4.5+)

- **标准化APR**: 固定参数(delta≤0.25, DTE 14-35, PUT only)，确保跨时间可比
- **P75 APR(稳健上限)**: 第75百分位数线，过滤极端异常值
- **平均 APR**: 全量合约均值
- **异常值过滤**: 自动排除 APR<1% 或 >500% 的失真数据
- **前值填充**: 数据缺失时使用前一有效值按比例填补

#### 📉 IV 期限结构面板 (v5.5+)

- **5个到期节点**: 7D / 14D / 30D / 60D / 90D 的平均隐含波动率
- **IV曲面图**: Chart.js 渲染的期限结构折线图
- **Backwardation检测**: 近期IV > 远期IV时显示倒挂警告
- **颜色编码**: ≤14D红色(短期高风险), >14D青色

#### 💔 最大痛点 / Gamma Flip (Max Pain & GEX) (v5.5+)

- **现货价 vs 最大痛点**: 直观对比 + 距离百分比
- **PCR (Put/Call Ratio)**: 多空持仓比
- **情绪信号**: 中性/看多/看空
- **Pain Curve + GEX 双轴图**:
  - 归一化痛点曲线（橙色线）— 标记最大痛点位置
  - OI净敞口柱状图（绿红双色）— 正=Call主导, 负=Put主导
- **Gamma Flip 预警**: 当现货进入空头Gamma区时显示危险警告
- **多到期日支持**: 展示最近4个到期日的Max Pain和GEX

#### 🎮 Martingale 沙盒模拟器

输入当前持仓和假设崩盘价格：
- 自动估算内在价值亏损 + Vega膨胀
- 从Deribit筛选可用修复合约
- 计算最优张数、保证金占用、预期净利润
- 判断方案可行性（VIABLE/PARTIAL/DANGER）

#### 📊 统计信息 & 导出

- 总扫描次数 / 今日扫描数 / 大宗交易总数 / 数据库大小
- CSV导出：支持自定义时间范围和币种

---

## 🛠️ API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/quick-scan` | 快速期权扫描（推荐）|
| `POST` | `/api/scan` | 完整期权扫描（已弃用）|
| `GET` | `/api/latest?currency=BTC` | 获取最新扫描结果 |
| `GET` | `/api/stats` | 统计概览 |
| `GET` | `/api/health` | **健康检查**（DB WAL模式 + 各API可达性）|
| `POST` | `/api/recovery-calculate` | 倍投修复计算 |
| `POST` | `/api/calculator/roll` | 净信用滚仓计算器 |
| `POST` | `/api/sandbox/simulate` | Martingale沙盒模拟 |
| `GET` | `/api/charts/apr?hours=168` | APR 趋势数据（标准化参数）|
| `GET` | `/api/charts/dvol?hours=168` | DVOL 趋势数据 |
| `GET` | `/api/charts/vol-surface?currency=BTC` | **IV期限结构** |
| `GET` | `/api/charts/pcr?currency=BTC&hours=168` | PCR (Put/Call Ratio) 趋势 |
| `GET` | `/api/metrics/max-pain?currency=BTC` | **最大痛点/GEX** |
| `GET` | `/api/dvol-advice?currency=BTC` | DVOL自适应参数建议 |
| `GET` | `/api/export/csv?currency=BTC&hours=168` | CSV 数据导出 |
| `GET` | `/api/trades/history?days=7` | 大宗交易历史 |
| `GET` | `/api/trades/strike-distribution?days=30` | Strike 分布数据 |
| `GET` | `/api/trades/wind-analysis?currency=BTC&days=30` | **风向分析**（8类流向分类）|

### 快速扫描参数 (POST /api/quick-scan)

```json
{
  "currency": "BTC",
  "min_dte": 14,
  "max_dte": 35,
  "max_delta": 0.4,
  "margin_ratio": 0.2,
  "option_type": "PUT",
  "strike": null,
  "strike_range": null
}
```

---

## 🏗️ 项目架构

```
crypto-options-aggregator/
├── dashboard/                        # Web 监控面板 (FastAPI)
│   ├── main.py                      # 后端 API + SQLite(WAL)
│   ├── config.py                    # 统一配置管理
│   ├── static/
│   │   ├── index.html               # 前端 (TailwindCSS + Chart.js)
│   │   └── app.js                   # 前端逻辑
│   └── data/monitor.db              # SQLite (WAL模式)
├── deribit-options-monitor/          # Deribit 引擎
├── options_aggregator.py            # 双平台聚合入口
├── binance_options.py               # Binance E-API 封装
├── requirements.txt                 # 核心依赖
└── dashboard/requirements.txt       # 面板依赖
```

---

## 🧪 技术栈

| 层级 | 技术 |
|------|------|
| **后端** | Python 3.13+ / FastAPI / Pydantic / SQLite (WAL) |
| **前端** | 原生 JavaScript / TailwindCSS CDN / Chart.js |
| **数据源** | Deribit Public API / Binance E-API / Binance Spot API |
| **并发** | ThreadPoolExecutor (4线程并行请求) |
| **统计** | SciPy (norm.cdf for DVOL percentile) |

---

## 📦 依赖安装

```bash
pip install -r requirements.txt
pip install -r dashboard/requirements.txt
```

无需 Node.js / Webpack — 前端全部使用 CDN 引入。

---

## 🎛️ 策略预设系统

内置三档策略预设：

| 策略 | max_delta | min_dte | max_dte | margin_ratio |
|------|----------|---------|---------|-------------|
| **Conservative** | 0.20 | 30 | 45 | 0.18 |
| **Standard** | 0.30 | 14 | 35 | 0.20 |
| **Aggressive** | 0.40 | 7 | 28 | 0.22 |

PUT 和 CALL 有独立的预设配置。

---

## 📋 版本历史 (v5.x)

| 版本 | 主要变更 |
|------|---------|
| **v5.10** | 重大Bug修复：sentiment_score中英文key不匹配；sandbox KeyError/IV单位；roll计算器NameError；PCR月份误匹配；delta缺失导致risk_level恒低；_get_spot_from_scan查错字段 |
| **v5.9.1** | flow_breakdown聚合为8种核心中文分类；语法错误修复 |
| **v5.9.0** | 流向分类恢复8种核心类型；Sell PUT ITM阈值修正(0.5→0.7) |
| **v5.8.11** | Sell PUT分类阈值修正：ITM 0.5→0.7；ATM 0.2→0.4 |
| **v5.8.10** | 全面重构流向分类逻辑：基于Delta/Moneyness精确判断 |
| **v5.8.9** | 修复Sell PUT误判为"看跌投机"；Sell PUT本质是看涨操作 |
| **v5.7** | 统一Config配置管理 |
| **v5.6** | CalculationEngine统一计算引擎 |
| **v5.5** | IV期限结构曲面；Max Pain/GEX/Flip点检测 |
| **v5.4.5** | APR趋势标准化(P75)；固定参数确保跨时间可比 |
| **v5.3** | POP概率获利；Breakeven%安全垫；IV Rank；Weighted Score五维评分 |

---

## ⚠️ 免责声明

> 期权交易有风险，本工具仅供信息参考，不构成投资建议。
> Margin-APR 基于 20% 保证金估算。压力测试使用 Delta-Gamma 一阶近似，实际盈亏可能因波动率变化而偏离。
> 加权评分仅供参考，不保证收益。请根据自身风险承受能力谨慎操作。

---

## 🙏 致谢

- [Deribit](https://www.deribit.com/) — 公共 API 数据源
- [Binance](https://www.binance.com/) — 期权与现货数据源
- [ccxt](https://github.com/ccxt/ccxt) — 统一交易所 API 库

---

<p align="center">
  <b>Made with coffee for crypto options traders</b><br>
  <i>Sell puts like a pro</i>
</p>
