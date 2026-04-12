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

def _norm_cdf(x):
    """标准正态分布累积分布函数近似 (Abramowitz & Stegun)"""
    sign = 1 if x >= 0 else -1
    x = abs(x)
    if x >= 37.0:
        return 0.0 if sign > 0 else 1.0
    exp_arg = -x * x / 2
    if exp_arg < -700: exp_val = 0.0
    else: exp_val = math.exp(exp_arg)
    t = 1.0 / (1.0 + 0.2316419 * x)
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    p = 1.0 - exp_val * poly / math.sqrt(2 * math.pi)
    return p if sign > 0 else 1.0 - p
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
from models.contracts import ScanParams, RollCalcParams, QuickScanParams, RecoveryCalcParams, SandboxParams

sys.path.insert(0, str(Path(__file__).parent.parent))



from config import config
from routers.grid import router as grid_router
from services.dvol_analyzer import calc_delta_bs
from services.instrument import _parse_inst_name
from services.risk_framework import RiskFramework, CalculationEngine
from services.flow_classifier import _classify_flow_heuristic, parse_trade_alert, _severity_from_notional, get_flow_label_info
from services.risk_framework import _risk_emoji
from services.dvol_analyzer import adapt_params_by_dvol

# DeribitOptionsMonitor 单例缓存
_deribit_monitor_cache = {}

DB_PATH = Path(__file__).parent / "data" / "monitor.db"
DB_PATH.parent.mkdir(exist_ok=True)

import threading

_db_local = threading.local()  # Per-thread SQLite connections (WAL mode)

def get_db_connection():
    """Thread-safe SQLite connection with WAL mode and busy timeout"""
    conn = getattr(_db_local, 'conn', None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        _db_local.conn = conn
    return conn



class ScanParams(BaseModel):
    currency: str = Field(default="BTC", description="币种")
    min_dte: int = Field(default=14, ge=1, le=365, description="最小到期天数")
    max_dte: int = Field(default=25, ge=1, le=365, description="最大到期天数")
    max_delta: float = Field(default=0.4, ge=0.01, le=1.0, description="最大Delta")
    margin_ratio: float = Field(default=0.2, ge=0.05, le=1.0, description="保证金比率")
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$")
    strike: Optional[float] = Field(default=None, description="特定行权价")
    strike_range: Optional[str] = Field(default=None, description="行权价范围，如 60000-65000")



class RollCalcParams(BaseModel):
    currency: str = Field(default="BTC", description="币种")
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$", description="期权类型")
    old_strike: float = Field(..., description="原持仓行权价")
    old_qty: float = Field(default=1.0, gt=0, description="原持仓数量")
    close_cost_total: float = Field(..., gt=0, description="平仓总成本(USDT)")
    reserve_capital: float = Field(default=50000.0, ge=0, description="可用后备资金(USDT)")
    target_max_delta: float = Field(default=0.35, ge=0.01, le=0.8, description="目标最大Delta")
    min_dte: int = Field(default=7, ge=1)
    max_dte: int = Field(default=90, ge=1)
    max_qty_multiplier: float = Field(default=3.0, ge=1.0, description="最大倍投倍数")
    margin_ratio: float = Field(default=0.2, ge=0.05, le=1.0)


class QuickScanParams(BaseModel):
    currency: str = Field(default="BTC", pattern="^(BTC|ETH|SOL|XRP)$")
    min_dte: int = Field(default=14, ge=1, le=365)
    max_dte: int = Field(default=35, ge=1, le=365)
    max_delta: float = Field(default=0.4, ge=0.01, le=1.0)
    margin_ratio: float = Field(default=0.2, ge=0.05, le=1.0)
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$")
    strike: Optional[float] = Field(default=None, gt=0)
    strike_range: Optional[str] = Field(default=None)

    def model_post_init(self, __context):
        if self.min_dte > self.max_dte:
            raise ValueError(f"min_dte ({self.min_dte}) must be <= max_dte ({self.max_dte})")

class RecoveryCalcParams(BaseModel):
    currency: str = Field(default="BTC", description="币种")
    current_loss: float = Field(..., gt=0, description="当前浮亏金额(USDT)")
    target_apr: float = Field(default=200, ge=50, le=500, description="目标年化收益率(%)")
    max_contracts: int = Field(default=10, ge=1, le=50, description="最大合约数量")
    max_delta: float = Field(default=0.45, ge=0.1, le=0.8, description="最大Delta容忍")


def get_spot_price_binance(currency: str = "BTC") -> Optional[float]:
    """从 services.spot_price 导入"""
    from services.spot_price import get_spot_price_binance as _get_binance
    return _get_binance(currency)

def get_spot_price_deribit(currency: str = "BTC") -> Optional[float]:
    """从 services.spot_price 导入"""
    from services.spot_price import get_spot_price_deribit as _get_deribit
    return _get_deribit(currency)

def get_spot_price(currency: str = "BTC") -> float:
    """从 services.spot_price 导入"""
    from services.spot_price import get_spot_price as _get_spot
    return _get_spot(currency)

def _get_spot_from_scan(currency: str = "BTC"):
    """从 services.spot_price 导入"""
    from services.spot_price import _get_spot_from_scan as _from_scan
    return _from_scan(currency)

def _get_deribit_monitor():
    """获取 DeribitOptionsMonitor 单例（单进程安全，多 worker 各自独立）"""
    if 'mon' not in _deribit_monitor_cache:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'deribit-options-monitor'))
        from deribit_options_monitor import DeribitOptionsMonitor
        _deribit_monitor_cache['mon'] = DeribitOptionsMonitor()
    return _deribit_monitor_cache['mon']

def get_dvol_from_deribit(currency: str = "BTC") -> Dict[str, Any]:
    try:
        mon = _get_deribit_monitor()
        result = mon.get_dvol_signal(currency)
        if not result:
            return {}
        trend_arrow = "↑" if result.get("trend") == "上涨" else ("↓" if result.get("trend") == "下跌" else "→")
        return {
            "current": result.get("current_dvol", 0),
            "z_score": result.get("z_score_7d", 0),
            "signal": result.get("signal", "正常区间"),
            "trend": trend_arrow,
            "trend_label": result.get("trend", "震荡"),
            "confidence": result.get("confidence_label", "中"),
            "interpretation": result.get("recommendation", ""),
            "data_points": result.get("history_points", 0),
            "percentile_7d": result.get("iv_percentile_7d", 50.0),
        }
    except Exception as e:
        print(f"获取DVOL失败(高级版): {e}, 回退简单版", file=sys.stderr)
        return _get_dvol_simple_fallback(currency)
