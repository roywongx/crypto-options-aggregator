# Database connection management
import sqlite3
import threading
import time
import logging
from pathlib import Path
from typing import Optional, Any, Callable, TypeVar

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "monitor.db"
DB_PATH.parent.mkdir(exist_ok=True)

# 写入锁：序列化所有写操作，避免 database is locked
_write_lock = threading.Lock()

# 读连接：每个线程独立的只读连接
_read_local = threading.local()

def get_db_connection(read_only: bool = False) -> sqlite3.Connection:
    """获取数据库连接（线程安全，支持读写分离）
    
    对于读取操作：使用线程本地缓存连接（只读模式）
    对于写入操作：返回新连接，调用方需持有 _write_lock
    
    WAL 模式 + busy_timeout=60s 支持高并发读取。
    """
    if read_only:
        conn = getattr(_read_local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(
                f"file:{DB_PATH}?mode=ro", 
                uri=True,
                timeout=60.0
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=60000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.row_factory = sqlite3.Row
            _read_local.conn = conn
        return conn
    else:
        # 写操作返回新连接，由调用方通过 _write_lock 序列化
        conn = sqlite3.connect(DB_PATH, timeout=60.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        conn.row_factory = sqlite3.Row
        return conn

def execute_read(query: str, params: tuple = ()) -> list:
    """执行只读查询（自动获取/释放连接）"""
    conn = get_db_connection(read_only=True)
    cursor = conn.cursor()
    try:
        cursor.execute(query, params)
        return cursor.fetchall()
    except sqlite3.OperationalError as e:
        logger.warning("read query failed: %s", str(e))
        # 清除损坏的连接
        _read_local.conn = None
        raise
    finally:
        cursor.close()

def execute_write(query: str, params: tuple = ()) -> Any:
    """执行写操作（自动获取锁，序列化执行）"""
    with _write_lock:
        conn = get_db_connection(read_only=False)
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            conn.commit()
            return cursor.lastrowid
        except sqlite3.OperationalError as e:
            conn.rollback()
            logger.warning("write query failed: %s", str(e))
            raise
        finally:
            cursor.close()
            conn.close()

def close_db_connection():
    """关闭当前线程的数据库连接"""
    conn = getattr(_read_local, 'conn', None)
    if conn:
        try:
            conn.close()
        except Exception as e:
            logger.warning("close_db_connection error: %s", str(e))
        finally:
            _read_local.conn = None

def execute_with_retry(func: Callable, max_retries: int = 3, base_delay: float = 0.05) -> Any:
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
    for attempt in range(max_retries):
        try:
            return func()
        except sqlite3.OperationalError as e:
            if "database is locked" not in str(e).lower() or attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning("DB locked, retry %d/%d in %.2fs", attempt + 1, max_retries, delay)
            time.sleep(delay)
