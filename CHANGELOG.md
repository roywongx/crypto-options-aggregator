# Changelog

## v5.7 — Code Quality & Security Hardening

### Security Fixes
- **X-Forwarded-For spoofing removed** — API key auth no longer trusts client-supplied `X-Forwarded-For` headers, preventing IP-based auth bypass
- **Timing-safe API key comparison** — Switched from `!=` to `hmac.compare_digest` to prevent timing attacks
- **Global SSL warning suppression removed** — `urllib3.disable_warnings()` in `onchain_metrics.py` removed; warnings are now scoped

### Bug Fixes
- **sandbox.py import error** — Replaced non-existent `db.repository` import with `scan_records` table query
- **refresh_dvol blocking event loop** — Wrapped synchronous `get_dvol_from_deribit` call with `run_in_threadpool`
- **maintenance.py datetime type mismatch** — `cleanup_old_records` now converts `datetime` to string before SQL comparison
- **f-string in logger** — `onchain_metrics.py` error logging changed from f-string to `%s` format

### Performance
- **dashboard-init parallelized** — 4 sequential `asyncio.to_thread` calls replaced with `asyncio.gather` (~3x faster)

### Resource Management
- **ThreadPoolExecutor singleton** — `UnifiedRiskAssessor` now reuses a module-level executor instead of creating one per call
- **Sync HTTP client cleanup** — Added `close_sync_client()` to lifespan shutdown, fixing connection pool leak
- **Health check deduplication** — Removed duplicate `/api/health` endpoint from `routers/status.py`

### Code Deduplication
- **norm_cdf unified** — 6 duplicate implementations across `dvol_analyzer`, `options_debate_engine`, `pressure_test`, `grid_engine`, `payoff_calculator` now all import from `shared_calculations`
- **Max Pain calculation** — `api/risk.py` now delegates to `routers/maxpain.py` instead of duplicating 100 lines

### Dependencies
- Added `websockets>=12.0` to `requirements.txt`

---

## v5.6 — Large Trade Data Aggregation Fix

### Core Fixes
- **Large trade buy/sell aggregation** — Fixed same-instrument buy+sell data loss when non-dominant direction was discarded
  - `services/large_trades_fetcher.py` — `_enrich_from_api` now accumulates buy/sell data instead of overwriting
  - New fields: `buy_notional`, `sell_notional`, `buy_count`, `sell_count`
- **AI Debate Flow Analyst** — Fixed "100% buy/sell ratio" display bug
  - `services/options_debate_engine.py` — `_flow_analyst` uses `buy_notional`/`sell_notional` instead of `direction`+`notional_usd`
  - `_bear_analyst` PCR calculation fixed (buy_put/buy_call instead of buy_put/sell_put)
- **Wind analysis data consistency** — `_fetch_wind_analysis` switched from OI data to `large_trades_history` actual trade data
- **Wind Analysis API** — `trades_api.py` uses notional value instead of trade count for ratio calculation
- **AI Sentiment** — `ai_sentiment.py` uses notional value instead of trade count for Put/Call ratio
- **Frontend totalNotional** — `app.js` uses API-returned `total_notional` instead of computing from distribution array

### Exception Handling
- Fixed `httpx` import error in `large_trades_fetcher.py` exception handler

---

## v5.5 — Greeks Calculation & Async DB Fixes