def _get_dvol_simple_fallback(currency: str = "BTC") -> Dict[str, Any]:
    try:
        base_params = {
            "currency": currency,
            "start_timestamp": int((datetime.utcnow() - timedelta(days=7)).timestamp() * 1000),
            "end_timestamp": int(datetime.utcnow().timestamp() * 1000)
        }
        response = requests.get(
            "https://www.deribit.com/api/v2/public/get_volatility_index_data",
            params={**base_params, "resolution": "3600"}, timeout=10
        )
        data = response.json()
        if data.get("result") and data["result"].get("data"):
            points = data["result"]["data"]
            if len(points) > 0:
                current = float(points[-1][4])
                closes = [float(p[4]) for p in points]
                if len(closes) > 1:
                    mean_val = sum(closes) / len(closes)
                    std_val = (sum((x - mean_val) ** 2 for x in closes) / len(closes)) ** 0.5
                    z_score = (current - mean_val) / std_val if std_val > 0 else 0
                else:
                    z_score = 0
                if z_score > config.DVOL_Z_HIGH: signal = "异常偏高"
                elif z_score > config.DVOL_Z_MID: signal = "偏高"
                elif z_score < -2: signal = "异常偏低"
                elif z_score < -1: signal = "偏低"
                else: signal = "正常区间"
                return {
                    "current": round(current, 2), "z_score": round(z_score, 2),
                    "signal": signal, "trend": "→", "trend_label": "震荡",
                    "confidence": "低", "interpretation": f"DVOL {round(current,1)}% (Z={round(z_score,2)})",
                    "data_points": len(closes),
                    "percentile_7d": round(sum(1 for x in closes if x <= current) / len(closes) * 100, 1) if closes else 50.0
                }
        return {}
    except Exception:
        return {}


def init_database():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scan_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            currency TEXT,
            spot_price REAL,
            dvol_current REAL,
            dvol_z_score REAL,
            dvol_signal TEXT,
            large_trades_count INTEGER,
            large_trades_details TEXT,
            contracts_data TEXT,
            raw_output TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS large_trades_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            currency TEXT NOT NULL,
            source TEXT,
            title TEXT,
            message TEXT,
            direction TEXT DEFAULT 'unknown',
            strike REAL,
            volume REAL DEFAULT 0,
            option_type TEXT,
            flow_label TEXT DEFAULT '',
            notional_usd REAL DEFAULT 0,
            delta REAL DEFAULT 0,
            instrument_name TEXT DEFAULT ''
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_currency ON large_trades_history(currency)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON large_trades_history(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_strike ON large_trades_history(strike)")

    cursor.execute("PRAGMA table_info(scan_records)")
    columns = [col[1] for col in cursor.fetchall()]

    for col in ['dvol_signal', 'large_trades_details', 'contracts_data', 'raw_output']:
        if col not in columns:
            cursor.execute(f"ALTER TABLE scan_records ADD COLUMN {col} TEXT")

    cursor.execute("PRAGMA table_info(large_trades_history)")
    trade_cols = [col[1] for col in cursor.fetchall()]
    for col in ['flow_label', 'notional_usd', 'delta', 'instrument_name']:
        if col not in trade_cols:
            cursor.execute(f"ALTER TABLE large_trades_history ADD COLUMN {col} {'REAL' if col in ('notional_usd','delta') else 'TEXT'}")

    conn.commit()
    # conn.close()  # threading.local() manages per-thread connection lifecycle


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


def calculate_recovery_plan(contracts: List[Dict], params: RecoveryCalcParams, spot_price: float) -> Dict[str, Any]:
    if not contracts or len(contracts) == 0:
        return {"error": "没有可用的合约数据"}

    current_loss = params.current_loss
    target_apr = params.target_apr / 100

    valid_contracts = [c for c in contracts if c.get('apr', 0) > 50 and abs(c.get('delta', 0)) <= params.max_delta]

    if not valid_contracts:
        return {"error": "没有符合条件的合约（APR>50%且Delta在范围内）"}

    valid_contracts.sort(key=lambda x: x.get('apr', 0), reverse=True)

    plans = []
    for contract in valid_contracts[:5]:
        apr = contract.get('apr', 0) / 100
        dte = contract.get('dte', 30)
        strike = contract.get('strike', 0)
        delta = abs(contract.get('delta', 0))

        period_yield = apr * (dte / 365)
        if period_yield <= 0:
            continue

        required_premium = current_loss
        contract_value = strike * 0.2
        num_contracts = max(1, int(required_premium / (contract_value * period_yield)))

        if num_contracts > params.max_contracts:
            num_contracts = params.max_contracts

        total_margin = contract_value * num_contracts
        expected_premium = total_margin * period_yield
        net_profit = expected_premium - current_loss

        risk_level = "低风险"
        if delta > 0.4:
            risk_level = "高风险"
        elif delta > 0.3:
            risk_level = "中风险"
        elif dte < 7:
            risk_level = "中风险"

        plan = {
            "symbol": contract.get('symbol', 'N/A'),
            "platform": contract.get('platform', 'N/A'),
            "strike": strike,
            "dte": dte,
            "apr": contract.get('apr', 0),
            "delta": delta,
            "num_contracts": num_contracts,
            "total_margin": round(total_margin, 2),
            "expected_premium": round(expected_premium, 2),
            "net_profit": round(net_profit, 2),
            "risk_level": risk_level
        }
        plans.append(plan)

    plans.sort(key=lambda x: x['net_profit'], reverse=True)

    return {
        "current_loss": current_loss,
        "target_apr": params.target_apr,
        "plans": plans,
        "recommended": plans[0] if plans else None
    }


def generate_wind_sentiment(summary: Dict, spot: float) -> str:
    parts = []
    kr = summary.get('key_levels', {})

    buy_ratio = summary.get('buy_ratio', 0.5)
    sell_ratio = summary.get('sell_ratio', 0.5)
    total_trades = summary.get('total_trades', 0)
    if total_trades > 0:
        buy_pct = buy_ratio * 100
        if buy_pct > 55:
            parts.append(f"买盘主导({buy_pct:.0f}%)")
        elif buy_pct < 45:
            parts.append(f"卖盘主导({100-buy_pct:.0f}%)")


    support = kr.get('net_support')
    resistance = kr.get('net_resistance')
    if support and resistance:
        spct_s = (support - spot) / spot * 100
        spct_r = (resistance - spot) / spot * 100
        parts.append(f"支撑${support/1000:.0f}K({spct_s:+.1f}%)/阻力${resistance/1000:.0f}K({spct_r:+.1f}%)")

    top_flow = summary.get('dominant_flow')
    if top_flow and top_flow != 'unknown':
        # dominant_flow is now already the Chinese label (from v5.9 aggregation)
        parts.append(f"主流行为:{top_flow}")

    if not parts:
        return "数据不足，暂无法判断"
    return " | ".join(parts)


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
        f_trades = executor.submit(_fetch_large_trades, currency, days=7, limit=50)
        
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
                "breakeven_pct": _calc_breakeven_pct(spot, strike, prem_usd, meta.option_type),
                "pop": _calc_pop(delta_val, meta.option_type, spot, strike, iv, meta.dte),
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
                "breakeven_pct": _calc_breakeven_pct(spot, strike, prem_usd, opt_type),
                "pop": _calc_pop(abs(delta_val or 0), opt_type, spot, strike, iv, int(dte)),
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


@app.get("/api/latest")
async def get_latest_scan(currency: str = Query(default="BTC")):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM scan_records 
        WHERE currency = ? 
        ORDER BY timestamp DESC 
        LIMIT 1
    """, (currency,))

    row = cursor.fetchone()
    # conn.close()  # threading.local() manages per-thread connection lifecycle

    if not row:
        raise HTTPException(status_code=404, detail="暂无数据")

    col_names = [desc[0] for desc in cursor.description] if cursor.description else []
    rd = dict(zip(col_names, row)) if row and col_names else {}

    _dvol_raw = {}
    if rd.get('raw_output'):
        try: _dvol_raw = json.loads(rd['raw_output'])
        except Exception as e:
            print(f"[WARN] Fallback failed: {e}")

    try:
        _ltd = rd.get('large_trades_details', '')
        large_trades = json.loads(_ltd) if _ltd else []
    except Exception:
        large_trades = rd.get('large_trades_details', []) if isinstance(rd.get('large_trades_details'), list) else []

    dvol_trend = _dvol_raw.get('trend', '') if _dvol_raw else ''
    dvol_trend_label = _dvol_raw.get('trend_label', '') if _dvol_raw else ''
    dvol_confidence = _dvol_raw.get('confidence', '') if _dvol_raw else ''
    dvol_interpretation = _dvol_raw.get('interpretation', '') if _dvol_raw else ''

    return {
        "timestamp": rd.get('timestamp'),
        "currency": rd.get('currency'),
        "spot_price": rd.get('spot_price'),
        "dvol_current": rd.get('dvol_current'),
        "dvol_z_score": rd.get('dvol_z_score'),
        "dvol_signal": rd.get('dvol_signal', ''),
        "dvol_trend": dvol_trend,
        "dvol_trend_label": dvol_trend_label,
        "dvol_confidence": dvol_confidence,
        "dvol_interpretation": dvol_interpretation,
        "large_trades_count": rd.get('large_trades_count', 0),
        "large_trades_details": large_trades,
        "contracts": json.loads(rd.get('contracts_data', '')) if rd.get('contracts_data') else [],
        "dvol_raw": _dvol_raw
    }


@app.post("/api/recovery-calculate")
async def calculate_recovery(params: RecoveryCalcParams):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT contracts_data, spot_price FROM scan_records 
        WHERE currency = ? ORDER BY timestamp DESC LIMIT 1
    """, (params.currency,))
    row = cursor.fetchone()
    # conn.close()  # threading.local() manages per-thread connection lifecycle

    if not row:
        raise HTTPException(status_code=404, detail="暂无扫描数据，请先执行扫描")

    col_names = [desc[0] for desc in cursor.description] if cursor.description else []
    rd = dict(zip(col_names, row)) if row and col_names else {}

    try:
        contracts = json.loads(rd.get('contracts_data', '')) if rd.get('contracts_data') else []
    except Exception:
        contracts = []

    spot = rd.get('spot_price', 0) or 0
    result = calculate_recovery_plan(contracts, params, spot)
    return result



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

