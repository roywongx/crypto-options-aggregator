"""
Grid Manager - 网格持仓管理服务
管理网格策略的创建、查询、调整和关闭
"""
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from db.connection import execute_read, execute_write, execute_transaction

logger = logging.getLogger(__name__)

_grid_table_initialized = False


def _ensure_grid_table():
    """确保 grid_positions 表存在"""
    global _grid_table_initialized
    if _grid_table_initialized:
        return
    try:
        execute_write("""
            CREATE TABLE IF NOT EXISTS grid_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                currency TEXT NOT NULL DEFAULT 'BTC',
                direction TEXT NOT NULL,
                strike REAL NOT NULL,
                expiry TEXT DEFAULT '',
                margin_ratio REAL DEFAULT 0.2,
                grid_count INTEGER DEFAULT 4,
                grid_range_pct REAL DEFAULT 0.15,
                total_capital REAL DEFAULT 0,
                status TEXT DEFAULT 'active',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                closed_at DATETIME,
                close_reason TEXT DEFAULT '',
                config_json TEXT DEFAULT '{}',
                notes TEXT DEFAULT ''
            )
        """)
        execute_write("""
            CREATE INDEX IF NOT EXISTS idx_grid_currency_status 
            ON grid_positions(currency, status)
        """)
        _grid_table_initialized = True
    except (RuntimeError, ValueError, TypeError) as e:
        logger.warning("Grid table init failed: %s", e)


class GridManager:
    """网格持仓管理器"""

    def __init__(self):
        _ensure_grid_table()

    def list_positions(self, currency: str = "BTC") -> Dict[str, Any]:
        """获取网格持仓列表"""
        rows = execute_read("""
            SELECT id, currency, direction, strike, expiry, margin_ratio,
                   grid_count, grid_range_pct, total_capital, status,
                   created_at, updated_at, notes
            FROM grid_positions
            WHERE currency = ? AND status = 'active'
            ORDER BY created_at DESC
        """, (currency,))

        positions = []
        for row in rows:
            positions.append({
                "id": row[0],
                "currency": row[1],
                "direction": row[2],
                "strike": row[3],
                "expiry": row[4],
                "margin_ratio": row[5],
                "grid_count": row[6],
                "grid_range_pct": row[7],
                "total_capital": row[8],
                "status": row[9],
                "created_at": row[10],
                "updated_at": row[11],
                "notes": row[12],
            })

        return {
            "success": True,
            "currency": currency,
            "count": len(positions),
            "positions": positions
        }

    def get_position_detail(self, position_id: int) -> Optional[Dict[str, Any]]:
        """获取网格详情"""
        rows = execute_read("""
            SELECT id, currency, direction, strike, expiry, margin_ratio,
                   grid_count, grid_range_pct, total_capital, status,
                   created_at, updated_at, closed_at, close_reason,
                   config_json, notes
            FROM grid_positions WHERE id = ?
        """, (position_id,))

        if not rows:
            return None

        row = rows[0]
        return {
            "id": row[0],
            "currency": row[1],
            "direction": row[2],
            "strike": row[3],
            "expiry": row[4],
            "margin_ratio": row[5],
            "grid_count": row[6],
            "grid_range_pct": row[7],
            "total_capital": row[8],
            "status": row[9],
            "created_at": row[10],
            "updated_at": row[11],
            "closed_at": row[12],
            "close_reason": row[13],
            "config": json.loads(row[14]) if row[14] else {},
            "notes": row[15],
        }

    def adjust_position(
        self, position_id: int, new_strike: float,
        new_expiry: str = "", reason: str = ""
    ) -> Dict[str, Any]:
        """调整网格参数（滚仓）"""
        detail = self.get_position_detail(position_id)
        if not detail:
            return {"error": "网格持仓不存在"}

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        execute_write("""
            UPDATE grid_positions 
            SET strike = ?, expiry = ?, updated_at = ?, notes = ?
            WHERE id = ?
        """, (new_strike, new_expiry or detail["expiry"], now, reason, position_id))

        return {
            "success": True,
            "position_id": position_id,
            "old_strike": detail["strike"],
            "new_strike": new_strike,
            "message": f"网格已调整: {detail['strike']} -> {new_strike}"
        }

    def close_position(self, position_id: int, close_reason: str = "manual") -> Dict[str, Any]:
        """关闭网格持仓"""
        detail = self.get_position_detail(position_id)
        if not detail:
            return {"error": "网格持仓不存在"}

        if detail["status"] != "active":
            return {"error": f"网格已处于 {detail['status']} 状态"}

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        execute_write("""
            UPDATE grid_positions
            SET status = 'closed', closed_at = ?, close_reason = ?, updated_at = ?
            WHERE id = ?
        """, (now, close_reason, now, position_id))

        return {
            "success": True,
            "position_id": position_id,
            "message": f"网格已关闭: {close_reason}"
        }

    def create_position(
        self, currency: str, direction: str, strike: float,
        expiry: str = "", margin_ratio: float = 0.2,
        grid_count: int = 4, grid_range_pct: float = 0.15,
        total_capital: float = 0, config: dict = None
    ) -> Dict[str, Any]:
        """创建网格持仓"""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        config_json = json.dumps(config or {}, ensure_ascii=False)

        row_id = execute_write("""
            INSERT INTO grid_positions
            (currency, direction, strike, expiry, margin_ratio, grid_count,
             grid_range_pct, total_capital, status, created_at, updated_at, config_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        """, (currency, direction, strike, expiry, margin_ratio, grid_count,
              grid_range_pct, total_capital, now, now, config_json))

        return {
            "success": True,
            "position_id": row_id,
            "message": f"网格已创建: {direction} @ {strike}"
        }
