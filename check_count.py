import urllib.request
import json
req = urllib.request.Request('http://localhost:8080/api/quick-scan', data=b'{"max_delta": 0.8, "min_dte": 1}', headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(req) as response:
    res = json.loads(response.read().decode())
    print(res.get('contracts_count'))
