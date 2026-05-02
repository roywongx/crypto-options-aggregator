import json
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/charts", tags=["charts"])


@router.get("/pcr")
async def get_pcr_chart(currency: str = "BTC", hours: int = 168):
    from services.trades import fetch_deribit_summaries
    from services.instrument import _parse_inst_name
    from db.async_connection import execute_read_async

    summaries = fetch_deribit_summaries(currency)
    if not summaries:
        return []

    by_expiry = {}
    for s in summaries:
        meta = _parse_inst_name(s.get("instrument_name", ""))
        if not meta:
            continue
        exp = meta.expiry
        if exp not in by_expiry:
            by_expiry[exp] = {"put_oi": 0, "call_oi": 0, "dte": meta.dte}
        oi = float(s.get("open_interest") or 0)
        if meta.option_type == "P":
            by_expiry[exp]["put_oi"] += oi
        else:
            by_expiry[exp]["call_oi"] += oi

    if not by_expiry:
        return []

    nearest_exp = min(by_expiry.keys(), key=lambda e: by_expiry[e]["dte"])
    ne = by_expiry[nearest_exp]
    current_pcr = ne["put_oi"] / ne["call_oi"] if ne["call_oi"] > 0 else 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    if hours <= 24:
        return [{"time": now, "pcr": round(current_pcr, 3), "puts": round(ne["put_oi"], 0), "calls": round(ne["call_oi"], 0)}]

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')
    rows = await execute_read_async("""
        SELECT timestamp, large_trades_details, spot_price FROM scan_records
        WHERE currency = ? AND timestamp >= ? AND large_trades_details IS NOT NULL AND large_trades_details != ''
        ORDER BY timestamp ASC
    """, (currency, since))

    result = []
    for row in rows:
        ts = row[0]
        ltd = row[1]
        try:
            trades = json.loads(ltd) if ltd else []
            if not trades:
                continue

            # 过滤有效交易：volume > 0 且不超过极端值
            valid_trades = [t for t in trades if 0 < (t.get('volume') or 0) < 1000000]
            if not valid_trades:
                continue

            puts = sum(t.get('volume', 0) or 0 for t in valid_trades if (t.get('option_type') or 'P')[0].upper() == 'P')
            calls = sum(t.get('volume', 0) or 0 for t in valid_trades if (t.get('option_type') or 'C')[0].upper() == 'C')
            if puts == 0 and calls == 0:
                continue
            pcr_val = puts / calls if calls > 0 else 0

            # 过滤极端异常值（PCR 正常范围 0.1 ~ 10）
            if pcr_val > 50 or pcr_val < 0.01:
                continue

            result.append({"time": ts, "pcr": round(pcr_val, 3), "puts": puts, "calls": calls})
        except (ValueError, TypeError, ZeroDivisionError) as e:
            logger.debug("PCR calc skip: %s", e)

    if not result:
        result.append({"time": now, "pcr": round(current_pcr, 3), "puts": round(ne["put_oi"], 0), "calls": round(ne["call_oi"], 0)})

    return result


@router.get("/dvol")
async def get_dvol_chart(currency: str = "BTC", hours: int = 168):
    from db.async_connection import execute_read_async
    from services.dvol_analyzer import get_dvol_from_deribit

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')
    rows = await execute_read_async("""
        SELECT timestamp, dvol_current FROM scan_records
        WHERE currency = ? AND timestamp >= ? AND dvol_current IS NOT NULL
        ORDER BY timestamp ASC
    """, (currency, since))

    result = []
    for row in rows:
        ts = row[0]
        dvol = row[1] or 0
        if dvol <= 0:
            continue
        result.append({"time": ts, "dvol": dvol})

    # 如果数据库中没有有效 DVOL 数据，尝试从 Deribit API 获取历史数据
    if not result:
        try:
            dvol_data = get_dvol_from_deribit(currency)
            if dvol_data and dvol_data.get("current"):
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                result.append({"time": now, "dvol": dvol_data["current"]})
        except (RuntimeError, ConnectionError, TimeoutError) as e:
            logger.debug("DVOL fallback failed: %s", e)

    return result


