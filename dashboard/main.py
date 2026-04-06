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
import requests
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = Path(__file__).parent / "data" / "monitor.db"
DB_PATH.parent.mkdir(exist_ok=True)


class ScanParams(BaseModel):
    currency: str = Field(default="BTC", description="币种")
    min_dte: int = Field(default=14, ge=1, le=365, description="最小到期天数")
    max_dte: int = Field(default=25, ge=1, le=365, description="最大到期天数")
    max_delta: float = Field(default=0.4, ge=0.01, le=1.0, description="最大Delta")
    margin_ratio: float = Field(default=0.2, ge=0.05, le=1.0, description="保证金比率")
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$")
    strike: Optional[float] = Field(default=None, description="特定行权价")
    strike_range: Optional[str] = Field(default=None, description="行权价范围，如 60000-65000")


class RecoveryCalcParams(BaseModel):
    currency: str = Field(default="BTC", description="币种")
    current_loss: float = Field(..., gt=0, description="当前浮亏金额(USDT)")
    target_apr: float = Field(default=200, ge=50, le=500, description="目标年化收益率(%)")
    max_contracts: int = Field(default=10, ge=1, le=50, description="最大合约数量")
    max_delta: float = Field(default=0.45, ge=0.1, le=0.8, description="最大Delta容忍")


def get_spot_price_binance(currency: str = "BTC") -> Optional[float]:
    try:
        symbol = f"{currency}USDT"
        response = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=10
        )
        data = response.json()
        return float(data.get("price", 0))
    except Exception as e:
        print(f"获取现货价格失败: {e}", file=sys.stderr)
        return None


