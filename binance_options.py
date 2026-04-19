import requests
import json
import time
import argparse
import sys
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ExchangeInfo 缓存（1小时TTL）
_exchange_info_cache = None
_exchange_info_cache_time = 0
_EXCHANGE_INFO_TTL = 3600  # 1小时

def get_session():
    session = requests.Session()
    retry = Retry(connect=1, read=1, backoff_factor=0.1, status_forcelist=[ 500, 502, 503, 504 ])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def get_exchange_info_cached(session):
    """获取带缓存的 ExchangeInfo，避免每次扫描都请求 1MB+ 数据"""
    global _exchange_info_cache, _exchange_info_cache_time
    now = time.time()
    if _exchange_info_cache and (now - _exchange_info_cache_time) < _EXCHANGE_INFO_TTL:
        return _exchange_info_cache
    try:
        r = session.get('https://eapi.binance.com/eapi/v1/exchangeInfo', timeout=10)
        r.raise_for_status()
        _exchange_info_cache = r.json()
        _exchange_info_cache_time = now
        return _exchange_info_cache
    except Exception:
        return _exchange_info_cache or {}

def calc_liquidity_score(volume, spread_pct):
    vol_score = min(50, (volume / 1000) * 50)
    spread_score = max(0, 50 - (spread_pct * 10))
    return int(vol_score + spread_score)

def _fetch_single_oi(session, currency, exp_yymmdd, base_url):
    """获取单个到期日的OI数据"""
    try:
        r = session.get(base_url, params={
            'underlyingAsset': currency,
            'expiration': exp_yymmdd
        }, timeout=15).json()
        if isinstance(r, list):
            result = {}
            for item in r:
                sym = item.get('symbol', '')
                oi_val = float(item.get('sumOpenInterest', 0))
                result[sym] = round(oi_val, 2)
            return result
    except Exception:
        pass
    return {}

def fetch_oi_map_parallel(session, currency, expirations, max_workers=5):
    """并行获取所有到期日的OI数据，替代顺序请求"""
    oi_map = {}
    base_url = 'https://eapi.binance.com/eapi/v1/openInterest'
    
    with ThreadPoolExecutor(max_workers=min(max_workers, len(expirations))) as executor:
        futures = {
            executor.submit(_fetch_single_oi, session, currency, exp, base_url): exp
            for exp in expirations
        }
        for future in as_completed(futures):
            result = future.result()
            oi_map.update(result)
    
    return oi_map

def ms_to_yymmdd(ms_timestamp):
    dt = datetime.fromtimestamp(ms_timestamp / 1000, tz=timezone.utc)
    return dt.strftime('%y%m%d')

