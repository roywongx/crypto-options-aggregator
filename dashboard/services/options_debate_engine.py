"""
多智能体期权辩论引擎 v2.0

灵感来自 TradingAgents 框架，5 个确定性分析智能体
通过加权合成产出最终交易建议。

v2.0 改进:
- 使用 Black-Scholes Greeks (Delta/Gamma/Theta/Vega)
- 修正 VaR 计算 (加入 z_confidence)
- 修正最大亏损公式 (strike - premium)
- 新增 Theta 衰减分析
- 新增盈亏平衡点分析
- 新增持仓保证金效率分析
- 使用真实 maxpain 数据
"""

import math
import time
import threading
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# 模块级线程池 — 避免每次调用 _gather_market_data 重复创建
_debate_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="debate")

# ---------------------------------------------------------------------------
# 数据收集层 — 短周期缓存，避免同一次请求中重复拉取 API
# ---------------------------------------------------------------------------

_gmd_cache: Dict[str, tuple] = {}  # {currency: (data, timestamp)}
_GMD_CACHE_TTL = 5  # 秒，覆盖同一个 HTTP 请求的生命周期
_gmd_lock = threading.Lock()


def _gather_market_data(currency: str, force_refresh: bool = False) -> Dict[str, Any]:
    """并行采集市场数据 (ThreadPoolExecutor, 4 workers, 30s total timeout)

    内置 5 秒缓存：同一个 currency 在 TTL 内重复调用返回相同数据，
    避免 LLM analyst 的 _prepare_context 和 run_debate 各自拉取导致不一致。
    """
    if not force_refresh:
        now = time.time()
        with _gmd_lock:
            cached = _gmd_cache.get(currency)
            if cached and (now - cached[1]) < _GMD_CACHE_TTL:
                return cached[0]

    data: Dict[str, Any] = {"currency": currency, "errors": []}

    results = {}

    def _fetch_spot():
        try:
            from services.spot_price import get_spot_price
            return ("spot", get_spot_price(currency))
        except (RuntimeError, ConnectionError, TimeoutError) as e:
            return ("spot_error", str(e))

    def _fetch_dvol():
        try:
            from services.dvol_analyzer import get_dvol_from_deribit
            return ("dvol", get_dvol_from_deribit(currency))
        except (RuntimeError, ConnectionError, TimeoutError) as e:
            return ("dvol_error", str(e))

    def _fetch_large_trades():
        try:
            from services.large_trades_fetcher import fetch_large_trades_sync
            return ("large_trades", fetch_large_trades_sync(currency, days=3, limit=30))
        except (RuntimeError, ConnectionError, TimeoutError) as e:
            return ("large_trades_error", str(e))

    def _fetch_db_contracts():
        try:
            from db.connection import execute_read
            import json
            rows = execute_read(
                """SELECT contracts_data, spot_price, dvol_current, dvol_z_score, dvol_signal, timestamp
                   FROM scan_records WHERE currency=? ORDER BY timestamp DESC LIMIT 1""",
                (currency,)
            )
            if rows and rows[0][0]:
                contracts_list = json.loads(rows[0][0])
                scan_ts = rows[0][5] if len(rows[0]) > 5 and rows[0][5] else None
                return ("db_contracts", {
                    "strategy_contracts": contracts_list,
                    "data_timestamp": scan_ts if isinstance(scan_ts, str) else (str(scan_ts) if scan_ts else ""),
                    "db_spot": float(rows[0][1]) if rows[0][1] else 0,
                    "db_dvol": float(rows[0][2]) if rows[0][2] else 0,
                    "db_dvol_z": float(rows[0][3]) if rows[0][3] else 0,
                    "db_dvol_signal": rows[0][4] or "",
                })
            return ("db_contracts", {
                "strategy_contracts": [], "data_timestamp": "",
                "db_spot": 0, "db_dvol": 0, "db_dvol_z": 0, "db_dvol_signal": "",
            })
        except (RuntimeError, ValueError, TypeError) as e:
            return ("db_contracts_error", str(e))

    futures = {
        _debate_executor.submit(_fetch_spot): "spot",
        _debate_executor.submit(_fetch_dvol): "dvol",
        _debate_executor.submit(_fetch_large_trades): "trades",
        _debate_executor.submit(_fetch_db_contracts): "db",
    }
    for future in as_completed(futures, timeout=30):
            try:
                key, value = future.result(timeout=25)
                results[key] = value
            except (FuturesTimeoutError, RuntimeError) as e:
                task = futures[future]
                logger.warning("debate gather %s timed out: %s", task, e)
                data["errors"].append(f"{task}: timeout")

    # Unpack parallel results
    if "spot" in results:
        data["spot"] = results["spot"]
    elif "spot_error" in results:
        data["spot"] = 0
        data["errors"].append(f"spot: {results['spot_error']}")
    else:
        data["spot"] = 0

    if "dvol" in results:
        data["dvol"] = results["dvol"]
    elif "dvol_error" in results:
        data["dvol"] = {}
        data["errors"].append(f"dvol: {results['dvol_error']}")
    else:
        data["dvol"] = {}

    if "large_trades" in results:
        data["large_trades"] = results["large_trades"]
    elif "large_trades_error" in results:
        data["large_trades"] = []
        data["errors"].append(f"large_trades: {results['large_trades_error']}")
    else:
        data["large_trades"] = []

    # Risk framework (depends on spot, run synchronously after)
    try:
        from services.risk_framework import RiskFramework
        spot = data.get("spot", 0)
        data["risk_status"] = RiskFramework.get_status(spot) if spot > 0 else "UNKNOWN"
        data["risk_label"], data["risk_desc"] = RiskFramework.get_risk_label(spot) if spot > 0 else ("⚪ 未知", "")
    except (RuntimeError, ValueError) as e:
        logger.warning("debate gather risk failed: %s", e)
        data["risk_status"] = "UNKNOWN"
        data["risk_label"], data["risk_desc"] = "⚪ 未知", ""
        data["errors"].append(f"risk: {e}")

    # Unpack DB contracts
    if "db_contracts" in results:
        db = results["db_contracts"]
        data["strategy_contracts"] = db["strategy_contracts"]
        data["data_timestamp"] = db["data_timestamp"]
        data["db_spot"] = db["db_spot"]
        data["db_dvol"] = db["db_dvol"]
        data["db_dvol_z"] = db["db_dvol_z"]
        data["db_dvol_signal"] = db["db_dvol_signal"]
        if db["strategy_contracts"] and db["data_timestamp"]:
            for c in db["strategy_contracts"]:
                if isinstance(c, dict) and not c.get("timestamp"):
                    c["timestamp"] = db["data_timestamp"]
    elif "db_contracts_error" in results:
        data["strategy_contracts"] = []
        data["errors"].append(f"contracts: {results['db_contracts_error']}")
    else:
        data["strategy_contracts"] = []

    # 5b) 全量分析合约 — 从 Deribit 直取全量数据（OI≥1, DTE≥1, IV>0）
    # 分析计算用全量数据（合约越多统计越显著），策略过滤仅用于交易面板
    try:
        from services.trades import fetch_deribit_summaries
        from services.instrument import _parse_inst_name
        from services.dvol_analyzer import calc_delta_bs
        spot_for_analysis = data.get("spot", 0)
        raw_summaries = fetch_deribit_summaries(currency)
        if raw_summaries:
            wide_contracts = []
            for s in raw_summaries:
                meta = _parse_inst_name(s.get("instrument_name", ""))
                if not meta or meta.dte < 1:
                    continue
                iv = float(s.get("mark_iv") or 0)
                oi = float(s.get("open_interest") or 0)
                if iv <= 0 or oi < 1:
                    continue
                strike = meta.strike
                underlying = float(s.get("underlying_price", spot_for_analysis)) or spot_for_analysis
                raw_delta = s.get("delta")
                if raw_delta is None or float(raw_delta or 0) == 0:
                    delta_val = abs(calc_delta_bs(strike, underlying, iv, meta.dte, meta.option_type))
                else:
                    delta_val = abs(float(raw_delta))
                prem = float(s.get("mark_price") or 0)
                prem_usd = prem * underlying
                cv = strike * 0.2
                apr = (prem_usd / cv) * (365 / meta.dte) * 100 if cv > 0 else 0
                from services.shared_calculations import black_scholes_price
                bs_greeks = black_scholes_price(meta.option_type, strike, underlying, meta.dte, iv)
                dist = abs(strike - spot_for_analysis) / spot_for_analysis * 100 if spot_for_analysis > 0 else 0
                wide_contracts.append({
                    "symbol": s.get("instrument_name", ""),
                    "platform": "Deribit",
                    "expiry": meta.expiry,
                    "dte": meta.dte,
                    "option_type": meta.option_type,
                    "strike": strike,
                    "apr": round(apr, 1),
                    "premium_usd": round(prem_usd, 2),
                    "premium": round(prem, 2),
                    "delta": round(delta_val, 3),
                    "theta": round(bs_greeks["theta"], 4),
                    "gamma": round(bs_greeks["gamma"], 6),
                    "vega": round(bs_greeks["vega"], 4),
                    "iv": round(iv, 1),
                    "open_interest": round(oi, 0),
                    "oi": round(oi, 0),
                    "distance_spot_pct": round(dist, 1),
                })
            # 注入时间戳
            if data.get("data_timestamp"):
                for c in wide_contracts:
                    if isinstance(c, dict) and not c.get("timestamp"):
                        c["timestamp"] = data["data_timestamp"]
            # 全量数据作为主分析路径
            data["contracts"] = wide_contracts
            data["wide_contracts"] = wide_contracts
            logger.debug("全量分析合约: %d 个 (策略合约: %d 个)",
                        len(wide_contracts), len(data.get("strategy_contracts", [])))
        else:
            # 无 Deribit 数据时回退到 DB 策略合约
            data["contracts"] = data.get("strategy_contracts", [])
            data["wide_contracts"] = []
    except (RuntimeError, ValueError, TypeError, ImportError) as e:
        logger.warning("debate wide contracts failed: %s", e)
        data["contracts"] = data.get("strategy_contracts", [])
        data["wide_contracts"] = []

    # 6) 最大痛点 — 使用 Deribit OI 数据的官方算法
    try:
        from services.max_pain import get_max_pain
        data["max_pain"] = get_max_pain(currency, auto_calc=True)
    except (RuntimeError, ValueError, TypeError, ConnectionError, TimeoutError):
        data["max_pain"] = 0

    # 写入短周期缓存，同一次 HTTP 请求中的后续调用直接复用
    with _gmd_lock:
        _gmd_cache[currency] = (data, time.time())
    return data


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _clamp(val: float, lo: float = -100, hi: float = 100) -> float:
    return max(lo, min(hi, val))


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _bs_greeks(option_type: str, strike: float, spot: float,
               dte: int, iv: float) -> Dict[str, float]:
    """计算 Black-Scholes Greeks"""
    if strike <= 0 or spot <= 0 or iv <= 0 or dte <= 0:
        return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0, "premium": 0}

    T = dte / 365.0
    sigma = iv / 100.0
    r = 0.05

    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    nd1 = _norm_cdf(d1)
    nd2 = _norm_cdf(d2)
    pdf_d1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)

    if option_type.upper() in ("P", "PUT"):
        premium = strike * math.exp(-r * T) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = nd1 - 1
    else:
        premium = spot * nd1 - strike * math.exp(-r * T) * nd2
        delta = nd1

    premium = max(0, premium)
    gamma = pdf_d1 / (spot * sigma * math.sqrt(T))
    vega = spot * pdf_d1 * math.sqrt(T) / 100
    theta = -(spot * pdf_d1 * sigma) / (2 * math.sqrt(T)) / 365

    return {
        "premium": round(premium, 2),
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 2),
        "vega": round(vega, 2),
    }


def _calc_theta_efficiency(premium: float, dte: int, margin: float) -> float:
    """Theta 效率 = 每日 theta 收入 / 保证金占用 (年化)"""
    if dte <= 0 or premium <= 0 or margin <= 0:
        return 0
    # 近似: theta ≈ premium / (2 * sqrt(dte))  (ATM 近似)
    daily_theta = premium / (2 * math.sqrt(max(dte, 1)))
    annual_roi = (daily_theta * 365) / margin * 100
    return round(annual_roi, 1)


def _make_report(name: str, score: float, confidence: float,
                 verdict: str, key_points: List[str],
                 data: Optional[Dict] = None) -> Dict[str, Any]:
    return {
        "name": name,
        "score": round(_clamp(score), 1),
        "confidence": round(max(0, min(100, confidence)), 1),
        "verdict": verdict,
        "key_points": key_points,
        "data": data or {},
    }


# ---------------------------------------------------------------------------
# Agent 1: BullAnalyst — 多头分析师 (v2.0: 加入 Greeks + Theta 效率)
# ---------------------------------------------------------------------------

def _bull_analyst(md: Dict[str, Any]) -> Dict[str, Any]:
    score = 0.0
    conf = 50.0
    points: List[str] = []
    extra: Dict[str, Any] = {}

    spot = md.get("spot", 0)
    dvol = md.get("dvol", {})
    contracts = md.get("contracts", [])
    risk_status = md.get("risk_status", "UNKNOWN")

    if spot <= 0:
        return _make_report("🐂 多头分析师", -20, 10, "无数据", ["现货价格获取失败，无法分析"], extra)

    # 风险状态加权
    risk_bonus = {"NORMAL": 25, "NEAR_FLOOR": 5, "ADVERSE": -15, "PANIC": -40}.get(risk_status, 0)
    score += risk_bonus
    if risk_status == "NORMAL":
        points.append("市场风险状态正常，适合 Sell Put 策略")
    elif risk_status == "NEAR_FLOOR":
        points.append("接近支撑位，Sell Put 需谨慎选择行权价")
    elif risk_status == "ADVERSE":
        points.append("市场处于逆境，减少 Sell Put 仓位")
    elif risk_status == "PANIC":
        points.append("极端行情，不建议新建 Sell Put")

    # 分析合约 — contracts 没有 direction 字段，所有 Put 合约都是潜在 Sell Put 标的
    dvol_current = dvol.get("current", 50)
    put_contracts = [c for c in contracts
                     if c.get("option_type", "").upper() in ("PUT", "P")
                     and c.get("premium_usd", 0) > 0
                     and c.get("dte", 999) >= 14]  # DTE≥14 过滤短期到期合约，避免 365/DTE 乘数膨胀
    if put_contracts:
        # win_rate 从 delta 计算（contracts 不含此字段）
        for c in put_contracts:
            c["_win_rate"] = 1.0 - abs(c.get("delta", 0.5))
            # APR 上限 = DVOL × 3，防止极端短期合约 APR 拉高中位数
            cap = dvol_current * 3
            if c.get("apr", 0) > cap:
                c["apr"] = cap
        # 用中位数替代均值，消除极端短期合约对平均值的拉偏
        aprs = sorted(c.get("apr", 0) for c in put_contracts)
        n = len(aprs)
        if n % 2 == 1:
            avg_apr = aprs[n // 2]
        else:
            avg_apr = (aprs[n // 2 - 1] + aprs[n // 2]) / 2
        avg_win = sum(c["_win_rate"] * 100 for c in put_contracts) / len(put_contracts)
        # 选最优合约：平衡 APR 与胜率，APR 超过 DVOL×3 后边际价值递减
        cap = dvol_current * 3
        best = max(put_contracts, key=lambda c: min(c.get("apr", 0), cap) * c.get("_win_rate", 0)**2)
        best_apr = best.get("apr", 0)
        best_win = best.get("_win_rate", 0)
        extra["avg_apr"] = round(avg_apr, 1)
        extra["avg_win_rate"] = round(avg_win, 1)
        extra["best_apr"] = round(best_apr, 1)
        extra["best_win_rate"] = round(best_win * 100, 1)
        extra["contract_count"] = len(put_contracts)

        # APR 评分
        if avg_apr > 50:
            score += 20
            points.append(f"平均 APR {avg_apr:.0f}%，权利金收益丰厚")
        elif avg_apr > 20:
            score += 10
            points.append(f"平均 APR {avg_apr:.0f}%，收益合理")
        else:
            score -= 5
            points.append(f"平均 APR 仅 {avg_apr:.0f}%，收益偏低")

        # 胜率评分
        if avg_win > 80:
            score += 15
            points.append(f"平均胜率 {avg_win:.0f}%，Sell Put 安全边际充足")
        elif avg_win > 65:
            score += 5
            points.append(f"平均胜率 {avg_win:.0f}%，尚可接受")
        else:
            score -= 10
            points.append(f"平均胜率仅 {avg_win:.0f}%，风险偏高")

        # v3.0: Theta 效率分析（best 已从 APR*win_rate 综合选出）
        best_strike = best.get("strike", 0)
        best_premium = best.get("premium", 0)
        best_dte = best.get("dte", 30)
        if best_strike > 0 and best_premium > 0:
            margin_est = max(best_strike * 0.2, (best_strike - best_premium) * 0.2)
            theta_eff = _calc_theta_efficiency(best_premium, best_dte, margin_est)
            extra["theta_efficiency"] = theta_eff
            if theta_eff > 100:
                score += 10
                points.append(f"Theta 效率 {theta_eff:.0f}% (年化)，卖方收益优秀")
            elif theta_eff > 50:
                score += 5
                points.append(f"Theta 效率 {theta_eff:.0f}% (年化)")

        # v2.0: 盈亏平衡点分析
        if best_strike > 0 and best_premium > 0:
            breakeven = best_strike - best_premium
            breakeven_dist = (spot - breakeven) / spot * 100
            extra["breakeven"] = round(breakeven, 0)
            extra["breakeven_dist_pct"] = round(breakeven_dist, 1)
            if breakeven_dist > 20:
                score += 10
                points.append(f"盈亏平衡点 ${breakeven:,.0f} 距现货 {breakeven_dist:.1f}%，安全边际大")
            elif breakeven_dist > 10:
                points.append(f"盈亏平衡点 ${breakeven:,.0f} 距现货 {breakeven_dist:.1f}%")
            else:
                score -= 5
                points.append(f"盈亏平衡点 ${breakeven:,.0f} 距现货仅 {breakeven_dist:.1f}%，风险较高")

        # v3.0: 最佳合约分析（使用合约自带 delta + 动态 Greeks）
        dvol_val = dvol.get("current", 50)
        if best_strike > 0 and best_dte > 0 and dvol_val > 0:
            greeks = _bs_greeks("PUT", best_strike, spot, best_dte, dvol_val)
            extra["best_greeks"] = greeks
            abs_delta = abs(best.get("delta", greeks.get("delta", 0)))
            if abs_delta < 0.15:
                score += 5
                points.append(f"最佳合约 Delta {abs_delta:.2f}，深度 OTM，Sell Put 胜率 ~{100-abs_delta*100:.0f}%")
            elif abs_delta < 0.30:
                points.append(f"最佳合约 Delta {abs_delta:.2f}，合理 OTM 区间，胜率 ~{100-abs_delta*100:.0f}%")
            else:
                score -= 5
                points.append(f"最佳合约 Delta {abs_delta:.2f}，接近 ATM，胜率仅 ~{100-abs_delta*100:.0f}%")

        conf += min(20, len(put_contracts) * 2)
    else:
        points.append("当前无合适的 Sell Put 合约数据")
        score -= 10

    # DVOL 对卖方有利程度
    dvol_val = dvol.get("current", 50)
    if dvol_val > 70:
        score += 10
        points.append(f"DVOL {dvol_val:.0f}% 偏高，权利金溢价利于卖方")
    elif dvol_val < 50:
        score -= 5
        points.append(f"DVOL {dvol_val:.0f}% 偏低，权利金收益有限")

    verdict = "强烈看多" if score > 50 else "偏多" if score > 15 else "中性" if score > -15 else "偏空" if score > -50 else "强烈看空"
    return _make_report("🐂 多头分析师", score, conf, verdict, points, extra)


# ---------------------------------------------------------------------------
# Agent 2: BearAnalyst — 空头分析师 (v2.0: 修正最大亏损公式)
# ---------------------------------------------------------------------------

def _bear_analyst(md: Dict[str, Any]) -> Dict[str, Any]:
    score = 0.0
    conf = 50.0
    points: List[str] = []
    extra: Dict[str, Any] = {}

    spot = md.get("spot", 0)
    dvol = md.get("dvol", {})
    risk_status = md.get("risk_status", "UNKNOWN")
    contracts = md.get("contracts", [])
    large_trades = md.get("large_trades", [])

    if spot <= 0:
        return _make_report("🐻 空头分析师", 30, 10, "无数据", ["现货价格获取失败，建议保守"], extra)

    # 风险状态分析
    risk_bear_bonus = {"NORMAL": -20, "NEAR_FLOOR": 10, "ADVERSE": 30, "PANIC": 50}.get(risk_status, 0)
    score += risk_bear_bonus
    if risk_status in ("ADVERSE", "PANIC"):
        points.append(f"风险状态: {risk_status}，下行风险显著增加")

    # DVOL 高 = 下行风险大
    dvol_val = dvol.get("current", 50)
    z_score = dvol.get("z_score", 0)
    if dvol_val > 70:
        score += 25
        points.append(f"DVOL {dvol_val:.0f}% 极高，隐含大幅波动风险")
    elif dvol_val > 50:
        score += 10
        points.append(f"DVOL {dvol_val:.0f}% 偏高，波动率风险上升")
    elif dvol_val < 50:
        score -= 10
        points.append(f"DVOL {dvol_val:.0f}% 低位，波动率风险较低")

    if abs(z_score) > 2:
        score += 15
        points.append(f"DVOL Z-Score {z_score:.1f} 极端，市场恐慌情绪蔓延")

    # 大单流向分析 - 修正 PCR 计算（Put/Call Ratio = 买入Put名义价值 / 买入Call名义价值）
    # 使用 buy_notional 字段而非 direction+notional_usd，避免丢失非主导方向数据
    buy_put_notional = sum(t.get("buy_notional", 0) for t in large_trades
                           if t.get("option_type", "").upper() in ("PUT", "P"))
    buy_call_notional = sum(t.get("buy_notional", 0) for t in large_trades
                            if t.get("option_type", "").upper() in ("CALL", "C"))

    if buy_put_notional > 0 or buy_call_notional > 0:
        pcr = buy_put_notional / buy_call_notional if buy_call_notional > 0 else 999
        extra["put_call_ratio"] = round(pcr, 2)
        if pcr > 1.5:
            score += 20
            points.append(f"大单 PCR {pcr:.1f} 极高，机构大量买入看跌期权")
        elif pcr > 1.0:
            score += 10
            points.append(f"大单 PCR {pcr:.1f} 偏高，看跌力量偏强")
        elif pcr < 0.5:
            score -= 10
            points.append(f"大单 PCR {pcr:.1f} 极低，看涨力量主导")
        else:
            points.append(f"大单 PCR {pcr:.1f} 中性")

    # v2.0: 修正最大亏损计算
    # Sell Put 最大亏损 = strike - premium (当 spot→0)
    # 不是 strike - spot
    if contracts:
        put_contracts = [c for c in contracts
                         if c.get("option_type", "").upper() in ("PUT", "P")]
        if put_contracts and spot > 0:
            # 找 ATM 附近的 Put (最危险的)
            near_atm = min(put_contracts, key=lambda c: abs(c.get("strike", 0) - spot))
            atm_strike = near_atm.get("strike", 0)
            atm_premium = near_atm.get("premium", 0)
            # v2.0 修正: max_loss = strike - premium (当 spot→0)
            max_loss_per_unit = max(0, atm_strike - atm_premium)
            max_loss_pct = max_loss_per_unit / spot * 100 if spot > 0 else 0
            extra["worst_case_loss_per_unit"] = round(max_loss_per_unit, 2)
            extra["worst_case_loss_pct"] = round(max_loss_pct, 1)
            extra["worst_case_breakeven"] = round(atm_strike - atm_premium, 0)
            if max_loss_pct > 50:
                score += 20
                points.append(f"ATM Put 盈亏平衡 ${atm_strike - atm_premium:,.0f}，极端下跌最大亏损 {max_loss_pct:.0f}%")
            elif max_loss_pct > 30:
                score += 10
                points.append(f"ATM Put 最大亏损约 {max_loss_pct:.0f}%")

    # v2.0: Gamma 风险 (高 Gamma = 近到期 + ATM，价格敏感度高)
    if contracts and dvol_val > 0:
        near_expiry = [c for c in contracts if 0 < c.get("dte", 999) <= 7
                       and abs(c.get("strike", 0) - spot) / spot < 0.05]
        if near_expiry:
            score += 10
            points.append(f"有 {len(near_expiry)} 个近到期 ATM 合约，Gamma 风险高，价格敏感")

    conf = min(80, 40 + len(large_trades) + (10 if dvol_val > 0 else 0))
    verdict = "极度危险" if score > 60 else "偏空" if score > 20 else "中性" if score > -15 else "偏多" if score > -40 else "极度乐观"
    return _make_report("🐻 空头分析师", -score, conf, verdict, points, extra)


# ---------------------------------------------------------------------------
# Agent 3: VolAnalyst — 波动率分析师 (v2.0: 加入 Vega 风险)
# ---------------------------------------------------------------------------

def _vol_analyst(md: Dict[str, Any]) -> Dict[str, Any]:
    score = 0.0
    conf = 40.0
    points: List[str] = []
    extra: Dict[str, Any] = {}

    dvol = md.get("dvol", {})
    contracts = md.get("contracts", [])
    spot = md.get("spot", 0)

    dvol_val = dvol.get("current", 0)
    z_score = dvol.get("z_score", 0)
    signal = dvol.get("signal", "")
    trend = dvol.get("trend", "")
    percentile = dvol.get("percentile_7d", 50)

    if not dvol_val and not dvol:
        return _make_report("📊 波动率分析师", 0, 15, "无数据", ["DVOL 数据获取失败"], extra)

    extra["dvol"] = dvol_val
    extra["z_score"] = z_score
    extra["percentile_7d"] = percentile

    # 波动率体制判断
    if dvol_val < 30:
        regime = "低波动"
        score = 20
        points.append(f"DVOL {dvol_val:.0f}% 处于低波动体制，适合卖权收权利金")
        conf += 15
    elif dvol_val < 50:
        regime = "中等波动"
        score = 10
        points.append(f"DVOL {dvol_val:.0f}% 处于中等波动体制")
        conf += 10
    elif dvol_val < 70:
        regime = "高波动"
        score = -15
        points.append(f"DVOL {dvol_val:.0f}% 处于高波动体制，卖权需谨慎")
        conf += 10
    else:
        regime = "极端波动"
        score = -35
        points.append(f"DVOL {dvol_val:.0f}% 处于极端波动体制，强烈建议减少仓位")
        conf += 20
    extra["regime"] = regime

    # Z-Score 分析
    if z_score > 2:
        score -= 20
        points.append(f"Z-Score {z_score:.1f} 异常偏高，波动率可能回归均值（利多卖方但短期风险大）")
    elif z_score > 1:
        score -= 5
        points.append(f"Z-Score {z_score:.1f} 偏高")
    elif z_score < -2:
        score += 15
        points.append(f"Z-Score {z_score:.1f} 异常偏低，波动率可能回升（利多买方）")
    elif z_score < -1:
        score += 5
        points.append(f"Z-Score {z_score:.1f} 偏低，当前波动率压缩")
    else:
        score += 5
        points.append(f"Z-Score {z_score:.1f} 在正常范围内")

    # IV 百分位
    if percentile > 80:
        score -= 10
        points.append(f"IV 百分位 {percentile:.0f}%，隐含波动率历史高位")
    elif percentile < 20:
        score += 10
        points.append(f"IV 百分位 {percentile:.0f}%，隐含波动率历史低位，权利金便宜")
    else:
        points.append(f"IV 百分位 {percentile:.0f}%")

    # 趋势分析
    trend_map = {"↑": "上涨", "↓": "下跌", "→": "震荡"}
    trend_label = trend_map.get(trend, "未知")
    extra["vol_trend"] = trend_label
    if trend == "↑":
        score -= 5
        points.append(f"波动率趋势{trend_label}，市场不确定性增加")
    elif trend == "↓":
        score += 5
        points.append(f"波动率趋势{trend_label}，市场趋于平稳")

    # v2.0: IV 偏度分析
    if contracts:
        put_ivs = [c.get("iv", 0) for c in contracts
                   if c.get("option_type", "").upper() in ("PUT", "P") and c.get("iv", 0) > 0]
        call_ivs = [c.get("iv", 0) for c in contracts
                    if c.get("option_type", "").upper() in ("CALL", "C") and c.get("iv", 0) > 0]
        if put_ivs and call_ivs:
            avg_put_iv = sum(put_ivs) / len(put_ivs)
            avg_call_iv = sum(call_ivs) / len(call_ivs)
            skew = avg_put_iv - avg_call_iv
            extra["put_iv_avg"] = round(avg_put_iv, 1)
            extra["call_iv_avg"] = round(avg_call_iv, 1)
            extra["skew"] = round(skew, 1)
            if skew > 10:
                points.append(f"Put IV 高于 Call IV {skew:.0f}%，下行保护需求旺盛")
                score -= 5
            elif skew < -10:
                points.append(f"Call IV 高于 Put IV {abs(skew):.0f}%，上行投机需求旺盛")
                score += 5
            else:
                points.append(f"IV 偏度 {skew:.0f}%，整体均衡")

    # v2.0: Vega 风险评估 (高 Vega = IV 变化对仓位影响大)
    if contracts and spot > 0 and dvol_val > 0:
        sample_contract = contracts[0]
        sample_strike = sample_contract.get("strike", 0)
        sample_dte = sample_contract.get("dte", 30)
        if sample_strike > 0:
            greeks = _bs_greeks("PUT", sample_strike, spot, sample_dte, dvol_val)
            vega = greeks.get("vega", 0)
            extra["sample_vega"] = vega
            if vega > 50:
                points.append(f"Vega 敏感度高 (${vega:.0f}/1% IV)，IV 变化对仓位影响大")

    verdict = "极度利多卖方" if score > 40 else "利多卖方" if score > 15 else "中性" if score > -15 else "利多买方" if score > -40 else "极度利多买方"
    return _make_report("📊 波动率分析师", score, conf, verdict, points, extra)


# ---------------------------------------------------------------------------
# Agent 4: FlowAnalyst — 资金流向分析师 (v2.0: 使用真实 maxpain)
# ---------------------------------------------------------------------------

def _flow_analyst(md: Dict[str, Any]) -> Dict[str, Any]:
    score = 0.0
    conf = 30.0
    points: List[str] = []
    extra: Dict[str, Any] = {}

    large_trades = md.get("large_trades", [])
    spot = md.get("spot", 0)
    contracts = md.get("contracts", [])
    max_pain = md.get("max_pain", 0)

    if not large_trades and not contracts:
        return _make_report("🐋 资金流向分析师", 0, 10, "无数据", ["无大宗交易或合约数据"], extra)

    # 大单交易分析
    # 注意：使用 buy_notional/sell_notional 字段而非 direction+notional_usd
    # 因为 direction 只反映主导方向，会丢失非主导方向的数据
    if large_trades:
        total = len(large_trades)
        # 优先使用 buy_count/sell_count 字段（聚合后的真实笔数）
        buy_count = sum(t.get("buy_count", 0) for t in large_trades)
        sell_count = sum(t.get("sell_count", 0) for t in large_trades)
        # 优先使用 buy_notional/sell_notional 字段（聚合后的真实名义价值）
        buy_notional = sum(t.get("buy_notional", 0) for t in large_trades)
        sell_notional = sum(t.get("sell_notional", 0) for t in large_trades)
        total_notional = buy_notional + sell_notional

        extra["total_trades"] = total
        extra["buy_count"] = buy_count
        extra["sell_count"] = sell_count
        extra["buy_notional"] = round(buy_notional, 0)
        extra["sell_notional"] = round(sell_notional, 0)

        conf += min(30, total * 2)

        # 买卖方向偏度
        if total_notional > 0:
            buy_pct = buy_notional / total_notional * 100
            extra["buy_pct"] = round(buy_pct, 1)
            if buy_pct > 65:
                score += 15
                points.append(f"大单买入占比 {buy_pct:.0f}%，资金流入明显")
            elif buy_pct < 35:
                score -= 15
                points.append(f"大单卖出占比 {100 - buy_pct:.0f}%，资金流出明显")
            else:
                points.append(f"大单买卖均衡，买入占比 {buy_pct:.0f}%")

        # PCR 分析 - 使用 buy_notional 按 option_type 分类
        put_buy = sum(t.get("buy_notional", 0) for t in large_trades
                      if t.get("option_type", "").upper() in ("PUT", "P"))
        call_buy = sum(t.get("buy_notional", 0) for t in large_trades
                       if t.get("option_type", "").upper() in ("CALL", "C"))
        put_sell = sum(t.get("sell_notional", 0) for t in large_trades
                       if t.get("option_type", "").upper() in ("PUT", "P"))
        call_sell = sum(t.get("sell_notional", 0) for t in large_trades
                        if t.get("option_type", "").upper() in ("CALL", "C"))

        extra["buy_put"] = round(put_buy, 0)
        extra["buy_call"] = round(call_buy, 0)
        extra["sell_put"] = round(put_sell, 0)
        extra["sell_call"] = round(call_sell, 0)

        if call_buy > 0:
            pcr = put_buy / call_buy
            extra["pcr"] = round(pcr, 2)
            if pcr > 1.5:
                score -= 20
                points.append(f"PCR(买Put/买Call) {pcr:.1f} 极高，看跌情绪浓厚")
            elif pcr > 1.0:
                score -= 10
                points.append(f"PCR(买Put/买Call) {pcr:.1f} 偏高，看跌力量占优")
            elif pcr < 0.5:
                score += 15
                points.append(f"PCR(买Put/买Call) {pcr:.1f} 极低，看涨情绪浓厚")
            elif pcr < 0.8:
                score += 5
                points.append(f"PCR(买Put/买Call) {pcr:.1f} 偏低，看涨力量占优")
            else:
                points.append(f"PCR(买Put/买Call) {pcr:.1f} 中性")

        # v3.0: 卖方 PCR 分析 — 卖Call/卖Put → 主动卖出方向
        if put_sell > 0:
            sell_pcr = call_sell / put_sell
            extra["sell_pcr"] = round(sell_pcr, 2)
            if call_sell > put_sell * 2:
                score -= 10
                points.append(f"主动卖出Call显著(${call_sell:,.0f})，大户不看好上涨空间")
            elif put_sell > call_sell * 2:
                score += 10
                points.append(f"主动卖出Put显著(${put_sell:,.0f})，大户不担忧下跌风险")

        # v3.0: 综合四象限净方向
        net_bullish = call_buy + put_sell  # 看涨力量: 买Call + 卖Put
        net_bearish = put_buy + call_sell   # 看跌力量: 买Put + 卖Call
        if net_bullish + net_bearish > 0:
            net_ratio = (net_bullish - net_bearish) / (net_bullish + net_bearish)
            extra["net_sentiment_ratio"] = round(net_ratio, 2)
            if abs(net_ratio) > 0.3:
                direction = "偏多" if net_ratio > 0 else "偏空"
                points.append(f"综合资金流 {direction} (净值比 {net_ratio:+.2f})")

        # 机构大单分析
        whale_trades = [t for t in large_trades if t.get("notional_usd", 0) > 1_000_000]
        if whale_trades:
            whale_buy = sum(1 for t in whale_trades if t.get("direction") == "buy")
            whale_sell = len(whale_trades) - whale_buy
            extra["whale_trades"] = len(whale_trades)
            if whale_buy > whale_sell * 1.5:
                score += 10
                points.append(f"超大单（>$1M）中买入主导 ({whale_buy} vs {whale_sell})")
            elif whale_sell > whale_buy * 1.5:
                score -= 10
                points.append(f"超大单（>$1M）中卖出主导 ({whale_sell} vs {whale_buy})")

    # v2.0: 最大痛点分析 (使用真实数据)
    if max_pain > 0 and spot > 0:
        pain_dist_pct = (spot - max_pain) / spot * 100
        extra["max_pain"] = round(max_pain, 0)
        extra["pain_distance_pct"] = round(pain_dist_pct, 1)
        if abs(pain_dist_pct) < 2:
            points.append(f"现货接近最大痛点 ${max_pain:,.0f}，期权博弈激烈")
        elif pain_dist_pct > 5:
            score += 5
            points.append(f"现货高于最大痛点 {pain_dist_pct:.1f}% (${max_pain:,.0f})，有回归引力")
        elif pain_dist_pct < -5:
            score -= 5
            points.append(f"现货低于最大痛点 {abs(pain_dist_pct):.1f}% (${max_pain:,.0f})，有回归引力")
    elif contracts and spot > 0:
        # 回退: 用 OI 加权估算
        strikes_oi = [(c.get("strike", 0), c.get("oi", 0)) for c in contracts
                      if c.get("strike", 0) > 0 and c.get("oi", 0) > 0]
        if strikes_oi:
            total_oi = sum(oi for _, oi in strikes_oi)
            if total_oi > 0:
                weighted_strike = sum(s * oi for s, oi in strikes_oi) / total_oi
                pain_dist_pct = (spot - weighted_strike) / spot * 100
                extra["max_pain_est"] = round(weighted_strike, 0)
                extra["pain_distance_pct"] = round(pain_dist_pct, 1)
                points.append(f"OI 加权行权价 ${weighted_strike:,.0f}，距现货 {pain_dist_pct:.1f}%")

    verdict = "强烈看多" if score > 40 else "偏多" if score > 10 else "中性" if score > -10 else "偏空" if score > -40 else "强烈看空"
    return _make_report("🐋 资金流向分析师", score, conf, verdict, points, extra)


# ---------------------------------------------------------------------------
# Agent 5: RiskOfficer — 风险官 (v2.0: 修正 VaR + 加入保证金效率)
# ---------------------------------------------------------------------------

def get_portfolio_position_pct(risk_status: str, dvol: float = 50) -> dict:
    """组合级仓位建议 — 系统中唯一的仓位权威来源

    所有需要仓位建议的模块（Risk API、LLM、前端）都应从此函数获取，
    避免多处硬编码导致建议冲突。

    Returns:
        {"recommended_pct": int, "range": str, "rationale": str}
    """
    if risk_status == "NORMAL" and dvol < 50:
        return {"recommended_pct": 70, "range": "60-80%", "rationale": "低波动 + 正常风险环境，可积极配置"}
    elif risk_status == "NORMAL":
        return {"recommended_pct": 50, "range": "40-60%", "rationale": "正常风险但波动率偏高，适度配置"}
    elif risk_status == "NEAR_FLOOR":
        return {"recommended_pct": 35, "range": "20-50%", "rationale": "接近支撑位，需保留现金应对破位风险"}
    elif risk_status == "ADVERSE":
        return {"recommended_pct": 20, "range": "10-30%", "rationale": "市场逆境，优先减仓防守"}
    else:
        return {"recommended_pct": 10, "range": "0-20%", "rationale": "极端行情，强烈建议清仓观望"}


def _risk_officer(md: Dict[str, Any]) -> Dict[str, Any]:
    score = 0.0
    conf = 50.0
    points: List[str] = []
    extra: Dict[str, Any] = {}

    spot = md.get("spot", 0)
    risk_status = md.get("risk_status", "UNKNOWN")
    dvol = md.get("dvol", {})
    contracts = md.get("contracts", [])

    if spot <= 0:
        return _make_report("🛡️ 风险官", -30, 15, "无数据", ["现货价格获取失败，全面保守"], extra)

    # 基础风险状态评分
    risk_score_map = {"NORMAL": 20, "NEAR_FLOOR": -10, "ADVERSE": -35, "PANIC": -60}
    base_risk = risk_score_map.get(risk_status, -20)
    score = base_risk

    if risk_status == "NORMAL":
        points.append("风险框架: 市场状态正常，可维持标准仓位")
    elif risk_status == "NEAR_FLOOR":
        points.append("风险框架: 接近支撑位，建议减仓至 50-70%")
    elif risk_status == "ADVERSE":
        points.append("风险框架: 逆境状态，建议减仓至 30% 以下")
    elif risk_status == "PANIC":
        points.append("风险框架: 极端恐慌，强烈建议止损/对冲")

    # v2.0: 修正 VaR 计算 (加入 95% 置信度 z=1.645)
    dvol_val = dvol.get("current", 50)
    # 正确公式: daily_var = spot * (dvol/100) * z / sqrt(365)
    # z=1.645 对应 95% 置信度
    daily_var = spot * (dvol_val / 100) * 1.645 / math.sqrt(365)
    weekly_var = daily_var * math.sqrt(5)
    extra["daily_var"] = round(daily_var, 0)
    extra["weekly_var"] = round(weekly_var, 0)
    extra["daily_var_pct"] = round(daily_var / spot * 100, 2) if spot > 0 else 0
    extra["var_confidence"] = "95%"

    if daily_var / spot > 0.05:
        score -= 15
        points.append(f"95% VaR ${daily_var:,.0f} ({daily_var / spot * 100:.1f}%/日)，风险暴露高")
    else:
        points.append(f"95% VaR ${daily_var:,.0f} ({daily_var / spot * 100:.1f}%/日)")

    points.append(f"周度 VaR ${weekly_var:,.0f} (95% 置信度)")

    # 保证金利用率估算 — 使用 premium_usd > 0 过滤有效合约
    if contracts:
        sell_contracts = [c for c in contracts if c.get("premium_usd", 0) > 0]
        if sell_contracts:
            # margin 估算: max(strike*0.2, (strike-premium)*0.2)，没有 margin 字段时自动计算
            total_margin = 0
            total_premium = 0
            for c in sell_contracts:
                s = c.get("strike", 0)
                p = c.get("premium_usd", 0) or c.get("premium", 0) or 0
                total_premium += p
                m = c.get("margin", 0)
                if not m and s > 0:
                    m = max(s * 0.2, (s - p) * 0.2)
                total_margin += m
            extra["total_margin_est"] = round(total_margin, 0)
            extra["total_premium_est"] = round(total_premium, 0)

            if total_margin > 0:
                margin_efficiency = total_premium / total_margin * 100
                extra["margin_efficiency"] = round(margin_efficiency, 1)
                points.append(f"保证金效率: {margin_efficiency:.1f}% (权利金/保证金)")

                # v2.0: 保证金效率评分
                if margin_efficiency > 10:
                    score += 5
                    points.append("保证金效率优秀 (>10%)")
                elif margin_efficiency < 3:
                    score -= 5
                    points.append("保证金效率偏低 (<3%)")

            if len(sell_contracts) > 10:
                score -= 5
                points.append(f"卖出合约 {len(sell_contracts)} 个，注意分散风险")

    # 仓位建议 — 使用统一的 get_portfolio_position_pct（系统中唯一权威来源）
    pos = get_portfolio_position_pct(risk_status, dvol_val)
    position_pct = pos["recommended_pct"]
    points.append(f"建议仓位: 可用资金的 {pos['range']}")
    extra["recommended_position_pct"] = position_pct

    # 最坏情景
    extreme_drop = spot * 0.3
    extra["extreme_scenario_drop"] = round(extreme_drop, 0)
    extra["extreme_scenario_price"] = round(spot - extreme_drop, 0)
    if risk_status in ("ADVERSE", "PANIC"):
        points.append(f"最坏情景: 若跌 30% 至 ${spot - extreme_drop:,.0f}，需确保保证金充足")

    conf = min(80, 40 + (20 if dvol_val > 0 else 0) + (10 if contracts else 0))
    verdict = "极度危险" if score < -50 else "高风险" if score < -20 else "中等风险" if score < 10 else "低风险" if score < 30 else "安全"
    return _make_report("🛡️ 风险官", score, conf, verdict, points, extra)


# ---------------------------------------------------------------------------
# 合成器 (Synthesizer)
# ---------------------------------------------------------------------------

AGENT_WEIGHTS = {
    "🐂 多头分析师": 0.25,
    "🐻 空头分析师": 0.20,
    "📊 波动率分析师": 0.25,
    "🐋 资金流向分析师": 0.15,
    "🛡️ 风险官": 0.15,
}

RECOMMENDATION_MAP = [
    (50, "strong_buy", "强烈建议卖出看跌期权收租"),
    (25, "buy", "建议卖出看跌期权收租"),
    (5, "hold", "观望或小仓位操作"),
    (-25, "sell", "建议减少看涨仓位/对冲"),
    (-100, "strong_sell", "强烈建议止损/清仓"),
]


def _synthesize(reports: List[Dict[str, Any]], md: Dict[str, Any]) -> Dict[str, Any]:
    weighted_sum = 0.0
    total_weight = 0.0
    for r in reports:
        name = r["name"]
        w = AGENT_WEIGHTS.get(name, 0.2)
        effective_w = w * (r["confidence"] / 100)
        weighted_sum += r["score"] * effective_w
        total_weight += effective_w

    overall_score = weighted_sum / total_weight if total_weight > 0 else 0
    overall_score = _clamp(overall_score)

    # 冲突检测
    scores = [r["score"] for r in reports]
    max_score = max(scores)
    min_score = min(scores)
    conflict_range = max_score - min_score
    consensus = "高共识" if conflict_range < 40 else "中等分歧" if conflict_range < 70 else "严重分歧"

    # 推荐等级
    recommendation = "hold"
    rec_label = "观望"
    for threshold, rec, label in RECOMMENDATION_MAP:
        if overall_score >= threshold:
            recommendation = rec
            rec_label = label
            break

    # 入场建议
    entry_suggestions = _generate_entry_suggestions(md, overall_score, recommendation)

    return {
        "overall_score": round(overall_score, 1),
        "recommendation": recommendation,
        "recommendation_label": rec_label,
        "consensus": consensus,
        "conflict_range": round(conflict_range, 1),
        "entry_suggestions": entry_suggestions,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _generate_entry_suggestions(md: Dict, overall_score: float,
                                recommendation: str) -> List[Dict[str, Any]]:
    suggestions: List[str] = []
    spot = md.get("spot", 0)
    contracts = md.get("contracts", [])
    dvol = md.get("dvol", {})

    if spot <= 0 or not contracts:
        return suggestions

    dvol_val = dvol.get("current", 50)

    if recommendation in ("strong_buy", "buy"):
        put_contracts = [c for c in contracts
                         if c.get("option_type", "").upper() in ("PUT", "P")
                         and c.get("apr", 0) > 0]
        put_contracts.sort(key=lambda c: c.get("apr", 0), reverse=True)

        for c in put_contracts[:3]:
            strike = c.get("strike", 0)
            premium = c.get("premium", 0)
            apr = c.get("apr", 0)
            dte = c.get("dte", 30)
            # v3.0: 胜率从 delta 计算（contracts 不含 win_rate 字段）
            c_delta = abs(c.get("delta", 0.5))
            win_rate = max(0.05, 1.0 - c_delta)
            dist = (spot - strike) / spot * 100 if spot > 0 else 0

            # v2.0: 使用统一保证金公式
            margin_per = max(strike * 0.2, (strike - premium) * 0.2)
            roi = (premium / margin_per * 100) if margin_per > 0 else 0
            breakeven = strike - premium
            breakeven_dist = (spot - breakeven) / spot * 100 if spot > 0 else 0

            # v2.0: Greeks
            greeks = _bs_greeks("PUT", strike, spot, dte, dvol_val) if dvol_val > 0 else {}

            suggestions.append({
                "action": "Sell Put",
                "strike": strike,
                "premium": round(premium, 2),
                "dte": dte,
                "apr": round(apr, 1),
                "win_rate": round(win_rate * 100, 1),
                "distance_pct": round(dist, 1),
                "roi_per_trade": round(roi, 1),
                "margin_per_unit": round(margin_per, 0),
                "breakeven": round(breakeven, 0),
                "breakeven_dist_pct": round(breakeven_dist, 1),
                "greeks": greeks,
                "reason": f"APR {apr:.0f}% | 胜率 {win_rate*100:.0f}% | 距离 {dist:.1f}% | 盈亏平衡 ${breakeven:,.0f}",
            })

    elif recommendation in ("sell", "strong_sell"):
        suggestions.append({
            "action": "减仓/对冲",
            "reason": "市场偏空，建议降低风险暴露",
            "具体操作": [
                "平仓近 ATM 的 Sell Put 仓位",
                "买入 OTM Put 对冲下行风险",
                "降低保证金使用率至 30% 以下",
            ],
        })

    else:
        suggestions.append({
            "action": "观望",
            "reason": "信号不明确，建议等待更清晰的方向",
            "具体操作": [
                "维持现有仓位，不加新仓",
                "监控 DVOL 和大单流向变化",
                "设置价格预警（支撑位/阻力位突破）",
            ],
        })

    return suggestions


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run_debate(currency: str = "BTC", quick: bool = False) -> Dict[str, Any]:
    md = _gather_market_data(currency)

    reports = []
    reports.append(_bull_analyst(md))
    reports.append(_bear_analyst(md))
    reports.append(_vol_analyst(md))
    if not quick:
        reports.append(_flow_analyst(md))
    reports.append(_risk_officer(md))

    synthesis = _synthesize(reports, md)

    dvol = md.get("dvol", {})
    market_summary = {
        "spot": round(md.get("spot", 0), 0),
        "data_timestamp": md.get("data_timestamp", ""),
        "dvol": dvol.get("current", 0),
        "dvol_signal": dvol.get("signal", ""),
        "dvol_trend": dvol.get("trend", ""),
        "risk_status": md.get("risk_status", "UNKNOWN"),
        "risk_label": md.get("risk_label", ""),
        "large_trades_count": len(md.get("large_trades", [])),
        "contracts_count": len(md.get("contracts", [])),
        "max_pain": md.get("max_pain", 0),
    }

    return {
        "currency": currency,
        "spot": md.get("spot", 0),
        "reports": reports,
        "synthesis": synthesis,
        "market_data_summary": market_summary,
        "errors": md.get("errors", []),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def save_debate_result(result: Dict[str, Any]) -> bool:
    try:
        import json
        from db.connection import execute_write
        execute_write(
            """INSERT INTO debate_results
               (currency, spot_price, overall_score, recommendation,
                recommendation_label, consensus, reports_json, synthesis_json, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result.get("currency", "BTC"),
                result.get("spot", 0),
                result["synthesis"]["overall_score"],
                result["synthesis"]["recommendation"],
                result["synthesis"]["recommendation_label"],
                result["synthesis"]["consensus"],
                json.dumps(result["reports"], ensure_ascii=False),
                json.dumps(result["synthesis"], ensure_ascii=False),
                result.get("timestamp", datetime.now(timezone.utc).isoformat()),
            )
        )
        return True
    except (RuntimeError, ValueError, TypeError) as e:
        logger.warning("save_debate_result failed: %s", e)
        return False
