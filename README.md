<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Platform-Binance%20%2B%20Deribit-orange?logo=bitcoin" alt="Platform">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/v6.0-BTC%E9%A3%8E%E9%99%A9%E6%A1%86%E6%9E%B6+%E5%B9%B6%E8%A1%8C%E6%89%AB%E6%8F%8F+%E6%8A%84%E5%BA%95%E5%BB%BA%E8%AE%AE-blueviolet" alt="Version">
</p>

<h1 align="center">Crypto Options Aggregator (期权监控聚合面板)</h1>

<p align="center">
  <b>专业级双平台期权扫描器 + 动态风险框架 + 抄底建议引擎</b><br>
  实时聚合 Binance (USDT本位) 与 Deribit (币本位) 深度期权数据<br>
  专为进阶 <b>Sell Put / Covered Call</b> 策略及滚仓(Rolling)交易者打造
</p>

---

## 🌟 核心特性与优势

| 功能模块 | 详细说明 |
|------|------|
| 🔗 **双平台统一视图** | 消除 Binance + Deribit 之间的差异，在同一面板对比真实收益。 |
| 💰 **真实 Margin-APR** | 摒弃传统面值收益率，采用真实**保证金占用回报率**（如锁定20%资金计算APR），反映资金真实效率。 |
| 🌊 **DVOL 波动率引擎** | 基于 Deribit 波动率指数，自动计算 Z-Score 和历史分位数。高波收紧参数，低波放宽，实现策略自动适配。 |
| 🛡️ **动态风险框架(v6.0)** | 引入 BTC 风险阶梯（如 55k 常规底，45k 极限底），为不同市场阶段提供精确的参数权重修正。 |
| 💡 **智能抄底助手(v6.0)** | 融合当前水位、Max Pain (最大痛点)、GEX (Gamma Exposure)，输出实时的建仓/滚仓/平仓操作指令。 |
| 🔄 **正收益滚仓计算器** | 当持仓遇险时，自动寻找更低行权价、更远到期日的新合约，并测算所需保证金，确保净信用(Net Credit)大于零。 |
| 🌊 **大单风向标 & 资金流** | 监控百万级大单，基于 Delta 深度解析真实交易意图（备兑、保护性买入、追涨等），透视主力底牌。 |
| 📊 **多维数据图表分析** | 实时生成 APR 分位图、DVOL 趋势图、波动率曲面(Term Structure) 以及 PCR (Put/Call Ratio) 面板。 |

---

## 🚀 快速开始

本项目采用 FastAPI 后端与纯原生 JS + TailwindCSS 前端，轻量且高效。

### 1. 环境准备
请确保已安装 Python 3.10 及以上版本。

```bash
# 克隆仓库
git clone https://github.com/roywongx/crypto-options-aggregator.git
cd crypto-options-aggregator

# 安装依赖项
pip install -r requirements.txt
pip install -r dashboard/requirements.txt
```

### 2. 启动服务
```bash
# 进入 dashboard 目录并启动
cd dashboard
python -m uvicorn main:app --reload --port 8080
```
启动成功后，浏览器访问 👉 `http://localhost:8080` 即可进入监控面板。

---

## 🏗️ 核心架构与 API

| 请求方式 | 路由 | 描述 |
|------|------|------|
| `POST` | `/api/quick-scan` | 核心扫描接口，多线程并行获取盘口、现货价、期权链 |
| `GET` | `/api/latest` | 获取数据库中缓存的最后一次有效扫描结果 |
| `GET` | `/api/bottom-fishing/advice` | **(New)** 基于动态风险框架的抄底建议 |
| `POST` | `/api/calculator/roll` | 持仓遇险时的正收益滚仓计算器 |
| `POST` | `/api/sandbox/simulate` | 极端行情(如闪崩)下的保证金压力测试沙盒 |
| `GET` | `/api/metrics/max-pain` | 获取当月最大痛点及 Gamma Flip 关键点位 |
| `GET` | `/api/charts/vol-surface` | 获取波动率曲面及升贴水状态 |

---

## ⚙️ 策略预设参考

系统内置的三种默认过滤预设（支持在界面自由微调）：

| 风格 | Max Delta | DTE (到期天数) | 目标 APR |
|------|----------|---------|-------------|
| **保守 (Conservative)** | 0.20 | 30 - 45 天 | 15%+ |
| **标准 (Standard)** | 0.30 | 14 - 35 天 | 20%+ |
| **激进 (Aggressive)** | 0.40 | 7 - 28 天 | 25%+ |

*(注：系统会自动根据 DVOL 波动率指数，对上述预设进行动态微调。)*

---

## 💡 更新日志

| 版本 | 核心更新内容 |
|------|---------|
| **v6.0** | 新增 **BTC动态风险框架** (55k常规底/45k极限底)；新增**抄底建议模块**，结合 Max Pain 与 GEX 生成策略指令；全面启用**并发网络请求**，大幅缩短加载延迟。 |
| **v5.10** | 修复现货价抓取 Bug，新增大单流向深度分类、PCR 分析指标及最大痛点数据源优化。 |
| **v5.9** | 重构大单监控系统，引入交易意图智能判定（如识别“深度ITM保护性买入”）。 |
| **v5.7** | 重构配置引擎，抽离全局 `config.py`，彻底消除硬编码散落。 |
| **v5.3** | 引入 `Calculation Engine` 和加权评分系统 (POP, Breakeven, Liquidity)。 |

---

## 🙏 致谢 (Acknowledgments)

本项目并非从零开始，其底层核心逻辑得益于开源社区的无私奉献。在此特别感谢：

- **[lianyanshe-ai/deribit-options-monitor](https://github.com/lianyanshe-ai/deribit-options-monitor)** 
  本项目最初的灵感与核心根基来源于此库。原作者为其提供了极其健壮的 Deribit API 封装、Greeks 期权希腊字母推算以及 DVOL/大单提醒的基础框架。正是基于这一出色的开源工作，我们才得以扩展支持 Binance、重构风险模型并打造统一的双平台监控面板。
- **[ccxt](https://github.com/ccxt/ccxt)** 
  为极端行情下现货价格的 Fallback 获取提供了稳定的跨交易所 API 方案。

---

## ⚠️ 风险免责声明

期权交易（尤其是裸卖期权 / Sell Puts）具有极高的资金风险，可能导致本金完全损失。
本工具所有数据、建议、压力测试及滚仓计算**仅供学习与量化分析参考，绝不构成任何投资建议**。在进行实盘交易前，请务必充分理解期权规则并严格做好资金与仓位管理。
