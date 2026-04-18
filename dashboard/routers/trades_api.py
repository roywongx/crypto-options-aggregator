from fastapi import APIRouter, Query
from datetime import datetime, timedelta

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("/history")
async def get_trades_history(
    days: int = Query(default=7),
    direction: str = Query(default=""),
    source: str = Query(default="")
):
    from db.connection import execute_read

    since_str = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

    query = "SELECT * FROM large_trades_history WHERE timestamp > ?"
    params = [since_str]

    if direction:
        query += " AND direction = ?"
        params.append(direction)
    if source:
        query += " AND source = ?"
        params.append(source)

    query += " ORDER BY timestamp DESC LIMIT 500"
    rows = execute_read(query, tuple(params))
    
    col_names = ['id', 'timestamp', 'currency', 'source', 'title', 'message',
                 'direction', 'strike', 'volume', 'option_type', 'flow_label',
                 'notional_usd', 'delta', 'instrument_name', 'premium_usd', 'severity']
    
    return [{col_names[i]: val for i, val in enumerate(row) if i < len(col_names)} for row in rows]


@router.get("/strike-distribution")
async def get_strike_distribution(
    currency: str = Query(default="BTC"),
    days: int = Query(default=7)
):
    from db.connection import execute_read

    since_str = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    rows = execute_read("""
        SELECT strike, option_type, SUM(volume) as total_volume, COUNT(*) as trade_count
        FROM large_trades_history
        WHERE currency = ? AND timestamp > ?
        GROUP BY strike, option_type
        ORDER BY total_volume DESC
        LIMIT 50
    """, (currency, since_str))
    
    return [{"strike": r[0], "option_type": r[1], "total_volume": r[2], "trade_count": r[3]} for r in rows]


