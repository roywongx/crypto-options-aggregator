# Services - Spot Price
import sys
import time
import logging
from typing import Optional, Dict
from pathlib import Path

logger = logging.getLogger(__name__)

# 缓存：{currency: (price, timestamp)}
_spot_cache: Dict[str, tuple] = {}
_CACHE_TTL_SECONDS = 5  # 5秒缓存，确保所有组件使用相同的时间戳和价格

async def get_spot_price_binance_async(currency: str = "BTC") -> Optional[float]:
    """从 Binance 异步获取现货价格"""
    import httpx
    symbol = f"{currency}USDT"
    async with httpx.AsyncClient() as client:
        for host in ["api3.binance.com", "api2.binance.com", "api1.binance.com"]:
            try:
                response = await client.get(
                    f"https://{host}/api/v3/ticker/price",
                    params={"symbol": symbol},
                    timeout=5.0
                )
                if response.status_code == 200:
                    data = response.json()
                    return float(data.get("price", 0))
            except Exception:
                continue
    return None

async def get_spot_price_deribit_async(currency: str = "BTC") -> Optional[float]:
    """从 Deribit 异步获取现货价格"""
    import httpx
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                "https://www.deribit.com/api/v2/public/get_index_price",
                params={"currency": currency, "index_name": f"{currency}_usd"},
                timeout=10.0
            )
            data = response.json()
            if data.get("result"):
                return float(data["result"]["index_price"])
        except Exception as e:
            logger.warning(f"获取Deribit现货价格失败: {e}")
    return None

def get_spot_price_binance(currency: str = "BTC") -> Optional[float]:
    """从 Binance 获取现货价格（同步版本，向后兼容）"""
    import requests
    try:
        symbol = f"{currency}USDT"
        for host in ["api3.binance.com", "api2.binance.com", "api1.binance.com"]:
            try:
                response = requests.get(
                    f"https://{host}/api/v3/ticker/price",
                    params={"symbol": symbol},
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    return float(data.get("price", 0))
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"获取现货价格失败: {e}")
    return None

def get_spot_price_deribit(currency: str = "BTC") -> Optional[float]:
    """从 Deribit 获取现货价格（同步版本，向后兼容）"""
    import requests
    try:
        response = requests.get(
            "https://www.deribit.com/api/v2/public/get_index_price",
            params={"currency": currency, "index_name": f"{currency}_usd"},
            timeout=10
        )
        data = response.json()
        if data.get("result"):
            return float(data["result"]["index_price"])
    except Exception as e:
        logger.warning(f"获取Deribit现货价格失败: {e}")
    return None

async def get_spot_price_async(currency: str = "BTC", source: str = "auto") -> float:
    """异步统一入口获取现货价格

    Args:
        currency: 币种 (BTC, ETH, SOL)
        source: 来源策略
            - "auto": 按优先级尝试所有来源
            - "cache": 仅从缓存获取
            - "binance": 仅 Binance
            - "deribit": 仅 Deribit

    Returns:
        float: 现货价格

    Raises:
        RuntimeError: 所有来源都失败时抛出
    """
    now = time.time()
    if currency in _spot_cache:
        cached_price, cached_time = _spot_cache[currency]
        if now - cached_time < _CACHE_TTL_SECONDS:
            return cached_price

    if source == "cache":
        return _spot_cache.get(currency, (None, 0))[0] or 0.0

    sources = []

    def _try(name: str, val) -> Optional[float]:
        if val and isinstance(val, (int, float)) and val > 0:
            sources.append(name)
            return float(val)
        return None

    if source in ("auto", "binance"):
        spot = _try("BinanceSpot", await get_spot_price_binance_async(currency))
        if spot:
            _spot_cache[currency] = (spot, now)
            return spot

    if source in ("auto", "deribit"):
        spot = _try("DeribitIndex", await get_spot_price_deribit_async(currency))
        if spot:
            _spot_cache[currency] = (spot, now)
            return spot

    raise RuntimeError(
        f"[CRITICAL] Cannot obtain spot price for {currency}. "
        f"All sources exhausted: {sources}."
    )

