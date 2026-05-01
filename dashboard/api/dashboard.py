"""仪表盘聚合 API"""
import asyncio
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Query

from services.spot_price import get_spot_price
from services.risk_framework import RiskFramework
from db.connection import execute_read

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard-init")
async def dashboard_init(currency: str = Query(default="BTC")):
    """聚合初始化 API - 一次性返回所有仪表盘模块数据"""
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
    
    # 获取链上指标数据
    onchain_data = await asyncio.to_thread(_fetch_onchain_metrics, currency)
    
    # 获取衍生品市场数据
    derivative_data = await asyncio.to_thread(_fetch_derivative_metrics, currency)
    
    # 获取压力测试数据
    pressure_test_data = await asyncio.to_thread(_fetch_pressure_test, currency)
    
    # 获取 AI 情绪分析数据
    ai_sentiment_data = await asyncio.to_thread(_fetch_ai_sentiment, currency)
    
    return {
        "success": True,
        "currency": currency,
        "wind": wind_data,
        "term_structure": ts_data,
        "max_pain": mp_data,
        "onchain_metrics": onchain_data,
        "derivative_metrics": derivative_data,
        "pressure_test": pressure_test_data,
        "ai_sentiment": ai_sentiment_data,
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
    except (RuntimeError, ValueError) as e:
        logger.warning("Dashboard wind spot price failed: %s", e)
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
    from services.iv_term_structure import IVTermStructureAnalyzer

    try:
        spot = get_spot_price(currency)
    except (RuntimeError, ValueError) as e:
        logger.warning("Term structure spot price failed: %s, using fallback", e)
        spot = 70000 if currency == "BTC" else 3000

    # 从数据库获取最近的 scan_records 中的合约数据
    try:
        rows = execute_read("""
            SELECT contracts_data FROM scan_records
            WHERE currency = ? AND contracts_data IS NOT NULL
            ORDER BY timestamp DESC LIMIT 1
        """, (currency,))
    except Exception as e:
        logger.warning("Term structure DB query failed: %s", e)
        rows = []

    import json
    term_data = []
    if rows and rows[0][0]:
        try:
            contracts = json.loads(rows[0][0])
            # 按到期日分组，计算每个到期日的平均 IV
            expiry_ivs = {}
            for c in contracts:
                iv = c.get("mark_iv") or c.get("iv") or 0
                dte = c.get("dte", 0)
                if iv > 0 and dte > 0:
                    key = int(dte)
                    if key not in expiry_ivs:
                        expiry_ivs[key] = []
                    expiry_ivs[key].append(float(iv))

            for dte, ivs in sorted(expiry_ivs.items()):
                avg_iv = sum(ivs) / len(ivs)
                term_data.append({"dte": dte, "avg_iv": round(avg_iv, 2)})
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Term structure JSON parse failed: %s", e)

    if len(term_data) < 2:
        return {"error": "数据不足，至少需要 2 个期限点", "term_structure": [], "backwardation": False}

    # 使用 IVTermStructureAnalyzer 进行分析
    analysis = IVTermStructureAnalyzer.analyze_term_structure(term_data, spot)

    backwardation = False
    if len(term_data) >= 2:
        front_iv = term_data[0]["avg_iv"]
        back_iv = term_data[-1]["avg_iv"]
        if front_iv and back_iv:
            backwardation = front_iv > back_iv * 1.05

    return {
        "currency": currency,
        "term_structure": term_data,
        "backwardation": backwardation,
        "analysis": analysis,
        "timestamp": datetime.utcnow().isoformat()
    }


def _fetch_onchain_metrics(currency: str):
    """获取链上核心指标"""
    try:
        from services.onchain_metrics import OnChainMetrics
        return OnChainMetrics.get_all_metrics(currency)
    except Exception as e:
        return {"error": str(e)}


def _fetch_derivative_metrics(currency: str):
    """获取衍生品市场过热检测数据"""
    try:
        from services.derivative_metrics import DerivativeMetrics
        return DerivativeMetrics.get_all_metrics()
    except Exception as e:
        return {"error": str(e)}


def _fetch_pressure_test(currency: str):
    """获取压力测试数据"""
    try:
        from services.pressure_test import PressureTestEngine
        # 获取当前价格和期权数据
        spot = get_spot_price(currency)
        # 返回基础 Greeks 数据
        return PressureTestEngine.get_greeks(spot, spot, 30/365, 0.05, 0.5)
    except Exception as e:
        return {"error": str(e)}


def _fetch_ai_sentiment(currency: str):
    """获取 AI 驱动的大宗交易情绪分析"""
    try:
        from services.ai_sentiment import AISentimentAnalyzer
        from services.spot_price import get_spot_price
        from db.connection import execute_read
        
        # 获取最近的大宗交易数据
        since = datetime.utcnow() - timedelta(days=7)
        since_str = since.strftime('%Y-%m-%d %H:%M:%S')
        rows = execute_read("""
            SELECT direction, option_type, strike, volume, delta, notional_usd, timestamp
            FROM large_trades_history
            WHERE currency = ? AND timestamp >= ?
            ORDER BY timestamp DESC LIMIT 100
        """, (currency, since_str))
        
        trades = []
        for row in rows:
            trades.append({
                "direction": row[0],
                "option_type": row[1],
                "strike": row[2],
                "volume": row[3],
                "delta": row[4],
                "notional_usd": row[5],
                "timestamp": row[6]
            })
        
        spot = get_spot_price(currency)
        return AISentimentAnalyzer.analyze_market_sentiment(trades, spot)
    except Exception as e:
        return {"error": str(e)}
