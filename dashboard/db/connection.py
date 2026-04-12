# Database connection management
import sqlite3
import threading
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "data" / "monitor.db"
DB_PATH.parent.mkdir(exist_ok=True)

_db_local = threading.local()

def get_db_connection() -> sqlite3.Connection:
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
