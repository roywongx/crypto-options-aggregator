"""
大单交易获取服务 - 从 scan_engine.py 提取，消除 async/sync 双版本重复代码
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from db.connection import get_db_connection

logger = logging.getLogger(__name__)

MIN_NOTIONAL = 100000


def _build_large_trades_query(currency: str, days: int, limit: int):
    """构建大单查询 SQL 和参数"""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
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
    """解析数据库行，返回 (results, results_by_inst, seen)
    
    注意：不再按 instrument_name 去重，而是汇总同一合约的买卖数据
    """
    results = []
    seen = set()
    results_by_inst = {}
    
    # 先汇总同一合约的数据
    inst_data = {}
    for r in rows:
        inst = (r[0] or '').strip()
        strike = r[4] or 0
        direction = r[1] or ''
        opt_type = r[5] or ''
        if not inst or strike <= 100:
            continue
        
        notional = r[2] or 0
        if notional <= 0 and (r[3] or 0) > 0 and strike > 0:
            notional = float(r[3]) * spot
        
        if inst not in inst_data:
            inst_data[inst] = {
                "instrument_name": inst,
                "strike": strike,
                "option_type": opt_type,
                "buy_notional": 0,
                "sell_notional": 0,
                "buy_count": 0,
                "sell_count": 0,
                "total_volume": 0,
                "flow_label": r[6] or '',
                "delta": r[7] or 0,
                "premium_usd": r[8] or 0,
                "severity": r[9] or ''
            }
        
        if direction == 'buy':
            inst_data[inst]["buy_notional"] += float(notional)
            inst_data[inst]["buy_count"] += 1
        else:
            inst_data[inst]["sell_notional"] += float(notional)
            inst_data[inst]["sell_count"] += 1
        inst_data[inst]["total_volume"] += r[3] or 0
    
    # 转换为结果列表，使用净方向
    for inst, data in inst_data.items():
        seen.add(inst)
        
        # 确定主导方向
        if data["buy_notional"] > data["sell_notional"]:
            direction = "buy"
            notional = data["buy_notional"]
        else:
            direction = "sell"
            notional = data["sell_notional"]
        
        fl = data["flow_label"]
        if not fl or fl == 'unknown':
            fl = classify_fn(direction, data["option_type"], float(data["delta"]), data["strike"], spot)
        
        entry = {
            "instrument_name": inst, "direction": direction,
            "notional_usd": round(float(notional), 2),
            "volume": data["total_volume"],
            "strike": data["strike"], "option_type": data["option_type"], "flow_label": fl,
            "delta": data["delta"],
            "premium_usd": data["premium_usd"],
            "severity": data["severity"],
            "buy_notional": round(data["buy_notional"], 2),
            "sell_notional": round(data["sell_notional"], 2),
            "buy_count": data["buy_count"],
            "sell_count": data["sell_count"]
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
    """从 Deribit API 补充数据并合并
    
    重要：累加同一合约的买卖数据，而不是覆盖
    """
    # 保留原有的 DB 结果
    results = list(results_by_inst.values())
    
    # 先汇总 API 数据（按合约 + 方向）
    api_data = {}
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

        if inst not in api_data:
            api_data[inst] = {
                "instrument_name": inst,
                "strike": meta.strike,
                "option_type": meta.option_type,
                "buy_notional": 0,
                "sell_notional": 0,
                "buy_volume": 0,
                "sell_volume": 0,
                "premium_usd": 0,
                "delta": 0,
                "iv": 0,
                "is_block": False,
            }
        
        if direction == "buy":
            api_data[inst]["buy_notional"] += notional_usd
            api_data[inst]["buy_volume"] += trade_amount
        else:
            api_data[inst]["sell_notional"] += notional_usd
            api_data[inst]["sell_volume"] += trade_amount
        
        api_data[inst]["premium_usd"] += premium_usd
        api_data[inst]["is_block"] = api_data[inst]["is_block"] or t.get("block_trade", False) or t.get("block_trade_id") is not None
    
    # 合并到现有结果
    for inst, data in api_data.items():
        total_notional = data["buy_notional"] + data["sell_notional"]
        if total_notional < MIN_NOTIONAL:
            continue
        
        # 确定主导方向
        if data["buy_notional"] > data["sell_notional"]:
            direction = "buy"
            dominant_notional = data["buy_notional"]
            dominant_volume = data["buy_volume"]
        else:
            direction = "sell"
            dominant_notional = data["sell_notional"]
            dominant_volume = data["sell_volume"]
        
        trade_iv = float(data.get("iv") or 50)
        delta_val = abs(calc_delta_fn(data["strike"], spot, trade_iv, meta.dte, data["option_type"]))
        fl = classify_fn(direction, data["option_type"], delta_val, data["strike"], spot)
        
        if inst in results_by_inst:
            # 累加到现有条目
            db_entry = results_by_inst[inst]
            db_entry["buy_notional"] = db_entry.get("buy_notional", 0) + data["buy_notional"]
            db_entry["sell_notional"] = db_entry.get("sell_notional", 0) + data["sell_notional"]
            db_entry["volume"] = db_entry.get("volume", 0) + data["buy_volume"] + data["sell_volume"]
            
            # 重新确定主导方向
            if db_entry["buy_notional"] > db_entry["sell_notional"]:
                db_entry["direction"] = "buy"
                db_entry["notional_usd"] = round(db_entry["buy_notional"], 2)
            else:
                db_entry["direction"] = "sell"
                db_entry["notional_usd"] = round(db_entry["sell_notional"], 2)
            
            if not db_entry.get('premium_usd'):
                db_entry['premium_usd'] = round(data["premium_usd"], 2)
            if not db_entry.get('iv'):
                db_entry['iv'] = round(trade_iv * 100, 1)
            if not db_entry.get('is_block'):
                db_entry['is_block'] = data["is_block"]
        else:
            seen.add(inst)
            api_entry = {
                "instrument_name": inst, "direction": direction,
                "notional_usd": round(dominant_notional, 2),
                "premium_usd": round(data["premium_usd"], 2),
                "volume": round(dominant_volume, 4),
                "strike": data["strike"],
                "option_type": data["option_type"],
                "flow_label": fl,
                "delta": delta_val,
                "iv": round(trade_iv, 1),
                "is_block": data["is_block"],
                "buy_notional": data["buy_notional"],
                "sell_notional": data["sell_notional"],
                "buy_count": 1 if data["buy_notional"] > 0 else 0,
                "sell_count": 1 if data["sell_notional"] > 0 else 0,
            }
            results.append(api_entry)
            results_by_inst[inst] = api_entry

        if len(results) >= limit:
            break
    return results


def _finalize_results(results: List[dict], limit: int, severity_fn, risk_emoji_fn) -> List[dict]:
    """填充 severity/risk_level 并排序截断"""
    # 过滤掉 notional_usd 为 None 或 0 的无效数据
    valid_results = []
    for t in results:
        notional = t.get("notional_usd")
        if notional is None or notional <= 0:
            continue
        if not t.get("severity"):
            t["severity"] = severity_fn(notional)
        t["risk_level"] = risk_emoji_fn(abs(t.get("delta", 0) or 0))
        valid_results.append(t)
    
    valid_results.sort(key=lambda x: x.get("notional_usd", 0), reverse=True)
    return valid_results[:limit]


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
        except Exception as e:
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
