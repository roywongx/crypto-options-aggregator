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

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 数据库路径
DB_PATH = Path(__file__).parent / "data" / "monitor.db"
DB_PATH.parent.mkdir(exist_ok=True)


class ScanParams(BaseModel):
    """扫描参数模型"""
    currency: str = Field(default="BTC", description="币种")
    min_dte: int = Field(default=14, ge=1, le=365, description="最小到期天数")
    max_dte: int = Field(default=25, ge=1, le=365, description="最大到期天数")
    max_delta: float = Field(default=0.4, ge=0.01, le=1.0, description="最大Delta")
    margin_ratio: float = Field(default=0.2, ge=0.05, le=1.0, description="保证金比率")
    option_type: str = Field(default="PUT", pattern="^(PUT|CALL)$")
    strike: Optional[float] = Field(default=None, description="特定行权价")
    strike_range: Optional[str] = Field(default=None, description="行权价范围，如 60000-65000")


class RecoveryCalcParams(BaseModel):
    """倍投修复计算器参数"""
    currency: str = Field(default="BTC", description="币种")
    current_loss: float = Field(..., gt=0, description="当前浮亏金额(USDT)")
    target_apr: float = Field(default=200, ge=50, le=500, description="目标年化收益率(%)")
    max_contracts: int = Field(default=10, ge=1, le=50, description="最大合约数量")
    max_delta: float = Field(default=0.45, ge=0.1, le=0.8, description="最大Delta容忍")


def get_spot_price_binance(currency: str = "BTC") -> Optional[float]:
    """从币安获取现货价格"""
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
    """从Deribit获取DVOL数据"""
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


def parse_trade_alert(trade: Dict[str, str], currency: str, timestamp: str) -> Dict[str, Any]:
    """解析大宗交易告警数据为结构化格式"""
    title = trade.get('title', '')
    message = trade.get('message', '')
    
    # 判断来源平台
    source = 'Unknown'
    if 'Deribit' in message or 'deribit' in message.lower():
        source = 'Deribit'
    elif 'Binance' in message or 'binance' in message.lower():
        source = 'Binance'
    
    # 判断方向
    direction = 'unknown'
    if any(w in message.lower() for w in ['buy', '买入', '购买']):
        direction = 'buy'
    elif any(w in message.lower() for w in ['sell', '卖出', '出售']):
        direction = 'sell'
    
    # 提取Strike价格（从消息中提取数字）
    strike = None
    strike_match = re.search(r'(?:strike|行权价)?[:\s]*(\d{3,}(?:,\d{3})*)\s*(?:PUT|CALL)', message, re.IGNORECASE)
    if not strike_match:
        strike_match = re.search(r'\$(\d{3,}(?:,\d{3})*)', message)
    if strike_match:
        try:
            strike = float(strike_match.group(1).replace(',', ''))
        except ValueError:
            pass
    
    # 提取成交量/金额
    volume = 0
    volume_patterns = [
        r'(\d+(?:\.\d+)?)\s*(?:BTC|ETH|SOL)\s*(?:worth|价值)',
        r'\$([\d,]+(?:\.\d+)?)',
        r'(\d+(?:,\d{3})*)\s*(?:contracts?|张)',
    ]
    for pattern in volume_patterns:
        match = re.search(pattern, message)
        if match:
            try:
                volume = float(match.group(1).replace(',', ''))
                break
            except ValueError:
                continue
    
    # 判断期权类型
    option_type = None
    if 'PUT' in message.upper() or 'put' in message.lower() or '看跌' in message:
        option_type = 'PUT'
    elif 'CALL' in message.upper() or 'call' in message.lower() or '看涨' in message:
        option_type = 'CALL'
    
    return {
        'timestamp': timestamp,
        'currency': currency,
        'source': source,
        'title': title,
        'message': message,
        'direction': direction,
        'strike': strike,
        'volume': volume,
        'option_type': option_type
    }