@app.get("/api/stats")
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

    # conn.close()  # threading.local() manages per-thread connection lifecycle

    return {
        "total_scans": total_scans,
        "today_scans": today_scans,
        "total_large_trades": total_trades,
        "db_size_mb": round(db_size / (1024 * 1024), 2)
    }






@app.get("/api/charts/pcr")
async def get_pcr_chart(currency: str = Query(default="BTC"), hours: int = Query(default=168)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, large_trades_details FROM scan_records
        WHERE currency = ? AND timestamp > datetime('now', ?||' hours')
        ORDER BY timestamp ASC
    """, (currency, -hours))
    rows = cursor.fetchall()
    result = []
    for r in rows:
        try:
            trades = json.loads(r[1]) if r[1] else []
            put_vol = sum(t.get('notional_usd', 0) for t in trades if t.get('instrument_name', '').endswith('-P') and t.get('notional_usd', 0) > 0)
            call_vol = sum(t.get('notional_usd', 0) for t in trades if t.get('instrument_name', '').endswith('-C') and t.get('notional_usd', 0) > 0)
            pcr = put_vol / call_vol if call_vol > 0 else None
            if pcr is not None:
                result.append({"timestamp": r[0], "pcr": round(pcr, 3)})
        except Exception:
            pass
    return {"currency": currency, "data": result}

@app.get("/api/export/csv")
async def export_csv(currency: str = "BTC", hours: int = 168):
    import csv, io
    conn = get_db_connection()
    all_contracts = []
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT contracts_data FROM scan_records
            WHERE currency = ? AND timestamp > datetime('now', ?||' hours')
        """, (currency, -hours))
        for row in cursor.fetchall():
            try:
                contracts = json.loads(row[0]) if row[0] else []
                all_contracts.extend(contracts)
            except Exception:
                pass
    except Exception:
        pass

    output = io.StringIO()
    writer = csv.writer(output)
    headers = ["合约","平台","类型","行权价","DTE","Delta","Gamma","Vega","IV%","APR%","POP%",
               "权利金$","流动性","-10%亏损","盈亏平衡$","安全垫%","持仓量","价差%","IV_Rank","评分"]
    writer.writerow(headers)
    for c in all_contracts:
        writer.writerow([
            c.get('instrument_name', ''), c.get('platform', ''), c.get('option_type', ''),
            c.get('strike', ''), c.get('dte', ''), c.get('delta', ''), c.get('gamma', ''),
            c.get('vega', ''), c.get('iv', ''), c.get('apr', ''), c.get('pop', ''),
            c.get('premium_usd', ''), c.get('liquidity_score', ''), c.get('loss_at_10pct', ''),
            c.get('breakeven_usd', ''), c.get('breakeven_pct', ''), c.get('open_interest', ''),
            c.get('spread_pct', ''), c.get('iv_rank', ''), c.get('_score', '')
        ])

    from fastapi.responses import Response
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f"attachment; filename=options_{currency}_{hours}h.csv"}
    )

