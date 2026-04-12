"""
期权监控面板 - FastAPI 后端
基于 crypto-options-aggregator 的实时监控系统
"""

import os
import sys
import json
import sqlite3
import asyncio
import subprocess
import math
from concurrent.futures import ThreadPoolExecutor
import requests
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from models.contracts import ScanParams, RollCalcParams, QuickScanParams, StrategyCalcParams, SandboxParams

sys.path.insert(0, str(Path(__file__).parent.parent))



from config import config
from routers.grid import router as grid_router
from services.dvol_analyzer import adapt_params_by_dvol, calc_delta_bs, calc_pop, get_dvol_from_deribit, _get_dvol_simple_fallback
from services.instrument import _parse_inst_name
from services.risk_framework import RiskFramework, CalculationEngine, _risk_emoji
from services.flow_classifier import _classify_flow_heuristic, parse_trade_alert, _severity_from_notional, get_flow_label_info
from services.spot_price import get_spot_price, get_spot_price_binance, get_spot_price_deribit, _get_spot_from_scan
from services.strategy_calc import calc_roll_plan, calc_new_plan
from services.trades import generate_wind_sentiment, fetch_large_trades, fetch_deribit_summaries
from routers.charts import router as charts_router
from routers.trades_api import router as trades_router
from routers.status import router as status_router
from routers.maxpain import router as maxpain_router
from db.connection import get_db_connection as _db_conn
from db.schema import init_database_schema

def get_db_connection():
    return _db_conn()

_deribit_monitor_cache = {}

DB_PATH = Path(__file__).parent / "data" / "monitor.db"


def _get_deribit_monitor():
    """获取 DeribitOptionsMonitor 单例（单进程安全，多 worker 各自独立）"""
    if 'mon' not in _deribit_monitor_cache:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'deribit-options-monitor'))
        from deribit_options_monitor import DeribitOptionsMonitor
        _deribit_monitor_cache['mon'] = DeribitOptionsMonitor()
    return _deribit_monitor_cache['mon']


def init_database():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    init_database_schema(conn)
    conn.commit()


def save_scan_record(data: Dict[str, Any]):
    conn = get_db_connection()
    cursor = conn.cursor()

    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    large_trades = data.get('large_trades_details', []) or data.get('large_trades', [])

    cursor.execute("""
        INSERT INTO scan_records 
        (currency, spot_price, dvol_current, dvol_z_score, dvol_signal, 
         large_trades_count, large_trades_details, contracts_data, raw_output)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get('currency', 'BTC'),
        data.get('spot_price', 0),
        data.get('dvol_current', 0),
        data.get('dvol_z_score', 0),
        data.get('dvol_signal', ''),
        data.get('large_trades_count', 0),
        json.dumps(large_trades, ensure_ascii=False),
        json.dumps(data.get('contracts', []), ensure_ascii=False),
        json.dumps({"dvol_raw": data.get('dvol_raw', {}), "trend": data.get('dvol_trend', ''), "trend_label": data.get('dvol_trend_label', ''), "confidence": data.get('dvol_confidence', ''), "interpretation": data.get('dvol_interpretation', '')}, ensure_ascii=False)
    ))

    if large_trades and isinstance(large_trades, list):
        for trade in large_trades:
            parsed = parse_trade_alert(trade, data.get('currency', 'BTC'), now_str)
            cursor.execute("""
                INSERT INTO large_trades_history 
                (timestamp, currency, source, title, message, direction, strike, volume, 
                 option_type, flow_label, notional_usd, delta, instrument_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                parsed['timestamp'], parsed['currency'], parsed['source'],
                parsed['title'], parsed['message'], parsed['direction'],
                parsed['strike'], parsed['volume'], parsed['option_type'],
                parsed['flow_label'], parsed['notional_usd'], parsed['delta'],
                parsed['instrument_name']
            ))

    _cutoff = (datetime.utcnow() - timedelta(days=90)).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("DELETE FROM scan_records WHERE timestamp < ?", (_cutoff,))
    cursor.execute("DELETE FROM large_trades_history WHERE timestamp < ?", (_cutoff,))

    conn.commit()
    # conn.close()  # threading.local() manages per-thread connection lifecycle


def run_options_scan(params: ScanParams) -> Dict[str, Any]:
    import warnings
    warnings.warn(
        "/api/scan is deprecated - use /api/quick-scan for better performance",
        DeprecationWarning, stacklevel=2
    )

    base_dir = Path(__file__).parent.parent
    sys.path.insert(0, str(base_dir))

    spot_price = get_spot_price(params.currency)
    dvol_data = get_dvol_from_deribit(params.currency)
    dvol_raw_for_adapt = dvol_data if isinstance(dvol_data, dict) else {}

    scan_params = {
        "max_delta": params.max_delta, "min_dte": params.min_dte,
        "max_dte": params.max_dte, "margin_ratio": params.margin_ratio,
        "option_type": params.option_type, "min_apr": 15.0
    }
    adapted = adapt_params_by_dvol(scan_params, dvol_raw_for_adapt)

    use_delta = adapted.get('max_delta', params.max_delta)
    use_min_dte = adapted.get('min_dte', params.min_dte)
    use_max_dte = adapted.get('max_dte', params.max_dte)
    use_margin = adapted.get('margin_ratio', params.margin_ratio)

    try:
        from options_aggregator import format_report
        from binance_options import scan_binance_options
    except ImportError as e:
        return {"success": False, "error": f"Module import failed: {e}"}

    try:
        mon = _get_deribit_monitor()
        
        with ThreadPoolExecutor(max_workers=4) as executor:
            f_dvol = executor.submit(mon.get_dvol_signal, params.currency)
            f_trades = executor.submit(mon.get_large_trade_alerts, currency=params.currency, min_usd_value=200000)
            
            def _run_binance():
                kw = {"currency": params.currency, "min_dte": use_min_dte,
                      "max_dte": use_max_dte, "max_delta": use_delta,
                      "margin_ratio": use_margin, "option_type": params.option_type}
                if params.strike: kw["strike"] = params.strike
                if params.strike_range: kw["strike_range"] = params.strike_range
                return scan_binance_options(kw)
            
            def _run_deribit():
                kw = dict(currency=params.currency, max_delta=use_delta, min_apr=15.0,
                         min_dte=use_min_dte, max_dte=use_max_dte, top_k=20,
                         max_spread_pct=10.0, min_open_interest=100.0, option_type=params.option_type)
                if params.strike: kw["strike"] = params.strike
                if params.strike_range: kw["strike_range"] = params.strike_range
                return mon.get_sell_put_recommendations(**kw)

            f_bin = executor.submit(_run_binance)
            f_der = executor.submit(_run_deribit)

            dvol_res = f_dvol.result(timeout=30)
            trades_res = f_trades.result(timeout=30)
            bin_res = f_bin.result(timeout=60)
            der_res = f_der.result(timeout=60)

        parsed = format_report(params.currency, dvol_res, trades_res, bin_res, der_res, json_output=True)
        if not isinstance(parsed, dict):
            parsed = {"raw_output": str(parsed), "contracts": []}

        parsed['success'] = True
        if spot_price:
            parsed['spot_price'] = spot_price
        if dvol_data.get('current'):
            parsed['dvol_current'] = dvol_data['current']
            parsed['dvol_z_score'] = dvol_data['z_score']
            parsed['dvol_signal'] = dvol_data['signal']
            parsed['dvol_trend'] = dvol_data.get('trend', '')
            parsed['dvol_trend_label'] = dvol_data.get('trend_label', '')
            parsed['dvol_confidence'] = dvol_data.get('confidence', '')
            parsed['dvol_interpretation'] = dvol_data.get('interpretation', '')

        save_scan_record(parsed)

        parsed['dvol_advice'] = adapted.get('_dvol_advice', [])
        parsed['dvol_adjustment'] = adapted.get('_adjustment_level', 'none')
        parsed['adapted_params'] = {
            'max_delta': use_delta, 'min_dte': use_min_dte, 'max_dte': use_max_dte
        }

        return parsed

    except Exception as e:
        import logging
        logging.getLogger(__name__).error("adapt_params_by_dvol failed: %s", str(e), exc_info=True)
        return {"success": False, "error": "参数适配失败，请检查输入参数"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    yield

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
API_KEY = os.getenv("DASHBOARD_API_KEY", "")

def verify_api_key(request: Request, api_key: str = Depends(API_KEY_HEADER)):
    if API_KEY and api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key. Set DASHBOARD_API_KEY env to enable.")

app = FastAPI(title="期权监控面板", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(grid_router)
app.include_router(charts_router)
app.include_router(trades_router)
app.include_router(status_router)
app.include_router(maxpain_router)


# CORS middleware for cross-origin requests
@app.middleware("http")
async def corsMiddleware(request, call_next):
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response

@app.middleware("http")
async def no_cache_middleware(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding='utf-8'))


from fastapi.concurrency import run_in_threadpool

@app.post("/api/scan")
async def scan_options(params: ScanParams):
    result = await run_in_threadpool(run_options_scan, params)
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', '扫描失败'))
    return result


@app.post("/api/quick-scan")
async def quick_scan(params: QuickScanParams = None):
    return await run_in_threadpool(_quick_scan_sync, params)


def _quick_scan_sync(params: QuickScanParams = None):
    """快速扫描：直接获取Deribit数据，不依赖options_aggregator.py"""
    from datetime import datetime
    from concurrent.futures import ThreadPoolExecutor
    _p = params or QuickScanParams()
    currency = _p.currency

    # Step 1: Parallel Fetching
    with ThreadPoolExecutor(max_workers=5) as executor:
        f_spot = executor.submit(get_spot_price, currency)
        f_dvol = executor.submit(get_dvol_from_deribit, currency)
        f_deribit = executor.submit(_fetch_deribit_summaries, currency)
        f_trades = executor.submit(_fetch_large_trades, currency, days=1, limit=40)
        
        # Parallel fetch Binance data components
        def _fetch_bin(url):
            try:
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                return resp.json()
            except Exception: return {}
            
        f_bin_mark = executor.submit(_fetch_bin, 'https://eapi.binance.com/eapi/v1/mark')
        f_bin_info = executor.submit(_fetch_bin, 'https://eapi.binance.com/eapi/v1/exchangeInfo')
        f_bin_ticker = executor.submit(_fetch_bin, 'https://eapi.binance.com/eapi/v1/ticker')

        # Collect results
        try:
            spot = f_spot.result(timeout=15)
            dvol_data = f_dvol.result(timeout=15)
            summaries = f_deribit.result(timeout=15)
            large_trades = f_trades.result(timeout=15)
            r_mark = f_bin_mark.result(timeout=15)
            r_info = f_bin_info.result(timeout=15)
            r_ticker = f_bin_ticker.result(timeout=15)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Parallel fetch failed: %s", str(e))
            # Fallback spot price if possible, or raise
            try: spot = get_spot_price(currency)
            except: raise HTTPException(status_code=500, detail=f"数据抓取失败: {str(e)}")

    _min_spot = {"BTC": 1000, "ETH": 100, "SOL": 10, "XRP": 0.5}.get(currency, 100)
    if not spot or spot < _min_spot:
        raise RuntimeError("[CRITICAL] quick_scan: cannot obtain spot price, scan aborted")

    # 处理DVOL数据
    dvol_current = dvol_data.get('current', 0) or 0
    dvol_z = dvol_data.get('z_score', 0) or 0
    dvol_signal = dvol_data.get('signal', '正常区间')
    
    use_min_dte = _p.min_dte
    use_max_dte = _p.max_dte
    
    dvol_pct = 50
    if abs(dvol_z) > 0:
        try:
            from scipy.stats import norm
            dvol_pct = round(norm.cdf(dvol_z) * 100, 1)
        except Exception:
            dvol_pct = round(50 + dvol_z * 20, 1)
            dvol_pct = max(1, min(99, dvol_pct))

    contracts = []
    
    # Process Deribit
    if summaries:
        for s in summaries:
            meta = _parse_inst_name(s.get("instrument_name", ""))
            if not meta: continue
            if meta.dte < use_min_dte or meta.dte > use_max_dte: continue
            
            req_type = _p.option_type.upper()
            req_type_short = "P" if req_type == "PUT" else "C"
            if meta.option_type != req_type_short: continue
                
            iv = float(s.get("mark_iv") or 0) / 100.0
            prem = float(s.get("mark_price") or 0)
            oi = float(s.get("open_interest") or 0)
            if iv <= 0 or prem <= 0 or oi < 10: continue

            strike = meta.strike
            underlying = float(s.get("underlying_price", spot)) or spot

            raw_delta = s.get("delta")
            if raw_delta is None or float(raw_delta or 0) == 0:
                delta_val = abs(calc_delta_bs(strike, underlying, iv, meta.dte, meta.option_type))
            else:
                delta_val = abs(float(raw_delta))
            
            max_delta = _p.max_delta
            if isinstance(dvol_pct, (int, float)) and dvol_pct >= 80:
                max_delta = max_delta * 0.7
            elif isinstance(dvol_pct, (int, float)) and dvol_pct <= 20:
                max_delta = min(max_delta * 1.2, 0.55)
            
            if delta_val > max_delta: continue
            if _p.strike and abs(strike - _p.strike) > 0.5: continue
            
            prem_usd = prem * underlying
            margin_ratio = _p.margin_ratio
            cv = strike * margin_ratio
            apr = (prem_usd / cv) * (365 / meta.dte) * 100 if cv > 0 else 0
            
            dist = abs(strike - spot) / spot * 100

            contracts.append({
                "symbol": s.get("instrument_name", ""),
                "platform": "Deribit",
                "expiry": meta.expiry,
                "dte": meta.dte,
                "option_type": meta.option_type,
                "strike": strike,
                "apr": round(apr, 1),
                "premium_usd": round(prem_usd, 2),
                "delta": round(delta_val, 3),
                "iv": round(iv * 100, 1),
                "open_interest": round(oi, 0),
                "loss_at_10pct": round(max(0, (strike - spot * 0.9) if meta.option_type == "P" else (spot * 1.1 - strike)), 2),
                "breakeven": round(strike - prem_usd if meta.option_type == "P" else strike + prem_usd, 0),
                "distance_spot_pct": round(dist, 1),
                "spread_pct": 0.1,
                "breakeven_pct": CalculationEngine.calc_breakeven_pct(strike, prem_usd, meta.option_type, spot),
                "pop": calc_pop(delta_val, meta.option_type, spot, strike, iv, meta.dte),
                "iv_rank": round(dvol_pct, 1) if isinstance(dvol_pct, (int,float)) else None,
                "liquidity_score": min(100, int((oi / 500) * 100))
            })

    # Process Binance
    if r_info and r_info.get('optionSymbols'):
        import time
        now_ms = time.time() * 1000
        req_type = _p.option_type.upper()
        max_delta = _p.max_delta
        margin_ratio = _p.margin_ratio

        for s in r_info.get('optionSymbols', []):
            if s['underlying'] != f"{currency}USDT": continue
            if s['side'] != req_type: continue
            
            dte = (s['expiryDate'] - now_ms) / 86400000
            if dte <= 0: continue
            if not (use_min_dte <= dte <= use_max_dte): continue
            
            b_strike = float(s['strikePrice'])
            if _p.strike and abs(b_strike - _p.strike) > 0.5: continue

            mark = next((m for m in r_mark if m['symbol'] == s['symbol']), None)
            if not mark or float(mark['markPrice']) <= 0: continue
            
            delta_val = abs(float(mark['delta']))
            if delta_val > max_delta: continue
            
            ticker = next((t for t in r_ticker if t['symbol'] == s['symbol']), None)
            volume = float(ticker['volume']) if ticker else 0
            bid = float(ticker['bidPrice']) if ticker else 0
            ask = float(ticker['askPrice']) if ticker else 0
            
            if volume < config.MIN_VOLUME_FILTER: continue
            
            spread_pct = ((ask - bid) / bid) * 100 if bid > 0 and ask > 0 else 0
            if spread_pct >= config.MAX_SPREAD_PCT: continue
            
            strike = float(s['strikePrice'])
            prem_usd = float(mark['markPrice'])
            cv = strike * margin_ratio
            apr = (prem_usd / cv) * (365 / dte) * 100 if cv > 0 else 0
            iv = float(mark['markIV']) * 100
            opt_type = 'P' if s['side'] == 'PUT' else 'C'
            liq_score = min(50, (volume / 100) * 50) + max(0, 50 - (spread_pct * 5))

            contracts.append({
                "symbol": s['symbol'],
                "platform": "Binance",
                "expiry": s['symbol'].split('-')[1],
                "dte": round(dte, 1),
                "option_type": opt_type,
                "strike": strike,
                "apr": round(apr, 1),
                "premium_usd": round(prem_usd, 2),
                "delta": round(delta_val, 3),
                "gamma": round(float(mark.get('gamma', 0)), 6),
                "theta": round(float(mark.get('theta', 0)), 4),
                "vega": round(float(mark.get('vega', 0)), 4),
                "iv": round(iv, 1),
                "open_interest": volume,
                "loss_at_10pct": round(max(0, (strike - spot * 0.9) if opt_type == "P" else (spot * 1.1 - strike)), 2),
                "breakeven": round(strike - prem_usd if opt_type == 'P' else strike + prem_usd, 0),
                "distance_spot_pct": round(abs(strike - spot) / spot * 100, 1),
                "spread_pct": round(spread_pct, 2),
                "breakeven_pct": CalculationEngine.calc_breakeven_pct(strike, prem_usd, opt_type, spot),
                "pop": calc_pop(abs(delta_val or 0), opt_type, spot, strike, iv, int(dte)),
                "iv_rank": round(dvol_pct, 1) if isinstance(dvol_pct, (int,float)) else None,
                "liquidity_score": int(liq_score)
            })

    # Scoring and Filtering
    def _normalize_liquidity(ct):
        """按平台归一化流动性分数 - Binance OI 量级比 Deribit 小 1-2 个数量级"""
        platform = ct.get("platform", "")
        base = ct.get("liquidity_score", 0)
        if platform == "Binance":
            oi_factor = min(2.0, 1.0 + (ct.get("open_interest", 0) / 200))
            spread_penalty = max(0.5, 1.0 - ct.get("spread_pct", 0) / 20)
            return min(100, int(base * oi_factor * spread_penalty))
        return base

    def _weighted_score(ct):
        score = CalculationEngine.weighted_score(
            apr=ct.get("apr", 0),
            pop=ct.get("pop", 50),
            breakeven_pct=ct.get("breakeven_pct", 0),
            liquidity_score=_normalize_liquidity(ct),
            iv_rank=ct.get("iv_rank", 50),
            strike=ct.get("strike", 0),
            spot=spot
        )
        ct["_score"] = score
        return score

    all_c = sorted(contracts, key=_weighted_score, reverse=True)
    deribit_list = [c for c in all_c if c.get("platform") == "Deribit"][:15]
    binance_list = [c for c in all_c if c.get("platform") == "Binance"][:15]
    
    contracts = []
    for i in range(max(len(deribit_list), len(binance_list))):
        if i < len(deribit_list): contracts.append(deribit_list[i])
        if i < len(binance_list): contracts.append(binance_list[i])

    large_trades_count = len(large_trades)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # DB persistence
    conn = get_db_connection()
    cursor = conn.cursor()
    _raw_out = json.dumps({
        "dvol_raw": dvol_data, "trend": dvol_data.get("trend", ""),
        "trend_label": dvol_data.get("trend_label", ""),
        "confidence": dvol_data.get("confidence", ""),
        "interpretation": dvol_data.get("interpretation", ""),
        "percentile_7d": dvol_data.get("percentile_7d", 50)
    }, ensure_ascii=False)
    cursor.execute("""
        INSERT INTO scan_records (timestamp, currency, spot_price, dvol_current, dvol_z_score,
            dvol_signal, large_trades_count, large_trades_details, contracts_data, raw_output)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (timestamp, currency, spot, dvol_current, dvol_z, dvol_signal, large_trades_count,
          json.dumps(large_trades[:20]), json.dumps(contracts[:30]), _raw_out))

    if large_trades and isinstance(large_trades, list):
        for trade in large_trades:
            parsed = parse_trade_alert(trade, currency, timestamp)
            cursor.execute("""
                INSERT INTO large_trades_history
                (timestamp, currency, source, title, message, direction, strike, volume,
                 option_type, flow_label, notional_usd, delta, instrument_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                parsed['timestamp'], parsed['currency'], parsed['source'],
                parsed['title'], parsed['message'], parsed['direction'],
                parsed['strike'], parsed['volume'], parsed['option_type'],
                parsed['flow_label'], parsed['notional_usd'], parsed['delta'],
                parsed['instrument_name']
            ))

    conn.commit()

    return {
        "success": True,
        "contracts_count": len(contracts),
        "spot_price": spot,
        "timestamp": timestamp,
        "contracts": contracts[:30],
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


@app.post("/api/strategy-calc")
async def strategy_calc(params: StrategyCalcParams):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT contracts_data, spot_price FROM scan_records WHERE currency = ? ORDER BY timestamp DESC LIMIT 1", (params.currency,))
    row = cursor.fetchone()

    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="暂无扫描数据，请先执行扫描")

    try:
        contracts = json.loads(row[0])
    except Exception:
        contracts = []

    spot = row[1] or 0

    if params.mode == "roll":
        return calc_roll_plan(contracts, params, spot)
    else:
        return calc_new_plan(contracts, params, spot)


@app.post("/api/calculator/roll")
async def calculate_net_credit_roll(params: RollCalcParams):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT contracts_data FROM scan_records WHERE currency = ? ORDER BY timestamp DESC LIMIT 1", (params.currency,))
    row = cursor.fetchone()
    # conn.close()  # threading.local() manages per-thread connection lifecycle

    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="暂无扫描数据，请先执行扫描")

    try:
        contracts = json.loads(row[0])
    except Exception:
        contracts = []

    import math

    from config import config

    MIN_NET_CREDIT_USD = config.MIN_NET_CREDIT_USD
    SLIPPAGE_PCT = config.ROLL_SLIPPAGE_PCT
    SAFETY_BUFFER_PCT = config.ROLL_SAFETY_BUFFER_PCT

    plans = []
    break_even_exceeds_cap = 0
    filtered_by_negative_nc = 0
    filtered_by_margin = 0

    for c in contracts:
        c_type = c.get('option_type', 'P').upper()
        if c_type != 'P' and c_type != 'C': continue
        c_strike = c.get('strike', 0)
        if c_type == 'P' and c_strike >= params.old_strike: continue
        if c_type == 'C' and c_strike <= params.old_strike: continue
        if c.get('dte', 0) < params.min_dte or c.get('dte', 0) > params.max_dte: continue
        if abs(c.get('delta', 1)) > params.target_max_delta: continue
        
        prem_usd = c.get('premium_usd') or c.get('premium', 0)
        if prem_usd <= 0: continue

        effective_prem_usd = prem_usd * (1 - SLIPPAGE_PCT)

        break_even_qty = math.ceil(params.close_cost_total / effective_prem_usd)
        
        min_qty_for_profit = math.ceil(
            params.close_cost_total / effective_prem_usd * (1 + SAFETY_BUFFER_PCT)
        )
        max_allowed_qty = int(params.old_qty * params.max_qty_multiplier)

        if break_even_qty > max_allowed_qty:
            break_even_exceeds_cap += 1
            continue

        new_qty = max(min_qty_for_profit, break_even_qty)

        strike = c['strike']
        margin_req = new_qty * strike * params.margin_ratio if params.option_type == 'PUT' else new_qty * prem_usd * 10
        if margin_req > params.reserve_capital:
            filtered_by_margin += 1
            continue
            
        gross_credit = new_qty * effective_prem_usd
        net_credit = gross_credit - params.close_cost_total

        if net_credit < MIN_NET_CREDIT_USD:
            filtered_by_negative_nc += 1
            continue

        delta_val = abs(c.get('delta', 0))
        dte_val = c.get('dte', 30)
        apr_val = c.get('apr', 0)

        capital_efficiency = net_credit / margin_req if margin_req > 0 else 0
        delta_penalty = max(0, (delta_val - 0.25) * 2)
        dte_weight = min(1.0, dte_val / 45.0)
        
        # 应用风险框架修正
        spot = get_spot_price(params.currency)
        rf_modifier = RiskFramework.get_score_modifier(strike, spot)
        
        risk_adjusted_score = capital_efficiency * (1 - delta_penalty) * (0.5 + 0.5 * dte_weight) * rf_modifier
        annualized_roi = (net_credit / margin_req * 365 / max(dte_val, 1)) if margin_req > 0 else 0

        plans.append({
            "symbol": c.get('symbol', 'N/A'),
            "platform": c.get('platform', 'N/A'),
            "strike": strike,
            "dte": dte_val,
            "delta": delta_val,
            "apr": apr_val,
            "premium_usd": prem_usd,
            "effective_prem_usd": round(effective_prem_usd, 2),
            "new_qty": new_qty,
            "break_even_qty": break_even_qty,
            "margin_req": round(margin_req, 2),
            "gross_credit": round(gross_credit, 2),
            "net_credit": round(net_credit, 2),
            "roi_pct": round(annualized_roi, 1),
            "score": round(risk_adjusted_score, 4),
            "capital_efficiency": round(capital_efficiency, 4)
        })

    plans.sort(key=lambda x: (x['score'], x['net_credit'], -x['delta']), reverse=True)

    return {
        "success": True,
        "params": params.model_dump(),
        "plans": plans[:15],
        "meta": {
            "total_contracts_scanned": len(contracts),
            "plans_found": len(plans),
            "filtered": {
                "break_even_exceeded_cap": break_even_exceeds_cap,
                "negative_net_credit": filtered_by_negative_nc,
                "insufficient_margin": filtered_by_margin
            }
        }
    }

