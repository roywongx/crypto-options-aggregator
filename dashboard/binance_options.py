"""
Binance Options API — 获取币安期权链数据

Binance Options API 端点 (EAPI v1):
  GET /eapi/v1/ticker         — 期权行情
  GET /eapi/v1/exchangeInfo   — 期权合约规则
  GET /eapi/v1/mark           — 标记价格
  GET /eapi/v1/openInterest   — 未平仓合约
"""
import logging
import time
from typing import Optional, List, Dict, Any

import httpx

logger = logging.getLogger(__name__)

BINANCE_OPTIONS_BASE = "https://eapi.binance.com"
CACHE_TTL = 30  # 秒


# ---------------------------------------------------------------------------
# 模块级缓存（避免同一次扫描中重复请求）
# ---------------------------------------------------------------------------
_ticker_cache: Dict[str, tuple] = {}  # {currency: (data, timestamp)}
_exchange_info_cache: Dict[str, tuple] = {}


def _get_json(url: str, params: dict = None, timeout: float = 10.0) -> Optional[dict]:
    try:
        resp = httpx.get(url, params=params, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        logger.warning("Binance Options HTTP %d from %s", resp.status_code, url)
    except (httpx.HTTPError, httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("Binance Options request failed: %s", e)
    return None


def _get_ticker(currency: str) -> List[Dict]:
    now = time.time()
    if currency in _ticker_cache:
        data, ts = _ticker_cache[currency]
        if now - ts < CACHE_TTL:
            return data

    result = _get_json(f"{BINANCE_OPTIONS_BASE}/eapi/v1/ticker")
    if result and isinstance(result, list):
        _ticker_cache[currency] = (result, now)
        return result
    return []


def _get_exchange_info(currency: str) -> Dict:
    now = time.time()
    if currency in _exchange_info_cache:
        data, ts = _exchange_info_cache[currency]
        if now - ts < 600:
            return data

    result = _get_json(f"{BINANCE_OPTIONS_BASE}/eapi/v1/exchangeInfo")
    if result:
        _exchange_info_cache[currency] = (result, now)
        return result
    return {}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def fetch_binance_options(
    currency: str = "BTC",
    option_type: str = "PUT",
    min_dte: int = 3,
    max_dte: int = 60,
    max_delta: float = 0.40,
    strike: Optional[float] = None,
    strike_range: Optional[tuple] = None,
    min_vol: float = 0,
    max_spread: float = 20.0,
    margin_ratio: float = 0.2,
) -> List[Dict[str, Any]]:
    """
    获取币安期权链，返回标准化合约列表。
    """
    try:
        from services.spot_price import get_spot_price
        spot = get_spot_price(currency)
    except Exception:
        spot = 0

    try:
        from services.dvol_analyzer import calc_delta_bs
    except Exception:
        calc_delta_bs = None

    tickers = _get_ticker(currency)
    if not tickers:
        return []

    from services.instrument import _parse_inst_name
    results = []

    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.startswith(currency):
            continue

        sym_opt_type = "C" if symbol.endswith("-C") else "P" if symbol.endswith("-P") else ""
        if option_type.upper() == "PUT" and sym_opt_type != "P":
            continue
        if option_type.upper() == "CALL" and sym_opt_type != "C":
            continue

        meta = _parse_inst_name(symbol)
        if not meta:
            continue

        dte = meta.dte
        if dte < min_dte or dte > max_dte:
            continue

        strike_val = meta.strike
        if strike_val <= 0:
            continue

        if strike is not None and abs(strike_val - strike) / max(strike, 1) > 0.15:
            continue
        if strike_range and not (strike_range[0] <= strike_val <= strike_range[1]):
            continue

        volume = float(t.get("volume", 0) or 0)
        if volume < min_vol:
            continue

        bid = float(t.get("bidPrice", 0) or 0)
        ask = float(t.get("askPrice", 0) or 0)
        if bid > 0 and ask > 0:
            spread_pct = (ask - bid) / ((bid + ask) / 2) * 100
            if spread_pct > max_spread:
                continue
        else:
            spread_pct = 0

        iv = float(t.get("markIV", 0) or 0)
        if iv <= 0 or iv > 200:
            continue

        price = float(t.get("markPrice", 0) or 0)
        oi = float(t.get("openInterest", 0) or 0)

        # Delta 计算
        if calc_delta_bs and spot > 0:
            try:
                delta_val = abs(calc_delta_bs(strike_val, spot, iv, dte, sym_opt_type))
            except Exception:
                delta_val = 0.5
        else:
            delta_val = 0.5

        if delta_val > max_delta:
            continue

        premium_usdt = price * spot if spot > 0 else price
        cv = strike_val * margin_ratio
        apr = (premium_usdt / cv) * (365 / dte) * 100 if cv > 0 and dte > 0 else 0

        results.append({
            "symbol": symbol,
            "platform": "Binance",
            "strike": strike_val,
            "dte": dte,
            "option_type": sym_opt_type,
            "premium_usdt": round(premium_usdt, 2),
            "premium_usd": round(premium_usdt, 2),
            "premium": round(price, 4),
            "iv": round(iv, 1),
            "delta": round(delta_val, 3),
            "open_interest": round(oi, 0),
            "oi": round(oi, 0),
            "volume_24h": round(volume, 0),
            "bid": round(bid, 4),
            "ask": round(ask, 4),
            "spread_pct": round(spread_pct, 1),
            "apr": round(apr, 1),
            "currency": currency,
            "mark_price": round(price, 4),
            "mark_iv": round(iv, 1),
            "expiry": meta.expiry,
        })

    logger.info("Binance Options: %d contracts for %s %s (DTE %d-%d)",
                len(results), currency, option_type, min_dte, max_dte)
    return results
