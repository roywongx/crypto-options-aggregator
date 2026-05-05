<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Binance+Deribit-orange?logo=bitcoin" alt="Platform">
  <img src="https://img.shields.io/badge/Tests-163_passing-149e61" alt="Tests">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
</p>

<h1 align="center">Crypto Options Aggregator</h1>

<p align="center">
  Professional crypto options trading dashboard — Binance + Deribit real-time aggregation<br>
  专业加密货币期权交易仪表盘 — 币安 + Deribit 实时聚合
</p>

---

## Overview / 概述

A high-performance options trading terminal purpose-built for **Sell Put / Covered Call / Wheel strategy** traders. Aggregates real-time options chains from Binance and Deribit, scores every contract by risk-adjusted return (Margin-APR), and provides multi-layered decision support from deterministic rule engines to LLM-powered deep analysis.

为 Sell Put / Covered Call / Wheel 策略交易者打造的高性能期权交易终端。实时聚合币安和 Deribit 期权链，按风险调整收益率（保证金 APR）评分，提供从规则引擎到 LLM 深度分析的多层决策支持。

- **163 tests passing** · **47 services** · **14 API modules** · **17 analysis panels**
- **163 个测试通过** · **47 个服务模块** · **14 个 API 模块** · **17 个分析面板**

---

## Quick Start / 快速开始

```bash
git clone https://github.com/roywongx/crypto-options-aggregator.git
cd crypto-options-aggregator/dashboard
pip install -r requirements.txt
python main.py
```

Open **http://localhost:8000**

> Single-worker mode required — the system uses in-memory singletons for WebSocket connections and caching.
>
> 必须单 worker 运行 — 系统使用内存单例管理 WebSocket 连接和缓存。

---

## Architecture / 架构

```
Binance REST API  ──┐
                    ├── DataHub (Pub/Sub + WebSocket + memory cache)
Deribit WebSocket  ─┘        │
                              ▼
               ┌──────────────────────────────┐
               │       Compute Layer           │
               │                               │
               │  Scan Engine · Risk Framework │
               │  Strategy Calc · Greeks · IV  │
               │  DVOL · On-Chain · MaxPain    │
               └──────────────┬───────────────┘
                              │
                              ▼
               ┌──────────────────────────────┐
               │      Decision Layer           │
               │                               │
               │  17-Panel Rule Engine         │
               │  Unified Recommendation       │
               │  LLM Deep Analysis (SSE)      │
               │  Multi-Agent Debate           │
               └──────────────┬───────────────┘
                              │
                              ▼
            FastAPI + Static SPA (Chart.js + Tailwind CSS v4)
```

---

## Key Features / 核心功能

### Trading & Strategy / 交易与策略

| Feature | Description |
|---|---|
| **Dual-Platform Scan** | Binance + Deribit options chain with unified Margin-APR ranking |
| **Two-Layer Scanning** | Layer 1 quality filter (Delta/DTE/Volume/OI) + Layer 2 strategy scoring |
| **Strategy Engine** | Roll / New position / Grid modes with automated P&L and breakeven calculation |
| **Paper Trading** | 50K USDT simulation with real market data, margin freezing, position tracking |
| **Martingale Sandbox** | Interactive DCA simulation with configurable price path and strategy parameters |

### Risk & Analytics / 风控与分析

| Feature | Description |
|---|---|
| **Risk Command Center** | 4D assessment (price / volatility / sentiment / liquidity) with Gauge + Radar visualization |
| **Greeks Matrix** | Delta, Gamma, Vega, Theta, Rho across all positions with institutional-grade accuracy |
| **GEX Analysis** | Gamma Exposure curve, flip point detection, dealer positioning |
| **BS Stress Testing** | Multi-scenario joint stress of Delta, Gamma, Vanna, Volga |
| **DVOL Analyzer** | Deribit Volatility Index with Z-Score, percentile ranking, regime detection |
| **Max Pain** | Max pain price calculation with gamma exposure overlay |
| **IV Smile** | Skew metrics (25-delta), form classification (smile/skew/flat), curvature analysis |
| **IV Term Structure** | Hull-White calibration, variance risk premium, contango/backwardation detection |
| **PCR Chart** | Put/Call ratio tracking with extreme zone detection |
| **On-Chain Metrics** | MVRV-Z, NUPL, Mayer Multiple, Puell Multiple, 200WMA, Balanced Price |
| **Derivative Metrics** | Perp basis, OI-price divergence, funding volatility, liquidation heat, stablecoin reserves, futures/spot ratio, Sharpe |

### AI & Intelligence / AI 与智能

