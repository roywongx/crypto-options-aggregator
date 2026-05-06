# Scan Parameters Redesign — Design Spec

## Overview

Redesign the scan parameter system to support two-layer data collection: a full-data layer for downstream analytics (IV Smile, Greeks/GEX, Vol Surface) and a filtered+scored layer for strategy recommendations. The root cause of current inaccuracy is that `quick_scan()` filters out ATM options (|delta| > 0.4) and stores only 30 contracts to `contracts_data`, starving analytics modules of data they need.

## Architecture: Two-Layer Scan

```
fetch_deribit_summaries() — single API call, returns full options chain
    │
    ├─→ [quality filter: OI>=10, IV>0, spread<25%]
    │       │
    │       └─→ contracts_data (full, ~500 contracts)
    │            Used by: IV Smile, Greeks/GEX, Vol Surface, Risk Liquidity
    │
    └─→ [quality filter] → [DVOL-adaptive filter: delta+DTE] → [score+sort] → [:30]
            │
            └─→ top_contracts_data (top 30, scored)
                 Used by: Strategy Engine, Sandbox, Grid, Debate agents
```

Key principle: scoring/sorting only applies to the strategy layer, NOT to data collection.

## DVOL-Adaptive Parameters

Thresholds calibrated to real Deribit DVOL data (historical range: ~40 to 92+):

| Regime | DVOL | max_delta | min_dte | max_dte | min_apr |
|--------|------|-----------|---------|---------|---------|
| Low vol | < 50 | 0.35 | 21 | 45 | 10% |
| Normal | 50-70 | 0.30 | 14 | 35 | 15% |
| High vol | > 70 | 0.20 | 7 | 21 | 25% |

Existing bug fixed: `dvol < 30` branch was dead code (DVOL never goes below ~40). Threshold changed to 50. Same fix applies to `dvol_analyzer.py`, `unified_risk_assessor.py`, `options_debate_engine.py`.

## Full-Scan Parameters (Analytics Layer)

```
option_type: ALL       (PUT + CALL required for IV Smile, GEX)
min_dte: 1             (cover front-end weeklies)
max_dte: 90            (cover quarterly expiries)
delta: no limit        (ATM at 0.5 is the GEX peak and smile anchor)
Hard filters:
  OI >= 10
  IV > 0 (was IV >= 10 — dropped, kills low-IV data in calm markets)
  spread < 25%
Frequency: 5 min (unchanged)
```

## Storage

No schema changes. `contracts_data` and `top_contracts_data` columns already exist.

Write logic change in `quick_scan()`:
- `contracts_data` → JSON of all contracts passing quality filter
- `top_contracts_data` → JSON of top 30 scored contracts

Retention (new in `db/maintenance.py`):
- `contracts_data` → NULL after 7 days (frees space, keeps row metadata)
- `top_contracts_data` → kept full 30 days
- Row metadata → 30 days

Storage estimate: ~1 GB/month for BTC full data (was ~80 MB). SQLite handles this fine.

New: write `dvol_history` row per scan (table already exists, unused).

## Scoring Fixes

### IV Rank (seller-perspective)

**Before**: `1.0 - abs(ir - 50) / 50.0` — treats IV=80 and IV=20 identically (both score 0.4).
**After**: `0.5 + (ir - 50) / 100` — IV=80 → 0.8, IV=20 → 0.3. High IV is good for sellers.

### APR Normalization

**Before**: `min(apr, 0) / 200` — APR=50% → 0.25, almost no differentiation.
**After**: `min(max(apr, 0), 100) / 100` — APR=50% → 0.50, wider spread.

### Risk Modifier Direction

**Before**: PANIC (strike <= extreme floor) → ×1.2 (incorrectly boosted score).
**After**: PANIC → ×0.70, ADVERSE → ×0.85, NEAR_FLOOR → ×0.95, NORMAL → ×1.0. Higher risk = lower score.

## Strategy Presets Fix

Current CALL presets are too aggressive (max_delta=0.55 ATM for aggressive).
Aligned with DVOL_PROFILES:

```
PUT:
  conservative: delta=0.20, dte=30-45
  standard:     delta=0.30, dte=14-35
  aggressive:   delta=0.35, dte=7-28

CALL:
  conservative: delta=0.15, dte=30-45
  standard:     delta=0.25, dte=14-35
  aggressive:   delta=0.30, dte=7-28
```

## Additional Threshold Fixes Found in Audit

| File | Issue | Fix |
|------|-------|-----|
| `config.py:121` | `DVOL_LOW_THRESHOLD=20` never reached | → 50 |
| `flow_classifier.py:57-65` | Severity thresholds same for all currencies | Deferred to separate task |
| `constants.py:11-13` | Spot fallback hardcoded BTC=83000 | Use DB last-known-price |
| `support_calculator.py:43-44` | Fallback floors `spot*0.75/0.55` | Acceptable as last-resort |
| `unified_risk_assessor.py:84-93` | DVOL >20 tier never triggered | → align with 50/70 thresholds |

## Files Changed

| File | Change |
|------|--------|
| `config.py` | DVOL thresholds, DVOL_PROFILES, retention config, strategy presets |
| `models/contracts.py` | QuickScanParams defaults, ScanParams max_dte |
| `services/scan_engine.py` | full_scan(), strategy_scan(), quick_scan() refactor, dvol_history write |
| `services/dvol_analyzer.py` | adapt_params_by_dvol thresholds 30→50 |
| `services/risk_framework.py` | weighted_score IV rank, APR cap, risk modifier direction |
| `services/unified_risk_assessor.py` | DVOL tier thresholds |
| `services/options_debate_engine.py` | DVOL thresholds in bull/bear agents |
| `db/maintenance.py` | Layered retention cleanup |

## Not Changed

- Frontend JS (same API contracts, richer data)
- DB schema (columns already exist)
- DataHub WebSocket (caching logic unchanged)
- Downstream API routes (queries unchanged, just get more data)
- New dependencies (none)

## Verification

1. Start dashboard, confirm `contracts_data` JSON array length increases from ~30 to ~300-600
2. IV Smile chart shows ATM data point and more strikes per expiry
3. Greeks/GEX chart shows gamma concentrated at ATM (was missing before)
4. Vol Surface shows full DTE 1-90 curve (was 14-35 fragment)
5. Strategy recommendations still show relevant OTM contracts
6. `dvol_history` table populates with new rows
7. Run `db/maintenance.py` cleanup — old `contracts_data` NULLed, rows kept
