"""
期权扫描引擎 - 核心扫描逻辑
从 main.py 提取，消除 api/scan.py 反向 import main 的循环依赖
"""

import json
import sqlite3
import math
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

from pydantic import BaseModel, field_validator

from config import config
from models.contracts import ScanParams, QuickScanParams
from services.dvol_analyzer import adapt_params_by_dvol, calc_delta_bs, calc_pop, get_dvol_from_deribit, _get_dvol_simple_fallback
from services.shared_calculations import black_scholes_price
from services.instrument import _parse_inst_name
from services.risk_framework import RiskFramework, CalculationEngine, _risk_emoji
from services.margin_calculator import calc_margin
from services.flow_classifier import _classify_flow_heuristic, parse_trade_alert, _severity_from_notional, get_flow_label_info

def _enrich_trades_with_flow(trades: list, currency: str) -> list:
    """为原始交易列表补充 flow_label（大宗异动/大单风向标依赖此字段）"""
    from services.spot_price import get_spot_price
    spot = get_spot_price(currency) or 0
    for t in trades:
        if not isinstance(t, dict):
            continue
        fl = t.get('flow_label', '')
        if fl and fl != 'unknown':
            continue
        direction = t.get('direction', '')
        opt_type = t.get('option_type', '')
        delta = float(t.get('delta', 0) or 0)
        strike = float(t.get('strike', 0) or 0)
        t['flow_label'] = _classify_flow_heuristic(direction, opt_type, delta, strike, spot)
    return trades
from services.spot_price import get_spot_price, get_spot_price_async, get_spot_price_binance, get_spot_price_deribit, _get_spot_from_scan
from services.trades import generate_wind_sentiment, fetch_deribit_summaries
from db.connection import get_db_connection as _db_conn, execute_read, execute_write, execute_transaction
from constants import get_spot_fallback


def _get_dvol_profile(dvol_current: float) -> dict:
    """根据 DVOL 当前值返回参数档位"""
    if dvol_current > config.DVOL_HIGH_THRESHOLD:
        return config.DVOL_PROFILES["high"]
    elif dvol_current < config.DVOL_LOW_THRESHOLD:
        return config.DVOL_PROFILES["low"]
    return config.DVOL_PROFILES["mid"]


def get_db_connection(read_only: bool = True):
    """获取数据库连接（默认只读）"""
    return _db_conn(read_only=read_only)


def _sanitize_raw_output(raw_data: dict) -> str:
    """
    对原始 API 响应进行脱敏处理
    移除可能包含敏感信息的字段，限制存储大小
    """
    if not isinstance(raw_data, dict):
        return ""
    # 只保留必要的分析字段，过滤掉可能敏感的原始响应
    safe_keys = {"trend", "trend_label", "confidence", "interpretation", "signal", "z_score"}
    sanitized = {k: v for k, v in raw_data.items() if k in safe_keys}
    output = json.dumps(sanitized, ensure_ascii=False, default=str)
    # 限制大小 50KB
    MAX_SIZE = 50000
    if len(output) > MAX_SIZE:
        output = output[:MAX_SIZE] + "...[truncated]"
    return output


class TradeAlertRecord(BaseModel):
    """Pydantic model 验证写入 large_trades_history 的字段"""
    timestamp: str
    currency: str
    source: str
    title: str = ""
    message: str = ""
    direction: str = ""
    strike: float = 0
    volume: float = 0
    option_type: str = ""
    flow_label: str = ""
    notional_usd: float = 0
    delta: float = 0
    instrument_name: str = ""
    premium_usd: float = 0
    severity: str = ""

    @field_validator("volume", "notional_usd", "premium_usd", "strike")
    @classmethod
    def must_be_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"must be non-negative, got {v}")
        return v


