"""
期权扫描引擎 - 核心扫描逻辑
从 main.py 提取，消除 api/scan.py 反向 import main 的循环依赖
"""

import os
import sys
import json
import sqlite3
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import config
from models.contracts import ScanParams, QuickScanParams
from services.dvol_analyzer import adapt_params_by_dvol, calc_delta_bs, calc_pop, get_dvol_from_deribit, _get_dvol_simple_fallback
from services.instrument import _parse_inst_name
from services.risk_framework import RiskFramework, CalculationEngine, _risk_emoji
from services.margin_calculator import calc_margin
from services.flow_classifier import _classify_flow_heuristic, parse_trade_alert, _severity_from_notional, get_flow_label_info
from services.spot_price import get_spot_price, get_spot_price_async, get_spot_price_binance, get_spot_price_deribit, _get_spot_from_scan
from services.trades import generate_wind_sentiment, fetch_deribit_summaries
from db.connection import get_db_connection as _db_conn, execute_read, execute_write, execute_transaction
from constants import get_spot_fallback


def get_db_connection(read_only: bool = True):
    """获取数据库连接（默认只读）"""
    return _db_conn(read_only=read_only)


def save_scan_record(data: Dict[str, Any]):
    """保存扫描记录到数据库（使用 execute_transaction 保证 _write_lock 和原子性）"""
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    large_trades = data.get('large_trades_details', []) or data.get('large_trades', [])
    contracts = data.get('contracts', [])
    currency = data.get('currency', 'BTC')

    stmts = []

    # 1. 插入 scan_records
    stmts.append(("""
        INSERT INTO scan_records
        (currency, spot_price, dvol_current, dvol_z_score, dvol_signal,
         large_trades_count, large_trades_details, contracts_data, top_contracts_data, raw_output)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        currency,
        data.get('spot_price', 0),
        data.get('dvol_current', 0),
        data.get('dvol_z_score', 0),
        data.get('dvol_signal', ''),
        data.get('large_trades_count', 0),
        json.dumps(large_trades, ensure_ascii=False),
        json.dumps(contracts, ensure_ascii=False),
        json.dumps(contracts[:30], ensure_ascii=False),
        json.dumps({"dvol_raw": data.get('dvol_raw', {}), "trend": data.get('dvol_trend', ''), "trend_label": data.get('dvol_trend_label', ''), "confidence": data.get('dvol_confidence', ''), "interpretation": data.get('dvol_interpretation', '')}, ensure_ascii=False)
    )))

    # 2. 插入 large_trades_history
    if large_trades and isinstance(large_trades, list):
        for trade in large_trades:
            parsed = parse_trade_alert(trade, currency, now_str)
            stmts.append(("""
                INSERT INTO large_trades_history
                (timestamp, currency, source, title, message, direction, strike, volume,
                 option_type, flow_label, notional_usd, delta, instrument_name, premium_usd, severity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                parsed['timestamp'], parsed['currency'], parsed['source'],
                parsed['title'], parsed['message'], parsed['direction'],
                parsed['strike'], parsed['volume'], parsed['option_type'],
                parsed['flow_label'], parsed['notional_usd'], parsed['delta'],
                parsed['instrument_name'], parsed.get('premium_usd', 0), parsed.get('severity', '')
            )))

    # 3. 清理过期数据
    _cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime('%Y-%m-%d %H:%M:%S')
    stmts.append(("DELETE FROM scan_records WHERE timestamp < ?", (_cutoff,)))
    stmts.append(("DELETE FROM large_trades_history WHERE timestamp < ?", (_cutoff,)))

    execute_transaction(stmts)


import asyncio
import logging
import httpx
from concurrent.futures import ThreadPoolExecutor
from scipy import interpolate

# 全局 ThreadPoolExecutor 实例（避免每次扫描创建新线程池）
_scan_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="scan_engine")

from services.large_trades_fetcher import fetch_large_trades_sync, fetch_large_trades_async

logger = logging.getLogger(__name__)