@router.get("/vol-surface")
async def get_vol_surface(currency: str = "BTC"):
    """获取 IV 期限结构数据"""
    from services.spot_price import get_spot_price
    from db.async_connection import execute_read_async

    try:
        spot = get_spot_price(currency)
    except (RuntimeError, ValueError) as e:
        logger.warning("Vol surface spot price failed: %s, using fallback", e)
        from constants import get_spot_fallback
        spot = get_spot_fallback(currency)

    # 优先从数据库获取最近的 scan_records 中的合约数据
    try:
        rows = await execute_read_async("""
            SELECT contracts_data FROM scan_records
            WHERE currency = ? AND contracts_data IS NOT NULL
            ORDER BY timestamp DESC LIMIT 1
        """, (currency,))
    except (OSError, IOError, RuntimeError) as e:
        logger.warning("Vol surface DB query failed: %s", e)
        rows = []

    term_data = []
    if rows and rows[0][0]:
        try:
            contracts = json.loads(rows[0][0])
            expiry_ivs = {}
            for c in contracts:
                iv = c.get("mark_iv") or c.get("iv") or 0
                dte = c.get("dte", 0)
                if iv > 0 and dte > 0:
                    # 统一 IV 单位为百分比：如果 iv < 1，假设为小数形式，乘以 100
                    iv_float = float(iv)
                    if iv_float < 1.0:
                        iv_float = iv_float * 100
                    key = int(dte)
                    if key not in expiry_ivs:
                        expiry_ivs[key] = []
                    expiry_ivs[key].append(iv_float)

            for dte, ivs in sorted(expiry_ivs.items()):
                avg_iv = sum(ivs) / len(ivs)
                term_data.append({"dte": dte, "avg_iv": round(avg_iv, 2)})
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Vol surface JSON parse failed: %s", e)

    # 如果数据库中没有足够数据，尝试从 Deribit API 获取
    if len(term_data) < 2:
        try:
            from services.trades import fetch_deribit_summaries
            from services.instrument import _parse_inst_name
            from scipy import interpolate

            summaries = fetch_deribit_summaries(currency)
            if summaries:
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

                if parsed:
                    expiries = {}
                    for p in parsed:
                        key = p["expiry"]
                        if key not in expiries:
                            expiries[key] = {"dte": p["dte"], "strikes": []}
                        expiries[key]["strikes"].append({"strike": p["strike"], "iv": p["iv"]})

                    expiry_data = sorted(expiries.values(), key=lambda x: x["dte"])
                    for ed in expiry_data:
                        strikes = sorted(ed["strikes"], key=lambda x: abs(x["strike"] - spot))
                        atm_iv = None
                        if strikes:
                            atm_iv = strikes[0]["iv"]
                            for s in strikes[:3]:
                                if s["iv"] > 0:
                                    atm_iv = s["iv"]
                                    break
                        if atm_iv:
                            term_data.append({"dte": ed["dte"], "avg_iv": round(atm_iv, 2)})

                    # 插值填充缺失数据
                    if len(term_data) >= 3:
                        ivs = [t["avg_iv"] for t in term_data]
                        valid_ivs = [(i, iv) for i, iv in enumerate(ivs) if iv is not None]
                        if len(valid_ivs) >= 2:
                            x = [v[0] for v in valid_ivs]
                            y = [v[1] for v in valid_ivs]
                            f = interpolate.interp1d(x, y, kind='linear', fill_value='extrapolate')
                            for i in range(len(term_data)):
                                if term_data[i]["avg_iv"] is None:
                                    term_data[i]["avg_iv"] = round(float(f(i)), 2)
        except (ValueError, TypeError, ZeroDivisionError, RuntimeError) as e:
            logger.warning("Vol surface Deribit fallback failed: %s", e)

    if len(term_data) < 2:
        return {"error": "数据不足，至少需要 2 个期限点", "term_structure": [], "backwardation": False}

    backwardation = False
    if len(term_data) >= 2:
        front_iv = term_data[0]["avg_iv"]
        back_iv = term_data[-1]["avg_iv"]
        if front_iv is not None and back_iv is not None:
            backwardation = front_iv > back_iv * 1.05

    return {
        "currency": currency,
        "term_structure": term_data,
        "backwardation": backwardation
    }


