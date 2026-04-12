"""
动态支撑位计算器
基于技术分析和链上数据计算动态支撑位
"""
import requests
import numpy as np
from datetime import datetime, timedelta


class DynamicSupportCalculator:
    def __init__(self, currency: str = "BTC"):
        self.currency = currency
        self.support_levels = {}
    
    def get_dynamic_floors(self) -> dict:
        """获取动态支撑位"""
        try:
            # 方法1: 200日移动平均线
            ma200 = self._get_200day_ma()
            
            # 方法2: 斐波那契回撤位
            fib_levels = self._get_fibonacci_levels()
            
            # 方法3: 链上数据 (已实现价格)
            on_chain_price = self._get_on_chain_price()

            # 综合计算支撑位
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
        except Exception as e:
            print(f"计算动态支撑位失败: {e}")
            # 回退到硬编码值
            return {
                "regular": 55000.0,
                "extreme": 45000.0,
                "components": {},
                "timestamp": datetime.now().isoformat(),
                "fallback": True
            }
    
    def _get_200day_ma(self) -> float:
        """获取200日移动平均线"""
        try:
            # 使用CoinGecko API获取历史价格数据
            url = f"https://api.coingecko.com/api/v3/coins/{self.currency.lower()}/market_chart"
            params = {
                "vs_currency": "usd",
                "days": "200",
                "interval": "daily"
            }
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            if "prices" in data and len(data["prices"]) > 0:
                prices = [p[1] for p in data["prices"]]
                return np.mean(prices[-200:])  # 最近200天平均
        except Exception as e:
            print(f"获取200日均线失败: {e}")
        
        # 回退值
        return 60000.0 if self.currency == "BTC" else 3000.0
    
    def _get_fibonacci_levels(self) -> dict:
        """计算斐波那契回撤位"""
        try:
            # 获取最近的高点和低点
            url = f"https://api.coingecko.com/api/v3/coins/{self.currency.lower()}/market_chart"
            params = {
                "vs_currency": "usd",
                "days": "90",
                "interval": "daily"
            }
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            if "prices" in data and len(data["prices"]) > 0:
                prices = [p[1] for p in data["prices"]]
                high = max(prices)
                low = min(prices)
                
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
        except Exception as e:
            print(f"计算斐波那契回撤位失败: {e}")
        
        # 回退值
        if self.currency == "BTC":
            high, low = 73000, 38000
        else:
            high, low = 4000, 2000
        
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
        """获取链上已实现价格"""
        # 这里需要接入链上数据API
        # 暂时使用回退值
        if self.currency == "BTC":
            return 50000.0
        else:
            return 2500.0
    
    def _calculate_regular_floor(self, ma200: float, fib_levels: dict, on_chain: float) -> float:
        """计算常规支撑位"""
        # 综合多个指标
        supports = [
            ma200,
            fib_levels.get("0.382", 50000),
            on_chain
        ]
        # 取平均值作为常规支撑
        return sum(supports) / len(supports)
    
    def _calculate_extreme_floor(self, regular_floor: float, fib_levels: dict) -> float:
        """计算极端支撑位"""
        # 极端支撑位通常在常规支撑位下方10-20%
        extreme1 = regular_floor * 0.85  # 15% below regular
        extreme2 = fib_levels.get("0.618", regular_floor * 0.8)
        return min(extreme1, extreme2)
