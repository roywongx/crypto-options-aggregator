import sqlite3, json
conn = sqlite3.connect('data/monitor.db')
cursor = conn.cursor()
cursor.execute('SELECT timestamp, large_trades_details FROM scan_records ORDER BY timestamp DESC LIMIT 1')
row = cursor.fetchone()
if row:
    ltd = row[1]
    if ltd:
        trades = json.loads(ltd)
        print(f'Total: {len(trades)}')
        for t in trades[:5]:
            print(f"  {t.get('option_type')} vol={t.get('volume')} notional={t.get('notional_usd')}")
    else:
        print('No ltd data')
else:
    print('No scans')
conn.close()