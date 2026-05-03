# Services - DVOL Analyzer
import httpx
import sys
import math
import logging
import threading
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta

from services.http_client import http_get

logger = logging.getLogger(__name__)

# 缓存机制 (线程安全)
_dvol_lock = threading.Lock()
_dvol_cache = {}
_dvol_cache_time = None
_dvol_cache_ttl = 300  # 5分钟缓存

from services.shared_calculations import norm_cdf as _norm_cdf_approx


def calc_delta_bs(strike: float, spot: float, iv: float, dte: float, option_type: str = 'P') -> float:
    """使用 Black-Scholes 计算期权 Delta
    
    优先使用 scipy.stats.norm.cdf，回退到 Abramowitz & Stegun 近似（精度 7.5e-8）
    """
    if spot is None or strike is None or dte is None or iv is None:
        return 0.3
    if strike <= 0 or spot <= 0 or dte <= 0 or iv <= 0:
        return 0.3
    t = dte / 365.0
    if t <= 0.01:
        t = 0.01
    iv_decimal = iv / 100.0
    d1 = (math.log(spot / strike) + (iv_decimal ** 2 / 2) * t) / (iv_decimal * math.sqrt(t))
    try:
        from scipy.stats import norm
        nd1 = norm.cdf(d1)
    except ImportError:
        nd1 = _norm_cdf_approx(d1)
    if option_type.upper() in ('P', 'PUT'):
        return round(nd1 - 1, 4)
    return round(nd1, 4)

def get_dvol_from_deribit(currency: str = "BTC") -> Dict[str, Any]:
    """从 Deribit 获取 DVOL 数据（带5分钟缓存）"""
    global _dvol_cache, _dvol_cache_time
    
    # 检查缓存 (线程安全)
    with _dvol_lock:
        now = datetime.now()
        if (_dvol_cache_time and 
            (now - _dvol_cache_time).total_seconds() < _dvol_cache_ttl and
            _dvol_cache.get("currency") == currency):
            return _dvol_cache.get("data", {})
    
    try:
        from services.instrument import _get_deribit_monitor
        mon = _get_deribit_monitor()
        result = mon.get_dvol_signal(currency)
        if not result:
            return {}
        trend_arrow = "↑" if result.get("trend") == "上涨" else ("↓" if result.get("trend") == "下跌" else "→")
        data = {
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
        # 更新缓存 (线程安全)
        with _dvol_lock:
            _dvol_cache = {"currency": currency, "data": data}
            _dvol_cache_time = now
        return data
    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
        logger.warning("获取DVOL失败(高级版): %s, 回退简单版", e)
        return _get_dvol_simple_fallback(currency)

def _get_dvol_simple_fallback(currency: str = "BTC") -> Dict[str, Any]:
    """DVOL 简单回退版本（当 Deribit monitor 不可用时）"""
    try:
        from config import config
        base_params = {
            "currency": currency,
            "start_timestamp": int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000),
            "end_timestamp": int(datetime.now(timezone.utc).timestamp() * 1000)
        }
        response = http_get(
            "https://www.deribit.com/api/v2/public/get_volatility_index_data",
            params={**base_params, "resolution": "3600"}, timeout=10.0
        )
        data = response.json()
        if data.get("result") and data["result"].get("data"):
            points = data["result"]["data"]
            if len(points) > 0:
                current = float(points[-1][4])
                closes = [float(p[4]) for p in points]
                if len(closes) > 1:
                    mean_val = sum(closes) / len(closes)
                    n = len(closes)
                    std_val = (sum((x - mean_val) ** 2 for x in closes) / (n - 1)) ** 0.5 if n > 1 else 0
                    z_score = (current - mean_val) / std_val if std_val > 0 else 0
                else:
                    z_score = 0

                from config import config
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
    except (ValueError, TypeError, ZeroDivisionError, RuntimeError) as e:
        logger.warning("获取DVOL失败(简单版): %s", e)
        return {}

def adapt_params_by_dvol(params: dict, dvol_raw: dict) -> dict:
    """根据 DVOL 信号调整交易参数"""
    from config import config

    dvol = dvol_raw.get("current", 50)
    z_score = dvol_raw.get("z_score", 0)
    signal = dvol_raw.get("signal", "正常区间")

    adapted = {**params}

    # 波动率极高时（>70%），降低风险偏好
    if dvol > 70:
        adapted["max_delta"] = min(0.25, params.get("max_delta", 0.4))
        adapted["margin_ratio"] = min(0.3, params.get("margin_ratio", 0.2) * 1.5)
    # 波动率极低时（<30%），提高风险偏好
    elif dvol < 30:
        adapted["max_delta"] = max(0.5, params.get("max_delta", 0.4))
        adapted["margin_ratio"] = max(0.15, params.get("margin_ratio", 0.2) * 0.8)
    # Z-score 极端值处理
    elif abs(z_score) > 2:
        if z_score > 0:
            adapted["max_delta"] = min(0.25, params.get("max_delta", 0.4))
        else:
            adapted["max_delta"] = max(0.45, params.get("max_delta", 0.4))

    # 调整最小 APR 要求
    if dvol > 60:
        adapted["min_apr"] = max(20, params.get("min_apr", 15))
    elif dvol < 40:
        adapted["min_apr"] = max(10, params.get("min_apr", 15) * 0.8)

    return adapted

def calc_pop(delta_val: float, option_type: str, spot: float, strike: float, iv: float, dte: float) -> float:
    """
    计算 POP (Probability of Profit) 使用 Black-Scholes

    Args:
        delta_val: 期权 delta 值
        option_type: 'CALL' 或 'PUT'
        spot: 标的资产当前价格
        strike: 行权价格
        iv: 隐含波动率 (%)
        dte: 到期时间 (天)

    Returns:
        float: 盈利概率 (0-1)
    """
    try:
        from scipy.stats import norm
        import math

        dte_years = dte / 365.0
        if dte_years <= 0 or iv <= 0 or strike <= 0:
            return 0.5

        iv_decimal = iv / 100.0
        sqrt_t = math.sqrt(dte_years)

        d1 = (math.log(spot / strike) + (0.5 * iv_decimal ** 2) * dte_years) / (iv_decimal * sqrt_t)
        d2 = d1 - iv_decimal * sqrt_t

        if option_type.upper() == "CALL":
            pop = norm.cdf(-d2)      # seller POP = P(S_T ≤ K) = N(-d2)
        else:
            pop = norm.cdf(d2)       # seller POP = P(S_T ≥ K) = N(d2)

        return max(0.0, min(1.0, pop))
    except (ImportError, ValueError, ZeroDivisionError) as e:
        logger.debug("POP calc failed: %s", e)
        return 0.5
