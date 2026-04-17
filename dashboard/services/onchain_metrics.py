"""
链上数据指标服务
使用与 fuckbtc.com 相同的真实数据源

数据源:
1. MVRV Ratio: https://looknode-proxy.corms-cushier-0l.workers.dev/mCapRealizedRatio
2. Balanced Price: https://looknode-proxy.corms-cushier-0l.workers.dev/balancedPrice
3. 200WMA: Binance 周K线 (200周)
4. 减半倒计时: blockchain.info 区块高度
"""
import requests
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from services.api_retry import request_with_retry

logger = logging.getLogger(__name__)


class OnChainMetrics:
    """使用真实数据源的链上指标"""
    
    # 缓存
    _cache = {}
    _cache_time = None
    CACHE_DURATION = 300  # 5分钟缓存
    
    @classmethod
    def get_all_metrics(cls, currency: str = "bitcoin") -> Dict[str, Any]:
        """获取所有链上指标"""
        # 检查缓存
        if cls._cache_time and cls._cache:
            elapsed = (datetime.now() - cls._cache_time).total_seconds()
            if elapsed < cls.CACHE_DURATION:
                return cls._cache.copy()
        
        try:
            metrics = cls._fetch_all()
            if metrics and metrics.get('current_price'):
                cls._cache = metrics
                cls._cache_time = datetime.now()
                return metrics
        except Exception as e:
            logger.error(f"获取链上指标失败: {e}")
        
        return cls._cache if cls._cache else cls._get_fallback_data()
    
    @classmethod
    def _fetch_all(cls) -> Dict[str, Any]:
        """获取所有数据"""
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        # 获取当前价格
        current_price = cls._get_current_price()
        if not current_price:
            return cls._get_fallback_data()
        
        # 获取MVRV
        mvrv = cls._get_mvrv()
        
        # 获取Balanced Price
        balanced_price = cls._get_balanced_price()
        
        # 获取200WMA
        wma_200, wma_ratio = cls._get_200wma(current_price)
        
        # 获取减半倒计时
        halving_days = cls._get_halving_countdown()
        
        return {
            "current_price": round(current_price, 2),
            "mvrv_ratio": round(mvrv, 2) if mvrv else None,
            "mvrv_signal": cls._interpret_mvrv(mvrv) if mvrv else None,
            "price_200wma": round(wma_200, 2) if wma_200 else None,
            "price_to_200wma_ratio": round(wma_ratio, 2) if wma_ratio else None,
            "balanced_price": round(balanced_price, 2) if balanced_price else None,
            "balanced_price_ratio": round(current_price / balanced_price, 2) if balanced_price else None,
            "halving_days_remaining": halving_days,
            "timestamp": datetime.now().isoformat(),
            "data_source": "fuckbtc.com APIs"
        }
    
    @classmethod
    def _get_current_price(cls) -> Optional[float]:
        """获取当前价格"""
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        # Binance
        try:
            resp = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"},
                timeout=10,
                verify=False
            )
            if resp.status_code == 200:
                return float(resp.json().get("price", 0))
        except Exception as e:
            logger.warning(f"Binance价格失败: {e}")
        
        # CoinGecko
        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"},
                timeout=10,
                verify=False
            )
            if resp.status_code == 200:
                return resp.json().get("bitcoin", {}).get("usd")
        except Exception as e:
            logger.warning(f"CoinGecko价格失败: {e}")
        
        return None
    
    @classmethod
    def _get_mvrv(cls) -> Optional[float]:
        """
        获取MVRV Ratio
        API: https://looknode-proxy.corms-cushier-0l.workers.dev/mCapRealizedRatio
        """
        try:
            resp = request_with_retry(
                "https://looknode-proxy.corms-cushier-0l.workers.dev/mCapRealizedRatio",
                timeout=10, verify=False, max_retries=3
            )
            data = resp.json()
            # API返回格式: {"code": 100, "data": [{"t": timestamp, "v": value}, ...]}
            if "data" in data and data["data"]:
                return float(data["data"][-1]["v"])
            elif "values" in data and data["values"]:
                return float(data["values"][-1].get("value", 0))
            elif "value" in data:
                return float(data["value"])
        except Exception as e:
            logger.warning(f"MVRV获取失败: {e}")
        
        return None
    
    @classmethod
    def _get_balanced_price(cls) -> Optional[float]:
        """
        获取Balanced Price
        API: https://looknode-proxy.corms-cushier-0l.workers.dev/balancedPrice
        """
        try:
            resp = request_with_retry(
                "https://looknode-proxy.corms-cushier-0l.workers.dev/balancedPrice",
                timeout=10, verify=False, max_retries=3
            )
            data = resp.json()
            # API返回格式: {"code": 100, "data": [{"t": timestamp, "v": value}, ...]}
            if "data" in data and data["data"]:
                return float(data["data"][-1]["v"])
            elif "values" in data and data["values"]:
                return float(data["values"][-1].get("value", 0))
            elif "value" in data:
                return float(data["value"])
        except Exception as e:
            logger.warning(f"Balanced Price获取失败: {e}")
        
        return None
    
    @classmethod
    def _get_200wma(cls, current_price: float):
        """
        计算200周均线
        使用Binance周K线数据，取最近200周收盘价平均值
        """
        try:
            resp = request_with_retry(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "1w", "limit": 200},
                timeout=15, verify=False, max_retries=3
            )
            klines = resp.json()
            closes = [float(k[4]) for k in klines]  # 收盘价
            
            if closes:
                wma_200 = sum(closes) / len(closes)
                ratio = current_price / wma_200 if wma_200 else None
                return wma_200, ratio
        except Exception as e:
            logger.warning(f"200WMA计算失败: {e}")
        
        return None, None
    
    @classmethod
    def _get_halving_countdown(cls) -> Optional[int]:
        """获取减半倒计时"""
        try:
            resp = request_with_retry(
                "https://blockchain.info/q/getblockcount",
                timeout=10, verify=False, max_retries=3
            )
            current_block = int(resp.text)
            next_halving_block = 1050000
            blocks_remaining = next_halving_block - current_block
            days_remaining = int(blocks_remaining * 10 / (60 * 24))
            return max(0, days_remaining)
        except Exception as e:
            logger.warning(f"减半倒计时失败: {e}")
        
        # 估算
        from datetime import datetime
        halving_date = datetime(2028, 4, 1)
        return max(0, (halving_date - datetime.now()).days)
    
    @classmethod
    def _interpret_mvrv(cls, mvrv: float) -> str:
        """解读MVRV信号"""
        if mvrv < 1:
            return "低估（历史底部）"
        elif mvrv < 1.5:
            return "偏低（积累区）"
        elif mvrv < 3:
            return "正常区间"
        elif mvrv < 3.5:
            return "偏高（谨慎）"
        elif mvrv < 5:
            return "过热（顶部区域）"
        else:
            return "极度泡沫（历史顶部）"
    
    @classmethod
    def _get_fallback_data(cls) -> Dict[str, Any]:
        """降级数据"""
        return {
            "current_price": None,
            "mvrv_ratio": None,
            "mvrv_signal": "数据获取失败",
            "price_200wma": None,
            "price_to_200wma_ratio": None,
            "balanced_price": None,
            "balanced_price_ratio": None,
            "halving_days_remaining": None,
            "timestamp": datetime.now().isoformat(),
            "data_source": "error",
            "error": "链上数据获取失败，请稍后重试"
        }
