# Database connection management
import sqlite3
import threading
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "monitor.db"
DB_PATH.parent.mkdir(exist_ok=True)

_db_local = threading.local()
_db_lock = threading.Lock()

def get_db_connection() -> sqlite3.Connection:
    """Thread-safe SQLite connection with WAL mode and busy timeout
    
    使用 threading.local() 确保每个线程独立连接，
    WAL 模式 + 60s busy_timeout 支持高并发读取。
    """
    conn = getattr(_db_local, 'conn', None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=60.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        conn.row_factory = sqlite3.Row
        _db_local.conn = conn
    return conn

def close_db_connection():
    """关闭当前线程的数据库连接"""
    conn = getattr(_db_local, 'conn', None)
    if conn:
        try:
            conn.close()
        except Exception as e:
            logger.warning("close_db_connection error: %s", str(e))
        finally:
            _db_local.conn = None

def execute_with_retry(func, max_retries=3, base_delay=0.05):
    """带重试的数据库操作包装器，处理 database is locked 错误
    
    Args:
        func: 要执行的函数（无参数）
        max_retries: 最大重试次数
        base_delay: 基础退避时间（秒）
    
    Returns:
        func 的返回值
    
    Raises:
        最后一次重试仍失败时抛出原始异常
    """
    import time
    for attempt in range(max_retries):
        try:
            return func()
        except sqlite3.OperationalError as e:
            if "database is locked" not in str(e).lower() or attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning("DB locked, retry %d/%d in %.2fs", attempt + 1, max_retries, delay)
            time.sleep(delay)
