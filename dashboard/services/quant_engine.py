"""
QuantLib 替代方案 - 高级希腊字母计算引擎 v2.0
使用 scipy.stats 实现机构级精度计算
支持: Delta, Gamma, Theta, Vega, Vanna, Charm, IV 逆推
"""
import math
from scipy.stats import norm
from typing import Dict, Optional


def bs_d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes d1"""
    if T <= 0 or sigma <= 0:
        return 0.0
    return (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))


def bs_d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes d2"""
    return bs_d1(S, K, T, r, sigma) - sigma * math.sqrt(T)


def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Call 期权理论价格"""
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = bs_d2(S, K, T, r, sigma)
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Put 期权理论价格"""
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = bs_d2(S, K, T, r, sigma)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_delta(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "P") -> float:
    """
    Delta: 期权价格对标的资产价格的一阶导数
    """
    d1 = bs_d1(S, K, T, r, sigma)
    if option_type.upper() == "C":
        return norm.cdf(d1)
    else:
        return norm.cdf(d1) - 1


def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Gamma: Delta 对标的资产价格的一阶导数 (看涨看跌相同)
    """
    d1 = bs_d1(S, K, T, r, sigma)
    return norm.pdf(d1) / (S * sigma * math.sqrt(T)) if T > 0 and S > 0 else 0.0


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Vega: 期权价格对波动率的一阶导数
    """
    d1 = bs_d1(S, K, T, r, sigma)
    return S * norm.pdf(d1) * math.sqrt(T) if T > 0 else 0.0


def bs_theta(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "P") -> float:
    """
    Theta: 期权价格对时间的一阶导数 (每日衰减)
    """
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = bs_d2(S, K, T, r, sigma)
    
    common = -S * norm.pdf(d1) * sigma / (2 * math.sqrt(T)) if T > 0 else 0
    
    if option_type.upper() == "C":
        theta = common - r * K * math.exp(-r * T) * norm.cdf(d2)
    else:
        theta = common + r * K * math.exp(-r * T) * norm.cdf(-d2)
    
    return theta / 365.0  # 转为每日衰减


def bs_vanna(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Vanna: Delta 对波动率的一阶导数 (或 Vega 对价格的一阶导数)
    二阶混合偏导: ∂²V / (∂S∂σ)
    """
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = bs_d2(S, K, T, r, sigma)
    return -norm.pdf(d1) * d2 / sigma if sigma > 0 else 0.0


def bs_charm(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "P") -> float:
    """
    Charm: Delta 对时间的一阶导数 (∂Delta/∂T)
    衡量 Delta 随时间衰减的速度
    """
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = bs_d2(S, K, T, r, sigma)
    
    sqrt_T = math.sqrt(T) if T > 0 else 0.001
    pdf_d1 = norm.pdf(d1)
    
    if option_type.upper() == "C":
        charm = -pdf_d1 * (2 * r * T - d2 * sigma * sqrt_T) / (2 * T * sigma * sqrt_T)
    else:
        charm = pdf_d1 * (2 * r * T - d2 * sigma * sqrt_T) / (2 * T * sigma * sqrt_T)
    
    return charm / 365.0  # 每日变化


def bs_rho(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "P") -> float:
    """
    Rho: 期权价格对无风险利率的一阶导数
    """
    d2 = bs_d2(S, K, T, r, sigma)
    if option_type.upper() == "C":
        return K * T * math.exp(-r * T) * norm.cdf(d2) / 100
    else:
        return -K * T * math.exp(-r * T) * norm.cdf(-d2) / 100


def implied_volatility(market_price: float, S: float, K: float, T: float, r: float, option_type: str = "P") -> Optional[float]:
    """
    隐含波动率逆推 (Newton-Raphson 方法)
    """
    if market_price <= 0 or T <= 0:
        return None
    
    sigma = 0.5  # 初始猜测
    for _ in range(100):
        if option_type.upper() == "C":
            price = bs_call_price(S, K, T, r, sigma)
        else:
            price = bs_put_price(S, K, T, r, sigma)
        
        vega = bs_vega(S, K, T, r, sigma)
        if vega < 1e-10:
            break
        
        diff = price - market_price
        if abs(diff) < 1e-6:
            return sigma
        
        sigma = sigma - diff / vega
        if sigma <= 0 or sigma > 5.0:
            break
    
    return sigma if 0 < sigma < 5.0 else None


def calculate_greeks_full(
    S: float, K: float, T: float, r: float, sigma: float, option_type: str = "P"
) -> Dict[str, float]:
    """
    一次性计算所有希腊字母
    """
    return {
        "delta": bs_delta(S, K, T, r, sigma, option_type),
        "gamma": bs_gamma(S, K, T, r, sigma),
        "theta": bs_theta(S, K, T, r, sigma, option_type),
        "vega": bs_vega(S, K, T, r, sigma),
        "vanna": bs_vanna(S, K, T, r, sigma),
        "charm": bs_charm(S, K, T, r, sigma, option_type),
        "rho": bs_rho(S, K, T, r, sigma, option_type),
    }


def calculate_greeks_dollar(
    S: float, K: float, T: float, r: float, sigma: float, qty: float, option_type: str = "P"
) -> Dict[str, float]:
    """
    计算美元价值希腊字母 (考虑持仓数量)
    """
    greeks = calculate_greeks_full(S, K, T, r, sigma, option_type)
    
    return {
        "delta_dollar": greeks["delta"] * qty * S,
        "gamma_dollar": greeks["gamma"] * qty * S**2,
        "theta_dollar": greeks["theta"] * qty / 365,
        "vega_dollar": greeks["vega"] * qty / 100,
        "vanna_dollar": greeks["vanna"] * qty * S / 100,
    }