def _get_deribit_monitor():
    """获取 DeribitOptionsMonitor 单例"""
    cache = getattr(_get_deribit_monitor, '_cache', None)
    if cache is None:
        cache = {}
        setattr(_get_deribit_monitor, '_cache', cache)
    if 'mon' not in cache:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'deribit-options-monitor'))
        from deribit_options_monitor import DeribitOptionsMonitor
        cache['mon'] = DeribitOptionsMonitor()
    return cache['mon']


def _format_scan_report(currency: str, dvol_res: Any, trades_res: Any, der_res: Any) -> Dict[str, Any]:
    """内联 format_report 逻辑，替代 options_aggregator 依赖"""
    contracts = []
    if isinstance(der_res, dict):
        contracts = der_res.get('contracts', []) or der_res.get('opportunities', [])
    elif isinstance(der_res, list):
        contracts = der_res

    large_trades = []
    if isinstance(trades_res, list):
        large_trades = trades_res
    elif isinstance(trades_res, dict):
        large_trades = trades_res.get('alerts', []) or trades_res.get('trades', [])

    dvol_current = 50.0
    dvol_z_score = 0.0
    dvol_signal = "NEUTRAL"
    if isinstance(dvol_res, dict):
        dvol_current = dvol_res.get('current', 50.0)
        dvol_z_score = dvol_res.get('z_score', 0.0)
        dvol_signal = dvol_res.get('signal', 'NEUTRAL')

    return {
        "currency": currency,
        "contracts": contracts,
        "contracts_count": len(contracts),
        "large_trades_count": len(large_trades),
        "large_trades_details": large_trades,
        "dvol_current": dvol_current,
        "dvol_z_score": dvol_z_score,
        "dvol_signal": dvol_signal,
        "dvol_raw": dvol_res if isinstance(dvol_res, dict) else {},
    }


def run_options_scan(params: ScanParams) -> Dict[str, Any]:
    import warnings
    warnings.warn(
        "/api/scan is deprecated - use /api/quick-scan for better performance",
        DeprecationWarning, stacklevel=2
    )

    base_dir = Path(__file__).parent.parent
    sys.path.insert(0, str(base_dir))

    spot_price = get_spot_price(params.currency)
    dvol_data = get_dvol_from_deribit(params.currency)
    dvol_raw_for_adapt = dvol_data if isinstance(dvol_data, dict) else {}

    scan_params = {
        "max_delta": params.max_delta, "min_dte": params.min_dte,
        "max_dte": params.max_dte, "margin_ratio": params.margin_ratio,
        "option_type": params.option_type, "min_apr": 15.0
    }
    adapted = adapt_params_by_dvol(scan_params, dvol_raw_for_adapt)

    use_delta = adapted.get('max_delta', params.max_delta)
    use_min_dte = adapted.get('min_dte', params.min_dte)
    use_max_dte = adapted.get('max_dte', params.max_dte)
    use_margin = adapted.get('margin_ratio', params.margin_ratio)

    try:
        mon = _get_deribit_monitor()

        executor = _scan_executor
        f_dvol = executor.submit(mon.get_dvol_signal, params.currency)
        f_trades = executor.submit(mon.get_large_trade_alerts, currency=params.currency, min_usd_value=200000)

        dvol_res = f_dvol.result(timeout=60)
        trades_res = f_trades.result(timeout=60)

        def _run_deribit():
            return mon.scan_options(
                currency=params.currency, option_type=params.option_type,
                min_dte=use_min_dte, max_dte=use_max_dte,
                max_delta=use_delta, margin_ratio=use_margin
            )

        f_der = executor.submit(_run_deribit)
        der_res = f_der.result(timeout=60)

        # 内联 format_report 逻辑
        parsed = _format_scan_report(params.currency, dvol_res, trades_res, der_res)

        parsed['success'] = True
        if spot_price:
            parsed['spot_price'] = spot_price
        if isinstance(dvol_data, dict) and dvol_data.get('current'):
            parsed['dvol_current'] = dvol_data['current']
            parsed['dvol_z_score'] = dvol_data['z_score']
            parsed['dvol_signal'] = dvol_data['signal']
            parsed['dvol_trend'] = dvol_data.get('trend', '')
            parsed['dvol_trend_label'] = dvol_data.get('trend_label', '')
            parsed['dvol_confidence'] = dvol_data.get('confidence', '')
            parsed['dvol_interpretation'] = dvol_data.get('interpretation', '')

        save_scan_record(parsed)

        parsed['dvol_advice'] = adapted.get('_dvol_advice', [])
        parsed['dvol_adjustment'] = adapted.get('_adjustment_level', 'none')
        parsed['adapted_params'] = {
            'max_delta': use_delta, 'min_dte': use_min_dte, 'max_dte': use_max_dte
        }

        return parsed

    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
        logger.error("scan adapter failed: %s", str(e), exc_info=True)
        return {"success": False, "error": "参数适配失败，请检查输入参数"}