@router.get("/iv-smile")
async def get_iv_smile(currency: str = "BTC"):
    """获取波动率微笑数据 (strike vs IV，最近到期)"""
    from services.spot_price import get_spot_price
    from services.instrument import _parse_inst_name
    from db.async_connection import execute_read_async

    try:
        spot = get_spot_price(currency)
    except (RuntimeError, ValueError):
        from constants import get_spot_fallback
        spot = get_spot_fallback(currency)

    # 从 DB 获取最新合约数据
    rows = await execute_read_async("""
        SELECT contracts_data FROM scan_records
        WHERE currency = ? AND contracts_data IS NOT NULL
        ORDER BY timestamp DESC LIMIT 1
    """, (currency,))

    if not rows or not rows[0][0]:
        return {"error": "无合约数据", "smile": [], "currency": currency, "spot": spot}

    try:
        contracts = json.loads(rows[0][0])
    except json.JSONDecodeError:
        return {"error": "数据解析失败", "smile": [], "currency": currency, "spot": spot}

    # 按到期日分组
    by_expiry = {}
    for c in contracts:
        iv = c.get("mark_iv") or c.get("iv") or 0
        strike = c.get("strike", 0)
        dte = c.get("dte", 0)
        option_type = c.get("option_type", "")
        oi = c.get("oi") if c.get("oi") is not None else c.get("open_interest", 0)
        volume = c.get("volume") if c.get("volume") is not None else 0

        iv_float = float(iv) if iv else 0
        # 统一 IV 格式: 如果 < 1.0 认为是小数，转换为百分比
        if 0 < iv_float < 1.0:
            iv_float *= 100
        # 如果 > 100，可能是错误数据，过滤掉
        elif iv_float > 200:
            continue

        if iv_float <= 0 or strike <= 0 or dte <= 0:
            continue

        # 过滤无效数据 (无 OI 或 IV 异常)
        if float(oi) < 1:
            continue

        exp_key = int(float(dte))
        if exp_key not in by_expiry:
            by_expiry[exp_key] = []
        by_expiry[exp_key].append({
            "strike": float(strike),
            "iv": round(iv_float, 2),
            "type": option_type.upper()[0] if option_type else "?",
            "oi": float(oi),
            "volume": float(volume) if volume else 0,
            "moneyness": round((float(strike) - spot) / spot * 100, 2) if spot > 0 else 0,
        })

    if not by_expiry:
        return {"error": "无有效 IV 数据", "smile": [], "currency": currency, "spot": spot}

    # 取最近到期 + 最远到期 做对比
    sorted_expiries = sorted(by_expiry.keys())
    result = {"currency": currency, "spot": round(spot, 2), "smiles": {}}

    for exp_dte in sorted_expiries[:3]:  # 最近3个到期日
        points = sorted(by_expiry[exp_dte], key=lambda x: x["strike"])
        # 分离 Put/Call
        puts = [p for p in points if p["type"] == "P"]
        calls = [p for p in points if p["type"] == "C"]
        result["smiles"][f"dte_{exp_dte}"] = {
            "dte": exp_dte,
            "puts": puts,
            "calls": calls,
            "all": points,
        }

    return result


