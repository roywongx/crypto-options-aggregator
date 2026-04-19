# Dashboard services - spot price fetching
import requests
import sys
from typing import Optional

def get_spot_price_binance(currency: str = "BTC") -> Optional[float]:
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
                print(f"[ERROR] spot_price.py: {e}", file=sys.stderr)
        print(f"获取现货价格失败: {e}", file=sys.stderr)

    except Exception as e:
        print(f"获取现货价格失败: {e}", file=sys.stderr)
    return None
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
        print(f"获取Deribit现货价格失败: {e}", file=sys.stderr)
    return None

def get_spot_price(currency: str = "BTC") -> float:
    """统一入口：按优先级 Binance → Deribit → CCXT → CoinGecko"""
    sources = []

    def _try(name, val):
        if val and isinstance(val, (int, float)) and val > 0:
            sources.append(name)
            return float(val)
        return None

    spot = _try("BinanceSpot", get_spot_price_binance(currency))
    if spot: return spot

    spot = _try("DeribitIndex", get_spot_price_deribit(currency))
    if spot: return spot

    try:
        import ccxt
        sym_map = {"BTC": "BTC/USDT", "ETH": "ETH/USDT"}
        ex = ccxt.binance() if currency in ("BTC","ETH") else ccxt.deribit()
        t = ex.fetch_ticker(sym_map.get(currency, f"{currency}/USDT"))
        spot = _try("CCXT", t.get('last') if t else None)
        if spot: return spot
    except Exception as e:
        print(f"[WARN] All spot price sources failed for {currency}: tried {sources}. Last error: {e}")

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
                        if spot: return spot
                    elif currency.lower() in d:
                        spot = _try("CoinGecko", d[currency.lower()].get("usd"))
            except Exception as e:
                print(f"[ERROR] spot_price.py: {e}", file=sys.stderr)
                continue
                continue
    except Exception as e:
        print(f"[WARN] Fallback oracle failed: {e}")

    return 0.0
