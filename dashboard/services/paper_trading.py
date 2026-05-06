"""
Paper Trading Engine - 连续模拟盘引擎
功能:
- 虚拟本金管理
- 模拟开仓/平仓 (Sell Put / Covered Call)
- 实时 UPnL 计算
- 保证金占用率
- 滚仓策略试算
- 历史交易记录
"""
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from db.connection import execute_read, execute_write, execute_transaction
from services.spot_price import get_spot_price
from services.quant_engine import bs_put_price, bs_call_price, bs_delta
from services.margin_calculator import calc_margin

logger = logging.getLogger(__name__)

# 并发控制：模拟盘读写-修改-写入操作必须串行化
_paper_lock = threading.Lock()

# ============================================================
# 数据库初始化
# ============================================================

def _add_column_if_missing(table: str, column: str, col_def: str):
    """安全添加列 — 忽略已存在错误（SQLite 不支持 ALTER TABLE ADD COLUMN IF NOT EXISTS）"""
    try:
        execute_write(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        logger.info("Paper Trading: added column %s to %s", column, table)
    except (sqlite3.Error, sqlite3.OperationalError):
        pass  # column already exists


def init_paper_trading_db():
    """初始化模拟盘数据库表"""
    try:
        # 持仓表
        execute_write("""
            CREATE TABLE IF NOT EXISTS paper_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                currency TEXT NOT NULL,
                option_type TEXT NOT NULL,
                strike REAL NOT NULL,
                expiry TEXT,
                qty REAL NOT NULL,
                entry_premium REAL NOT NULL,
                entry_premium_total REAL NOT NULL,
                entry_price REAL,
                margin_ratio REAL NOT NULL,
                status TEXT DEFAULT 'open'
            )
        """)

        # 交易记录表
        execute_write("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                currency TEXT NOT NULL,
                action TEXT NOT NULL,
                option_type TEXT NOT NULL,
                strike REAL NOT NULL,
                expiry TEXT,
                qty REAL NOT NULL,
                premium REAL NOT NULL,
                premium_total REAL NOT NULL,
                price REAL,
                pnl REAL DEFAULT 0,
                notes TEXT
            )
        """)

        # 账户状态表
        execute_write("""
            CREATE TABLE IF NOT EXISTS paper_account (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                initial_capital REAL NOT NULL DEFAULT 50000,
                current_cash REAL NOT NULL DEFAULT 50000,
                currency TEXT NOT NULL DEFAULT 'BTC'
            )
        """)

        # 迁移：为旧版 paper_account 添加 locked_margin 字段
        _add_column_if_missing("paper_account", "locked_margin", "REAL DEFAULT 0")

        # 检查是否需要初始化账户
        rows = execute_read("SELECT COUNT(*) FROM paper_account")
        if rows[0][0] == 0:
            execute_write(
                "INSERT INTO paper_account (id, initial_capital, current_cash, locked_margin, currency) "
                "VALUES (1, 50000, 50000, 0, 'BTC')"
            )

        logger.info("Paper Trading DB 初始化完成")
    except (OSError, IOError, RuntimeError) as e:
        logger.error("Paper Trading DB 初始化失败: %s", str(e))


# ============================================================
# 核心操作
# ============================================================

def paper_open_position(
    currency: str,
    option_type: str,
    strike: float,
    qty: float,
    premium: float,
    expiry: str = None,
    margin_ratio: float = 0.2,
    notes: str = ""
) -> Dict[str, Any]:
    """
    模拟开仓 — 三账户模型: cash + locked_margin + unrealized_pnl = total_equity

    Args:
        currency: BTC/ETH
        option_type: PUT/CALL
        strike: 行权价
        qty: 数量
        premium: 单张权利金 (USDT)
        expiry: 到期日
        margin_ratio: 保证金比率
        notes: 备注

    Returns:
        开仓结果
    """
    with _paper_lock:
        try:
            account = _get_account()
            if not account:
                return {"error": "账户未初始化"}

            premium_total = premium * qty
            margin_required = calc_margin(strike, premium, option_type)

            # 可用 = 现金（保证金已单独跟踪在 locked_margin）
            available = account["current_cash"]

            # 检查资金充足性
            if available < margin_required:
                return {
                    "error": "保证金不足",
                    "required": margin_required,
                    "available": available,
                    "locked_margin": account["locked_margin"]
                }

            # 三账户模型: 收到权利金计入 cash，保证金单独锁定
            new_cash = account["current_cash"] + premium_total - margin_required
            new_locked = account["locked_margin"] + margin_required

            execute_transaction([
                ("""
                    INSERT INTO paper_positions
                    (currency, option_type, strike, expiry, qty, entry_premium, entry_premium_total, entry_price, margin_ratio)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (currency, option_type, strike, expiry, qty, premium, premium_total, None, margin_ratio)),
                ("""
                    INSERT INTO paper_trades
                    (currency, action, option_type, strike, expiry, qty, premium, premium_total, price, pnl, notes)
                    VALUES (?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """, (currency, option_type, strike, expiry, qty, premium, premium_total, None, notes)),
                (
                    "UPDATE paper_account SET current_cash = ?, locked_margin = ? WHERE id = 1",
                    (new_cash, new_locked)
                )
            ])

            return {
                "success": True,
                "action": "OPEN",
                "currency": currency,
                "option_type": option_type,
                "strike": strike,
                "qty": qty,
                "premium": premium,
                "premium_received": premium_total,
                "margin_required": margin_required,
                "available": available,
                "new_cash": new_cash,
                "locked_margin": new_locked,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            }

        except (sqlite3.Error, sqlite3.OperationalError, RuntimeError) as e:
            logger.error("开仓失败: %s", str(e))
            return {"error": str(e)}