| Feature | Description |
|---|---|
| **Unified Recommendation Engine** | 17-panel signal lights with 3-tier progressive disclosure (badge → report → LLM drawer) |
| **LLM Deep Analysis** | Per-panel SSE streaming analysis with synthesis / bull debate / bear debate / judge audit |
| **AI Co-Pilot** | Built-in trading assistant with full market context injection |
| **LLM Analyst Center** | Comprehensive full-pipeline analysis (rules → synthesis → debate → audit) |
| **Multi-Model** | OpenAI-compatible API routing — DeepSeek, Claude, GPT, Gemini, or any proxy |
| **LLM Result Caching** | Input-hash indexed cache with force-refresh support |
| **Crypto-Native AI Framework** | 8 crypto-native metrics, hybrid thresholds (percentile + fixed), structural market context injection into LLM prompts |

### Infrastructure / 基础设施

| Feature | Description |
|---|---|
| **DataHub** | High-performance Pub/Sub with persistent WebSocket connections to both exchanges |
| **Multi-Exchange Abstraction** | Unified interface for Binance + Deribit, extensible to Bybit/OKX |
| **Background Task Manager** | Scheduled scanning, DVOL polling, on-chain updates, maintenance jobs |
| **SQLite WAL** | Async read + concurrent write, zero-config, auto-vacuum maintenance |
| **API Key Auth** | HMAC constant-time comparison, localhost bypass for development |
| **CORS Middleware** | Environment-aware CORS with production whitelist |

---

## Dashboard Panels / 仪表盘面板

All 17 panels feature the unified recommendation signal light + rule report + LLM deep analysis.

| Panel | Signal Formula | Key Metrics |
|---|---|---|
| **Market Metrics** | weighted_score | Spot, DVOL, Fear & Greed |
| **Risk Command Center** | weighted_score | Price risk, Vol risk, Sentiment, Liquidity |
| **Strategy Center** | weighted_score | APR quality, Delta quality, DTE quality |
| **Greeks Matrix** | weighted_score | Delta exposure, Gamma risk, Vega exposure |
| **AI Analyst Center** | weighted_score | Full pipeline status, agent consensus |
| **IV Term Structure** | majority | Contango depth, VRP, calendar spread quality |
| **IV Smile** | weighted_score | Skew (25d), Curvature, Vol regime |
| **DVOL Trend** | weighted_score | DVOL signal, Vol regime |
| **PCR Chart** | majority | PCR extreme, Fear & Greed sentiment |
| **Max Pain** | worst_case | Max pain magnet, Gamma flip risk |
| **Large Trades** | weighted_score | Direction bias, Flow sentiment |
| **Martingale Sandbox** | worst_case | DCA risk, Vol regime |
| **Opportunities Table** | weighted_score | Contract quality, APR, risk/reward |
| **GEX Chart** | weighted_score | Gamma structure, dealer positioning |
| **Money Flow** | weighted_score | Active buy/sell, Flow direction |
| **On-Chain Metrics** | weighted_score | MVRV-Z valuation, Holder behavior |
| **Derivative Metrics** | weighted_score | Perp basis, OI divergence, funding vol, liquidation heat, stablecoin flow |

---

## API Reference / API 参考

Interactive docs: **http://localhost:8000/docs**

### Recommendations / 统一推荐

| Endpoint | Method | Description |
|---|---|---|
| `/api/recommendation/{panel_id}` | GET | Panel rule recommendation (signal + report) |
| `/api/recommendation/{panel_id}/llm` | POST | Panel LLM deep analysis (SSE stream) |
| `/api/recommendations/summary` | GET | All-panel signal summary |
| `/api/recommendations/batch` | POST | Batch panel recommendations |

### Scan & Data / 扫描与数据

| Endpoint | Method | Description |
|---|---|---|
| `/api/latest` | GET | Latest scan results (cached) |
| `/api/quick-scan` | POST | Quick scan with custom parameters |
| `/api/scan` | POST | Full options chain scan |
| `/api/dashboard-init` | GET | Aggregated dashboard initialization data |
| `/api/refresh/{source}` | POST | Force-refresh data source (spot/dvol/macro) |

### Risk & Analytics / 风控与分析

| Endpoint | Method | Description |
|---|---|---|
| `/api/risk/overview` | GET | Risk command center data |
| `/api/risk/assess` | GET | Risk assessment with scores |
| `/api/risk/llm-insight` | GET | LLM risk analysis |

### Strategy / 策略

| Endpoint | Method | Description |
|---|---|---|
| `/api/strategy/recommend` | POST | Strategy recommendations |
| `/api/strategy/roll-plan` | POST | Roll strategy calculation |
| `/api/strategy/new-plan` | POST | New position calculation |
| `/api/sandbox/simulate` | POST | Martingale sandbox simulation |

### AI / AI

| Endpoint | Method | Description |
|---|---|---|
| `/api/llm-analyst/analyze` | POST | Full pipeline LLM analysis |
| `/api/llm-analyst/config` | GET/POST | Load/save LLM configuration |
| `/api/llm-analyst/test` | POST | Test LLM connection |
| `/api/llm-analyst/history` | GET | Analysis history |

### Trading / 交易

| Endpoint | Method | Description |
|---|---|---|
| `/api/paper/trade` | POST | Execute paper trade |
| `/api/paper/positions` | GET | Open paper positions |
| `/api/paper/history` | GET | Paper trading history |

> All endpoints except `/api/health` require `X-API-Key` header in production mode.
>
> 生产模式下所有端点（除 `/api/health`）需要 `X-API-Key` 请求头。

---

## Tech Stack / 技术栈

| Layer | Technology |
|---|---|
| **Backend** | Python 3.13 · FastAPI · uvicorn · httpx · websockets |
| **Frontend** | Vanilla JS (ES modules) · Chart.js · Tailwind CSS v4 (compiled) |
| **Database** | SQLite (WAL mode) with async read / concurrent write |
| **AI** | OpenAI-compatible SDK · SSE streaming · Input-hash caching |
| **Quant** | Black-Scholes · Institutional Greeks · IV Smile analysis · Hull-White |
| **Data** | Binance eAPI · Deribit WebSocket · CoinGecko · alternative.me · FRED |

---

## Production Deployment / 生产部署

```bash
export DASHBOARD_ENV=production
export DASHBOARD_API_KEY=<your-secure-key>
export CORS_ALLOWED_ORIGINS=https://your-domain.com
cd dashboard && python main.py
```

> **Never use `--workers N` with N > 1.** The system relies on in-memory singletons for WebSocket state, caching, and pub/sub. Multi-worker causes data duplication and connection issues.

---

## Project Structure / 项目结构

```
crypto-options-aggregator/
├── dashboard/
│   ├── main.py                         # FastAPI app entrypoint
│   ├── config.py                       # Runtime configuration
│   ├── requirements.txt                # Python dependencies
│   ├── tailwind-input.css              # Tailwind v4 CSS config
│   ├── api/                            # Route handlers (14 modules)
│   │   ├── recommendations.py          # Unified recommendation + LLM endpoints
│   │   ├── risk.py                     # Risk command center
│   │   ├── llm_analyst.py              # LLM analyst endpoints + config
│   │   ├── scan.py                     # Options scan
│   │   ├── strategy.py                 # Strategy calculation
│   │   ├── sandbox.py                  # Martingale sandbox
│   │   ├── paper_trading.py            # Paper trading
│   │   ├── mcp.py                      # MCP server
│   │   └── ...                         # dashboard, datahub, exchanges, health, macro, refresh
│   ├── services/                       # Business logic (44 modules)
│   │   ├── unified_recommendation_engine.py  # Signal + report + LLM prompt builder
│   │   ├── panel_analyzers.py          # 16 panel configs + rule functions + LLM templates
│   │   ├── scan_engine.py              # Two-layer options scan engine
│   │   ├── risk_framework.py           # Dynamic risk scoring
│   │   ├── llm_analyst.py              # Full pipeline LLM analysis engine
│   │   ├── ai_router.py                # Multi-model OpenAI-compatible routing
│   │   ├── datahub.py                  # Pub/Sub + WebSocket data center
│   │   ├── iv_smile.py                 # IV smile/skew/curvature analyzer
│   │   ├── greeks_analyzer.py          # Greeks computation + GEX analysis
│   │   └── ...                         # 35 more services
│   ├── routers/                        # Additional routers (5 modules)
│   │   ├── maxpain.py                  # Max pain + GEX
│   │   ├── grid.py                     # Grid strategy
│   │   ├── charts.py                   # Chart data
│   │   └── ...
│   ├── static/                         # Frontend (SPA)
│   │   ├── index.html                  # Single-page entry
│   │   ├── app.js                      # Core application logic
│   │   ├── recommendations.js          # Recommendation engine frontend
│   │   ├── utils.js                    # Shared utilities + safeFetch
│   │   ├── tailwind-output.css         # Compiled Tailwind CSS
│   │   ├── favicon.svg                 # Vector favicon
│   │   └── ...                         # maxpain.js, sandbox.js, grid-strategy.js, term-structure.js
│   ├── db/                             # Database layer
│   │   ├── connection.py               # SQLite connection pool
│   │   ├── schema.py                   # DDL + migrations
│   │   └── maintenance.py              # Retention + vacuum
│   ├── tests/                          # 141 tests
│   │   ├── test_unified_recommendation.py  # Recommendation engine tests
│   │   ├── test_risk_math.py           # Risk formula tests
│   │   ├── test_risk_api.py            # Risk API integration
│   │   └── ...
│   ├── docs/superpowers/               # Design specs + implementation plans
│   └── data/                           # SQLite database (monitor.db)
├── deribit-options-monitor/            # Standalone Deribit monitor
└── README.md
```

---

## Tests / 测试

```bash
cd dashboard
python -m pytest tests/ -v
# 163 passed
```

---

## License / 许可证

[MIT](LICENSE)
