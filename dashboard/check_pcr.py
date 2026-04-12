import sqlite3, json
conn = sqlite3.connect('data/monitor.db')
cursor = conn.cursor()
cursor.execute("""
    SELECT timestamp, large_trades_details FROM scan_records
    WHERE large_trades_details IS NOT NULL AND large_trades_details != ''
    ORDER BY timestamp DESC LIMIT 10
""")
rows = cursor.fetchall()
print(f'Rows with ltd: {len(rows)}')
for row in rows[:3]:
    try:
        trades = json.loads(row[1])
        puts = sum(1 for t in trades if (t.get('option_type') or 'PUT').upper() == 'PUT')
        calls = sum(1 for t in trades if (t.get('option_type') or 'CALL').upper() == 'CALL')
        print(f"{row[0]}: {len(trades)} trades PUT={puts} CALL={calls}")
    except Exception as e:
        print(f"{row[0]}: error - {e}")
conn.close()
