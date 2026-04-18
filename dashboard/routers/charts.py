import sqlite3
from datetime import datetime, timedelta
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/charts", tags=["charts"])


def get_db_path():
    from pathlib import Path
    return Path(__file__).parent.parent / "data" / "monitor.db"


@router.get("/pcr")
async def get_pcr_chart(currency: str = "BTC", hours: int = 168):
    from services.trades import fetch_deribit_summaries
    from services.instrument import _parse_inst_name

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

    conn = sqlite3.connect(str(get_db_path()), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    since = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute("""
        SELECT timestamp, large_trades_details, spot_price FROM scan_records
        WHERE currency = ? AND timestamp >= ? AND large_trades_details IS NOT NULL AND large_trades_details != ''
        ORDER BY timestamp ASC
    """, (currency, since))
    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        ts = row[0]
        ltd = row[1]
        spot = row[2] or 70000
        try:
            import json
            trades = json.loads(ltd) if ltd else []
            if not trades:
                continue
            
            # Filter out trades with suspicious volume (> 10000 is likely notional value instead of contract count)
            valid_trades = [t for t in trades if (t.get('volume') or 0) < 10000]
            if not valid_trades:
                continue
                
            puts = sum(t.get('volume', 0) or 0 for t in valid_trades if (t.get('option_type') or 'P')[0].upper() == 'P')
            calls = sum(t.get('volume', 0) or 0 for t in valid_trades if (t.get('option_type') or 'C')[0].upper() == 'C')
            if puts == 0 and calls == 0:
                continue
            pcr_val = puts / calls if calls > 0 else 0
            
            # Filter out extreme PCR values (likely data error)
            if pcr_val > 50:
                continue
                
            result.append({"time": ts, "pcr": round(pcr_val, 3), "puts": puts, "calls": calls})
        except Exception:
            pass

    if not result:
        result.append({"time": now, "pcr": round(current_pcr, 3), "puts": round(ne["put_oi"], 0), "calls": round(ne["call_oi"], 0)})

    return result


@router.get("/apr")
async def get_apr_chart(currency: str = "BTC", hours: int = 168):
    conn = sqlite3.connect(str(get_db_path()), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    since = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute("""
        SELECT timestamp, contracts_data FROM scan_records
        WHERE currency = ? AND timestamp >= ?
        ORDER BY timestamp ASC
    """, (currency, since))
    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        ts = row[0]
        contracts_json = row[1]
        try:
            import json
            contracts = json.loads(contracts_json) if contracts_json else []
            if not contracts:
                continue

            put_contracts = [c for c in contracts if c.get('option_type') in ['P', 'PUT']]
            if not put_contracts:
                continue

            aprs = [c.get('apr', 0) for c in put_contracts if c.get('apr', 0) > 0]
            if not aprs:
                continue

            aprs_sorted = sorted(aprs)
            best_apr = aprs_sorted[-1] if aprs_sorted else 0
            p75_idx = int(len(aprs_sorted) * 0.75)
            p75_apr = aprs_sorted[p75_idx] if p75_idx < len(aprs_sorted) else (best_apr * 0.85)

            result.append({
                "time": ts,
                "best_safe_apr": best_apr,
                "p75_safe_apr": p75_apr
            })
        except Exception:
            pass

    return result


@router.get("/dvol")
async def get_dvol_chart(currency: str = "BTC", hours: int = 168):
    conn = sqlite3.connect(str(get_db_path()), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    since = (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute("""
        SELECT timestamp, dvol_current FROM scan_records
        WHERE currency = ? AND timestamp >= ? AND dvol_current IS NOT NULL
        ORDER BY timestamp ASC
    """, (currency, since))
    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        ts = row[0]
        dvol = row[1] or 0
        if dvol <= 0:
            continue
        result.append({"time": ts, "dvol": dvol})

    return result


@router.get("/vol-surface")
async def get_vol_surface(currency: str = "BTC"):
    from services.trades import fetch_deribit_summaries
    from services.instrument import _parse_inst_name

    summaries = fetch_deribit_summaries(currency)
    if not summaries:
        return {"error": "无法获取Deribit数据", "surface": [], "term_structure": [], "backwardation": False}

    by_expiry = {}
    for s in summaries:
        meta = _parse_inst_name(s.get("instrument_name", ""))
        if not meta:
            continue
        exp = meta.expiry
        if exp not in by_expiry:
            by_expiry[exp] = {"dte": meta.dte, "contracts": []}
        by_expiry[exp]["contracts"].append({
            "strike": meta.strike,
            "option_type": meta.option_type,
            "iv": (float(s.get("mark_iv", 0)) or 0) / 100.0,
            "delta": float(s.get("delta", 0)) or 0,
            "open_interest": float(s.get("open_interest", 0)) or 0,
            "bid_iv": (float(s.get("bid_iv", 0)) or 0) / 100.0,
            "ask_iv": (float(s.get("ask_iv", 0)) or 0) / 100.0,
        })

    expiry_data = []
    for exp, data in by_expiry.items():
        contracts = data["contracts"]
        if not contracts:
            continue

        puts = [c for c in contracts if c["option_type"] == "P"]
        calls = [c for c in contracts if c["option_type"] == "C"]

        atm_iv = None
        all_cont = puts + calls
        if all_cont:
            valid_atm = [c for c in all_cont if 0 < c["iv"] < 2.0 and abs(c["delta"]) <= 0.55]
            if valid_atm:
                valid_atm.sort(key=lambda c: abs(c["delta"]))
                top3 = valid_atm[:min(3, len(valid_atm))]
                atm_iv = sum(c["iv"] for c in top3) / len(top3)

        surface_row = {"dte": data["dte"], "expiry": exp, "atm": round(atm_iv * 100, 2) if atm_iv else None}

        delta_levels = [(-0.4, "-40delta", "P"), (-0.2, "-20delta", "P"), (0.2, "+20delta", "C"), (0.4, "+40delta", "C")]
        for delta_target, label, opt_type in delta_levels:
            pool = puts if opt_type == "P" else calls
            candidates = [c for c in pool if 0 < c["iv"] < 2.0 and abs(c["delta"]) > 0.01 and abs(c["delta"] - abs(delta_target)) < 0.15]
            if candidates:
                candidates.sort(key=lambda c: abs(c["delta"] - abs(delta_target)))
                surface_row[label] = round(candidates[0]["iv"] * 100, 2)
            else:
                surface_row[label] = None

        expiry_data.append(surface_row)

    expiry_data.sort(key=lambda x: x["dte"])

    term_structure = []
    for ed in expiry_data:
        atm = ed.get("atm")
        if atm and 5 < atm < 200:
            term_structure.append({"dte": ed["dte"], "avg_iv": atm, "expiry": ed["expiry"]})

    if len(term_structure) >= 3:
        ivs = [t["avg_iv"] for t in term_structure]
        median_iv = sorted(ivs)[len(ivs) // 2]
        for t in term_structure:
            if t["avg_iv"] > median_iv * 2.0:
                t["avg_iv"] = None
        term_structure = [t for t in term_structure if t["avg_iv"] is not None]

    if len(term_structure) >= 3:
        dtes = [t["dte"] for t in term_structure]
        ivs = [t["avg_iv"] for t in term_structure]
        for i in range(1, len(ivs) - 1):
            if ivs[i] is not None and ivs[i-1] is not None and ivs[i+1] is not None:
                expected = (ivs[i-1] + ivs[i+1]) / 2
                if abs(ivs[i] - expected) > expected * 0.5:
                    term_structure[i]["avg_iv"] = round(expected, 2)

    backwardation = False
    if len(term_structure) >= 2:
        front_iv = term_structure[0]["avg_iv"]
        back_iv = term_structure[-1]["avg_iv"]
        if front_iv and back_iv and front_iv > back_iv:
            backwardation = True

    return {
        "surface": expiry_data,
        "term_structure": term_structure,
        "backwardation": backwardation,
        "analysis": _get_iv_term_analysis(term_structure)
    }

def _get_iv_term_analysis(term_structure: list) -> dict:
    """获取 IV 期限结构学术分析报告"""
    if not term_structure or len(term_structure) < 2:
        return {"error": "数据不足"}
    
    try:
        from services.iv_term_structure import IVTermStructureAnalyzer
        from services.spot_price import get_spot_price
        
        currency = "BTC"
        spot = get_spot_price(currency) or 0
        
        # 尝试获取历史波动率（30天）
        hist_vol = None
        try:
            import requests
            resp = requests.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                params={"symbol": "BTCUSDT"},
                timeout=5
            )
            data = resp.json()
            # 估算30天历史波动率（简化：使用priceChangePercent作为参考）
            # 更精确的方法：获取30天K线计算标准差
            klines_resp = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "1d", "limit": 30},
                timeout=5
            )
            klines = klines_resp.json()
            if len(klines) >= 10:
                import math
                closes = [float(k[4]) for k in klines]
                returns = [math.log(closes[i]/closes[i-1]) for i in range(1, len(closes))]
                mean_ret = sum(returns) / len(returns)
                variance = sum((r - mean_ret)**2 for r in returns) / (len(returns) - 1)
                daily_vol = math.sqrt(variance)
                hist_vol = daily_vol * math.sqrt(365) * 100  # 年化
        except Exception:
            hist_vol = None
        
        return IVTermStructureAnalyzer.analyze_term_structure(
            term_data=term_structure,
            spot=spot,
            hist_vol=hist_vol
        )
    except Exception as e:
        return {"error": str(e)}