def _validate_trade(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Pydantic 校验交易记录字段，减少脏数据写入"""
    try:
        return TradeAlertRecord(**parsed).model_dump()
    except Exception as e:
        logger.warning("Trade record validation failed: %s — skipping", e)
        return None


def save_scan_record(data: Dict[str, Any]):
    """保存扫描记录到数据库（使用 execute_transaction 保证 _write_lock 和原子性）"""
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    large_trades = data.get('large_trades_details', []) or data.get('large_trades', [])
    contracts = data.get('contracts', [])
    currency = data.get('currency', 'BTC')
    # 补充 flow_label（上游数据不含此字段）
    _enrich_trades_with_flow(large_trades, currency)

    # 脱敏 raw_output
    dvol_raw = data.get('dvol_raw', {})
    raw_output = _sanitize_raw_output(dvol_raw)

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
        raw_output
    )))

    # 2. 插入 large_trades_history（Pydantic 校验）
    if large_trades and isinstance(large_trades, list):
        for trade in large_trades:
            parsed = parse_trade_alert(trade, currency, now_str)
            validated = _validate_trade(parsed)
            if validated is None:
                continue
            stmts.append(("""
                INSERT INTO large_trades_history
                (timestamp, currency, source, title, message, direction, strike, volume,
                 option_type, flow_label, notional_usd, delta, instrument_name, premium_usd, severity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                validated['timestamp'], validated['currency'], validated['source'],
                validated['title'], validated['message'], validated['direction'],
                validated['strike'], validated['volume'], validated['option_type'],
                validated['flow_label'], validated['notional_usd'], validated['delta'],
                validated['instrument_name'], validated['premium_usd'], validated['severity']
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
    """获取 DeribitOptionsMonitor 单例（统一到 services.monitors）"""
    from services.monitors import get_deribit_monitor
    return get_deribit_monitor()


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


def _apply_quality_filter(contracts: list, spot: float) -> list:
    """质量过滤：OI>=10, IV>0 — 不做 DTE/Delta 限制，保留全量数据供下游分析"""
    filtered = []
    for s in contracts:
        meta = _parse_inst_name(s.get("instrument_name", ""))
        if not meta or meta.dte < 1:
            continue
        iv = float(s.get("mark_iv") or 0)
        oi = float(s.get("open_interest") or 0)
        if iv <= 0 or oi < 10:
            continue
        strike = meta.strike
        underlying = float(s.get("underlying_price", spot)) or spot
        raw_delta = s.get("delta")
        if raw_delta is None or float(raw_delta or 0) == 0:
            delta_val = abs(calc_delta_bs(strike, underlying, iv, meta.dte, meta.option_type))
        else:
            delta_val = abs(float(raw_delta))
        prem = float(s.get("mark_price") or 0)
        prem_usd = prem * underlying
        dist = abs(strike - spot) / spot * 100
        margin_ratio = config.DEFAULT_MARGIN_RATIO
        cv = strike * margin_ratio
        apr = (prem_usd / cv) * (365 / meta.dte) * 100 if cv > 0 else 0
        bs_greeks = black_scholes_price(meta.option_type, strike, underlying, meta.dte, iv)
        filtered.append({
            "symbol": s.get("instrument_name", ""),
            "platform": "Deribit",
            "expiry": meta.expiry,
            "dte": meta.dte,
            "option_type": meta.option_type,
            "strike": strike,
            "apr": round(apr, 1),
            "premium_usd": round(prem_usd, 2),
            "delta": round(delta_val, 3),
            "theta": round(bs_greeks["theta"], 4),
            "gamma": round(bs_greeks["gamma"], 6),
            "vega": round(bs_greeks["vega"], 4),
            "iv": round(iv, 1),
            "open_interest": round(oi, 0),
            "distance_spot_pct": round(dist, 1),
        })
    return filtered


def _apply_strategy_filter(contracts: list, dvol_current: float, spot: float) -> list:
    """策略过滤：DVOL 自适应 DTE/Delta + 评分排序"""
    profile = _get_dvol_profile(dvol_current)
    max_delta = profile["max_delta"]
    min_dte = profile["min_dte"]
    max_dte = profile["max_dte"]

    filtered = []
    for c in contracts:
        if c["dte"] < min_dte or c["dte"] > max_dte:
            continue
        if c["delta"] > max_delta:
            continue
        c["_score"] = CalculationEngine.weighted_score(
            apr=c.get("apr", 0),
            pop=calc_pop(c["delta"], c["option_type"], spot, c["strike"], c["iv"], c["dte"]),
            breakeven_pct=c.get("distance_spot_pct", 0),
            liquidity_score=min(100, int((c.get("open_interest", 0) / 500) * 100)),
            iv_rank=50,
            strike=c["strike"],
            spot=spot
        )
        filtered.append(c)

    filtered.sort(key=lambda x: x.get("_score", 0), reverse=True)
    return filtered


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
                        "mark_price": opt_data.get("mark_price") or 0,
                        "mark_iv": (opt_data.get("iv") or 0) * 100 if (opt_data.get("iv") or 0) <= 1 else (opt_data.get("iv") or 0),
                        "delta": opt_data.get("delta") or 0,
                        "gamma": opt_data.get("gamma") or 0,
                        "theta": opt_data.get("theta") or 0,
                        "vega": opt_data.get("vega") or 0,
                        "best_bid_amount": opt_data.get("best_bid") or 0,
                        "best_ask_amount": opt_data.get("best_ask") or 0,
                        "open_interest": opt_data.get("open_interest") or 0,
                        "stats": {"volume": opt_data.get("volume") or 0},
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
            large_trades = await fetch_large_trades_async(currency, days=1, limit=40)
        except (RuntimeError, ConnectionError, TimeoutError) as e:
            logger.warning("Quick scan: Large trades fetch failed: %s", e)
            large_trades = []

    if not binance_contracts:
        logger.info("Quick scan: DataHub not ready, fetching Binance via REST")
        try:
            from binance_options import fetch_binance_options
            opt_type = _p.option_type.upper()
            if opt_type in ("ALL", "BOTH"):
                puts = await asyncio.to_thread(
                    fetch_binance_options, currency=currency,
                    min_dte=_p.min_dte, max_dte=_p.max_dte, max_delta=_p.max_delta,
                    strike=_p.strike, min_vol=config.MIN_VOLUME_FILTER,
                    max_spread=config.MAX_SPREAD_PCT, margin_ratio=_p.margin_ratio,
                    option_type="PUT"
                )
                calls = await asyncio.to_thread(
                    fetch_binance_options, currency=currency,
                    min_dte=_p.min_dte, max_dte=_p.max_dte, max_delta=_p.max_delta,
                    strike=_p.strike, min_vol=config.MIN_VOLUME_FILTER,
                    max_spread=config.MAX_SPREAD_PCT, margin_ratio=_p.margin_ratio,
                    option_type="CALL"
                )
                binance_contracts = (puts if isinstance(puts, list) else []) + (calls if isinstance(calls, list) else [])
            else:
                binance_contracts = await asyncio.to_thread(
                    fetch_binance_options,
                    currency=currency, min_dte=_p.min_dte, max_dte=_p.max_dte,
                    max_delta=_p.max_delta, strike=_p.strike,
                    min_vol=config.MIN_VOLUME_FILTER, max_spread=config.MAX_SPREAD_PCT,
                    margin_ratio=_p.margin_ratio, option_type=_p.option_type
                )
            if not isinstance(binance_contracts, list):
                binance_contracts = []
        except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError, ImportError) as e:
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

    quality_contracts = _apply_quality_filter(summaries, spot) if summaries else []

    if isinstance(binance_contracts, list):
        for s in binance_contracts:
            if not isinstance(s, dict):
                continue
            strike = s.get('strike', 0)
            prem_usd = s.get('premium_usdt', 0)
            dte = s.get('dte', 0)
            delta_val = s.get('delta', 0)
            iv = s.get('mark_iv', 0)
            oi = s.get('oi', 0)
            if iv <= 0:
                continue
            if oi > 0 and oi < 10:
                continue
            spread_pct = s.get('spread_pct', 0)
            opt_type = 'P' if 'P' in s.get('symbol', '').upper() else 'C'
            apr = s.get('apr', 0)
            dist = abs(strike - spot) / spot * 100
            bs_greeks_binance = black_scholes_price(opt_type, strike, spot, int(dte), iv) if iv > 0 and dte > 0 else {"gamma": 0, "theta": 0, "vega": 0}
            quality_contracts.append({
                "symbol": s['symbol'],
                "platform": "Binance",
                "expiry": s['symbol'].split('-')[1] if '-' in s.get('symbol', '') else '',
                "dte": round(dte, 1),
                "option_type": opt_type,
                "strike": strike,
                "apr": round(apr, 1),
                "premium_usd": round(prem_usd, 2),
                "delta": round(abs(delta_val), 3),
                "gamma": round(bs_greeks_binance.get("gamma", 0), 6),
                "theta": round(bs_greeks_binance.get("theta", 0), 4),
                "vega": round(bs_greeks_binance.get("vega", 0), 4),
                "iv": round(iv, 1),
                "open_interest": round(oi, 0),
                "distance_spot_pct": round(dist, 1),
            })

    strategy_contracts = _apply_strategy_filter(quality_contracts, dvol_current, spot)

    # 为大宗异动/大单风向标补充 flow_label（上游数据不含此字段）
    _enrich_trades_with_flow(large_trades, currency)

    large_trades_count = len(large_trades)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    _raw_out = _sanitize_raw_output(dvol_data)

    stmts = []
    stmts.append(("""
        INSERT INTO scan_records (timestamp, currency, spot_price, dvol_current, dvol_z_score,
            dvol_signal, large_trades_count, large_trades_details, contracts_data, top_contracts_data, raw_output)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (timestamp, currency, spot, dvol_current, dvol_z, dvol_signal, large_trades_count,
          json.dumps(large_trades[:20]), json.dumps(quality_contracts), json.dumps(strategy_contracts[:30]), _raw_out)))

    stmts.append(("""
        INSERT INTO dvol_history (timestamp, currency, current, z_score, signal, trend)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (timestamp, currency, dvol_current, dvol_z, dvol_signal, dvol_data.get("trend", ""))))

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
        "contracts_count": len(quality_contracts),
        "strategy_count": len(strategy_contracts[:30]),
        "spot_price": spot,
        "timestamp": timestamp,
        "contracts": strategy_contracts[:30],
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





def _fetch_wind_analysis(currency: str, days: int = 30):
    """获取风向分析数据 - 使用 large_trades_history 实际交易数据而非 OI 数据"""
    from db.connection import execute_read

    spot = get_spot_price(currency)
    if not spot:
        spot = get_spot_fallback(currency)

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    rows = execute_read("""
        SELECT direction, option_type, notional_usd, volume
        FROM large_trades_history
        WHERE currency = ? AND timestamp >= ?
    """, (currency, since))

    buy_puts = sell_puts = buy_calls = sell_calls = 0
    buy_put_notional = sell_put_notional = buy_call_notional = sell_call_notional = 0
    total_count = 0

    for row in rows:
        direction = (row[0] or '').lower()
        opt_type = (row[1] or 'PUT').upper()
        notional = float(row[2] or 0)
        volume = float(row[3] or 0)

        total_count += 1

        if opt_type in ('PUT', 'P'):
            if direction == 'buy':
                buy_puts += 1
                buy_put_notional += notional
            else:
                sell_puts += 1
                sell_put_notional += notional
        else:
            if direction == 'buy':
                buy_calls += 1
                buy_call_notional += notional
            else:
                sell_calls += 1
                sell_call_notional += notional

    if total_count <= 0:
        return {"error": "No valid trade data", "buy_ratio": 0.5, "bullish_ratio": 0.5, "dominant_flow": "unknown"}

    # 使用名义价值计算方向性比率
    # bullish = buy_call + sell_put (都预期上涨)
    # bearish = buy_put + sell_call (都预期下跌/对冲)
    total_buy_notional = buy_put_notional + buy_call_notional
    total_sell_notional = sell_put_notional + sell_call_notional
    total_notional = total_buy_notional + total_sell_notional

    bullish_notional = buy_call_notional + sell_put_notional
    bearish_notional = buy_put_notional + sell_call_notional
    bullish_ratio = bullish_notional / total_notional if total_notional > 0 else 0.5
    buy_ratio = total_buy_notional / total_notional if total_notional > 0 else 0.5

    # PCR = Put成交量 / Call成交量
    put_vol = buy_put_notional + sell_put_notional
    call_vol = buy_call_notional + sell_call_notional
    pcr = put_vol / call_vol if call_vol > 0 else 1.0

    sentiment_score = round((bullish_ratio - 0.5) * 200)  # -100 to +100

    dominant = "neutral"
    if pcr > 1.5 and bearish_notional > bullish_notional * 1.5:
        dominant = "看跌保护"
    elif pcr > 1.2 and bullish_notional > bearish_notional * 1.2:
        dominant = "卖出Put为主"
    elif pcr < 0.7 and bullish_notional > bearish_notional * 1.5:
        dominant = "追涨建仓"
    elif pcr < 0.8 and bearish_notional > bullish_notional * 1.2:
        dominant = "Covered Call偏好"
    elif bullish_ratio > 0.55:
        dominant = "偏多"
    elif bullish_ratio < 0.45:
        dominant = "偏空"

    return {
        "currency": currency, "spot": spot, "days": days,
        "buy_ratio": round(buy_ratio, 3),
        "bullish_ratio": round(bullish_ratio, 3),
        "dominant_flow": dominant,
        "risk_level": RiskFramework.get_status(spot),
        "sentiment_score": sentiment_score,
        "sentiment_text": dominant,
        "pcr": round(pcr, 2),
        "summary": {"total_trades": total_count,
                    "buy_puts": buy_puts, "sell_puts": sell_puts,
                    "buy_calls": buy_calls, "sell_calls": sell_calls,
                    "buy_put_notional": round(buy_put_notional, 0),
                    "sell_put_notional": round(sell_put_notional, 0),
                    "buy_call_notional": round(buy_call_notional, 0),
                    "sell_call_notional": round(sell_call_notional, 0)}
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
