import json
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
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
        except (ValueError, TypeError, ZeroDivisionError) as e:
            logger.debug("PCR calc skip: %s", e)

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
        except (RuntimeError, ConnectionError, TimeoutError) as e:
            logger.debug("DVOL fallback failed: %s", e)

    return result


@router.get("/vol-surface")
async def get_vol_surface(currency: str = "BTC"):
    """获取 IV 期限结构数据"""
    from services.spot_price import get_spot_price
    from db.connection import execute_read

    try:
        spot = get_spot_price(currency)
    except (RuntimeError, ValueError) as e:
        logger.warning("Vol surface spot price failed: %s, using fallback", e)
        from constants import get_spot_fallback
        spot = get_spot_fallback(currency)

    # 优先从数据库获取最近的 scan_records 中的合约数据
    try:
        rows = execute_read("""
            SELECT contracts_data FROM scan_records
            WHERE currency = ? AND contracts_data IS NOT NULL
            ORDER BY timestamp DESC LIMIT 1
        """, (currency,))
    except Exception as e:
        logger.warning("Vol surface DB query failed: %s", e)
        rows = []

    term_data = []
    if rows and rows[0][0]:
        try:
            contracts = json.loads(rows[0][0])
            expiry_ivs = {}
            for c in contracts:
                iv = c.get("mark_iv") or c.get("iv") or 0
                dte = c.get("dte", 0)
                if iv > 0 and dte > 0:
                    # 统一 IV 单位为百分比：如果 iv < 1，假设为小数形式，乘以 100
                    iv_float = float(iv)
                    if iv_float < 1.0:
                        iv_float = iv_float * 100
                    key = int(dte)
                    if key not in expiry_ivs:
                        expiry_ivs[key] = []
                    expiry_ivs[key].append(iv_float)

            for dte, ivs in sorted(expiry_ivs.items()):
                avg_iv = sum(ivs) / len(ivs)
                term_data.append({"dte": dte, "avg_iv": round(avg_iv, 2)})
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Vol surface JSON parse failed: %s", e)

    # 如果数据库中没有足够数据，尝试从 Deribit API 获取
    if len(term_data) < 2:
        try:
            from services.trades import fetch_deribit_summaries
            from services.instrument import _parse_inst_name
            from scipy import interpolate

            summaries = fetch_deribit_summaries(currency)
            if summaries:
                parsed = []
                for s in summaries:
                    meta = _parse_inst_name(s.get("instrument_name", ""))
                    if not meta or meta.dte < 1:
                        continue
                    iv = float(s.get("mark_iv") or 0)
                    oi = float(s.get("open_interest") or 0)
                    if iv < 10 or oi < 10:
                        continue
                    parsed.append({"strike": meta.strike, "expiry": meta.expiry, "dte": meta.dte,
                                   "option_type": meta.option_type, "iv": iv, "oi": oi})

                if parsed:
                    expiries = {}
                    for p in parsed:
                        key = p["expiry"]
                        if key not in expiries:
                            expiries[key] = {"dte": p["dte"], "strikes": []}
                        expiries[key]["strikes"].append({"strike": p["strike"], "iv": p["iv"]})

                    expiry_data = sorted(expiries.values(), key=lambda x: x["dte"])
                    for ed in expiry_data:
                        strikes = sorted(ed["strikes"], key=lambda x: abs(x["strike"] - spot))
                        atm_iv = None
                        if strikes:
                            atm_iv = strikes[0]["iv"]
                            for s in strikes[:3]:
                                if s["iv"] > 0:
                                    atm_iv = s["iv"]
                                    break
                        if atm_iv:
                            term_data.append({"dte": ed["dte"], "avg_iv": round(atm_iv, 2)})

                    # 插值填充缺失数据
                    if len(term_data) >= 3:
                        ivs = [t["avg_iv"] for t in term_data]
                        valid_ivs = [(i, iv) for i, iv in enumerate(ivs) if iv is not None]
                        if len(valid_ivs) >= 2:
                            x = [v[0] for v in valid_ivs]
                            y = [v[1] for v in valid_ivs]
                            f = interpolate.interp1d(x, y, kind='linear', fill_value='extrapolate')
                            for i in range(len(term_data)):
                                if term_data[i]["avg_iv"] is None:
                                    term_data[i]["avg_iv"] = round(float(f(i)), 2)
        except Exception as e:
            logger.warning("Vol surface Deribit fallback failed: %s", e)

    if len(term_data) < 2:
        return {"error": "数据不足，至少需要 2 个期限点", "term_structure": [], "backwardation": False}

    backwardation = False
    if len(term_data) >= 2:
        front_iv = term_data[0]["avg_iv"]
        back_iv = term_data[-1]["avg_iv"]
        if front_iv is not None and back_iv is not None:
            backwardation = front_iv > back_iv * 1.05

    return {
        "currency": currency,
        "term_structure": term_data,
        "backwardation": backwardation
    }
