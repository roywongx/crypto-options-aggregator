# Dashboard db - connection management
import sqlite3
import threading
import os
from contextlib import contextmanager

_db_local = threading.local()

def get_db_path():
    base = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base, 'data', 'monitor.db')

DB_PATH = get_db_path()

def get_db_connection():
    """Thread-safe SQLite connection with WAL mode and busy timeout"""
    conn = getattr(_db_local, 'conn', None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        _db_local.conn = conn
    return conn

@contextmanager
def get_db():
    """Context manager for database operations"""
    conn = get_db_connection()
    try:
        yield conn
    finally:
        pass  # Connection managed by threading.local

def init_db():
    """Initialize database tables"""
    conn = get_db_connection()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS scan_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            currency TEXT NOT NULL,
            platform TEXT,
            total_contracts INTEGER,
            avg_apr REAL,
            best_contract TEXT,
            best_apr REAL,
            dvol_current REAL,
            spot_price REAL,
            data TEXT
        );
        CREATE TABLE IF NOT EXISTS large_trades_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            platform TEXT,
            direction TEXT,
            premium_usd REAL,
            size_usd REAL,
            strike REAL,
            expiry TEXT,
            contract_type TEXT,
            data TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_scan_time ON scan_records(scan_time);
        CREATE INDEX IF NOT EXISTS idx_trade_time ON large_trades_history(trade_time);
    ''')
    conn.commit()
