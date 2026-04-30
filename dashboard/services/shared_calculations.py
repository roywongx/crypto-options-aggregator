"""
共享计算模块 v1.0
统一 Payoff Calculator 和 Grid Engine 的核心计算逻辑

包含:
1. Black-Scholes 期权定价公式
2. 正态分布 CDF 近似
3. 胜率计算框架
4. 流动性评分框架
5. 通用评分工具
"""
import math
from typing import Dict, Optional, List


def norm_cdf(x: float) -> float:
    """标准正态分布累积分布函数近似计算"""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def norm_pdf(x: float) -> float:
    """标准正态分布概率密度函数"""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def black_scholes_price(option_type: str, strike: float, spot: float,
                       dte: int, iv: float, risk_free_rate: float = 0.05) -> Dict[str, float]:
    """
    Black-Scholes 期权定价公式
    
    参数:
    - option_type: "P" 或 "C"
    - strike: 行权价
    - spot: 当前价格
    - dte: 到期天数
    - iv: 隐含波动率（百分比，如 50 表示 50%）
    - risk_free_rate: 无风险利率（默认 5%）
    
    返回:
    - premium: 理论权利金
    - delta: Delta 值
    - gamma: Gamma 值
    - theta: Theta 值
    - vega: Vega 值
    - intrinsic_value: 内在价值
    - time_value: 时间价值
    """
    if strike <= 0 or spot <= 0 or iv <= 0 or dte <= 0:
        return {
            "premium": 0, "delta": 0, "gamma": 0, 
            "theta": 0, "vega": 0, "intrinsic_value": 0, "time_value": 0
        }
    
    T = dte / 365.0
    sigma = iv / 100.0
    
    d1 = (math.log(spot / strike) + (risk_free_rate + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    
    if option_type.upper() in ("P", "PUT"):
        premium = strike * math.exp(-risk_free_rate * T) * norm_cdf(-d2) - spot * norm_cdf(-d1)
        delta = norm_cdf(d1) - 1
        intrinsic = max(0, strike - spot)
    else:
        premium = spot * norm_cdf(d1) - strike * math.exp(-risk_free_rate * T) * norm_cdf(d2)
        delta = norm_cdf(d1)
        intrinsic = max(0, spot - strike)
    
    premium = max(0, premium)
    time_value = max(0, premium - intrinsic)
    
    # Gamma (P 和 C 相同)
    gamma = norm_pdf(d1) / (spot * sigma * math.sqrt(T))
    
    # Vega
    vega = spot * norm_pdf(d1) * math.sqrt(T) / 100  # 每 1% IV 变化
    
    # Theta (每天)
    theta_term = -(spot * norm_pdf(d1) * sigma) / (2 * math.sqrt(T)) / 365
    if option_type.upper() in ("P", "PUT"):
        # Put Theta: theta_term + r*K*exp(-rT)*N(-d2)/365
        theta = theta_term + risk_free_rate * strike * math.exp(-risk_free_rate * T) * norm_cdf(-d2) / 365
    else:
        theta = theta_term - risk_free_rate * strike * math.exp(-risk_free_rate * T) * norm_cdf(d2) / 365
    
    return {
        "premium": round(premium, 2),
        "delta": round(delta, 3),
        "gamma": round(gamma, 6),
        "theta": round(theta, 2),
        "vega": round(vega, 2),
        "intrinsic_value": round(intrinsic, 2),
        "time_value": round(time_value, 2)
    }


def calc_win_rate(option_type: str, direction: str, strike: float,
                  premium: float, spot: float, iv: float, dte: int) -> float:
    """
    计算期权策略胜率
    
    参数:
    - option_type: "P" 或 "C"
    - direction: "sell" 或 "buy"
    - strike: 行权价
    - premium: 权利金
    - spot: 当前价格
    - iv: 隐含波动率（百分比）
    - dte: 到期天数
    
    返回:
    - 胜率 (0-1 之间)
    """
    if spot <= 0 or dte <= 0 or iv <= 0:
        return 0.5
    
    # 盈亏平衡点
    breakeven = strike - premium if option_type.upper() in ("P", "PUT") else strike + premium
    
    # 波动率因子
    volatility_factor = spot * (iv / 100) * math.sqrt(dte / 365)
    if volatility_factor <= 0:
        return 0.5
    
    z = (breakeven - spot) / volatility_factor
    
    # 根据方向和买卖方向计算概率
    is_put = option_type.upper() in ("P", "PUT")
    is_sell = direction.lower() == "sell"
    
    if is_sell:
        if is_put:
            # Sell Put: 价格保持在 breakeven 之上的概率
            prob = 1 - norm_cdf(z)
        else:
            # Sell Call: 价格保持在 breakeven 之下的概率
            prob = norm_cdf(z)
    else:
        if is_put:
            # Buy Put: 价格跌破 breakeven 的概率
            prob = norm_cdf(z)
        else:
            # Buy Call: 价格上涨超过 breakeven 的概率
            prob = 1 - norm_cdf(z)
    
    return max(0.0, min(1.0, prob))


def calc_liquidity_score(oi: int = 0, volume: int = 0,
                         iv: float = 50, dte: int = 30,
                         method: str = "grid") -> float:
    """
    统一流动性评分计算
    
    参数:
    - oi: 未平仓合约数
    - volume: 成交量
    - iv: 隐含波动率
    - dte: 到期天数
    - method: "grid" 或 "payoff" (不同方法的权重)
    
    返回:
    - 流动性评分 (0-100)
    """
    if method == "grid":
        # Grid Engine 方法：基于 OI 和 Volume
        liquidity_score = min((oi / 500.0 + volume / 100.0), 1.0) / 2.0
        return round(liquidity_score * 100, 1)
    else:
        # Payoff Calculator 方法：基于 IV 和 DTE
        score = min(100, max(0, 50 + (iv - 30) * 0.5 + (30 - dte) * 0.5))
        return round(score, 1)


def apr_to_annualized_roi(apr: float, capital_at_risk: float, 
                          dte: int) -> Dict[str, float]:
    """
    APR 转换为年化 ROI
    
    参数:
    - apr: 年化百分比收益率
    - capital_at_risk: 风险资金
    - dte: 到期天数
    
    返回:
    - roi: 单次收益百分比
    - annualized_roi: 年化收益百分比
    - income: 预期收入
    """
    if capital_at_risk <= 0 or dte <= 0:
        return {"roi": 0, "annualized_roi": 0, "income": 0}
    
    income = capital_at_risk * (apr / 100) * (dte / 365)
    roi = (income / capital_at_risk) * 100
    annualized_roi = (roi * 365 / dte) if dte > 0 else 0
    
    return {
        "roi": round(roi, 2),
        "annualized_roi": round(annualized_roi, 2),
        "income": round(income, 2)
    }


def calc_theta_decay(premium: float, dte: int, target_dte: int = None) -> Dict[str, float]:
    """
    计算 Theta 衰减（时间价值损耗）
    
    参数:
    - premium: 当前权利金
    - dte: 当前到期天数
    - target_dte: 目标天数（默认 7 天）
    
    返回:
    - remaining_value: 剩余价值
    - decay_amount: 损耗金额
    - decay_pct: 损耗百分比
    - daily_decay: 每日损耗
    """
    if dte <= 0 or premium <= 0:
        return {"remaining_value": 0, "decay_amount": 0, "decay_pct": 0, "daily_decay": 0}
    
    if target_dte is None:
        target_dte = 7
    
    # Theta 衰减近似：与 sqrt(剩余时间) 成正比
    current_sqrt = math.sqrt(max(1, dte))
    target_sqrt = math.sqrt(max(1, target_dte))
    
    remaining_ratio = target_sqrt / current_sqrt
    remaining_value = premium * remaining_ratio
    decay_amount = premium - remaining_value
    decay_pct = (decay_amount / premium) * 100
    days_passed = dte - target_dte
    daily_decay = decay_amount / days_passed if days_passed > 0 else 0
    
    return {
        "remaining_value": round(remaining_value, 2),
        "decay_amount": round(decay_amount, 2),
        "decay_pct": round(decay_pct, 1),
        "daily_decay": round(daily_decay, 2)
    }


def score_to_rating(score: float, scale: str = "0-100") -> Dict[str, str]:
    """
    将分数转换为评级
    
    参数:
    - score: 原始分数
    - scale: "0-100" 或 "0-1"
    
    返回:
    - rating: 评级名称
    - level: 颜色级别
    - description: 描述
    """
    if scale == "0-1":
        score = score * 100
    
    if score >= 80:
        return {"rating": "强烈推荐", "level": "green", "description": "极佳机会"}
    elif score >= 70:
        return {"rating": "推荐", "level": "emerald", "description": "良好机会"}
    elif score >= 60:
        return {"rating": "中性", "level": "yellow", "description": "可考虑"}
    elif score >= 50:
        return {"rating": "谨慎", "level": "orange", "description": "需要警惕"}
    elif score >= 40:
        return {"rating": "一般", "level": "orange", "description": "风险较高"}
    else:
        return {"rating": "不推荐", "level": "red", "description": "高风险"}


def calc_grid_score(apr: float, distance_pct: float, oi: int,
                    volume: int, dte: int) -> float:
    """
    网格策略评分（统一框架）
    
    参数:
    - apr: 年化收益率
    - distance_pct: 距离百分比
    - oi: 未平仓合约
    - volume: 成交量
    - dte: 到期天数
    
    返回:
    - score: 0-1 之间的分数
    """
    apr_score = min(apr / 100.0, 1.0)
    safety_score = 1.0 - min(abs(distance_pct) / 15.0, 1.0)
    liquidity_score = min((oi / 500.0 + volume / 100.0), 1.0) / 2.0

    if 14 <= dte <= 21:
        theta_score = 1.0
    elif dte < 14:
        theta_score = 0.5 + (dte / 14.0) * 0.5
    else:
        theta_score = max(0.3, 1.0 - (dte - 21) / 30.0)

    score = apr_score * 0.35 + safety_score * 0.30 + liquidity_score * 0.20 + theta_score * 0.15
    return score


def score_to_recommendation_level(score: float) -> str:
    """
    将分数转换为推荐等级
    
    等级: BEST / GOOD / OK / CAUTION / SKIP
    """
    if score >= 0.75:
        return "BEST"
    elif score >= 0.60:
        return "GOOD"
    elif score >= 0.45:
        return "OK"
    elif score >= 0.30:
        return "CAUTION"
    return "SKIP"