def _fetch_deribit_summaries(currency="BTC"):
    try:
        mon = _get_deribit_monitor()
        return mon._get_book_summaries(currency)
    except Exception:
        return []


def _fetch_large_trades(currency: str, days: int = 7, limit: int = 50):
    """获取大单交易：优先DB，不足时从Deribit实时API补充"""
    import requests as req_lib
    from datetime import datetime, timedelta
    spot = get_spot_price(currency)
    
    # Step 1: Try DB first
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT instrument_name, direction, notional_usd, volume, strike, option_type, flow_label, delta
        FROM large_trades_history
        WHERE currency = ? AND timestamp > ?
          AND instrument_name IS NOT NULL AND instrument_name != '' 
          AND instrument_name != '(EMPTY)' AND strike > 100
        ORDER BY notional_usd DESC LIMIT ?
    """, (currency, since, limit))
    rows = cursor.fetchall()
    # conn.close()  # threading.local() manages per-thread connection lifecycle

    results = []
    seen = set()
    for r in rows:
        inst = (r[0] or '').strip()
        strike = r[4] or 0
        direction = r[1] or ''
        opt_type = r[5] or ''
        if not inst or strike <= 100 or inst in seen:
            continue
        seen.add(inst)
        fl = r[6] or ''
        delta_val = r[7] or 0
        if not fl or fl == 'unknown':
            fl = _classify_flow_heuristic(direction, opt_type, float(delta_val), strike, spot)
        results.append({
            "instrument_name": inst, "direction": direction,
            "notional_usd": r[2] or 0, "volume": r[3] or 0,
            "strike": strike, "option_type": opt_type, "flow_label": fl,
            "delta": delta_val
        })

    # Step 2: If DB has < limit/2 records, fetch live from Deribit API
    MIN_NOTIONAL = 10000
    if len(results) < max(5, limit // 2):
        try:
            api_url = "https://www.deribit.com/api/v2/public/get_last_trades_by_currency"
            payload = req_lib.get(api_url, params={
                "currency": currency, "kind": "option", "count": 500
            }, timeout=10).json()
            trades = payload.get("result", {}).get("trades", [])
            
            for t in trades:
                inst = t.get("instrument_name", "")
                if not inst or inst in seen:
                    continue
                
                meta = None
                try:
                    meta = _parse_inst_name(inst)
                except Exception:
                    continue
                if not meta:
                    continue
                    
                direction = t.get("direction", "")
                trade_amount = float(t.get("amount", 0))
                index_price = float(t.get("index_price", 0) or 0)
                premium_usd = float(t.get("price", 0)) * trade_amount * (index_price or spot)
                
                if premium_usd < MIN_NOTIONAL:
                    continue
                
                # Use calc_delta_bs (skip slow order book API call)
                trade_iv = float(t.get("iv") or 50) / 100.0
                delta_val = abs(calc_delta_bs(meta.strike, spot,
                    trade_iv, meta.dte, meta.option_type))
                
                fl = _classify_flow_heuristic(
                    direction, meta.option_type, delta_val, meta.strike, spot)
                
                seen.add(inst)
                results.append({
                    "instrument_name": inst, "direction": direction,
                    "notional_usd": round(premium_usd, 2),
                    "volume": round(trade_amount, 4),
                    "strike": meta.strike,
                    "option_type": meta.option_type,
                    "flow_label": fl,
                    "delta": delta_val
                })
                if len(results) >= limit:
                    break
        except Exception as e:
            print(f"Deribit live trades fallback error: {e}")

    # Sort by notional and return top N
    for t in results:
        t["severity"] = _severity_from_notional(t.get("notional_usd", 0) or 0)
        t["risk_level"] = _risk_emoji(abs(t.get("delta", 0) or 0))
    
    results.sort(key=lambda x: x.get("notional_usd", 0), reverse=True)
    return results[:limit]


# DEPRECATED: Use mark['delta'] from API instead. Kept for Deribit branch fallback only.
@app.post("/api/sandbox/simulate")
async def sandbox_simulate(params: SandboxParams):
    spot = _get_spot_from_scan()
    if spot < 1000:
        spot = params.crash_price * 1.5
    steps = []

    try:
        parts = params.current_symbol.rsplit('-', 2)
        base_strike = float(parts[-2]) if len(parts) >= 3 else spot * 0.95
        opt_type = parts[-1] if len(parts) >= 3 else 'P'
    except Exception:
        base_strike = spot * 0.95
        opt_type = 'P'

    drop = ((params.crash_price - spot) / spot * 100) if spot > 0 else -30
    intrinsic = max(0, base_strike - params.crash_price) if opt_type.upper() == 'P' else max(0, params.crash_price - base_strike)
    old_cv = base_strike * params.margin_ratio
    old_margin = old_cv * params.num_contracts

    summaries = _fetch_deribit_summaries("BTC" if "BTC" in params.current_symbol else "ETH")
    index_price = spot  # fallback to spot if index not available
    cands = []
    for s in summaries:
        meta = _parse_inst_name(s.get("instrument_name", ""))
        if not meta or meta.dte < 14 or meta.dte > 180:
            continue
        if meta.option_type != opt_type.upper():
            continue
        if opt_type.upper() == 'P' and meta.strike >= params.crash_price * 0.85:
            continue
        # mark_iv is in percentage (e.g., 47.5), convert to decimal (0.475)
        iv = float(s.get("mark_iv") or 0) / 100.0
        if iv <= 0.05 or iv > 3:
            continue
        # mark_price is per unit, multiply by index_price to get USD value
        prem_usd = float(s.get("mark_price") or 0) * index_price
        oi = float(s.get("open_interest") or 0)
        if prem_usd <= 0 or oi < 10:
            continue
        ncv = meta.strike * params.margin_ratio
        apr_e = (prem_usd / ncv) * (365 / meta.dte) * 100 if ncv > 0 else 0
        if apr_e < 5:
            continue
        cands.append({**meta, "premium_usd": prem_usd, "apr": round(apr_e, 1), "oi": oi, "cv": round(ncv, 2)})
    cands.sort(key=lambda x: x["apr"], reverse=True)

    s1_loss = intrinsic * params.num_contracts
    s1_vega = s1_loss * 0.15
    total_cost = s1_loss + s1_vega

    steps.append({"step": 1, "title": f"Loss at ${params.crash_price:,.0f}",
        "details": [f"Pos: {params.num_contracts}x {params.current_symbol}",
            f"Intrinsic: ${intrinsic:,.0f}/ct x {params.num_contracts} = ${s1_loss:,.0f}",
            f"Vega bloat (~15%): ${s1_vega:,.0f}", f"Est loss: ~${total_cost:,.0f}"],
        "loss_amount": round(total_cost, 0), "status": "warning"})

    plans = []
    for c in cands[:8]:
        needed = total_cost
        pyld = c["apr"] / 100 * (c["dte"] / 365)
        if pyld <= 0.001:
            continue
        nc = max(1, min(20, int(needed / (c["cv"] * pyld))))
        tnm = c["cv"] * nc
        ei = tnm * pyld
        nr = ei - needed
        tcn = old_margin + tnm
        ok = tcn <= params.reserve_capital + old_margin
        st = "success" if ok and nr >= 0 else ("partial" if ok else "danger")
        plans.append({"symbol": f"{c.get('currency','BTC')}-{c['expiry']}-{int(c['strike'])}-{opt_type}",
            "strike": int(c["strike"]), "dte": c["dte"], "apr": c["apr"],
            "prem_ct": round(c["premium_usd"], 2), "contracts": nc, "margin": round(tnm, 0),
            "income": round(ei, 0), "net": round(nr, 0), "capital": round(tcn, 0),
            "reserve": round(params.reserve_capital - tnm, 0), "ok": ok, "status": st})

    bp = plans[0] if plans else None
    if bp:
        al = ""
        if bp["status"] == "danger":
            al = f"MARGIN CALL! Reserve ${params.reserve_capital:,.0f} cannot cover recovery at ${params.crash_price:,.0f}"
        elif bp["status"] == "partial":
            al = f"TIGHT! Can open but net may be negative"
        else:
            al = f"VIABLE! Loss ~${total_cost:,.0f} -> Deploy ${bp['margin']:,.0f} -> {bp['contracts']}x -> Net ${abs(bp['net']):+.0f}"
        steps.append({"step": 2, "title": "Recovery Plan",
            "details": [f"{bp['contracts']}x {bp['symbol']} ({bp['dte']}d APR={bp['apr']}%)",
                f"Prem/ct: ${bp['prem_ct']}", f"Margin: ${bp['margin']:,.0f}", f"Income: ${bp['income']:,.0f}",
                f"Net: ${bp['net']:+,.0f}", f"Reserve: ${bp['reserve']:,.0f}"],
            "loss_amount": 0, "status": bp["status"], "alert": al})

    return {"crash": {"from": round(spot, 0), "to": params.crash_price, "drop_pct": round(drop, 1)},
        "position": {"symbol": params.current_symbol, "contracts": params.num_contracts, "strike": base_strike},
        "loss": round(total_cost, 0), "reserve": params.reserve_capital,
        "steps": steps, "plans": plans[:10], "best": bp,
        "status": bp.get("status", "none") if bp else "no_candidates", "n_cands": len(plans)}


@app.get("/api/bottom-fishing/advice")
async def get_bottom_fishing_advice(currency: str = Query(default="BTC")):
    return await get_risk_overview(currency)


@app.get("/api/risk/assess")
async def get_risk_assessment(currency: str = Query(default="BTC")):
    return await get_risk_overview(currency)


# v8.0: 统一风险中枢API
@app.get("/api/risk/overview")
async def get_risk_overview(currency: str = Query(default="BTC")):
    """统一风险中枢 - 合并风险评估与抄底建议"""
    from services.unified_risk_assessor import UnifiedRiskAssessor
    
    spot = get_spot_price(currency)
    status = RiskFramework.get_status(spot)
    floors = RiskFramework._get_floors()

    # 综合风险评估
    assessor = UnifiedRiskAssessor()
    risk_data = assessor.assess_comprehensive_risk(spot, currency)

    # 最大痛点
    try:
        from routers.maxpain import _calc_max_pain_internal
        pain_data = await _calc_max_pain_internal(currency)
        nearest_mp = pain_data.get("nearest_mp")
        mm_signal = pain_data.get("mm_overview", "")
    except Exception:
        nearest_mp = None
        mm_signal = ""

    # 策略建议
    advice = []
    actions = []

    if status == "NORMAL":
        advice.append(f"当前价格 ${spot:,.0f} 处于常规区间。")
        advice.append("建议：以获取稳定 APR 为目标，保持低杠杆。")
        actions.append("卖出 OTM Put (Delta 0.15-0.25)")
    elif status == "NEAR_FLOOR":
        advice.append(f"当前价格 ${spot:,.0f} 接近常规底 ${floors['regular']:,.0f}。")
        advice.append("建议：可适当增加仓位，博取高 Theta 收益。")
        actions.append("卖出 ATM/ITM Put 并准备滚仓")
    elif status == "ADVERSE":
        advice.append(f"市场处于逆境区 (${spot:,.0f} < ${floors['regular']:,.0f})。")
        advice.append("建议：启用后备资金，高杠杆快平仓，积极执行 Rolling Down & Out。")
        actions.append("将持仓滚动至支撑区间")
    elif status == "PANIC":
        advice.append(f"⚠️ 警告：价格已破极限底 ${floors['extreme']:,.0f}！")
        advice.append("核心指令：止损并保留本金。不要在此区域接货。")
        actions.append("平掉所有 Put 仓位，保持现金")

    if nearest_mp:
        advice.append(f"当前最大痛点在 ${nearest_mp:,.0f}。")
        if spot < nearest_mp:
            advice.append("价格低于痛点，存在向上吸引力。")
        else:
            advice.append("价格高于痛点，存在向下回归压力。")

    return {
        "currency": currency,
        "spot": spot,
        "status": status,
        "composite_score": risk_data["composite_score"],
        "risk_level": risk_data["risk_level"],
        "components": risk_data["components"],
        "recommendations": risk_data["recommendations"],
        "floors": {
            "regular": floors["regular"],
            "extreme": floors["extreme"]
        },
        "max_pain": nearest_mp,
        "mm_signal": mm_signal,
        "advice": advice,
        "recommended_actions": actions,
        "timestamp": risk_data["timestamp"]
    }


# v8.0: Payoff可视化API
@app.post("/api/payoff/calc")
async def calc_payoff(data: dict):
    """计算策略Payoff图"""
    from services.payoff_calculator import PayoffCalculator
    
    calc = PayoffCalculator()
    legs = data.get("legs", [])
    spot = data.get("spot", 0)
    pct_range = data.get("pct_range", 0.3)
    steps = data.get("steps", 100)
    
    if not legs or not spot:
        return {"error": "缺少legs或spot参数"}
    
    return calc.calc_payoff(legs, spot, pct_range, steps)


@app.post("/api/payoff/wheel")
async def calc_wheel_roi(data: dict):
    """计算Wheel策略ROI"""
    from services.payoff_calculator import PayoffCalculator
    
    calc = PayoffCalculator()
    put_strike = data.get("put_strike", 0)
    put_premium = data.get("put_premium", 0)
    call_strike = data.get("call_strike", 0)
    call_premium = data.get("call_premium", 0)
    spot = data.get("spot", 0)
    quantity = data.get("quantity", 1)
    
    if not put_strike or not spot:
        return {"error": "缺少put_strike或spot参数"}
    
    return calc.calc_wheel_roi(put_strike, put_premium, call_strike, call_premium, spot, quantity)


# 启动服务器
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
