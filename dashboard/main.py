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


def init_database():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
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
    
    # 检查是否需要添加新列
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
    """保存扫描记录到数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
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
        json.dumps(data.get('large_trades_details', []), ensure_ascii=False),
        json.dumps(data.get('contracts', []), ensure_ascii=False),
        data.get('raw_output', '')
    ))
    
    conn.commit()
    conn.close()


def run_options_scan(params: ScanParams) -> Dict[str, Any]:
    """执行期权扫描 - 使用JSON模式"""
    base_dir = Path(__file__).parent.parent
    
    spot_price = get_spot_price_binance(params.currency)
    dvol_data = get_dvol_from_deribit(params.currency)
    
    cmd = [
        sys.executable,
        str(base_dir / "options_aggregator.py"),
        "--currency", params.currency,
        "--min-dte", str(params.min_dte),
        "--max-dte", str(params.max_dte),
        "--max-delta", str(params.max_delta),
        "--margin-ratio", str(params.margin_ratio),
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
            text=True,
            encoding='utf-8',
            timeout=60
        )
        
        if result.returncode != 0:
            return {"success": False, "error": result.stderr or "扫描失败"}
        
        # 直接解析JSON输出
        parsed = json.loads(result.stdout)
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


@app.post("/api/scan")
async def scan_options(params: ScanParams):
    """执行期权扫描"""
    result = run_options_scan(params)
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
    
    return {
        "timestamp": row[1],
        "currency": row[2],
        "spot_price": row[3],
        "dvol_current": row[4],
        "dvol_z_score": row[5],
        "dvol_signal": row[6],
        "large_trades_count": row[7],
        "large_trades_details": json.loads(row[8]) if row[8] else [],
        "contracts": json.loads(row[9]) if row[9] else []
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
    
    conn.close()
    
    db_size_mb = round(DB_PATH.stat().st_size / (1024 * 1024), 2)
    
    return {
        "total_scans": total_scans,
        "today_scans": today_scans,
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
