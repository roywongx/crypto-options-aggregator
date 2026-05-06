# Scan Parameters Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement two-layer scan architecture (full data for analytics + filtered/scored for strategy) with DVOL-adaptive parameters calibrated to real data.

**Architecture:** Single `fetch_deribit_summaries()` call feeds two paths — quality-filtered full data stored to `contracts_data` for IV Smile/Greeks/Vol Surface, and DVOL-adaptive filtered+scored data stored to `top_contracts_data` for strategy recommendations. No schema changes, no new dependencies.

**Tech Stack:** Python 3.13, SQLite, existing httpx/Deribit API

---

### Task 1: config.py — DVOL profiles, thresholds, presets, retention

**Files:**
- Modify: `dashboard/config.py`

- [ ] **Step 1: Add DVOL_PROFILES and update DVOL thresholds**

Replace lines 119-123 (the `# === DVOL 阈值配置 ===` block) in `dashboard/config.py`:

```python
        # === DVOL 阈值配置 ===
        self.DVOL_PANIC_THRESHOLD = _get_env("DVOL_PANIC_THRESHOLD", 80, env)
        self.DVOL_HIGH_THRESHOLD = _get_env("DVOL_HIGH_THRESHOLD", 70, env)
        self.DVOL_LOW_THRESHOLD = _get_env("DVOL_LOW_THRESHOLD", 50, env)
        self.DVOL_Z_HIGH = _get_env("DVOL_Z_HIGH", 2.0, env)
        self.DVOL_Z_MID = _get_env("DVOL_Z_MID", 1.0, env)

        # === DVOL 自适应参数档位 ===
        self.DVOL_PROFILES = {
            "low":  {"max_delta": 0.35, "min_dte": 21, "max_dte": 45, "min_apr": 10.0, "margin_ratio": 0.18},
            "mid":  {"max_delta": 0.30, "min_dte": 14, "max_dte": 35, "min_apr": 15.0, "margin_ratio": 0.20},
            "high": {"max_delta": 0.20, "min_dte": 7,  "max_dte": 21, "min_apr": 25.0, "margin_ratio": 0.22},
        }
```

- [ ] **Step 2: Add retention config**

After line 95 (`DATA_RETENTION_DAYS`), add:

```python
        self.CONTRACTS_DATA_RETENTION_DAYS = _get_env("CONTRACTS_DATA_RETENTION_DAYS", 7, env)
        self.TOP_CONTRACTS_RETENTION_DAYS = _get_env("TOP_CONTRACTS_RETENTION_DAYS", 30, env)
```

- [ ] **Step 3: Update STRATEGY_PRESETS**

Replace lines 154-165 (the `STRATEGY_PRESETS` dict) in `dashboard/config.py`:

```python
        self.STRATEGY_PRESETS = {
            "PUT": {
                "conservative": {"max_delta": 0.20, "min_dte": 30, "max_dte": 45, "margin_ratio": 0.18, "min_apr": 12.0},
                "standard":     {"max_delta": 0.30, "min_dte": 14, "max_dte": 35, "margin_ratio": 0.20, "min_apr": 15.0},
                "aggressive":   {"max_delta": 0.35, "min_dte": 7,  "max_dte": 28, "margin_ratio": 0.22, "min_apr": 20.0}
            },
            "CALL": {
                "conservative": {"max_delta": 0.15, "min_dte": 30, "max_dte": 45, "margin_ratio": 0.18, "min_apr": 10.0},
                "standard":     {"max_delta": 0.25, "min_dte": 14, "max_dte": 35, "margin_ratio": 0.20, "min_apr": 12.0},
                "aggressive":   {"max_delta": 0.30, "min_dte": 7,  "max_dte": 28, "margin_ratio": 0.22, "min_apr": 18.0}
            }
        }
```

- [ ] **Step 4: Verify config loads**

Run: `python -c "from dashboard.config import config; print(config.DVOL_PROFILES); print(config.DVOL_LOW_THRESHOLD); print(config.STRATEGY_PRESETS)"`
Expected: Prints profile dict, 50, updated presets.

- [ ] **Step 5: Commit**

```bash
git add dashboard/config.py
git commit -m "feat: add DVOL_PROFILES, fix thresholds, update strategy presets"
```

---

