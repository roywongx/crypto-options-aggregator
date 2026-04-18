import requests
import json
import time

start = time.time()
r = requests.get('http://localhost:8000/api/risk/overview?currency=BTC')
elapsed = time.time() - start

d = r.json()
dm = d.get('derivative_metrics', {})

print('=== 衍生品市场过热检测 v12.0 ===')
print(f'HTTP {r.status_code} | Time: {elapsed:.2f}s\n')

print('Sharpe Ratio:')
print(f"  7d:  {dm.get('sharpe_ratio_7d')} ({dm.get('sharpe_signal_7d')})")
print(f"  30d: {dm.get('sharpe_ratio_30d')} ({dm.get('sharpe_signal_30d')})")

fr = dm.get('funding_rate')
if fr is not None:
    print(f"\n资金费率: {fr*100:.3f}% ({dm.get('funding_signal')})")
else:
    print(f"\n资金费率: --")

print(f"\n期货/现货比率: {dm.get('futures_spot_ratio')} ({dm.get('futures_spot_signal')})")

oa = dm.get('overheating_assessment', {})
print(f'\n衍生品市场评估:')
print(f"  状态: {oa.get('icon')} {oa.get('name')} (分数: {oa.get('score')})")
print(f"  建议: {oa.get('advice')}")
print(f"  信号:")
for sig in oa.get('signals', []):
    print(f"    {sig[0]} {sig[1]} [{sig[2]}]")