def init_database():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    
    # 扫描记录表
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
    
    # 大宗交易历史表（结构化存储）
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
            option_type TEXT
        )
    """)
    
    # 创建索引加速查询
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_currency ON large_trades_history(currency)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON large_trades_history(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_strike ON large_trades_history(strike)")
    
    # 检查scan_records是否需要添加新列
    cursor.execute("PRAGMA table_info(scan_records)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'dvol_signal' not in columns:
        cursor.execute("ALTER TABLE scan_records ADD COLUMN dvol_signal TEXT")
    if 'large_trades_details' not in columns:
        cursor.execute("ALTER TABLE scan_records ADD COLUMN large_trades_details TEXT")
    if 'contracts_data' not in columns:
        cursor.execute("ALTER TABLE scan_records ADD COLUMN contracts_data TEXT")
    if 'raw_output' not in columns:
        cursor.execute("ALTER TABLE scan_records ADD COLUMN raw_output TEXT")
    
    conn.commit()
    conn.close()


def save_scan_record(data: Dict[str, Any]):
    """保存扫描记录到数据库，并清理超过3个月（90天）的旧数据"""
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
    
    # 同时将大宗交易保存到结构化表
    if large_trades and isinstance(large_trades, list):
        for trade in large_trades:
            parsed = parse_trade_alert(trade, data.get('currency', 'BTC'), now_str)
            cursor.execute("""
                INSERT INTO large_trades_history 
                (timestamp, currency, source, title, message, direction, strike, volume, option_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                parsed['timestamp'], parsed['currency'], parsed['source'],
                parsed['title'], parsed['message'], parsed['direction'],
                parsed['strike'], parsed['volume'], parsed['option_type']
            ))

    # 执行清理：删除90天之前的记录
    cursor.execute("""
        DELETE FROM scan_records WHERE timestamp < datetime('now', '-90 days')
    """)
    cursor.execute("""
        DELETE FROM large_trades_history WHERE timestamp < datetime('now', '-90 days')
    """)

    conn.commit()
    conn.close()


def run_options_scan(params: ScanParams) -> Dict[str, Any]:
    """执行期权扫描 - 使用JSON模式"""
    base_dir = Path(__file__).parent.parent
    
    spot_price = get_spot_price_binance(params.currency)
    dvol_data = get_dvol_from_deribit(params.currency)
    
    # 获取DVOL原始数据用于自适应调整
    dvol_full = get_dvol_from_deribit(params.currency)
    dvol_raw_for_adapt = {}
    if isinstance(dvol_full, dict):
        dvol_raw_for_adapt = dvol_full
    
    # 应用DVOL自适应参数调整
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
            text=False, # Receive bytes first
            timeout=120
        )

        if result.returncode != 0:
            return {"success": False, "error": result.stderr.decode('utf-8', errors='replace') or "扫描失败"}

        # Decode stdout explicitly handling potential gbk/utf-8 confusion from subprocess on Windows
        output_text = result.stdout.decode('utf-8', errors='replace')
        try:
            parsed = json.loads(output_text)
            parsed['success'] = True
        except json.JSONDecodeError:
            # Fallback to try GBK if utf-8 fails, common on Windows Chinese cmd
            try:
                output_text = result.stdout.decode('gbk', errors='replace')
                parsed = json.loads(output_text)
                parsed['success'] = True
            except Exception as e:
                return {"success": False, "error": "JSON 解析失败", "raw": output_text[:200]}
        parsed['success'] = True
        
        # 使用从API获取的数据覆盖
        if spot_price:
            parsed['spot_price'] = spot_price
        if dvol_data.get('current'):
            parsed['dvol_current'] = dvol_data['current']
            parsed['dvol_z_score'] = dvol_data['z_score']
            parsed['dvol_signal'] = dvol_data['signal']
        
        # 保存到数据库
        save_scan_record(parsed)
        
        # 注入DVOL自适应信息
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
    """计算倍投修复方案"""
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


# FastAPI 应用
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
    """执行期权扫描 (异步无阻塞)"""
    result = await run_in_threadpool(run_options_scan, params)
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', '扫描失败'))
    return result


@app.get("/api/latest")
async def get_latest_scan(currency: str = Query(default="BTC")):
    """获取最新扫描结果"""
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
    """计算倍投修复方案"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT contracts_data, spot_price FROM scan_records 
        WHERE currency = ? 
        ORDER BY timestamp DESC 
        LIMIT 1
    """, (params.currency,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="请先执行扫描获取合约数据")
    
    contracts = json.loads(row[0]) if row[0] else []
    spot_price = row[1] or 0
    
    result = calculate_recovery_plan(contracts, params, spot_price)
    return result


