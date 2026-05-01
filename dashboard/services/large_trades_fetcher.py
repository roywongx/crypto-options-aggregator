"""
大单交易获取服务 - 从 scan_engine.py 提取，消除 async/sync 双版本重复代码
"""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from db.connection import get_db_connection

logger = logging.getLogger(__name__)

MIN_NOTIONAL = 100000


def _build_large_trades_query(currency: str, days: int, limit: int):
    """构建大单查询 SQL 和参数"""
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    sql = """
        SELECT instrument_name, direction, notional_usd, volume, strike,
               option_type, flow_label, delta, premium_usd, severity
        FROM large_trades_history
        WHERE currency = ? AND timestamp > ?
          AND instrument_name IS NOT NULL AND instrument_name != ''
          AND instrument_name != '(EMPTY)' AND strike > 100
        ORDER BY notional_usd DESC LIMIT ?
    """
    return sql, (currency, since, limit)


def _parse_db_rows(rows, spot: float, classify_fn, parse_inst_fn) -> tuple:
    """解析数据库行，返回 (results, results_by_inst, seen)"""
    results = []
    seen = set()
    results_by_inst = {}
    for r in rows:
        inst = (r[0] or '').strip()
        strike = r[4] or 0
        direction = r[1] or ''
        opt_type = r[5] or ''
        if not inst or strike <= 100 or inst in seen:
            continue
        seen.add(inst)
        fl = r[6] or ''
        delta_val = r[7] or 0
        if not fl or fl == 'unknown':
            fl = classify_fn(direction, opt_type, float(delta_val), strike, spot)

        notional = r[2] or 0
        if notional <= 0 and (r[3] or 0) > 0 and strike > 0:
            notional = float(r[3]) * spot

        entry = {
            "instrument_name": inst, "direction": direction,
            "notional_usd": round(float(notional), 2),
            "volume": r[3] or 0,
            "strike": strike, "option_type": opt_type, "flow_label": fl,
            "delta": delta_val,
            "premium_usd": r[8] or 0,
            "severity": r[9] or ''
        }
        results.append(entry)
        results_by_inst[inst] = entry
    return results, results_by_inst, seen


def _enrich_from_api(
    trades: List[dict],
    results_by_inst: Dict[str, dict],
    seen: set,
    spot: float,
    limit: int,
    classify_fn,
    parse_inst_fn,
    calc_delta_fn,
    severity_fn,
    risk_emoji_fn
) -> List[dict]:
    """从 Deribit API 补充数据并合并"""
    results = list(trades)
    for t in trades:
        inst = t.get("instrument_name", "")
        if not inst:
            continue

        meta = None
        try:
            meta = parse_inst_fn(inst)
        except (ValueError, TypeError, AttributeError) as e:
            logger.debug("Instrument parse failed for %s: %s", inst, e)
            continue
        if not meta:
            continue

        direction = t.get("direction", "")
        trade_amount = float(t.get("amount", 0))
        index_price = float(t.get("index_price", 0) or spot)
        option_price = float(t.get("price", 0))

        notional_usd = trade_amount * index_price
        premium_usd = trade_amount * option_price * index_price

        if notional_usd < MIN_NOTIONAL:
            continue

        trade_iv = float(t.get("iv") or 50)
        delta_val = abs(calc_delta_fn(meta.strike, spot, trade_iv, meta.dte, meta.option_type))

        fl = classify_fn(direction, meta.option_type, delta_val, meta.strike, spot)

        is_block = t.get("block_trade", False) or t.get("block_trade_id") is not None

        api_entry = {
            "instrument_name": inst, "direction": direction,
            "notional_usd": round(notional_usd, 2),
            "premium_usd": round(premium_usd, 2),
            "volume": round(trade_amount, 4),
            "strike": meta.strike,
            "option_type": meta.option_type,
            "flow_label": fl,
            "delta": delta_val,
            "iv": round(trade_iv, 1),
            "is_block": is_block,
            "trade_price": option_price
        }

        if inst in results_by_inst:
            db_entry = results_by_inst[inst]
            if not db_entry.get('premium_usd'):
                db_entry['premium_usd'] = round(premium_usd, 2)
            if not db_entry.get('iv'):
                db_entry['iv'] = round(trade_iv * 100, 1)
            if not db_entry.get('is_block'):
                db_entry['is_block'] = is_block
            if not db_entry.get('trade_price'):
                db_entry['trade_price'] = option_price
            if not db_entry.get('notional_usd') or db_entry['notional_usd'] < notional_usd:
                db_entry['notional_usd'] = round(notional_usd, 2)
                db_entry['volume'] = round(trade_amount, 4)
        else:
            seen.add(inst)
            results.append(api_entry)
            results_by_inst[inst] = api_entry

        if len(results) >= limit:
            break
    return results


