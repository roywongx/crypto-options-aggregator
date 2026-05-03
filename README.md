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
  Built for <b>Sell Put / Covered Call / Wheel / Roll</b> strategy traders
</p>

---

## Features

- **Dual-Platform Aggregation** — Binance eAPI + Deribit WebSocket unified into a single options chain, sorted by Margin-APR
- **Real-Time Scanning** — Background engine continuously scans all options contracts, auto-filters by Delta, DTE, liquidity
- **Strategy Engine** — Roll / New / Grid modes with automated P&L, breakeven, and margin calculations
- **Risk Framework** — 4-dimensional assessment (price, volatility, sentiment, liquidity) with Black-Scholes stress testing
- **AI Co-Pilot** — Built-in trading assistant with market context injection (DVOL, Fear/Greed, funding rate, large trades)
- **Paper Trading** — 50K USDT simulation with real market data, margin freezing, and position tracking

## Quick Start

```bash
git clone https://github.com/roywongx/crypto-options-aggregator.git
cd crypto-options-aggregator/dashboard
pip install -r requirements.txt
python main.py
```

Open **http://localhost:8000** in your browser.

> **Important**: Must use single-worker mode (`--workers 1`). The system uses in-memory singletons for WebSocket connections and caching — multi-worker will cause duplicate connections and doubled memory usage.

## Architecture

```
Binance API / Deribit WebSocket
        │
        ▼
   DataHub (WebSocket + memory cache + Pub/Sub)
        │
        ▼
  ┌─────┴─────┐
  │  Compute   │  Scan Engine · Risk Framework · Strategy Calc · Quant Engine
  └─────┬─────┘
        │
        ▼
  ┌─────┴─────┐
  │  Decision  │  5-Agent Debate · AI Co-Pilot · MCP Server
  └─────┬─────┘
        │
        ▼
  FastAPI + Static Frontend (Chart.js)
```

## API Reference

Full interactive docs at **http://localhost:8000/docs**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/latest` | GET | Latest scan results |
| `/api/quick-scan` | POST | Quick options scan |
| `/api/risk/overview` | GET | Risk assessment |
| `/api/debate/analyze` | POST | Multi-agent debate analysis |
| `/api/copilot/chat` | POST | AI Co-Pilot conversation |
| `/api/paper/trade` | POST | Paper trading |
| `/api/dashboard-init` | GET | Aggregated dashboard data |

All endpoints (except `/api/health`) require `X-API-Key` header in production.

## Tech Stack

**Backend**: Python 3.13 · FastAPI · SQLite (WAL + async) · httpx · websockets · LiteLLM
**Frontend**: Vanilla JS · Chart.js · Tailwind CSS
**AI**: Multi-model routing via LiteLLM (Claude / GPT / DeepSeek / Gemini)

## Production Deployment

```bash
export DASHBOARD_ENV=production
export DASHBOARD_API_KEY=your-secure-key
export CORS_ALLOWED_ORIGINS=https://your-domain.com
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

## Contributing

1. Fork → Branch (`feature/xxx`) → Commit → PR
2. Run `python main.py` and verify no regressions
3. See [CHANGELOG.md](CHANGELOG.md) for version history

## License

[MIT](LICENSE)
