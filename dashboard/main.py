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
        for host in ["api3.binance.com", "api2.binance.com", "api1.binance.com"]:
            try:
                response = requests.get(
                    f"https://{host}/api/v3/ticker/price",
                    params={"symbol": symbol},
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    return float(data.get("price", 0))
            except:
                continue
    except Exception as e:
        print(f"获取现货价格失败: {e}", file=sys.stderr)
    return None


def get_spot_price_deribit(currency: str = "BTC") -> Optional[float]:
    try:
        response = requests.get(
            "https://www.deribit.com/api/v2/public/get_index_price",
            params={"currency": currency, "index_name": f"{currency}_usd"},
            timeout=10
        )
        data = response.json()
        if data.get("result"):
            return float(data["result"]["index_price"])
    except Exception as e:
        print(f"获取Deribit现货价格失败: {e}", file=sys.stderr)
    return None


def get_spot_price(currency: str = "BTC") -> float:
    # 优先使用原生API
    spot = get_spot_price_binance(currency)
    if spot and spot > 1000:
        return spot
    spot = get_spot_price_deribit(currency)
    if spot and spot > 1000:
        return spot
    # 备用ccxt
    try:
        import ccxt
        if currency == "BTC":
            symbol = "BTC/USDT"
        elif currency == "ETH":
            symbol = "ETH/USDT"
        else:
            symbol = f"{currency}/USDT"
        deribit = ccxt.deribit()
        ticker = deribit.fetch_ticker(symbol)
        if ticker and ticker.get('last') and ticker['last'] > 100:
            return float(ticker['last'])
    except Exception as e:
        print(f"ccxt deribit failed: {e}")
    if currency == "BTC":
        return 100000.0
    elif currency == "ETH":
        return 5000.0
    return 100000.0


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
    "call_speculative": ("看涨投机", "低Delta Call买入，博反弹"),
    "call_momentum": ("追涨建仓", "高Delta Call买入，看好上涨"),
    "covered_call": ("备兑开仓", "卖出Call锁定收益"),
    "call_overwrite": ("改仓操作", "高Delta Call卖出改仓"),
}
def _classify_flow_heuristic(direction, option_type, delta, strike, spot):
    """流向分类 (参考 lianyanshe-ai/deribit-options-monitor 原始逻辑)
    
    PUT:
      buy  + 0.10<=delta<=0.35 + 7<=dte<=60 -> protective_hedge
      buy  + other                     -> speculative_put
      sell + delta<=0.35               -> premium_collect  
      sell + delta>0.35                -> speculative_put
    
    CALL:
      buy  + delta>=0.30               -> call_momentum
      buy  + delta<0.30                -> call_speculative
      sell + delta<=0.40               -> covered_call
      sell + delta>0.40                -> call_overwrite
    """
    if not direction or direction == "unknown" or not option_type:
        return "unclassified"
    d = abs(delta or 0)
    if direction == "buy":
        if option_type in ("PUT", "P"):
            if 0.10 <= d <= 0.35:
                return "protective_hedge"
            return "speculative_put"
        elif option_type in ("CALL", "C"):
            if d >= 0.30:
                return "call_momentum"
            return "call_speculative"
    elif direction == "sell":
        if option_type in ("PUT", "P"):
            if d <= 0.35:
                return "premium_collect"
            return "speculative_put"
        elif option_type in ("CALL", "C"):
            if d <= 0.40:
                return "covered_call"
            return "call_overwrite"
    return "unclassified"


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
        ins_name = trade.get('instrument_name') or trade.get('symbol') or ''
        ins_match = re.search(r'-(\d+)-[PC]$', str(ins_name))
        if ins_match:
            try:
                strike = float(ins_match.group(1))
            except ValueError:
                pass
    if not strike:
        msg_match = re.search(r'(?:strike|行权价)?[:\s]*(\d{3}(?:,\d{3})*)\s*(?:PUT|CALL|-[PC])', message, re.IGNORECASE)
        if msg_match:
            try:
                strike = float(msg_match.group(1).replace(',', ''))
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

    spot_price = get_spot_price(params.currency)
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
        result = subprocess.run(
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
async def quick_scan(params: dict = None):
    """快速扫描：直接获取Deribit数据，不依赖options_aggregator.py"""
    from datetime import datetime
    currency = "BTC"
    spot = get_spot_price(currency)
    if spot < 1000:
        spot = 69455.0

    # 获取DVOL数据
    dvol_data = get_dvol_from_deribit(currency)
    dvol_current = dvol_data.get('current', 0) or 0
    dvol_z = dvol_data.get('z_score', 0) or 0
    dvol_signal = dvol_data.get('signal', '正常区间')

    try:
        summaries = _fetch_derivit_summaries(currency)
        if not summaries:
            return {"success": False, "error": "无法获取Deribit数据"}

        contracts = []
        for s in summaries:
            meta = _parse_inst_name(s.get("instrument_name", ""))
            if not meta: continue
            if meta["dte"] < 7 or meta["dte"] > 90: continue
            
            # 必须和用户请求的 option_type (PUT/CALL) 匹配
            req_type = params.get("option_type", "PUT").upper() if params else "PUT"
            req_type_short = "P" if req_type == "PUT" else "C"
            if meta["option_type"] != req_type_short: continue
                
            iv = float(s.get("mark_iv") or 0) / 100.0
            prem = float(s.get("mark_price") or 0)
            oi = float(s.get("open_interest") or 0)
            if iv <= 0 or prem <= 0 or oi < 10: continue

            strike = meta["strike"]
            underlying = float(s.get("underlying_price", spot)) or spot

            # Delta 过滤 (使用估算值，Deribit summaries不返回delta字段)
            raw_delta = s.get("delta")
            if raw_delta is None or float(raw_delta or 0) == 0:
                delta_val = abs(_estimate_delta(strike, underlying, iv, meta["dte"], meta["option_type"]))
            else:
                delta_val = abs(float(raw_delta))
            max_delta = params.get("max_delta", 0.4) if params else 0.4
            if delta_val > max_delta: continue
            prem_usd = prem * underlying
            
            # 使用正确的 Margin APR 公式 (默认 20% 保证金)
            margin_ratio = params.get("margin_ratio", 0.2) if params else 0.2
            cv = strike * margin_ratio
            apr = (prem_usd / cv) * (365 / meta["dte"]) * 100 if cv > 0 else 0
            
            dist = abs(strike - spot) / spot * 100

            contracts.append({
                "symbol": s.get("instrument_name", ""),
                "platform": "Deribit",
                "expiry": meta["expiry"],
                "dte": meta["dte"],
                "option_type": meta["option_type"],
                "strike": strike,
                "apr": round(apr, 1),
                "premium_usd": round(prem_usd, 2),
                "delta": round(delta_val, 3),
                "iv": round(iv * 100, 1),
                "open_interest": round(oi, 0),
                "loss_at_10pct": round(cv * 0.1, 2),
                "breakeven": round(strike - prem_usd if meta["option_type"] == "P" else strike + prem_usd, 0),
                "distance_spot_pct": round(dist, 1),
                "spread_pct": 0.1,
                "liquidity_score": min(100, int((oi / 500) * 100)) # 给 Deribit 一个基于 OI 的动态评分
            })

        # 抓取 Binance 数据
        try:
            import requests, time
            r_mark = requests.get('https://eapi.binance.com/eapi/v1/mark', timeout=10).json()
            r_info = requests.get('https://eapi.binance.com/eapi/v1/exchangeInfo', timeout=10).json()
            r_ticker = requests.get('https://eapi.binance.com/eapi/v1/ticker', timeout=10).json()
            
            now_ms = time.time() * 1000
            req_type = params.get("option_type", "PUT").upper() if params else "PUT"
            max_delta = params.get("max_delta", 0.4) if params else 0.4
            margin_ratio = params.get("margin_ratio", 0.2) if params else 0.2

            for s in r_info.get('optionSymbols', []):
                if s['underlying'] != f"{currency}USDT": continue
                if s['side'] != req_type: continue
                
                dte = (s['expiryDate'] - now_ms) / 86400000
                if not (7 <= dte <= 90): continue
                
                mark = next((m for m in r_mark if m['symbol'] == s['symbol']), None)
                if not mark or float(mark['markPrice']) <= 0: continue
                
                delta_val = abs(float(mark['delta']))
                if delta_val > max_delta: continue
                
                ticker = next((t for t in r_ticker if t['symbol'] == s['symbol']), None)
                volume = float(ticker['volume']) if ticker else 0
                bid = float(ticker['bidPrice']) if ticker else 0
                ask = float(ticker['askPrice']) if ticker else 0
                
                if volume < 5: continue # Binance 流动性极差的过滤
                
                strike = float(s['strikePrice'])
                prem_usd = float(mark['markPrice'])
                cv = strike * margin_ratio
                apr = (prem_usd / cv) * (365 / dte) * 100 if cv > 0 else 0
                iv = float(mark['markIV']) * 100
                opt_type = 'P' if s['side'] == 'PUT' else 'C'
                
                spread_pct = 0.0
                if bid > 0: spread_pct = ((ask - bid) / bid) * 100
                liq_score = min(50, (volume / 100) * 50) + max(0, 50 - (spread_pct * 5))

                dist = abs(strike - spot) / spot * 100
                breakeven = strike - prem_usd if opt_type == 'P' else strike + prem_usd
                
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
                    "iv": round(iv, 1),
                    "open_interest": volume,
                    "loss_at_10pct": round(cv * 0.1, 2),
                    "breakeven": round(breakeven, 0),
                    "distance_spot_pct": round(dist, 1),
                    "spread_pct": round(spread_pct, 2),
                    "liquidity_score": int(liq_score)
                })
        except Exception as e:
            print(f"Binance fetch error in quick_scan: {e}")

        # 按平台分组排序，确保 Deribit 和 Binance 都有展示
        deribit_list = sorted([c for c in contracts if c.get("platform") == "Deribit"],
                              key=lambda x: (x.get("liquidity_score", 0), x["apr"]), reverse=True)
        binance_list = sorted([c for c in contracts if c.get("platform") == "Binance"],
                              key=lambda x: (x.get("liquidity_score", 0), x["apr"]), reverse=True)
        # 各取前15个，交替合并
        contracts = []
        for i in range(max(len(deribit_list), len(binance_list))):
            if i < len(deribit_list):
                contracts.append(deribit_list[i])
            if i < len(binance_list):
                contracts.append(binance_list[i])

        large_trades = _fetch_large_trades(currency, days=7, limit=50)
        large_trades_count = len(large_trades)

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO scan_records (timestamp, currency, spot_price, dvol_current, dvol_z_score,
                dvol_signal, large_trades_count, large_trades_details, contracts_data, raw_output)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (timestamp, currency, spot, dvol_current, dvol_z, dvol_signal, large_trades_count,
              json.dumps(large_trades[:20]), json.dumps(contracts[:30]), json.dumps({})))
        conn.commit()
        conn.close()

        return {
            "success": True,
            "contracts_count": len(contracts),
            "spot_price": spot,
            "timestamp": timestamp,
            "contracts": contracts[:30],
            "dvol_current": dvol_current,
            "dvol_z_score": dvol_z,
            "dvol_signal": dvol_signal
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


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
        SELECT 
               MAX(timestamp) as ts,
               AVG(CAST(json_extract(contracts_data, '$[0].apr') AS REAL)) as avg_apr,
               MAX(CAST(json_extract(contracts_data, '$[0].apr') AS REAL)) as max_apr
        FROM scan_records 
        WHERE timestamp > ?
        GROUP BY strftime('%Y-%m-%d %H:00', timestamp)
        ORDER BY ts ASC
    """, (since.strftime('%Y-%m-%d %H:%M:%S'),))

    rows = cursor.fetchall()
    conn.close()

    return [{"time": r[0], "avg_apr": round(r[1] or 0, 1), "max_apr": round(r[2] or 0, 1)} for r in rows]


@app.get("/api/charts/dvol")
async def get_dvol_chart(hours: int = Query(default=168)):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    since = datetime.now() - timedelta(hours=hours)

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
        WHERE timestamp > ? AND dvol_current IS NOT NULL
        GROUP BY ts
        ORDER BY ts ASC
    """, (since.strftime('%Y-%m-%d %H:%M:%S'),))

    rows = cursor.fetchall()
    conn.close()

    return [{"time": r[0], "dvol": round(r[1], 2) if r[1] else 0, "z_score": round(r[2], 2) if r[2] else 0, "signal": r[3]} for r in rows]


