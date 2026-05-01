import sys, traceback, asyncio
sys.path.insert(0, 'dashboard')
from dashboard.main import quick_scan, QuickScanParams
async def main():
    try:
        await quick_scan(QuickScanParams(currency='BTC'))
    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
        with open('traceback.txt', 'w') as f:
            traceback.print_exc(file=f)
asyncio.run(main())
