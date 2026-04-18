import requests
import json
import time

start = time.time()
r = requests.get('http://localhost:8000/api/risk/overview?currency=BTC')
elapsed = time.time() - start

d = r.json()
oc = d.get('onchain_metrics', {})

print('=== BTC 筑底信号 v3.0 (Bitcoin Magazine Pro 标准) ===')
print(f'HTTP {r.status_code} | Time: {elapsed:.2f}s\n')

print('链上指标:')
print(f"  MVRV: {oc.get('mvrv_ratio')} ({oc.get('mvrv_signal')})")
print(f"  Z-Score: {oc.get('mvrv_z_score')} | 区域: {oc.get('mvrv_z_zone_name')}")
print(f"  Z-Score 历史极值: min={oc.get('mvrv_z_extremes', {}).get('min_z')} ~ max={oc.get('mvrv_z_extremes', {}).get('max_z')}")
print(f"  NUPL: {oc.get('nupl')} ({oc.get('nupl_signal')})")
print(f"  Mayer: {oc.get('mayer_multiple')} ({oc.get('mayer_signal')})")
print(f"  Puell: {oc.get('puell_multiple')} ({oc.get('puell_signal')})")
print(f"  200WMA: ${oc.get('price_200wma'):,.0f}")
print(f"  200DMA: ${oc.get('price_200dma'):,.0f}")
print(f"  Balanced: ${oc.get('balanced_price'):,.0f}")
print(f"  Halving: {oc.get('halving_days_remaining')} days")

cs = oc.get('convergence_score', {})
print(f'\n汇合评分 (8指标):')
print(f"  Level: {cs.get('icon')} {cs.get('name')}")
print(f"  Score: {cs.get('score')} / {cs.get('max_score')}")
print(f"  Bottom Prob: {cs.get('bottom_probability')}")
print(f"  Active: {cs.get('active_indicators')}/8")
print(f"  Signals:")
for sig in cs.get('signals', []):
    print(f"    {sig[0]} {sig[1]} [{sig[2]}]")