@router.get("/greeks-summary")
async def get_greeks_summary(currency: str = "BTC"):
    """获取持仓 Greeks 汇总 (风险矩阵) - 使用 OI 加权计算"""
    from services.spot_price import get_spot_price
    from services.shared_calculations import black_scholes_price
    from db.async_connection import execute_read_async

    try:
        spot = get_spot_price(currency)
    except (RuntimeError, ValueError):
        from constants import get_spot_fallback
        spot = get_spot_fallback(currency)

    # 从 DB 获取最新合约数据
    rows = await execute_read_async("""
        SELECT contracts_data FROM scan_records
        WHERE currency = ? AND contracts_data IS NOT NULL
        ORDER BY timestamp DESC LIMIT 1
    """, (currency,))

    if not rows or not rows[0][0]:
        return {"error": "无合约数据", "greeks": {}, "currency": currency, "spot": spot}

    try:
        contracts = json.loads(rows[0][0])
    except json.JSONDecodeError:
        return {"error": "数据解析失败", "greeks": {}, "currency": currency, "spot": spot}

    total_delta = 0.0
    total_gamma = 0.0
    total_theta = 0.0
    total_vega = 0.0
    total_premium = 0.0
    total_notional = 0.0
    total_oi = 0.0
    contract_count = 0
    put_count = 0
    call_count = 0

    for c in contracts:
        strike = float(c.get("strike", 0))
        dte = int(float(c.get("dte", 0)))
        iv_raw = c.get("mark_iv") or c.get("iv") or 0
        iv = float(iv_raw) if iv_raw else 0
        # 统一 IV 格式
        if 0 < iv < 1.0:
            iv *= 100
        elif iv > 200 or iv <= 0:
            continue
        option_type = c.get("option_type", "")
        premium = float(c.get("premium_usd", c.get("premium", 0)) or 0)
        oi_raw = c.get("oi") if c.get("oi") is not None else c.get("open_interest", 0)
        oi = float(oi_raw) if oi_raw else 0

        if strike <= 0 or dte <= 0:
            continue

        # 用 BS 模型计算 Greeks
        bs = black_scholes_price(option_type, strike, spot, dte, iv)

        # OI 加权 Greeks（反映市场真实风险敞口）
        weight = max(1.0, oi)  # 至少权重为1
        total_delta += bs["delta"] * weight
        total_gamma += bs["gamma"] * weight
        total_theta += bs["theta"] * weight
        total_vega += bs["vega"] * weight
        total_premium += premium * weight
        total_notional += strike * weight
        total_oi += weight
        contract_count += 1

        if option_type.upper()[0] == "P":
            put_count += 1
        elif option_type.upper()[0] == "C":
            call_count += 1

    if total_oi <= 0:
        return {"error": "无有效 Greeks 数据", "greeks": {}, "currency": currency, "spot": spot}

    # 归一化（每单位 OI 的平均 Greeks）
    norm_delta = total_delta / total_oi
    norm_gamma = total_gamma / total_oi
    norm_theta = total_theta / total_oi
    norm_vega = total_vega / total_oi

    # 风险评级（基于归一化后的 Delta）
    abs_delta = abs(norm_delta)
    if abs_delta > 0.5:
        delta_risk = "🔴 高"
    elif abs_delta > 0.2:
        delta_risk = "🟡 中"
    else:
        delta_risk = "🟢 低"

    # 计算风险敞口（使用总 OI 作为乘数）
    total_delta_exposure = norm_delta * total_oi

    return {
        "currency": currency,
        "spot": round(spot, 2),
        "contract_count": contract_count,
        "put_count": put_count,
        "call_count": call_count,
        "total_oi": round(total_oi, 0),
        "greeks_per_contract": {
            "delta": round(norm_delta, 4),
            "gamma": round(norm_gamma, 6),
            "theta": round(norm_theta, 4),
            "vega": round(norm_vega, 4),
        },
        "total_greeks_exposure": {
            "delta": round(total_delta, 2),
            "gamma": round(total_gamma, 4),
            "theta": round(total_theta, 2),
            "vega": round(total_vega, 2),
        },
        "risk_assessment": {
            "delta_risk": delta_risk,
            "delta_pnl_if_down_10pct": round(total_delta_exposure * spot * -0.1, 0),
            "delta_pnl_if_up_10pct": round(total_delta_exposure * spot * 0.1, 0),
            "theta_daily_decay": round(total_theta, 0),
            "vega_pnl_if_iv_up_5pct": round(total_vega * 5, 0),
        },
        "totals": {
            "premium": round(total_premium, 0),
            "notional": round(total_notional, 0),
        }
    }
