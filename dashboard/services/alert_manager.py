"""
预警管理器
提供持久化、分类、统计的预警系统
"""
import json
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from enum import Enum


class AlertLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertType(Enum):
    PRICE = "price"
    VOLATILITY = "volatility"
    POSITION = "position"
    RISK = "risk"
    SYSTEM = "system"


class AlertManager:
    def __init__(self, db_path: str = "data/alerts.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """初始化数据库"""
        import os
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                level TEXT NOT NULL,
                type TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT,
                acknowledged BOOLEAN DEFAULT FALSE,
                action_taken TEXT,
                resolved BOOLEAN DEFAULT FALSE
            )
        """)

        conn.commit()
        conn.close()

    def create_alert(self, level: AlertLevel, alert_type: AlertType,
                    message: str, details: Dict = None) -> int:
        """创建新预警"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO alerts (level, type, message, details)
            VALUES (?, ?, ?, ?)
        """, (level.value, alert_type.value, message,
              json.dumps(details) if details else None))

        alert_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return alert_id

    def get_alerts(self, level: str = None, alert_type: str = None,
                  hours: int = 24, limit: int = 100) -> List[Dict]:
        """获取预警列表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        query = """
            SELECT id, timestamp, level, type, message, details,
                   acknowledged, action_taken, resolved
            FROM alerts
            WHERE timestamp > datetime('now', ? || ' hours')
        """
        params = [str(-hours)]

        if level:
            query += " AND level = ?"
            params.append(level)

        if alert_type:
            query += " AND type = ?"
            params.append(alert_type)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(str(limit))

        cursor.execute(query, params)
        rows = cursor.fetchall()

        alerts = []
        for row in rows:
            alerts.append({
                "id": row[0],
                "timestamp": row[1],
                "level": row[2],
                "type": row[3],
                "message": row[4],
                "details": json.loads(row[5]) if row[5] else None,
                "acknowledged": bool(row[6]),
                "action_taken": row[7],
                "resolved": bool(row[8])
            })

        conn.close()
        return alerts

    def acknowledge_alert(self, alert_id: int, action_taken: str = None):
        """确认预警"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE alerts
            SET acknowledged = TRUE, action_taken = ?
            WHERE id = ?
        """, (action_taken, alert_id))

        conn.commit()
        conn.close()

    def get_alert_stats(self, hours: int = 24) -> Dict[str, Any]:
        """获取预警统计"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 按级别统计
        cursor.execute("""
            SELECT level, COUNT(*) as count
            FROM alerts
            WHERE timestamp > datetime('now', ? || ' hours')
            GROUP BY level
        """, (str(-hours),))

        level_stats = {row[0]: row[1] for row in cursor.fetchall()}

        # 按类型统计
        cursor.execute("""
            SELECT type, COUNT(*) as count
            FROM alerts
            WHERE timestamp > datetime('now', ? || ' hours')
            GROUP BY type
        """, (str(-hours),))

        type_stats = {row[0]: row[1] for row in cursor.fetchall()}

        # 总数
        cursor.execute("""
            SELECT COUNT(*) as total
            FROM alerts
            WHERE timestamp > datetime('now', ? || ' hours')
        """, (str(-hours),))

        total = cursor.fetchone()[0]

        conn.close()

        return {
            "total": total,
            "by_level": level_stats,
            "by_type": type_stats,
            "hours": hours
        }


# 全局实例
_alert_manager = None

def get_alert_manager() -> AlertManager:
    """获取预警管理器实例"""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
    return _alert_manager