def paper_close_position(
    position_id: int,
    close_premium: float,
    notes: str = ""
) -> Dict[str, Any]:
    """
    模拟平仓 — 三账户模型: 释放 locked_margin，现金支付平仓成本

    Args:
        position_id: 持仓 ID
        close_premium: 平仓权利金 (单张)
        notes: 备注

    Returns:
        平仓结果
    """
    with _paper_lock:
        try:
            rows = execute_read(
                "SELECT * FROM paper_positions WHERE id = ? AND status = 'open'",
                (position_id,)
            )
            if not rows:
                return {"error": "持仓不存在或已平仓"}

            pos = {
                "id": rows[0][0], "timestamp": rows[0][1], "currency": rows[0][2],
                "option_type": rows[0][3], "strike": rows[0][4], "expiry": rows[0][5],
                "qty": rows[0][6], "entry_premium": rows[0][7],
                "entry_premium_total": rows[0][8], "entry_price": rows[0][9],
                "margin_ratio": rows[0][10], "status": rows[0][11]
            }

            close_premium_total = close_premium * pos["qty"]

            # PnL (卖方: 开仓收权利金 - 平仓付权利金)
            pnl = pos["entry_premium_total"] - close_premium_total

            # 释放保证金
            original_margin = pos["strike"] * pos["qty"] * pos["margin_ratio"]
            account = _get_account()

            # 三账户: 平仓支付从 cash 扣除，locked_margin 释放
            new_cash = account["current_cash"] - close_premium_total + original_margin
            new_locked = max(0, account["locked_margin"] - original_margin)

            execute_transaction([
                (
                    "UPDATE paper_positions SET status = 'closed' WHERE id = ?",
                    (position_id,)
                ),
                ("""
                    INSERT INTO paper_trades
                    (currency, action, option_type, strike, expiry, qty, premium, premium_total, pnl, notes)
                    VALUES (?, 'CLOSE', ?, ?, ?, ?, ?, ?, ?, ?)
                """, (pos["currency"], pos["option_type"], pos["strike"], pos["expiry"],
                      pos["qty"], close_premium, close_premium_total, pnl, notes)),
                (
                    "UPDATE paper_account SET current_cash = ?, locked_margin = ? WHERE id = 1",
                    (new_cash, new_locked)
                )
            ])

            return {
                "success": True,
                "action": "CLOSE",
                "position_id": position_id,
                "pnl": pnl,
                "new_cash": new_cash,
                "locked_margin": new_locked,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            }

        except (sqlite3.Error, sqlite3.OperationalError, RuntimeError) as e:
            logger.error("平仓失败: %s", str(e))
            return {"error": str(e)}