### Task 2: models/contracts.py — widen scan parameter defaults

**Files:**
- Modify: `dashboard/models/contracts.py`

- [ ] **Step 1: Widen QuickScanParams defaults**

Replace lines 30-34 in `dashboard/models/contracts.py`:

```python
class QuickScanParams(BaseModel):
    currency: str = Field(default="BTC", pattern="^(BTC|ETH|SOL|XRP)$")
    min_dte: int = Field(default=1, ge=1, le=365)
    max_dte: int = Field(default=90, ge=1, le=365)
    max_delta: float = Field(default=0.99, ge=0.01, le=1.0)
    margin_ratio: float = Field(default=0.2, ge=0.05, le=1.0)
    option_type: str = Field(default="ALL", pattern="^(PUT|CALL|ALL|BOTH)$")
    strike: Optional[float] = Field(default=None, gt=0)
    strike_range: Optional[str] = Field(default=None)
```

- [ ] **Step 2: Widen ScanParams defaults**

Replace lines 7-9 in `dashboard/models/contracts.py`:

```python
class ScanParams(BaseModel):
    currency: str = Field(default="BTC", description="币种")
    min_dte: int = Field(default=1, ge=1, le=365, description="最小到期天数")
    max_dte: int = Field(default=90, ge=1, le=365, description="最大到期天数")
    max_delta: float = Field(default=0.99, ge=0.01, le=1.0, description="最大Delta")
```

- [ ] **Step 3: Verify models import**

Run: `python -c "from dashboard.models.contracts import QuickScanParams, ScanParams; q = QuickScanParams(); print(q.min_dte, q.max_dte, q.max_delta); s = ScanParams(); print(s.max_dte)"`
Expected: `1 90 0.99` and `90`.

- [ ] **Step 4: Commit**

```bash
git add dashboard/models/contracts.py
git commit -m "feat: widen scan defaults to DTE 1-90, delta 0.99"
```

---

### Task 3: dvol_analyzer.py — fix DVOL thresholds in adapt_params_by_dvol

**Files:**
- Modify: `dashboard/services/dvol_analyzer.py:131-162`

- [ ] **Step 1: Replace adapt_params_by_dvol with profile-based lookup**

Replace lines 131-162 in `dashboard/services/dvol_analyzer.py`:

```python
def adapt_params_by_dvol(params: dict, dvol_raw: dict) -> dict:
    """根据 DVOL 信号调整交易参数（使用 DVOL_PROFILES 三档查表）"""
    from config import config

    dvol = dvol_raw.get("current", 50)
    z_score = dvol_raw.get("z_score", 0)

    if dvol > config.DVOL_HIGH_THRESHOLD:
        regime = "high"
    elif dvol < config.DVOL_LOW_THRESHOLD:
        regime = "low"
    else:
        regime = "mid"

    profile = config.DVOL_PROFILES[regime]
    adapted = {**params, **{k: v for k, v in profile.items() if k in params or k in ("max_delta", "min_dte", "max_dte", "min_apr", "margin_ratio")}}

    # Z-score 极端值额外调整
    if abs(z_score) > 2:
        if z_score > 0:
            adapted["max_delta"] = min(adapted.get("max_delta", 0.3), 0.20)
        else:
            adapted["max_delta"] = max(adapted.get("max_delta", 0.3), 0.40)

    return adapted
```

- [ ] **Step 2: Verify adapt_params_by_dvol with test values**

Run: `python -c "from dashboard.services.dvol_analyzer import adapt_params_by_dvol; print(adapt_params_by_dvol({'max_delta': 0.4}, {'current': 40, 'z_score': 0})); print(adapt_params_by_dvol({'max_delta': 0.4}, {'current': 75, 'z_score': 0}))"`
Expected: low regime (max_delta=0.35), high regime (max_delta=0.20).

- [ ] **Step 3: Commit**

```bash
git add dashboard/services/dvol_analyzer.py
git commit -m "fix: replace dead dvol<30 branch with DVOL_PROFILES lookup"
```

---

### Task 4: risk_framework.py — fix scoring formulas

**Files:**
- Modify: `dashboard/services/risk_framework.py:137-156` (weighted_score)
- Modify: `dashboard/services/risk_framework.py:76-87` (get_score_modifier)

- [ ] **Step 1: Fix weighted_score IV rank and APR normalization**