# ============================================================
# Module 1: Volatility Surface & Term Structure
# ============================================================

def _fetch_derivit_summaries(currency="BTC"):
    try:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'deribit-options-monitor'))
        from deribit_options_monitor import DeribitOptionsMonitor
        mon = DeribitOptionsMonitor()
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
    conn = sqlite3.connect(DB_PATH)
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
    conn.close()

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
            "strike": strike, "option_type": opt_type, "flow_label": fl
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
                except:
                    continue
                if not meta:
                    continue
                    
                direction = t.get("direction", "")
                trade_amount = float(t.get("amount", 0))
                index_price = float(t.get("index_price", 0) or 0)
                premium_usd = float(t.get("price", 0)) * trade_amount * (index_price or spot)
                
                if premium_usd < MIN_NOTIONAL:
                    continue
                
                # Use estimated delta (skip slow order book API call)
                trade_iv = float(t.get("iv") or 50) / 100.0
                delta_val = abs(_estimate_delta(meta["strike"], spot,
                    trade_iv, meta["dte"], meta["option_type"]))
                
                fl = _classify_flow_heuristic(
                    direction, meta["option_type"], delta_val, meta["strike"], spot)
                
                seen.add(inst)
                results.append({
                    "instrument_name": inst, "direction": direction,
                    "notional_usd": round(premium_usd, 2),
                    "volume": round(trade_amount, 4),
                    "strike": meta["strike"],
                    "option_type": meta["option_type"],
                    "flow_label": fl
                })
                if len(results) >= limit:
                    break
        except Exception as e:
            print(f"Deribit live trades fallback error: {e}")

    # Sort by notional and return top N
    results.sort(key=lambda x: x.get("notional_usd", 0), reverse=True)
    return results[:limit]