def _finalize_results(results: List[dict], limit: int, severity_fn, risk_emoji_fn) -> List[dict]:
    """填充 severity/risk_level 并排序截断"""
    for t in results:
        if not t.get("severity"):
            t["severity"] = severity_fn(t.get("notional_usd", 0) or 0)
        t["risk_level"] = risk_emoji_fn(abs(t.get("delta", 0) or 0))
    results.sort(key=lambda x: x.get("notional_usd", 0), reverse=True)
    return results[:limit]


def _need_api_fallback(results: List[dict], limit: int) -> bool:
    """判断是否需要从 API 补充数据"""
    db_missing_premium = sum(1 for r in results if not r.get('premium_usd'))
    return len(results) < max(5, limit // 2) or db_missing_premium > len(results) * 0.5


def fetch_large_trades_sync(
    currency: str,
    days: int = 7,
    limit: int = 50,
    spot_fetcher=None,
    classify_fn=None,
    parse_inst_fn=None,
    calc_delta_fn=None,
    severity_fn=None,
    risk_emoji_fn=None
) -> List[Dict[str, Any]]:
    """同步获取大单交易"""
    from services.spot_price import get_spot_price
    from services.flow_classifier import _classify_flow_heuristic, _severity_from_notional
    from services.instrument import _parse_inst_name
    from services.dvol_analyzer import calc_delta_bs
    from services.risk_framework import _risk_emoji

    spot = spot_fetcher() if spot_fetcher else get_spot_price(currency)
    classify_fn = classify_fn or _classify_flow_heuristic
    parse_inst_fn = parse_inst_fn or _parse_inst_name
    calc_delta_fn = calc_delta_fn or calc_delta_bs
    severity_fn = severity_fn or _severity_from_notional
    risk_emoji_fn = risk_emoji_fn or _risk_emoji

    sql, params = _build_large_trades_query(currency, days, limit)
    conn = get_db_connection(read_only=True)
    cursor = conn.cursor()
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()

    results, results_by_inst, seen = _parse_db_rows(rows, spot, classify_fn, parse_inst_fn)

    if _need_api_fallback(results, limit):
        try:
            from services.http_client import http_get
            api_url = "https://www.deribit.com/api/v2/public/get_last_trades_by_currency"
            payload = http_get(api_url, params={
                "currency": currency, "kind": "option", "count": 500
            }, timeout=10.0).json()
            trades = payload.get("result", {}).get("trades", [])
            results = _enrich_from_api(
                trades, results_by_inst, seen, spot, limit,
                classify_fn, parse_inst_fn, calc_delta_fn, severity_fn, risk_emoji_fn
            )
        except (httpx.HTTPError, httpx.ConnectError, httpx.TimeoutException) as e:
            logger.error("Deribit live trades fallback error: %s", e)

    return _finalize_results(results, limit, severity_fn, risk_emoji_fn)


async def fetch_large_trades_async(
    currency: str,
    days: int = 7,
    limit: int = 50,
    spot_fetcher=None,
    classify_fn=None,
    parse_inst_fn=None,
    calc_delta_fn=None,
    severity_fn=None,
    risk_emoji_fn=None
) -> List[Dict[str, Any]]:
    """异步获取大单交易"""
    from services.spot_price import get_spot_price_async
    from services.flow_classifier import _classify_flow_heuristic, _severity_from_notional
    from services.instrument import _parse_inst_name
    from services.dvol_analyzer import calc_delta_bs
    from services.risk_framework import _risk_emoji

    spot = await spot_fetcher() if spot_fetcher else await get_spot_price_async(currency)
    classify_fn = classify_fn or _classify_flow_heuristic
    parse_inst_fn = parse_inst_fn or _parse_inst_name
    calc_delta_fn = calc_delta_fn or calc_delta_bs
    severity_fn = severity_fn or _severity_from_notional
    risk_emoji_fn = risk_emoji_fn or _risk_emoji

    sql, params = _build_large_trades_query(currency, days, limit)
    conn = get_db_connection(read_only=True)
    cursor = conn.cursor()
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()

    results, results_by_inst, seen = _parse_db_rows(rows, spot, classify_fn, parse_inst_fn)

    if _need_api_fallback(results, limit):
        try:
            import httpx
            api_url = "https://www.deribit.com/api/v2/public/get_last_trades_by_currency"
            async with httpx.AsyncClient() as client:
                response = await client.get(api_url, params={
                    "currency": currency, "kind": "option", "count": 500
                }, timeout=10.0)
                payload = response.json()
            trades = payload.get("result", {}).get("trades", [])
            results = _enrich_from_api(
                trades, results_by_inst, seen, spot, limit,
                classify_fn, parse_inst_fn, calc_delta_fn, severity_fn, risk_emoji_fn
            )
        except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
            logger.error("Deribit live trades fallback error: %s", e)

    return _finalize_results(results, limit, severity_fn, risk_emoji_fn)
