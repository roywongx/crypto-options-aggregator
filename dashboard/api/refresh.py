"""数据刷新 API"""
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api", tags=["refresh"])


@router.get("/dvol/refresh")
async def refresh_dvol(currency: str = Query(default="BTC")):
    """手动刷新 DVOL 数据"""
    from services.dvol_analyzer import get_dvol_from_deribit
    from db.async_connection import execute_write_async
    import json
    from datetime import datetime, timezone

    dvol_data = get_dvol_from_deribit(currency)
    await execute_write_async("""
        INSERT INTO dvol_history (timestamp, currency, current, z_score, signal, trend)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        currency,
        dvol_data.get('current', 0),
        dvol_data.get('z_score', 0),
        dvol_data.get('signal', ''),
        json.dumps({"trend_label": dvol_data.get('trend_label', ''), "confidence": dvol_data.get('confidence', '')})
    ))
    return {"success": True, "dvol": dvol_data}


@router.get("/trades/refresh")
async def refresh_trades(currency: str = Query(default="BTC"), days: int = 7, limit: int = 50):
    """手动刷新大单交易数据"""
    from services.trades import fetch_large_trades
    trades = fetch_large_trades(currency, days=days, limit=limit)
    return {"success": True, "count": len(trades), "trades": trades}