### Core Fixes
- **Greeks calculation** — Real-time Black-Scholes Greeks (Deribit API doesn't return Greeks)
- **Greeks OI weighting** — Risk matrix uses Open Interest-weighted Greeks for accurate risk exposure
- **Async DB calls** — Fixed 5 API endpoints with synchronous DB calls blocking event loop
  - `api/scan.py` — `get_latest` / `export_csv`
  - `api/refresh.py` — `refresh_dvol`
  - `api/debate.py` — `get_debate_history`
  - `routers/charts.py` — `get_vol_surface`

### Frontend
- **IV Smile rendering** — Fixed bar chart height calculation causing blank canvas
- **Greeks/IV Smile tooltips** — Added indicator descriptions and legends

---

## v5.4 — AI Configuration Enhancement

### AI Co-Pilot
- **Custom API Key** — Frontend AI config panel supports any OpenAI-compatible API key
- **Custom Base URL** — Support for self-hosted proxies or third-party services
- **Custom Model** — Free-form model name input with smart suggestions
- **Config persistence** — API config saved to localStorage, survives page refresh
- **CORS fix** — Added `X-AI-API-Key`, `X-AI-Base-URL`, `X-AI-Model` to CORS allow list
- **Exception handling** — Catches `AuthenticationError`, `BadRequestError`, `RateLimitError` from LiteLLM
- **Model prefix auto-complete** — Custom APIs auto-prepend `openai/` prefix

### Frontend
- AI chat window enlarged (320px → 480px width, 320px → 500px height)
- Message font size increased (12px → 14px)

---

## v5.3 — Architecture Debt Cleanup

### Architecture
- **HTTP library unified** — Full codebase migrated from `requests` to `httpx`, new `services/http_client.py` with unified sync/async clients
- **Code deduplication** — Extracted `large_trades_fetcher.py` from `scan_engine.py`, eliminating 140 lines of async/sync duplicate code
- **Exception handling** — Replaced 186 bare `except Exception` with specific types (`httpx.HTTPError`, `ValueError`, `KeyError`, etc.)
- **DB config unified** — `DASHBOARD_DB_PATH` env var unifies dashboard and Deribit monitor database paths
- **JSON1 index** — Added `top_apr` and `contracts_count` virtual columns + indexes for JSON blob queries

### Fixes
- **IV Term Structure v2.0** — New `/api/charts/vol-surface` endpoint, ATM IV term structure from DB contract data
- **IV unit normalization** — Auto-detect and unify IV units (decimal 0.42 → percentage 42%)
- **SQLite compatibility** — Fixed `ALTER TABLE ADD COLUMN IF NOT EXISTS` syntax (unsupported in SQLite)
- **Lifespan fix** — Fixed `UnboundLocalError: logger` in lifespan handler
- **DVOL outlier filter** — Fixed outlier value 50 in DVOL trend chart

### Dependencies
- Removed `requests`, unified on `httpx>=0.27.0`

---

## v5.2 — Stability & Security Overhaul

### Frontend
- **Auto-refresh protection** — `_refreshInFlight` lock prevents concurrent request UI race conditions
- **Error handling** — `safeFetch()` reads backend JSON detail for precise HTTP error messages
- **API failure feedback** — DVOL/Stats/Wind/PCR show "load failed / data expired" instead of stale data
- **Time parsing** — New `parseUTC()` function unifies T-space ISO time formats, eliminates 8-hour timezone offset
- **PCR chart fix** — Destroy old `_pcrChart` before showing empty state
- **Term structure sort** — Sort by dte ascending before computing front/back, prevents contango/backwardation misclassification
- **CSS selector safety** — `CSS.escape()` on `presetId` before entering selectors
- **CSP compliance** — Migrated all inline onclick to `addEventListener`

### Backend
- **IV unit unification** — Removed double `/100` in `quick_scan` and `calc_delta_bs`
- **API Key injection** — Frontend `safeFetch()` auto-injects `X-API-Key`
- **CORS fix** — Replaced custom middleware with FastAPI `CORSMiddleware`
- **Health check fix** — Fixed `config` import path resolving `SCAN_INTERVAL_SECONDS`
- **DataHub startup** — Started in `lifespan`, graceful shutdown with WebSocket cache optimization
- **Circular import** — Moved `quick_scan`/`run_options_scan` to `services/scan_engine.py`
- **DB indexes** — Added composite indexes for large trade queries
- **Transaction atomicity** — Paper trading open/trade/account updates in single transaction
- **Margin freeze** — Paper trading adds `locked_margin` to prevent over-leveraging
- **Event loop protection** — `mcp_chat()` uses `run_in_threadpool` for sync `ai_chat()`
- **Connection pool cleanup** — `lifespan` shutdown calls `close_async_client()`
- **DB init optimization** — Paper trading DB init moved to startup phase
- **Parameter validation** — `trades_api` adds `days` (le=90) and `limit` (le=500) bounds

---

## v5.1 — Bug Fixes & Stability

- Fixed strategy calculation returning empty plans (asyncio event loop conflict)
- Added spot price anomaly protection
- Fixed division-by-zero risk with parameter validation
- Risk Overview enhanced with Put Wall / Gamma Flip / Max Pain data

---

## v5.0 — Progressive Refactoring

- API modularization: 2600+ line `main.py` split into 15 `api/` modules
- DataHub real-time data center: WebSocket replaces polling
- Exchange Abstraction Layer: Unified exchange interface
- Paper Trading Engine: Continuous simulation
- MCP Server + AI Co-Pilot: Intelligent trading assistant
