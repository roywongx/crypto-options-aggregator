"""仪表盘聚合 API"""
import asyncio
from datetime import datetime, timedelta
from fastapi import APIRouter, Query

from services.spot_price import get_spot_price
from services.risk_framework import RiskFramework
from db.connection import execute_read

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard-init")
async def dashboard_init(currency: str = Query(default="BTC")):
    """聚合初始化 API - 一次性返回 Wind/TermStructure/MaxPain 三大模块"""
    from routers.maxpain import _calc_max_pain_internal
    
    wind_task = asyncio.create_task(asyncio.to_thread(_fetch_wind_analysis, currency))
    ts_task = asyncio.create_task(asyncio.to_thread(_fetch_term_structure, currency))
    mp_task = asyncio.create_task(_calc_max_pain_internal(currency))
    
    results = await asyncio.gather(
        wind_task, ts_task, mp_task,
        return_exceptions=True
    )
    
    wind_data = results[0] if not isinstance(results[0], Exception) else {"error": str(results[0])}
    ts_data = results[1] if not isinstance(results[1], Exception) else {"error": str(results[1])}
    mp_data = results[2] if not isinstance(results[2], Exception) else {"error": str(results[2])}
    
    return {
        "success": True,
        "currency": currency,
        "wind": wind_data,
        "term_structure": ts_data,
        "max_pain": mp_data,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    }


def _fetch_wind_analysis(currency: str, days: int = 30):
    """获取大单风向标分析"""
    since = datetime.utcnow() - timedelta(days=days)
    since_str = since.strftime('%Y-%m-%d %H:%M:%S')
    
    grouped = execute_read("""
        SELECT direction, option_type, SUM(volume) as total_volume, COUNT(*) as trade_count
        FROM large_trades_history
        WHERE currency = ? AND timestamp >= ?
        GROUP BY direction, option_type
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
        "summary": summary_data
    }


def _fetch_term_structure(currency: str):
    """获取 IV 期限结构"""
    from services.dvol_analyzer import get_dvol_from_deribit
    
    dvol = get_dvol_from_deribit(currency)
    if not dvol:
        return {"error": "无法获取 DVOL 数据"}
    
    return {
        "currency": currency,
        "dvol": dvol,
        "timestamp": datetime.utcnow().isoformat()
    }
