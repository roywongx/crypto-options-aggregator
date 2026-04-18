# Trades and wind analysis services
from typing import Dict, Any, List
import requests
import re
from datetime import datetime, timedelta


def generate_wind_sentiment(summary: Dict, spot: float) -> str:
    parts = []
    kr = summary.get('key_levels', {})

    buy_ratio = summary.get('buy_ratio', 0.5)
    sell_ratio = summary.get('sell_ratio', 0.5)
    total_trades = summary.get('total_trades', 0)
    if total_trades > 0:
        buy_pct = buy_ratio * 100
        if buy_pct > 55:
            parts.append(f"买盘主导({buy_pct:.0f}%)")
        elif buy_pct < 45:
            parts.append(f"卖盘主导({100-buy_pct:.0f}%)")

    support = kr.get('net_support')
    resistance = kr.get('net_resistance')
    if support and resistance:
        spct_s = (support - spot) / spot * 100
        spct_r = (resistance - spot) / spot * 100
        parts.append(f"支撑${support/1000:.0f}K({spct_s:+.1f}%)/阻力${resistance/1000:.0f}K({spct_r:+.1f}%)")

    top_flow = summary.get('dominant_flow')
    if top_flow and top_flow != 'unknown':
        parts.append(f"主流行为:{top_flow}")

    if not parts:
        return "数据不足，暂无法判断"
    return " | ".join(parts)


def fetch_deribit_summaries(currency: str = "BTC") -> List[Dict]:
    try:
        from main import _get_deribit_monitor
        mon = _get_deribit_monitor()
        summaries = mon._get_book_summaries(currency)
        return summaries if summaries else []
    except Exception:
        return []


def fetch_large_trades(currency: str = "BTC", days: int = 7, limit: int = 50) -> List[Dict]:
    from db.connection import get_db_connection
    try:
        conn = get_db_connection(read_only=True)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT timestamp, currency, source, title, message, direction, strike,
                   volume, option_type, flow_label, notional_usd, delta, instrument_name
            FROM large_trades_history
            WHERE currency = ?
            AND timestamp >= datetime('now', ?)
            ORDER BY timestamp DESC LIMIT ?
        """, (currency, f'-{days} days', limit))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []
