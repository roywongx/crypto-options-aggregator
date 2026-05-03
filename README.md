<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Platform-Binance%20%2B%20Deribit-orange?logo=bitcoin" alt="Platform">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/v5.7-Production%20Ready-blueviolet" alt="Version">
</p>

<h1 align="center">Crypto Options Aggregator Pro</h1>

<p align="center">
  Professional crypto options trading terminal — Binance + Deribit real-time aggregation<br>
  专业加密期权交易终端 — 币安 + Deribit 实时聚合<br><br>
  Built for <b>Sell Put / Covered Call / Wheel / Roll</b> strategy traders<br>
  为 <b>Sell Put / Covered Call / Wheel / Roll</b> 策略交易者打造
</p>

---

## Table of Contents / 目录

- [Features / 功能特性](#features--功能特性)
- [Quick Start / 快速开始](#quick-start--快速开始)
- [Architecture / 系统架构](#architecture--系统架构)
- [Modules / 模块说明](#modules--模块说明)
- [API Reference / API 参考](#api-reference--api-参考)
- [Tech Stack / 技术栈](#tech-stack--技术栈)
- [Production Deployment / 生产部署](#production-deployment--生产部署)
- [Project Structure / 项目结构](#project-structure--项目结构)
- [Contributing / 贡献指南](#contributing--贡献指南)
- [License / 许可证](#license--许可证)

---

## Features / 功能特性

### Core Trading / 核心交易

| Feature / 功能 | Description / 说明 |
|---|---|
| **Dual-Platform Aggregation / 双平台聚合** | Binance eAPI + Deribit WebSocket unified into a single options chain, sorted by Margin-APR / 币安 eAPI + Deribit WebSocket 统一期权链，按保证金APR排序 |
| **Real-Time Scanning / 实时扫描** | Background engine continuously scans all contracts, auto-filters by Delta, DTE, liquidity / 后台引擎持续扫描所有合约，按 Delta、DTE、流动性自动过滤 |
| **Strategy Engine / 策略引擎** | Roll / New / Grid modes with automated P&L, breakeven, and margin calculations / 滚仓/新建/网格模式，自动计算盈亏、盈亏平衡、保证金 |
| **Paper Trading / 模拟交易** | 50K USDT simulation with real market data, margin freezing, and position tracking / 5万USDT模拟盘，真实行情数据、保证金冻结、持仓追踪 |

### Risk & Analytics / 风控与分析

| Feature / 功能 | Description / 说明 |
|---|---|
| **Risk Command Center / 风险指挥中心** | 4-dimensional assessment (price, volatility, sentiment, liquidity) with Gauge + Radar visualization / 四维评估（价格、波动率、情绪、流动性），仪表盘+雷达图可视化 |
| **Black-Scholes Stress Testing / BS压力测试** | High-order Greeks sensitivity: Delta, Gamma, Vanna, Volga with multi-scenario joint stress / 高阶Greeks敏感度：Delta、Gamma、Vanna、Volga，多场景联合压力 |
| **DVOL Analyzer / DVOL分析器** | Deribit Volatility Index tracking with Z-Score, percentile, and signal generation / Deribit波动率指数追踪，Z-Score、百分位、信号生成 |
| **On-Chain Metrics / 链上指标** | MVRV, NUPL, Mayer Multiple, Puell Multiple, 200WMA/200DMA, Balanced Price / MVRV、NUPL、Mayer倍数、Puell倍数、200周均线/200日均线、平衡价格 |
| **Derivative Metrics / 衍生品指标** | Sharpe Ratio, funding rate, futures/spot ratio, overheating assessment / Sharpe比率、资金费率、期货/现货比、过热评估 |
| **Max Pain & GEX / 最大痛点与GEX** | Max pain calculation with gamma exposure curve and flip point detection / 最大痛点计算，Gamma暴露曲线与翻转点检测 |
| **AI Sentiment / AI情绪分析** | Large-trade intent classification, put/call ratio, risk warnings / 大单意图分类、看跌/看涨比、风险预警 |

### AI & Intelligence / AI与智能

| Feature / 功能 | Description / 说明 |
|---|---|
| **LLM Analyst / LLM研判中心** | Comprehensive analysis, bull/bear debate, anomaly audit via multi-model LLM / 综合分析、多空辩论、异常审计，多模型LLM驱动 |
| **AI Co-Pilot / AI副驾驶** | Built-in trading assistant with market context injection / 内置交易助手，注入市场上下文 |
| **Multi-Model Routing / 多模型路由** | Claude / GPT / DeepSeek / Gemini via LiteLLM / 通过LiteLLM路由Claude/GPT/DeepSeek/Gemini |
| **MCP Server / MCP服务** | Model Context Protocol — exposes tools to external LLMs / 模型上下文协议 — 向外部LLM暴露工具 |

### Infrastructure / 基础设施

| Feature / 功能 | Description / 说明 |
|---|---|
| **DataHub / 数据中心** | High-performance Pub/Sub with persistent WebSocket connections / 高性能发布/订阅，持久化WebSocket连接 |
| **Multi-Exchange Abstraction / 多交易所抽象** | Unified interface for Binance + Deribit, extensible to Bybit/OKX / 统一接口对接币安+Deribit，可扩展至Bybit/OKX |
| **Event Bus / 事件总线** | asyncio.Queue-based pub/sub with WebSocket push to frontend / 基于asyncio.Queue的发布/订阅，WebSocket推送到前端 |
| **SQLite WAL / SQLite WAL模式** | Async read, concurrent write, zero-config database / 异步读、并发写、零配置数据库 |

---

## Quick Start / 快速开始

### Prerequisites / 前置要求

- Python 3.13+
- pip

### Installation / 安装

```bash
git clone https://github.com/roywongx/crypto-options-aggregator.git
cd crypto-options-aggregator/dashboard
pip install -r requirements.txt
python main.py
```

Open **http://localhost:8000** in your browser.
打开浏览器访问 **http://localhost:8000**。

> **Important / 重要**: Must use single-worker mode (`--workers 1`). The system uses in-memory singletons for WebSocket connections and caching — multi-worker will cause duplicate connections and doubled memory usage.
>
> 必须使用单worker模式（`--workers 1`）。系统使用内存单例管理WebSocket连接和缓存 — 多worker会导致重复连接和双倍内存占用。

### Optional Dependencies / 可选依赖

```bash
pip install litellm  # For AI features / 启用AI功能
```

---

## Architecture / 系统架构

```
Binance API / Deribit WebSocket
        │
        ▼
   DataHub (WebSocket + memory cache + Pub/Sub)
   数据中心（WebSocket + 内存缓存 + 发布/订阅）
        │
        ▼
  ┌─────────────────────────────────────┐
  │           Compute Layer             │
  │           计算层                     │
  │                                     │
  │  Scan Engine    · Risk Framework    │
  │  扫描引擎       · 风险框架          │
  │                                     │
  │  Strategy Calc  · Quant Engine      │
  │  策略计算       · 量化引擎          │
  │                                     │
  │  DVOL Analyzer  · On-Chain Metrics  │
  │  DVOL分析器     · 链上指标          │
  └─────────────┬───────────────────────┘
                │
                ▼
  ┌─────────────────────────────────────┐
  │          Decision Layer             │
  │          决策层                      │
  │                                     │
  │  LLM Analyst · AI Co-Pilot          │
  │  LLM研判     · AI副驾驶             │
  │                                     │
  │  Multi-Agent Debate · MCP Server    │
  │  多Agent辩论        · MCP服务       │
  └─────────────┬───────────────────────┘
                │
                ▼
  FastAPI + Static Frontend (Chart.js)
  FastAPI + 静态前端（Chart.js）
```

---

## Modules / 模块说明

### Services (35 modules) / 服务层（35个模块）

<details>
<summary><b>Trading & Strategy / 交易与策略</b></summary>

| Module / 模块 | Description / 说明 |
|---|---|
| `scan_engine.py` | Core options scan engine / 核心期权扫描引擎 |
| `strategy_engine.py` | Unified strategy recommendation v2 / 统一策略推荐引擎 v2 |
| `strategy_calc.py` | Roll plan and new plan calculations / 滚仓计划与新建计划计算 |
| `grid_engine.py` | Grid strategy scoring and recommendations / 网格策略评分与推荐 |
| `grid_manager.py` | Grid position management (CRUD) / 网格持仓管理 |
| `margin_calculator.py` | Unified margin calculator (Deribit + Binance) / 统一保证金计算器 |
| `paper_trading.py` | Paper trading engine (virtual capital, P&L) / 模拟交易引擎 |

</details>

<details>
<summary><b>Risk & Quant / 风控与量化</b></summary>

| Module / 模块 | Description / 说明 |
|---|---|
| `risk_framework.py` | Dynamic risk framework with support levels / 动态风险框架与支撑位 |
| `unified_risk_assessor.py` | 4-dimensional risk assessment / 四维风险评估 |
| `pressure_test.py` | BS stress testing (Delta, Gamma, Vanna, Volga) / BS压力测试 |
| `quant_engine.py` | Institutional-grade Greeks via scipy / 机构级Greeks计算 |
| `shared_calculations.py` | BS pricing, norm CDF, win-rate, liquidity scoring / BS定价、正态CDF、胜率、流动性评分 |
| `support_calculator.py` | Dynamic support levels (MA + Fibonacci + on-chain) / 动态支撑位（均线+斐波那契+链上） |
| `dvol_analyzer.py` | DVOL tracking with Z-Score and signals / DVOL追踪与Z-Score信号 |
| `derivative_metrics.py` | Sharpe, funding rate, overheating / Sharpe、资金费率、过热评估 |
| `onchain_metrics.py` | MVRV, NUPL, Mayer, Puell, 200WMA / 链上指标全家桶 |

</details>

<details>
<summary><b>AI & Intelligence / AI与智能</b></summary>

| Module / 模块 | Description / 说明 |
|---|---|
| `llm_analyst.py` | LLM synthesis, debate, anomaly audit / LLM综合分析、辩论、异常审计 |
| `ai_router.py` | Multi-model LiteLLM routing / 多模型LiteLLM路由 |
| `ai_sentiment.py` | Large-trade sentiment analysis / 大单情绪分析 |
| `options_debate_engine.py` | 5-agent deterministic debate / 5 Agent确定性辩论 |
| `flow_classifier.py` | Options flow intent classification / 期权流意图分类 |
| `mcp_server.py` | MCP tool server for external LLMs / MCP工具服务 |

</details>

<details>
<summary><b>Infrastructure / 基础设施</b></summary>

| Module / 模块 | Description / 说明 |
|---|---|
| `datahub.py` | Pub/Sub data center with persistent WebSocket / 发布/订阅数据中心 |
| `event_bus.py` | asyncio.Queue-based event bus / 基于asyncio.Queue的事件总线 |
| `exchange_abstraction.py` | Multi-exchange unified interface / 多交易所统一接口 |
| `background_tasks.py` | Scheduled scan and DataHub lifecycle / 定时扫描与DataHub生命周期 |
| `http_client.py` | Unified sync/async httpx wrapper / 统一同步/异步httpx封装 |
| `api_retry.py` | Exponential-backoff retry / 指数退避重试 |
| `spot_price.py` | Spot price with caching / 现货价格缓存 |
| `macro_data.py` | Fear&Greed, QQQ/SPY, FRED rates / 恐惧贪婪、QQQ/SPY、FRED利率 |
| `iv_term_structure.py` | IV term structure (Hull-White, VRP) / IV期限结构 |

</details>

---

## API Reference / API 参考

Full interactive docs at **http://localhost:8000/docs**
完整交互文档访问 **http://localhost:8000/docs**

### Core Endpoints / 核心端点

| Endpoint / 端点 | Method | Description / 说明 |
|---|---|---|
| `/api/health` | GET | Health check / 健康检查 |
| `/api/latest` | GET | Latest scan results / 最新扫描结果 |
| `/api/quick-scan` | POST | Quick options scan / 快速期权扫描 |
| `/api/dashboard-init` | GET | Aggregated dashboard data / 仪表盘聚合数据 |

### Risk & Analytics / 风控与分析

| Endpoint / 端点 | Method | Description / 说明 |
|---|---|---|
| `/api/risk/overview` | GET | Risk command center data / 风险指挥中心数据 |
| `/api/risk/assess` | GET | Risk assessment / 风险评估 |
| `/api/risk/llm-insight` | GET | LLM risk analysis report / LLM风险分析报告 |
| `/api/metrics/max-pain` | GET | Max pain & GEX data / 最大痛点与GEX数据 |
| `/api/macro/all` | GET | Macro indicators / 宏观指标 |

### Strategy / 策略

| Endpoint / 端点 | Method | Description / 说明 |
|---|---|---|
| `/api/strategy/recommend` | GET | Strategy recommendations / 策略推荐 |
| `/api/grid/positions` | GET | Grid positions / 网格持仓 |
| `/api/grid/create` | POST | Create grid position / 创建网格持仓 |

### AI / AI

| Endpoint / 端点 | Method | Description / 说明 |
|---|---|---|
| `/api/llm-analyst/synthesize` | POST | Comprehensive analysis / 综合研判 |
| `/api/llm-analyst/debate` | POST | Bull/bear debate / 多空辩论 |
| `/api/llm-analyst/audit` | POST | Anomaly detection / 异常审计 |
| `/api/copilot/chat` | POST | AI Co-Pilot conversation / AI副驾驶对话 |

### Trading / 交易

| Endpoint / 端点 | Method | Description / 说明 |
|---|---|---|
| `/api/paper/trade` | POST | Paper trading / 模拟交易 |
| `/api/paper/positions` | GET | Open positions / 持仓列表 |
| `/api/trades/large` | GET | Large trade history / 大单历史 |

> All endpoints (except `/api/health`) require `X-API-Key` header in production.
>
> 生产环境中所有端点（`/api/health` 除外）需要 `X-API-Key` 请求头。

---

## Tech Stack / 技术栈

| Layer / 层级 | Technology / 技术 |
|---|---|
| **Backend / 后端** | Python 3.13 · FastAPI · SQLite (WAL + async) · httpx · websockets |
| **Frontend / 前端** | Vanilla JS · Chart.js · Tailwind CSS (no bundler / 无打包工具) |
| **AI** | LiteLLM multi-model routing (Claude / GPT / DeepSeek / Gemini) |
| **Quant / 量化** | scipy · Black-Scholes · institutional-grade Greeks |
| **Data / 数据** | Binance eAPI · Deribit WebSocket · FRED · yfinance |

---

## Production Deployment / 生产部署

```bash
export DASHBOARD_ENV=production
export DASHBOARD_API_KEY=your-secure-key
export CORS_ALLOWED_ORIGINS=https://your-domain.com
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

> **Warning / 警告**: Do NOT use `--workers N` with N > 1. The system relies on in-memory singletons for WebSocket state, caching, and pub/sub. Multi-worker will cause data duplication and connection issues.
>
> 不要使用 `--workers N`（N > 1）。系统依赖内存单例管理WebSocket状态、缓存和发布/订阅。多worker会导致数据重复和连接问题。

---

## Project Structure / 项目结构

```
crypto-options-aggregator/
├── dashboard/
│   ├── main.py                 # App entrypoint / 应用入口
│   ├── requirements.txt        # Python dependencies / Python依赖
│   ├── api/                    # API route handlers (13 files) / API路由处理
│   │   ├── risk.py             # Risk command center / 风险指挥中心
│   │   ├── llm_analyst.py      # LLM analyst endpoints / LLM研判端点
│   │   ├── paper_trading.py    # Paper trading / 模拟交易
│   │   ├── mcp.py              # MCP server / MCP服务
│   │   └── ...
│   ├── routers/                # Additional routers (5 files) / 附加路由
│   │   ├── maxpain.py          # Max pain & GEX / 最大痛点与GEX
│   │   ├── grid.py             # Grid strategy / 网格策略
│   │   └── ...
│   ├── services/               # Business logic (35 files) / 业务逻辑
│   │   ├── risk_framework.py   # Risk framework / 风险框架
│   │   ├── pressure_test.py    # Stress testing / 压力测试
│   │   ├── llm_analyst.py      # LLM analyst engine / LLM研判引擎
│   │   ├── datahub.py          # Pub/Sub data center / 数据中心
│   │   └── ...
│   ├── static/                 # Frontend assets (7 files) / 前端资源
│   │   ├── index.html          # SPA entry / SPA入口
│   │   ├── app.js              # Core logic / 核心逻辑
│   │   └── ...
│   ├── tests/                  # Test suite / 测试套件
│   │   ├── test_risk_math.py   # Math formula tests / 数学公式测试
│   │   ├── test_risk_api.py    # API integration tests / API集成测试
│   │   └── ...
│   ├── docs/                   # Design specs & plans / 设计文档与计划
│   └── db/                     # SQLite database / SQLite数据库
├── deribit-options-monitor/    # Deribit standalone monitor / Deribit独立监控
├── CHANGELOG.md                # Version history / 版本历史
└── README.md                   # This file / 本文件
```

---

## Contributing / 贡献指南

1. Fork → Branch (`feature/xxx`) → Commit → PR
2. Run `python main.py` and verify no regressions / 运行并验证无回归
3. Run tests / 运行测试: `cd dashboard && python -m pytest tests/ -v`
4. See [CHANGELOG.md](CHANGELOG.md) for version history / 查看版本历史

---

## License / 许可证

[MIT](LICENSE)