Replace lines 137-156 in `dashboard/services/risk_framework.py`:

```python
    @staticmethod
    def weighted_score(apr: float, pop: float, breakeven_pct: float,
                       liquidity_score: float, iv_rank: float,
                       strike: float = 0, spot: float = 0) -> float:
        a = min(max(apr, 0) / 100.0, 1.0)
        p = min(max(pop, 0), 1.0)
        b = min(max(breakeven_pct, 0) / config.CALC_BREAKEVEN_MAX, 1.0)
        l = min(max(liquidity_score, 0) / config.CALC_LIQUIDITY_MAX, 1.0)
        ir = max(iv_rank, 0)
        iv = 0.5 + (ir - 50) / 100.0

        score = (a * config.CALC_WEIGHT_APR +
                 p * config.CALC_WEIGHT_POP +
                 b * config.CALC_WEIGHT_BREAKEVEN +
                 l * config.CALC_WEIGHT_LIQUIDITY +
                 iv * config.CALC_WEIGHT_IV)

        if spot > 0 and strike > 0:
            score *= RiskFramework.get_score_modifier(strike, spot)

        return round(score, 4)
```

- [ ] **Step 2: Fix get_score_modifier risk direction**

Replace lines 76-87 in `dashboard/services/risk_framework.py`:

```python
    @classmethod
    def get_score_modifier(cls, strike: float, spot: float) -> float:
        floors = cls._get_floors()
        extreme = floors["extreme"]
        regular = floors["regular"]

        if strike <= extreme:
            return 0.70
        elif strike <= regular:
            return 0.85
        elif strike > spot:
            return 0.80
        return 1.0
```

- [ ] **Step 3: Run existing tests**

```bash
python -m pytest dashboard/tests/ -x -q --tb=short 2>&1 | tail -20
```
Expected: All tests pass (or only pre-existing failures).

- [ ] **Step 4: Commit**

```bash
git add dashboard/services/risk_framework.py
git commit -m "fix: seller-perspective IV rank, APR cap 100, risk modifier direction"
```

---

### Task 5: unified_risk_assessor.py — align DVOL tier thresholds

**Files:**
- Modify: `dashboard/services/unified_risk_assessor.py:84-93`

- [ ] **Step 1: Update _assess_volatility_risk DVOL tiers**

Replace lines 84-93 in `dashboard/services/unified_risk_assessor.py`:

```python
            if dvol > 80:
                score = 90
            elif dvol > 70:
                score = 70
            elif dvol > 50:
                score = 40
            else:
                score = 20
```

- [ ] **Step 2: Verify import**

Run: `python -c "from dashboard.services.unified_risk_assessor import UnifiedRiskAssessor; print('OK')"`
Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add dashboard/services/unified_risk_assessor.py
git commit -m "fix: align DVOL tiers with calibrated 50/70 thresholds"
```

---

### Task 6: options_debate_engine.py — fix DVOL thresholds in agents

**Files:**
- Modify: `dashboard/services/options_debate_engine.py:302-308`

- [ ] **Step 1: Fix _bull_analyst DVOL thresholds**

Replace lines 302-308 in `dashboard/services/options_debate_engine.py`:

```python
    dvol_val = dvol.get("current", 50)
    if dvol_val > 70:
        score += 10
        points.append(f"DVOL {dvol_val:.0f}% 偏高，权利金溢价利于卖方")
    elif dvol_val < 50:
        score -= 5
        points.append(f"DVOL {dvol_val:.0f}% 偏低，权利金收益有限")
```

- [ ] **Step 2: Find and fix _bear_analyst DVOL thresholds**

Search for `dvol_val` in `_bear_analyst` function (around lines 318-450 in `dashboard/services/options_debate_engine.py`). Replace any `dvol_val > 60` with `dvol_val > 70` and `dvol_val < 30` with `dvol_val < 50`.

- [ ] **Step 3: Verify**

Run: `python -c "from dashboard.services.options_debate_engine import _bull_analyst; print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add dashboard/services/options_debate_engine.py
git commit -m "fix: align debate agent DVOL thresholds 30/60→50/70"
```

---

### Task 7: scan_engine.py — core two-layer scan refactoring

**Files:**
- Modify: `dashboard/services/scan_engine.py`

- [ ] **Step 1: Add get_dvol_profile helper**

Add after the existing imports (after line 28) in `dashboard/services/scan_engine.py`:

```python
def _get_dvol_profile(dvol_current: float) -> dict:
    """根据 DVOL 当前值返回参数档位"""
    if dvol_current > config.DVOL_HIGH_THRESHOLD:
        return config.DVOL_PROFILES["high"]
    elif dvol_current < config.DVOL_LOW_THRESHOLD:
        return config.DVOL_PROFILES["low"]
    return config.DVOL_PROFILES["mid"]
