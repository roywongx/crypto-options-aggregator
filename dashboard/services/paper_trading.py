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
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from db.connection import execute_read, execute_write, execute_transaction
from services.spot_price import get_spot_price
from services.quant_engine import bs_put_price, bs_call_price, bs_delta
from services.margin_calculator import calc_margin

logger = logging.getLogger(__name__)

# ============================================================
# 数据库初始化
# ============================================================

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
        
        # 检查是否需要初始化账户
        rows = execute_read("SELECT COUNT(*) FROM paper_account")
        if rows[0][0] == 0:
            execute_write(
                "INSERT INTO paper_account (id, initial_capital, current_cash, currency) VALUES (1, 50000, 50000, 'BTC')"
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
    模拟开仓
    
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
    try:
        # 获取账户信息
        account = _get_account()
        if not account:
            return {"error": "账户未初始化"}

        premium_total = premium * qty
        margin_required = calc_margin(option_type, strike, premium, qty, currency=currency)

        # 计算已占用保证金（open positions）
        locked_margin = _get_locked_margin()
        available_cash = account["current_cash"] - locked_margin

        # 检查保证金
        if available_cash < margin_required:
            return {
                "error": "保证金不足",
                "required": margin_required,
                "available": available_cash,
                "locked_margin": locked_margin
            }

        # 扣除现金 (收到权利金)
        new_cash = account["current_cash"] + premium_total

        # 同一事务提交：持仓 + 交易记录 + 账户更新
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
                "UPDATE paper_account SET current_cash = ? WHERE id = 1",
                (new_cash,)
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
            "locked_margin": locked_margin + margin_required,
            "new_cash": new_cash,
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
    模拟平仓
    
    Args:
        position_id: 持仓 ID
        close_premium: 平仓权利金 (单张)
        notes: 备注
    
    Returns:
        平仓结果
    """
    try:
        # 获取持仓
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

        # 计算 PnL (卖方: 开仓收权利金 - 平仓付权利金)
        pnl = pos["entry_premium_total"] - close_premium_total

        # 释放保证金 + 退回/扣除平仓成本
        account = _get_account()
        new_cash = account["current_cash"] - close_premium_total

        # 同一事务提交：更新持仓 + 交易记录 + 账户更新
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
                "UPDATE paper_account SET current_cash = ? WHERE id = 1",
                (new_cash,)
            )
        ])

        return {
            "success": True,
            "action": "CLOSE",
            "position_id": position_id,
            "pnl": pnl,
            "new_cash": new_cash,
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
    获取模拟盘组合概览
    """
    account = _get_account()
    if not account:
        return {"error": "账户未初始化"}
    
    # 获取所有 open 持仓
    positions = _get_open_positions(currency)
    
    spot = get_spot_price(currency) or 0
    
    # 计算 UPnL 和保证金
    total_margin = 0
    total_upnl = 0
    position_details = []
    
    for pos in positions:
        # 估算当前权利金 (简化: 按内在价值 + 时间价值衰减)
        current_premium = _estimate_current_premium(pos, spot)
        
        # UPnL = 开仓收入 - 当前价值
        upnl = pos["entry_premium"] - current_premium
        total_upnl += upnl * pos["qty"]
        
        margin = pos["strike"] * pos["qty"] * pos["margin_ratio"]
        total_margin += margin
        
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
    
    # 组合总价值
    total_equity = account["current_cash"] + total_upnl
    pnl_pct = (total_equity - account["initial_capital"]) / account["initial_capital"] * 100
    
    return {
        "initial_capital": account["initial_capital"],
        "current_cash": round(account["current_cash"], 2),
        "total_equity": round(total_equity, 2),
        "total_upnl": round(total_upnl, 2),
        "total_margin": round(total_margin, 2),
        "margin_usage_pct": round(total_margin / account["initial_capital"] * 100, 1) if account["initial_capital"] > 0 else 0,
        "pnl_pct": round(pnl_pct, 2),
        "spot_price": spot,
        "positions_count": len(positions),
        "positions": position_details,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    }


def get_trade_history(currency: str = "BTC", limit: int = 50) -> List[Dict[str, Any]]:
    """获取历史交易记录"""
    rows = execute_read("""
        SELECT * FROM paper_trades
        WHERE currency = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (currency, limit))
    
    return [
        {
            "id": r[0], "timestamp": r[1], "currency": r[2], "action": r[3],
            "option_type": r[4], "strike": r[5], "expiry": r[6],
            "qty": r[7], "premium": r[8], "premium_total": r[9],
            "pnl": r[11], "notes": r[12]
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
                "estimated_new_apr": round(current_premium * 1.2 / (suggested_strike * 0.2), 1)
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
    """获取账户信息"""
    rows = execute_read("SELECT * FROM paper_account WHERE id = 1")
    if rows:
        return {
            "initial_capital": rows[0][1],
            "current_cash": rows[0][2],
            "currency": rows[0][3]
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
    """估算当前权利金 (简化: 内在价值 + 时间价值衰减)"""
    if spot <= 0:
        return pos["entry_premium"] * 0.5  # 默认衰减一半
    
    strike = pos["strike"]
    entry_premium = pos["entry_premium"]
    
    if pos["option_type"] == "PUT":
        intrinsic = max(0, strike - spot)
    else:
        intrinsic = max(0, spot - strike)
    
    # 简化时间衰减: 假设每天衰减 2%
    try:
        entry_date = datetime.strptime(pos["timestamp"], "%Y-%m-%d %H:%M:%S")
        days_held = (datetime.now(timezone.utc) - entry_date).days
    except (ValueError, TypeError, KeyError) as e:
        logger.debug("Time decay calc fallback: %s", e)
        days_held = 1

    time_value = max(0, entry_premium - intrinsic) * max(0, 1 - 0.02 * days_held)

    return intrinsic + time_value


def _get_dte(expiry: str) -> int:
    """估算 DTE"""
    if not expiry:
        return 30  # 默认
    try:
        exp_date = datetime.strptime(expiry, "%Y-%m-%d")
        return max(0, (exp_date - datetime.now(timezone.utc)).days)
    except (ValueError, TypeError) as e:
        logger.debug("DTE parse fallback for %s: %s", expiry, e)
        return 30
