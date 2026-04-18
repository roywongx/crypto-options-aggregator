"""
期权监控面板 - FastAPI 后端
基于 crypto-options-aggregator 的实时监控系统
"""

import os
import sys
import json
import sqlite3
import asyncio
import logging
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
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)
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
from db.connection import get_db_connection as _db_conn, execute_read, execute_write
from db.schema import init_database_schema

def get_db_connection(read_only: bool = True):
    """获取数据库连接（默认只读）
    
    读操作（SELECT）: get_db_connection(read_only=True)
    写操作（INSERT/UPDATE）: get_db_connection(read_only=False)
    """
    return _db_conn(read_only=read_only)

_deribit_monitor_cache = {}

DB_PATH = Path(__file__).parent / "data" / "monitor.db"


def _get_deribit_monitor():
    """获取 DeribitOptionsMonitor 单例（单进程安全，多 worker 各自独立）"""
    if 'mon' not in _deribit_monitor_cache:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'deribit-options-monitor'))
        from deribit_options_monitor import DeribitOptionsMonitor
        _deribit_monitor_cache['mon'] = DeribitOptionsMonitor()
    return _deribit_monitor_cache['mon']

def _get_cached_contracts_count(currency: str = "BTC") -> int:
    """快速获取最近一次扫描的合约数量（不解析完整合约数据）"""
    conn = get_db_connection(read_only=True)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT contracts_data FROM scan_records 
        WHERE currency = ? ORDER BY timestamp DESC LIMIT 1
    """, (currency,))
    row = cursor.fetchone()
    if row and row[0]:
        try:
            return len(json.loads(row[0]))
        except Exception as e:
            logger.debug("_get_cached_contracts_count parse error: %s", str(e))
    return 0


def init_database():
    conn = get_db_connection(read_only=False)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    init_database_schema(conn)
    conn.commit()


def save_scan_record(data: Dict[str, Any]):
    conn = get_db_connection(read_only=False)
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
                 option_type, flow_label, notional_usd, delta, instrument_name, premium_usd, severity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                parsed['timestamp'], parsed['currency'], parsed['source'],
                parsed['title'], parsed['message'], parsed['direction'],
                parsed['strike'], parsed['volume'], parsed['option_type'],
                parsed['flow_label'], parsed['notional_usd'], parsed['delta'],
                parsed['instrument_name'], parsed.get('premium_usd', 0), parsed.get('severity', '')
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
        logger.error("scan adapter failed: %s", str(e), exc_info=True)
        return {"success": False, "error": "参数适配失败，请检查输入参数"}


SCAN_INTERVAL_SECONDS = 300  # 5分钟
AUTO_SCAN_ENABLED = True

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    
    # 启动后台定时扫描任务
    if AUTO_SCAN_ENABLED:
        import logging
        logger = logging.getLogger(__name__)
        
        async def background_scan():
            logger.info("启动后台定时扫描任务，间隔 %d 秒", SCAN_INTERVAL_SECONDS)
            while True:
                try:
                    await asyncio.sleep(SCAN_INTERVAL_SECONDS)
                    
                    # 对所有支持的币种执行扫描
                    for currency in ["BTC", "ETH", "SOL"]:
                        try:
                            params = QuickScanParams(currency=currency, option_type="ALL")
                            await quick_scan(params)
                            logger.info("定时扫描完成: %s", currency)
                        except Exception as e:
                            logger.error("定时扫描失败 %s: %s", currency, str(e))
                except asyncio.CancelledError:
                    logger.info("后台扫描任务已取消")
                    break
                except Exception as e:
                    logger.error("后台扫描任务异常: %s", str(e))
                    await asyncio.sleep(60)  # 异常后等待1分钟再继续
        
        # 创建后台任务
        scan_task = asyncio.create_task(background_scan())
        logger.info("后台扫描任务已创建")
    
    yield

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
API_KEY = os.getenv("DASHBOARD_API_KEY", "")

def verify_api_key(request: Request, api_key: str = Depends(API_KEY_HEADER)):
    if API_KEY and api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key. Set DASHBOARD_API_KEY env to enable.")

app = FastAPI(title="期权监控面板", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)
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


@app.get("/api/health")
async def health_check():
    """API健康检查端点"""
    import time
    health = {
        "status": "healthy",
        "timestamp": time.time(),
        "checks": {}
    }
    
    # 检查数据库连接
    try:
        conn = get_db_connection(read_only=True)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        health["checks"]["database"] = "ok"
    except Exception as e:
        health["checks"]["database"] = f"error: {str(e)}"
        health["status"] = "degraded"
    
    # 检查后台扫描状态
    try:
        cursor.execute("SELECT MAX(timestamp) FROM scan_records")
        row = cursor.fetchone()
        if row and row[0]:
            last_scan = float(row[0])
            age = time.time() - last_scan
            health["checks"]["last_scan_age_seconds"] = round(age, 1)
            if age > SCAN_INTERVAL_SECONDS * 2:
                health["checks"]["scan_status"] = "stale"
                health["status"] = "degraded"
            else:
                health["checks"]["scan_status"] = "fresh"
        else:
            health["checks"]["scan_status"] = "no_data"
    except Exception as e:
        health["checks"]["scan_status"] = f"error: {str(e)}"
        health["status"] = "degraded"
    
    # 检查现货价格缓存
    try:
        from services.spot_price import _spot_cache, _CACHE_TTL_SECONDS
        import time as _time
        now = _time.time()
        fresh_count = sum(1 for _, (p, t) in _spot_cache.items() if now - t < _CACHE_TTL_SECONDS)
        health["checks"]["spot_cache_fresh"] = fresh_count
    except Exception:
        health["checks"]["spot_cache"] = "unknown"
    
    status_code = 200 if health["status"] == "healthy" else 503
    return JSONResponse(content=health, status_code=status_code)


@app.post("/api/quick-scan")
async def quick_scan(params: QuickScanParams = None):
    return await run_in_threadpool(_quick_scan_sync, params)


@app.get("/api/latest")
async def get_latest(currency: str = Query(default="BTC")):
    """获取最新扫描数据（用于页面加载和自动刷新）"""
    conn = get_db_connection(read_only=True)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, spot_price, dvol_current, dvol_z_score, dvol_signal,
               large_trades_count, large_trades_details, contracts_data, raw_output
        FROM scan_records WHERE currency = ? ORDER BY timestamp DESC LIMIT 1
    """, (currency,))
    row = cursor.fetchone()

    if not row:
        return {
            "success": False,
            "currency": currency,
            "spot_price": get_spot_price(currency),
            "contracts": [],
            "large_trades_details": [],
            "large_trades_count": 0,
            "timestamp": None,
            "message": "暂无扫描数据，请先执行扫描"
        }

    try:
        contracts = json.loads(row[7]) if row[7] else []
    except Exception:
        contracts = []

    try:
        large_trades = json.loads(row[6]) if row[6] else []
    except Exception:
        large_trades = []

    raw = {}
    if row[8]:
        try:
            raw = json.loads(row[8])
        except Exception:
            raw = {}

    spot_price = row[1] or get_spot_price(currency)
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

    return {
        "success": True,
        "currency": currency,
        "spot_price": row[1] or get_spot_price(currency),
        "dvol_current": row[2] or 0,
        "dvol_z_score": row[3] or 0,
        "dvol_signal": row[4] or '',
        "dvol_interpretation": raw.get("interpretation", ""),
        "dvol_trend": raw.get("trend", ""),
        "dvol_trend_label": raw.get("trend_label", ""),
        "dvol_confidence": raw.get("confidence", ""),
        "dvol_percentile_7d": raw.get("percentile_7d", 50),
        "contracts": contracts,
        "large_trades_details": large_trades,
        "large_trades_count": row[5] or 0,
        "timestamp": row[0]
    }