```

- [ ] **Step 2: Add full_scan() function**

Add before `quick_scan()` (before line ~250) in `dashboard/services/scan_engine.py`:

```python
def _apply_quality_filter(contracts: list, spot: float) -> list:
    """质量过滤：OI>=10, IV>0, spread<25% — 不做 DTE/Delta 限制"""
    filtered = []
    for s in contracts:
        meta = _parse_inst_name(s.get("instrument_name", ""))
        if not meta or meta.dte < 1:
            continue
        iv = float(s.get("mark_iv") or 0)
        oi = float(s.get("open_interest") or 0)
        if iv <= 0 or oi < 10:
            continue
        strike = meta.strike
        underlying = float(s.get("underlying_price", spot)) or spot
        raw_delta = s.get("delta")
        if raw_delta is None or float(raw_delta or 0) == 0:
            delta_val = abs(calc_delta_bs(strike, underlying, iv, meta.dte, meta.option_type))
        else:
            delta_val = abs(float(raw_delta))
        prem = float(s.get("mark_price") or 0)
        prem_usd = prem * underlying
        dist = abs(strike - spot) / spot * 100
        margin_ratio = 0.2
        cv = strike * margin_ratio
        apr = (prem_usd / cv) * (365 / meta.dte) * 100 if cv > 0 else 0
        bs_greeks = black_scholes_price(meta.option_type, strike, underlying, meta.dte, iv)
        filtered.append({
            "symbol": s.get("instrument_name", ""),
            "platform": "Deribit",
            "expiry": meta.expiry,
            "dte": meta.dte,
            "option_type": meta.option_type,
            "strike": strike,
            "apr": round(apr, 1),
            "premium_usd": round(prem_usd, 2),
            "delta": round(delta_val, 3),
            "theta": round(bs_greeks["theta"], 4),
            "gamma": round(bs_greeks["gamma"], 6),
            "vega": round(bs_greeks["vega"], 4),
            "iv": round(iv, 1),
            "open_interest": round(oi, 0),
            "distance_spot_pct": round(dist, 1),
        })
    return filtered


def _apply_strategy_filter(contracts: list, dvol_current: float, spot: float) -> list:
    """策略过滤：DVOL 自适应 DTE/Delta + 评分排序"""
    profile = _get_dvol_profile(dvol_current)
    max_delta = profile["max_delta"]
    min_dte = profile["min_dte"]
    max_dte = profile["max_dte"]

    filtered = []
    for c in contracts:
        if c["dte"] < min_dte or c["dte"] > max_dte:
            continue
        if c["delta"] > max_delta:
            continue
        filtered.append(c)

    for c in filtered:
        c["_score"] = CalculationEngine.weighted_score(
            apr=c.get("apr", 0),
            pop=calc_pop(c["delta"], c["option_type"], spot, c["strike"], c["iv"], c["dte"]),
            breakeven_pct=c.get("distance_spot_pct", 0),
            liquidity_score=min(100, int((c.get("open_interest", 0) / 500) * 100)),
            iv_rank=50,
            strike=c["strike"],
            spot=spot
        )

    filtered.sort(key=lambda x: x.get("_score", 0), reverse=True)
    return filtered
