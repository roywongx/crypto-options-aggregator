# Database Maintenance
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

def get_db_maintenance_stats(conn: sqlite3.Connection) -> dict:
    """获取数据库维护统计信息"""
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM scan_records")
    scan_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM large_trades_history")
    trades_count = cursor.fetchone()[0]

    cursor.execute("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()")
    db_size_bytes = cursor.fetchone()[0]

    cursor.execute("SELECT MAX(timestamp) FROM scan_records")
    last_scan = cursor.fetchone()[0]

    return {
        "scan_records_count": scan_count,
        "trades_history_count": trades_count,
        "db_size_bytes": db_size_bytes,
        "db_size_mb": round(db_size_bytes / (1024 * 1024), 2),
        "last_scan_timestamp": last_scan
    }

def cleanup_old_records(conn: sqlite3.Connection, days: int = 30) -> dict:
    """清理指定天数之前的旧记录"""
    cursor = conn.cursor()
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    cursor.execute("DELETE FROM scan_records WHERE timestamp < ?", (cutoff_date,))
    scans_deleted = cursor.rowcount

    cursor.execute("DELETE FROM large_trades_history WHERE timestamp < ?", (cutoff_date,))
    trades_deleted = cursor.fetchone()[0] if cursor.rowcount else 0

    conn.commit()

    return {
        "scans_deleted": scans_deleted,
        "trades_deleted": trades_deleted,
        "cutoff_date": cutoff_date.isoformat()
    }

def vacuum_database(conn: sqlite3.Connection) -> bool:
    """执行 VACUUM 压缩数据库"""
    try:
        conn.execute("VACUUM")
        return True
    except Exception as e:
        print(f"VACUUM failed: {e}")
        return False

def vacuum_if_needed(conn: sqlite3.Connection, threshold_mb: float = 100) -> dict:
    """如果数据库超过阈值大小，执行 VACUUM"""
    cursor = conn.cursor()
    cursor.execute("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()")
    db_size_bytes = cursor.fetchone()[0]
    db_size_mb = db_size_bytes / (1024 * 1024)

    result = {"db_size_mb": round(db_size_mb, 2), "vacuum_performed": False}

    if db_size_mb > threshold_mb:
        conn.execute("VACUUM")
        result["vacuum_performed"] = True
        result["message"] = f"Database ({db_size_mb:.1f}MB) exceeded threshold ({threshold_mb}MB), VACUUM performed"

    return result