async def quick_scan(params: QuickScanParams = None):
    """
    快速扫描（DataHub 优化版）
    """
    from datetime import datetime, timezone
    _p = params or QuickScanParams()
    currency = _p.currency

    spot = None
    dvol_data = {}
    summaries = []
    large_trades = []
    binance_contracts = []

    try:
        from services.datahub import datahub, TOPIC_SPOT, TOPIC_DVOL, TOPIC_BTC_OPTIONS, TOPIC_ETH_OPTIONS

        spot_snapshot = datahub.get_snapshot(TOPIC_SPOT, currency)
        if spot_snapshot:
            spot = spot_snapshot.get("price")
            spot_age = datahub.get_snapshot_age(TOPIC_SPOT)
            if spot_age > 30:
                logger.info("DataHub spot data too old (%.1fs), falling back to REST", spot_age)
                spot = None

        dvol_snapshot = datahub.get_snapshot(TOPIC_DVOL, currency)
        if dvol_snapshot:
            dvol_age = datahub.get_snapshot_age(TOPIC_DVOL)
            if dvol_age < 30:
                dvol_data = {
                    "current": dvol_snapshot.get("current", 0),
                    "z_score": 0,
                    "signal": "normal"
                }

        options_topic = TOPIC_BTC_OPTIONS if currency == "BTC" else TOPIC_ETH_OPTIONS
        options_snapshot = datahub.get_snapshot(options_topic)

        if options_snapshot:
            options_age = datahub.get_snapshot_age(options_topic)
            if options_age < 30 and len(options_snapshot) > 0:
                logger.info("DataHub scan: %d options from WebSocket cache (%.1fs old)", len(options_snapshot), options_age)
                summaries = []
                for symbol, opt_data in options_snapshot.items():
                    inst_meta = _parse_inst_name(symbol)
                    if not inst_meta:
                        continue
                    summaries.append({
                        "instrument_name": opt_data.get("symbol", symbol),
                        "mark_price": opt_data.get("mark_price", 0),
                        "mark_iv": opt_data.get("iv", 0) * 100 if opt_data.get("iv", 0) <= 1 else opt_data.get("iv", 0),
                        "delta": opt_data.get("delta", 0),
                        "gamma": opt_data.get("gamma", 0),
                        "theta": opt_data.get("theta", 0),
                        "vega": opt_data.get("vega", 0),
                        "best_bid_amount": opt_data.get("best_bid", 0),
                        "best_ask_amount": opt_data.get("best_ask", 0),
                        "open_interest": opt_data.get("open_interest", 0),
                        "stats": {"volume": opt_data.get("volume", 0)},
                        "underlying_price": spot
                    })
                binance_contracts = []
            else:
                logger.info("DataHub options data too old (%.1fs), falling back to REST", options_age)
    except ImportError:
        logger.debug("DataHub not available, using REST fallback")
    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
        logger.debug("DataHub read failed: %s, using REST fallback", str(e))

    if not spot:
        logger.info("Quick scan: DataHub not ready, using REST fallback for spot price")
        try:
            spot = await get_spot_price_async(currency)
        except (RuntimeError, ValueError) as e:
            logger.warning("Quick scan: Spot price fallback failed: %s", e)
            spot = 0

    if spot is None or spot <= 0:
        logger.error("Quick scan: Failed to get valid spot price, aborting scan")
        return {"error": "无法获取现货价格", "currency": currency}

    if not summaries:
        logger.info("Quick scan: DataHub not ready, fetching Deribit via REST")
        try:
            summaries = await asyncio.to_thread(fetch_deribit_summaries, currency)
        except (RuntimeError, ConnectionError, TimeoutError) as e:
            logger.error("Quick scan: Failed to fetch Deribit summaries: %s", e)
            summaries = []

    if not large_trades:
        try:
            large_trades = await _fetch_large_trades_async(currency, days=1, limit=40)
        except (RuntimeError, ConnectionError, TimeoutError) as e:
            logger.warning("Quick scan: Large trades fetch failed: %s", e)
            large_trades = []

    if not binance_contracts:
        logger.info("Quick scan: DataHub not ready, fetching Binance via REST")
        try:
            from binance_options import fetch_binance_options
            binance_contracts = await asyncio.to_thread(
                fetch_binance_options,
                currency=currency,
                min_dte=_p.min_dte,
                max_dte=_p.max_dte,
                max_delta=_p.max_delta,
                strike=_p.strike,
                min_vol=config.MIN_VOLUME_FILTER,
                max_spread=config.MAX_SPREAD_PCT,
                margin_ratio=_p.margin_ratio,
                option_type=_p.option_type
            )
            if not isinstance(binance_contracts, list):
                binance_contracts = []
        except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
            logger.warning("binance_options fetch failed: %s", str(e))
            binance_contracts = []

    _min_spot = {"BTC": 1000, "ETH": 100, "SOL": 10, "XRP": 0.5}.get(currency, 100)
    if not spot or spot < _min_spot:
        raise RuntimeError("[CRITICAL] quick_scan: cannot obtain spot price, scan aborted")

    if not dvol_data or not dvol_data.get('current'):
        try:
            dvol_data = await asyncio.to_thread(get_dvol_from_deribit, currency)
            if not dvol_data:
                dvol_data = {}
        except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
            logger.warning("Failed to fetch DVOL from Deribit: %s", str(e))
            dvol_data = {}

    dvol_current = dvol_data.get('current', 0) or 0
    dvol_z = dvol_data.get('z_score', 0) or 0
    dvol_signal = dvol_data.get('signal', '正常区间')

    use_min_dte = _p.min_dte
    use_max_dte = _p.max_dte

    dvol_pct = 50
    if abs(dvol_z) > 0:
        try:
            from scipy.stats import norm
            dvol_pct = round(norm.cdf(dvol_z) * 100, 1)
        except (ImportError, ValueError) as e:
            logger.debug("scipy norm.cdf failed: %s, using linear fallback", e)
            dvol_pct = round(50 + dvol_z * 20, 1)
            dvol_pct = max(1, min(99, dvol_pct))

    contracts = []
    floors = RiskFramework._get_floors()
    regular_floor = floors.get("regular", 0)
    extreme_floor = floors.get("extreme", 0)

    if summaries:
        for s in summaries:
            meta = _parse_inst_name(s.get("instrument_name", ""))
            if not meta: continue
            if meta.dte < use_min_dte or meta.dte > use_max_dte: continue

            req_type = _p.option_type.upper()
            req_type_short = "P" if req_type == "PUT" else "C"
            fetch_all = req_type in ("ALL", "BOTH")
            if not fetch_all and meta.option_type != req_type_short: continue

            iv = float(s.get("mark_iv") or 0)
            prem = float(s.get("mark_price") or 0)
            oi = float(s.get("open_interest") or 0)
            if iv <= 0 or prem <= 0 or oi < 10: continue

            strike = meta.strike
            underlying = float(s.get("underlying_price", spot)) or spot

            raw_delta = s.get("delta")
            if raw_delta is None or float(raw_delta or 0) == 0:
                delta_val = abs(calc_delta_bs(strike, underlying, iv, meta.dte, meta.option_type))
            else:
                delta_val = abs(float(raw_delta))

            max_delta = _p.max_delta
            if isinstance(dvol_pct, (int, float)) and dvol_pct >= 80:
                max_delta = max_delta * 0.7
            elif isinstance(dvol_pct, (int, float)) and dvol_pct <= 20:
                max_delta = min(max_delta * 1.2, 0.55)

            if delta_val > max_delta: continue
            if _p.strike and abs(strike - _p.strike) > 0.5: continue

            prem_usd = prem * underlying
            margin_ratio = _p.margin_ratio
            cv = strike * margin_ratio
            apr = (prem_usd / cv) * (365 / meta.dte) * 100 if cv > 0 else 0

            dist = abs(strike - spot) / spot * 100

            contracts.append({
                "symbol": s.get("instrument_name", ""),
                "platform": "Deribit",
                "expiry": meta.expiry,
                "dte": meta.dte,
                "option_type": meta.option_type,
                "strike": strike,
                "apr": round(apr, 1),
                "premium_usd": round(prem_usd, 2),
                "delta": round(delta_val, 3),
                "theta": round(float(s.get("theta", 0) or 0), 4),
                "gamma": round(float(s.get("gamma", 0) or 0), 6),
                "vega": round(float(s.get("vega", 0) or 0), 4),
                "iv": round(iv, 1),
                "open_interest": round(oi, 0),
                "loss_at_10pct": round(max(0, (strike - spot * 0.9) if meta.option_type == "P" else (spot * 1.1 - strike)), 2),
                "breakeven": round(strike - prem_usd if meta.option_type == "P" else strike + prem_usd, 0),
                "distance_spot_pct": round(dist, 1),
                "support_distance_pct": round((strike - regular_floor) / regular_floor * 100, 1) if regular_floor > 0 and meta.option_type == "P" else None,
                "margin_required": round(calc_margin(strike, prem_usd, meta.option_type, margin_ratio), 2),
                "capital_efficiency": round(prem_usd / calc_margin(strike, prem_usd, meta.option_type, margin_ratio) * 100, 1) if cv > 0 else 0,
                "spread_pct": 0.1,
                "breakeven_pct": CalculationEngine.calc_breakeven_pct(strike, prem_usd, meta.option_type, spot),
                "pop": calc_pop(delta_val, meta.option_type, spot, strike, iv, meta.dte),
                "iv_rank": round(dvol_pct, 1) if isinstance(dvol_pct, (int,float)) else None,
                "liquidity_score": min(100, int((oi / 500) * 100))
            })

    if isinstance(binance_contracts, list):
        for s in binance_contracts:
            if not isinstance(s, dict):
                continue
            strike = s.get('strike', 0)
            prem_usd = s.get('premium_usdt', 0)
            dte = s.get('dte', 0)
            delta_val = s.get('delta', 0)
            iv = s.get('mark_iv', 0)
            volume = s.get('volume', 0)
            oi = s.get('oi', 0)
            spread_pct = s.get('spread_pct', 0)
            opt_type = 'P' if 'P' in s.get('symbol', '').upper() else 'C'
            margin_ratio = _p.margin_ratio
            cv = strike * margin_ratio
            apr = s.get('apr', 0)
            dist = abs(strike - spot) / spot * 100
            liq_score = s.get('liquidity_score', 50)

            contracts.append({
                "symbol": s['symbol'],
                "platform": "Binance",
                "expiry": s['symbol'].split('-')[1] if '-' in s.get('symbol', '') else '',
                "dte": round(dte, 1),
                "option_type": opt_type,
                "strike": strike,
                "apr": round(apr, 1),
                "premium_usd": round(prem_usd, 2),
                "delta": round(abs(delta_val), 3),
                "gamma": round(s.get('gamma', 0), 6),
                "theta": round(s.get('theta', 0), 4),
                "vega": round(s.get('vega', 0), 4),
                "iv": round(iv, 1),
                "open_interest": round(oi, 0),
                "loss_at_10pct": round(max(0, (strike - spot * 0.9) if opt_type == "P" else (spot * 1.1 - strike)), 2),
                "breakeven": round(strike - prem_usd if opt_type == 'P' else strike + prem_usd, 0),
                "distance_spot_pct": round(dist, 1),
                "support_distance_pct": round((strike - regular_floor) / regular_floor * 100, 1) if regular_floor > 0 and opt_type == "P" else None,
                "margin_required": round(calc_margin(strike, prem_usd, opt_type, margin_ratio), 2),
                "capital_efficiency": round(prem_usd / calc_margin(strike, prem_usd, opt_type, margin_ratio) * 100, 1) if cv > 0 else 0,
                "spread_pct": round(spread_pct, 2),
                "breakeven_pct": CalculationEngine.calc_breakeven_pct(strike, prem_usd, opt_type, spot),
                "pop": calc_pop(abs(delta_val or 0), opt_type, spot, strike, iv, int(dte)),
                "iv_rank": round(dvol_pct, 1) if isinstance(dvol_pct, (int,float)) else None,
                "liquidity_score": int(liq_score)
            })

    def _normalize_liquidity(ct):
        platform = ct.get("platform", "")
        base = ct.get("liquidity_score", 0)
        if platform == "Binance":
            oi_factor = min(2.0, 1.0 + (ct.get("open_interest", 0) / 200))
            spread_penalty = max(0.5, 1.0 - ct.get("spread_pct", 0) / 20)
            return min(100, int(base * oi_factor * spread_penalty))
        return base

    def _weighted_score(ct):
        score = CalculationEngine.weighted_score(
            apr=ct.get("apr", 0),
            pop=ct.get("pop", 50),
            breakeven_pct=ct.get("breakeven_pct", 0),
            liquidity_score=_normalize_liquidity(ct),
            iv_rank=ct.get("iv_rank", 50),
            strike=ct.get("strike", 0),
            spot=spot
        )
        ct["_score"] = score
        return score

    all_c = sorted(contracts, key=_weighted_score, reverse=True)
    deribit_list = [c for c in all_c if c.get("platform") == "Deribit"][:15]
    binance_list = [c for c in all_c if c.get("platform") == "Binance"][:15]

    contracts = []
    for i in range(max(len(deribit_list), len(binance_list))):
        if i < len(deribit_list): contracts.append(deribit_list[i])
        if i < len(binance_list): contracts.append(binance_list[i])

    large_trades_count = len(large_trades)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # 使用 execute_transaction 保证 _write_lock 和原子性
    _raw_out = json.dumps({
        "dvol_raw": dvol_data, "trend": dvol_data.get("trend", ""),
        "trend_label": dvol_data.get("trend_label", ""),
        "confidence": dvol_data.get("confidence", ""),
        "interpretation": dvol_data.get("interpretation", "")
    }, ensure_ascii=False)

    stmts = []
    stmts.append(("""
        INSERT INTO scan_records (timestamp, currency, spot_price, dvol_current, dvol_z_score,
            dvol_signal, large_trades_count, large_trades_details, contracts_data, top_contracts_data, raw_output)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (timestamp, currency, spot, dvol_current, dvol_z, dvol_signal, large_trades_count,
          json.dumps(large_trades[:20]), json.dumps(contracts[:30]), json.dumps(contracts[:30]), _raw_out)))

    if large_trades and isinstance(large_trades, list):
        for trade in large_trades:
            parsed = parse_trade_alert(trade, currency, timestamp)
            stmts.append(("""
                INSERT INTO large_trades_history
                (timestamp, currency, source, title, message, direction, strike, volume,
                 option_type, flow_label, notional_usd, delta, instrument_name, premium_usd, severity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                parsed['timestamp'], parsed['currency'], parsed['source'],
                parsed['title'], parsed['message'], parsed['direction'],
                parsed['strike'], parsed['volume'], parsed['option_type'],
                parsed['flow_label'], parsed['notional_usd'], parsed['delta'],
                parsed['instrument_name'], parsed.get('premium_usd', 0), parsed.get('severity', '')
            )))

    execute_transaction(stmts)

    return {
        "success": True,
        "contracts_count": len(contracts),
        "spot_price": spot,
        "timestamp": timestamp,
        "contracts": contracts[:30],
        "dvol_current": dvol_current,
        "dvol_z_score": dvol_z,
        "dvol_signal": dvol_signal,
        "dvol_trend": dvol_data.get("trend", ""),
        "dvol_trend_label": dvol_data.get("trend_label", ""),
        "dvol_confidence": dvol_data.get("confidence", ""),
        "dvol_interpretation": dvol_data.get("interpretation", ""),
        "dvol_percentile_7d": dvol_data.get("percentile_7d", None),
        "large_trades_count": large_trades_count,
        "large_trades_details": large_trades[:20]
    }


