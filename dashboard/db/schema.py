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
    top_contracts_data TEXT,
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

SCHEMA_DVOL_HISTORY = """
CREATE TABLE IF NOT EXISTS dvol_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    currency TEXT NOT NULL,
    current REAL DEFAULT 0,
    z_score REAL DEFAULT 0,
    signal TEXT DEFAULT '',
    trend TEXT DEFAULT ''
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_trades_currency ON large_trades_history(currency)",
    "CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON large_trades_history(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_trades_strike ON large_trades_history(strike)",
    "CREATE INDEX IF NOT EXISTS idx_trades_currency_timestamp ON large_trades_history(currency, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_trades_currency_timestamp_notional ON large_trades_history(currency, timestamp DESC, notional_usd DESC)",
    "CREATE INDEX IF NOT EXISTS idx_dvol_currency ON dvol_history(currency)",
    "CREATE INDEX IF NOT EXISTS idx_dvol_timestamp ON dvol_history(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_scan_currency_timestamp ON scan_records(currency, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_scan_timestamp ON scan_records(timestamp DESC)",
]

# JSON1 虚拟列索引（用于 contracts_data/top_contracts_data 的快速查询）
JSON_INDEXES = [
    # 提取 top_contracts_data 中第一个合约的 APR 作为虚拟列
    "ALTER TABLE scan_records ADD COLUMN IF NOT EXISTS top_apr REAL GENERATED ALWAYS AS (COALESCE(json_extract(top_contracts_data, '$[0].apr'), 0)) VIRTUAL",
    "CREATE INDEX IF NOT EXISTS idx_scan_top_apr ON scan_records(top_apr)",
    # 提取 contracts_data 长度作为虚拟列
    "ALTER TABLE scan_records ADD COLUMN IF NOT EXISTS contracts_count INTEGER GENERATED ALWAYS AS (COALESCE(json_array_length(contracts_data), 0)) VIRTUAL",
    "CREATE INDEX IF NOT EXISTS idx_scan_contracts_count ON scan_records(contracts_count)",
]

SCAN_RECORDS_COLUMNS = ['dvol_signal', 'large_trades_details', 'contracts_data', 'top_contracts_data', 'raw_output']
TRADE_HISTORY_COLUMNS = ['flow_label', 'notional_usd', 'delta', 'instrument_name', 'premium_usd', 'severity']

def init_database_schema(conn: sqlite3.Connection):
    """Initialize database schema"""
    cursor = conn.cursor()

    cursor.execute(SCHEMA_SCAN_RECORDS)
    cursor.execute(SCHEMA_LARGE_TRADES_HISTORY)
    cursor.execute(SCHEMA_DVOL_HISTORY)

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
            cursor.execute(f"ALTER TABLE large_trades_history ADD COLUMN {col} {'REAL' if col in ('notional_usd','delta','premium_usd') else 'TEXT'}")

    # 创建 JSON1 虚拟列索引（SQLite ALTER TABLE 不支持 IF NOT EXISTS）
    # 使用 try/except 处理已存在的列，因为 PRAGMA table_info 可能不显示虚拟列
    try:
        cursor.execute(
            "ALTER TABLE scan_records ADD COLUMN top_apr REAL GENERATED ALWAYS AS (COALESCE(json_extract(top_contracts_data, '$[0].apr'), 0)) VIRTUAL"
        )
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scan_top_apr ON scan_records(top_apr)")

    try:
        cursor.execute(
            "ALTER TABLE scan_records ADD COLUMN contracts_count INTEGER GENERATED ALWAYS AS (COALESCE(json_array_length(contracts_data), 0)) VIRTUAL"
        )
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scan_contracts_count ON scan_records(contracts_count)")

    conn.commit()


def ensure_top_contracts_column(conn):
    """确保 top_contracts_data 字段存在（兼容旧数据库）"""
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT top_contracts_data FROM scan_records LIMIT 1")
    except sqlite3.OperationalError:
        # Column doesn't exist, add it
        try:
            cursor.execute("ALTER TABLE scan_records ADD COLUMN top_contracts_data TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass
