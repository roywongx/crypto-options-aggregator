"""
动态支撑位计算器
基于Binance真实K线数据和链上数据计算动态支撑位
"""
import httpx
import json
import logging
from datetime import datetime, timedelta
from services.api_retry import request_with_retry
from constants import get_spot_fallback

logger = logging.getLogger(__name__)


class DynamicSupportCalculator:
    def __init__(self, currency: str = "BTC"):
        self.currency = currency

    def get_dynamic_floors(self) -> dict:
        """获取动态支撑位"""
        try:
            ma200 = self._get_200day_ma()
            fib_levels = self._get_fibonacci_levels()
            on_chain_price = self._get_on_chain_price()

            regular_floor = self._calculate_regular_floor(ma200, fib_levels, on_chain_price)
            extreme_floor = self._calculate_extreme_floor(regular_floor, fib_levels)

            return {
                "regular": regular_floor,
                "extreme": extreme_floor,
                "components": {
                    "ma200": ma200,
                    "fib_levels": fib_levels,
                    "on_chain": on_chain_price
                },
                "timestamp": datetime.now().isoformat()
            }
        except (ValueError, TypeError, ZeroDivisionError, RuntimeError) as e:
            logger.error("计算动态支撑位失败: {e}")
            fallback = get_spot_fallback(self.currency)
            return {
                "regular": fallback * 0.75,
                "extreme": fallback * 0.55,
                "components": {},
                "timestamp": datetime.now().isoformat(),
                "fallback": True
            }

    def _get_200day_ma(self) -> float:
        """获取200日移动平均线 - 使用Binance日线数据"""
        try:
            resp = request_with_retry(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": f"{self.currency}USDT", "interval": "1d", "limit": 200},
                timeout=10, verify=True, max_retries=3
            )
            klines = resp.json()
            closes = [float(k[4]) for k in klines]
            if closes:
                return sum(closes) / len(closes)
        except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
            logger.warning("获取200日均线失败: %s", e)

        fallback = get_spot_fallback(self.currency)
        return fallback * 0.85

    def _get_fibonacci_levels(self) -> dict:
        """计算斐波那契回撤位 - 使用Binance真实高低点"""
        try:
            resp = request_with_retry(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": f"{self.currency}USDT", "interval": "1d", "limit": 90},
                timeout=10, verify=True, max_retries=3
            )
            klines = resp.json()
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            if highs and lows:
                high = max(highs)
                low = min(lows)
                diff = high - low
                return {
                    "0.236": high - diff * 0.236,
                    "0.382": high - diff * 0.382,
                    "0.500": high - diff * 0.500,
                    "0.618": high - diff * 0.618,
                    "0.786": high - diff * 0.786,
                    "high": high,
                    "low": low
                }
        except (ValueError, TypeError, ZeroDivisionError, RuntimeError) as e:
            logger.warning("计算斐波那契回撤位失败: {e}")

        spot = get_spot_fallback(self.currency)
        high, low = spot * 1.15, spot * 0.65

        diff = high - low
        return {
            "0.236": high - diff * 0.236,
            "0.382": high - diff * 0.382,
            "0.500": high - diff * 0.500,
            "0.618": high - diff * 0.618,
            "0.786": high - diff * 0.786,
            "high": high,
            "low": low
        }

    def _get_on_chain_price(self) -> float:
        """获取链上已实现价格 - 从MVRV API反算"""
        try:
            resp = request_with_retry(
                "https://looknode-proxy.corms-cushier-0l.workers.dev/balancedPrice",
                timeout=10, verify=True, max_retries=3
            )
            data = resp.json()
            if "data" in data and data["data"]:
                balanced = float(data["data"][-1]["v"])
                if balanced and balanced > 0:
                    return round(balanced, 2)

            from services.onchain_metrics import OnChainMetrics
            inst = OnChainMetrics()
            price = inst._get_current_price()
            if price:
                resp2 = request_with_retry(
                    "https://looknode-proxy.corms-cushier-0l.workers.dev/mCapRealizedRatio",
                    timeout=10, verify=True, max_retries=3
                )
                d = resp2.json()
                if "data" in d and d["data"]:
                    mvrv = float(d["data"][-1]["v"])
                    if mvrv and mvrv > 0:
                        realized = price / mvrv
                        return round(realized, 2)
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("获取链上价格失败: {e}")

        fallback = get_spot_fallback(self.currency)
        return fallback * 0.5  # Realized Price 通常远低于现价

    def _calculate_regular_floor(self, ma200: float, fib_levels: dict, on_chain: float) -> float:
        """计算常规支撑位 - 加权平均: MA200 25%, Fibonacci 25%, 链上数据 50%"""
        weights = [0.25, 0.25, 0.50]
        supports = [
            ma200,
            fib_levels.get("0.382", 50000),
            on_chain
        ]
        return sum(s * w for s, w in zip(supports, weights))

    def _calculate_extreme_floor(self, regular_floor: float, fib_levels: dict) -> float:
        """计算极端支撑位"""
        extreme1 = regular_floor * 0.85
        extreme2 = fib_levels.get("0.618", regular_floor * 0.8)
        return min(extreme1, extreme2)
