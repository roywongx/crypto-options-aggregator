"""
期权监控面板 - FastAPI 后端
基于 crypto-options-aggregator 的实时监控系统

v5.0: 渐进式重构 - API 端点已迁移到 api/ 目录模块
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
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import APIKeyHeader
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

# 配置日志 — 使用 StreamHandler 避免 I/O closed file 错误
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

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
from db.schema import init_database_schema, ensure_top_contracts_column


def get_db_connection(read_only: bool = True):
    """获取数据库连接（默认只读）"""
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
    ensure_top_contracts_column(conn)
    conn.commit()


def save_scan_record(data: Dict[str, Any]):
    conn = get_db_connection(read_only=False)
    cursor = conn.cursor()

    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    large_trades = data.get('large_trades_details', []) or data.get('large_trades', [])

    contracts = data.get('contracts', [])
    cursor.execute("""
        INSERT INTO scan_records
        (currency, spot_price, dvol_current, dvol_z_score, dvol_signal,
         large_trades_count, large_trades_details, contracts_data, top_contracts_data, raw_output)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get('currency', 'BTC'),
        data.get('spot_price', 0),
        data.get('dvol_current', 0),
        data.get('dvol_z_score', 0),
        data.get('dvol_signal', ''),
        data.get('large_trades_count', 0),
        json.dumps(large_trades, ensure_ascii=False),
        json.dumps(contracts, ensure_ascii=False),
        json.dumps(contracts[:30], ensure_ascii=False),
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
                return scan_binance_options(**kw)

            f_bin = executor.submit(_run_binance)

            dvol_res = f_dvol.result(timeout=60)
            trades_res = f_trades.result(timeout=60)
            bin_res = f_bin.result(timeout=60)

            def _run_deribit():
                return mon.scan_options(
                    currency=params.currency, option_type=params.option_type,
                    min_dte=use_min_dte, max_dte=use_max_dte,
                    max_delta=use_delta, margin_ratio=use_margin
                )

            f_der = executor.submit(_run_deribit)
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

# 注册 api/ 目录路由模块
from api import (
    scan_router, dashboard_router, paper_trading_router,
    mcp_router, exchanges_router, datahub_router, copilot_router, health_router, macro_router,
    refresh_router, strategy_router, sandbox_router, risk_router, payoff_router
)
app.include_router(scan_router)
app.include_router(dashboard_router)
app.include_router(paper_trading_router)
app.include_router(mcp_router)
app.include_router(exchanges_router)
app.include_router(datahub_router)
app.include_router(copilot_router)
app.include_router(health_router)
app.include_router(macro_router)
app.include_router(refresh_router)
app.include_router(strategy_router)
app.include_router(sandbox_router)
app.include_router(risk_router)
app.include_router(payoff_router)


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