def _parse_inst_name(inst):
    import re
    from datetime import datetime
    m = re.match(r'([A-Z]+)-(\d+[A-Z]{3}\d+)-(\d+)-([PC])', inst)
    if not m:
        return None
    currency, expiry_str, strike_str, opt_type = m.groups()
    try:
        exp_date = datetime.strptime(expiry_str, '%d%b%y')
        dte = max(1, (exp_date - datetime.utcnow()).days)
    except:
        dte = 30
    return {"currency": currency, "expiry": expiry_str, "strike": float(strike_str),
            "option_type": opt_type, "dte": dte}


def _estimate_delta(strike, spot, iv, dte, option_type='P'):
    """估算期权Delta (Deribit book_summaries不返回delta字段)"""
    import math
    if strike <= 0 or spot <= 0 or dte <= 0 or iv <= 0:
        return 0.3
    t = dte / 365.0
    if t <= 0.01:
        t = 0.01
    d1 = (math.log(spot / strike) + (iv ** 2 / 2) * t) / (iv * math.sqrt(t))
    try:
        from scipy.stats import norm
        nd1 = norm.cdf(d1)
    except:
        nd1 = max(0.0, min(1.0, 0.5 + 0.5 * math.tanh(d1 * 0.8)))
    if option_type.upper() in ('P', 'PUT'):
        return round(nd1 - 1, 4)
    return round(nd1, 4)


