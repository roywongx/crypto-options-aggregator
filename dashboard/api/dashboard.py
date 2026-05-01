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
    """获取大单风向标分析（委托给 scan_engine 的统一实现）"""
    from services.scan_engine import _fetch_wind_analysis as _se_wind
    return _se_wind(currency, days)


def _fetch_term_structure(currency: str):
    """获取 IV 期限结构（委托给 scan_engine 的统一实现）"""
    from services.scan_engine import _fetch_term_structure as _se_term
    return _se_term(currency)


def _fetch_onchain_metrics(currency: str):
    """获取链上核心指标"""
    try:
        from services.onchain_metrics import OnChainMetrics
        return OnChainMetrics.get_all_metrics(currency)
    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
        return {"error": str(e)}


def _fetch_derivative_metrics(currency: str):
    """获取衍生品市场过热检测数据"""
    try:
        from services.derivative_metrics import DerivativeMetrics
        return DerivativeMetrics.get_all_metrics()
    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
        return {"error": str(e)}


def _fetch_pressure_test(currency: str):
    """获取压力测试数据"""
    try:
        from services.pressure_test import PressureTestEngine
        # 获取当前价格和期权数据
        spot = get_spot_price(currency)
        # 返回基础 Greeks 数据
        return PressureTestEngine.get_greeks(spot, spot, 30/365, 0.05, 0.5)
    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
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
    except (RuntimeError, ValueError, TypeError) as e:
        return {"error": str(e)}