def get_dvol_from_deribit(currency: str = "BTC") -> Dict[str, Any]:
    try:
        response = requests.get(
            "https://www.deribit.com/api/v2/public/get_volatility_index_data",
            params={
                "currency": currency,
                "resolution": "3600",
                "start_timestamp": int((datetime.now() - timedelta(days=7)).timestamp() * 1000),
                "end_timestamp": int(datetime.now().timestamp() * 1000)
            },
            timeout=10
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

                if z_score > 2:
                    signal = "异常偏高"
                elif z_score > 1:
                    signal = "偏高"
                elif z_score < -2:
                    signal = "异常偏低"
                elif z_score < -1:
                    signal = "偏低"
                else:
                    signal = "正常区间"

                return {
                    "current": round(current, 2),
                    "z_score": round(z_score, 2),
                    "signal": signal
                }
        return {}
    except Exception as e:
        print(f"获取DVOL失败: {e}", file=sys.stderr)
        return {}


FLOW_LABEL_MAP = {
    "protective_hedge": ("保护性对冲", "机构买入Put对冲下行风险"),
    "premium_collect": ("收权利金", "卖出Put/Call收取权利金"),
    "speculative_put": ("看跌投机", "投机性买入看跌期权"),
    "speculative_call": ("看涨投机", "投机性买入看涨期权"),
    "call_momentum": ("追涨建仓", "高Delta Call买入，看好上涨"),
    "call_speculative": ("虚值博彩", "低Delta Call买入，博反弹"),
    "covered_call": ("备兑开仓", "卖出Call锁定收益"),
    "call_overwrite": ("改仓操作", "高Delta Call卖出改仓"),
}

def parse_trade_alert(trade: Dict[str, Any], currency: str, timestamp: str) -> Dict[str, Any]:
    title = trade.get('title', '')
    message = trade.get('message', '')

    source = 'Unknown'
    if 'Deribit' in message or 'deribit' in message.lower():
        source = 'Deribit'
    elif 'Binance' in message or 'binance' in message.lower():
        source = 'Binance'

    direction = trade.get('direction', 'unknown')
    if direction == 'unknown':
        if any(w in message.lower() for w in ['buy', '买入', '购买']):
            direction = 'buy'
        elif any(w in message.lower() for w in ['sell', '卖出', '出售']):
            direction = 'sell'

    strike = trade.get('strike')
    if not strike:
        strike_match = re.search(r'(?:strike|行权价)?[:\s]*(\d{3}(?:,\d{3})*)\s*(?:PUT|CALL)', message, re.IGNORECASE)
        if not strike_match:
            strike_match = re.search(r'\$(\d{3}(?:,\d{3})*)', message)
        if strike_match:
            try:
                strike = float(strike_match.group(1).replace(',', ''))
            except ValueError:
                pass

    volume = trade.get('amount', 0) or trade.get('volume', 0) or 0
    if not volume or volume == 0:
        for pattern in [
            r'(\d+(?:\.\d+)?)\s*(?:BTC|ETH|SOL)\s*(?:worth|价值)',
            r'\$([\d,]+(?:\.\d+)?)',
            r'(\d+(?:,\d{3})*)\s*(?:contracts?|张)',
        ]:
            match = re.search(pattern, message)
            if match:
                try:
                    volume = float(match.group(1).replace(',', ''))
                    break
                except ValueError:
                    continue

    option_type = None
    if 'PUT' in message.upper() or 'put' in message.lower():
        option_type = 'PUT'
    elif 'CALL' in message.upper() or 'call' in message.lower():
        option_type = 'CALL'

    flow_label = trade.get('flow_label', '')
    notional_usd = trade.get('underlying_notional_usd', 0) or 0
    delta = trade.get('delta', 0) or 0
    instrument_name = trade.get('instrument_name', '') or trade.get('symbol', '') or ''

    return {
        'timestamp': timestamp,
        'currency': currency,
        'source': source,
        'title': title,
        'message': message,
        'direction': direction,
        'strike': strike,
        'volume': volume,
        'option_type': option_type,
        'flow_label': flow_label,
        'notional_usd': float(notional_usd) if notional_usd else 0,
        'delta': float(delta) if delta else 0,
        'instrument_name': instrument_name,
    }


def init_database():
    conn = sqlite3.connect(DB_PATH)
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
    conn.close()


def save_scan_record(data: Dict[str, Any]):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
        json.dumps(data.get('dvol_raw', {}), ensure_ascii=False)
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

    cursor.execute("DELETE FROM scan_records WHERE timestamp < datetime('now', '-90 days')")
    cursor.execute("DELETE FROM large_trades_history WHERE timestamp < datetime('now', '-90 days')")

    conn.commit()
    conn.close()


def run_options_scan(params: ScanParams) -> Dict[str, Any]:
    base_dir = Path(__file__).parent.parent

    spot_price = get_spot_price_binance(params.currency)
    dvol_data = get_dvol_from_deribit(params.currency)

    dvol_full = get_dvol_from_deribit(params.currency)
    dvol_raw_for_adapt = {}
    if isinstance(dvol_full, dict):
        dvol_raw_for_adapt = dvol_full

    scan_params = {
        "max_delta": params.max_delta,
        "min_dte": params.min_dte,
        "max_dte": params.max_dte,
        "margin_ratio": params.margin_ratio,
        "option_type": params.option_type,
        "min_apr": 15.0
    }
    adapted = adapt_params_by_dvol(scan_params, dvol_raw_for_adapt)

    use_delta = adapted.get('max_delta', params.max_delta)
    use_min_dte = adapted.get('min_dte', params.min_dte)
    use_max_dte = adapted.get('max_dte', params.max_dte)
    use_margin = adapted.get('margin_ratio', params.margin_ratio)

    cmd = [
        sys.executable,
        str(base_dir / "options_aggregator.py"),
        "--currency", params.currency,
        "--min-dte", str(use_min_dte),
        "--max-dte", str(use_max_dte),
        "--max-delta", str(use_delta),
        "--margin-ratio", str(use_margin),
        "--option-type", params.option_type,
        "--json"
    ]

    if params.strike:
        cmd.extend(["--strike", str(int(params.strike))])
    if params.strike_range:
        cmd.extend(["--strike-range", params.strike_range])

    try:
        result = await run_in_threadpool(
            subprocess.run,
            cmd,
            capture_output=True,
            text=False,
            timeout=120
        )

        if result.returncode != 0:
            return {"success": False, "error": result.stderr.decode('utf-8', errors='replace') or "扫描失败"}

        output_text = result.stdout.decode('utf-8', errors='replace')
        try:
            parsed = json.loads(output_text)
            parsed['success'] = True
        except json.JSONDecodeError:
            try:
                output_text = result.stdout.decode('gbk', errors='replace')
                parsed = json.loads(output_text)
                parsed['success'] = True
            except Exception as e:
                return {"success": False, "error": "JSON 解析失败", "raw": output_text[:200]}
        parsed['success'] = True

        if spot_price:
            parsed['spot_price'] = spot_price
        if dvol_data.get('current'):
            parsed['dvol_current'] = dvol_data['current']
            parsed['dvol_z_score'] = dvol_data['z_score']
            parsed['dvol_signal'] = dvol_data['signal']

        save_scan_record(parsed)

        parsed['dvol_advice'] = adapted.get('_dvol_advice', [])
        parsed['dvol_adjustment'] = adapted.get('_adjustment_level', 'none')
        parsed['adapted_params'] = {
            'max_delta': use_delta,
            'min_dte': use_min_dte,
            'max_dte': use_max_dte
        }

        return parsed

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "扫描超时"}
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"JSON解析错误: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


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

    net_buy = summary.get('net_buy_count', 0)
    net_sell = summary.get('net_sell_count', 0)
    total = net_buy + net_sell
    if total > 0:
        buy_pct = net_buy / total * 100
        if buy_pct > 60:
            parts.append(f"买盘主导({buy_pct:.0f}%)")
        elif buy_pct < 40:
            parts.append(f"卖盘主导({100-buy_pct:.0f}%)")


    support = kr.get('net_support')
    resistance = kr.get('net_resistance')
    if support and resistance:
        spct_s = (support - spot) / spot * 100
        spct_r = (resistance - spot) / spot * 100
        parts.append(f"支撑${support/1000:.0f}K({spct_s:+.1f}%)/阻力${resistance/1000:.0f}K({spct_r:+.1f}%)")

    top_flow = summary.get('dominant_flow')
    if top_flow:
        label_info = FLOW_LABEL_MAP.get(top_flow)
        if label_info:
            parts.append(f"主流行为:{label_info[0]}")

    if not parts:
        return "数据不足，暂无法判断"
    return " | ".join(parts)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    yield

