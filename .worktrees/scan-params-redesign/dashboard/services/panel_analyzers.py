"""services/panel_analyzers.py - Full panel analyzer configs, rule functions, and LLM prompt templates."""
from typing import Dict, Any, List, Optional


def _make_result(name: str, score: float, verdict: str, reasoning: list = None) -> Any:
    """Lazy factory for RuleResult to avoid circular import at module level."""
    from services.unified_recommendation_engine import RuleResult
    return RuleResult(name=name, score=score, verdict=verdict, reasoning=reasoning or [])


# ============================================================
# Helpers
# ============================================================

def _safe_float(v: Any, default: float = 0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ============================================================
# 14 General-purpose rule functions
# ============================================================

def calc_dvol_signal(data: dict, cache: dict):
    dvol = _safe_float(data.get("dvol", 0))
    dvol_z = _safe_float(data.get("dvol_z", 0))
    if dvol <= 0:
        return _make_result("DVOL信号", 50, "数据缺失")
    if dvol < 50 and dvol_z < -1.0:
        return _make_result("DVOL信号", 85, f"低波动率(IV={dvol})，卖方有利", [f"DVOL={dvol}处于低位", f"Z-Score={dvol_z}<-1，显著低于均值"])
    elif dvol > 70 and dvol_z > 2.0:
        return _make_result("DVOL信号", 15, f"高波动率(IV={dvol})，卖方风险极高", [f"DVOL={dvol}>70 恐慌区间", f"Z-Score={dvol_z}>2，极端偏离"])
    elif dvol > 70:
        return _make_result("DVOL信号", 25, f"偏高波动率(IV={dvol})，需谨慎", [f"DVOL={dvol}>70"])
    elif dvol > 50:
        return _make_result("DVOL信号", 60, f"中等波动率(IV={dvol})，正常操作", [f"DVOL={dvol}处于中位区间"])
    else:
        return _make_result("DVOL信号", 75, f"较低波动率(IV={dvol})，卖方窗口", [f"DVOL={dvol}<50"])


def calc_sentiment(data: dict, cache: dict):
    fg = _safe_float(data.get("fear_greed", 50))
    if fg <= 0:
        return _make_result("市场情绪", 50, "数据缺失")
    if fg <= 25:
        return _make_result("市场情绪", 80, f"极度恐惧({fg})，历史表明是买入机会", [f"恐贪指数={fg}≤25", "极度恐惧常对应市场底部"])
    elif fg <= 45:
        return _make_result("市场情绪", 65, f"偏恐惧({fg})，可逐步建仓", [f"恐贪指数={fg}"])
    elif fg >= 75:
        return _make_result("市场情绪", 25, f"极度贪婪({fg})，市场过热风险", [f"恐贪指数={fg}≥75", "极度贪婪常对应市场顶部"])
    elif fg >= 60:
        return _make_result("市场情绪", 45, f"偏贪婪({fg})，注意回调风险", [f"恐贪指数={fg}"])
    else:
        return _make_result("市场情绪", 55, f"中性情绪({fg})", [f"恐贪指数={fg}处于中性区间"])


def calc_trend_strength(data: dict, cache: dict):
    trend = _safe_float(data.get("trend_strength", 0))
    spot = _safe_float(data.get("spot", 0))
    if trend > 0.5:
        return _make_result("趋势强度", 70, f"上升趋势(强度={trend:.2f})，顺势卖PUT", [f"趋势强度={trend:.2f}>0.5", f"现货={spot}"])
    elif trend < -0.3:
        return _make_result("趋势强度", 30, f"下降趋势(强度={trend:.2f})，卖PUT需更大安全边际", [f"趋势强度={trend:.2f}<-0.3"])
    else:
        return _make_result("趋势强度", 50, f"趋势不明朗(强度={trend:.2f})", [f"趋势强度={trend:.2f}"])


def calc_term_premium(data: dict, cache: dict):
    tp = _safe_float(data.get("term_premium", 0))
    if tp > 5:
        return _make_result("期限溢价", 85, f"陡峭Contango(溢价={tp:.1f}%)，有利于卖方", [f"近月IV < 远月IV，溢价={tp:.1f}%", "适合卖近买远的日历价差"])
    elif tp > 0:
        return _make_result("期限溢价", 65, f"轻微Contango(溢价={tp:.1f}%)", [f"期限溢价={tp:.1f}%"])
    elif tp < -3:
        return _make_result("期限溢价", 25, f"Backwardation(溢价={tp:.1f}%)，近月恐慌", [f"近月IV > 远月IV，倒挂={tp:.1f}%", "可能预示短期风险事件"])
    else:
        return _make_result("期限溢价", 50, f"平坦期限结构(溢价={tp:.1f}%)", [f"期限溢价={tp:.1f}%"])


def calc_iv_steepness(data: dict, cache: dict):
    steep = _safe_float(data.get("iv_steepness", 0))
    if steep > 0.8:
        return _make_result("曲线陡峭度", 75, f"后端陡峭(斜率={steep:.2f})，远月溢价充足", [f"远月IV显著高于近月", "日历价差利润空间大"])
    elif steep < -0.3:
        return _make_result("曲线陡峭度", 30, f"前端翘起(斜率={steep:.2f})，近月风险高", [f"近月IV异常偏高"])
    else:
        return _make_result("曲线陡峭度", 55, f"正常斜率(斜率={steep:.2f})", ["曲线斜率正常"])


def calc_vol_regime(data: dict, cache: dict):
    dvol = _safe_float(data.get("dvol", 0))
    if dvol >= 80:
        return _make_result("波动率区间", 10, f"恐慌区间(DVOL={dvol})，建议暂停操作", [f"DVOL={dvol}≥80", "极端波动，保证金需求极高"])
    elif dvol >= 70:
        return _make_result("波动率区间", 30, f"高波区间(DVOL={dvol})，缩小仓位", [f"DVOL={dvol}≥70"])
    elif dvol >= 50:
        return _make_result("波动率区间", 65, f"中波区间(DVOL={dvol})，正常操作", [f"DVOL={dvol}健康区间"])
    else:
        return _make_result("波动率区间", 80, f"低波区间(DVOL={dvol})，提高仓位", [f"DVOL={dvol}<50", "低波动率环境有利卖方"])


def calc_calendar_spread(data: dict, cache: dict):
    tp = _safe_float(data.get("term_premium", 0))
    steep = _safe_float(data.get("iv_steepness", 0))
    if tp > 5 and steep > 0.5:
        return _make_result("日历价差", 80, "日历价差机会明确，卖近买远", [f"期限溢价{tp:.1f}%>5%且曲线陡峭{steep:.2f}>0.5"])
    elif tp > 2:
        return _make_result("日历价差", 55, "日历价差可考虑，但利润空间一般", [f"期限溢价{tp:.1f}%"])
    else:
        return _make_result("日历价差", 30, "当前不适合日历价差策略", [f"期限溢价{tp:.1f}%不足"])


def calc_skew_signal(data: dict, cache: dict):
    skew = _safe_float(data.get("skew", 0))
    if skew < -5:
        return _make_result("偏度信号", 65, f"显著负偏(skew={skew:.1f})，PUT端溢价较高", ["OTM PUT IV显著高于CALL", "卖PUT收取更高溢价"])
    elif skew > 5:
        return _make_result("偏度信号", 70, f"显著正偏(skew={skew:.1f})，CALL端溢价较高", ["OTM CALL IV显著高于PUT", "卖CALL收取更高溢价"])
    else:
        return _make_result("偏度信号", 50, f"偏度正常(skew={skew:.1f})", ["IV微笑基本对称"])


def calc_smile_morphology(data: dict, cache: dict):
    kurt = _safe_float(data.get("kurtosis", 0))
    if kurt > 1:
        return _make_result("微笑形态", 55, f"肥尾分布(kurt={kurt:.1f})，尾部风险溢价高", [f"峰度={kurt:.1f}>1", "市场定价了尾部风险"])
    elif kurt < -0.5:
        return _make_result("微笑形态", 60, f"瘦尾分布(kurt={kurt:.1f})，风险定价偏低", [f"峰度={kurt:.1f}<-0.5"])
    else:
        return _make_result("微笑形态", 50, f"正常形态(kurt={kurt:.1f})", ["微笑形态正常"])


def calc_pcr_signal(data: dict, cache: dict):
    pcr = _safe_float(data.get("pcr", 1.0))
    if pcr > 1.5:
        return _make_result("PCR信号", 75, f"PCR极高({pcr:.2f})，市场过度恐慌，反向看多", [f"PCR={pcr:.2f}>1.5", "极端值常对应底部"])
    elif pcr > 1.2:
        return _make_result("PCR信号", 60, f"PCR偏高({pcr:.2f})，偏谨慎情绪", [f"PCR={pcr:.2f}>1.2"])
    elif pcr < 0.7:
        return _make_result("PCR信号", 35, f"PCR极低({pcr:.2f})，市场过度乐观", [f"PCR={pcr:.2f}<0.7", "极端低值常对应顶部"])
    elif pcr < 0.9:
        return _make_result("PCR信号", 45, f"PCR偏低({pcr:.2f})，偏乐观情绪", [f"PCR={pcr:.2f}<0.9"])
    else:
        return _make_result("PCR信号", 50, f"PCR正常({pcr:.2f})", ["PCR处于正常区间"])


def calc_large_trades_direction(data: dict, cache: dict):
    trades = data.get("large_trades", [])
    if not trades:
        return _make_result("大单方向", 50, "无大单数据")
    buys = sum(1 for t in trades if t.get("direction") in ("buy", "call_buy", "put_sell"))
    sells = sum(1 for t in trades if t.get("direction") in ("sell", "call_sell", "put_buy"))
    total = len(trades)
    ratio = buys / max(sells, 1)
    if ratio > 1.5:
        return _make_result("大单方向", 70, f"主力偏多(买{buys}/卖{sells}/{total})", [f"买入/卖出={ratio:.1f}>1.5"])
    elif ratio < 0.67:
        return _make_result("大单方向", 30, f"主力偏空(买{buys}/卖{sells}/{total})", [f"买入/卖出={ratio:.1f}<0.67"])
    else:
        return _make_result("大单方向", 50, f"多空均衡(买{buys}/卖{sells}/{total})", ["买入/卖出≈1"])


def calc_opportunity_signal(data: dict, cache: dict):
    contracts = data.get("contracts", [])
    spot = _safe_float(data.get("spot", 0))
    if not contracts or spot <= 0:
        return _make_result("机会信号", 50, "无合约数据")
    puts = [c for c in contracts if c.get("option_type") in ("P", "PUT")]
    top_puts = sorted(puts, key=lambda c: _safe_float(c.get("apr", 0)), reverse=True)[:5]
    if top_puts:
        avg_apr = sum(_safe_float(c.get("apr", 0)) for c in top_puts) / len(top_puts)
        good_count = sum(1 for c in top_puts if _safe_float(c.get("apr", 0)) > 20 and abs(_safe_float(c.get("delta", 1))) < 0.3)
        if avg_apr > 30 and good_count >= 3:
            return _make_result("机会信号", 80, f"PUT端机会丰富(平均APR={avg_apr:.0f}%，优质{good_count}个)", [f"Top5 PUT APR={avg_apr:.0f}%", f"优质合约{good_count}个"])
        elif avg_apr > 15:
            return _make_result("机会信号", 60, f"PUT端有操作空间(平均APR={avg_apr:.0f}%)", [f"Top5 PUT APR={avg_apr:.0f}%"])
    return _make_result("机会信号", 45, "当前无突出机会", ["APR未达到显著水平"])


# ============================================================
# 8 Wrapper functions (wrap existing engines)
# ============================================================

def wrap_risk_framework(data: dict, cache: dict):
    try:
        from services.risk_framework import RiskFramework
        spot = _safe_float(data.get("spot", 0))
        status = RiskFramework.get_status(spot)
        floors = RiskFramework._get_floors()
        regular_floor = floors.get("regular", 0)
        extreme_floor = floors.get("extreme", 0)
        dist_pct = ((spot - regular_floor) / regular_floor * 100) if regular_floor > 0 and spot > 0 else 0
        if status == "extreme":
            return _make_result("风险框架(6因子)", 15, f"极端风险，现货${spot:.0f}接近极端支撑${extreme_floor:.0f}", [f"现货${spot:.0f}", f"距常规支撑{dist_pct:.1f}%", f"极端支撑${extreme_floor:.0f}"])
        elif status == "high":
            return _make_result("风险框架(6因子)", 35, f"高风险，距支撑{dist_pct:.1f}%", [f"现货${spot:.0f}", f"距常规支撑{dist_pct:.1f}%"])
        elif status == "warning":
            return _make_result("风险框架(6因子)", 55, f"警告级别，距支撑{dist_pct:.1f}%", [f"现货${spot:.0f}", f"距常规支撑{dist_pct:.1f}%"])
        else:
            return _make_result("风险框架(6因子)", 75, f"正常，距支撑{dist_pct:.1f}%", [f"现货${spot:.0f}", "安全边际充足"])
    except Exception as e:
        return _make_result("风险框架(6因子)", 50, f"计算失败: {e}")


def wrap_unified_risk(data: dict, cache: dict):
    try:
        from services.unified_risk_assessor import UnifiedRiskAssessor
        spot = _safe_float(data.get("spot", 0))
        assessor = UnifiedRiskAssessor()
        result = assessor.assess(spot, data.get("contracts", []))
        score = _safe_float(result.get("score", 50))
        label = result.get("label", "未知")
        return _make_result("统一风险评估", score, label, [f"综合评分={score}/100", f"风险等级={label}"])
    except Exception as e:
        return _make_result("统一风险评估", 50, f"评估失败: {e}")


def wrap_greeks_analyzer(data: dict, cache: dict):
    try:
        greeks = data.get("greeks", {})
        gex = _safe_float(greeks.get("gex", 0))
        dvol = _safe_float(data.get("dvol", 0))
        if gex > 0 and dvol < 60:
            return _make_result("Greeks风险矩阵", 70, f"GEX正值({gex:.0f})，伽马做市商稳定市场", [f"GEX={gex:.0f}>0", f"DVOL={dvol}<60"])
        elif gex < 0 and dvol > 60:
            return _make_result("Greeks风险矩阵", 25, f"GEX负值({gex:.0f})且高波，市场不稳定", [f"GEX={gex:.0f}<0", f"DVOL={dvol}>60"])
        else:
            return _make_result("Greeks风险矩阵", 50, f"GEX中性({gex:.0f})", [f"GEX={gex:.0f}"])
    except Exception as e:
        return _make_result("Greeks风险矩阵", 50, f"分析失败: {e}")


def wrap_maxpain(data: dict, cache: dict):
    try:
        spot = _safe_float(data.get("spot", 0))
        mp = _safe_float(data.get("max_pain", 0))
        if mp <= 0 or spot <= 0:
            return _make_result("MaxPain磁吸", 50, "无数据")
        dist_pct = abs(spot - mp) / spot * 100
        if dist_pct < 2:
            return _make_result("MaxPain磁吸", 65, f"距MaxPain仅{dist_pct:.1f}%，磁吸效应强", [f"现货${spot:.0f}", f"MaxPain=${mp:.0f}", f"距离{dist_pct:.1f}%"])
        elif dist_pct < 5:
            return _make_result("MaxPain磁吸", 55, f"距MaxPain{dist_pct:.1f}%，有磁吸力", [f"距离{dist_pct:.1f}%"])
        else:
            return _make_result("MaxPain磁吸", 40, f"距MaxPain较远({dist_pct:.1f}%)，磁吸弱", [f"距离{dist_pct:.1f}%>5%"])
    except Exception as e:
        return _make_result("MaxPain磁吸", 50, f"计算失败: {e}")


def wrap_gamma_flip(data: dict, cache: dict):
    try:
        gf = _safe_float(data.get("gamma_flip", 0))
        if gf > 0:
            return _make_result("Gamma Flip", 60, f"Gamma Flip价格${gf:.0f}，当前位置在上方", [f"Gamma Flip=${gf:.0f}"])
        else:
            return _make_result("Gamma Flip", 50, "无Gamma Flip数据")
    except Exception as e:
        return _make_result("Gamma Flip", 50, f"计算失败: {e}")


def wrap_martingale(data: dict, cache: dict):
    try:
        margin_ratio = _safe_float(data.get("margin_ratio", 0.2))
        if margin_ratio > 0.3:
            return _make_result("马丁格尔风险", 25, f"保证金率{margin_ratio:.0%}过高，补仓空间不足", [f"保证金率={margin_ratio:.0%}"])
        elif margin_ratio > 0.2:
            return _make_result("马丁格尔风险", 50, f"保证金率{margin_ratio:.0%}中等", [f"保证金率={margin_ratio:.0%}"])
        else:
            return _make_result("马丁格尔风险", 70, f"保证金率{margin_ratio:.0%}健康，有补仓空间", [f"保证金率={margin_ratio:.0%}"])
    except Exception as e:
        return _make_result("马丁格尔风险", 50, f"计算失败: {e}")


def wrap_money_flow(data: dict, cache: dict):
    try:
        flow = _safe_float(data.get("net_flow", 0))
        if flow > 1000000:
            return _make_result("资金流向", 70, f"显著净流入(${flow/1e6:.1f}M)", [f"净流入${flow/1e6:.1f}M"])
        elif flow < -1000000:
            return _make_result("资金流向", 30, f"显著净流出(${abs(flow)/1e6:.1f}M)", [f"净流出${abs(flow)/1e6:.1f}M"])
        else:
            return _make_result("资金流向", 50, "资金流向中性", [f"净流入=${flow:.0f}"])
    except Exception as e:
        return _make_result("资金流向", 50, f"分析失败: {e}")


def wrap_onchain(data: dict, cache: dict):
    try:
        mvrv = _safe_float(data.get("mvrv", 0))
        if mvrv > 3:
            return _make_result("链上MVRV", 25, f"MVRV={mvrv:.1f}>3，市场过热", [f"MVRV={mvrv:.1f}"])
        elif mvrv > 2:
            return _make_result("链上MVRV", 45, f"MVRV={mvrv:.1f}偏高", [f"MVRV={mvrv:.1f}"])
        elif mvrv < 1:
            return _make_result("链上MVRV", 75, f"MVRV={mvrv:.1f}<1，低估区间", [f"MVRV={mvrv:.1f}"])
        else:
            return _make_result("链上MVRV", 55, f"MVRV={mvrv:.1f}正常", [f"MVRV={mvrv:.1f}"])
    except Exception as e:
        return _make_result("链上MVRV", 50, f"分析失败: {e}")


# ============================================================
# PANEL_CONFIGS registry (16 panels)
# ============================================================

PANEL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "metric_cards": {
        "name": "顶部指标卡",
        "rules": [
            {"id": "dvol_signal", "name": "DVOL信号", "fn": calc_dvol_signal, "weight": 0.35},
            {"id": "sentiment", "name": "市场情绪", "fn": calc_sentiment, "weight": 0.30},
            {"id": "trend_strength", "name": "趋势强度", "fn": calc_trend_strength, "weight": 0.35},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "risk_command_center": {
        "name": "风险指挥中心",
        "rules": [
            {"id": "risk_framework", "name": "RiskFramework", "fn": wrap_risk_framework, "weight": 0.40},
            {"id": "unified_risk", "name": "统一风险评估", "fn": wrap_unified_risk, "weight": 0.35},
            {"id": "greek_risk", "name": "Greeks风险矩阵", "fn": wrap_greeks_analyzer, "weight": 0.25},
        ],
        "signal_formula": "worst_case",
        "default_action": "",
    },
    "strategy_center": {
        "name": "策略推荐中心",
        "rules": [
            {"id": "opportunity", "name": "机会信号", "fn": calc_opportunity_signal, "weight": 1.0},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "greeks_matrix": {
        "name": "Greeks风险矩阵",
        "rules": [
            {"id": "greek_risk", "name": "Greeks风险矩阵", "fn": wrap_greeks_analyzer, "weight": 0.5},
            {"id": "dvol", "name": "DVOL信号", "fn": calc_dvol_signal, "weight": 0.5},
        ],
        "signal_formula": "worst_case",
        "default_action": "",
    },
    "ai_analyst_center": {
        "name": "AI分析中心",
        "rules": [
            {"id": "dvol", "name": "DVOL信号", "fn": calc_dvol_signal, "weight": 0.4},
            {"id": "sentiment", "name": "市场情绪", "fn": calc_sentiment, "weight": 0.3},
            {"id": "trend", "name": "趋势强度", "fn": calc_trend_strength, "weight": 0.3},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "iv_term_structure": {
        "name": "IV期限结构",
        "rules": [
            {"id": "term_premium", "name": "期限溢价", "fn": calc_term_premium, "weight": 0.35},
            {"id": "steepness", "name": "曲线陡峭度", "fn": calc_iv_steepness, "weight": 0.25},
            {"id": "vol_regime", "name": "波动率区间", "fn": calc_vol_regime, "weight": 0.25},
            {"id": "spread", "name": "日历价差", "fn": calc_calendar_spread, "weight": 0.15},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "iv_smile": {
        "name": "IV Smile",
        "rules": [
            {"id": "skew", "name": "偏度信号", "fn": calc_skew_signal, "weight": 0.4},
            {"id": "morphology", "name": "微笑形态", "fn": calc_smile_morphology, "weight": 0.3},
            {"id": "vol_regime", "name": "波动率区间", "fn": calc_vol_regime, "weight": 0.3},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "dvol_trend": {
        "name": "DVOL趋势",
        "rules": [
            {"id": "dvol_signal", "name": "DVOL信号", "fn": calc_dvol_signal, "weight": 0.6},
            {"id": "vol_regime", "name": "波动率区间", "fn": calc_vol_regime, "weight": 0.4},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "pcr_chart": {
        "name": "PCR图表",
        "rules": [
            {"id": "pcr", "name": "PCR信号", "fn": calc_pcr_signal, "weight": 0.5},
            {"id": "sentiment", "name": "市场情绪", "fn": calc_sentiment, "weight": 0.5},
        ],
        "signal_formula": "majority",
        "default_action": "",
    },
    "max_pain": {
        "name": "最大痛点",
        "rules": [
            {"id": "maxpain", "name": "MaxPain磁吸", "fn": wrap_maxpain, "weight": 0.5},
            {"id": "gamma_flip", "name": "Gamma Flip", "fn": wrap_gamma_flip, "weight": 0.5},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "large_trades": {
        "name": "大单追踪",
        "rules": [
            {"id": "direction", "name": "大单方向", "fn": calc_large_trades_direction, "weight": 1.0},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "martingale_sandbox": {
        "name": "马丁格尔沙盒",
        "rules": [
            {"id": "martingale", "name": "马丁格尔风险", "fn": wrap_martingale, "weight": 0.5},
            {"id": "risk", "name": "RiskFramework", "fn": wrap_risk_framework, "weight": 0.5},
        ],
        "signal_formula": "worst_case",
        "default_action": "",
    },
    "opportunities_table": {
        "name": "实时机会列表",
        "rules": [
            {"id": "dvol", "name": "DVOL信号", "fn": calc_dvol_signal, "weight": 0.4},
            {"id": "opportunity", "name": "机会信号", "fn": calc_opportunity_signal, "weight": 0.6},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "gex_chart": {
        "name": "GEX图表",
        "rules": [
            {"id": "greek", "name": "Greeks风险矩阵", "fn": wrap_greeks_analyzer, "weight": 0.5},
            {"id": "vol_regime", "name": "波动率区间", "fn": calc_vol_regime, "weight": 0.5},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "money_flow": {
        "name": "资金流向",
        "rules": [
            {"id": "flow", "name": "资金流向", "fn": wrap_money_flow, "weight": 0.6},
            {"id": "sentiment", "name": "市场情绪", "fn": calc_sentiment, "weight": 0.4},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "onchain_metrics": {
        "name": "链上指标",
        "rules": [
            {"id": "onchain", "name": "链上MVRV", "fn": wrap_onchain, "weight": 0.6},
            {"id": "sentiment", "name": "市场情绪", "fn": calc_sentiment, "weight": 0.4},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
}


# ============================================================
# LLM Prompt Templates (16 panels)
# ============================================================

LLM_PROMPT_TEMPLATES: Dict[str, Dict[str, str]] = {
    "iv_term_structure": {
        "synthesis": "你是加密货币期权波动率结构分析师。基于以下数据分析{currency}的IV期限结构:\n- 现货: ${spot}\n- 期限溢价: {term_premium}%\n- DVOL: {dvol}(z={dvol_z})\n- 规则评分:\n{rule_scores}\n\n请从卖方角度给出结构判断和操作建议。",
        "bull_context": "期限结构利多因素:\n- 陡峭Contango有利于日历价差卖方\n- 远月IV溢价提供缓冲\n- 低DVOL环境下保证金成本低",
        "bear_context": "期限结构利空因素:\n- 近月IV异常偏高可能预示风险事件\n- Backwardation结构对卖方不利\n- 高DVOL挤压利润空间",
        "judge_criteria": "从风险收益比角度判定整体结构方向，给出具体操作建议。",
    },
    "risk_command_center": {
        "synthesis": "你是加密货币风险管理专家。评估{currency}当前风险:\n- 现货: ${spot}\n- DVOL: {dvol}(z={dvol_z})\n- 规则评分:\n{rule_scores}\n\n请综合评估风险并给出对冲建议。",
        "bull_context": "风险偏低因素:\n- 安全边际充足\n- DVOL处于健康区间\n- 无极端风险信号",
        "bear_context": "风险偏高因素:\n- 接近支撑位\n- DVOL偏高\n- 极端风险信号触发",
        "judge_criteria": "评估综合风险等级并给出仓位管理和对冲建议。",
    },
    "metric_cards": {
        "synthesis": "你是加密货币宏观分析师。快速评估{currency}市场全景:\n- 现货: ${spot}\n- DVOL: {dvol}(z={dvol_z})\n- 恐贪指数: {fear_greed}\n- 规则评分:\n{rule_scores}\n\n用3-5句话概括当前市场状态。",
        "bull_context": "宏观利多:\n- 低波动率 + 恐惧情绪 = 卖权机会\n- 资金费率正常",
        "bear_context": "宏观利空:\n- 高波动率 + 贪婪情绪 = 风险累积\n- 关注反转信号",
        "judge_criteria": "简短判断市场方向，给1个最具体的操作建议。",
    },
    "iv_smile": {
        "synthesis": "你是波动率曲面分析师。分析{currency}的IV Smile形态:\n- 现货: ${spot}\n- 偏度: {skew}\n- DVOL: {dvol}(z={dvol_z})\n- 规则评分:\n{rule_scores}\n\n判断哪个方向的行权价被高估/低估。",
        "bull_context": "Smile利多:\n- 负偏意味着PUT溢价更高 → 卖PUT更有利\n- OTM PUT IV溢价充足",
        "bear_context": "Smile利空:\n- 正偏意味着CALL溢价更高 → 方向性看涨信号\n- 但卖PUT利润降低",
        "judge_criteria": "判断最佳卖权方向和行权价区间。",
    },
    "dvol_trend": {
        "synthesis": "你是波动率分析专家。分析{currency}的DVOL趋势:\n- DVOL: {dvol}(z={dvol_z})\n- 规则评分:\n{rule_scores}\n\n判断波动率区间和均值回归方向。",
        "bull_context": "低波环境:\n- DVOL低于历史中位数\n- 有利于卖方策略\n- 可适当放大仓位",
        "bear_context": "高波环境:\n- DVOL高于历史中位数\n- 卖方风险加大\n- 建议缩小仓位或等待回归",
        "judge_criteria": "给出波动率区间判断、预期回归时间、仓位调整建议。",
    },
    "pcr_chart": {
        "synthesis": "你是市场情绪分析师。分析{currency}的PCR:\n- PCR: {pcr}\n- 恐贪指数: {fear_greed}\n- 规则评分:\n{rule_scores}\n\n判断市场情绪极端程度。",
        "bull_context": "PCR看多:\n- PCR极高表示市场过度恐慌 → 反向看多\n- 历史数据显示极端PCR常对应底部",
        "bear_context": "PCR看空:\n- PCR极低表示市场过度乐观 → 谨慎看空\n- 但趋势市中PCR可持续低位",
        "judge_criteria": "判断情绪极端度和反向操作机会。",
    },
    "max_pain": {
        "synthesis": "你是期权到期日分析师。分析{currency}的MaxPain:\n- 现货: ${spot}\n- MaxPain: {max_pain}\n- 规则评分:\n{rule_scores}\n\n判断到期日前的价格磁吸效应。",
        "bull_context": "MaxPain利多:\n- 现货高于MaxPain，市场偏强\n- 磁吸力可能拉回但未必",
        "bear_context": "MaxPain利空:\n- 现货低于MaxPain，有下压阻力\n- Gamma Flip可能加剧下跌",
        "judge_criteria": "判断到期日价格区间和GEX/Gamma影响。",
    },
    "large_trades": {
        "synthesis": "你是订单流分析师。分析{currency}的大单动向:\n- 大单数据: {large_trades_summary}\n- 规则评分:\n{rule_scores}\n\n判断聪明钱方向。",
        "bull_context": "大单利多:\n- 主力买入期权/卖出PUT\n- 大单偏向买方",
        "bear_context": "大单利空:\n- 主力买入PUT/卖出CALL\n- 大单偏向卖方",
        "judge_criteria": "判断主力方向并评估跟随价值。",
    },
    "martingale_sandbox": {
        "synthesis": "你是风险量化分析师。评估马丁格尔策略风险:\n- 现货: ${spot}\n- 保证金率: {margin_ratio}\n- 规则评分:\n{rule_scores}\n\n评估补仓空间和爆仓风险。",
        "bull_context": "低风险:\n- 保证金充足，有多次补仓空间\n- 支撑位坚实",
        "bear_context": "高风险:\n- 保证金紧张，补仓空间有限\n- 接近极端支撑位",
        "judge_criteria": "给出最大可承受跌幅、补仓点位建议、止损条件。",
    },
    "opportunities_table": {
        "synthesis": "你是期权策略筛选专家。分析当前机会列表:\n- 现货: ${spot}\n- DVOL: {dvol}(z={dvol_z})\n- 规则评分:\n{rule_scores}\n\n筛选最佳交易机会。",
        "bull_context": "机会丰富:\n- 高APR + 低风险 = 最佳交易窗口\n- 多个合约满足策略条件",
        "bear_context": "机会稀少:\n- 当前无高性价比合约\n- 建议等待更好时机",
        "judge_criteria": "推荐1-3个最佳合约并给出持仓建议。",
    },
    "strategy_center": {
        "synthesis": "你是期权策略师。分析{currency}的策略推荐:\n- 现货: ${spot}\n- DVOL: {dvol}(z={dvol_z})\n- 规则评分:\n{rule_scores}\n\n给出具体交易计划。",
        "bull_context": "策略利多:\n- 多因子共振，操作胜率提升\n- 确定性强",
        "bear_context": "策略利空:\n- 信号分歧，需降低仓位\n- 某些因子发出警告",
        "judge_criteria": "给出具体策略（方向/期限/行权价/仓位/止损）。",
    },
    "greeks_matrix": {
        "synthesis": "你是期权Greeks专家。分析{currency}的Greeks风险:\n- 现货: ${spot}\n- GEX: {gex}\n- DVOL: {dvol}(z={dvol_z})\n- 规则评分:\n{rule_scores}\n\n评估希腊字母风险敞口。",
        "bull_context": "Greeks利多:\n- GEX正值 → 做市商稳定市场\n- 低Gamma → 低波动预期",
        "bear_context": "Greeks利空:\n- GEX负值 → 做市商放大波动\n- 高Gamma → 高波动风险",
        "judge_criteria": "给出Delta/Gamma/Vega敞口建议。",
    },
    "gex_chart": {
        "synthesis": "你是GEX分析专家。分析{currency}的GEX水平:\n- 现货: ${spot}\n- GEX: {gex}\n- DVOL: {dvol}(z={dvol_z})\n- 规则评分:\n{rule_scores}\n\n判断Gamma暴露对市场的影响。",
        "bull_context": "GEX利多: 正值GEX压制波动，利于卖方",
        "bear_context": "GEX利空: 负值GEX放大波动，谨慎卖方",
        "judge_criteria": "判断GEX水平和方向性影响。",
    },
    "money_flow": {
        "synthesis": "你是资金流向分析师。分析{currency}的资金流向:\n- 净流入: {net_flow}\n- 规则评分:\n{rule_scores}\n\n判断资金面偏多/偏空。",
        "bull_context": "资金面利多: 显著净流入支撑价格",
        "bear_context": "资金面利空: 净流出暗示撤离",
        "judge_criteria": "判断资金面方向并给出交易建议。",
    },
    "onchain_metrics": {
        "synthesis": "你是链上数据分析师。分析{currency}的链上指标:\n- MVRV: {mvrv}\n- 规则评分:\n{rule_scores}\n\n判断长期估值区间。",
        "bull_context": "链上利多: MVRV低估区间 → 长期买入机会",
        "bear_context": "链上利空: MVRV高估区间 → 注意泡沫风险",
        "judge_criteria": "判断估值区间并给出长期仓位建议。",
    },
    "ai_analyst_center": {
        "synthesis": "总览{currency}的整体市场状态:\n- 现货: ${spot}\n- DVOL: {dvol}(z={dvol_z})\n- 规则评分:\n{rule_scores}\n\n概括当前最关键的3个信号。",
        "bull_context": "总体偏多",
        "bear_context": "总体偏空",
        "judge_criteria": "3句话总结 + 1个核心建议。",
    },
}


# ============================================================
# get_llm_prompt
# ============================================================

def get_llm_prompt(panel_id: str) -> Dict[str, str]:
    """获取面板的 LLM prompt 模板"""
    default = {
        "synthesis": "分析{currency}的市场状态:\n- 规则评分:\n{rule_scores}\n\n给出操作建议。",
        "bull_context": "利多因素可能包括: 低波动率、恐惧情绪、强支撑位",
        "bear_context": "利空因素可能包括: 高波动率、贪婪情绪、接近阻力位",
        "judge_criteria": "综合判断方向，给出具体建议。",
    }
    return LLM_PROMPT_TEMPLATES.get(panel_id, default)
