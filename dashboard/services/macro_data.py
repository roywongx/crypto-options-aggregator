"""
宏观数据服务 - 集成多个外部数据源，为交易决策提供多维度信号
- Fear & Greed Index (恐慌贪婪指数)
- yfinance 宏观数据 (QQQ/SPY)
- 资金费率 (Coinglass/HyperLiquid)
- FRED 无风险利率
"""
import logging
import requests
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 缓存机制
_fg_cache = {}
_fg_cache_time = None
_fg_cache_ttl = 300  # 5分钟缓存

_funding_cache = {}
_funding_cache_time = None
_funding_cache_ttl = 300  # 5分钟缓存

# ============================================================
# 1. Fear & Greed Index (恐慌贪婪指数)
# ============================================================

def get_fear_greed_index() -> Dict[str, Any]:
    """
    获取 Crypto Fear & Greed Index（带5分钟缓存）
    API: https://api.alternative.me/fng/
    返回: value (0-100), classification
    """
    global _fg_cache, _fg_cache_time
    
    # 检查缓存
    now = datetime.now()
    if (_fg_cache_time and 
        (now - _fg_cache_time).total_seconds() < _fg_cache_ttl):
        return _fg_cache
    
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        data = resp.json()
        if data.get("data"):
            item = data["data"][0]
            value = int(item["value"])
            
            if value <= 20:
                classification = "极度恐慌"
            elif value <= 40:
                classification = "恐慌"
            elif value <= 60:
                classification = "中性"
            elif value <= 80:
                classification = "贪婪"
            else:
                classification = "极度贪婪"
            
            result = {
                "value": value,
                "classification": classification,
                "timestamp": item.get("timestamp"),
                "source": "alternative.me"
            }
            # 更新缓存
            _fg_cache = result
            _fg_cache_time = now
            return result
    except Exception as e:
        logger.warning("Fear & Greed Index 获取失败: %s", str(e))
    
    return {"value": None, "classification": "未知", "source": "alternative.me"}


def get_fear_greed_risk_multiplier(value: Optional[int] = None) -> float:
    """
    根据恐惧贪婪指数计算风险乘数
    < 20 (极度恐慌) -> 0.7 (市场杀跌到位，Sell Put 加分)
    20-40 (恐慌) -> 0.85
    40-60 (中性) -> 1.0
    60-80 (贪婪) -> 1.15
    > 80 (极度贪婪) -> 1.3 (可能见顶，保守)
    """
    if value is None:
        return 1.0
    if value <= 20:
        return 0.7
    elif value <= 40:
        return 0.85
    elif value <= 60:
        return 1.0
    elif value <= 80:
        return 1.15
    else:
        return 1.3


# ============================================================
# 2. yfinance 宏观数据 (QQQ/SPY)
# ============================================================

def get_macro_data() -> Dict[str, Any]:
    """
    获取美股宏观数据 (QQQ, SPY)
    需要: pip install yfinance
    """
    result = {"qqq": None, "spy": None, "risk_off": False, "source": "yfinance"}
    
    try:
        import yfinance as yf
        
        qqq = yf.Ticker("QQQ")
        spy = yf.Ticker("SPY")
        
        qqq_info = qqq.fast_info
        spy_info = spy.fast_info
        
        qqq_price = qqq_info.get("lastPrice") or qqq_info.get("previousClose")
        spy_price = spy_info.get("lastPrice") or spy_info.get("previousClose")
        
        if qqq_price:
            result["qqq"] = round(qqq_price, 2)
        if spy_price:
            result["spy"] = round(spy_price, 2)
        
        # 判断 Risk-Off 信号: QQQ 日内跌幅 > 2%
        qqq_prev = qqq_info.get("previousClose")
        if qqq_price and qqq_prev:
            qqq_change_pct = (qqq_price - qqq_prev) / qqq_prev * 100
            result["qqq_change_pct"] = round(qqq_change_pct, 2)
            if qqq_change_pct < -2.0:
                result["risk_off"] = True
        
    except ImportError:
        logger.warning("yfinance 未安装，跳过宏观数据获取")
    except Exception as e:
        logger.warning("宏观数据获取失败: %s", str(e))
    
    return result


# ============================================================
# 3. 资金费率 (Funding Rate)
# ============================================================

def get_funding_rate(currency: str = "BTC") -> Dict[str, Any]:
    """
    获取永续合约资金费率（带5分钟缓存）
    数据源: Binance Futures (公开 API)
    返回: 当前资金费率, 历史均值
    """
    global _funding_cache, _funding_cache_time
    
    # 检查缓存
    now = datetime.now()
    if (_funding_cache_time and 
        (now - _funding_cache_time).total_seconds() < _funding_cache_ttl and
        _funding_cache.get("currency") == currency):
        return _funding_cache.get("data", {})
    
    result = {
        "current_rate": None,
        "avg_rate_8h": None,
        "sentiment": "中性",
        "source": "binance_futures"
    }
    
    try:
        symbol = f"{currency}USDT"
        
        # 当前资金费率
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/premiumIndex",
            params={"symbol": symbol},
            timeout=10
        )
        data = resp.json()
        current_rate = float(data.get("lastFundingRate", 0))
        result["current_rate"] = round(current_rate * 100, 6)
        
        # 资金费率情绪判断
        if current_rate < -0.001:
            result["sentiment"] = "极度看空 (空头付钱)"
        elif current_rate < -0.0005:
            result["sentiment"] = "看空"
        elif current_rate > 0.001:
            result["sentiment"] = "极度看多 (多头付钱)"
        elif current_rate > 0.0005:
            result["sentiment"] = "看多"
        else:
            result["sentiment"] = "中性"
        
        # 更新缓存
        _funding_cache = {"currency": currency, "data": result}
        _funding_cache_time = now
            
    except Exception as e:
        logger.warning("资金费率获取失败: %s", str(e))
    
    return result


# ============================================================
# 4. FRED 无风险利率
# ============================================================

def get_risk_free_rate() -> Dict[str, Any]:
    """
    获取无风险利率 (美国国债收益率)
    数据源: FRED API (需要 API Key)
    备选: 使用近似值 (当前约 5.3%)
    """
    result = {"rate": 5.3, "source": "default_approx"}
    
    try:
        import os
        fred_key = os.environ.get("FRED_API_KEY")
        
        if fred_key:
            from fredapi import Fred
            fred = Fred(api_key=fred_key)
            
            # 3 个月国债收益率
            data = fred.get_series("DTB3")
            if data is not None and len(data) > 0:
                latest = data.dropna().iloc[-1]
                result["rate"] = round(float(latest), 3)
                result["source"] = "FRED"
        else:
            logger.info("FRED_API_KEY 未设置，使用默认近似值 5.3%")
            
    except ImportError:
        logger.warning("fredapi 未安装，使用默认无风险利率")
    except Exception as e:
        logger.warning("FRED 无风险利率获取失败: %s", str(e))
    
    return result


# ============================================================
# 5. 聚合接口 - 一键获取所有宏观数据
# ============================================================

def get_all_macro_data() -> Dict[str, Any]:
    """
    一次性获取所有宏观数据，用于综合风险评分
    """
    result = {
        "fear_greed": get_fear_greed_index(),
        "macro": get_macro_data(),
        "funding_rate": get_funding_rate(),
        "risk_free_rate": get_risk_free_rate(),
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    # 综合风险判断
    risk_signals = []
    
    # 信号 1: 极度恐慌 -> 抄底机会
    fg_value = result["fear_greed"].get("value")
    if fg_value is not None and fg_value <= 20:
        risk_signals.append({
            "type": "opportunity",
            "text": "极度恐慌区，Sell Put 抄底机会"
        })
    
    # 信号 2: QQQ 暴跌 -> Risk-Off
    if result["macro"].get("risk_off"):
        risk_signals.append({
            "type": "warning",
            "text": f"QQQ 日内下跌 {result['macro'].get('qqq_change_pct')}%，宏观 Risk-Off"
        })
    
    # 信号 3: 资金费率极度负值 -> 空头杠杆极端
    fr_rate = result["funding_rate"].get("current_rate")
    if fr_rate is not None and fr_rate < -0.1:
        risk_signals.append({
            "type": "signal",
            "text": f"资金费率 {fr_rate}%，空头极度极端"
        })
    
    result["risk_signals"] = risk_signals
    
    return result
