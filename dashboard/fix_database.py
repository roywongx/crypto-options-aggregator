"""
修复数据库表结构
"""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "monitor.db"

def fix_database():
    """删除旧表，重新创建"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 删除旧表
    cursor.execute("DROP TABLE IF EXISTS scan_records")
    
    # 创建新表
    cursor.execute("""
        CREATE TABLE scan_records (
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
    """)
    
    conn.commit()
    conn.close()
    print("数据库表结构已修复！")

if __name__ == "__main__":
    fix_database()
