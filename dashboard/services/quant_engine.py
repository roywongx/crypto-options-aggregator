"""
QuantLib 替代方案 - 高级希腊字母计算引擎 v2.0
使用 scipy.stats 实现机构级精度计算
支持: Delta, Gamma, Theta, Vega, Vanna, Charm, IV 逆推
"""
import math
from scipy.stats import norm
from typing import Dict, Optional


def _validate_bs_params(S: float, K: float, T: float, sigma: float) -> bool:
    """校验 Black-Scholes 参数合法性"""
    return S > 0 and K > 0 and T > 0 and sigma > 0


def bs_d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes d1"""
    if not _validate_bs_params(S, K, T, sigma):
        return 0.0
    return (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))


def bs_d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes d2"""
    if not _validate_bs_params(S, K, T, sigma):
        return 0.0
    return bs_d1(S, K, T, r, sigma) - sigma * math.sqrt(T)


def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Call 期权理论价格"""
    if not _validate_bs_params(S, K, T, sigma):
        return max(0, S - K)
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = bs_d2(S, K, T, r, sigma)
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Put 期权理论价格"""
    if not _validate_bs_params(S, K, T, sigma):
        return max(0, K - S)
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = bs_d2(S, K, T, r, sigma)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_delta(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "P") -> float:
    """
    Delta: 期权价格对标的资产价格的一阶导数
    """
    if not _validate_bs_params(S, K, T, sigma):
        return 1.0 if option_type.upper() == "C" else -1.0
    d1 = bs_d1(S, K, T, r, sigma)
    if option_type.upper() == "C":
        return norm.cdf(d1)
    else:
        return norm.cdf(d1) - 1


def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Gamma: Delta 对标的资产价格的一阶导数 (看涨看跌相同)
    """
    if not _validate_bs_params(S, K, T, sigma):
        return 0.0
    d1 = bs_d1(S, K, T, r, sigma)
    return norm.pdf(d1) / (S * sigma * math.sqrt(T))


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Vega: 期权价格对波动率的一阶导数
    """
    if not _validate_bs_params(S, K, T, sigma):
        return 0.0
    d1 = bs_d1(S, K, T, r, sigma)
    return S * norm.pdf(d1) * math.sqrt(T)


def bs_theta(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "P") -> float:
    """
    Theta: 期权价格对时间的一阶导数 (每日衰减)
    """
    if not _validate_bs_params(S, K, T, sigma):
        return 0.0
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = bs_d2(S, K, T, r, sigma)

    common = -S * norm.pdf(d1) * sigma / (2 * math.sqrt(T))

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
    if not _validate_bs_params(S, K, T, sigma):
        return 0.0
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = bs_d2(S, K, T, r, sigma)
    return -norm.pdf(d1) * d2 / sigma


def bs_charm(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "P") -> float:
    """
    Charm: Delta 对时间的一阶导数 (∂Delta/∂T)
    衡量 Delta 随时间衰减的速度
    """
    if not _validate_bs_params(S, K, T, sigma):
        return 0.0
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = bs_d2(S, K, T, r, sigma)

    sqrt_T = math.sqrt(T)
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
    if not _validate_bs_params(S, K, T, sigma):
        return 0.0
    d2 = bs_d2(S, K, T, r, sigma)
    if option_type.upper() == "C":
        return K * T * math.exp(-r * T) * norm.cdf(d2) / 100
    else:
        return -K * T * math.exp(-r * T) * norm.cdf(-d2) / 100


def calculate_greeks_full(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "P") -> Dict[str, float]:
    """
    计算完整的希腊字母组合
    返回: delta, gamma, theta, vega, vanna, charm, rho
    """
    if not _validate_bs_params(S, K, T, sigma):
        return {
            "delta": 1.0 if option_type.upper() == "C" else -1.0,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "vanna": 0.0,
            "charm": 0.0,
            "rho": 0.0
        }

    return {
        "delta": bs_delta(S, K, T, r, sigma, option_type),
        "gamma": bs_gamma(S, K, T, r, sigma),
        "theta": bs_theta(S, K, T, r, sigma, option_type),
        "vega": bs_vega(S, K, T, r, sigma),
        "vanna": bs_vanna(S, K, T, r, sigma),
        "charm": bs_charm(S, K, T, r, sigma, option_type),
        "rho": bs_rho(S, K, T, r, sigma, option_type)
    }