def fetch_binance_options(currency, min_dte, max_dte, max_delta, strike=None, strike_range=None, min_vol=0, max_spread=20.0, margin_ratio=0.2, option_type='PUT', return_results=True):
    """获取Binance期权数据（优化版：O(1)查找 + 并行OI + 缓存ExchangeInfo）
    
    Args:
        return_results: True时返回结果列表，False时仅打印（兼容旧版调用）
    """
    try:
        session = get_session()
        
        # 使用缓存的 ExchangeInfo（1小时TTL）
        r_info = get_exchange_info_cached(session)
        if not r_info or 'optionSymbols' not in r_info:
            output = {"error": "ExchangeInfo unavailable"}
            if return_results:
                return output
            print(json.dumps(output))
            return
        
        # 并行请求 mark 和 ticker
        with ThreadPoolExecutor(max_workers=2) as executor:
            f_mark = executor.submit(session.get, 'https://eapi.binance.com/eapi/v1/mark', timeout=10)
            f_ticker = executor.submit(session.get, 'https://eapi.binance.com/eapi/v1/ticker', timeout=10)
            r_mark_resp = f_mark.result()
            r_ticker_resp = f_ticker.result()
        
        r_mark = r_mark_resp.json()
        r_ticker = r_ticker_resp.json()

        # 关键优化：将 r_mark 和 r_ticker 转换为字典，O(1) 查找替代 O(N)
        mark_dict = {m['symbol']: m for m in r_mark if 'symbol' in m}
        ticker_dict = {t['symbol']: t for t in r_ticker if 'symbol' in t}

        now = time.time() * 1000
        results = []
        underlying = f"{currency}USDT"
        target_side = option_type.upper()
        expirations_needed = set()

        for s in r_info['optionSymbols']:
            if s['underlying'] != underlying or s['side'] != target_side or s['expiryDate'] <= now:
                continue
                
            dte = (s['expiryDate'] - now) / 86400000
            if not (min_dte <= dte <= max_dte):
                continue

            strike_val = float(s['strikePrice'])
            if strike is not None and abs(strike_val - float(strike)) > 1.0:
                continue
            if strike_range:
                s_min, s_max = map(float, strike_range.split('-'))
                if not (s_min <= strike_val <= s_max):
                    continue

            # O(1) 字典查找，替代 O(N) 线性搜索
            mark = mark_dict.get(s['symbol'])
            if not mark or float(mark.get('markPrice', 0)) <= 0:
                continue

            delta = float(mark.get('delta', 0))
            if abs(delta) > max_delta:
                continue

            # O(1) 字典查找
            ticker = ticker_dict.get(s['symbol'])
            volume = float(ticker.get('volume', 0)) if ticker else 0
            bid = float(ticker.get('bidPrice', 0)) if ticker else 0
            ask = float(ticker.get('askPrice', 0)) if ticker else 0

            if volume < min_vol: continue

            spread_pct = 100.0
            if bid > 0:
                spread_pct = ((ask - bid) / bid) * 100

            if spread_pct > max_spread: continue

            premium = float(mark['markPrice'])
            margin_required = strike_val * margin_ratio
            apr = (premium / margin_required) * (365 / dte) * 100
            trad_apr = (premium / strike_val) * (365 / dte) * 100

            breakeven = strike_val - premium if target_side == 'PUT' else strike_val + premium

            liq_score = calc_liquidity_score(volume, spread_pct)

            expirations_needed.add(ms_to_yymmdd(s['expiryDate']))

            results.append({
                'symbol': s['symbol'],
                'strike': strike_val,
                'dte': round(dte, 1),
                'premium_usdt': round(premium, 2),
                'delta': round(delta, 4),
                'gamma': round(float(mark.get('gamma', 0)), 6),
                'theta': round(float(mark.get('theta', 0)), 4),
                'vega': round(float(mark.get('vega', 0)), 2),
                'mark_iv': round(float(mark.get('markIV', mark.get('markIv', mark.get('impliedVolatility', mark.get('iv', 0))))), 4),
                'oi': 0,
                'spread_pct': round(spread_pct, 2),
                'liquidity_score': liq_score,
                'apr': round(apr, 2),
                'trad_apr_ref': round(trad_apr, 2),
                'breakeven': round(breakeven, 2)
            })

        # 并行获取所有到期日的OI数据
        if expirations_needed:
            oi_map = fetch_oi_map_parallel(session, currency, expirations_needed)
            for r in results:
                r['oi'] = oi_map.get(r['symbol'], 0)

        results.sort(key=lambda x: (x['liquidity_score'], x['apr']), reverse=True)
        
        if return_results:
            return results[:20]
        print(json.dumps(results[:20], indent=2, ensure_ascii=False))
    except Exception as e:
        import traceback
        error_output = {"error": str(e), "trace": traceback.format_exc()}
        if return_results:
            return error_output
        print(json.dumps(error_output))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--currency', default='BTC')
    parser.add_argument('--min-dte', type=int, default=5)
    parser.add_argument('--max-dte', type=int, default=45)
    parser.add_argument('--max-delta', type=float, default=0.6)
    parser.add_argument('--strike', type=float)
    parser.add_argument('--strike-range')
    parser.add_argument('--min-oi', type=int, default=0)
    parser.add_argument('--max-spread', type=float, default=20.0)
    parser.add_argument('--margin-ratio', type=float, default=0.2)
    parser.add_argument('--option-type', type=str, default='PUT', choices=['PUT', 'CALL'])
    args = parser.parse_args()

    fetch_binance_options(
        args.currency, args.min_dte, args.max_dte, args.max_delta,
        args.strike, args.strike_range, args.min_oi, args.max_spread, args.margin_ratio, args.option_type
    )
