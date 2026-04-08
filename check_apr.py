import urllib.request
import json
req = urllib.request.Request('http://localhost:8080/api/quick-scan', data=b'{"max_delta": 0.4}', headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(req) as response:
    res = json.loads(response.read().decode())
    for c in res.get('contracts', [])[:5]:
        print(f"Platform: {c.get('platform')}, Sym: {c['symbol']}, DTE: {c['dte']}, Strike: {c['strike']}, Premium USD: {c['premium_usd']}, APR: {c['apr']}, Delta: {c['delta']}, Liq: {c.get('liquidity_score')}")