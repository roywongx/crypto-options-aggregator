"""
个人投资组合聚合服务

从 Binance 拉取用户的全量资产数据：
- 期权持仓 (EAPI /eapi/v1/position)
- 期权账户 (EAPI /eapi/v1/account 不可用，从 position 推算)
- 现货余额 (/api/v3/account)
- 活期理财 (/sapi/v1/simple-earn/flexible/position)
- 定期理财 (/sapi/v1/simple-earn/locked/position)
- 合约账户 (/fapi/v2/account)
- 清算数据 (/fapi/v1/forceOrders)

⚠️ 所有签名请求需要 .env 中配置 BINANCE_API_KEY + BINANCE_SECRET_KEY
"""
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode

import requests as req_lib

_portfolio_executor = ThreadPoolExecutor(max_workers=7, thread_name_prefix="portfolio")

from config import config
from services.spot_price import get_spot_price

logger = logging.getLogger(__name__)


# ============================================================
# Binance 签名请求
# ============================================================

def _signed_get(base_url: str, endpoint: str, params: dict = None, timeout: int = 15) -> Optional[dict]:
    """发送 Binance 签名 GET 请求"""
    api_key = config.BINANCE_API_KEY
    secret_key = config.BINANCE_SECRET_KEY
    if not api_key or not secret_key:
        logger.debug("Binance API Key not configured, skipping signed request")
        return None

    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000)

    qs = urlencode(params)
    signature = hmac.new(
        secret_key.encode("utf-8"),
        qs.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    url = f"{base_url}{endpoint}?{qs}&signature={signature}"
    try:
        resp = req_lib.get(url, headers={"X-MBX-APIKEY": api_key}, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
            return None
        else:
            logger.warning("Binance %s returned %d: %s", endpoint, resp.status_code, resp.text[:200])
            return None
    except Exception as e:
        logger.warning("Binance %s request failed: %s", endpoint, e)
        return None


# ============================================================
# 数据获取
# ============================================================

def _fetch_options_positions() -> list:
    """获取期权持仓"""
    data = _signed_get("https://eapi.binance.com", "/eapi/v1/position")
    if not data or not isinstance(data, list):
        return []
    return data


def _fetch_spot_balances() -> list:
    """获取现货非零余额"""
    data = _signed_get("https://api.binance.com", "/api/v3/account")
    if not data:
        return []
    balances = []
    for b in data.get("balances", []):
        free = float(b.get("free", 0) or 0)
        locked = float(b.get("locked", 0) or 0)
        if free > 0 or locked > 0:
            balances.append({
                "asset": b["asset"],
                "free": free,
                "locked": locked,
                "total": free + locked,
            })
    return balances


def _fetch_earn_positions() -> list:
    """获取活期+定期理财产品持仓"""
    positions = []

    flexible = _signed_get("https://api.binance.com", "/sapi/v1/simple-earn/flexible/position")
    if flexible and "rows" in flexible:
        for r in flexible["rows"]:
            positions.append({
                "asset": r.get("asset", ""),
                "amount": float(r.get("totalAmount", 0) or 0),
                "apy": float(r.get("latestAnnualPercentageRate", 0) or 0),
                "type": "活期",
                "product_id": r.get("productId", ""),
            })

    locked = _signed_get("https://api.binance.com", "/sapi/v1/simple-earn/locked/position")
    if locked and "rows" in locked:
        for r in locked["rows"]:
            positions.append({
                "asset": r.get("asset", ""),
                "amount": float(r.get("totalAmount", 0) or 0),
                "apy": float(r.get("latestAnnualPercentageRate", 0) or 0),
                "type": "定期",
                "product_id": r.get("positionId", ""),
            })

    return positions


def _fetch_funding_wallet() -> dict:
    """获取资金钱包总览（按 Spot/Earn/Options 分类）"""
    data = _signed_get("https://api.binance.com", "/sapi/v1/asset/wallet/balance")
    if not data or not isinstance(data, list):
        return {}
    wallets = {}
    for w in data:
        bal = float(w.get("balance", 0) or 0)
        wallets[w.get("walletName", "Unknown")] = {
            "balance": round(bal, 8),
            "active": w.get("activate", False),
        }
    return wallets


def _fetch_futures_account() -> dict:
    """获取合约账户概览"""
    data = _signed_get("https://fapi.binance.com", "/fapi/v2/account")
    if not data:
        return {}
    return {
        "total_wallet_balance": float(data.get("totalWalletBalance", 0) or 0),
        "total_unrealized_profit": float(data.get("totalUnrealizedProfit", 0) or 0),
        "total_margin_balance": float(data.get("totalMarginBalance", 0) or 0),
        "available_balance": float(data.get("availableBalance", 0) or 0),
        "positions": data.get("positions", []),
    }


def _fetch_options_history(symbol: str = None) -> list:
    """获取期权历史订单"""
    params = {"limit": 50}
    if symbol:
        params["symbol"] = symbol
    data = _signed_get("https://eapi.binance.com", "/eapi/v1/historyOrders", params)
    if not data or not isinstance(data, list):
        return []
    return data


# ============================================================
# 价格查询
# ============================================================

def _get_asset_prices(assets: list) -> dict:
    """批量获取资产的 USDT 价格"""
    prices = {}
    # Stablecoins
    for a in assets:
        if a in ("USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDP"):
            prices[a] = 1.0

    # 需要查价的资产
    need_price = [a for a in assets if a not in prices]
    if not need_price:
        return prices

    try:
        # Use Binance exchangeInfo to get all tickers at once
        resp = req_lib.get("https://api.binance.com/api/v3/ticker/price", timeout=10)
        if resp.status_code == 200:
            all_tickers = {t["symbol"]: float(t["price"]) for t in resp.json()}

            for asset in need_price:
                # Try direct USDT pair
                symbol = f"{asset}USDT"
                if symbol in all_tickers:
                    prices[asset] = all_tickers[symbol]
                    continue
                # Try USDC pair
                symbol = f"{asset}USDC"
                if symbol in all_tickers:
                    prices[asset] = all_tickers[symbol]
                    continue
                # Try BTC pair and convert
                symbol = f"{asset}BTC"
                if symbol in all_tickers and "BTC" in all_tickers:
                    btc_pair = all_tickers.get(f"BTCUSDT", 0)
                    prices[asset] = all_tickers[symbol] * btc_pair if btc_pair else 0
                    continue
                # Try ETH pair and convert
                symbol = f"{asset}ETH"
                if symbol in all_tickers and "ETH" in all_tickers:
                    eth_pair = all_tickers.get(f"ETHUSDT", 0)
                    prices[asset] = all_tickers[symbol] * eth_pair if eth_pair else 0
                    continue
                # 也可能是锁定/质押版本 (LD prefix)
                base = asset[2:] if asset.startswith("LD") else asset
                if base != asset:
                    prices[asset] = prices.get(base, 0)
                else:
                    prices[asset] = 0
    except Exception as e:
        logger.debug("Batch price fetch failed: %s", e)
        # Fallback: individual queries
        for asset in need_price:
            try:
                resp = req_lib.get(
                    f"https://api.binance.com/api/v3/ticker/price?symbol={asset}USDT",
                    timeout=5,
                )
                if resp.status_code == 200:
                    prices[asset] = float(resp.json()["price"])
                else:
                    prices[asset] = 0
            except Exception:
                prices[asset] = 0

    return prices


# ============================================================
# 聚合分析
# ============================================================

def _parse_option_symbol(symbol: str) -> dict:
    """解析 Binance 期权 symbol: BTC-260522-76000-P"""
    parts = symbol.split("-")
    if len(parts) != 4:
        return {}
    underlying = parts[0]
    date_str = parts[1]  # YYMMDD
    strike = float(parts[2])
    opt_type = parts[3]  # C or P

    expiry = datetime.strptime(f"20{date_str}", "%Y%m%d")
    dte = max((expiry - datetime.now()).days, 0)

    return {
        "underlying": underlying,
        "expiry": expiry.strftime("%Y-%m-%d"),
        "dte": dte,
        "strike": strike,
        "option_type": "CALL" if opt_type == "C" else "PUT",
    }


def _analyze_options(positions: list, spot_price: float) -> dict:
    """分析期权持仓组合"""
    if not positions:
        return {"count": 0, "positions": [], "summary": {}}

    enriched = []
    total_premium = 0
    total_mark_value = 0
    total_unrealized_pnl = 0
    puts_count = 0
    calls_count = 0
    short_count = 0
    long_count = 0
    nearest_expiry_dte = 999

    for p in positions:
        symbol = p.get("symbol", "")
        parsed = _parse_option_symbol(symbol)
        side = p.get("side", "LONG")
        qty = abs(float(p.get("quantity", 0) or 0))
        entry_price = float(p.get("entryPrice", 0) or 0)
        mark_price = float(p.get("markPrice", 0) or 0)
        mark_value = float(p.get("markValue", 0) or 0)
        unrealized_pnl = float(p.get("unrealizedPNL", 0) or 0)
        strike = parsed.get("strike", 0)

        # Premium = entry_price * qty (for 1 contract multiplier = 1)
        premium = entry_price * qty
        mark_val_abs = abs(mark_value)

        total_premium += premium
        total_mark_value += mark_val_abs
        total_unrealized_pnl += unrealized_pnl

        if parsed.get("option_type") == "PUT":
            puts_count += 1
        else:
            calls_count += 1

        if side == "SHORT":
            short_count += 1
        else:
            long_count += 1

        if parsed.get("dte", 999) < nearest_expiry_dte:
            nearest_expiry_dte = parsed["dte"]

        # Strike distance from spot
        dist_pct = abs(strike - spot_price) / spot_price * 100 if spot_price > 0 and strike > 0 else 0
        otm_itm = "OTM" if (
            (parsed.get("option_type") == "PUT" and strike < spot_price) or
            (parsed.get("option_type") == "CALL" and strike > spot_price)
        ) else "ITM"

        enriched.append({
            "symbol": symbol,
            **parsed,
            "side": side,
            "quantity": round(qty, 2),
            "entry_price": round(entry_price, 2),
            "mark_price": round(mark_price, 2),
            "mark_value": round(mark_val_abs, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "premium": round(premium, 2),
            "distance_spot_pct": round(dist_pct, 2),
            "otm_itm": otm_itm,
            "pnl_pct": round(unrealized_pnl / premium * 100, 1) if premium > 0 else 0,
        })

    # Sort by expiry (nearest first)
    enriched.sort(key=lambda x: x["dte"])

    return {
        "count": len(positions),
        "positions": enriched,
        "summary": {
            "total_premium_usd": round(total_premium, 2),
            "total_mark_value_usd": round(total_mark_value, 2),
            "total_unrealized_pnl_usd": round(total_unrealized_pnl, 2),
            "total_pnl_pct": round(total_unrealized_pnl / total_premium * 100, 1) if total_premium > 0 else 0,
            "puts_count": puts_count,
            "calls_count": calls_count,
            "short_count": short_count,
            "long_count": long_count,
            "nearest_expiry_dte": nearest_expiry_dte if nearest_expiry_dte < 999 else None,
        },
    }


def _analyze_spot(balances: list) -> dict:
    """估算现货余额的 USD 价值"""
    if not balances:
        return {"count": 0, "assets": [], "total_usd": 0}

    assets = [b["asset"] for b in balances]
    prices = _get_asset_prices(set(assets + ["BTC", "ETH"]))

    total_usd = 0
    enriched = []
    for b in balances:
        price = prices.get(b["asset"], 0)
        value_usd = b["total"] * price
        total_usd += value_usd
        if value_usd > 0.01:  # 只展示 > $0.01 的
            enriched.append({
                "asset": b["asset"],
                "amount": round(b["total"], 8),
                "price": price,
                "value_usd": round(value_usd, 2),
            })

    enriched.sort(key=lambda x: x["value_usd"], reverse=True)
    return {
        "count": len(enriched),
        "assets": enriched,
        "total_usd": round(total_usd, 2),
    }


def _analyze_earn(positions: list) -> dict:
    """分析理财产品"""
    if not positions:
        return {"count": 0, "products": [], "total_usd": 0, "weighted_apy": 0}

    assets = list(set(p["asset"] for p in positions))
    prices = _get_asset_prices(assets)

    total_usd = 0
    apy_weighted_sum = 0
    enriched = []

    for p in positions:
        price = prices.get(p["asset"], 0)
        value_usd = p["amount"] * price
        total_usd += value_usd
        apy_weighted_sum += value_usd * p["apy"]

        enriched.append({
            "asset": p["asset"],
            "amount": round(p["amount"], 8),
            "apy": round(p["apy"], 4),
            "type": p["type"],
            "value_usd": round(value_usd, 2),
        })

    enriched.sort(key=lambda x: x["value_usd"], reverse=True)
    weighted_apy = round(apy_weighted_sum / total_usd * 100, 2) if total_usd > 0 else 0

    return {
        "count": len(enriched),
        "products": enriched,
        "total_usd": round(total_usd, 2),
        "weighted_apy_pct": weighted_apy,
    }


# ============================================================
# 缓存
# ============================================================

_portfolio_cache: dict = {}  # {data, timestamp}
_PORTFOLIO_CACHE_TTL = 300  # 5 分钟


# ============================================================
# 主入口
# ============================================================

def get_portfolio() -> dict:
    """获取完整投资组合概览 — 并行 API 调用 + 缓存回退"""
    api_key = config.BINANCE_API_KEY
    if not api_key:
        # API 未配置但缓存可用 → 返回缓存
        if _portfolio_cache:
            aged = int(time.time() - _portfolio_cache["ts"])
            cached = dict(_portfolio_cache["data"])
            cached["cache_hit"] = True
            cached["cache_age_seconds"] = aged
            cached["configured"] = False
            return cached
        return {"error": "未配置 Binance API Key", "configured": False}

    t0 = time.time()

    # 并行获取 spot_price + 所有 Binance 数据
    spot_price = 0

    future_spot = _portfolio_executor.submit(_safe_spot_price)
    future_opts = _portfolio_executor.submit(_fetch_options_positions)
    future_spot_bal = _portfolio_executor.submit(_fetch_spot_balances)
    future_flex = _portfolio_executor.submit(_signed_get, "https://api.binance.com", "/sapi/v1/simple-earn/flexible/position")
    future_locked = _portfolio_executor.submit(_signed_get, "https://api.binance.com", "/sapi/v1/simple-earn/locked/position")
    future_futures = _portfolio_executor.submit(_fetch_futures_account)
    future_funding = _portfolio_executor.submit(_fetch_funding_wallet)

    spot_price = _result_or(future_spot, 0)
    options_raw = _result_or(future_opts, [])
    spot_balances = _result_or(future_spot_bal, [])

    flexible_data = _result_or(future_flex, None)
    locked_data = _result_or(future_locked, None)
    futures_account = _result_or(future_futures, {})
    funding_wallet = _result_or(future_funding, {})

    for f in as_completed([future_spot, future_opts, future_spot_bal,
                           future_flex, future_locked, future_futures, future_funding], timeout=0):
        pass

    # 核心数据为空且缓存可用 → 回退缓存
    if not options_raw and not spot_balances and _portfolio_cache:
        aged = int(time.time() - _portfolio_cache["ts"])
        cached = dict(_portfolio_cache["data"])
        cached["cache_hit"] = True
        cached["cache_age_seconds"] = aged
        cached["timestamp"] = datetime.now().isoformat()
        return cached

    earn_positions = _merge_earn_results(flexible_data, locked_data)

    options_analysis = _analyze_options(options_raw, spot_price)
    spot_analysis = _analyze_spot(spot_balances)
    earn_analysis = _analyze_earn(earn_positions)

    total_portfolio_usd = (
        spot_analysis.get("total_usd", 0)
        + earn_analysis.get("total_usd", 0)
        + options_analysis.get("summary", {}).get("total_mark_value_usd", 0)
        + futures_account.get("total_wallet_balance", 0)
    )

    elapsed_ms = int((time.time() - t0) * 1000)

    result = {
        "configured": True,
        "cache_hit": False,
        "cache_age_seconds": 0,
        "timestamp": datetime.now().isoformat(),
        "spot_price_btc": spot_price,
        "elapsed_ms": elapsed_ms,

        "options": options_analysis,
        "spot": spot_analysis,
        "earn": earn_analysis,
        "futures": futures_account,
        "funding_wallet": funding_wallet,

        "total_portfolio_usd": round(total_portfolio_usd, 2),
    }

    # 更新缓存
    _portfolio_cache["data"] = dict(result)
    _portfolio_cache["ts"] = time.time()

    return result


def _safe_spot_price() -> float:
    try:
        return get_spot_price("BTC")
    except Exception:
        return 0


def _result_or(future, default):
    try:
        return future.result(timeout=20)
    except Exception:
        return default


def _merge_earn_results(flexible_data, locked_data) -> list:
    """合并活期 + 定期理财结果"""
    positions = []
    if flexible_data and "rows" in flexible_data:
        for r in flexible_data["rows"]:
            positions.append({
                "asset": r.get("asset", ""),
                "amount": float(r.get("totalAmount", 0) or 0),
                "apy": float(r.get("latestAnnualPercentageRate", 0) or 0),
                "type": "活期",
                "product_id": r.get("productId", ""),
            })
    if locked_data and "rows" in locked_data:
        for r in locked_data["rows"]:
            positions.append({
                "asset": r.get("asset", ""),
                "amount": float(r.get("totalAmount", 0) or 0),
                "apy": float(r.get("latestAnnualPercentageRate", 0) or 0),
                "type": "定期",
                "product_id": r.get("positionId", ""),
            })
    return positions
