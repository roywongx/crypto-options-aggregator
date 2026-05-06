import logging
from fastapi import APIRouter, Query
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("/history")
async def get_trades_history(
    days: int = Query(default=7, ge=1, le=90),
    direction: str = Query(default=""),
    source: str = Query(default="")
):
    from db.async_connection import execute_read_async

    since_str = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

    query = "SELECT * FROM large_trades_history WHERE timestamp > ?"
    params = [since_str]

    if direction:
        query += " AND direction = ?"
        params.append(direction)
    if source:
        query += " AND source = ?"
        params.append(source)

    query += " ORDER BY timestamp DESC LIMIT 500"
    rows = await execute_read_async(query, tuple(params))
    
    col_names = ['id', 'timestamp', 'currency', 'source', 'title', 'message',
                 'direction', 'strike', 'volume', 'option_type', 'flow_label',
                 'notional_usd', 'delta', 'instrument_name', 'premium_usd', 'severity']
    
    return [{col_names[i]: val for i, val in enumerate(row) if i < len(col_names)} for row in rows]


@router.get("/strike-distribution")
async def get_strike_distribution(
    currency: str = Query(default="BTC"),
    days: int = Query(default=7, ge=1, le=90)
):
    from db.async_connection import execute_read_async

    since_str = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    rows = await execute_read_async("""
        SELECT strike, option_type, SUM(volume) as total_volume, COUNT(*) as trade_count
        FROM large_trades_history
        WHERE currency = ? AND timestamp > ?
        GROUP BY strike, option_type
        ORDER BY total_volume DESC
        LIMIT 50
    """, (currency, since_str))
    
    return [{"strike": r[0], "option_type": r[1], "total_volume": r[2], "trade_count": r[3]} for r in rows]


