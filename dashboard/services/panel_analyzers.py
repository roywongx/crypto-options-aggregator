"""services/panel_analyzers.py
16个面板的规则配置 + 规则函数 + LLM prompt 模板

每个面板定义:
  - data_sources: 数据来源列表
  - rules: [规则函数引用]
  - signal_formula: weighted_score | worst_case | majority
  - default_action: 默认操作建议
"""
from typing import Dict, Any


# ============================================================
# 延迟导入 RuleResult（避免循环依赖）
# ============================================================

def _make_result(name: str = "", score: float = 0, max_score: float = 100.0,
                 verdict: str = "", reasoning: list = None) -> 'RuleResult':
    from services.unified_recommendation_engine import RuleResult
    return RuleResult(name=name, score=score, max_score=max_score,
                      verdict=verdict, reasoning=reasoning or [])


# ============================================================
# 工具函数
# ============================================================

def _safe_float(v, default: float = 0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ============================================================
# 通用规则函数
# ============================================================

def calc_dvol_signal(data: dict, cache: dict):
    """DVOL 波动率信号"""
    dvol = _safe_float(data.get("dvol", 0))
    dvol_z = _safe_float(data.get("dvol_z", 0))

    if dvol <= 0:
        return _make_result(name="DVOL信号", score=50, verdict="数据缺失")

    if dvol < 50 and dvol_z < -1.0:
        return _make_result(name="DVOL信号", score=85,
                            verdict=f"低波动率(IV={dvol})，卖方有利",
                            reasoning=[f"DVOL={dvol}处于低位", f"Z-Score={dvol_z}<-1，显著低于均值"])
    elif dvol > 70 and dvol_z > 2.0:
        return _make_result(name="DVOL信号", score=15,
                            verdict=f"高波动率(IV={dvol})，卖方风险极高",
                            reasoning=[f"DVOL={dvol}>70 恐慌区间", f"Z-Score={dvol_z}>2，极端偏离"])
    elif dvol > 70:
        return _make_result(name="DVOL信号", score=25,
                            verdict=f"偏高波动率(IV={dvol})，需谨慎",
                            reasoning=[f"DVOL={dvol}>70"])
    elif dvol > 50:
        return _make_result(name="DVOL信号", score=60,
                            verdict=f"中等波动率(IV={dvol})，正常操作",
                            reasoning=[f"DVOL={dvol} 处于中位区间"])
    else:
        return _make_result(name="DVOL信号", score=75,
                            verdict=f"较低波动率(IV={dvol})，卖方窗口",
                            reasoning=[f"DVOL={dvol}<50"])


def calc_sentiment(data: dict, cache: dict):
    """市场情绪（恐惧贪婪指数）"""
    fg = _safe_float(data.get("fear_greed", 50))
    if fg <= 0:
        return _make_result(name="市场情绪", score=50, verdict="数据缺失")
    if fg <= 25:
        return _make_result(name="市场情绪", score=80,
                            verdict=f"极度恐惧({fg})，历史表明是买入机会",
                            reasoning=[f"恐贪指数={fg}≤25", "极度恐惧常对应市场底部"])
    elif fg <= 45:
        return _make_result(name="市场情绪", score=65,
                            verdict=f"偏恐惧({fg})，可逐步建仓",
                            reasoning=[f"恐贪指数={fg}", "恐惧区间往往酝酿机会"])
    elif fg >= 75:
        return _make_result(name="市场情绪", score=25,
                            verdict=f"极度贪婪({fg})，市场过热风险",
                            reasoning=[f"恐贪指数={fg}≥75", "极度贪婪常对应市场顶部"])
    elif fg >= 60:
        return _make_result(name="市场情绪", score=45,
                            verdict=f"偏贪婪({fg})，注意回调风险",
                            reasoning=[f"恐贪指数={fg}"])
    else:
        return _make_result(name="市场情绪", score=55,
                            verdict=f"中性情绪({fg})",
                            reasoning=[f"恐贪指数={fg} 处于中性区间"])


def calc_trend_strength(data: dict, cache: dict):
    """价格趋势强度"""
    trend = _safe_float(data.get("trend_strength", 0))
    spot = _safe_float(data.get("spot", 0))
    if trend > 0.5:
        return _make_result(name="趋势强度", score=70,
                            verdict=f"上升趋势(强度={trend:.2f})，顺势卖PUT",
                            reasoning=[f"趋势强度={trend:.2f}>0.5", f"现货={spot}"])
    elif trend < -0.3:
        return _make_result(name="趋势强度", score=30,
                            verdict=f"下降趋势(强度={trend:.2f})，卖PUT需更大安全边际",
                            reasoning=[f"趋势强度={trend:.2f}<-0.3"])
    else:
        return _make_result(name="趋势强度", score=50,
                            verdict=f"趋势不明朗(强度={trend:.2f})",
                            reasoning=[f"趋势强度={trend:.2f}"])


def calc_term_premium(data: dict, cache: dict):
    """IV期限溢价"""
    tp = _safe_float(data.get("term_premium", 0))
    if tp > 5:
        return _make_result(name="期限溢价", score=85,
                            verdict=f"陡峭Contango(溢价={tp:.1f}%)，有利于卖方",
                            reasoning=[f"近月IV < 远月IV，溢价={tp:.1f}%", "适合卖近买远的日历价差"])
    elif tp > 0:
        return _make_result(name="期限溢价", score=65,
                            verdict=f"轻微Contango(溢价={tp:.1f}%)",
                            reasoning=[f"期限溢价={tp:.1f}%"])
    elif tp < -3:
        return _make_result(name="期限溢价", score=25,
                            verdict=f"Backwardation(溢价={tp:.1f}%)，近月恐慌",
                            reasoning=[f"近月IV > 远月IV，倒挂={tp:.1f}%", "可能预示短期风险事件"])
    else:
        return _make_result(name="期限溢价", score=50,
                            verdict=f"平坦期限结构(溢价={tp:.1f}%)",
                            reasoning=[f"期限溢价={tp:.1f}%"])


def calc_iv_steepness(data: dict, cache: dict):
    """IV曲线陡峭度"""
    steep = _safe_float(data.get("iv_steepness", 0))
    if steep > 0.8:
        return _make_result(name="曲线陡峭度", score=75,
                            verdict=f"后端陡峭(斜率={steep:.2f})，远月溢价充足",
                            reasoning=["远月IV显著高于近月", "日历价差利润空间大"])
    elif steep < -0.3:
        return _make_result(name="曲线陡峭度", score=30,
                            verdict=f"前端翘起(斜率={steep:.2f})，近月风险高",
                            reasoning=["近月IV异常偏高"])
    else:
        return _make_result(name="曲线陡峭度", score=55,
                            verdict=f"正常斜率(斜率={steep:.2f})",
                            reasoning=["曲线斜率正常"])


def calc_vol_regime(data: dict, cache: dict):
    """波动率区间判断"""
    dvol = _safe_float(data.get("dvol", 0))
    if dvol >= 80:
        return _make_result(name="波动率区间", score=10,
                            verdict=f"恐慌区间(DVOL={dvol})，建议暂停操作",
                            reasoning=[f"DVOL={dvol}≥80", "极端波动，保证金需求极高"])
    elif dvol >= 70:
        return _make_result(name="波动率区间", score=30,
                            verdict=f"高波区间(DVOL={dvol})，缩小仓位",
                            reasoning=[f"DVOL={dvol}≥70"])
    elif dvol >= 50:
        return _make_result(name="波动率区间", score=65,
                            verdict=f"中波区间(DVOL={dvol})，正常操作",
                            reasoning=[f"DVOL={dvol} 健康区间"])
    else:
        return _make_result(name="波动率区间", score=80,
                            verdict=f"低波区间(DVOL={dvol})，提高仓位",
                            reasoning=[f"DVOL={dvol}<50", "低波动率环境有利卖方"])


def calc_calendar_spread(data: dict, cache: dict):
    """日历价差机会判定"""
    tp = _safe_float(data.get("term_premium", 0))
    steep = _safe_float(data.get("iv_steepness", 0))
    if tp > 5 and steep > 0.5:
        return _make_result(name="日历价差", score=80,
                            verdict="日历价差机会明确，卖近买远",
                            reasoning=[f"期限溢价{tp:.1f}%>5%且曲线陡峭{steep:.2f}>0.5"])
    elif tp > 2:
        return _make_result(name="日历价差", score=55,
                            verdict="日历价差可考虑，但利润空间一般",
                            reasoning=[f"期限溢价{tp:.1f}%"])
    else:
        return _make_result(name="日历价差", score=30,
                            verdict="当前不适合日历价差策略",
                            reasoning=[f"期限溢价{tp:.1f}%不足"])


def calc_skew_signal(data: dict, cache: dict):
    """IV偏度信号"""
    skew = _safe_float(data.get("skew", 0))
    if skew < -5:
        return _make_result(name="偏度信号", score=65,
                            verdict=f"显著负偏(skew={skew:.1f})，PUT端溢价较高",
                            reasoning=["OTM PUT IV显著高于CALL", "卖PUT收取更高溢价"])
    elif skew > 5:
        return _make_result(name="偏度信号", score=70,
                            verdict=f"显著正偏(skew={skew:.1f})，CALL端溢价较高",
                            reasoning=["OTM CALL IV显著高于PUT", "卖CALL收取更高溢价"])
    else:
        return _make_result(name="偏度信号", score=50,
                            verdict=f"偏度正常(skew={skew:.1f})",
                            reasoning=["IV微笑基本对称"])


def calc_smile_morphology(data: dict, cache: dict):
    """IV微笑形态"""
    kurt = _safe_float(data.get("kurtosis", 0))
    if kurt > 1:
        return _make_result(name="微笑形态", score=55,
                            verdict=f"肥尾分布(kurt={kurt:.1f})，尾部风险溢价高",
                            reasoning=[f"峰度={kurt:.1f}>1", "市场定价了尾部风险"])
    elif kurt < -0.5:
        return _make_result(name="微笑形态", score=60,
                            verdict=f"瘦尾分布(kurt={kurt:.1f})，风险定价偏低",
                            reasoning=[f"峰度={kurt:.1f}<-0.5"])
    else:
        return _make_result(name="微笑形态", score=50,
                            verdict=f"正常形态(kurt={kurt:.1f})",
                            reasoning=["微笑形态正常"])


def calc_pcr_signal(data: dict, cache: dict):
    """Put/Call Ratio 信号"""
    pcr = _safe_float(data.get("pcr", 1.0))
    if pcr > 1.5:
        return _make_result(name="PCR信号", score=75,
                            verdict=f"PCR极高({pcr:.2f})，市场过度恐慌，反向看多",
                            reasoning=[f"PCR={pcr:.2f}>1.5", "极端值常对应底部"])
    elif pcr > 1.2:
        return _make_result(name="PCR信号", score=60,
                            verdict=f"PCR偏高({pcr:.2f})，偏谨慎情绪",
                            reasoning=[f"PCR={pcr:.2f}>1.2"])
    elif pcr < 0.7:
        return _make_result(name="PCR信号", score=35,
                            verdict=f"PCR极低({pcr:.2f})，市场过度乐观",
                            reasoning=[f"PCR={pcr:.2f}<0.7", "极端低值常对应顶部"])
    elif pcr < 0.9:
        return _make_result(name="PCR信号", score=45,
                            verdict=f"PCR偏低({pcr:.2f})，偏乐观情绪",
                            reasoning=[f"PCR={pcr:.2f}<0.9"])
    else:
        return _make_result(name="PCR信号", score=50,
                            verdict=f"PCR正常({pcr:.2f})",
                            reasoning=["PCR处于正常区间"])


def calc_large_trades_direction(data: dict, cache: dict):
    """大单方向判断"""
    trades = data.get("large_trades", [])
    if not trades:
        return _make_result(name="大单方向", score=50, verdict="无大单数据")
    buys = sum(1 for t in trades if t.get("direction") in ("buy", "call_buy", "put_sell"))
    sells = sum(1 for t in trades if t.get("direction") in ("sell", "call_sell", "put_buy"))
    total = len(trades)
    ratio = buys / max(sells, 1)
    if ratio > 1.5:
        return _make_result(name="大单方向", score=70,
                            verdict=f"主力偏多(买{buys}/卖{sells}/{total})",
                            reasoning=[f"买入/卖出={ratio:.1f}>1.5"])
    elif ratio < 0.67:
        return _make_result(name="大单方向", score=30,
                            verdict=f"主力偏空(买{buys}/卖{sells}/{total})",
                            reasoning=[f"买入/卖出={ratio:.1f}<0.67"])
    else:
        return _make_result(name="大单方向", score=50,
                            verdict=f"多空均衡(买{buys}/卖{sells}/{total})",
                            reasoning=[f"买入/卖出≈1"])


def calc_opportunity_signal(data: dict, cache: dict):
    """机会质量评分"""
    contracts = data.get("contracts", [])
    if not contracts:
        return _make_result(name="机会质量", score=40, verdict="无可用合约数据",
                            reasoning=["合约列表为空"])
    quality_scores = [c.get("quality_score", 0) for c in contracts if isinstance(c, dict)]
    if not quality_scores:
        return _make_result(name="机会质量", score=40, verdict="合约数据无质量评分",
                            reasoning=["缺少quality_score字段"])
    avg_q = sum(quality_scores) / len(quality_scores)
    high_quality = sum(1 for q in quality_scores if q >= 70)
    if avg_q >= 70:
        return _make_result(name="机会质量", score=80,
                            verdict=f"高质量机会充足(均分={avg_q:.0f}，优质{high_quality}个)",
                            reasoning=[f"均分={avg_q:.0f}", f"优质合约={high_quality}/{len(quality_scores)}"])
    elif avg_q >= 50:
        return _make_result(name="机会质量", score=60,
                            verdict=f"机会质量尚可(均分={avg_q:.0f})",
                            reasoning=[f"均分={avg_q:.0f}"])
    else:
        return _make_result(name="机会质量", score=35,
                            verdict=f"机会质量偏低(均分={avg_q:.0f})",
                            reasoning=[f"均分={avg_q:.0f}"])


# ============================================================
# 包装器（包装现有引擎输出）
# ============================================================

def wrap_risk_framework(data: dict, cache: dict):
    """包装 RiskFramework.get_status()"""
    try:
        from services.risk_framework import RiskFramework
        spot = _safe_float(data.get("spot", 0))
        status = RiskFramework.get_status(spot)
        floors = RiskFramework._get_floors()
        regular_floor = floors.get("regular", 0)
        extreme_floor = floors.get("extreme", 0)
        dist_pct = ((spot - regular_floor) / regular_floor * 100) if regular_floor > 0 and spot > 0 else 0

        if status == "extreme":
            return _make_result(name="风险框架", score=15,
                                verdict=f"极端风险区(距常规支撑{dist_pct:.1f}%)",
                                reasoning=[f"现货={spot}", f"常规支撑={regular_floor}",
                                           f"极端支撑={extreme_floor}", "建议暂停操作"])
        elif status == "high_risk":
            return _make_result(name="风险框架", score=35,
                                verdict=f"高风险区(距常规支撑{dist_pct:.1f}%)",
                                reasoning=[f"现货={spot}", f"常规支撑={regular_floor}"])
        elif status == "normal":
            return _make_result(name="风险框架", score=75,
                                verdict=f"正常区域(距常规支撑{dist_pct:.1f}%)",
                                reasoning=[f"现货={spot}高于常规支撑{regular_floor}"])
        else:
            return _make_result(name="风险框架", score=65,
                                verdict=f"安全区域(距常规支撑{dist_pct:.1f}%)",
                                reasoning=[f"现货={spot}", f"常规支撑={regular_floor}"])
    except Exception as e:
        return _make_result(name="风险框架", score=50,
                            verdict=f"风险引擎不可用: {e}")


def wrap_unified_risk(data: dict, cache: dict):
    """包装 UnifiedRiskAssessor.assess_comprehensive_risk()"""
    try:
        from services.unified_risk_assessor import UnifiedRiskAssessor
        spot = _safe_float(data.get("spot", 0))
        assessor = UnifiedRiskAssessor()
        result = assessor.assess_comprehensive_risk(spot)
        risk_score = result.get("overall_risk", 50)

        if risk_score >= 70:
            return _make_result(name="统一风险评估", score=15,
                                verdict=f"综合风险极高(评分={risk_score})",
                                reasoning=[f"综合风险评分={risk_score}"])
        elif risk_score >= 50:
            return _make_result(name="统一风险评估", score=40,
                                verdict=f"综合风险偏高(评分={risk_score})",
                                reasoning=[f"综合风险评分={risk_score}"])
        elif risk_score >= 30:
            return _make_result(name="统一风险评估", score=65,
                                verdict=f"风险可控(评分={risk_score})",
                                reasoning=[f"综合风险评分={risk_score}"])
        else:
            return _make_result(name="统一风险评估", score=85,
                                verdict=f"低风险环境(评分={risk_score})",
                                reasoning=[f"综合风险评分={risk_score}"])
    except Exception as e:
        return _make_result(name="统一风险评估", score=50,
                            verdict=f"风险评估引擎不可用: {e}")


def wrap_greeks_analyzer(data: dict, cache: dict):
    """包装 GreeksAnalyzer.analyze()"""
    try:
        from services.greeks_analyzer import GreeksAnalyzer
        contracts = data.get("contracts", [])
        spot = _safe_float(data.get("spot", 0))
        if not contracts:
            return _make_result(name="Greeks分析", score=50, verdict="无合约数据")
        greeks_result = GreeksAnalyzer.analyze(contracts, spot)
        gex = _safe_float(greeks_result.get("total_gex", 0))
        if gex > 0:
            return _make_result(name="Greeks分析", score=60,
                                verdict=f"正GEX({gex:.0f})，市场稳定力量较强",
                                reasoning=[f"总GEX={gex:.0f}>0", "正GEX抑制波动"])
        elif gex < 0:
            return _make_result(name="Greeks分析", score=35,
                                verdict=f"负GEX({gex:.0f})，可能放大波动",
                                reasoning=[f"总GEX={gex:.0f}<0", "负GEX加剧波动"])
        else:
            return _make_result(name="Greeks分析", score=50,
                                verdict="GEX中性",
                                reasoning=["总GEX≈0"])
    except Exception as e:
        return _make_result(name="Greeks分析", score=50,
                            verdict=f"Greeks引擎不可用: {e}")


def wrap_maxpain(data: dict, cache: dict):
    """包装 MaxPain 分析"""
    max_pain = _safe_float(data.get("max_pain", 0))
    spot = _safe_float(data.get("spot", 0))
    if max_pain <= 0:
        return _make_result(name="MaxPain", score=50, verdict="无MaxPain数据")
    dist_pct = ((spot - max_pain) / max_pain * 100) if max_pain > 0 else 0
    if abs(dist_pct) < 2:
        return _make_result(name="MaxPain", score=40,
                            verdict=f"现货接近MaxPain({max_pain})，磁吸效应强",
                            reasoning=[f"偏离仅{dist_pct:.1f}%", "到期前可能向MaxPain靠拢"])
    elif dist_pct > 5:
        return _make_result(name="MaxPain", score=60,
                            verdict=f"现货高于MaxPain({max_pain})，上方压力有限",
                            reasoning=[f"偏离{dist_pct:.1f}%", "MaxPain有上移空间"])
    else:
        return _make_result(name="MaxPain", score=50,
                            verdict=f"现货偏离MaxPain({max_pain})适中",
                            reasoning=[f"偏离{dist_pct:.1f}%"])


def wrap_gamma_flip(data: dict, cache: dict):
    """包装 Gamma Flip 风险检测"""
    spot = _safe_float(data.get("spot", 0))
    flip_level = _safe_float(data.get("gamma_flip", 0))
    if flip_level <= 0:
        return _make_result(name="GammaFlip", score=50, verdict="无GammaFlip数据")
    dist_pct = ((spot - flip_level) / flip_level * 100) if flip_level > 0 else 0
    if abs(dist_pct) < 1:
        return _make_result(name="GammaFlip", score=20,
                            verdict=f"接近GammaFlip({flip_level})，方向转换风险极高",
                            reasoning=[f"偏离仅{dist_pct:.1f}%", "一旦翻转可能引发快速行情"])
    elif dist_pct < -3:
        return _make_result(name="GammaFlip", score=35,
                            verdict=f"已跌破GammaFlip({flip_level})，空方主导",
                            reasoning=[f"偏离{dist_pct:.1f}%", "负GEX环境"])
    elif dist_pct > 3:
        return _make_result(name="GammaFlip", score=65,
                            verdict=f"远离GammaFlip({flip_level})，多方安全边际充足",
                            reasoning=[f"偏离{dist_pct:.1f}%"])
    else:
        return _make_result(name="GammaFlip", score=45,
                            verdict=f"接近GammaFlip({flip_level})，需关注",
                            reasoning=[f"偏离{dist_pct:.1f}%"])


def wrap_martingale(data: dict, cache: dict):
    """包装 Martingale 补仓风险分析"""
    try:
        from services.martingale_sandbox import MartingaleSandboxEngine
        contracts = data.get("contracts", [])
        if not contracts or not isinstance(contracts, list):
            return _make_result(name="补仓风险", score=50, verdict="无合约数据")
        candidate = contracts[0] if contracts else {}
        if not candidate:
            return _make_result(name="补仓风险", score=50, verdict="无有效合约")
        strike = _safe_float(candidate.get("strike", 0))
        crash_price = strike * 0.7
        loss = MartingaleSandboxEngine.calculate_loss(strike, crash_price, 1.0, candidate.get("option_type", "PUT"))
        if loss > 5000:
            return _make_result(name="补仓风险", score=25,
                                verdict=f"单次补仓损失高(≈{loss:.0f} USD)",
                                reasoning=[f"估算损失={loss:.0f} USD"])
        elif loss > 2000:
            return _make_result(name="补仓风险", score=45,
                                verdict=f"补仓损失可控(≈{loss:.0f} USD)",
                                reasoning=[f"估算损失={loss:.0f} USD"])
        else:
            return _make_result(name="补仓风险", score=65,
                                verdict=f"补仓风险低(≈{loss:.0f} USD)",
                                reasoning=[f"估算损失={loss:.0f} USD"])
    except Exception as e:
        return _make_result(name="补仓风险", score=50,
                            verdict=f"补仓引擎不可用: {e}")


def wrap_money_flow(data: dict, cache: dict):
    """包装资金流向分析"""
    flow_direction = data.get("flow_direction", data.get("money_flow_direction", "neutral"))
    flow_strength = _safe_float(data.get("flow_strength", data.get("money_flow_strength", 0)))
    if flow_direction == "inflow":
        if flow_strength > 0.5:
            return _make_result(name="资金流向", score=75,
                                verdict=f"强势流入(强度={flow_strength:.2f})",
                                reasoning=["资金持续流入", "对现货有支撑作用"])
        return _make_result(name="资金流向", score=60,
                            verdict=f"温和流入(强度={flow_strength:.2f})",
                            reasoning=["资金偏多"])
    elif flow_direction == "outflow":
        if flow_strength > 0.5:
            return _make_result(name="资金流向", score=25,
                                verdict=f"强势流出(强度={flow_strength:.2f})",
                                reasoning=["资金持续流出", "对现货构成压力"])
        return _make_result(name="资金流向", score=40,
                            verdict=f"温和流出(强度={flow_strength:.2f})",
                            reasoning=["资金偏空"])
    else:
        return _make_result(name="资金流向", score=50,
                            verdict="资金流向中性",
                            reasoning=["多空资金基本均衡"])


def wrap_onchain(data: dict, cache: dict):
    """包装链上指标分析"""
    onchain = data.get("onchain", {})
    if not isinstance(onchain, dict) or not onchain:
        return _make_result(name="链上指标", score=50, verdict="无链上数据")
    mvrv_z = _safe_float(onchain.get("mvrv_z", 0))
    if mvrv_z > 3:
        return _make_result(name="链上指标", score=25,
                            verdict=f"MVRV-Z极高({mvrv_z:.1f})，市场高估",
                            reasoning=[f"MVRV-Z={mvrv_z:.1f}>3", "历史顶部区间"])
    elif mvrv_z > 1:
        return _make_result(name="链上指标", score=50,
                            verdict=f"MVRV-Z偏高({mvrv_z:.1f})",
                            reasoning=[f"MVRV-Z={mvrv_z:.1f}"])
    elif mvrv_z < -1:
        return _make_result(name="链上指标", score=80,
                            verdict=f"MVRV-Z极低({mvrv_z:.1f})，市场低估",
                            reasoning=[f"MVRV-Z={mvrv_z:.1f}<-1", "历史底部区间"])
    else:
        return _make_result(name="链上指标", score=65,
                            verdict=f"MVRV-Z正常({mvrv_z:.1f})",
                            reasoning=[f"MVRV-Z={mvrv_z:.1f}"])


def wrap_strategy_engine(data: dict, cache: dict):
    """包装 UnifiedStrategyEngine 策略推荐"""
    try:
        from services.unified_strategy_engine import UnifiedStrategyEngine
        spot = _safe_float(data.get("spot", 0))
        contracts = data.get("contracts", [])
        dvol = _safe_float(data.get("dvol", 0))
        engine = UnifiedStrategyEngine()
        recommendation = engine.execute(spot=spot, contracts=contracts, dvol_score=dvol)
        strategies = recommendation.get("strategies", [])
        strategy_count = len(strategies)

        if strategy_count >= 3:
            return _make_result(name="策略引擎", score=70,
                                verdict=f"策略引擎匹配{strategy_count}个策略",
                                reasoning=[f"现货={spot}", f"匹配策略={strategy_count}个"] +
                                          [f"{s.get('type', '?')}: {s.get('name', '?')}" for s in strategies[:3]])
        elif strategy_count >= 1:
            return _make_result(name="策略引擎", score=55,
                                verdict=f"策略引擎有限匹配{strategy_count}个策略",
                                reasoning=[f"现货={spot}", f"匹配策略={strategy_count}个"])
        else:
            return _make_result(name="策略引擎", score=40,
                                verdict="策略引擎未匹配到策略",
                                reasoning=[f"现货={spot}", "当前市场条件未触发策略"])
    except Exception as e:
        return _make_result(name="策略引擎", score=50,
                            verdict=f"策略引擎不可用: {e}")


# ============================================================
# 面板配置注册表 (16 面板)
# ============================================================

PANEL_CONFIGS: Dict[str, Dict[str, Any]] = {
    # === 测试面板（保留向后兼容） ===
    "test_panel": {
        "name": "测试面板",
        "rules": [
            {"id": "r1", "name": "规则1", "fn": lambda d, c: _make_result(name="规则1", score=75, verdict="好", reasoning=["t1"]), "weight": 0.6},
            {"id": "r2", "name": "规则2", "fn": lambda d, c: _make_result(name="规则2", score=55, verdict="中", reasoning=["t2"]), "weight": 0.4},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "risk_test_panel": {
        "name": "风险测试面板",
        "rules": [
            {"id": "r1", "name": "风险1", "fn": lambda d, c: _make_result(name="风险1", score=25, verdict="高风险", reasoning=["r1"]), "weight": 1.0},
        ],
        "signal_formula": "worst_case",
        "default_action": "",
    },

    # === 指标卡片 ===
    "metric_cards": {
        "name": "市场总览",
        "rules": [
            {"id": "dvol", "name": "DVOL信号", "fn": calc_dvol_signal, "weight": 0.4},
            {"id": "sentiment", "name": "市场情绪", "fn": calc_sentiment, "weight": 0.35},
            {"id": "trend", "name": "趋势强度", "fn": calc_trend_strength, "weight": 0.25},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },

    # === 风险指挥中心 ===
    "risk_command_center": {
        "name": "风险指挥中心",
        "rules": [
            {"id": "risk_fw", "name": "风险框架", "fn": wrap_risk_framework, "weight": 0.5},
            {"id": "unified_risk", "name": "统一风险评估", "fn": wrap_unified_risk, "weight": 0.5},
        ],
        "signal_formula": "worst_case",
        "default_action": "",
    },

    # === 策略中心 ===
    "strategy_center": {
        "name": "策略中心",
        "rules": [
            {"id": "strategy_eng", "name": "策略引擎", "fn": wrap_strategy_engine, "weight": 0.6},
            {"id": "dvol_sig", "name": "DVOL信号", "fn": calc_dvol_signal, "weight": 0.4},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },

    # === Greeks矩阵 ===
    "greeks_matrix": {
        "name": "Greeks矩阵",
        "rules": [
            {"id": "greeks_eng", "name": "Greeks分析", "fn": wrap_greeks_analyzer, "weight": 0.5},
            {"id": "vol_regime", "name": "波动率区间", "fn": calc_vol_regime, "weight": 0.5},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },

    # === AI分析中心 ===
    "ai_analyst_center": {
        "name": "AI分析中心",
        "rules": [
            {"id": "dvol", "name": "DVOL信号", "fn": calc_dvol_signal, "weight": 0.3},
            {"id": "sentiment", "name": "市场情绪", "fn": calc_sentiment, "weight": 0.3},
            {"id": "trend", "name": "趋势强度", "fn": calc_trend_strength, "weight": 0.4},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },

    # === IV期限结构 ===
    "iv_term_structure": {
        "name": "IV期限结构",
        "rules": [
            {"id": "term_premium", "name": "期限溢价", "fn": calc_term_premium, "weight": 0.3},
            {"id": "iv_steepness", "name": "曲线陡峭度", "fn": calc_iv_steepness, "weight": 0.25},
            {"id": "vol_regime", "name": "波动率区间", "fn": calc_vol_regime, "weight": 0.25},
            {"id": "calendar", "name": "日历价差", "fn": calc_calendar_spread, "weight": 0.2},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },

    # === IV Smile ===
    "iv_smile": {
        "name": "IV Smile",
        "rules": [
            {"id": "skew", "name": "偏度信号", "fn": calc_skew_signal, "weight": 0.4},
            {"id": "morphology", "name": "微笑形态", "fn": calc_smile_morphology, "weight": 0.35},
            {"id": "vol_regime", "name": "波动率区间", "fn": calc_vol_regime, "weight": 0.25},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },

    # === DVOL趋势 ===
    "dvol_trend": {
        "name": "DVOL趋势",
        "rules": [
            {"id": "dvol", "name": "DVOL信号", "fn": calc_dvol_signal, "weight": 0.5},
            {"id": "vol_regime", "name": "波动率区间", "fn": calc_vol_regime, "weight": 0.5},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },

    # === PCR图表 ===
    "pcr_chart": {
        "name": "PCR图表",
        "rules": [
            {"id": "pcr", "name": "PCR信号", "fn": calc_pcr_signal, "weight": 0.5},
            {"id": "sentiment", "name": "市场情绪", "fn": calc_sentiment, "weight": 0.5},
        ],
        "signal_formula": "majority",
        "default_action": "",
    },

    # === MaxPain ===
    "max_pain": {
        "name": "MaxPain",
        "rules": [
            {"id": "maxpain", "name": "MaxPain", "fn": wrap_maxpain, "weight": 0.5},
            {"id": "gamma_flip", "name": "GammaFlip", "fn": wrap_gamma_flip, "weight": 0.5},
        ],
        "signal_formula": "worst_case",
        "default_action": "",
    },

    # === 大单追踪 ===
    "large_trades": {
        "name": "大单追踪",
        "rules": [
            {"id": "large_dir", "name": "大单方向", "fn": calc_large_trades_direction, "weight": 0.6},
            {"id": "sentiment", "name": "市场情绪", "fn": calc_sentiment, "weight": 0.4},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },

    # === 补仓沙盒 ===
    "martingale_sandbox": {
        "name": "补仓沙盒",
        "rules": [
            {"id": "martingale", "name": "补仓风险", "fn": wrap_martingale, "weight": 0.5},
            {"id": "vol_regime", "name": "波动率区间", "fn": calc_vol_regime, "weight": 0.5},
        ],
        "signal_formula": "worst_case",
        "default_action": "",
    },

    # === 机会表 ===
    "opportunities_table": {
        "name": "机会列表",
        "rules": [
            {"id": "opp_quality", "name": "机会质量", "fn": calc_opportunity_signal, "weight": 0.5},
            {"id": "dvol", "name": "DVOL信号", "fn": calc_dvol_signal, "weight": 0.5},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },

    # === GEX图表 ===
    "gex_chart": {
        "name": "GEX图表",
        "rules": [
            {"id": "greeks_eng", "name": "Greeks分析", "fn": wrap_greeks_analyzer, "weight": 0.6},
            {"id": "gamma_flip", "name": "GammaFlip", "fn": wrap_gamma_flip, "weight": 0.4},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },

    # === 资金流向 ===
    "money_flow": {
        "name": "资金流向",
        "rules": [
            {"id": "flow", "name": "资金流向", "fn": wrap_money_flow, "weight": 0.6},
            {"id": "sentiment", "name": "市场情绪", "fn": calc_sentiment, "weight": 0.4},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },

    # === 链上指标 ===
    "onchain_metrics": {
        "name": "链上指标",
        "rules": [
            {"id": "onchain", "name": "链上指标", "fn": wrap_onchain, "weight": 0.6},
            {"id": "trend", "name": "趋势强度", "fn": calc_trend_strength, "weight": 0.4},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
}


# ============================================================
# LLM Prompt 模板 (16 面板)
# ============================================================

LLM_PROMPT_TEMPLATES: Dict[str, Dict[str, str]] = {
    "metric_cards": {
        "synthesis": "基于以下数据，分析{currency}当前市场状态:\n- 现货: ${spot}\n- DVOL: {dvol} (z={dvol_z})\n- 恐惧贪婪: {fear_greed}\n- 趋势强度: {trend_strength}\n- 规则评分:\n{rule_scores}\n\n请给出综合市场状态判断和操作建议。",
        "bull_context": "利多因素:\n- DVOL低有利于卖方策略\n- 恐惧情绪可能蕴藏买入机会\n- 趋势向上支撑卖PUT",
        "bear_context": "利空因素:\n- DVOL高增加卖方风险\n- 贪婪情绪预示回调\n- 趋势向下需要更大安全边际",
        "judge_criteria": "从风险收益比角度判定整体市场方向，给出具体的操作策略建议（卖PUT/卖CALL/观望/价差），并说明推荐DTE和OTM%范围。",
    },
    "risk_command_center": {
        "synthesis": "基于以下多因子风险数据，分析{currency}的风险状况:\n- 现货: ${spot}\n- 规则评分:\n{rule_scores}\n- 数据:\n{data_snapshot}\n\n请给出综合风险评估。",
        "bull_context": "低风险因素:\n- 现货远高于支撑位\n- DVOL处于低位\n- 趋势向上",
        "bear_context": "高风险因素:\n- 现货接近或跌破支撑位\n- DVOL飙升\n- 趋势恶化",
        "judge_criteria": "综合判断风险等级，给出仓位建议（正常/减仓/暂停）和具体风控措施。",
    },
    "strategy_center": {
        "synthesis": "基于以下数据，分析{currency}的策略方向:\n- 现货: ${spot}\n- DVOL: {dvol}\n- 规则评分:\n{rule_scores}\n\n请给出策略推荐。",
        "bull_context": "做多策略机会:\n- 低波动率窗口\n- 趋势支撑\n- 风险可控",
        "bear_context": "做空/防御策略:\n- 高波动率环境\n- 趋势恶化\n- 风险升高",
        "judge_criteria": "给出具体策略推荐（卖PUT/卖CALL/铁鹰/日历价差等），说明推荐的DTE、OTM%和执行时机。",
    },
    "greeks_matrix": {
        "synthesis": "基于以下Greeks数据，分析{currency}的Greeks风险敞口:\n- 现货: ${spot}\n- 规则评分:\n{rule_scores}\n- 数据:\n{data_snapshot}\n\n请给出Greeks解读。",
        "bull_context": "Greeks利多信号:\n- 正GEX抑制波动\n- IV适中可控",
        "bear_context": "Greeks利空信号:\n- 负GEX放大波动\n- IV异常高",
        "judge_criteria": "从GEX、Delta、Gamma角度分析市场结构，给出对冲建议。",
    },
    "ai_analyst_center": {
        "synthesis": "基于以下数据，对{currency}进行全面AI分析:\n- 现货: ${spot}\n- DVOL: {dvol} (z={dvol_z})\n- 规则评分:\n{rule_scores}\n\n请给出综合分析。",
        "bull_context": "看多论点:\n- 波动率环境支持卖方\n- 情绪指标显示机会\n- 趋势方向有利",
        "bear_context": "看空论点:\n- 波动率环境不利卖方\n- 情绪指标警示过热\n- 趋势方向不利",
        "judge_criteria": "综合多空论点，给出最终判決和操作建议。关注风险收益比和具体执行参数。",
    },
    "iv_term_structure": {
        "synthesis": "基于以下数据，分析{currency}的IV期限结构:\n- 现货: ${spot}\n- 期限溢价: {term_premium}%\n- DVOL: {dvol} (z={dvol_z})\n- 曲线形态: iv_steepness={iv_steepness}\n- 规则评分:\n{rule_scores}\n\n请从卖方角度给出结构判断。",
        "bull_context": "期限结构利多因素:\n- 陡峭Contango有利于日历价差\n- 远月IV充足\n- 低波环境增强卖方信心",
        "bear_context": "期限结构利空因素:\n- Backwardation预示短期风险\n- 前端IV异常\n- 曲线平坦化",
        "judge_criteria": "从风险收益比角度判定整体结构方向，给出具体操作建议（日历价差/单卖近月/单卖远月）。",
    },
    "iv_smile": {
        "synthesis": "基于以下数据，分析{currency}的IV微笑:\n- 现货: ${spot}\n- 偏度: {skew}\n- 峰度: {kurtosis}\n- 规则评分:\n{rule_scores}\n\n请给出微笑形态解读。",
        "bull_context": "微笑利多因素:\n- 负偏意味着PUT溢价高\n- 卖PUT可收取更多权利金",
        "bear_context": "微笑利空因素:\n- 正偏意味着CALL溢价高\n- 肥尾意味着尾部风险定价高",
        "judge_criteria": "判定当前微笑形态对卖方策略的影响，给出卖PUT还是卖CALL的建议。",
    },
    "dvol_trend": {
        "synthesis": "基于以下数据，分析{currency}的DVOL趋势:\n- DVOL: {dvol} (z={dvol_z})\n- 波动率区间: current regime\n- 规则评分:\n{rule_scores}\n\n请给出波动率操作建议。",
        "bull_context": "低波利多:\n- 均值回归向下\n- 卖方策略窗口\n- 保证金需求低",
        "bear_context": "高波利空:\n- 均值回归向上\n- 卖方策略风险大\n- 保证金需求高",
        "judge_criteria": "判断波动率趋势和均值回归方向，给出仓位调整建议。",
    },
    "pcr_chart": {
        "synthesis": "基于以下数据，分析{currency}的PCR信号:\n- PCR: {pcr}\n- 情绪: {fear_greed}\n- 规则评分:\n{rule_scores}\n\n请给出PCR解读。",
        "bull_context": "PCR利多因素:\n- PCR极端高→市场过度恐慌→反向看多\n- 情绪指标确认",
        "bear_context": "PCR利空因素:\n- PCR极端低→市场过度乐观→警惕顶部\n- 情绪指标确认",
        "judge_criteria": "判定PCR是否处于极端区间，给出反向操作建议。",
    },
    "max_pain": {
        "synthesis": "基于以下数据，分析{currency}的MaxPain:\n- 现货: ${spot}\n- MaxPain: {max_pain}\n- GammaFlip: {gamma_flip}\n- 规则评分:\n{rule_scores}\n\n请给出MaxPain分析。",
        "bull_context": "MaxPain利多因素:\n- 现货远高于MaxPain\n- 上方压力有限\n- GammaFlip安全",
        "bear_context": "MaxPain利空因素:\n- 接近或跌破MaxPain\n- 磁吸效应向下\n- GammaFlip风险",
        "judge_criteria": "判定MaxPain磁吸方向和GammaFlip风险，给出到期前仓位管理建议。",
    },
    "large_trades": {
        "synthesis": "基于以下数据，分析{currency}的大单动向:\n- 大单数据: {large_trades} trades\n- 情绪: {fear_greed}\n- 规则评分:\n{rule_scores}\n\n请给出聪明钱方向判断。",
        "bull_context": "大单利多因素:\n- 主力买入占优\n- 大单方向偏多",
        "bear_context": "大单利空因素:\n- 主力卖出占优\n- 大单方向偏空",
        "judge_criteria": "判断主力资金方向，给出跟随或反向的操作建议。",
    },
    "martingale_sandbox": {
        "synthesis": "基于以下数据，分析{currency}的补仓策略风险:\n- 规则评分:\n{rule_scores}\n- 数据:\n{data_snapshot}\n\n请给出补仓风险评估。",
        "bull_context": "补仓有利因素:\n- 亏损可控\n- 波动率适中\n- 趋势支持补仓",
        "bear_context": "补仓不利因素:\n- 亏损可能扩大\n- 波动率恶化\n- 趋势不利补仓",
        "judge_criteria": "综合评估补仓的风险收益比，给出是否适合补仓、补仓间距的建议。",
    },
    "opportunities_table": {
        "synthesis": "基于以下数据，分析{currency}的期权机会:\n- 规则评分:\n{rule_scores}\n- DVOL: {dvol}\n- 数据:\n{data_snapshot}\n\n请给出机会筛选建议。",
        "bull_context": "机会筛选:\n- 高质量合约充足\n- DTE和OTM%合适\n- APR有吸引力",
        "bear_context": "机会警告:\n- 高质量合约稀缺\n- 风险收益比不理想",
        "judge_criteria": "给出当前环境下最优的合约筛选标准（DTE范围、OTM%范围、最低APR要求）。",
    },
    "gex_chart": {
        "synthesis": "基于以下数据，分析{currency}的GEX结构:\n- 规则评分:\n{rule_scores}\n- 数据:\n{data_snapshot}\n\n请给出GEX结构解读。",
        "bull_context": "GEX利多:\n- 正GEX支撑市场\n- Gamma结构稳定",
        "bear_context": "GEX利空:\n- 负GEX放大波动\n- Gamma结构不稳定",
        "judge_criteria": "判定GEX对市场的影响方向，给出操作建议。",
    },
    "money_flow": {
        "synthesis": "基于以下数据，分析{currency}的资金流向:\n- 规则评分:\n{rule_scores}\n- 数据:\n{data_snapshot}\n\n请给出资金流向解读。",
        "bull_context": "资金流向利多:\n- 主动买入占优\n- 资金持续流入",
        "bear_context": "资金流向利空:\n- 主动卖出占优\n- 资金持续流出",
        "judge_criteria": "判定资金流向对现货方向的影响，给出跟随或防御策略。",
    },
    "onchain_metrics": {
        "synthesis": "基于以下数据，分析{currency}的链上指标:\n- 规则评分:\n{rule_scores}\n- 数据:\n{data_snapshot}\n\n请给出链上指标解读。",
        "bull_context": "链上利多:\n- MVRV-Z低估\n- 长期持有者增持",
        "bear_context": "链上利空:\n- MVRV-Z高估\n- 长期持有者减持",
        "judge_criteria": "综合链上数据判定估值水平，给出长期操作建议。",
    },

    # === 测试面板模板 ===
    "test_panel": {
        "synthesis": "Test synthesis for {currency} at spot {spot}",
        "bull_context": "Test bull",
        "bear_context": "Test bear",
        "judge_criteria": "Test judge criteria",
    },
}


def get_llm_prompt(panel_id: str) -> Dict[str, str]:
    """获取指定面板的 LLM prompt 模板，若未配置则返回默认模板"""
    if panel_id in LLM_PROMPT_TEMPLATES:
        return LLM_PROMPT_TEMPLATES[panel_id]
    return {
        "synthesis": "基于以下数据，分析{currency}的{panel_id}面板:\n- 现货: ${spot}\n- DVOL: {dvol} (z={dvol_z})\n- 规则评分:\n{rule_scores}\n\n请给出综合分析。",
        "bull_context": "利多因素: 请根据数据分析",
        "bear_context": "利空因素: 请根据数据分析",
        "judge_criteria": "从风险收益比角度判定，给出具体操作建议。",
    }