@app.get("/api/charts/vol-surface")
async def get_vol_surface(currency: str = Query(default="BTC")):
    summaries = _fetch_derivit_summaries(currency)
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
        if not meta or meta["dte"] < 1 or meta["dte"] > 180:
            continue
        iv_pct = float(s.get("mark_iv") or 0)
        if iv_pct <= 5 or iv_pct > 500:
            continue
        oi = float(s.get("open_interest") or 0)
        strike = meta["strike"]
        moneyness = (strike - spot) / spot if spot > 0 else 0

        bucket = "ATM"
        if meta["option_type"] == "P":
            if moneyness < -0.08: bucket = "-40D"
            elif moneyness < -0.03: bucket = "-20D"
            elif moneyness > 0.03: bucket = "+20D"
            elif moneyness > 0.08: bucket = "+40D"
        else:
            if moneyness > 0.08: bucket = "+40D"
            elif moneyness > 0.03: bucket = "+20D"
            elif moneyness < -0.03: bucket = "-20D"
            elif moneyness < -0.08: bucket = "-40D"

        surface.append({"dte": meta["dte"], "strike": strike,
            "type": meta["option_type"], "iv": round(iv_pct, 1),
            "bucket": bucket, "oi": round(oi, 1), "moneyness": round(moneyness, 3)})

        for db in dte_buckets:
            window = max(5, int(db * 0.6))
            if abs(meta["dte"] - db) <= window:
                term_data[db].append(iv_pct)
                break
        else:
            nearest = min(dte_buckets, key=lambda x: abs(meta["dte"] - x))
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


def _get_spot_from_scan():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT contracts_data FROM scan_records ORDER BY timestamp DESC LIMIT 1")
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            data = json.loads(row[0])
            for item in data:
                sp = item.get("spot_price")
                if sp and sp > 1000:
                    return sp
    except:
        pass
    try:
        import urllib.request
        url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
        resp = urllib.request.urlopen(url, timeout=5)
        return float(json.loads(resp.read())["price"])
    except:
        pass
    return 0


# ============================================================
# Module 2: Max Pain & Gamma Exposure (GEX)
# ============================================================

