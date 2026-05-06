"""
Max Pain 计算服务
基于 Deribit get_book_summary_by_currency 的 OI 数据，
使用 Deribit Insights 官方算法计算每个到期日的最大痛点。

算法来源: https://insights.deribit.com/dev-hub/deribit-max-pain-python-code/

Max Pain = 使所有期权买方内在价值之和最小的标的价
即: argmin_P  Σ  max(0, P-strike)*call_OI + max(0, strike-P)*put_OI
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)

# 候选价格步长（美元）。步长越小越精确，但计算量越大。
PRICE_STEP = 100
# 最大候选价格范围（相对于 min/max strike 的扩展）
PRICE_PADDING_PCT = 0.05


def _parse_expiry_from_name(instrument_name: str) -> str:
    """从 Deribit instrument name 提取到期日，如 BTC-29MAY26-80000-C → 29MAY26"""
    parts = instrument_name.split("-")
    if len(parts) >= 2:
        return parts[1]
    return ""


def _parse_option_type_from_name(instrument_name: str) -> str:
    """从 instrument name 提取期权类型: C → CALL, P → PUT"""
    parts = instrument_name.split("-")
    if len(parts) >= 4:
        return "CALL" if parts[3] == "C" else "PUT"
    return ""


def _parse_strike_from_name(instrument_name: str) -> float:
    """从 instrument name 提取行权价"""
    parts = instrument_name.split("-")
    if len(parts) >= 3:
        try:
            return float(parts[2])
        except (ValueError, TypeError):
            return 0
    return 0


def calculate_max_pain_from_summaries(
    summaries: List[Dict], spot: float = 0
) -> Dict[str, any]:
    """
    从 Deribit book summaries 计算所有到期日的 Max Pain。

    Args:
        summaries: Deribit get_book_summary_by_currency 返回的合约列表
        spot: 当前现货价格（仅用于日志和范围校验）

    Returns:
        {
            "success": bool,
            "max_pain_all": float,          # 所有到期日的综合 max pain（近到期加权）
            "nearest_expiry": str,           # 最近到期日
            "nearest_max_pain": float,       # 最近到期日的 max pain
            "by_expiry": {                   # 按到期日
                "29MAY26": {
                    "max_pain": float,
                    "total_oi_btc": float,
                    "total_oi_usd": float,
                    "call_oi_btc": float,
                    "put_oi_btc": float,
                    "strike_range": [min, max],
                    "resolution": float,      # 价格步长
                    "instruments_count": int,
                },
                ...
            },
            "spot": float,
            "timestamp": str,
        }
    """
    if not summaries:
        return {"success": False, "error": "无 Deribit 数据", "max_pain_all": 0}

    # 按到期日分组
    by_expiry: Dict[str, Dict[str, any]] = defaultdict(
        lambda: {"calls": defaultdict(float), "puts": defaultdict(float)}
    )
    instruments_count_by_expiry = defaultdict(int)

    for s in summaries:
        name = s.get("instrument_name", "")
        if not name:
            continue
        expiry = _parse_expiry_from_name(name)
        strike = _parse_strike_from_name(name)
        opt_type = _parse_option_type_from_name(name)
        oi = float(s.get("open_interest", 0) or 0)
        mark_price = float(s.get("mark_price", 0) or 0)
        underlying_price = float(s.get("underlying_price", 0) or 0)

        if not expiry or strike <= 0 or oi <= 0:
            continue

        # OI 单位: BTC 合约数。转换为美元名义价值便于比较。
        notional = oi * (underlying_price if underlying_price > 0 else spot)

        if opt_type == "CALL":
            by_expiry[expiry]["calls"][strike] += notional
        elif opt_type == "PUT":
            by_expiry[expiry]["puts"][strike] += notional

        instruments_count_by_expiry[expiry] += 1

    if not by_expiry:
        return {"success": False, "error": "无法解析到期日", "max_pain_all": 0}

    # 按到期日排序（近→远）
    from services.instrument import _parse_inst_name
    expiry_order = []
    for expiry in by_expiry:
        # 用该到期日的一个 sample instrument name 来解析 DTE
        sample_name = f"BTC-{expiry}-50000-C"
        try:
            meta = _parse_inst_name(sample_name)
            dte = meta.dte if meta else 999
        except Exception:
            dte = 999
        expiry_order.append((expiry, dte))
    expiry_order.sort(key=lambda x: x[1])

    results = {}
    total_weighted_pain = 0.0
    total_weight = 0.0
    nearest_expiry = None
    nearest_max_pain = 0.0

    for expiry, dte in expiry_order:
        calls = by_expiry[expiry]["calls"]
        puts = by_expiry[expiry]["puts"]
        all_strikes = sorted(set(list(calls.keys()) + list(puts.keys())))

        if len(all_strikes) < 2:
            continue

        min_strike = all_strikes[0]
        max_strike = all_strikes[-1]

        # 候选价格范围
        price_min = max(1, int(min_strike * (1 - PRICE_PADDING_PCT)))
        price_max = int(max_strike * (1 + PRICE_PADDING_PCT))

        # 扫描所有候选价格，找最小 pain 点
        min_pain = float("inf")
        best_price = 0
        pain_curve = []  # for debugging

        price = price_min
        while price <= price_max:
            total_pain = 0.0
            # Call pain
            for strike, oi_notional in calls.items():
                if price > strike:
                    total_pain += (price - strike) * oi_notional / (spot if spot > 0 else strike)
            # Put pain
            for strike, oi_notional in puts.items():
                if strike > price:
                    total_pain += (strike - price) * oi_notional / (spot if spot > 0 else strike)
            # 记录
            pain_curve.append((price, total_pain))

            if total_pain < min_pain:
                min_pain = total_pain
                best_price = price

            price += PRICE_STEP

        # 检查是否有明显的最小值区域（非平坦）
        if best_price <= 0:
            # fallback: 取 OI 加权中位数
            total_oi_by_strike = defaultdict(float)
            for strike, oi in calls.items():
                total_oi_by_strike[strike] += oi
            for strike, oi in puts.items():
                total_oi_by_strike[strike] += oi
            if total_oi_by_strike:
                best_price = max(total_oi_by_strike, key=total_oi_by_strike.get)

        call_oi = sum(calls.values())
        put_oi = sum(puts.values())
        total_oi = call_oi + put_oi

        expiry_result = {
            "max_pain": round(best_price, 0),
            "total_oi_btc": round(total_oi / (spot if spot > 0 else 1), 2),
            "total_oi_usd": round(total_oi, 0),
            "call_oi_btc": round(call_oi / (spot if spot > 0 else 1), 2),
            "put_oi_btc": round(put_oi / (spot if spot > 0 else 1), 2),
            "strike_range": [min_strike, max_strike],
            "resolution": PRICE_STEP,
            "instruments_count": instruments_count_by_expiry[expiry],
            "dte": dte,
        }
        results[expiry] = expiry_result

        # 加权综合（DTE 越近权重越大）
        weight = 1.0 / max(dte, 1)
        total_weighted_pain += best_price * weight
        total_weight += weight

        if nearest_expiry is None:
            nearest_expiry = expiry
            nearest_max_pain = best_price

    if total_weight > 0:
        max_pain_all = round(total_weighted_pain / total_weight, 0)
    else:
        max_pain_all = 0

    return {
        "success": True,
        "max_pain_all": max_pain_all,
        "nearest_expiry": nearest_expiry,
        "nearest_max_pain": nearest_max_pain,
        "by_expiry": results,
        "spot": spot,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def calculate_and_save_max_pain(currency: str = "BTC") -> Dict:
    """
    从 Deribit 获取 OI 数据 → 计算 Max Pain → 存入 max_pain_history 表
    返回计算结果字典
    """
    try:
        from services.trades import fetch_deribit_summaries
        from services.spot_price import get_spot_price
        from db.connection import execute_write
    except ImportError as e:
        logger.error("Max pain import failed: %s", e)
        return {"success": False, "error": str(e)}

    spot = get_spot_price(currency) or 0
    summaries = fetch_deribit_summaries(currency)

    if not summaries:
        logger.warning("Max pain: no Deribit summaries for %s", currency)
        return {"success": False, "error": "Deribit 数据为空", "max_pain_all": 0}

    result = calculate_max_pain_from_summaries(summaries, spot)

    if not result["success"]:
        return result

    # 存入数据库
    try:
        mp = result["nearest_max_pain"] or result["max_pain_all"]
        execute_write(
            """INSERT INTO max_pain_history (timestamp, currency, max_pain_price)
               VALUES (?, ?, ?)""",
            (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), currency, mp),
        )
    except Exception as e:
        logger.warning("Max pain DB save failed (non-critical): %s", e)

    return result


def get_max_pain(currency: str = "BTC", auto_calc: bool = True) -> float:
    """
    获取最新 max pain。如果表为空且 auto_calc=True，则自动计算并保存。

    Returns:
        max_pain_price (float), 0 表示不可用
    """
    try:
        from db.connection import execute_read
        rows = execute_read(
            """SELECT max_pain_price FROM max_pain_history
               WHERE currency=? ORDER BY timestamp DESC LIMIT 1""",
            (currency,),
        )
        if rows and rows[0][0] and float(rows[0][0]) > 0:
            return float(rows[0][0])
    except Exception as e:
        logger.warning("Max pain DB read failed: %s", e)

    if auto_calc:
        try:
            result = calculate_and_save_max_pain(currency)
            if result.get("success"):
                mp = result.get("nearest_max_pain") or result.get("max_pain_all", 0)
                if mp > 0:
                    return mp
        except Exception as e:
            logger.warning("Auto max pain calc failed: %s", e)

    return 0


def get_max_pain_full(currency: str = "BTC") -> Dict:
    """获取完整 max pain 数据（含所有到期日明细）"""
    try:
        from db.connection import execute_read
        rows = execute_read(
            """SELECT max_pain_price FROM max_pain_history
               WHERE currency=? ORDER BY timestamp DESC LIMIT 1""",
            (currency,),
        )
        db_value = float(rows[0][0]) if rows and rows[0][0] else 0
    except Exception:
        db_value = 0

    # 始终尝试重新计算以获取完整的 by_expiry 数据
    try:
        result = calculate_and_save_max_pain(currency)
        if result.get("success"):
            result["db_cached"] = db_value
            return result
    except Exception as e:
        logger.warning("Full max pain calc failed: %s", e)

    return {
        "success": bool(db_value > 0),
        "max_pain_all": db_value,
        "nearest_max_pain": db_value,
        "db_cached": db_value,
        "source": "database_only",
    }