@router.get("/wind-analysis")
async def get_wind_analysis(currency: str = Query(default="BTC"), days: int = Query(default=30)):
    from services.trades import fetch_deribit_summaries
    from services.risk_framework import RiskFramework
    from db.connection import execute_read

    since_str = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

    grouped = execute_read("""
        SELECT direction, option_type, SUM(volume) as total_volume, COUNT(*) as trade_count
        FROM large_trades_history
        WHERE currency = ? AND timestamp >= ?
        GROUP BY direction, option_type
    """, (currency, since_str))

    strike_rows = execute_read("""
        SELECT strike, option_type, SUM(volume) as total_volume, COUNT(*) as trade_count
        FROM large_trades_history
        WHERE currency = ? AND timestamp >= ?
        GROUP BY strike, option_type
        ORDER BY strike ASC
    """, (currency, since_str))

    summary_data = {'buy_put': 0, 'sell_call': 0, 'buy_call': 0, 'sell_put': 0, 'total': 0, 'put_vol': 0, 'call_vol': 0}
    for row in grouped:
        direction = (row[0] or '').lower()
        ot = (row[1] or 'PUT').upper()
        count = row[3] or 0
        vol = row[2] or 0
        summary_data['total'] += count
        if direction == 'buy' and ot == 'PUT':
            summary_data['buy_put'] += count
            summary_data['put_vol'] += vol
        elif direction == 'sell' and ot == 'CALL':
            summary_data['sell_call'] += count
            summary_data['call_vol'] += vol
        elif direction == 'buy' and ot == 'CALL':
            summary_data['buy_call'] += count
            summary_data['call_vol'] += vol
        elif direction == 'sell' and ot == 'PUT':
            summary_data['sell_put'] += count
            summary_data['put_vol'] += vol

    summaries = fetch_deribit_summaries(currency)
    spot = 0
    if summaries:
        deribit_sp = float(summaries[0].get('underlying_price', 0)) if summaries else 0
        spot = deribit_sp if deribit_sp > 1000 else spot
    if not spot:
        try:
            from services.spot_price import get_spot_price
            spot = get_spot_price(currency)
        except Exception:
            from constants import get_spot_fallback
            spot = get_spot_fallback(currency)

    total = summary_data['total'] or 1
    bp = summary_data['buy_put']
    sc = summary_data['sell_call']
    bc = summary_data['buy_call']
    sp = summary_data['sell_put']

    bp_ratio = bp / total
    sc_ratio = sc / total
    buy_ratio = (bp + bc) / total
    dominant = "看跌保护" if bp_ratio > 0.3 else ("Covered Call偏好" if sc_ratio > 0.3 else "中性")

    put_otm = sp * 0.6
    put_itm = sp * 0.4
    call_otm = bc * 0.6
    call_itm = bc * 0.4
    protected_put = bp * 0.3
    speculative_put = bp * 0.7
    covered_call = sc * 0.5
    call_overwrite = sc * 0.5

    flow_breakdown = [
        {"type": "protective_hedge", "count": round(protected_put), "label": "保护性对冲"},
        {"type": "premium_collect", "count": round(put_otm), "label": "收权利金(OTM Put)"},
        {"type": "covered_call", "count": round(covered_call), "label": "备兑开仓"},
        {"type": "speculative_put", "count": round(put_itm + speculative_put * 0.3), "label": "看跌投机(ITM Put)"},
        {"type": "speculative_call", "count": round(call_otm), "label": "看涨投机(OTM Call)"},
        {"type": "call_momentum", "count": round(call_itm), "label": "追涨建仓(ITM Call)"},
        {"type": "call_overwrite", "count": round(call_overwrite), "label": "改仓操作"},
        {"type": "put_buy_hedge", "count": round(speculative_put * 0.7), "label": "保护性买入"},
        {"type": "unclassified", "count": 0, "label": "未分类"}
    ]

    strikes = {}
    for row in strike_rows:
        strike = int(row[0] / 1000) * 1000
        ot = (row[1] or 'PUT').upper()
        vol = row[2] or 0
        if strike not in strikes:
            strikes[strike] = {'call': 0, 'put': 0, 'total': 0}
        if ot == 'CALL':
            strikes[strike]['call'] += vol
        else:
            strikes[strike]['put'] += vol
        strikes[strike]['total'] += vol

    distribution = []
    for k in sorted(strikes.keys()):
        v = strikes[k]
        dist_pct = ((k - spot) / spot * 100) if spot > 0 else 0
        distribution.append({
            "strike": k,
            "call": round(v['call'], 0),
            "put": round(v['put'], 0),
            "total": round(v['total'], 0),
            "dist_from_spot_pct": round(dist_pct, 2)
        })

    strike_flows = []
    for k in sorted(strikes.keys())[:20]:
        v = strikes[k]
        net = v['put'] - v['call']
        dist_pct = ((k - spot) / spot * 100) if spot > 0 else 0
        strike_flows.append({
            "strike": k,
            "option_type": "PUT" if net < 0 else "CALL",
            "volume": round(v['total'], 0),
            "net": round(net, 0),
            "dist_from_spot_pct": round(dist_pct, 2)
        })

    sentiment_score = round((bp_ratio * 2 + sc_ratio * 1.5 + bc / total * 1) - (sp / total * 1), 2) if total > 10 else 0

    risk = RiskFramework.get_status(spot)
    support = RiskFramework.REGULAR_FLOOR
    resistance = RiskFramework.REGULAR_FLOOR * 1.2

    return {
        "currency": currency, "spot": spot, "days": days,
        "distribution": distribution[:20],
        "strike_flows": strike_flows,
        "flow_breakdown": flow_breakdown,
        "buy_ratio": round(buy_ratio, 3), "dominant_flow": dominant,
        "risk_level": risk, "support": support, "resistance": resistance,
        "sentiment_score": sentiment_score,
        "sentiment_text": dominant,
        "summary": {"total_trades": summary_data['total'], "buy_puts": bp,
                    "sell_calls": sc, "buy_calls": bc, "sell_puts": sp}
    }