# ============================================================
# 查询与计算
# ============================================================

def get_portfolio_summary(currency: str = "BTC") -> Dict[str, Any]:
    """
    获取模拟盘组合概览 — 三账户模型: total_equity = cash + locked_margin + unrealized_pnl
    """
    account = _get_account()
    if not account:
        return {"error": "账户未初始化"}

    positions = _get_open_positions(currency)
    spot = get_spot_price(currency) or 0

    total_upnl = 0
    position_details = []

    for pos in positions:
        current_premium = _estimate_current_premium(pos, spot)
        upnl = pos["entry_premium"] - current_premium
        total_upnl += upnl * pos["qty"]

        margin = pos["strike"] * pos["qty"] * pos["margin_ratio"]

        position_details.append({
            "id": pos["id"],
            "currency": pos["currency"],
            "option_type": pos["option_type"],
            "strike": pos["strike"],
            "expiry": pos["expiry"],
            "qty": pos["qty"],
            "entry_premium": pos["entry_premium"],
            "current_premium": round(current_premium, 2),
            "upnl": round(upnl, 2),
            "margin": round(margin, 2),
            "dist_from_spot_pct": round((pos["strike"] - spot) / spot * 100, 1) if spot > 0 else 0
        })

    # 三账户: equity = cash + locked_margin + unrealized_pnl
    total_equity = account["current_cash"] + account["locked_margin"] + total_upnl
    pnl_pct = (total_equity - account["initial_capital"]) / account["initial_capital"] * 100

    return {
        "initial_capital": account["initial_capital"],
        "current_cash": round(account["current_cash"], 2),
        "locked_margin": round(account["locked_margin"], 2),
        "total_equity": round(total_equity, 2),
        "available": round(account["current_cash"], 2),
        "total_upnl": round(total_upnl, 2),
        "total_margin": round(account["locked_margin"], 2),
        "margin_usage_pct": round(account["locked_margin"] / account["initial_capital"] * 100, 1) if account["initial_capital"] > 0 else 0,
        "pnl_pct": round(pnl_pct, 2),
        "spot_price": spot,
        "positions_count": len(positions),
        "positions": position_details,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    }