async def quick_scan(params: QuickScanParams = None):
    """
    快速扫描（DataHub 优化版）：
    1. 优先从 DataHub WebSocket 缓存读取（<10ms 响应）
    2. 如果 DataHub 数据太旧（>30s），回退到网络请求
    3. 扫描速度从秒级降至毫秒级
    """
    from datetime import datetime
    from services.spot_price import get_spot_price_async
    _p = params or QuickScanParams()
    currency = _p.currency

    # Step 1: 尝试从 DataHub 缓存读取（毫秒级响应）
    spot = None
    dvol_data = {}
    summaries = []
    large_trades = []
    binance_contracts = []

    try:
        from services.datahub import datahub, TOPIC_SPOT, TOPIC_DVOL, TOPIC_BTC_OPTIONS, TOPIC_ETH_OPTIONS

        # 从 DataHub 获取现货价格（<10ms）
        spot_snapshot = datahub.get_snapshot(TOPIC_SPOT, currency)
        if spot_snapshot:
            spot = spot_snapshot.get("price")
            spot_age = datahub.get_snapshot_age(TOPIC_SPOT)
            if spot_age > 30:
                logger.info("DataHub spot data too old (%.1fs), falling back to REST", spot_age)
                spot = None

        # 从 DataHub 获取 DVOL
        dvol_snapshot = datahub.get_snapshot(TOPIC_DVOL, currency)
        if dvol_snapshot:
            dvol_age = datahub.get_snapshot_age(TOPIC_DVOL)
            if dvol_age < 30:
                dvol_data = {
                    "current": dvol_snapshot.get("current", 0),
                    "z_score": 0,
                    "signal": "normal"
                }

        # 从 DataHub 获取期权链（替代 REST 请求）
        options_topic = TOPIC_BTC_OPTIONS if currency == "BTC" else TOPIC_ETH_OPTIONS
        options_snapshot = datahub.get_snapshot(options_topic)

        if options_snapshot:
            options_age = datahub.get_snapshot_age(options_topic)
            if options_age < 30 and len(options_snapshot) > 0:
                logger.info("DataHub scan: %d options from WebSocket cache (%.1fs old)", len(options_snapshot), options_age)

                # 将 WebSocket 数据转换为 scan 格式
                summaries = []
                for symbol, opt_data in options_snapshot.items():
                    # 从 symbol 解析 DTE (Deribit: BTC-26JUN26-55000-P)
                    inst_meta = _parse_inst_name(symbol)
                    if not inst_meta:
                        continue

                    summaries.append({
                        "instrument_name": opt_data.get("symbol", symbol),
                        "mark_price": opt_data.get("mark_price", 0),
                        "mark_iv": opt_data.get("iv", 0) * 100 if opt_data.get("iv", 0) <= 1 else opt_data.get("iv", 0),
                        "delta": opt_data.get("delta", 0),
                        "gamma": opt_data.get("gamma", 0),
                        "theta": opt_data.get("theta", 0),
                        "vega": opt_data.get("vega", 0),
                        "best_bid_amount": opt_data.get("best_bid", 0),
                        "best_ask_amount": opt_data.get("best_ask", 0),
                        "open_interest": opt_data.get("open_interest", 0),
                        "stats": {"volume": opt_data.get("volume", 0)},
                        "underlying_price": spot
                    })

                # Binance DataHub 缓存尚未实现（需要额外 WebSocket 订阅）
                # 此处留空，后续会回退到 REST
                binance_contracts = []
            else:
                logger.info("DataHub options data too old (%.1fs), falling back to REST", options_age)
    except ImportError:
        logger.debug("DataHub not available, using REST fallback")
    except Exception as e:
        logger.debug("DataHub read failed: %s, using REST fallback", str(e))

    # Step 2: 如果 DataHub 缓存不可用，回退到 REST 请求
    if not spot:
        logger.info("Quick scan: DataHub not ready, using REST fallback for spot price")
        try:
            spot = await get_spot_price_async(currency)
        except Exception:
            spot = 0
    
    # 确保 spot 是有效的数字
    if spot is None or spot <= 0:
        logger.error("Quick scan: Failed to get valid spot price, aborting scan")
        return {"error": "无法获取现货价格", "currency": currency}

    if not summaries:
        logger.info("Quick scan: DataHub not ready, fetching Deribit via REST")
        try:
            summaries = await asyncio.to_thread(fetch_deribit_summaries, currency)
        except Exception as e:
            logger.error("Quick scan: Failed to fetch Deribit summaries: %s", str(e))
            summaries = []

    if not large_trades:
        try:
            large_trades = await _fetch_large_trades_async(currency, days=1, limit=40)
        except Exception:
            large_trades = []

    if not binance_contracts:
        logger.info("Quick scan: DataHub not ready, fetching Binance via REST")
        try:
            from binance_options import fetch_binance_options
            binance_contracts = await asyncio.to_thread(
                fetch_binance_options,
                currency=currency,
                min_dte=_p.min_dte,
                max_dte=_p.max_dte,
                max_delta=_p.max_delta,
                strike=_p.strike,
                min_vol=config.MIN_VOLUME_FILTER,
                max_spread=config.MAX_SPREAD_PCT,
                margin_ratio=_p.margin_ratio,
                option_type=_p.option_type
            )
            if not isinstance(binance_contracts, list):
                binance_contracts = []
        except Exception as e:
            logger.warning("binance_options fetch failed: %s", str(e))
            binance_contracts = []

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
            dvol_signal, large_trades_count, large_trades_details, contracts_data, top_contracts_data, raw_output)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (timestamp, currency, spot, dvol_current, dvol_z, dvol_signal, large_trades_count,
          json.dumps(large_trades[:20]), json.dumps(contracts[:30]), json.dumps(contracts[:30]), _raw_out))

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


