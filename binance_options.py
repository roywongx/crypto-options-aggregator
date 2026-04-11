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

def get_session():
    session = requests.Session()
    retry = Retry(connect=1, read=1, backoff_factor=0.1, status_forcelist=[ 500, 502, 503, 504 ])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def calc_liquidity_score(volume, spread_pct):
    vol_score = min(50, (volume / 1000) * 50)
    spread_score = max(0, 50 - (spread_pct * 10))
    return int(vol_score + spread_score)

def fetch_oi_map(session, currency, expirations):
    oi_map = {}
    base_url = 'https://eapi.binance.com/eapi/v1/openInterest'
    for exp_yymmdd in expirations:
        try:
            r = session.get(base_url, params={
                'underlyingAsset': currency,
                'expiration': exp_yymmdd
            }, timeout=15).json()
            if isinstance(r, list):
                for item in r:
                    sym = item.get('symbol', '')
                    oi_val = float(item.get('sumOpenInterest', 0))
                    oi_map[sym] = round(oi_val, 2)
        except Exception:
            pass
    return oi_map

def ms_to_yymmdd(ms_timestamp):
    dt = datetime.fromtimestamp(ms_timestamp / 1000, tz=timezone.utc)
    return dt.strftime('%y%m%d')

def fetch_binance_options(currency, min_dte, max_dte, max_delta, strike=None, strike_range=None, min_vol=0, max_spread=20.0, margin_ratio=0.2, option_type='PUT'):
    try:
        session = get_session()
        timeout = 3
        r_mark = session.get('https://eapi.binance.com/eapi/v1/mark', timeout=timeout).json()
        r_info = session.get('https://eapi.binance.com/eapi/v1/exchangeInfo', timeout=timeout).json()
        r_ticker = session.get('https://eapi.binance.com/eapi/v1/ticker', timeout=timeout).json()

        now = time.time() * 1000
        results = []
        underlying = f"{currency}USDT"
        target_side = option_type.upper()
        expirations_needed = set()

        for s in r_info['optionSymbols']:
            if s['underlying'] == underlying and s['side'] == target_side and s['expiryDate'] > now:
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

                mark = next((m for m in r_mark if m['symbol'] == s['symbol']), None)
                if not mark or float(mark['markPrice']) <= 0:
                    continue

                delta = float(mark['delta'])
                if abs(delta) > max_delta:
                    continue

                ticker = next((t for t in r_ticker if t['symbol'] == s['symbol']), None)
                volume = float(ticker['volume']) if ticker else 0
                bid = float(ticker['bidPrice']) if ticker else 0
                ask = float(ticker['askPrice']) if ticker else 0

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
                    'gamma': round(float(mark['gamma']), 6),
                    'theta': round(float(mark.get('theta', 0)), 4),
                    'vega': round(float(mark['vega']), 2),
                    'mark_iv': round(float(mark.get('markIV', mark.get('markIv', mark.get('impliedVolatility', mark.get('iv', 0))))), 4),
                    'oi': 0,
                    'spread_pct': round(spread_pct, 2),
                    'liquidity_score': liq_score,
                    'apr': round(apr, 2),
                    'trad_apr_ref': round(trad_apr, 2),
                    'breakeven': round(breakeven, 2)
                })

        oi_map = fetch_oi_map(session, currency, expirations_needed)

        for r in results:
            r['oi'] = oi_map.get(r['symbol'], 0)

        results.sort(key=lambda x: (x['liquidity_score'], x['apr']), reverse=True)
        print(json.dumps(results[:20], indent=2, ensure_ascii=False))
    except Exception as e:
        import traceback
        print(json.dumps({"error": str(e), "trace": traceback.format_exc()}))

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
