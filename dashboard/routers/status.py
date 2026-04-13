# Status and utility API routes
from fastapi import APIRouter, Query, HTTPException
from datetime import datetime, timedelta
import sqlite3
import requests
import os
from pathlib import Path

router = APIRouter(tags=["status"])

DB_PATH = Path(__file__).parent.parent / "data" / "monitor.db"


def get_db_connection():
    from db.connection import get_db_connection as _db_conn
    return _db_conn()


@router.get("/api/stats")
async def get_stats():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM scan_records")
    total_scans = cursor.fetchone()[0]
    _today = datetime.utcnow().strftime('%Y-%m-%d')
    cursor.execute("SELECT COUNT(*) FROM scan_records WHERE date(timestamp) = ?", (_today,))
    today_scans = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM large_trades_history")
    total_trades = cursor.fetchone()[0]
    db_size = os.path.getsize(DB_PATH)
    return {
        "total_scans": total_scans,
        "today_scans": today_scans,
        "total_large_trades": total_trades,
        "db_size_mb": round(db_size / (1024 * 1024), 2)
    }


@router.get("/api/latest")
async def get_latest_scan(currency: str = Query(default="BTC")):
    import json
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM scan_records WHERE currency = ? ORDER BY timestamp DESC LIMIT 1
    """, (currency,))
    row = cursor.fetchone()
    if not row:
        return {
            "success": False,
            "currency": currency,
            "spot_price": 0,
            "contracts": [],
            "large_trades_details": [],
            "large_trades_count": 0,
            "timestamp": None,
            "message": "暂无扫描数据，请先执行扫描"
        }
    col_names = [desc[0] for desc in cursor.description] if cursor.description else []
    rd = dict(zip(col_names, row)) if row and col_names else {}
    _dvol_raw = {}
    if rd.get('raw_output'):
        try: _dvol_raw = json.loads(rd['raw_output'])
        except Exception: pass
    try:
        large_trades = json.loads(rd.get('large_trades_details', '')) if rd.get('large_trades_details') else []
    except Exception:
        large_trades = rd.get('large_trades_details', []) if isinstance(rd.get('large_trades_details'), list) else []

    contracts = json.loads(rd.get('contracts_data', '')) if rd.get('contracts_data') else []

    try:
        from services.risk_framework import RiskFramework
        floors = RiskFramework._get_floors()
        regular_floor = floors.get("regular", 0)
        margin_ratio = 0.20
        for c in contracts:
            if c.get("margin_required") is None:
                strike = c.get("strike", 0)
                prem = c.get("premium_usd", 0) or c.get("premium", 0)
                c["margin_required"] = round(max(strike * 0.1, (strike - prem) * margin_ratio), 2)
            if c.get("capital_efficiency") is None:
                prem = c.get("premium_usd", 0) or c.get("premium", 0)
                margin = c.get("margin_required", 1)
                c["capital_efficiency"] = round(prem / margin * 100, 1) if margin > 0 else 0
            if c.get("support_distance_pct") is None and c.get("option_type") in ("P", "PUT") and regular_floor > 0:
                c["support_distance_pct"] = round((c.get("strike", 0) - regular_floor) / regular_floor * 100, 1)
    except Exception:
        pass

    return {
        "success": True,
        "timestamp": rd.get('timestamp'),
        "currency": rd.get('currency'),
        "spot_price": rd.get('spot_price'),
        "dvol_current": rd.get('dvol_current'),
        "dvol_z_score": rd.get('dvol_z_score'),
        "dvol_signal": rd.get('dvol_signal', ''),
        "dvol_trend": _dvol_raw.get('trend', ''),
        "dvol_trend_label": _dvol_raw.get('trend_label', ''),
        "dvol_confidence": _dvol_raw.get('confidence', ''),
        "dvol_interpretation": _dvol_raw.get('interpretation', ''),
        "dvol_percentile_7d": _dvol_raw.get('percentile_7d', 50),
        "large_trades_count": rd.get('large_trades_count', 0),
        "large_trades_details": large_trades,
        "contracts": contracts,
        "dvol_raw": _dvol_raw
    }


@router.get("/api/health")
async def health_check():
    checks = {}
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM scan_records")
        count = cursor.fetchone()[0]
        checks["database"] = {"status": "ok", "mode": mode, "records": count}
    except Exception as e:
        checks["database"] = {"status": "error", "message": str(e)}
    for name, url in [
        ("deribit_api", "https://www.deribit.com/api/v2/public/get_time"),
        ("binance_api", "https://api.binance.com/api/v3/ping"),
    ]:
        try:
            r = requests.get(url, timeout=5)
            checks[name] = {"status": "ok" if r.status_code == 200 else "error", "code": r.status_code}
        except Exception as e:
            checks[name] = {"status": "error", "message": str(e)[:100]}
    all_ok = all(c.get("status") == "ok" for c in checks.values())
    return {"status": "healthy" if all_ok else "degraded", "checks": checks}


@router.get("/api/dvol-advice")
async def get_dvol_advice(currency: str = Query(default="BTC")):
    import json
    from services.dvol_analyzer import adapt_params_by_dvol
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT raw_output FROM scan_records WHERE currency = ? ORDER BY timestamp DESC LIMIT 1", (currency,))
    row = cursor.fetchone()
    dvol_raw = {}
    if row and row[0]:
        try:
            dvol_raw = json.loads(row[0])
        except Exception: pass
    _inner = dvol_raw.get("dvol_raw", dvol_raw)
    dvol_snapshot = {
        "current": _inner.get("current", 0),
        "z_score": _inner.get("z_score", 0),
        "signal": _inner.get("signal", ""),
        "trend": dvol_raw.get("trend", _inner.get("trend", "")),
        "trend_label": dvol_raw.get("trend_label", _inner.get("trend_label", "")),
        "percentile_7d": dvol_raw.get("percentile_7d", _inner.get("percentile_7d", 50)),
        "confidence": dvol_raw.get("confidence", _inner.get("confidence", "")),
        "interpretation": _inner.get("interpretation", "")
    }
    base_params = {"max_delta": 0.30, "min_dte": 14, "max_dte": 35, "margin_ratio": 0.20, "min_apr": 15}
    adapted = adapt_params_by_dvol(base_params, dvol_raw)
    put_standard = dict(base_params)
    put_standard["option_type"] = "PUT"
    put_adapted = adapt_params_by_dvol(put_standard, dvol_raw)
    call_standard = dict(base_params)
    call_standard["max_delta"] = 0.45
    call_standard["option_type"] = "CALL"
    call_adapted = adapt_params_by_dvol(call_standard, dvol_raw)
    return {
        "dvol_snapshot": dvol_snapshot,
        "adapted_presets": {
            "PUT_standard": {
                "adjustment_level": put_adapted.get("_adjustment_level", "none"),
                "advice": put_adapted.get("_dvol_advice", []),
                "params": {k: v for k, v in put_adapted.items() if not k.startswith("_")}
            },
            "CALL_standard": {
                "adjustment_level": call_adapted.get("_adjustment_level", "none"),
                "advice": call_adapted.get("_dvol_advice", []),
                "params": {k: v for k, v in call_adapted.items() if not k.startswith("_")}
            }
        }
    }