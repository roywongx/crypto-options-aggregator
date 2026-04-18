import requests
import json

r = requests.get('http://localhost:8000/api/risk/overview?currency=BTC')
d = r.json()
oc = d.get('onchain_metrics', {})

print('=== BTC 筑底信号 v2.0 ===')
print(f"MVRV: {oc.get('mvrv_ratio')} ({oc.get('mvrv_signal')})")
print(f"Z-Score: {oc.get('mvrv_z_score')} ({oc.get('mvrv_z_signal')})")
print(f"NUPL: {oc.get('nupl')} ({oc.get('nupl_signal')})")
print(f"Mayer: {oc.get('mayer_multiple')} ({oc.get('mayer_signal')})")
print(f"200WMA: ${oc.get('price_200wma'):,.0f}")
print(f"200DMA: ${oc.get('price_200dma'):,.0f}")
print(f"Balanced: ${oc.get('balanced_price'):,.0f}")
print(f"Halving: {oc.get('halving_days_remaining')} days")

cs = oc.get('convergence_score', {})
print(f'\n=== 汇合评分 ===')
print(f"Level: {cs.get('icon')} {cs.get('name')}")
print(f"Score: {cs.get('score')}")
print(f"Bottom Prob: {cs.get('bottom_probability')}")
print(f"Active: {cs.get('active_indicators')}/7")
print(f"Signals: {len(cs.get('signals', []))}")