@app.get("/api/stats")
async def get_stats():
    """获取统计信息"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM scan_records")
    total_scans = cursor.fetchone()[0]
    
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("SELECT COUNT(*) FROM scan_records WHERE date(timestamp) = ?", (today,))
    today_scans = cursor.fetchone()[0]
    
    # 大宗交易统计
    cursor.execute("SELECT COUNT(*) FROM large_trades_history")
    total_trades = cursor.fetchone()[0]
    
    conn.close()
    
    db_size_mb = round(DB_PATH.stat().st_size / (1024 * 1024), 2)
    
    return {
        "total_scans": total_scans,
        "today_scans": today_scans,
        "total_trades": total_trades,
        "db_size_mb": db_size_mb
    }


@app.get("/api/charts/apr")
async def get_apr_chart(currency: str = Query(default="BTC"), hours: int = Query(default=168)):
    """获取APR趋势数据"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    since = datetime.now() - timedelta(hours=hours)
    since_str = since.strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute("""
        SELECT timestamp, contracts_data 
        FROM scan_records 
        WHERE currency = ? AND timestamp > ?
        ORDER BY timestamp ASC
    """, (currency, since_str))
    
    rows = cursor.fetchall()
    conn.close()
    
    data = []
    for row in rows:
        try:
            contracts = json.loads(row[1]) if row[1] else []
            if contracts:
                aprs = [c.get('apr', 0) for c in contracts if c.get('apr', 0) > 0]
                if aprs:
                    data.append({
                        "timestamp": row[0],
                        "max_apr": max(aprs),
                        "avg_apr": sum(aprs) / len(aprs)
                    })
        except:
            continue
    
    return data


@app.get("/api/charts/dvol")
async def get_dvol_chart(currency: str = Query(default="BTC"), hours: int = Query(default=168)):
    """获取DVOL趋势数据"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    since = datetime.now() - timedelta(hours=hours)
    since_str = since.strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute("""
        SELECT timestamp, dvol_current 
        FROM scan_records 
        WHERE currency = ? AND timestamp > ? AND dvol_current > 0
        ORDER BY timestamp ASC
    """, (currency, since_str))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [{"timestamp": row[0], "dvol": row[1]} for row in rows]


@app.get("/api/trades/history")
async def get_trades_history(
    currency: str = Query(default="BTC"),
    days: int = Query(default=7, ge=1, le=90),
    direction: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    min_strike: Optional[float] = Query(default=None),
    max_strike: Optional[float] = Query(default=None)
):
    """
    获取大宗交易历史（结构化查询）
    支持按方向、来源、行权价范围筛选
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    since = datetime.now() - timedelta(days=days)
    since_str = since.strftime('%Y-%m-%d %H:%M:%S')
    
    query = """
        SELECT * FROM large_trades_history 
        WHERE currency = ? AND timestamp > ?
    """
    params = [currency, since_str]
    
    if direction:
        query += " AND direction = ?"
        params.append(direction.upper())
    if source:
        query += " AND source = ?"
        params.append(source)
    if min_strike is not None:
        query += " AND strike >= ?"
        params.append(min_strike)
    if max_strike is not None:
        query += " AND strike <= ?"
        params.append(max_strike)
    
    query += " ORDER BY timestamp DESC LIMIT 200"
    
    cursor.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    conn.close()
    
    trades = []
    for row in rows:
        trade = dict(zip(columns, row))
        trades.append(trade)
    
    return {
        "total": len(trades),
        "trades": trades
    }


@app.get("/api/trades/strike-distribution")
async def get_strike_distribution(
    currency: str = Query(default="BTC"),
    days: int = Query(default=30)
):
    """
    获取大单Strike分布（用于风向标图表）
    返回每个Strike价位的大单数量和总成交量
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    since = datetime.now() - timedelta(days=days)
    since_str = since.strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute("""
        SELECT strike, direction, COUNT(*) as count, SUM(volume) as total_volume
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
            "total_volume": row[3] or 0
        })
    
    return distribution


STRATEGY_PRESETS = {
    "PUT": {
        "conservative": {"max_delta": 0.20, "min_dte": 30, "max_dte": 45, "margin_ratio": 0.18, "min_apr": 12.0, "label": "纯收租(高胜率)", "desc": "Delta≤0.20, DTE 30-45天, 胜率90%+, 适合纯收租"},
        "standard":     {"max_delta": 0.30, "min_dte": 14, "max_dte": 35, "margin_ratio": 0.20, "min_apr": 15.0, "label": "标准平衡", "desc": "Delta≤0.30, DTE 14-35天, 胜率75%+, 最常用配置"},
        "aggressive":   {"max_delta": 0.40, "min_dte": 7,  "max_dte": 28, "margin_ratio": 0.22, "min_apr": 20.0, "label": "折价接货", "desc": "Delta≤0.40, DTE 7-28天, 愿意被行权接货"}
    },
    "CALL": {
        "conservative": {"max_delta": 0.30, "min_dte": 30, "max_dte": 45, "margin_ratio": 0.18, "min_apr": 10.0, "label": "保留上涨空间", "desc": "Delta≤0.30, DTE 30-45天, 低被行权概率"},
        "standard":     {"max_delta": 0.45, "min_dte": 14, "max_dte": 35, "margin_ratio": 0.20, "min_apr": 12.0, "label": "标准备兑", "desc": "Delta≤0.45, DTE 14-35天, 平衡收益与参与"},
        "aggressive":   {"max_delta": 0.55, "min_dte": 7,  "max_dte": 28, "margin_ratio": 0.22, "min_apr": 18.0, "label": "强横盘预期", "desc": "Delta≤0.55, DTE 7-28天, 强横盘/愿被行权"}
    }
}