```

- [ ] **Step 3: Refactor quick_scan() storage logic**

In `quick_scan()` (around lines 539-560 in `dashboard/services/scan_engine.py`), replace the scoring/sorting/storage section. Find the block starting with `all_c = sorted(contracts, ...)` through `execute_transaction(stmts)`:

```python
    # Two-layer: quality-filter all contracts + strategy-filter top 30
    quality_contracts = _apply_quality_filter(summaries, spot)
    strategy_contracts = _apply_strategy_filter(quality_contracts, dvol_current, spot)

    large_trades_count = len(large_trades)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    _raw_out = _sanitize_raw_output(dvol_data)

    stmts = []
    stmts.append(("""
        INSERT INTO scan_records (timestamp, currency, spot_price, dvol_current, dvol_z_score,
            dvol_signal, large_trades_count, large_trades_details, contracts_data, top_contracts_data, raw_output)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (timestamp, currency, spot, dvol_current, dvol_z, dvol_signal, large_trades_count,
          json.dumps(large_trades[:20]), json.dumps(quality_contracts), json.dumps(strategy_contracts[:30]), _raw_out)))

    # Write dvol_history
    stmts.append(("""
        INSERT INTO dvol_history (timestamp, currency, current, z_score, signal, trend)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (timestamp, currency, dvol_current, dvol_z, dvol_signal, dvol_data.get("trend", ""))))

    if large_trades and isinstance(large_trades, list):
        for trade in large_trades:
            parsed = parse_trade_alert(trade, currency, timestamp)
            stmts.append(("""
                INSERT INTO large_trades_history
                (timestamp, currency, source, title, message, direction, strike, volume,
                 option_type, flow_label, notional_usd, delta, instrument_name, premium_usd, severity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                parsed['timestamp'], parsed['currency'], parsed['source'],
                parsed['title'], parsed['message'], parsed['direction'],
                parsed['strike'], parsed['volume'], parsed['option_type'],
                parsed['flow_label'], parsed['notional_usd'], parsed['delta'],
                parsed['instrument_name'], parsed.get('premium_usd', 0), parsed.get('severity', '')
            )))

    execute_transaction(stmts)

    return {
        "success": True,
        "contracts_count": len(quality_contracts),
        "strategy_count": len(strategy_contracts[:30]),
        "spot_price": spot,
        "timestamp": timestamp,
        "contracts": strategy_contracts[:30],
        "dvol_current": dvol_current,
        "dvol_z_score": dvol_z,
        "dvol_signal": dvol_signal,
        "dvol_trend": dvol_data.get("trend", ""),
        "dvol_trend_label": dvol_data.get("trend_label", ""),
        "dvol_confidence": dvol_data.get("confidence", ""),
        "dvol_interpretation": dvol_data.get("interpretation", ""),
        "dvol_percentile_7d": dvol_data.get("percentile_7d", None),
        "large_trades_count": large_trades_count,
        "large_trades_details": large_trades[:20]
    }
```

Note: Remove the old `_normalize_liquidity`, `_weighted_score`, and the old `all_c`/`deribit_list`/`binance_list`/`contracts` interleaving logic — it's replaced by `_apply_strategy_filter`.

- [ ] **Step 4: Run existing scan tests**

```bash
python -m pytest dashboard/tests/ -x -q --tb=short 2>&1 | tail -30
```
Expected: Import succeeds, tests pass or only pre-existing failures.

- [ ] **Step 5: Commit**

```bash
git add dashboard/services/scan_engine.py
git commit -m "feat: two-layer scan with full_scan + strategy_scan, dvol_history write"
```

---

### Task 8: constants.py — dynamic spot fallback

**Files:**
- Modify: `dashboard/constants.py:11-14`

- [ ] **Step 1: Make spot fallback use DB last-known price**

Replace lines 6-14 in `dashboard/constants.py`:

```python
import os

def _get_fallback_from_db(currency: str) -> float:
    """Try to get last known spot price from scan_records."""
    try:
        from db.connection import execute_read
        rows = execute_read(
            "SELECT spot_price FROM scan_records WHERE currency=? AND spot_price>0 ORDER BY timestamp DESC LIMIT 1",
            (currency,)
        )
        if rows and rows[0][0]:
            return float(rows[0][0])
    except (ValueError, TypeError, RuntimeError):
        pass
    return 0

DEFAULT_SPOT_FALLBACK = {
    "BTC": float(os.getenv("DASHBOARD_SPOT_BTC", "0") or _get_fallback_from_db("BTC") or 83000),
    "ETH": float(os.getenv("DASHBOARD_SPOT_ETH", "0") or _get_fallback_from_db("ETH") or 3500),
    "SOL": float(os.getenv("DASHBOARD_SPOT_SOL", "0") or _get_fallback_from_db("SOL") or 150),
}
```

- [ ] **Step 2: Verify import**

Run: `python -c "from dashboard.constants import DEFAULT_SPOT_FALLBACK; print(DEFAULT_SPOT_FALLBACK)"`
Expected: Prints dict with current BTC/ETH/SOL values.

- [ ] **Step 3: Commit**

```bash
git add dashboard/constants.py
git commit -m "fix: dynamic spot fallback from DB before hardcoded default"
```

---

### Task 9: db/maintenance.py — layered retention cleanup

**Files:**
- Modify: `dashboard/db/maintenance.py`

- [ ] **Step 1: Add layered cleanup function**

Add after `cleanup_old_records` (after line 52) in `dashboard/db/maintenance.py`:

```python
def cleanup_contracts_data(conn: sqlite3.Connection, days: int = 7) -> dict:
    """将超过指定天数的 contracts_data 置为 NULL 以释放空间，保留行和其他字段"""
    cursor = conn.cursor()
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        "UPDATE scan_records SET contracts_data = NULL WHERE timestamp < ? AND contracts_data IS NOT NULL",
        (cutoff_date,)
    )
    nulled = cursor.rowcount

    conn.commit()

    return {
        "contracts_data_nulled": nulled,
        "cutoff_date": cutoff_date,
        "message": f"NULLed contracts_data older than {days} days: {nulled} rows"
    }
```

- [ ] **Step 2: Verify maintenance functions import**

Run: `python -c "from dashboard.db.maintenance import cleanup_contracts_data; print('OK')"`
Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add dashboard/db/maintenance.py
git commit -m "feat: layered retention — contracts_data NULL after 7 days"
```

---

### Task 10: Integration verification

**Files:**
- No changes, verification only.

- [ ] **Step 1: Run full test suite**

```bash
cd dashboard && python -m pytest tests/ -x -q --tb=short 2>&1 | tail -30
```
Expected: All tests pass.

- [ ] **Step 2: Verify dashboard imports**

```bash
python -c "
from dashboard.services.scan_engine import _apply_quality_filter, _apply_strategy_filter, _get_dvol_profile
from dashboard.services.dvol_analyzer import adapt_params_by_dvol
from dashboard.services.risk_framework import CalculationEngine, RiskFramework
from dashboard.config import config
print('DVOL_PROFILES:', list(config.DVOL_PROFILES.keys()))
print('LOW_THRESHOLD:', config.DVOL_LOW_THRESHOLD)
print('HIGH_THRESHOLD:', config.DVOL_HIGH_THRESHOLD)
print('All imports OK')
"
```
Expected: Prints profile keys, thresholds, "All imports OK".

- [ ] **Step 3: Verify scoring formulas with known inputs**

```bash
python -c "
from dashboard.services.risk_framework import CalculationEngine
# High IV (80), good APR (50%), good POP (0.85)
s1 = CalculationEngine.weighted_score(apr=50, pop=0.85, breakeven_pct=15, liquidity_score=80, iv_rank=80, strike=60000, spot=65000)
# Low IV (20), low APR (10%), moderate POP (0.70)
s2 = CalculationEngine.weighted_score(apr=10, pop=0.70, breakeven_pct=5, liquidity_score=50, iv_rank=20, strike=60000, spot=65000)
print(f'High IV score: {s1:.4f}')
print(f'Low IV score: {s2:.4f}')
print(f'High > Low: {s1 > s2}')
"
```
Expected: `High > Low: True` (high IV score should beat low IV score).

- [ ] **Step 4: Manual verification checklist**

1. Start dashboard: `cd dashboard && python main.py`
2. Wait for first scan cycle (~30s)
3. Check `/api/iv-smile?currency=BTC` — response `smiles` should have ATM strikes near spot
4. Check `/api/greeks-summary?currency=BTC` — response `gex` should have non-zero gamma at ATM
5. Check `/api/vol-surface?currency=BTC` — response `term_structure` should span DTE 1-90
6. Check database: `sqlite3 dashboard/data/monitor.db "SELECT COUNT(*) FROM dvol_history"` — should be >0
7. Check storage: `sqlite3 dashboard/data/monitor.db "SELECT json_array_length(contracts_data) FROM scan_records ORDER BY timestamp DESC LIMIT 1"` — should be >100

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: integration verification complete"
```