async def _fetch_large_trades_async(currency: str, days: int = 7, limit: int = 50):
    """异步获取大单交易：委托给 large_trades_fetcher"""
    return await fetch_large_trades_async(currency, days, limit)


def _fetch_large_trades(currency: str, days: int = 7, limit: int = 50):
    """同步获取大单交易：委托给 large_trades_fetcher"""
    return fetch_large_trades_sync(currency, days, limit)


def _fetch_wind_analysis(currency: str, days: int = 30):
    """获取风向分析数据"""
    from services.trades import fetch_deribit_summaries
    from services.instrument import _parse_inst_name

    spot = get_spot_price(currency)
    if not spot:
        spot = get_spot_fallback(currency)

    summaries = fetch_deribit_summaries(currency)
    if not summaries:
        return {"error": "无法获取Deribit数据", "buy_ratio": 0.5, "dominant_flow": "unknown"}

    buy_puts = sell_puts = buy_calls = sell_calls = 0
    total_premium = 0
    put_premiums = []

    for s in summaries:
        meta = _parse_inst_name(s.get("instrument_name", ""))
        if not meta or meta.dte < 1:
            continue
        oi = float(s.get("open_interest") or 0)
        iv = float(s.get("mark_iv") or 0)
        if oi < 10 or iv < 10:
            continue

        delta = float(s.get("delta") or 0)
        if abs(delta) < 0.01:
            continue

        if meta.option_type == "P":
            if delta < 0:
                buy_puts += oi
            else:
                sell_puts += oi
        else:
            if delta > 0:
                buy_calls += oi
            else:
                sell_calls += oi

        prem = float(s.get("mark_price") or 0) * oi
        total_premium += prem
        if meta.option_type == "P":
            put_premiums.append(prem)

    total_oi = buy_puts + sell_puts + buy_calls + sell_calls
    if total_oi <= 0:
        return {"error": "No valid OI data", "buy_ratio": 0.5, "dominant_flow": "unknown"}

    buy_ratio = (buy_puts + buy_calls) / total_oi
    put_call_ratio = (buy_puts + sell_puts) / (buy_calls + sell_calls) if (buy_calls + sell_calls) > 0 else 1.0

    sentiment_score = 50
    if buy_ratio > 0.6:
        sentiment_score = 30
    elif buy_ratio < 0.4:
        sentiment_score = 70

    dominant = "neutral"
    if put_call_ratio > 1.2 and buy_puts > sell_puts * 1.5:
        dominant = "panic_buy_puts"
    elif put_call_ratio > 1.2 and sell_puts > buy_puts * 1.5:
        dominant = "aggressive_sell_puts"
    elif put_call_ratio < 0.8 and buy_calls > sell_calls * 1.5:
        dominant = "fomo_buy_calls"
    elif put_call_ratio < 0.8 and sell_calls > buy_calls * 1.5:
        dominant = "covered_call_selling"
    elif buy_ratio > 0.55:
        dominant = "bullish"
    elif buy_ratio < 0.45:
        dominant = "bearish"

    try:
        spot = get_spot_price(currency)
    except (RuntimeError, ValueError) as e:
        logger.warning("Wind analysis spot price failed: %s", e)
        spot = 0

    return {
        "currency": currency, "spot": spot, "days": days,
        "buy_ratio": round(buy_ratio, 3), "dominant_flow": dominant,
        "risk_level": RiskFramework.get_status(spot),
        "sentiment_score": sentiment_score,
        "sentiment_text": dominant,
        "summary": {"total_trades": 0, "buy_puts": buy_puts,
                    "sell_calls": sell_calls, "buy_calls": buy_calls, "sell_puts": sell_puts}
    }


