"""
马丁格尔沙盘推演引擎 v2.0
基于真实市场数据的崩盘情景模拟和恢复策略搜索
"""
import math
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class MartingaleSandboxEngine:
    """马丁格尔沙盘推演引擎"""
    
    @classmethod
    def calculate_loss(cls, strike: float, crash_price: float, qty: float, 
                       avg_premium: float, dte: int, option_type: str) -> Dict:
        """计算崩盘情景下的持仓损失
        
        Returns:
            损失详情（本金损失 + Vega冲击 + 时间价值衰减）
        """
        if option_type.upper() == "P":
            intrinsic = max(0, strike - crash_price)
        else:
            intrinsic = max(0, crash_price - strike)
        
        principal_loss = intrinsic * qty
        time_value_remaining = max(0, avg_premium - intrinsic)
        
        # Vega冲击：崩盘时IV飙升，卖方头寸市值膨胀
        # 近似公式：Vega冲击 ≈ 本金损失 × (IV变化率 × sqrt(DTE/365))
        # 典型崩盘 IV 从 50% 飙升到 120%+
        iv_change = 0.70  # 70% IV 上升（保守估计）
        vega_impact = principal_loss * iv_change * math.sqrt(dte / 365) * 0.1
        
        total_loss = principal_loss + vega_impact
        loss_pct = (total_loss / (avg_premium * qty)) * 100 if avg_premium > 0 else 0
        
        return {
            "intrinsic_loss": round(principal_loss, 0),
            "time_value_remaining": round(time_value_remaining, 0),
            "vega_impact": round(vega_impact, 0),
            "total_loss": round(total_loss, 0),
            "loss_pct": round(loss_pct, 1),
            "drop_pct": round((crash_price - strike) / strike * 100, 1) if strike > 0 else 0,
        }
    
    @classmethod
    def search_recovery_candidates(cls, contracts: List[Dict], crash_price: float, 
                                    spot: float, margin_ratio: float,
                                    min_dte: int = 14, max_dte: int = 180,
                                    min_apr: float = 5.0, max_contracts: int = 20,
                                    option_type: str = "P") -> List[Dict]:
        """搜索恢复策略候选合约
        
        使用真实扫描数据（Binance/Deribit），计算每个候选合约的恢复能力
        """
        candidates = []
        
        for c in contracts:
            # 过滤
            if c.get("option_type", "").upper() != option_type.upper():
                continue
            
            strike = c.get("strike", 0)
            dte = c.get("dte", 0)
            premium = c.get("premium_usd", c.get("premium", 0))
            oi = c.get("open_interest", c.get("oi", 0))
            
            if dte < min_dte or dte > max_dte:
                continue
            if premium <= 0:
                continue
            if oi < 10:
                continue
            
            # 恢复合约必须在崩盘价格以下足够安全
            if option_type.upper() == "P" and strike >= crash_price * 1.15:
                continue
            
            # 计算保证金和收益率
            ncv = strike * margin_ratio
            if ncv <= 0:
                continue
            
            # APR 计算
            apr = (premium / ncv) * (365 / dte) * 100
            if apr < min_apr:
                continue
            
            # 单次收益率
            yield_pct = apr / 100 * (dte / 365)
            if yield_pct <= 0.001:
                continue
            
            candidates.append({
                "symbol": c.get("symbol", ""),
                "strike": strike,
                "dte": dte,
                "premium_usd": round(premium, 2),
                "apr": round(apr, 1),
                "yield_pct": round(yield_pct * 100, 2),
                "ncv": round(ncv, 2),
                "oi": oi,
                "delta": abs(c.get("delta", 0)),
                "distance_pct": round((strike - crash_price) / crash_price * 100, 1),
            })
        
        # 按 APR 排序
        candidates.sort(key=lambda x: x["apr"], reverse=True)
        return candidates[:50]
    
    @classmethod
    def calculate_recovery_plan(cls, candidate: Dict, total_loss: float, 
                                 reserve_capital: float, old_margin: float,
                                 max_contracts: int = 20) -> Dict:
        """计算单个候选合约的恢复方案
        
        计算需要多少张合约才能覆盖损失，并检查资金是否足够
        """
        ncv = candidate["ncv"]
        yield_pct = candidate["yield_pct"] / 100
        
        # 计算需要的合约数量
        income_per_contract = ncv * yield_pct
        if income_per_contract <= 0:
            return None
        
        needed_contracts = math.ceil(total_loss / income_per_contract)
        needed_contracts = max(1, min(needed_contracts, max_contracts))
        
        # 计算总需求
        total_margin = ncv * needed_contracts
        total_income = income_per_contract * needed_contracts
        net_recovery = total_income - total_loss
        total_capital_needed = old_margin + total_margin
        remaining_reserve = reserve_capital - total_margin
        
        # 判定可行性
        if remaining_reserve >= 0 and net_recovery >= 0:
            status = "success"
        elif remaining_reserve >= 0:
            status = "partial"
        else:
            status = "danger"
        
        return {
            "symbol": candidate["symbol"],
            "strike": candidate["strike"],
            "dte": candidate["dte"],
            "apr": candidate["apr"],
            "premium_per_contract": candidate["premium_usd"],
            "contracts": needed_contracts,
            "margin_required": round(total_margin, 0),
            "total_income": round(total_income, 0),
            "net_recovery": round(net_recovery, 0),
            "capital_required": round(total_capital_needed, 0),
            "remaining_reserve": round(remaining_reserve, 0),
            "status": status,
            "distance_from_crash": candidate["distance_pct"],
            "delta": candidate["delta"],
            "oi": candidate["oi"],
        }
    
    @classmethod
    def generate_safety_assessment(cls, loss_info: Dict, reserve_capital: float,
                                    best_plan: Optional[Dict]) -> Dict:
        """生成安全评估报告"""
        total_capital_available = reserve_capital
        
        if best_plan:
            if best_plan["status"] == "success":
                safety_level = "SAFE"
                safety_color = "green"
                safety_message = f"✅ 安全：后备资金 ${reserve_capital:,.0f} 可完全覆盖恢复成本，净盈利 ${best_plan['net_recovery']:+,.0f}"
            elif best_plan["status"] == "partial":
                safety_level = "WARNING"
                safety_color = "yellow"
                safety_message = f"⚠️ 警戒：可恢复但净收益为负，亏损 ${abs(best_plan['net_recovery']):,.0f}，需追加保证金"
            else:
                safety_level = "DANGER"
                safety_color = "red"
                safety_message = f"🔴 危险：后备资金不足以覆盖恢复成本，资金缺口 ${abs(best_plan['remaining_reserve']):,.0f}"
        else:
            safety_level = "CRITICAL"
            safety_color = "red"
            safety_message = "🔴 极度危险：无可用恢复合约，资金链可能断裂"
        
        return {
            "level": safety_level,
            "color": safety_color,
            "message": safety_message,
            "reserve_sufficiency": round(reserve_capital / loss_info["total_loss"] * 100, 1) if loss_info["total_loss"] > 0 else 0,
        }
