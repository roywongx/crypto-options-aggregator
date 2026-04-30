import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/charts", tags=["charts"])


@router.get("/pcr")
async def get_pcr_chart(currency: str = "BTC", hours: int = 168):
    from services.trades import fetch_deribit_summaries
    from services.instrument import _parse_inst_name
    from db.connection import execute_read

    summaries = fetch_deribit_summaries(currency)
    if not summaries:
        return []

    by_expiry = {}
    for s in summaries:
        meta = _parse_inst_name(s.get("instrument_name", ""))
        if not meta:
            continue
        exp = meta.expiry
        if exp not in by_expiry:
            by_expiry[exp] = {"put_oi": 0, "call_oi": 0, "dte": meta.dte}
        oi = float(s.get("open_interest") or 0)
        if meta.option_type == "P":
            by_expiry[exp]["put_oi"] += oi
        else:
            by_expiry[exp]["call_oi"] += oi

    if not by_expiry:
        return []

    nearest_exp = min(by_expiry.keys(), key=lambda e: by_expiry[e]["dte"])
    ne = by_expiry[nearest_exp]
    current_pcr = ne["put_oi"] / ne["call_oi"] if ne["call_oi"] > 0 else 0
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    if hours <= 24:
        return [{"time": now, "pcr": round(current_pcr, 3), "puts": round(ne["put_oi"], 0), "calls": round(ne["call_oi"], 0)}]

    since = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')
    rows = execute_read("""
        SELECT timestamp, large_trades_details, spot_price FROM scan_records
        WHERE currency = ? AND timestamp >= ? AND large_trades_details IS NOT NULL AND large_trades_details != ''
        ORDER BY timestamp ASC
    """, (currency, since))

    result = []
    for row in rows:
        ts = row[0]
        ltd = row[1]
        try:
            trades = json.loads(ltd) if ltd else []
            if not trades:
                continue

            # 过滤有效交易：volume > 0 且不超过极端值
            valid_trades = [t for t in trades if 0 < (t.get('volume') or 0) < 1000000]
            if not valid_trades:
                continue

            puts = sum(t.get('volume', 0) or 0 for t in valid_trades if (t.get('option_type') or 'P')[0].upper() == 'P')
            calls = sum(t.get('volume', 0) or 0 for t in valid_trades if (t.get('option_type') or 'C')[0].upper() == 'C')
            if puts == 0 and calls == 0:
                continue
            pcr_val = puts / calls if calls > 0 else 0

            # 过滤极端异常值（PCR 正常范围 0.1 ~ 10）
            if pcr_val > 50 or pcr_val < 0.01:
                continue

            result.append({"time": ts, "pcr": round(pcr_val, 3), "puts": puts, "calls": calls})
        except Exception:
            pass

    if not result:
        result.append({"time": now, "pcr": round(current_pcr, 3), "puts": round(ne["put_oi"], 0), "calls": round(ne["call_oi"], 0)})

    return result


@router.get("/dvol")
async def get_dvol_chart(currency: str = "BTC", hours: int = 168):
    from db.connection import execute_read
    from services.dvol_analyzer import get_dvol_from_deribit

    since = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')
    rows = execute_read("""
        SELECT timestamp, dvol_current FROM scan_records
        WHERE currency = ? AND timestamp >= ? AND dvol_current IS NOT NULL
        ORDER BY timestamp ASC
    """, (currency, since))

    result = []
    for row in rows:
        ts = row[0]
        dvol = row[1] or 0
        if dvol <= 0:
            continue
        result.append({"time": ts, "dvol": dvol})

    # 如果数据库中没有有效 DVOL 数据，尝试从 Deribit API 获取历史数据
    if not result:
        try:
            dvol_data = get_dvol_from_deribit(currency)
            if dvol_data and dvol_data.get("current"):
                now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                result.append({"time": now, "dvol": dvol_data["current"]})
        except Exception:
            pass

    return result
