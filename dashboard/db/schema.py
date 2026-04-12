# Database schema definitions
import sqlite3

SCHEMA_SCAN_RECORDS = """
CREATE TABLE IF NOT EXISTS scan_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    currency TEXT,
    spot_price REAL,
    dvol_current REAL,
    dvol_z_score REAL,
    dvol_signal TEXT,
    large_trades_count INTEGER,
    large_trades_details TEXT,
    contracts_data TEXT,
    raw_output TEXT
)
"""

SCHEMA_LARGE_TRADES_HISTORY = """
CREATE TABLE IF NOT EXISTS large_trades_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    currency TEXT NOT NULL,
    source TEXT,
    title TEXT,
    message TEXT,
    direction TEXT DEFAULT 'unknown',
    strike REAL,
    volume REAL DEFAULT 0,
    option_type TEXT,
    flow_label TEXT DEFAULT '',
    notional_usd REAL DEFAULT 0,
    delta REAL DEFAULT 0,
    instrument_name TEXT DEFAULT ''
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_trades_currency ON large_trades_history(currency)",
    "CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON large_trades_history(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_trades_strike ON large_trades_history(strike)",
]

SCAN_RECORDS_COLUMNS = ['dvol_signal', 'large_trades_details', 'contracts_data', 'raw_output']
TRADE_HISTORY_COLUMNS = ['flow_label', 'notional_usd', 'delta', 'instrument_name']

def init_database_schema(conn: sqlite3.Connection):
    """Initialize database schema"""
    cursor = conn.cursor()

    cursor.execute(SCHEMA_SCAN_RECORDS)
    cursor.execute(SCHEMA_LARGE_TRADES_HISTORY)

    for idx in INDEXES:
        cursor.execute(idx)

    cursor.execute("PRAGMA table_info(scan_records)")
    columns = [col[1] for col in cursor.fetchall()]
    for col in SCAN_RECORDS_COLUMNS:
        if col not in columns:
            cursor.execute(f"ALTER TABLE scan_records ADD COLUMN {col} TEXT")

    cursor.execute("PRAGMA table_info(large_trades_history)")
    trade_cols = [col[1] for col in cursor.fetchall()]
    for col in TRADE_HISTORY_COLUMNS:
        if col not in trade_cols:
            cursor.execute(f"ALTER TABLE large_trades_history ADD COLUMN {col} {'REAL' if col in ('notional_usd','delta') else 'TEXT'}")

    conn.commit()
