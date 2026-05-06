"""
Binance Options API — 获取币安期权链数据

Binance Options API 端点 (EAPI v1):
  GET /eapi/v1/ticker         — 期权行情 (volume, bid/ask)
  GET /eapi/v1/mark           — 标记价格 + IV + Greeks
  GET /eapi/v1/openInterest   — 未平仓合约
  GET /eapi/v1/exchangeInfo   — 期权合约规则
"""
import logging
import time
from typing import Optional, List, Dict, Any

import httpx

logger = logging.getLogger(__name__)

BINANCE_OPTIONS_BASE = "https://eapi.binance.com"
TICKER_CACHE_TTL = 30  # 秒
MARK_CACHE_TTL = 30
OI_CACHE_TTL = 120


# ---------------------------------------------------------------------------
# 模块级缓存
# ---------------------------------------------------------------------------
_ticker_cache: Dict[str, tuple] = {}
_mark_cache: Dict[str, tuple] = {}
_oi_cache: Dict[str, tuple] = {}


def _get_json(url: str, params: dict = None, timeout: float = 10.0) -> Optional[dict]:
    try:
        resp = httpx.get(url, params=params, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        logger.warning("Binance Options HTTP %d from %s", resp.status_code, url)
    except (httpx.HTTPError, httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("Binance Options request failed: %s", e)
    return None


def _get_tickers(currency: str) -> List[Dict]:
    now = time.time()
    if currency in _ticker_cache:
        data, ts = _ticker_cache[currency]
        if now - ts < TICKER_CACHE_TTL:
            return data

    result = _get_json(f"{BINANCE_OPTIONS_BASE}/eapi/v1/ticker")
    if result and isinstance(result, list):
        _ticker_cache[currency] = (result, now)
        return result
    return []


def _get_marks(currency: str) -> Dict[str, Dict]:
    """返回 {symbol: mark_data} 映射"""
    now = time.time()
    if currency in _mark_cache:
        data, ts = _mark_cache[currency]
        if now - ts < MARK_CACHE_TTL:
            return data

    result = _get_json(f"{BINANCE_OPTIONS_BASE}/eapi/v1/mark")
    if result and isinstance(result, list):
        marks = {}
        for m in result:
            sym = m.get("symbol", "")
            if sym:
                marks[sym] = m
        _mark_cache[currency] = (marks, now)
        return marks
    return {}


def _get_open_interest(currency: str) -> Dict[str, float]:
    """返回 {symbol: oi_value} 映射"""
    now = time.time()
    if currency in _oi_cache:
        data, ts = _oi_cache[currency]
        if now - ts < OI_CACHE_TTL:
            return data

    result = _get_json(f"{BINANCE_OPTIONS_BASE}/eapi/v1/openInterest")
    if result and isinstance(result, list):
        oi = {}
        for item in result:
            sym = item.get("symbol", "")
            if sym:
                oi[sym] = float(item.get("openInterest", 0) or 0)
        _oi_cache[currency] = (oi, now)
        return oi
    # Binance EAPI 不公开 OI 端点，静默处理
    _oi_cache[currency] = ({}, now)
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
    """获取币安期权链，返回标准化合约列表。"""
    try:
        from services.spot_price import get_spot_price
        spot = get_spot_price(currency)
    except Exception:
        spot = 0

    from services.instrument import _parse_inst_name

    tickers = _get_tickers(currency)
    marks = _get_marks(currency)
    oi_map = _get_open_interest(currency)

    if not tickers or not marks:
        return []

    # 建立 ticker 索引: symbol -> ticker
    ticker_map = {}
    for t in tickers:
        sym = t.get("symbol", "")
        if sym:
            ticker_map[sym] = t

    results = []

    for sym, mark in marks.items():
        if not sym.startswith(currency):
            continue

        # 期权类型过滤
        sym_opt_type = "C" if sym.endswith("-C") else "P" if sym.endswith("-P") else ""
        if option_type.upper() == "PUT" and sym_opt_type != "P":
            continue
        if option_type.upper() == "CALL" and sym_opt_type != "C":
            continue

        meta = _parse_inst_name(sym)
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

        # 从 ticker 获取 volume / bid / ask
        ticker = ticker_map.get(sym, {})
        volume = float(ticker.get("volume", 0) or 0)
        if volume < min_vol:
            continue

        bid = float(ticker.get("bidPrice", 0) or 0)
        ask = float(ticker.get("askPrice", 0) or 0)
        if bid > 0 and ask > 0:
            spread_pct = (ask - bid) / ((bid + ask) / 2) * 100
            if spread_pct > max_spread:
                continue
        else:
            spread_pct = 0

        # 从 mark 获取 IV
        iv = float(mark.get("markIV", 0) or 0)
        # Binance markIV 以小数返回 (如 0.52 = 52%)
        if iv < 1.0:
            iv = iv * 100
        if iv <= 0 or iv > 200:
            continue

        # 使用 Binance 官方 delta（比本地 BS 计算更准确）
        delta_val = abs(float(mark.get("delta", 0) or 0))
        if delta_val > max_delta:
            continue

        # Binance markPrice 已经是 USDT 计价（每张合约=1 BTC 名义价值）
        price = float(mark.get("markPrice", 0) or 0)
        premium_usdt = price
        oi = oi_map.get(sym, 0)

        # 计算 APR
        cv = strike_val * margin_ratio
        apr = (premium_usdt / cv) * (365 / dte) * 100 if cv > 0 and dte > 0 else 0

        results.append({
            "symbol": sym,
            "platform": "Binance",
            "strike": strike_val,
            "dte": dte,
            "option_type": sym_opt_type,
            "premium_usdt": round(premium_usdt, 2),
            "premium_usd": round(premium_usdt, 2),
            "premium": round(price, 4),
            "premium_btc": round(price / spot, 6) if spot > 0 else 0,
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
            # 附加 Greeks（Binance 官方提供）
            "gamma": round(float(mark.get("gamma", 0) or 0), 6),
            "theta": round(float(mark.get("theta", 0) or 0), 2),
            "vega": round(float(mark.get("vega", 0) or 0), 2),
        })

    logger.info("Binance Options: %d contracts for %s %s (DTE %d-%d)",
                len(results), currency, option_type, min_dte, max_dte)
    return results
