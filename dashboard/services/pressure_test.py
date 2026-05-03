"""
压力测试系统 - 高级 Greeks 敏感度分析
基于 Black-Scholes 模型计算 Delta, Gamma, Vanna, Volga 等高阶敏感度
"""
import math
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

from services.shared_calculations import norm_cdf as _shared_norm_cdf


class PressureTestEngine:
    """压力测试引擎 - 计算高阶 Greeks 敏感度"""

    @staticmethod
    def _norm_cdf(x: float) -> float:
        """标准正态分布累积分布函数"""
        return _shared_norm_cdf(x)
    
    @staticmethod
    def _norm_pdf(x: float) -> float:
        """标准正态分布概率密度函数"""
        return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)
    
    @classmethod
    def d1(cls, S: float, K: float, T: float, r: float, sigma: float) -> float:
        """Black-Scholes d1 参数"""
        if T <= 0 or sigma <= 0:
            return 0
        return (math.log(S / K) + (r + sigma * sigma / 2) * T) / (sigma * math.sqrt(T))
    
    @classmethod
    def d2(cls, S: float, K: float, T: float, r: float, sigma: float) -> float:
        """Black-Scholes d2 参数"""
        return cls.d1(S, K, T, r, sigma) - sigma * math.sqrt(T)
    
    @classmethod
    def get_greeks(cls, S: float, K: float, T: float, r: float, sigma: float, option_type: str = "C") -> Dict[str, float]:
        """
        计算完整 Greeks 集合
        
        Args:
            S: 现货价格
            K: 行权价
            T: 到期时间（年）
            r: 无风险利率
            sigma: 波动率 (IV)
            option_type: "C" 或 "P"
        
        Returns:
            包含所有 Greeks 的字典
        """
        if T <= 0 or sigma <= 0 or S <= 0:
            return {"delta": 0, "gamma": 0, "vega": 0, "theta": 0, "vanna": 0, "volga": 0}
        
        d1 = cls.d1(S, K, T, r, sigma)
        d2 = cls.d2(S, K, T, r, sigma)
        sign = 1 if option_type == "C" else -1
        
        # 一阶 Greeks
        delta = sign * cls._norm_cdf(sign * d1)
        vega = S * cls._norm_pdf(d1) * math.sqrt(T) / 100  # 除以100便于显示
        
        # 二阶 Greeks
        gamma = cls._norm_pdf(d1) / (S * sigma * math.sqrt(T))
        
        # 时间衰减 Theta
        theta_term = -S * cls._norm_pdf(d1) * sigma / (2 * math.sqrt(T))
        if option_type == "C":
            theta = theta_term - r * K * math.exp(-r * T) * cls._norm_cdf(d2)
        else:
            theta = theta_term + r * K * math.exp(-r * T) * cls._norm_cdf(-d2)
        
        # 三阶 Greeks
        # Vanna = dDelta/dSigma = dVega/dS
        vanna = cls._norm_pdf(d1) * (-d2 / sigma)
        
        # Volga (Vomma) = dVega/dSigma
        volga = vega * math.sqrt(T) * d1 * d2 / sigma / 100
        
        return {
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "vega": round(vega, 4),
            "theta": round(theta, 4),
            "vanna": round(vanna, 6),
            "volga": round(volga, 4),
        }
    
    @classmethod
    def portfolio_greeks(cls, positions: List[Dict], S: float, r: float) -> Dict[str, float]:
        """
        计算投资组合整体 Greeks
        
        Args:
            positions: 持仓列表 [{"K": float, "T": float, "sigma": float, "type": "C"/"P", "qty": int}]
            S: 现货价格
            r: 无风险利率
        
        Returns:
            组合 Greeks
        """
        total = {"delta": 0, "gamma": 0, "vega": 0, "theta": 0, "vanna": 0, "volga": 0}
        
        for pos in positions:
            greeks = cls.get_greeks(S, pos["K"], pos["T"], r, pos["sigma"], pos["type"])
            qty = pos.get("qty", 1)
            for greek in total:
                total[greek] += greeks[greek] * qty
        
        return {k: round(v, 4) for k, v in total.items()}
    
    @classmethod
    def stress_test(cls, S: float, K: float, T: float, r: float, sigma: float, 
                    option_type: str = "C", 
                    price_scenarios: List[float] = None,
                    vol_scenarios: List[float] = None) -> Dict:
        """
        压力测试 - 多场景敏感度分析
        
        Args:
            S: 当前现货价格
            K: 行权价
            T: 到期时间（年）
            r: 无风险利率
            sigma: 当前波动率
            option_type: "C" 或 "P"
            price_scenarios: 价格压力场景列表，如 [0.85, 0.90, 0.95, 1.05, 1.10, 1.15]
            vol_scenarios: 波动率压力场景列表，如 [0.5, 0.7, 1.0, 1.3, 1.5]
        
        Returns:
            压力测试结果
        """
        if price_scenarios is None:
            price_scenarios = [0.80, 0.85, 0.90, 0.95, 1.05, 1.10, 1.15, 1.20]
        if vol_scenarios is None:
            vol_scenarios = [0.5, 0.7, 1.0, 1.3, 1.5]
        
        base_greeks = cls.get_greeks(S, K, T, r, sigma, option_type)
        
        # 价格敏感度矩阵
        price_sensitivity = []
        for price_mult in price_scenarios:
            S_new = S * price_mult
            greeks = cls.get_greeks(S_new, K, T, r, sigma, option_type)
            price_sensitivity.append({
                "price": round(S_new, 2),
                "price_change": f"{(price_mult - 1) * 100:+.1f}%",
                "delta": greeks["delta"],
                "gamma": greeks["gamma"],
                "vanna": greeks["vanna"],
            })
        
        # 波动率敏感度矩阵
        vol_sensitivity = []
        for vol_mult in vol_scenarios:
            sigma_new = sigma * vol_mult
            greeks = cls.get_greeks(S, K, T, r, sigma_new, option_type)
            vol_sensitivity.append({
                "volatility": round(sigma_new * 100, 1),
                "vol_change": f"{(vol_mult - 1) * 100:+.0f}%",
                "vega": greeks["vega"],
                "volga": greeks["volga"],
                "vanna": greeks["vanna"],
            })
        
        # 联合压力场景 (价格 + 波动率同时变动)
        joint_scenarios = []
        stress_cases = [
            {"name": "闪崩 -50% + 波动率飙升 200%", "price_mult": 0.50, "vol_mult": 2.0},
            {"name": "暴跌 -30% + 波动率飙升 150%", "price_mult": 0.70, "vol_mult": 1.5},
            {"name": "回调 -15% + 波动率上升 50%", "price_mult": 0.85, "vol_mult": 1.5},
            {"name": "温和回调 -5% + 波动率上升 30%", "price_mult": 0.95, "vol_mult": 1.3},
            {"name": "横盘 + 波动率不变", "price_mult": 1.0, "vol_mult": 1.0},
            {"name": "上涨 +5% + 波动率下降 20%", "price_mult": 1.05, "vol_mult": 0.8},
            {"name": "大涨 +20% + 波动率下降 30%", "price_mult": 1.20, "vol_mult": 0.7},
        ]
        
        for case in stress_cases:
            S_new = S * case["price_mult"]
            sigma_new = sigma * case["vol_mult"]
            greeks = cls.get_greeks(S_new, K, T, r, sigma_new, option_type)
            joint_scenarios.append({
                "scenario": case["name"],
                "price": round(S_new, 2),
                "volatility": round(sigma_new * 100, 1),
                **greeks
            })
        
        return {
            "base_greeks": base_greeks,
            "price_sensitivity": price_sensitivity,
            "vol_sensitivity": vol_sensitivity,
            "joint_stress_tests": joint_scenarios,
            "risk_assessment": cls._assess_risk(base_greeks, joint_scenarios)
        }
    
    @classmethod
    def _assess_risk(cls, base_greeks: Dict, stress_scenarios: List[Dict]) -> Dict:
        """评估风险等级"""
        # 基于 Vanna 和 Volga 评估风险
        vanna_risk = abs(base_greeks.get("vanna", 0)) > 0.1
        volga_risk = abs(base_greeks.get("volga", 0)) > 0.05
        gamma_risk = abs(base_greeks.get("gamma", 0)) > 0.01
        
        # 极端场景下的最大损失
        max_delta_loss = max(abs(s.get("delta", 0)) for s in stress_scenarios) if stress_scenarios else 0
        
        if vanna_risk and volga_risk:
            level = "HIGH"
            desc = "⚠️ 高风险：Vanna 和 Volga 均较大，价格-波动率双重敏感度强，需严格对冲"
        elif gamma_risk:
            level = "MEDIUM"
            desc = "⚡ 中风险：Gamma 较大，价格变动会加速 Delta 变化，注意动态对冲"
        else:
            level = "LOW"
            desc = "✅ 低风险：各阶 Greeks 均在可控范围"
        
        return {
            "level": level,
            "description": desc,
            "vanna_risk": vanna_risk,
            "volga_risk": volga_risk,
            "gamma_risk": gamma_risk,
            "max_delta_exposure": round(max_delta_loss, 4),
        }