@router.get("/wind-analysis")
async def get_wind_analysis(
    currency: str = Query(default="BTC"),
    days: int = Query(default=30, ge=1, le=90)
):
    from services.trades import fetch_deribit_summaries
    from services.risk_framework import RiskFramework
    from db.async_connection import execute_read_async

    since_str = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

    # 使用名义价值而非笔数进行计算，与 _flow_analyst 保持一致
    grouped = await execute_read_async("""
        SELECT direction, option_type, SUM(notional_usd) as total_notional, COUNT(*) as trade_count
        FROM large_trades_history
        WHERE currency = ? AND timestamp >= ?
        GROUP BY direction, option_type
    """, (currency, since_str))

    strike_rows = await execute_read_async("""
        SELECT strike, option_type, SUM(volume) as total_volume, SUM(notional_usd) as total_notional
        FROM large_trades_history
        WHERE currency = ? AND timestamp >= ?
        GROUP BY strike, option_type
        ORDER BY strike ASC
    """, (currency, since_str))

    # 现货附近的单笔交易（用于 moneyness 分桶和 freshness 判断）
    detail_rows = await execute_read_async("""
        SELECT strike, direction, option_type, notional_usd
        FROM large_trades_history
        WHERE currency = ? AND timestamp >= ?
    """, (currency, since_str))

    summary_data = {'buy_put': 0, 'sell_call': 0, 'buy_call': 0, 'sell_put': 0,
                    'total_count': 0, 'put_vol': 0, 'call_vol': 0,
                    'buy_put_notional': 0, 'sell_put_notional': 0,
                    'buy_call_notional': 0, 'sell_call_notional': 0}
    for row in grouped:
        direction = (row[0] or '').lower()
        ot = (row[1] or 'PUT').upper()
        count = row[3] or 0
        vol = row[2] or 0
        notional = float(row[2] or 0)
        summary_data['total_count'] += count
        if direction == 'buy' and ot == 'PUT':
            summary_data['buy_put'] += count
            summary_data['put_vol'] += vol
            summary_data['buy_put_notional'] += notional
        elif direction == 'sell' and ot == 'CALL':
            summary_data['sell_call'] += count
            summary_data['call_vol'] += vol
            summary_data['sell_call_notional'] += notional
        elif direction == 'buy' and ot == 'CALL':
            summary_data['buy_call'] += count
            summary_data['call_vol'] += vol
            summary_data['buy_call_notional'] += notional
        elif direction == 'sell' and ot == 'PUT':
            summary_data['sell_put'] += count
            summary_data['put_vol'] += vol
            summary_data['sell_put_notional'] += notional

    summaries = fetch_deribit_summaries(currency)
    spot = 0
    if summaries:
        deribit_sp = float(summaries[0].get('underlying_price', 0)) if summaries else 0
        spot = deribit_sp if deribit_sp > 1000 else spot
    if not spot:
        try:
            from services.spot_price import get_spot_price
            spot = get_spot_price(currency)
        except (RuntimeError, ValueError) as e:
            logger.warning("Trades API spot price failed: %s, using fallback", e)
            from constants import get_spot_fallback
            spot = get_spot_fallback(currency)

    # 使用名义价值计算比率（与 _flow_analyst 保持一致）
    total_buy_notional = summary_data['buy_put_notional'] + summary_data['buy_call_notional']
    total_sell_notional = summary_data['sell_put_notional'] + summary_data['sell_call_notional']
    total_notional = total_buy_notional + total_sell_notional

    # 方向性分类：bullish flow = buy_call + sell_put（都预期上涨）
    #              bearish flow = buy_put + sell_call（都预期下跌/对冲）
    bullish_notional = summary_data['buy_call_notional'] + summary_data['sell_put_notional']
    bearish_notional = summary_data['buy_put_notional'] + summary_data['sell_call_notional']
    bullish_ratio = bullish_notional / total_notional if total_notional > 0 else 0.5

    bp = summary_data['buy_put']
    sc = summary_data['sell_call']
    bc = summary_data['buy_call']
    sp = summary_data['sell_put']

    bp_ratio = summary_data['buy_put_notional'] / total_notional if total_notional > 0 else 0
    sc_ratio = summary_data['sell_call_notional'] / total_notional if total_notional > 0 else 0
    buy_ratio = total_buy_notional / total_notional if total_notional > 0 else 0.5
    dominant = "看跌保护" if bp_ratio > 0.3 else ("Covered Call偏好" if sc_ratio > 0.3 else "中性")

    # PCR = Put成交量 / Call成交量
    put_vol = summary_data['buy_put_notional'] + summary_data['sell_put_notional']
    call_vol = summary_data['buy_call_notional'] + summary_data['sell_call_notional']
    pcr = put_vol / call_vol if call_vol > 0 else 1.0

    # 互斥的流向分类（每笔交易只归一类）
    # 基于 direction + option_type 的真实分类
    flow_breakdown = [
        {"type": "sell_put", "count": sp, "label": "卖出 Put (收租)"},
        {"type": "buy_call", "count": bc, "label": "买入 Call (看涨)"},
        {"type": "buy_put", "count": bp, "label": "买入 Put (对冲)"},
        {"type": "sell_call", "count": sc, "label": "卖出 Call (备兑)"}
    ]
    # 按数量降序排列
    flow_breakdown.sort(key=lambda x: x["count"], reverse=True)

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

    # 只保留现价 ±25% 范围内的行权价
    spot_range_low = spot * 0.75 if spot > 0 else 0
    spot_range_high = spot * 1.25 if spot > 0 else float('inf')
    distribution = []
    for k in sorted(strikes.keys()):
        if k < spot_range_low or k > spot_range_high:
            continue
        v = strikes[k]
        dist_pct = ((k - spot) / spot * 100) if spot > 0 else 0
        distribution.append({
            "strike": k,
            "call": round(v['call'], 0),
            "put": round(v['put'], 0),
            "total": round(v['total'], 0),
            "dist_from_spot_pct": round(dist_pct, 2)
        })

    # 现价附近行权价流向（仅 ±25%）
    strike_flows = []
    for k in sorted(strikes.keys()):
        if k < spot_range_low or k > spot_range_high:
            continue
        v = strikes[k]
        net = v['put'] - v['call']
        dist_pct = ((k - spot) / spot * 100) if spot > 0 else 0
        strike_flows.append({
            "strike": k,
            "option_type": "CALL" if net < 0 else "PUT",
            "volume": round(v['total'], 0),
            "net": round(net, 0),
            "dist_from_spot_pct": round(dist_pct, 2)
        })

    # === Moneyness 分桶 (名义价值) ===
    moneyness_buckets = [
        {"key": "deep_otm_put",  "label": "深度虚值Put",  "range": "< -20%",  "notional": 0, "count": 0},
        {"key": "otm_put",       "label": "虚值Put",       "range": "-20%~-5%", "notional": 0, "count": 0},
        {"key": "near_atm",      "label": "平值附近",      "range": "±5%",      "notional": 0, "count": 0},
        {"key": "otm_call",      "label": "虚值Call",      "range": "+5%~+20%", "notional": 0, "count": 0},
        {"key": "deep_otm_call", "label": "深度虚值Call",  "range": "> +20%",   "notional": 0, "count": 0},
    ]
    near_spot_trades = 0
    for row in detail_rows:
        strike = float(row[0] or 0)
        notional = float(row[3] or 0)
        if spot > 0:
            pct = (strike - spot) / spot
            if pct < -0.20:
                moneyness_buckets[0]["notional"] += notional
                moneyness_buckets[0]["count"] += 1
            elif pct < -0.05:
                moneyness_buckets[1]["notional"] += notional
                moneyness_buckets[1]["count"] += 1
            elif pct <= 0.05:
                moneyness_buckets[2]["notional"] += notional
                moneyness_buckets[2]["count"] += 1
                near_spot_trades += 1
            elif pct <= 0.20:
                moneyness_buckets[3]["notional"] += notional
                moneyness_buckets[3]["count"] += 1
            else:
                moneyness_buckets[4]["notional"] += notional
                moneyness_buckets[4]["count"] += 1
    for b in moneyness_buckets:
        b["notional"] = round(b["notional"], 0)

    # 数据新鲜度：现价 ±10% 内有交易才认为数据有效
    data_freshness = "fresh" if near_spot_trades >= 3 else "stale"

    # 修正 sentiment_score 计算（使用方向性 bullish_ratio）
    total_count = summary_data['total_count']
    sentiment_score = round((bullish_ratio - 0.5) * 200) if total_count > 10 else 0  # -100 to +100

    risk = RiskFramework.get_status(spot)
    support = RiskFramework.REGULAR_FLOOR
    resistance = RiskFramework.REGULAR_FLOOR * 1.2

    return {
        "currency": currency, "spot": spot, "days": days,
        "distribution": distribution[:16],
        "strike_flows": strike_flows[:16],
        "flow_breakdown": flow_breakdown,
        "moneyness_breakdown": moneyness_buckets,
        "data_freshness": data_freshness,
        "near_spot_trades": near_spot_trades,
        "buy_ratio": round(buy_ratio, 3),
        "bullish_ratio": round(bullish_ratio, 3),
        "dominant_flow": dominant,
        "risk_level": risk, "support": support, "resistance": resistance,
        "sentiment_score": sentiment_score,
        "sentiment_text": dominant,
        "pcr": round(pcr, 2),
        "total_notional": round(total_notional, 0),
        "summary": {"total_trades": total_count, "buy_puts": bp,
                    "sell_calls": sc, "buy_calls": bc, "sell_puts": sp,
                    "buy_put_notional": round(summary_data['buy_put_notional'], 0),
                    "sell_put_notional": round(summary_data['sell_put_notional'], 0),
                    "buy_call_notional": round(summary_data['buy_call_notional'], 0),
                    "sell_call_notional": round(summary_data['sell_call_notional'], 0)}
    }