@app.get("/api/dvol-advice")
async def get_dvol_advice(currency: str = Query(default="BTC")):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT raw_output FROM scan_records WHERE currency = ? ORDER BY timestamp DESC LIMIT 1", (currency,))
    row = cursor.fetchone()
    dvol_raw = {}
    if row and row[0]:
        try:
            dvol_raw = json.loads(row[0])
        except Exception:
            pass

    _inner = dvol_raw.get("dvol_raw", dvol_raw)
    dvol_snapshot = {
        "current": _inner.get("current", 0),
        "z_score": _inner.get("z_score", 0),
        "signal": _inner.get("signal", ""),
        "trend": dvol_raw.get("trend", _inner.get("trend", "")),
        "trend_label": dvol_raw.get("trend_label", _inner.get("trend_label", "")),
        "percentile_7d": dvol_raw.get("percentile_7d", _inner.get("percentile_7d", 50)),
        "confidence": dvol_raw.get("confidence", _inner.get("confidence", "")),
        "interpretation": dvol_raw.get("interpretation", _inner.get("interpretation", ""))
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


@app.get("/api/health")
async def health_check():
    checks = {}

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM scan_records")
        count = cursor.fetchone()[0]
        # conn.close()  # threading.local() manages per-thread connection lifecycle
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


@app.get("/api/charts/apr")
async def get_apr_chart(currency: str = Query(default="BTC"), hours: int = Query(default=168)):
    import json as _json
    conn = get_db_connection()
    cursor = conn.cursor()
    since = datetime.utcnow() - timedelta(hours=hours)

    cursor.execute("""
        SELECT MAX(timestamp) as ts, contracts_data
        FROM scan_records 
        WHERE timestamp > ? AND currency = ?
        GROUP BY strftime('%Y-%m-%d %H:00', timestamp)
        ORDER BY ts ASC
    """, (since.strftime('%Y-%m-%d %H:%M:%S'), currency))

    raw_rows = cursor.fetchall()

    STD_MAX_DELTA = 0.25
    STD_MIN_DTE = 14
    STD_MAX_DTE = 35
    STD_OPT_TYPE = 'P'
    APR_MIN, APR_MAX = 1.0, 500.0

    result = []
    for r in raw_rows:
        ts = r[0]
        cdata = r[1]
        safe_aprs = []
        all_aprs = []
        try:
            arr = _json.loads(cdata) if isinstance(cdata, str) else cdata
            if isinstance(arr, list):
                for c in arr:
                    if not isinstance(c, dict):
                        continue
                    v = c.get('apr')
                    if not isinstance(v, (int, float)) or not (APR_MIN <= v <= APR_MAX):
                        continue
                    all_aprs.append(v)
                    ot = str(c.get('option_type', '')).upper()
                    if ot not in ('P', 'PUT'):
                        continue
                    d = c.get('dte')
                    if not isinstance(d, (int, float)) or not (STD_MIN_DTE <= d <= STD_MAX_DTE):
                        continue
                    delta = c.get('delta')
                    if not isinstance(delta, (int, float)) or abs(delta) > STD_MAX_DELTA:
                        continue
                    safe_aprs.append(v)
        except Exception:
            pass

        if safe_aprs:
            safe_aprs.sort()
            n = len(safe_aprs)
            p75_idx = min(int(n * 0.75), n - 1)
            best_safe = round(safe_aprs[-1], 1)
            p75_safe = round(safe_aprs[p75_idx], 1)
            avg_safe = round(sum(safe_aprs) / n, 1)
        elif all_aprs:
            best_safe = round(max(all_aprs), 1)
            all_aprs.sort()
            n_all = len(all_aprs)
            p75_idx = min(int(n_all * 0.75), n_all - 1)
            p75_safe = round(all_aprs[p75_idx], 1)
            avg_safe = round(sum(all_aprs) / n_all, 1)
        else:
            best_safe = None
            p75_safe = None
            avg_safe = None

        if all_aprs:
            all_aprs.sort()
            n_all = len(all_aprs)
            avg_all = round(sum(all_aprs) / n_all, 1)
        else:
            avg_all = None

        result.append({
            "time": ts,
            "best_safe_apr": best_safe,
            "p75_safe_apr": p75_safe,
            "avg_safe_apr": avg_safe,
            "avg_apr": avg_all,
            "safe_count": len(safe_aprs),
            "total_count": len(all_aprs)
        })

    _prev_best = None
    _prev_p75 = None
    _prev_avg = None
    for item in result:
        if item["best_safe_apr"] is not None:
            _prev_best = item["best_safe_apr"]
            _prev_p75 = item["p75_safe_apr"]
            _prev_avg = item["avg_safe_apr"]
        else:
            if _prev_best is not None and item["avg_apr"] is not None:
                ratio = _prev_avg / item["avg_apr"] if item["avg_apr"] else 1
                item["best_safe_apr"] = round(_prev_best * ratio, 1)
                item["p75_safe_apr"] = round(_prev_p75 * ratio, 1)
                item["avg_safe_apr"] = round(_prev_avg * ratio, 1)

    return result


@app.get("/api/charts/dvol")
async def get_dvol_chart(currency: str = Query(default="BTC"), hours: int = Query(default=168)):
    conn = get_db_connection()
    cursor = conn.cursor()
    since = datetime.utcnow() - timedelta(hours=hours)

    if hours <= 24:
        grp = "strftime('%Y-%m-%d %H:00', timestamp)"
    elif hours <= 168:
        grp = "strftime('%Y-%m-%d %H:00', timestamp)"
    else:
        grp = "strftime('%Y-%m-%d', timestamp)"

    cursor.execute(f"""
        SELECT {grp} as ts,
               AVG(dvol_current) as dvol,
               AVG(dvol_z_score) as z_score,
               MAX(dvol_signal) as signal
        FROM scan_records 
        WHERE timestamp > ? AND currency = ? AND dvol_current IS NOT NULL
        GROUP BY ts
        ORDER BY ts ASC
    """, (since.strftime('%Y-%m-%d %H:%M:%S'), currency))

    rows = cursor.fetchall()
    # conn.close()  # threading.local() manages per-thread connection lifecycle

    return [{"time": r[0], "dvol": round(r[1], 2) if r[1] else 0, "z_score": round(r[2], 2) if r[2] else 0, "signal": r[3]} for r in rows]


# ============================================================
# Module 1: Volatility Surface & Term Structure
# ============================================================

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
@app.get("/api/charts/vol-surface")
async def get_vol_surface(currency: str = Query(default="BTC")):
    summaries = _fetch_deribit_summaries(currency)
    if not summaries:
        return {"error": "Cannot fetch Deribit", "surface": [], "term_structure": [], "backwardation": False}

    dte_buckets = [7, 14, 30, 60, 90]
    delta_levels = [-0.4, -0.2, 0.0, 0.2, 0.4]
    delta_labels = ["-40D", "-20D", "ATM", "+20D", "+40D"]
    surface = []
    term_data = {d: [] for d in dte_buckets}

    deribit_sp = float(summaries[0].get('underlying_price', 0)) if summaries else 0
    spot = deribit_sp if deribit_sp > 1000 else (_get_spot_from_scan() or 70000)

    for s in summaries:
        meta = _parse_inst_name(s.get("instrument_name", ""))
        if not meta or meta.dte < 1 or meta.dte > 180:
            continue
        iv_pct = float(s.get("mark_iv") or 0)
        if iv_pct <= 5 or iv_pct > 500:
            continue
        oi = float(s.get("open_interest") or 0)
        strike = meta.strike
        moneyness = (strike - spot) / spot if spot > 0 else 0

        bucket = "ATM"
        if meta.option_type == "P":
            if moneyness < -0.08: bucket = "-40D"
            elif moneyness < -0.03: bucket = "-20D"
            elif moneyness > 0.03: bucket = "+20D"
            elif moneyness > 0.08: bucket = "+40D"
        else:
            if moneyness > 0.08: bucket = "+40D"
            elif moneyness > 0.03: bucket = "+20D"
            elif moneyness < -0.03: bucket = "-20D"
            elif moneyness < -0.08: bucket = "-40D"

        surface.append({"dte": meta.dte, "strike": strike,
            "type": meta.option_type, "iv": round(iv_pct, 1),
            "bucket": bucket, "oi": round(oi, 1), "moneyness": round(moneyness, 3)})

        for db in dte_buckets:
            window = max(5, int(db * 0.6))
            if abs(meta.dte - db) <= window:
                term_data[db].append(iv_pct)
                break
        else:
            nearest = min(dte_buckets, key=lambda x: abs(meta.dte - x))
            term_data[nearest].append(iv_pct)

    term_structure = []
    for d in dte_buckets:
        ivs = term_data[d]
        avg_iv = sum(ivs) / len(ivs) if ivs else None
        term_structure.append({"dte": d, "avg_iv": round(avg_iv, 1) if avg_iv else None,
            "count": len(ivs), "min_iv": round(min(ivs), 1) if ivs else None,
            "max_iv": round(max(ivs), 1) if ivs else None})

    for i, ts in enumerate(term_structure):
        if ts["avg_iv"] is None:
            left = right = None
            for j in range(i-1, -1, -1):
                if term_structure[j]["avg_iv"] is not None: left = j; break
            for j in range(i+1, len(term_structure)):
                if term_structure[j]["avg_iv"] is not None: right = j; break
            if left is not None and right is not None:
                t_l, t_r, t_c = term_structure[left]["dte"], term_structure[right]["dte"], ts["dte"]
                v_l, v_r = term_structure[left]["avg_iv"], term_structure[right]["avg_iv"]
                ts["avg_iv"] = round(v_l + (v_r - v_l) * (t_c - t_l) / (t_r - t_l), 1)
                ts["interpolated"] = True
            elif left is not None:
                ts["avg_iv"] = term_structure[left]["avg_iv"]; ts["interpolated"] = True
            elif right is not None:
                ts["avg_iv"] = term_structure[right]["avg_iv"]; ts["interpolated"] = True

    backwardation = False
    alert_msg = ""
    valid_ts = [t for t in term_structure if t["avg_iv"] is not None]
    if len(valid_ts) >= 2:
        near = [t for t in valid_ts if t["dte"] <= 14]
        far = [t for t in valid_ts if t["dte"] >= 30]
        if near and far:
            na = sum(t["avg_iv"] for t in near) / len(near)
            fa = sum(t["avg_iv"] for t in far) / len(far)
            backwardation = na > fa * 1.02
            if backwardation:
                alert_msg = f"BACKWARDATION! Near={na:.1f}% > Far={fa:.1f}%"

    return {"currency": currency, "spot_price": _get_spot_from_scan(),
        "surface": sorted(surface, key=lambda x: (x["dte"], x["strike"]))[:500],
        "term_structure": term_structure, "backwardation": backwardation,
        "alert": alert_msg, "total": len(surface)}

def _get_spot_from_scan(currency: str = "BTC"):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT spot_price FROM scan_records WHERE currency=? AND spot_price > 0 ORDER BY timestamp DESC LIMIT 1", (currency,))
        row = cur.fetchone()
        if row and float(row[0]) > 0:
            return float(row[0])
    except Exception:
        pass
    try:
        import urllib.request
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={currency}USDT"
        resp = urllib.request.urlopen(url, timeout=5)
        return float(json.loads(resp.read())["price"])
    except Exception:
        pass
    return 0



# ============================================================
# Module 2: Max Pain & Gamma Exposure (GEX)
# ============================================================

@app.get("/api/metrics/max-pain")
async def get_max_pain(currency: str = Query(default="BTC")):
    return await _calc_max_pain_internal(currency)

async def _calc_max_pain_internal(currency: str):
    """内部函数：计算最大痛点"""
    summaries = _fetch_deribit_summaries(currency)
    if not summaries:
        return {"error": "No data"}

    parsed = []
    for s in summaries:
        meta = _parse_inst_name(s.get("instrument_name", ""))
        if not meta or meta.dte < 1:
            continue
        oi = float(s.get("open_interest") or 0)
        gamma = float(s.get("gamma") or 0)
        if oi < 1:
            continue
        parsed.append({"strike": meta.strike, "expiry": meta.expiry, "dte": meta.dte, "option_type": meta.option_type, "oi": oi, "gamma": gamma})

    if not parsed:
        # Debug: count how many items were filtered
        total = len(summaries)
        no_meta = sum(1 for s in summaries if not _parse_inst_name(s.get("instrument_name", "")))
        oi_zero = sum(1 for s in summaries if float(s.get("open_interest") or 0) < 1)
        return {"error": "No OI data", "debug": {"total": total, "no_meta": no_meta, "oi_zero": oi_zero, "oi_values": [float(s.get("open_interest") or 0) for s in summaries[:10]]}}

    strikes = sorted(set(p["strike"] for p in parsed))
    expiries = sorted(set((p["expiry"], p["dte"]) for p in parsed))

    try:
        spot = get_spot_price(currency)
    except Exception:
        db_spot = _get_spot_from_scan()
        spot = db_spot if db_spot > 1000 else (strikes[len(strikes)//2] if strikes else 0)

    results = []
    for exp_name, exp_dte in expiries[:4]:
        calls = [p for p in parsed if p["expiry"] == exp_name and p["option_type"] == "C"]
        puts = [p for p in parsed if p["expiry"] == exp_name and p["option_type"] == "P"]
        if not calls and not puts:
            continue
        co_map = {p["strike"]: p["oi"] for p in calls}
        po_map = {p["strike"]: p["oi"] for p in puts}
        cg_map = {p["strike"]: p["gamma"] * p["oi"] for p in calls}
        pg_map = {p["strike"]: p["gamma"] * p["oi"] for p in puts}

        mp_strike = strikes[0]
        min_pain = float('inf')
        pain_at_s = 0
        pc = []
        gc = []
        flip = None
        prev_sign = None

        for ts in strikes:
            cp = sum(max(0, ts - k) * v for k, v in co_map.items())
            pp = sum(max(0, k - ts) * v for k, v in po_map.items())
            tp = cp + pp
            pc.append({"strike": ts, "pain": round(tp, 0), "call_pain": round(cp, 0), "put_pain": round(pp, 0)})
            if tp < min_pain:
                min_pain = tp
                mp_strike = ts
            if int(round(ts)) == int(round(spot)):
                pain_at_s = tp
            ng = sum(g for k, g in cg_map.items() if k >= ts) + sum(-g for k, g in pg_map.items() if k <= ts)
            call_oi_above = sum(v for k, v in co_map.items() if k >= ts)
            put_oi_below = sum(v for k, v in po_map.items() if k <= ts)
            net_oi_exposure = call_oi_above - put_oi_below
            if ng != 0:
                ngex = ng * spot * spot / 100
            else:
                ngex = net_oi_exposure * 100
            gc.append({"strike": ts, "gex": round(ngex, 0), "net_gamma": round(ng, 2),
                       "net_oi_exposure": round(net_oi_exposure, 0),
                       "call_oi_above": round(call_oi_above, 0), "put_oi_below": round(put_oi_below, 0)})
            cs = 1 if net_oi_exposure >= 0 else -1
            if prev_sign is not None and cs != prev_sign:
                flip = ts
            prev_sign = cs

        dist = ((mp_strike - spot) / spot * 100) if spot > 0 else 0
        tco = sum(co_map.values())
        tpo = sum(po_map.values())
        pcr = tpo / tco if tco > 0 else 0
        sig = "中性"
        if dist > 3:
            sig = "偏多: 价格在最大痛点下方"
        elif dist < -3:
            sig = "偏空: 价格在最大痛点上方"
        mm = ""
        if flip:
            if spot < flip:
                mm = f"⚠️ 危险: 现货 ${spot:,.0f} < Flip点 ${flip:,.0f} | 空头Gamma区，波动放大风险"
            else:
                mm = f"✅ 安全: 现货 ${spot:,.0f} > Flip点 ${flip:,.0f} | 多头Gamma区，波动受抑"

        results.append({"expiry": exp_name, "dte": exp_dte, "max_pain": round(mp_strike, 0),
            "dist_pct": round(dist, 2), "pain_at_spot": round(pain_at_s, 0),
            "pcr": round(pcr, 3), "call_oi": round(tco, 0), "put_oi": round(tpo, 0),
            "signal": sig, "pain_curve": pc, "gex_curve": gc,
            "flip_point": flip, "mm_signal": mm})

    best = results[0] if results else {}
    return {"currency": currency, "spot": round(spot, 0), "expiries": results,
        "nearest_mp": best.get("max_pain"), "nearest_dist": best.get("dist_pct"),
        "signal": best.get("signal", ""), "mm_overview": best.get("mm_signal", "")}


# ============================================================
# Module 3: Martingale Sandbox Simulation Engine
# ============================================================

class SandboxParams(BaseModel):
    current_symbol: str = Field(default="BTC-26APR26-65000-P")
    crash_price: float = Field(default=45000.0, gt=1000)
    reserve_capital: float = Field(default=50000.0, ge=1000)
    num_contracts: int = Field(default=1)
    margin_ratio: float = Field(default=0.20, ge=0.05, le=1.0)


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



@app.get("/api/trades/history")
async def get_trades_history(
    days: int = Query(default=7),
    direction: str = Query(default=""),
    source: str = Query(default="")
):
    conn = get_db_connection()
    cursor = conn.cursor()
    since = datetime.utcnow() - timedelta(days=days)
    since_str = since.strftime('%Y-%m-%d %H:%M:%S')

    query = """
        SELECT * FROM large_trades_history 
        WHERE timestamp > ?
    """
    params = [since_str]

    if direction:
        query += " AND direction = ?"
        params.append(direction)
    if source:
        query += " AND source = ?"
        params.append(source)

    query += " ORDER BY timestamp DESC LIMIT 100"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    # conn.close()  # threading.local() manages per-thread connection lifecycle

    result = []
    for row in rows:
        item = dict(zip(cols, row))
        flow = item.get('flow_label', '')
        if flow and flow in FLOW_LABEL_MAP:
            item['flow_label_cn'] = FLOW_LABEL_MAP[flow][0]
            item['flow_desc'] = FLOW_LABEL_MAP[flow][1]
        result.append(item)

    return result


@app.get("/api/trades/strike-distribution")
async def get_strike_distribution(
    currency: str = Query(default="BTC"),
    days: int = Query(default=30)
):
    conn = get_db_connection()
    cursor = conn.cursor()
    since = datetime.utcnow() - timedelta(days=days)
    since_str = since.strftime('%Y-%m-%d %H:%M:%S')
    
    spot = get_spot_price(currency)

    cursor.execute("""
        SELECT strike, direction, COUNT(*) as count, SUM(volume) as total_volume,
               SUM(notional_usd) as total_notional,
               SUM(CASE WHEN direction='buy' THEN 1 ELSE 0 END) as buys,
               SUM(CASE WHEN direction='sell' THEN 1 ELSE 0 END) as sells
        FROM large_trades_history 
        WHERE currency = ? AND timestamp > ?
              AND strike IS NOT NULL AND strike > ? AND strike < ?
        GROUP BY strike, direction
        ORDER BY count DESC
        LIMIT 50
    """, (currency, since_str, int(spot * 0.15), int(spot * 4.0)))

    rows = cursor.fetchall()
    # conn.close()  # threading.local() manages per-thread connection lifecycle

    distribution = []
    for row in rows:
        distribution.append({
            "strike": row[0],
            "direction": row[1],
            "count": row[2],
            "total_volume": row[3] or 0,
            "total_notional": row[4] or 0,
            "buys": row[5] or 0,
            "sells": row[6] or 0
        })

    return distribution


@app.get("/api/trades/wind-analysis")
async def get_wind_analysis(
    currency: str = Query(default="BTC"),
    days: int = Query(default=30)
):
    conn = get_db_connection()
    cursor = conn.cursor()
    since = datetime.utcnow() - timedelta(days=days)
    since_str = since.strftime('%Y-%m-%d %H:%M:%S')

    spot = get_spot_price(currency)

    cursor.execute("""
        SELECT strike, option_type, direction, COUNT(*) as cnt,
               SUM(notional_usd) as total_notional,
               SUM(CASE WHEN direction='buy' THEN 1 ELSE 0 END) as buys,
               SUM(CASE WHEN direction='sell' THEN 1 ELSE 0 END) as sells,
               SUM(volume) as total_volume,
               MAX(instrument_name) as last_inst,
               MAX(flow_label) as flow_label
        FROM large_trades_history
        WHERE currency = ? AND timestamp > ? AND strike IS NOT NULL
              AND strike > ? AND strike < ?
              AND option_type IS NOT NULL AND option_type != ''
        GROUP BY strike, option_type
        ORDER BY strike ASC
    """, (currency, since_str, int(spot * 0.15), int(spot * 4.0)))

    strike_rows = cursor.fetchall()

    cursor.execute("""
        SELECT direction, option_type, delta, strike, notional_usd
        FROM large_trades_history 
        WHERE currency = ? AND timestamp > ? AND strike IS NOT NULL
              AND strike > ? AND strike < ?
        ORDER BY notional_usd DESC
    """, (currency, since_str, int(spot * 0.15), int(spot * 4.0)))

    flow_rows = cursor.fetchall()

    cursor.execute("""
        SELECT COUNT(*), 
               SUM(CASE WHEN direction='buy' THEN 1 ELSE 0 END),
               SUM(CASE WHEN direction='sell' THEN 1 ELSE 0 END),
               SUM(notional_usd)
        FROM large_trades_history 
        WHERE currency = ? AND timestamp > ?
    """, (currency, since_str))
    totals = cursor.fetchone()
    # conn.close()  # threading.local() manages per-thread connection lifecycle

    total_trades = totals[0] or 0
    total_buys = totals[1] or 0
    total_sells = totals[2] or 0
    total_notional = totals[3] or 0

    strike_flows = []
    max_abs_net = 1
    for row in strike_rows:
        strike = float(row[0] or 0)
        option_type = row[1] or ''
        buys = int(row[5] or 0)
        sells = int(row[6] or 0)
        net = buys - sells
        notional = row[4] or 0
        vol = row[7] or 0
        flow = row[9] or ''
        max_abs_net = max(max_abs_net, abs(net))

        dist_pct = ((strike - spot) / spot * 100) if spot > 0 else 0

        strike_flows.append({
            "strike": strike,
            "option_type": option_type,
            "buys": buys,
            "sells": sells,
            "net": net,
            "count": buys + sells,
            "notional": notional,
            "volume": vol,
            "dominant_flow": flow,
            "dist_from_spot_pct": round(dist_pct, 1)
        })

    strike_flows.sort(key=lambda x: x['strike'])

    support_level = None
    resistance_level = None
    heaviest_strike = None
    max_support_net = -99999
    max_resist_net = -99999
    max_count = 0

    for sf in strike_flows:
        if sf['count'] > max_count:
            max_count = sf['count']
            heaviest_strike = sf['strike']
        if sf['strike'] < spot and sf['net'] > max_support_net:
            max_support_net = sf['net']
            support_level = sf['strike']
        if sf['strike'] > spot and (-sf['net']) > max_resist_net:
            max_resist_net = -sf['net']
            resistance_level = sf['strike']

    # 8种核心分类映射
    CORE_FLOW_MAP = {
        "sell_put_deep_itm": ("保护性对冲", "深度ITM Sell Put，强烈看涨"),
        "sell_put_atm_itm": ("收权利金", "ATM Sell Put，温和看涨+收权"),
        "sell_put_otm": ("备兑开仓", "OTM Sell Put，纯收权"),
        "buy_put_deep_itm": ("保护性买入", "深度ITM Buy Put，机构对冲"),
        "buy_put_atm": ("看跌投机", "ATM Buy Put，短线看跌"),
        "buy_put_otm": ("看跌投机", "OTM Buy Put，投机看跌"),
        "sell_call_otm": ("备兑开仓", "OTM Sell Call，备兑开仓"),
        "sell_call_itm": ("改仓操作", "ITM Sell Call，改仓"),
        "buy_call_atm_itm": ("追涨建仓", "ATM Buy Call，顺势追涨"),
        "buy_call_otm": ("看涨投机", "OTM Buy Call，博反弹"),
        "unknown": ("未知流向", "无法判断交易意图"),
    }

    flow_agg = {}
    for row in flow_rows:
        direction = row[0] or ''
        opt_type = row[1] or ''
        delta_val = row[2] or 0
        strike = row[3] or 0
        notional = row[4] or 0

        fl = _classify_flow_heuristic(direction, opt_type, float(delta_val), strike, spot)
        if fl not in flow_agg:
            flow_agg[fl] = {"count": 0, "notional": 0}
        flow_agg[fl]["count"] += 1
        flow_agg[fl]["notional"] += notional

    # 合并为8种核心分类
    core_agg = {}
    for fl, agg in flow_agg.items():
        core_info = CORE_FLOW_MAP.get(fl)
        if core_info:
            core_name = core_info[0]
        else:
            core_name = fl
        if core_name not in core_agg:
            core_agg[core_name] = {"count": 0, "notional": 0, "desc": core_info[1] if core_info else ""}
        core_agg[core_name]["count"] += agg["count"]
        core_agg[core_name]["notional"] += agg["notional"]

    flow_breakdown = []
    dominant_flow = ""
    max_flow_cnt = 0
    for fl, agg in sorted(core_agg.items(), key=lambda x: x[1]["count"], reverse=True):
        cnt = agg["count"]
        notional = agg["notional"]
        pct = (cnt / total_trades * 100) if total_trades > 0 else 0
        flow_breakdown.append({
            "label": fl,
            "label_cn": fl,
            "desc": agg.get("desc", ""),
            "count": cnt,
            "notional": round(notional, 0),
            "pct": round(pct, 1)
        })
        if fl != 'unknown' and cnt > max_flow_cnt:
            max_flow_cnt = cnt
            dominant_flow = fl

    if not dominant_flow:
        dominant_flow = 'unknown'

    buy_ratio = total_buys / total_trades if total_trades > 0 else 0.5

    sentiment_score = 0
    if buy_ratio > 0.55:
        sentiment_score = min(3, int((buy_ratio - 0.5) * 20))
    elif buy_ratio < 0.45:
        sentiment_score = max(-3, int((buy_ratio - 0.5) * 20))

    bullish_flows = ('保护性对冲', '收权利金', '备兑开仓', '追涨建仓', '看涨投机')
    bearish_flows = ('保护性买入', '看跌投机', '改仓操作')
    if dominant_flow in bullish_flows:
        sentiment_score += 1
    elif dominant_flow in bearish_flows:
        sentiment_score -= 1

    summary = {
        "total_trades": total_trades,
        "total_notional": round(total_notional, 0),
        "buy_ratio": round(buy_ratio, 2),
        "sell_ratio": round(1 - buy_ratio, 2),
        "sentiment_score": sentiment_score,
        "dominant_flow": dominant_flow,
        "key_levels": {
            "heaviest_strike": heaviest_strike,
            "net_support": support_level,
            "net_resistance": resistance_level,
        },
        "spot_price": round(spot, 0) if spot else 0,
        "time_range": f"近{days}天",
    }

    sentiment_text = generate_wind_sentiment(summary, spot)

    return {
        "summary": summary,
        "sentiment_text": sentiment_text,
        "strike_flows": strike_flows,
        "flow_breakdown": flow_breakdown,
    }


# STRATEGY_PRESETS moved to config.py
# adapt_params_by_dvol moved to services/dvol_analyzer.py

def _calc_iv_rank(current_iv: float, history_ivs: list) -> float:
    if not history_ivs or current_iv <= 0: return 50.0
    sorted_ivs = sorted(history_ivs); n = len(sorted_ivs)
    rank = 1
    for i, v in enumerate(sorted_ivs):
        if v >= current_iv: rank = i + 1; break
    else: rank = n
    if n == 1: return 50.0
    return round((rank - 1) / (n - 1) * 100, 1)

def _calc_pop(delta_val, option_type, spot, strike, iv, dte):
    """基于 Black-Scholes 的概率盈利计算"""
    if dte <= 0 or spot <= 0 or strike <= 0 or iv <= 0:
        return 50.0
    T = dte / 365.0
    r = 0.01
    sqrt_t = math.sqrt(T)
    d1 = (math.log(spot / strike) + (r + iv ** 2 / 2) * T) / (iv * sqrt_t) if iv * sqrt_t > 0 else 0
    if option_type.upper() in ('P', 'PUT'):
        pop = _norm_cdf(-d1) * 100
    else:
        pop = _norm_cdf(d1) * 100
    pop = max(5.0, min(95.0, round(pop, 1)))
    return pop

def _calc_breakeven_pct(spot, strike, premium_usd, option_type):
    premium_per_unit = premium_usd / spot if spot > 0 else 0
    if option_type.upper() in ('P', 'PUT'):
        safety = (spot - (strike - premium_per_unit)) / spot * 100
    else:
        safety = ((strike + premium_per_unit) - spot) / spot * 100
    return round(max(0, safety), 1)


@app.get("/api/bottom-fishing/advice")
async def get_bottom_fishing_advice(currency: str = Query(default="BTC")):
    """v6.0: 抄底建议 API - 结合风险框架、最大痛点和 GEX"""
    spot = get_spot_price(currency)
    status = RiskFramework.get_status(spot)
    
    # 获取最大痛点和 GEX
    try:
        pain_data = await _calc_max_pain_internal(currency)
        nearest_mp = pain_data.get("nearest_mp")
        mm_signal = pain_data.get("mm_overview", "")
    except Exception:
        nearest_mp = None
        mm_signal = ""
        
    advice = []
    actions = []
    
    if status == "NORMAL":
        advice.append(f"当前价格 ${spot:,.0f} 处于常规区间（高于 $55k）。")
        advice.append("建议：以获取 200% APR 为目标，保持低杠杆。")
        actions.append("卖出 OTM Put (Delta 0.15-0.25)")
    elif status == "NEAR_FLOOR":
        advice.append(f"当前价格 ${spot:,.0f} 接近常规底 ($55k)。")
        advice.append("建议：可适当增加仓位，博取高 Theta 收益。")
        actions.append("卖出 ATM/ITM Put 并在跌破时准备滚仓")
    elif status == "ADVERSE":
        advice.append(f"市场处于逆境区 (${spot:,.0f} < $55k)。")
        advice.append("建议：启用后备资金 ($50k)，高杠杆快平仓，积极执行 Rolling Down & Out。")
        actions.append("将持仓滚动至 $45k - $50k 区间")
    elif status == "PANIC":
        advice.append(f"⚠️ 警告：价格已破极限底 $45k！")
        advice.append("核心指令：止损并承认失败，保留剩余本金。不要在此区域接货。")
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
        "max_pain": nearest_mp,
        "mm_signal": mm_signal,
        "advice": advice,
        "recommended_actions": actions,
        "floors": {
            "regular": RiskFramework.REGULAR_FLOOR,
            "extreme": RiskFramework.EXTREME_FLOOR
        }
    }