def adapt_params_by_dvol(params: dict, dvol_raw: dict) -> dict:
    """DVOL自适应参数调整"""
    pct_7d = dvol_raw.get('iv_percentile_7d') if isinstance(dvol_raw.get('iv_percentile_7d'), (int, float)) else 50
    trend = dvol_raw.get('trend', '')
    signal = dvol_raw.get('signal', '')
    
    adjusted = dict(params)
    advice = []
    adjustment_level = "none"
    
    if pct_7d >= 80:
        adjusted['max_delta'] = round(adjusted.get('max_delta', 0.3) * 0.70, 2)
        adjusted['min_apr'] = (adjusted.get('min_apr') or 15) + 5
        advice.append("高波动环境: 自动收紧Delta至70%, APR门槛+5%")
        adjustment_level = "conservative"
    elif pct_7d <= 20:
        adjusted['max_delta'] = min(round((adjusted.get('max_delta') or 0.3) * 1.20, 2), 0.60)
        adj_apr = max((adjusted.get('min_apr') or 15) - 5, 8)
        adjusted['min_apr'] = adj_apr
        advice.append("低波动环境: 放宽Delta至120%, APR门槛-5%")
        adjustment_level = "aggressive"
    
    if trend == '上涨':
        advice.append("DVOL上升+反弹阶段，适合Sell Put接货")
    elif trend == '下跌':
        if params.get('option_type') == 'CALL':
            advice.append("市场下跌后反弹，Covered Call可锁定收益")
        else:
            advice.append("市场处于下跌阶段，建议降低仓位或观望")
    
    adjusted['_dvol_advice'] = advice
    adjusted['_adjustment_level'] = adjustment_level
    return adjusted

@app.get("/api/strategy-presets")
async def get_strategy_presets():
    """获取策略预设配置"""
    return STRATEGY_PRESETS

@app.get("/api/dvol-advice")
async def get_dvol_advice(currency: str = "BTC"):
    """获取基于当前DVOL的策略建议"""
    try:
        dvol_data = get_dvol_from_deribit(currency)
        dvol_raw = {}
        if isinstance(dvol_data, dict):
            dvol_raw = dvol_data
        
        # 对每种预设计算自适应结果
        result = {
            "currency": currency,
            "dvol_snapshot": {
                "current": dvol_raw.get('current'),
                "z_score": dvol_raw.get('z_score_7d'),
                "percentile_7d": dvol_raw.get('iv_percentile_7d'),
                "trend": dvol_raw.get('trend'),
                "signal": dvol_raw.get('signal')
            },
            "adapted_presets": {}
        }
        
        for opt_type in ["PUT", "CALL"]:
            for preset_name, preset_vals in STRATEGY_PRESETS.get(opt_type, {}).items():
                params = {**preset_vals, "option_type": opt_type}
                adapted = adapt_params_by_dvol(params, dvol_raw)
                result["adapted_presets"][f"{opt_type}_{preset_name}"] = {
                    "original": preset_vals,
                    "adapted": {k: v for k, v in adapted.items() if not k.startswith('_')},
                    "advice": adapted.get('_dvol_advice', []),
                    "adjustment_level": adapted.get('_adjustment_level', 'none')
                }
        
        return result
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/health")
async def health_check():
    """健康检查端点"""
    health = {"status": "ok", "components": {}}
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM scan_records")
        health["components"]["database"] = {"status": "up", "records": cursor.fetchone()[0]}
        
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        health["components"]["database"]["wal_mode"] = mode
        conn.close()
    except Exception as e:
        health["components"]["database"] = {"status": "error", "message": str(e)}
        health["status"] = "degraded"
    
    try:
        r = requests.get("https://api.binance.com/api/v3/ping", timeout=5)
        health["components"]["binance_api"] = {"status": "up" if r.status_code == 200 else "down"}
    except Exception as e:
        health["components"]["binance_api"] = {"status": "unreachable"}
        health["status"] = "degraded"
    
    try:
        r = requests.get("https://www.deribit.com/api/v2/public/get_time", timeout=5)
        health["components"]["deribit_api"] = {"status": "up" if r.status_code == 200 else "down"}
    except Exception as e:
        health["components"]["deribit_api"] = {"status": "unreachable"}
        health["status"] = "degraded"
    
    return health


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
