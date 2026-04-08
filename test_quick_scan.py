import urllib.request
import json
req = urllib.request.Request('http://localhost:8080/api/quick-scan', data=b'{}', headers={'Content-Type': 'application/json'}, method='POST')
try:
    with urllib.request.urlopen(req) as response:
        res = json.loads(response.read().decode())
        print(f"Success: {res.get('success')}, Count: {res.get('contracts_count')}, Spots: {res.get('spot_price')}")
        if res.get('contracts'):
            print(f"Platform: {res['contracts'][0]['platform']}")
        else:
            print("No contracts returned.")
            print(f"Error: {res.get('error')}")
except Exception as e:
    print(f'Error: {e}')