def get_spot_price(currency: str = "BTC", source: str = "auto") -> float:
    """
    统一入口获取现货价格

    Args:
        currency: 币种 (BTC, ETH, SOL)
        source: 来源策略
            - "auto": 按优先级尝试所有来源
            - "cache": 仅从缓存获取
            - "binance": 仅 Binance
            - "deribit": 仅 Deribit

    Returns:
        float: 现货价格

    Raises:
        RuntimeError: 所有来源都失败时抛出
    """
    # 检查缓存
    now = time.time()
    if currency in _spot_cache:
        cached_price, cached_time = _spot_cache[currency]
        if now - cached_time < _CACHE_TTL_SECONDS:
            return cached_price

    if source == "cache":
        return _spot_cache.get(currency, (None, 0))[0] or 0.0

    sources = []

    def _try(name: str, val) -> Optional[float]:
        if val and isinstance(val, (int, float)) and val > 0:
            sources.append(name)
            return float(val)
        return None

    # 按优先级尝试
    if source in ("auto", "binance"):
        spot = _try("BinanceSpot", get_spot_price_binance(currency))
        if spot:
            _spot_cache[currency] = (spot, now)
            return spot

    if source in ("auto", "deribit"):
        spot = _try("DeribitIndex", get_spot_price_deribit(currency))
        if spot:
            _spot_cache[currency] = (spot, now)
            return spot

    # CCXT fallback
    if source == "auto":
        try:
            import ccxt
            sym_map = {"BTC": "BTC/USDT", "ETH": "ETH/USDT"}
            ex = ccxt.binance() if currency in ("BTC", "ETH") else ccxt.deribit()
            t = ex.fetch_ticker(sym_map.get(currency, f"{currency}/USDT"))
            spot = _try("CCXT", t.get('last') if t else None)
            if spot:
                _spot_cache[currency] = (spot, now)
                return spot
        except Exception as e:
            logger.warning(f"CCXT failed for {currency}: {e}")

    # 最后 fallback
    if source == "auto":
        try:
            import urllib.request, json
            for url_base, path_fn in [
                ("https://api.binance.com", lambda c: f"/api/v3/ticker/price?symbol={c}USDT"),
                ("https://api.coingecko.com", lambda c: f"/api/v3/simple/price?ids={c.lower()}&vs_currencies=usd"),
            ]:
                try:
                    req = urllib.request.Request(url_base + path_fn(currency), headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        d = json.loads(resp.read().decode())
                        if "price" in d:
                            spot = _try(url_base.split("//")[1].split(".")[0], d["price"])
                            if spot:
                                _spot_cache[currency] = (spot, now)
                                return spot
                        elif currency.lower() in d:
                            spot = _try("CoinGecko", d[currency.lower()].get("usd"))
                            if spot:
                                _spot_cache[currency] = (spot, now)
                                return spot
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"Fallback oracle failed: {e}")

    raise RuntimeError(
        f"[CRITICAL] Cannot obtain spot price for {currency}. "
        f"All sources exhausted: {sources}."
    )

def _get_spot_from_scan(currency: str = "BTC"):
    """从数据库获取现货价格（用于扫描时）"""
    try:
        from main import get_db_connection
        conn = get_db_connection(read_only=True)
        cur = conn.cursor()
        cur.execute("SELECT spot_price FROM scan_records WHERE currency=? AND spot_price > 0 ORDER BY timestamp DESC LIMIT 1", (currency,))
        row = cur.fetchone()
        if row and float(row[0]) > 0:
            return float(row[0])
    except Exception:
        pass

    try:
        import urllib.request
        import json
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={currency}USDT"
        resp = urllib.request.urlopen(url, timeout=5)
        return float(json.loads(resp.read())["price"])
    except Exception:
        pass
    return 0