def _fetch_wind_analysis(currency: str, days: int = 30):
    """同步获取大单风向标分析数据"""
    since = datetime.utcnow() - timedelta(days=days)
    since_str = since.strftime('%Y-%m-%d %H:%M:%S')

    grouped = execute_read("""
        SELECT direction, option_type, SUM(volume) as total_volume, COUNT(*) as trade_count
        FROM large_trades_history
        WHERE currency = ? AND timestamp >= ?
        GROUP BY direction, option_type
    """, (currency, since_str))

    strike_rows = execute_read("""
        SELECT strike, option_type, SUM(volume) as total_volume, COUNT(*) as trade_count
        FROM large_trades_history
        WHERE currency = ? AND timestamp >= ?
        GROUP BY strike, option_type
        ORDER BY strike ASC
    """, (currency, since_str))

    summary_data = {'buy_put': 0, 'sell_call': 0, 'buy_call': 0, 'sell_put': 0, 'total': 0, 'put_vol': 0, 'call_vol': 0}
    for row in grouped:
        direction = (row[0] or '').lower()
        ot = (row[1] or 'PUT').upper()
        count = row[3] or 0
        vol = row[2] or 0
        summary_data['total'] += count
        if direction == 'buy' and ot == 'PUT':
            summary_data['buy_put'] += count
            summary_data['put_vol'] += vol
        elif direction == 'sell' and ot == 'CALL':
            summary_data['sell_call'] += count
            summary_data['call_vol'] += vol
        elif direction == 'buy' and ot == 'CALL':
            summary_data['buy_call'] += count
            summary_data['call_vol'] += vol
        elif direction == 'sell' and ot == 'PUT':
            summary_data['sell_put'] += count
            summary_data['put_vol'] += vol

    total = summary_data['total'] or 1
    bp = summary_data['buy_put']
    sc = summary_data['sell_call']
    bc = summary_data['buy_call']
    sp = summary_data['sell_put']

    bp_ratio = bp / total
    sc_ratio = sc / total
    buy_ratio = (bp + bc) / total
    dominant = "看跌保护" if bp_ratio > 0.3 else ("Covered Call偏好" if sc_ratio > 0.3 else "中性")

    sentiment_score = round((bp_ratio * 2 + sc_ratio * 1.5 + bc / total * 1) - (sp / total * 1), 2) if total > 10 else 0

    try:
        spot = get_spot_price(currency)
    except Exception:
        spot = 0

    return {
        "currency": currency, "spot": spot, "days": days,
        "buy_ratio": round(buy_ratio, 3), "dominant_flow": dominant,
        "risk_level": RiskFramework.get_status(spot),
        "sentiment_score": sentiment_score,
        "sentiment_text": dominant,
        "summary": {"total_trades": summary_data['total'], "buy_puts": bp,
                    "sell_calls": sc, "buy_calls": bc, "sell_puts": sp}
    }


def _fetch_term_structure(currency: str):
    """同步获取 IV Term Structure 数据"""
    from services.trades import fetch_deribit_summaries
    from services.instrument import _parse_inst_name
    from scipy import interpolate

    # 只调用一次现货价格
    try:
        spot = get_spot_price(currency)
    except Exception:
        spot = 70000 if currency == "BTC" else 3000

    summaries = fetch_deribit_summaries(currency)
    if not summaries:
        return {"error": "无法获取Deribit数据", "surface": [], "term_structure": [], "backwardation": False}

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

    if not parsed:
        return {"error": "No valid IV data", "surface": [], "term_structure": [], "backwardation": False}

    expiries = {}
    for p in parsed:
        key = p["expiry"]
        if key not in expiries:
            expiries[key] = {"dte": p["dte"], "expiry": p["expiry"], "strikes": []}
        expiries[key]["strikes"].append({"strike": p["strike"], "iv": p["iv"], "oi": p["oi"]})

    expiry_data = sorted(expiries.values(), key=lambda x: x["dte"])

    atm_ivs = []
    dtes = []
    for ed in expiry_data:
        # 使用预先获取的 spot 价格
        strikes = sorted(ed["strikes"], key=lambda x: abs(x["strike"] - spot))
        atm_iv = None
        if strikes:
            atm_iv = strikes[0]["iv"]
            for s in strikes[:3]:
                if s["iv"] > 0:
                    atm_iv = s["iv"]
                    break
        atm_ivs.append(atm_iv)
        dtes.append(ed["dte"])

    term_structure = []
    for i, ed in enumerate(expiry_data):
        atm = atm_ivs[i] if i < len(atm_ivs) else None
        term_structure.append({"dte": ed["dte"], "avg_iv": atm, "expiry": ed["expiry"]})

    if len(term_structure) >= 3:
        ivs = [t["avg_iv"] for t in term_structure]
        valid_ivs = [(i, iv) for i, iv in enumerate(ivs) if iv is not None]
        if len(valid_ivs) >= 2:
            x = [v[0] for v in valid_ivs]
            y = [v[1] for v in valid_ivs]
            f = interpolate.interp1d(x, y, kind='linear', fill_value='extrapolate')
            for i in range(len(term_structure)):
                if term_structure[i]["avg_iv"] is None:
                    expected = float(f(i))
                    term_structure[i]["avg_iv"] = round(expected, 2)

    backwardation = False
    if len(term_structure) >= 2:
        front_iv = term_structure[0]["avg_iv"]
        back_iv = term_structure[-1]["avg_iv"]
        if front_iv is not None and back_iv is not None:
            backwardation = front_iv > back_iv * 1.05

    return {
        "currency": currency,
        "term_structure": term_structure,
        "backwardation": backwardation,
        "analysis": _get_iv_term_analysis(term_structure)
    }


def _get_iv_term_analysis(term_structure: list) -> dict:
    """获取 IV 期限结构分析"""
    if not term_structure or len(term_structure) < 2:
        return {"state": "unknown", "signal": "数据不足", "suggestion": ""}

    front_iv = term_structure[0].get("avg_iv")
    back_iv = term_structure[-1].get("avg_iv")

    if front_iv is None or back_iv is None:
        return {"state": "unknown", "signal": "数据不足", "suggestion": ""}

    if front_iv > back_iv * 1.05:
        return {"state": "backwardation", "signal": "近高远低结构", "suggestion": "建议卖出近月期权获取更高权利金"}
    elif front_iv < back_iv * 0.95:
        return {"state": "contango", "signal": "正常升水结构", "suggestion": "可正常布局远期策略"}
    else:
        return {"state": "flat", "signal": "期限结构平坦", "suggestion": "市场观望情绪浓厚"}


async def _fetch_large_trades_async(currency: str, days: int = 7, limit: int = 50):
    """异步获取大单交易：优先DB，不足时从Deribit实时API补充"""
    import httpx
    from datetime import datetime, timedelta
    from services.spot_price import get_spot_price_async

    spot = await get_spot_price_async(currency)

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
            async with httpx.AsyncClient() as client:
                api_url = "https://www.deribit.com/api/v2/public/get_last_trades_by_currency"
                response = await client.get(api_url, params={
                    "currency": currency, "kind": "option", "count": 500
                }, timeout=10.0)
                payload = response.json()
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


def _fetch_large_trades(currency: str, days: int = 7, limit: int = 50):
    """获取大单交易：优先DB，不足时从Deribit实时API补充"""
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


# 启动服务器
if __name__ == "__main__":
    import uvicorn
    # 单worker模式：后台定时扫描任务需要在单worker中运行
    # 如需多worker，请移除main.py中的background_scan_async()启动代码
    print("[STARTUP] Starting uvicorn server on 0.0.0.0:8000", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info", access_log=True)