def _fetch_term_structure(currency: str):
    """同步获取 IV Term Structure 数据"""
    from services.trades import fetch_deribit_summaries
    from services.instrument import _parse_inst_name

    try:
        spot = get_spot_price(currency)
    except (RuntimeError, ValueError) as e:
        logger.warning("Term structure spot price failed: %s, using fallback", e)
        spot = get_spot_fallback(currency)

    summaries = fetch_deribit_summaries(currency)
    if not summaries:
        return {"error": "无法获取Deribit数据", "surface": [], "term_structure": [], "backwardation": False}

    parsed = []
    for s in summaries:
        meta = _parse_inst_name(s.get("instrument_name", ""))
        if not meta or meta.dte < 1:
            continue
        iv = float(s.get("mark_iv") or 0)
        oi = float(s.get("open_interest") or 0)
        if iv < 10 or oi < 10:
            continue
        parsed.append({"strike": meta.strike, "expiry": meta.expiry, "dte": meta.dte,
                       "option_type": meta.option_type, "iv": iv, "oi": oi})

    if not parsed:
        return {"error": "No valid IV data", "surface": [], "term_structure": [], "backwardation": False}

    expiries = {}
    for p in parsed:
        key = p["expiry"]
        if key not in expiries:
            expiries[key] = {"dte": p["dte"], "expiry": p["expiry"], "strikes": []}
        expiries[key]["strikes"].append({"strike": p["strike"], "iv": p["iv"], "oi": p["oi"]})

    expiry_data = sorted(expiries.values(), key=lambda x: x["dte"])

    atm_ivs = []
    dtes = []
    for ed in expiry_data:
        strikes = sorted(ed["strikes"], key=lambda x: abs(x["strike"] - spot))
        atm_iv = None
        if strikes:
            atm_iv = strikes[0]["iv"]
            for s in strikes[:3]:
                if s["iv"] > 0:
                    atm_iv = s["iv"]
                    break
        atm_ivs.append(atm_iv)
        dtes.append(ed["dte"])

    term_structure = []
    for i, ed in enumerate(expiry_data):
        atm = atm_ivs[i] if i < len(atm_ivs) else None
        term_structure.append({"dte": ed["dte"], "avg_iv": atm, "expiry": ed["expiry"]})

    if len(term_structure) >= 3:
        ivs = [t["avg_iv"] for t in term_structure]
        valid_ivs = [(i, iv) for i, iv in enumerate(ivs) if iv is not None]
        if len(valid_ivs) >= 2:
            x = [v[0] for v in valid_ivs]
            y = [v[1] for v in valid_ivs]
            f = interpolate.interp1d(x, y, kind='linear', fill_value='extrapolate')
            for i in range(len(term_structure)):
                if term_structure[i]["avg_iv"] is None:
                    expected = float(f(i))
                    term_structure[i]["avg_iv"] = round(expected, 2)

    backwardation = False
    if len(term_structure) >= 2:
        front_iv = term_structure[0]["avg_iv"]
        back_iv = term_structure[-1]["avg_iv"]
        if front_iv is not None and back_iv is not None:
            backwardation = front_iv > back_iv * 1.05

    return {
        "currency": currency,
        "term_structure": term_structure,
        "backwardation": backwardation,
        "analysis": _get_iv_term_analysis(term_structure)
    }


def _get_iv_term_analysis(term_structure: list) -> dict:
    """获取 IV 期限结构分析"""
    if not term_structure or len(term_structure) < 2:
        return {"state": "unknown", "signal": "数据不足", "suggestion": ""}

    front_iv = term_structure[0].get("avg_iv")
    back_iv = term_structure[-1].get("avg_iv")

    if front_iv is None or back_iv is None:
        return {"state": "unknown", "signal": "数据不足", "suggestion": ""}

    if front_iv > back_iv * 1.05:
        return {"state": "backwardation", "signal": "近高远低结构", "suggestion": "建议卖出近月期权获取更高权利金"}
    elif front_iv < back_iv * 0.95:
        return {"state": "contango", "signal": "正常升水结构", "suggestion": "可正常布局远期策略"}
    else:
        return {"state": "flat", "signal": "期限结构平坦", "suggestion": "市场观望情绪浓厚"}