def get_trade_history(currency: str = "BTC", limit: int = 50) -> List[Dict[str, Any]]:
    """获取历史交易记录（使用 sqlite3.Row 命名访问）"""
    rows = execute_read("""
        SELECT * FROM paper_trades
        WHERE currency = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (currency, limit))

    return [
        {
            "id": r["id"], "timestamp": r["timestamp"], "currency": r["currency"],
            "action": r["action"], "option_type": r["option_type"],
            "strike": r["strike"], "expiry": r["expiry"],
            "qty": r["qty"], "premium": r["premium"],
            "premium_total": r["premium_total"], "pnl": r["pnl"], "notes": r["notes"]
        }
        for r in rows
    ]


def get_roll_suggestion(position_id: int) -> Dict[str, Any]:
    """
    滚仓试算: 针对指定持仓，建议滚仓方案
    
    基于: DVOL + 当前价差 + 保证金优化
    """
    try:
        rows = execute_read(
            "SELECT * FROM paper_positions WHERE id = ? AND status = 'open'",
            (position_id,)
        )
        if not rows:
            return {"error": "持仓不存在或已平仓"}
        
        pos = rows[0]
        # 使用字段名映射，避免索引错位
        pos_map = {
            "id": pos[0], "timestamp": pos[1], "currency": pos[2],
            "option_type": pos[3], "strike": pos[4], "expiry": pos[5],
            "qty": pos[6], "entry_premium": pos[7],
            "entry_premium_total": pos[8], "entry_price": pos[9],
            "margin_ratio": pos[10], "status": pos[11]
        }
        spot = get_spot_price(pos_map["currency"]) or 0
        
        # 计算当前持仓的希腊字母 (简化)
        dte = _get_dte(pos_map["expiry"])  # 估算 DTE
        current_premium = _estimate_current_premium(pos_map, spot)
        
        # 建议: 滚到更低 Delta (更安全) 或更高 APR
        suggested_strike = pos_map["strike"] * 0.95  # 建议降低 5% 行权价
        
        return {
            "position": {
                "id": pos_map["id"],
                "strike": pos_map["strike"],
                "entry_premium": pos_map["entry_premium"],
                "current_premium": round(current_premium, 2)
            },
            "suggestion": {
                "new_strike": round(suggested_strike, 0),
                "reason": "降低 Delta 暴露，提高安全边际",
                "estimated_new_apr": round(current_premium * 1.2 / max(suggested_strike * 0.2, 1) * (365 / max(dte, 1)) * 100, 1) if dte > 0 else 0
            },
            "spot": spot,
            "dte": dte
        }
        
    except (sqlite3.Error, sqlite3.OperationalError, RuntimeError) as e:
        return {"error": str(e)}


# ============================================================
# 内部函数
# ============================================================

def _get_account() -> Optional[Dict[str, Any]]:
    """获取账户信息（三账户模型: id, initial_capital, current_cash, locked_margin, ...）"""
    rows = execute_read("SELECT * FROM paper_account WHERE id = 1")
    if rows:
        r = rows[0]
        return {
            "initial_capital": r[1],
            "current_cash": r[2],
            "locked_margin": r[3] if len(r) > 3 else 0,
            "currency": r[4] if len(r) > 4 else "BTC",
        }
    return None


def _get_open_positions(currency: str = "BTC") -> List[Dict[str, Any]]:
    """获取所有 open 持仓"""
    rows = execute_read(
        "SELECT * FROM paper_positions WHERE currency = ? AND status = 'open' ORDER BY timestamp ASC",
        (currency,)
    )
    return [
        {
            "id": r[0], "timestamp": r[1], "currency": r[2], "option_type": r[3],
            "strike": r[4], "expiry": r[5], "qty": r[6],
            "entry_premium": r[7], "entry_premium_total": r[8],
            "margin_ratio": r[10]
        }
        for r in rows
    ]


def _get_locked_margin() -> float:
    """计算当前所有 open 持仓占用的保证金"""
    rows = execute_read(
        "SELECT strike, qty, margin_ratio FROM paper_positions WHERE status = 'open'"
    )
    total = 0.0
    for r in rows:
        strike = r[0] or 0
        qty = r[1] or 0
        margin_ratio = r[2] or 0
        total += strike * qty * margin_ratio
    return total


def _estimate_current_premium(pos: Dict, spot: float) -> float:
    """使用 Black-Scholes 模型估算当前权利金"""
    if spot <= 0:
        return pos["entry_premium"] * 0.5

    strike = pos["strike"]
    dte = _get_dte(pos.get("expiry", ""))
    # 估算 IV: 从开仓权利金反推，或使用默认 60%
    iv = 0.6

    try:
        if pos["option_type"] == "PUT":
            return bs_put_price(spot, strike, dte / 365, iv, 0.05)
        else:
            return bs_call_price(spot, strike, dte / 365, iv, 0.05)
    except (ValueError, TypeError, ZeroDivisionError):
        # BS 计算失败时回退到开仓权利金的线性衰减
        try:
            entry_date = datetime.strptime(pos.get("timestamp", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            days_held = (datetime.now(timezone.utc) - entry_date).days
        except (ValueError, TypeError):
            days_held = 1
        decay = max(0, 1 - 0.02 * days_held)
        return pos["entry_premium"] * decay


def _get_dte(expiry: str) -> int:
    """估算 DTE"""
    if not expiry:
        return 30  # 默认
    try:
        exp_date = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return max(0, (exp_date - datetime.now(timezone.utc)).days)
    except (ValueError, TypeError) as e:
        logger.debug("DTE parse fallback for %s: %s", expiry, e)
        return 30
