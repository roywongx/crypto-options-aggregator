import sqlite3
from datetime import datetime, timedelta

db_path = 'data/monitor.db'
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

currency = 'BTC'
days = 7
since = datetime.utcnow() - timedelta(days=days)

cursor.execute("""
    SELECT direction, option_type, SUM(volume) as total_volume, COUNT(*) as trade_count
    FROM large_trades_history
    WHERE currency = ? AND timestamp >= ?
    GROUP BY direction, option_type
""", (currency, since.strftime('%Y-%m-%d %H:%M:%S')))
grouped = cursor.fetchall()
conn.close()

print(f"Rows returned: {len(grouped)}")
for row in grouped:
    print(f"  direction={row['direction']!r}, ot={row['option_type']!r}, count={row['trade_count']}")

summary_data = {k: 0 for k in ['buy_put', 'sell_call', 'buy_call', 'sell_put', 'total']}
for row in grouped:
    direction = row['direction'] or ''
    ot = (row['option_type'] or 'P').upper()
    count = row['trade_count'] or 0
    summary_data['total'] += count
    if direction == 'buy' and ot == 'P':
        summary_data['buy_put'] = count
    elif direction == 'sell' and ot == 'C':
        summary_data['sell_call'] = count
    elif direction == 'buy' and ot == 'C':
        summary_data['buy_call'] = count
    elif direction == 'sell' and ot == 'P':
        summary_data['sell_put'] = count

print(f"Summary: {summary_data}")