@app.get("/api/metrics/max-pain")
async def get_max_pain(currency: str = Query(default="BTC")):
    summaries = _fetch_derivit_summaries(currency)
    if not summaries:
        return {"error": "No data"}

    parsed = []
    for s in summaries:
        meta = _parse_inst_name(s.get("instrument_name", ""))
        if not meta or meta["dte"] < 1:
            continue
        oi = float(s.get("open_interest") or 0)
        gamma = float(s.get("gamma") or 0)
        if oi < 1:
            continue
        parsed.append({**meta, "oi": oi, "gamma": gamma})

    if not parsed:
        return {"error": "No OI data"}

    strikes = sorted(set(p["strike"] for p in parsed))
    expiries = sorted(set((p["expiry"], p["dte"]) for p in parsed))
    deribit_spot = float(summaries[0].get('underlying_price', 0)) if summaries else 0
    db_spot = _get_spot_from_scan()
    spot = deribit_spot if deribit_spot > 1000 else (db_spot if db_spot > 1000 else strikes[len(strikes)//2])

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
    except:
        base_strike = spot * 0.95
        opt_type = 'P'

    drop = ((params.crash_price - spot) / spot * 100) if spot > 0 else -30
    intrinsic = max(0, base_strike - params.crash_price) if opt_type.upper() == 'P' else max(0, params.crash_price - base_strike)
    old_cv = base_strike * params.margin_ratio
    old_margin = old_cv * params.num_contracts

    summaries = _fetch_derivit_summaries("BTC" if "BTC" in params.current_symbol else "ETH")
    cands = []
    for s in summaries:
        meta = _parse_inst_name(s.get("instrument_name", ""))
        if not meta or meta["dte"] < 14 or meta["dte"] > 180:
            continue
        if meta["option_type"] != opt_type.upper():
            continue
        if opt_type.upper() == 'P' and meta["strike"] >= params.crash_price * 0.85:
            continue
        iv = float(s.get("mark_iv") or 0)
        if iv <= 0.05 or iv > 3:
            continue
        prem = float(s.get("mark_price") or 0)
        oi = float(s.get("open_interest") or 0)
        if prem <= 0 or oi < 10:
            continue
        ncv = meta["strike"] * params.margin_ratio
        apr_e = (prem / ncv) * (365 / meta["dte"]) * 100 if ncv > 0 else 0
        if apr_e < 5:
            continue
        cands.append({**meta, "premium": prem, "apr": round(apr_e, 1), "oi": oi, "cv": round(ncv, 2)})
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
            "prem_ct": round(c["premium"], 2), "contracts": nc, "margin": round(tnm, 0),
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
        WHERE currency = ? AND timestamp > ?
              AND strike IS NOT NULL AND strike > ? AND strike < ?
        GROUP BY strike, direction
        ORDER BY count DESC
        LIMIT 50
    """, (currency, since_str, int(spot * 0.15), int(spot * 4.0)))

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
    conn.close()

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

    flow_breakdown = []
    dominant_flow = ""
    max_flow_cnt = 0
    for fl, agg in sorted(flow_agg.items(), key=lambda x: x[1]["count"], reverse=True):
        cnt = agg["count"]
        notional = agg["notional"]
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
        if fl != 'unclassified' and cnt > max_flow_cnt:
            max_flow_cnt = cnt
            dominant_flow = fl
    if not dominant_flow:
        dominant_flow = 'unclassified'
    # Also add unclassified to FLOW_LABEL_MAP if missing
    if 'unclassified' not in FLOW_LABEL_MAP:
        FLOW_LABEL_MAP['unclassified'] = ('未分类', '历史数据无流向标签')

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
        "strike_flows": strike_flows,
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


def _get_positions_greeks(currency: str) -> dict:
    import concurrent.futures
    cache = {}
    try:
        from deribit_options_monitor import DeribitOptionsMonitor
        mon = DeribitOptionsMonitor()
        summaries = mon._get_book_summaries(currency)
        if not summaries:
            return cache
        instruments = list({s["instrument_name"] for s in summaries})
        def fetch_greeks(inst):
            try:
                book = mon._request_json("/api/v2/public/get_order_book", {"instrument_name": inst})
                result = book.get("result", {}) if book else {}
                greeks = result.get("greeks", {}) if result else {}
                return inst, {"gamma": greeks.get("gamma"), "delta": greeks.get("delta"), "vega": greeks.get("vega"), "theta": greeks.get("theta")}
            except:
                return inst, {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            for fut in concurrent.futures.as_completed({ex.submit(fetch_greeks, i): i for i in instruments}, timeout=20):
                try:
                    inst, gk = fut.result()
                    cache[inst] = gk
                except:
                    pass
    except:
        pass
    return cache