@app.get("/api/macro")
async def get_macro(currency: str = Query(default="BTC")):
    """获取宏观数据（DVOL + 现货 + 大单统计），轻量快速响应
    
    用于页面初始加载，不返回合约详情，确保秒开。
    合约数据通过 /api/latest 单独获取。
    """
    conn = get_db_connection(read_only=True)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, spot_price, dvol_current, dvol_z_score, dvol_signal,
               large_trades_count, raw_output
        FROM scan_records WHERE currency = ? ORDER BY timestamp DESC LIMIT 1
    """, (currency,))
    row = cursor.fetchone()

    if not row:
        return {
            "success": True,
            "currency": currency,
            "spot_price": get_spot_price(currency),
            "dvol_current": 0,
            "dvol_z_score": 0,
            "dvol_signal": "",
            "dvol_trend": "",
            "dvol_trend_label": "",
            "dvol_confidence": "",
            "dvol_percentile_7d": 50,
            "dvol_interpretation": "",
            "large_trades_count": 0,
            "timestamp": None,
            "contracts_count": 0
        }

    raw = {}
    if row[6]:
        try:
            raw = json.loads(row[6])
        except Exception:
            raw = {}

    return {
        "success": True,
        "currency": currency,
        "spot_price": row[1] or get_spot_price(currency),
        "dvol_current": row[2] or 0,
        "dvol_z_score": row[3] or 0,
        "dvol_signal": row[4] or '',
        "dvol_interpretation": raw.get("interpretation", ""),
        "dvol_trend": raw.get("trend", ""),
        "dvol_trend_label": raw.get("trend_label", ""),
        "dvol_confidence": raw.get("confidence", ""),
        "dvol_percentile_7d": raw.get("percentile_7d", 50),
        "large_trades_count": row[5] or 0,
        "timestamp": row[0],
        "contracts_count": _get_cached_contracts_count(currency)
    }


def _quick_scan_sync(params: QuickScanParams = None):
    """快速扫描：直接获取Deribit数据，不依赖options_aggregator.py"""
    from datetime import datetime
    from concurrent.futures import ThreadPoolExecutor
    _p = params or QuickScanParams()
    currency = _p.currency

    # Step 1: Parallel Fetching
    with ThreadPoolExecutor(max_workers=4) as executor:
        f_spot = executor.submit(get_spot_price, currency)
        f_dvol = executor.submit(get_dvol_from_deribit, currency)
        f_deribit = executor.submit(_fetch_deribit_summaries, currency)
        f_trades = executor.submit(_fetch_large_trades, currency, days=1, limit=40)
        
        # Collect results
        try:
            spot = f_spot.result(timeout=15)
            dvol_data = f_dvol.result(timeout=15)
            summaries = f_deribit.result(timeout=15)
            large_trades = f_trades.result(timeout=15)
        except Exception as e:
            logger.error("Parallel fetch failed: %s", str(e))
            # Fallback: try to get essential data individually
            try: spot = get_spot_price(currency)
            except Exception: spot = None
            try: dvol_data = get_dvol_from_deribit(currency)
            except Exception: dvol_data = {}
            try: summaries = _fetch_deribit_summaries(currency)
            except Exception: summaries = []
            try: large_trades = _fetch_large_trades(currency, days=1, limit=40)
            except Exception: large_trades = []

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

    # Step 2: Fetch Binance data using optimized function
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'crypto-options-aggregator-link'))
        from binance_options import fetch_binance_options
        
        binance_contracts = fetch_binance_options(
            currency=currency,
            min_dte=_p.min_dte,
            max_dte=_p.max_dte,
            max_delta=_p.max_delta,
            strike=_p.strike,
            min_vol=config.MIN_VOLUME_FILTER,
            max_spread=config.MAX_SPREAD_PCT,
            margin_ratio=_p.margin_ratio,
            option_type=_p.option_type,
            return_results=True
        )
        if not isinstance(binance_contracts, list):
            binance_contracts = []
    except Exception as e:
        logger.warning("binance_options fetch failed: %s", str(e))
        binance_contracts = []

    contracts = []
    floors = RiskFramework._get_floors()
    regular_floor = floors.get("regular", 0)
    extreme_floor = floors.get("extreme", 0)
    
    # Process Deribit
    if summaries:
        for s in summaries:
            meta = _parse_inst_name(s.get("instrument_name", ""))
            if not meta: continue
            if meta.dte < use_min_dte or meta.dte > use_max_dte: continue
            
            req_type = _p.option_type.upper()
            req_type_short = "P" if req_type == "PUT" else "C"
            # 如果 option_type 为 "ALL" 或 "BOTH"，则获取所有合约
            fetch_all = req_type in ("ALL", "BOTH")
            if not fetch_all and meta.option_type != req_type_short: continue
                
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
                "theta": round(float(s.get("theta", 0) or 0), 4),
                "gamma": round(float(s.get("gamma", 0) or 0), 6),
                "vega": round(float(s.get("vega", 0) or 0), 4),
                "iv": round(iv * 100, 1),
                "open_interest": round(oi, 0),
                "loss_at_10pct": round(max(0, (strike - spot * 0.9) if meta.option_type == "P" else (spot * 1.1 - strike)), 2),
                "breakeven": round(strike - prem_usd if meta.option_type == "P" else strike + prem_usd, 0),
                "distance_spot_pct": round(dist, 1),
                "support_distance_pct": round((strike - regular_floor) / regular_floor * 100, 1) if regular_floor > 0 and meta.option_type == "P" else None,
                "margin_required": round(max(strike * 0.1, (strike - prem_usd) * margin_ratio), 2),
                "capital_efficiency": round(prem_usd / max(strike * 0.1, (strike - prem_usd) * margin_ratio) * 100, 1) if cv > 0 else 0,
                "spread_pct": 0.1,
                "breakeven_pct": CalculationEngine.calc_breakeven_pct(strike, prem_usd, meta.option_type, spot),
                "pop": calc_pop(delta_val, meta.option_type, spot, strike, iv, meta.dte),
                "iv_rank": round(dvol_pct, 1) if isinstance(dvol_pct, (int,float)) else None,
                "liquidity_score": min(100, int((oi / 500) * 100))
            })

    # Process Binance - 直接使用已优化的 binance_options 返回结果
    if isinstance(binance_contracts, list):
        for s in binance_contracts:
            if not isinstance(s, dict):
                continue
            
            strike = s.get('strike', 0)
            prem_usd = s.get('premium_usdt', 0)
            dte = s.get('dte', 0)
            delta_val = s.get('delta', 0)
            iv = s.get('mark_iv', 0)
            volume = s.get('volume', 0)
            oi = s.get('oi', 0)
            spread_pct = s.get('spread_pct', 0)
            opt_type = 'P' if 'P' in s.get('symbol', '').upper() else 'C'
            margin_ratio = _p.margin_ratio
            cv = strike * margin_ratio
            apr = s.get('apr', 0)
            dist = abs(strike - spot) / spot * 100
            liq_score = s.get('liquidity_score', 50)

            contracts.append({
                "symbol": s['symbol'],
                "platform": "Binance",
                "expiry": s['symbol'].split('-')[1] if '-' in s.get('symbol', '') else '',
                "dte": round(dte, 1),
                "option_type": opt_type,
                "strike": strike,
                "apr": round(apr, 1),
                "premium_usd": round(prem_usd, 2),
                "delta": round(abs(delta_val), 3),
                "gamma": round(s.get('gamma', 0), 6),
                "theta": round(s.get('theta', 0), 4),
                "vega": round(s.get('vega', 0), 4),
                "iv": round(iv, 1),
                "open_interest": round(oi, 0),
                "loss_at_10pct": round(max(0, (strike - spot * 0.9) if opt_type == "P" else (spot * 1.1 - strike)), 2),
                "breakeven": round(strike - prem_usd if opt_type == 'P' else strike + prem_usd, 0),
                "distance_spot_pct": round(dist, 1),
                "support_distance_pct": round((strike - regular_floor) / regular_floor * 100, 1) if regular_floor > 0 and opt_type == "P" else None,
                "margin_required": round(max(strike * 0.1, (strike - prem_usd) * margin_ratio), 2),
                "capital_efficiency": round(prem_usd / max(strike * 0.1, (strike - prem_usd) * margin_ratio) * 100, 1) if cv > 0 else 0,
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
    conn = get_db_connection(read_only=False)
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
                 option_type, flow_label, notional_usd, delta, instrument_name, premium_usd, severity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                parsed['timestamp'], parsed['currency'], parsed['source'],
                parsed['title'], parsed['message'], parsed['direction'],
                parsed['strike'], parsed['volume'], parsed['option_type'],
                parsed['flow_label'], parsed['notional_usd'], parsed['delta'],
                parsed['instrument_name'], parsed.get('premium_usd', 0), parsed.get('severity', '')
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
    """统一策略计算器 - 支持 Roll/New/Grid 三种模式"""
    from services.unified_strategy_engine import (
        UnifiedStrategyEngine, StrategyParams, StrategyMode, OptionType
    )
    
    conn = get_db_connection(read_only=True)
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

    # 转换为统一引擎参数
    if params.mode == "roll":
        mode = StrategyMode.ROLL
    elif params.mode == "grid":
        mode = StrategyMode.GRID
    else:
        mode = StrategyMode.NEW
        
    option_type = OptionType.PUT if params.option_type == "PUT" else OptionType.CALL
    
    unified_params = StrategyParams(
        currency=params.currency,
        mode=mode,
        option_type=option_type,
        reserve_capital=params.reserve_capital,
        target_max_delta=params.target_max_delta,
        min_dte=params.min_dte,
        max_dte=params.max_dte,
        margin_ratio=params.margin_ratio,
        old_strike=params.old_strike,
        old_qty=params.old_qty,
        close_cost_total=params.close_cost_total,
        max_qty_multiplier=params.max_qty_multiplier,
        target_apr=getattr(params, 'target_apr', 200.0),
        put_count=getattr(params, 'put_count', 5),
        call_count=getattr(params, 'call_count', 0),
        min_apr=getattr(params, 'min_apr', 8.0)
    )

    engine = UnifiedStrategyEngine()
    return engine.execute(contracts, unified_params, spot)


@app.post("/api/calculator/roll")
async def calculate_net_credit_roll(params: RollCalcParams):
    conn = get_db_connection(read_only=True)
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
    """获取大单交易：优先DB，不足时从Deribit实时API补充
    
    关键概念：
    - notional_usd = 合约数量 × 标的指数价格 (名义价值，代表交易敞口大小)
    - premium_usd = 合约数量 × 期权价格 × 标的指数价格 (权利金，实际支付金额)
    """
    import requests as req_lib
    from datetime import datetime, timedelta
    spot = get_spot_price(currency)
    
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection(read_only=True)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT instrument_name, direction, notional_usd, volume, strike, option_type, flow_label, delta,
               premium_usd, severity
        FROM large_trades_history
        WHERE currency = ? AND timestamp > ?
          AND instrument_name IS NOT NULL AND instrument_name != '' 
          AND instrument_name != '(EMPTY)' AND strike > 100
        ORDER BY notional_usd DESC LIMIT ?
    """, (currency, since, limit))
    rows = cursor.fetchall()

    results = []
    seen = set()
    results_by_inst = {}
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
        
        notional = r[2] or 0
        if notional <= 0 and (r[3] or 0) > 0 and strike > 0:
            notional = float(r[3]) * spot
        
        entry = {
            "instrument_name": inst, "direction": direction,
            "notional_usd": round(float(notional), 2),
            "volume": r[3] or 0,
            "strike": strike, "option_type": opt_type, "flow_label": fl,
            "delta": delta_val,
            "premium_usd": r[8] or 0,
            "severity": r[9] or ''
        }
        results.append(entry)
        results_by_inst[inst] = entry

    MIN_NOTIONAL = 100000
    db_missing_premium = sum(1 for r in results if not r.get('premium_usd'))
    need_api = len(results) < max(5, limit // 2) or db_missing_premium > len(results) * 0.5
    if need_api:
        try:
            api_url = "https://www.deribit.com/api/v2/public/get_last_trades_by_currency"
            payload = req_lib.get(api_url, params={
                "currency": currency, "kind": "option", "count": 500
            }, timeout=10).json()
            trades = payload.get("result", {}).get("trades", [])
            
            for t in trades:
                inst = t.get("instrument_name", "")
                if not inst:
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
                index_price = float(t.get("index_price", 0) or spot)
                option_price = float(t.get("price", 0))
                
                notional_usd = trade_amount * index_price
                premium_usd = trade_amount * option_price * index_price
                
                if notional_usd < MIN_NOTIONAL:
                    continue
                
                trade_iv = float(t.get("iv") or 50) / 100.0
                delta_val = abs(calc_delta_bs(meta.strike, spot,
                    trade_iv, meta.dte, meta.option_type))
                
                fl = _classify_flow_heuristic(
                    direction, meta.option_type, delta_val, meta.strike, spot)
                
                is_block = t.get("block_trade", False) or t.get("block_trade_id") is not None
                
                api_entry = {
                    "instrument_name": inst, "direction": direction,
                    "notional_usd": round(notional_usd, 2),
                    "premium_usd": round(premium_usd, 2),
                    "volume": round(trade_amount, 4),
                    "strike": meta.strike,
                    "option_type": meta.option_type,
                    "flow_label": fl,
                    "delta": delta_val,
                    "iv": round(trade_iv * 100, 1),
                    "is_block": is_block,
                    "trade_price": option_price
                }
                
                if inst in results_by_inst:
                    db_entry = results_by_inst[inst]
                    if not db_entry.get('premium_usd'):
                        db_entry['premium_usd'] = round(premium_usd, 2)
                    if not db_entry.get('iv'):
                        db_entry['iv'] = round(trade_iv * 100, 1)
                    if not db_entry.get('is_block'):
                        db_entry['is_block'] = is_block
                    if not db_entry.get('trade_price'):
                        db_entry['trade_price'] = option_price
                    if not db_entry.get('notional_usd') or db_entry['notional_usd'] < notional_usd:
                        db_entry['notional_usd'] = round(notional_usd, 2)
                        db_entry['volume'] = round(trade_amount, 4)
                else:
                    seen.add(inst)
                    results.append(api_entry)
                    results_by_inst[inst] = api_entry
                    
                if len(results) >= limit:
                    break
        except Exception as e:
            print(f"Deribit live trades fallback error: {e}")

    for t in results:
        if not t.get("severity"):
            t["severity"] = _severity_from_notional(t.get("notional_usd", 0) or 0)
        t["risk_level"] = _risk_emoji(abs(t.get("delta", 0) or 0))
    
    results.sort(key=lambda x: x.get("notional_usd", 0), reverse=True)
    return results[:limit]


# Martingale Sandbox v2.0 - 马丁格尔沙盘推演引擎
@app.post("/api/sandbox/simulate")
async def sandbox_simulate(params: SandboxParams):
    """沙盘推演：模拟崩盘情景，搜索恢复策略"""
    from services.martingale_sandbox import MartingaleSandboxEngine
    
    spot = _get_spot_from_scan()
    if spot < 1000:
        spot = params.crash_price * 1.5
    
    opt_type = params.option_type.upper()
    strike = params.current_strike
    qty = params.current_qty
    
    # Step 1: 计算崩盘损失
    loss_info = MartingaleSandboxEngine.calculate_loss(
        strike=strike, crash_price=params.crash_price, qty=qty,
        avg_premium=params.avg_premium, dte=params.avg_dte, option_type=opt_type
    )
    
    # 计算旧持仓保证金
    old_margin = strike * params.margin_ratio * qty
    
    # Step 2: 搜索恢复候选合约（使用 Binance + Deribit 真实数据）
    all_contracts = []
    try:
        from db.repository import ContractRepository
        repo = ContractRepository()
        currency = params.currency if params.currency else "BTC"
        all_contracts = repo.get_all_contracts(currency)
    except Exception as e:
        logger.warning("获取合约数据失败: %s", e)
        all_contracts = []
    
    candidates = MartingaleSandboxEngine.search_recovery_candidates(
        contracts=all_contracts, crash_price=params.crash_price, spot=spot,
        margin_ratio=params.margin_ratio, min_dte=params.min_dte, max_dte=params.max_dte,
        min_apr=params.min_apr, max_contracts=params.max_contracts, option_type=opt_type
    )
    
    # Step 3: 计算每个候选的恢复方案
    recovery_plans = []
    for c in candidates[:10]:
        plan = MartingaleSandboxEngine.calculate_recovery_plan(
            candidate=c, total_loss=loss_info["total_loss"],
            reserve_capital=params.reserve_capital, old_margin=old_margin,
            max_contracts=params.max_contracts
        )
        if plan:
            recovery_plans.append(plan)
    
    # 按净恢复值排序
    recovery_plans.sort(key=lambda x: x["net_recovery"], reverse=True)
    
    best_plan = recovery_plans[0] if recovery_plans else None
    
    # Step 4: 安全评估
    safety = MartingaleSandboxEngine.generate_safety_assessment(
        loss_info=loss_info, reserve_capital=params.reserve_capital, best_plan=best_plan
    )
    
    return {
        "crash_scenario": {
            "from_price": round(spot, 0),
            "to_price": params.crash_price,
            "drop_pct": round((params.crash_price - spot) / spot * 100, 1),
        },
        "position": {
            "strike": strike,
            "option_type": opt_type,
            "quantity": qty,
            "avg_premium": params.avg_premium,
            "old_margin": round(old_margin, 0),
        },
        "loss_analysis": loss_info,
        "safety_assessment": safety,
        "recovery_plans": recovery_plans[:8],
        "best_plan": best_plan,
        "total_candidates": len(candidates),
        "status": safety["level"],
    }


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
    put_wall = None
    gamma_flip = None
    advice = []
    actions = []
    try:
        from routers.maxpain import _calc_max_pain_internal
        pain_data = await _calc_max_pain_internal(currency)
        nearest_mp = pain_data.get("nearest_mp")
        mm_signal = pain_data.get("mm_overview", "")
        for exp in pain_data.get("expiries", []):
            gc = exp.get("gex_curve", [])
            if gc:
                max_put_oi_strike = None
                max_put_oi = 0
                for g in gc:
                    put_oi_at = g.get("put_oi_at_strike", 0)
                    if put_oi_at > max_put_oi:
                        max_put_oi = put_oi_at
                        max_put_oi_strike = g.get("strike")
                if max_put_oi_strike:
                    put_wall = {"strike": max_put_oi_strike, "oi": max_put_oi, "expiry": exp.get("expiry"), "dte": exp.get("dte")}
            flip = exp.get("flip_point")
            if flip:
                gamma_flip = {"strike": flip, "expiry": exp.get("expiry"), "dte": exp.get("dte")}
                break
    except Exception:
        nearest_mp = None
        mm_signal = ""

    if put_wall and spot < put_wall["strike"]:
        advice.append(f"🛡️ Put Wall防线: ${put_wall['strike']:,.0f} (OI={put_wall['oi']:,.0f}) — 机构在此布防")
    if gamma_flip:
        if spot > gamma_flip["strike"]:
            advice.append(f"✅ Gamma Flip ${gamma_flip['strike']:,.0f} — 价格在多头Gamma区，波动受抑")
        else:
            advice.append(f"⚠️ Gamma Flip ${gamma_flip['strike']:,.0f} — 价格在空头Gamma区，波动放大")

    # ===== 核心策略建议 v10.0（多维数据融合） =====

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

    position_guidance = {
        "NORMAL": {"max_position_pct": 30, "suggested_delta_range": "0.15-0.25", "suggested_dte": "14-35"},
        "NEAR_FLOOR": {"max_position_pct": 40, "suggested_delta_range": "0.20-0.35", "suggested_dte": "7-28"},
        "ADVERSE": {"max_position_pct": 15, "suggested_delta_range": "0.10-0.20", "suggested_dte": "14-45"},
        "PANIC": {"max_position_pct": 0, "suggested_delta_range": "N/A", "suggested_dte": "N/A"}
    }
    pos_guide = position_guidance.get(status, position_guidance["NORMAL"])

    # 链上指标（MVRV、200WMA等）
    onchain_data = {}
    try:
        from services.onchain_metrics import OnChainMetrics
        onchain_data = OnChainMetrics.get_all_metrics("bitcoin")
    except Exception as e:
        onchain_data = {"error": str(e)}

    # 压力测试系统 - 基于 Black-Scholes 的 Vanna/Volga 敏感度分析
    pressure_test_data = {}
    try:
        from services.pressure_test import PressureTestEngine
        atm_strike = round(spot / 100) * 100
        t = 30 / 365  # 30天到期
        r = 0.045  # 无风险利率 4.5%
        sigma = risk_data.get("components", {}).get("volatility", {}).get("current_iv", 60) / 100
        
        pressure_test_data = PressureTestEngine.stress_test(
            S=spot, K=atm_strike, T=t, r=r, sigma=sigma, option_type="P"
        )
        
        # 根据压力测试结果补充策略建议
        if "risk_assessment" in pressure_test_data:
            ra = pressure_test_data["risk_assessment"]
            if ra.get("level") == "HIGH":
                advice.append("⚡ 压力测试警告：Vanna/Volga 风险较高，建议严格对冲 Delta/Gamma 暴露")
                actions.append("买入保护性期权对冲高阶风险")
            elif ra.get("vanna_risk"):
                advice.append("⚡ Vanna 风险：价格-波动率交叉敏感度强，需警惕波动率变动对 Delta 的影响")
    except Exception as e:
        logger.warning("压力测试计算失败: %s", str(e))
        pressure_test_data = {"error": str(e)}

    # 衍生品市场指标（Sharpe Ratio、资金费率、期货/现货比率）
    derivative_data = {}
    try:
        from services.derivative_metrics import DerivativeMetrics
        derivative_data = DerivativeMetrics.get_all_metrics()
        
        # 根据衍生品评估补充策略建议
        if "overheating_assessment" in derivative_data:
            oa = derivative_data["overheating_assessment"]
            if oa.get("level") in ["OVERHEATED", "EXTREME_OVERHEAT"]:
                advice.append(f"⚠️ 衍生品{oa['name']}：{oa['advice']}")
                actions.append("降低杠杆暴露，关注资金费率和期货/现货比率")
            elif oa.get("level") in ["STRONG_BOTTOM", "BOTTOM"]:
                advice.append(f"🔴 衍生品底部信号：{oa['advice']}")
    except Exception as e:
        logger.warning("衍生品指标计算失败: %s", str(e))
        derivative_data = {"error": str(e)}

    # AI 驱动的情绪分析 - 基于大宗交易数据
    ai_sentiment_data = {}
    try:
        from services.ai_sentiment import AISentimentAnalyzer
        large_trades = _fetch_large_trades(currency, days=3, limit=50)
        ai_sentiment_data = AISentimentAnalyzer.analyze_market_sentiment(large_trades, spot)
        
        # 根据AI情绪分析补充策略建议
        if "key_signals" in ai_sentiment_data and ai_sentiment_data["key_signals"]:
            for signal in ai_sentiment_data["key_signals"][:2]:  # 只取前2个信号
                if signal.get("type") in ("warning", "danger"):
                    advice.append(f"🧠 AI信号: {signal.get('text', '')}")
        
        if "dominant_intent" in ai_sentiment_data:
            dom = ai_sentiment_data["dominant_intent"]
            if dom.get("name") == "机构对冲" and ai_sentiment_data.get("confidence", 0) > 60:
                advice.append("🧠 AI识别：机构正在积极对冲风险，建议降低仓位暴露")
            elif dom.get("name") == "方向性投机" and ai_sentiment_data.get("confidence", 0) > 60:
                advice.append(f"🧠 AI识别：方向性投机主导市场（{'看跌' if ai_sentiment_data.get('put_call_ratio', {}).get('put_pct', 50) > 50 else '看涨'}），注意短期波动加剧")
    except Exception as e:
        logger.warning("AI情绪分析失败: %s", str(e))
        ai_sentiment_data = {"error": str(e)}

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
        "position_guidance": pos_guide,
        "put_wall": put_wall,
        "gamma_flip": gamma_flip,
        "onchain_metrics": onchain_data,
        "derivative_metrics": derivative_data,
        "pressure_test": pressure_test_data,
        "ai_sentiment": ai_sentiment_data,
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


@app.post("/api/payoff/score")
async def calc_strategy_score(data: dict):
    """策略评分和实操建议"""
    from services.payoff_calculator import PayoffCalculator
    
    calc = PayoffCalculator()
    legs = data.get("legs", [])
    spot = data.get("spot", 0)
    dte = data.get("dte", 30)
    iv = data.get("iv", 50)
    
    if not legs or not spot:
        return {"error": "缺少 legs 或 spot 参数"}
    
    score_data = calc.calc_strategy_score(legs, spot, dte, iv)
    advice_data = calc.generate_strategy_advice(score_data, legs, spot)
    
    return {
        "score": score_data,
        "advice": advice_data
    }


@app.post("/api/payoff/estimate")
async def estimate_premium(data: dict):
    """智能估算权利金"""
    from services.payoff_calculator import PayoffCalculator
    
    calc = PayoffCalculator()
    option_type = data.get("option_type", "P")
    strike = data.get("strike", 0)
    spot = data.get("spot", 0)
    dte = data.get("dte", 30)
    iv = data.get("iv", 50)
    
    if not strike or not spot:
        return {"error": "缺少 strike 或 spot 参数"}
    
    return calc.estimate_premium(option_type, strike, spot, dte, iv)


@app.post("/api/payoff/compare")
async def compare_strategies(data: dict):
    """对比多个策略（最多 5 个）"""
    from services.payoff_calculator import PayoffCalculator
    
    calc = PayoffCalculator()
    strategies = data.get("strategies", [])
    spot = data.get("spot", 0)
    
    if not strategies or not spot:
        return {"error": "缺少 strategies 或 spot 参数"}
    
    return calc.compare_strategies(strategies, spot)


@app.post("/api/payoff/wheel")
async def calc_wheel_roi(data: dict):
    """计算 Wheel 策略 ROI（增强版）"""
    from services.payoff_calculator import PayoffCalculator
    
    calc = PayoffCalculator()
    put_strike = data.get("put_strike", 0)
    put_premium = data.get("put_premium", 0)
    call_strike = data.get("call_strike", 0)
    call_premium = data.get("call_premium", 0)
    spot = data.get("spot", 0)
    quantity = data.get("quantity", 1)
    put_dte = data.get("put_dte", 30)
    call_dte = data.get("call_dte", 30)
    
    if not put_strike or not spot:
        return {"error": "缺少 put_strike 或 spot 参数"}
    
    return calc.calc_wheel_roi(put_strike, put_premium, call_strike, call_premium, spot, quantity, put_dte, call_dte)


# 启动服务器
if __name__ == "__main__":
    import uvicorn
    # 单worker模式：后台定时扫描任务需要在单worker中运行
    # 如需多worker，请移除main.py中的background_scan_async()启动代码
    uvicorn.run(app, host="0.0.0.0", port=8000)