app = FastAPI(title="期权监控面板", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


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


@app.get("/api/latest")
async def get_latest_scan(currency: str = Query(default="BTC")):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM scan_records 
        WHERE currency = ? 
        ORDER BY timestamp DESC 
        LIMIT 1
    """, (currency,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="暂无数据")

    _dvol_raw = {}
    if row[10]:
        try: _dvol_raw = json.loads(row[10])
        except: pass

    try:
        large_trades = json.loads(row[8]) if row[8] else []
    except:
        large_trades = row[8] if isinstance(row[8], list) else []

    return {
        "timestamp": row[1],
        "currency": row[2],
        "spot_price": row[3],
        "dvol_current": row[4],
        "dvol_z_score": row[5],
        "dvol_signal": row[6],
        "large_trades_count": row[7],
        "large_trades_details": large_trades,
        "contracts": json.loads(row[9]) if row[9] else [],
        "dvol_raw": _dvol_raw
    }


@app.post("/api/recovery-calculate")
async def calculate_recovery(params: RecoveryCalcParams):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT contracts_data, spot_price FROM scan_records 
        WHERE currency = ? ORDER BY timestamp DESC LIMIT 1
    """, (params.currency,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="暂无扫描数据，请先执行扫描")

    try:
        contracts = json.loads(row[0]) if row[0] else []
    except:
        contracts = []

    spot = row[1] or 0
    result = calculate_recovery_plan(contracts, params, spot)
    return result


@app.get("/api/stats")
async def get_stats():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM scan_records")
    total_scans = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM scan_records WHERE date(timestamp) = date('now')")
    today_scans = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM large_trades_history")
    total_trades = cursor.fetchone()[0]

    db_size = os.path.getsize(DB_PATH)

    conn.close()

    return {
        "total_scans": total_scans,
        "today_scans": today_scans,
        "total_large_trades": total_trades,
        "db_size_mb": round(db_size / (1024 * 1024), 2)
    }


@app.get("/api/health")
async def health_check():
    checks = {}

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM scan_records")
        count = cursor.fetchone()[0]
        conn.close()
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
async def get_apr_chart(hours: int = Query(default=168)):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    since = datetime.now() - timedelta(hours=hours)

    cursor.execute("""
        SELECT timestamp, 
               AVG(CAST(json_extract(contracts_data, '$[0].apr') AS REAL)) as avg_apr,
               MAX(CAST(json_extract(contracts_data, '$[0].apr') AS REAL)) as max_apr
        FROM scan_records 
        WHERE timestamp > ?
        GROUP BY DATE(timestamp), HOUR(timestamp)
        ORDER BY timestamp ASC
    """, (since.strftime('%Y-%m-%d %H:%M:%S'),))

    rows = cursor.fetchall()
    conn.close()

    return [{"time": r[0], "avg_apr": r[1] or 0, "max_apr": r[2] or 0} for r in rows]


@app.get("/api/charts/dvol")
async def get_dvol_chart(hours: int = Query(default=168)):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    since = datetime.now() - timedelta(hours=hours)

    cursor.execute("""
        SELECT timestamp, dvol_current, dvol_z_score, dvol_signal
        FROM scan_records 
        WHERE timestamp > ? AND dvol_current IS NOT NULL
        ORDER BY timestamp ASC
    """, (since.strftime('%Y-%m-%d %H:%M:%S'),))

    rows = cursor.fetchall()
    conn.close()

    return [{"time": r[0], "dvol": r[1], "z_score": r[2], "signal": r[3]} for r in rows]


@app.get("/api/trades/history")
async def get_trades_history(
    days: int = Query(default=7),
    direction: str = Query(default=""),
    source: str = Query(default="")
):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    since = datetime.now() - timedelta(days=days)
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
    conn.close()

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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    since = datetime.now() - timedelta(days=days)
    since_str = since.strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute("""
        SELECT strike, direction, COUNT(*) as count, SUM(volume) as total_volume,
               SUM(notional_usd) as total_notional,
               SUM(CASE WHEN direction='buy' THEN 1 ELSE 0 END) as buys,
               SUM(CASE WHEN direction='sell' THEN 1 ELSE 0 END) as sells
        FROM large_trades_history 
        WHERE currency = ? AND timestamp > ? AND strike IS NOT NULL
        GROUP BY strike, direction
        ORDER BY count DESC
        LIMIT 50
    """, (currency, since_str))

    rows = cursor.fetchall()
    conn.close()

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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    since = datetime.now() - timedelta(days=days)
    since_str = since.strftime('%Y-%m-%d %H:%M:%S')

    spot = get_spot_price_binance(currency) or 0

    cursor.execute("""
        SELECT strike, direction, COUNT(*) as cnt,
               SUM(notional_usd) as total_notional,
               SUM(CASE WHEN direction='buy' THEN 1 ELSE 0 END) as buys,
               SUM(CASE WHEN direction='sell' THEN 1 ELSE 0 END) as sells,
               SUM(volume) as total_volume,
               flow_label
        FROM large_trades_history 
        WHERE currency = ? AND timestamp > ? AND strike IS NOT NULL
        GROUP BY strike
        ORDER BY cnt DESC
    """, (currency, since_str))

    strike_rows = cursor.fetchall()

    cursor.execute("""
        SELECT direction, COUNT(*) as cnt, SUM(notional_usd) as total_notional, flow_label
        FROM large_trades_history 
        WHERE currency = ? AND timestamp > ?
        GROUP BY flow_label
        ORDER BY cnt DESC
    """, (currency, since_str))

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
    conn.close()

    total_trades = totals[0] or 0
    total_buys = totals[1] or 0
    total_sells = totals[2] or 0
    total_notional = totals[3] or 0

    strike_flows = []
    max_abs_net = 1
    for row in strike_rows:
        strike = row[0]
        buys = row[4] or 0
        sells = row[5] or 0
        net = buys - sells
        notional = row[3] or 0
        vol = row[6] or 0
        flow = row[7] or ''
        max_abs_net = max(max_abs_net, abs(net))

        dist_pct = ((strike - spot) / spot * 100) if spot > 0 else 0

        strike_flows.append({
            "strike": strike,
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

    flow_breakdown = []
    dominant_flow = ""
    max_flow_cnt = 0
    for row in flow_rows:
        fl = row[3] or 'unknown'
        cnt = row[1]
        notional = row[3] or 0
        pct = (cnt / total_trades * 100) if total_trades > 0 else 0
        info = FLOW_LABEL_MAP.get(fl, (fl, ""))
        flow_breakdown.append({
            "label": fl,
            "label_cn": info[0] if info else fl,
            "desc": info[1] if info else "",
            "count": cnt,
            "notional": round(notional, 0),
            "pct": round(pct, 1)
        })
        if cnt > max_flow_cnt:
            max_flow_cnt = cnt
            dominant_flow = fl

    buy_ratio = total_buys / total_trades if total_trades > 0 else 0.5

    sentiment_score = 0
    if buy_ratio > 0.55:
        sentiment_score = min(3, int((buy_ratio - 0.5) * 20))
    elif buy_ratio < 0.45:
        sentiment_score = max(-3, int((buy_ratio - 0.5) * 20))

    if dominant_flow in ('protective_hedge', 'premium_collect'):
        sentiment_score += 1
    elif dominant_flow in ('speculative_put',):
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
        "strike_flows": strike_flows[:25],
        "flow_breakdown": flow_breakdown,
    }


STRATEGY_PRESETS = {
    "PUT": {
        "conservative": {"max_delta": 0.20, "min_dte": 30, "max_dte": 45, "margin_ratio": 0.18, "min_apr": 12.0},
        "standard":     {"max_delta": 0.30, "min_dte": 14, "max_dte": 35, "margin_ratio": 0.20, "min_apr": 15.0},
        "aggressive":   {"max_delta": 0.40, "min_dte": 7,  "max_dte": 28, "margin_ratio": 0.22, "min_apr": 20.0}
    },
    "CALL": {
        "conservative": {"max_delta": 0.30, "min_dte": 30, "max_dte": 45, "margin_ratio": 0.18, "min_apr": 10.0},
        "standard":     {"max_delta": 0.45, "min_dte": 14, "max_dte": 35, "margin_ratio": 0.20, "min_apr": 12.0},
        "aggressive":   {"max_delta": 0.55, "min_dte": 7,  "max_dte": 28, "margin_ratio": 0.22, "min_apr": 18.0}
    }
}


def adapt_params_by_dvol(params: dict, dvol_raw: dict) -> dict:
    pct_7d = (dvol_raw.get('iv_percentile_7d') or 50)
    trend = dvol_raw.get('trend', '')

    adjusted = dict(params)
    advice = []

    if isinstance(pct_7d, (int, float)):
        if pct_7d >= 80:
            adjusted['max_delta'] = round(adjusted['max_delta'] * 0.70, 2)
            adjusted['min_apr'] = adjusted.get('min_apr', 15) + 5
            advice.append("高波动环境: 自动收紧Delta，提高APR门槛")
        elif pct_7d <= 20:
            adjusted['max_delta'] = min(round(adjusted['max_delta'] * 1.20, 2), 0.60)
            adjusted['min_apr'] = max(adjusted.get('min_apr', 15) - 5, 8)
            advice.append("低波动环境: 权利金偏薄，适当放宽参数")

    if trend == '上涨' and params.get('option_type') == 'PUT':
        advice.append("DVOL上升趋势+反弹阶段，适合Sell Put接货")
    elif trend == '下跌':
        if params.get('option_type') == 'CALL':
            advice.append("市场下跌后反弹，Covered Call可锁定收益")
        else:
            advice.append("市场处于下跌阶段，建议降低仓位或观望")

    adjusted['_dvol_advice'] = advice
    return adjusted
