"""
智能抄底建议生成器
提供具体、可执行的建议
"""
from typing import Dict, Any, List
from services.risk_framework import RiskFramework


class SmartBottomFishingAdvisor:
    def __init__(self):
        self.risk_framework = RiskFramework()
    
    def generate_advice(self, spot: float, currency: str = "BTC", 
                       user_profile: Dict = None) -> Dict[str, Any]:
        """
        生成具体的抄底建议
        
        Args:
            spot: 当前价格
            currency: 货币
            user_profile: 用户画像 {
                "risk_tolerance": "low/medium/high",
                "portfolio_size": 100000,
                "existing_positions": [],
                "time_horizon": "short/medium/long"
            }
        """
        if user_profile is None:
            user_profile = {
                "risk_tolerance": "medium",
                "portfolio_size": 100000,
                "existing_positions": [],
                "time_horizon": "medium"
            }
        
        status = self.risk_framework.get_status(spot)
        
        # 根据风险状态和用户画像生成建议
        if status == "NORMAL":
            return self._generate_normal_advice(spot, user_profile, currency)
        elif status == "NEAR_FLOOR":
            return self._generate_near_floor_advice(spot, user_profile, currency)
        elif status == "ADVERSE":
            return self._generate_adverse_advice(spot, user_profile, currency)
        else:  # PANIC
            return self._generate_panic_advice(spot, user_profile, currency)
    
    def _generate_normal_advice(self, spot: float, profile: Dict, currency: str) -> Dict:
        """正常市场建议"""
        risk_tolerance = profile["risk_tolerance"]
        portfolio_size = profile["portfolio_size"]
        
        # 根据风险承受能力调整参数
        if risk_tolerance == "low":
            delta_range = "0.10-0.20"
            dte_range = "30-45天"
            position_size = portfolio_size * 0.05  # 5% of portfolio
            strike_pct = 0.90  # 90% of spot
        elif risk_tolerance == "medium":
            delta_range = "0.15-0.25"
            dte_range = "21-35天"
            position_size = portfolio_size * 0.10  # 10% of portfolio
            strike_pct = 0.92  # 92% of spot
        else:  # high
            delta_range = "0.20-0.30"
            dte_range = "14-28天"
            position_size = portfolio_size * 0.15  # 15% of portfolio
            strike_pct = 0.95  # 95% of spot
        
        strike_price = int(spot * strike_pct)
        max_contracts = int(position_size / (strike_price * 0.1))
        
        return {
            "status": "NORMAL",
            "advice": [
                f"当前价格 ${spot:,.0f} 处于正常区间",
                "市场健康，适合稳定获取权利金",
                "建议保持低杠杆，避免过度暴露"
            ],
            "recommended_actions": [
                {
                    "action": "卖出 OTM Put 期权",
                    "parameters": {
                        "delta_range": delta_range,
                        "dte_range": dte_range,
                        "strike_range": f"${strike_price:,}-${int(spot * 0.95):,}",
                        "position_size": f"${position_size:,.0f}",
                        "max_contracts": max(max_contracts, 1)
                    },
                    "expected_apr": "150-250%",
                    "max_loss": f"${position_size * 0.5:,.0f}",
                    "reasoning": "低Delta Put提供高概率盈利，同时获取时间价值"
                }
            ],
            "risk_management": {
                "stop_loss": f"价格跌破 ${int(spot * 0.85):,} 时止损",
                "roll_strategy": "如果价格接近行权价，提前滚仓",
                "max_position": f"最多持有 {max(max_contracts, 1)} 张合约"
            }
        }
    
    def _generate_near_floor_advice(self, spot: float, profile: Dict, currency: str) -> Dict:
        """接近支撑位建议 - 更保守"""
        risk_tolerance = profile["risk_tolerance"]
        portfolio_size = profile["portfolio_size"]
        
        # 更保守的参数
        if risk_tolerance == "low":
            delta_range = "0.05-0.15"
            dte_range = "45-60天"
            position_size = portfolio_size * 0.03
            strike_pct = 0.85
        elif risk_tolerance == "medium":
            delta_range = "0.10-0.20"
            dte_range = "30-45天"
            position_size = portfolio_size * 0.05
            strike_pct = 0.88
        else:
            delta_range = "0.15-0.25"
            dte_range = "21-35天"
            position_size = portfolio_size * 0.08
            strike_pct = 0.90
        
        strike_price = int(spot * strike_pct)
        max_contracts = int(position_size / (strike_price * 0.1))
        floors = self.risk_framework._get_floors()
        
        return {
            "status": "NEAR_FLOOR",
            "advice": [
                f"当前价格 ${spot:,.0f} 接近支撑位 ${floors['regular']:,.0f}",
                "市场接近关键支撑，建议谨慎操作",
                "降低仓位，增加安全边际"
            ],
            "recommended_actions": [
                {
                    "action": "卖出深度OTM Put 期权",
                    "parameters": {
                        "delta_range": delta_range,
                        "dte_range": dte_range,
                        "strike_range": f"${int(floors['regular'] * 0.95):,}-${int(floors['regular']):,}",
                        "position_size": f"${position_size:,.0f}",
                        "max_contracts": max(max_contracts, 1)
                    },
                    "expected_apr": "100-180%",
                    "max_loss": f"${position_size * 0.3:,.0f}",
                    "reasoning": "接近支撑位时，选择更保守的行权价，增加安全垫"
                }
            ],
            "risk_management": {
                "stop_loss": f"价格跌破 ${int(floors['regular'] * 0.95):,} 时止损",
                "roll_strategy": "准备向下滚仓或对冲",
                "max_position": f"最多持有 {max(max_contracts, 1)} 张合约",
                "hedge_suggestion": "考虑买入少量Put期权作为保护"
            }
        }
    
    def _generate_adverse_advice(self, spot: float, profile: Dict, currency: str) -> Dict:
        """逆境市场建议 - 激进策略"""
        risk_tolerance = profile["risk_tolerance"]
        portfolio_size = profile["portfolio_size"]
        
        floors = self.risk_framework._get_floors()
        
        # 逆境时可以选择激进或保守
        if risk_tolerance == "low":
            return {
                "status": "ADVERSE",
                "advice": [
                    f"当前价格 ${spot:,.0f} 已跌破常规支撑",
                    "风险较高，建议观望或减仓",
                    "等待市场稳定后再入场"
                ],
                "recommended_actions": [
                    {
                        "action": "观望或减仓",
                        "parameters": {
                            "suggestion": "不新建仓位，等待反弹信号",
                            "cash_position": "建议保持50%以上现金"
                        },
                        "reasoning": "保守型投资者应避免在逆境中冒险"
                    }
                ],
                "risk_management": {
                    "stop_loss": "严格执行止损",
                    "roll_strategy": "如有持仓，考虑止损或向下滚仓",
                    "hedge_suggestion": "强烈建议对冲现有仓位"
                }
            }
        
        # 中高风险承受能力 - 激进抄底
        position_size = portfolio_size * 0.10 if risk_tolerance == "medium" else portfolio_size * 0.15
        strike_price = int(floors['extreme'] * 1.05)  # 略高于极端支撑
        max_contracts = int(position_size / (strike_price * 0.1))
        
        return {
            "status": "ADVERSE",
            "advice": [
                f"当前价格 ${spot:,.0f} 处于逆境区间",
                "市场恐慌，但对于激进投资者可能是机会",
                "严格控制仓位，快进快出"
            ],
            "recommended_actions": [
                {
                    "action": "卖出ITM Put 期权或买入Call",
                    "parameters": {
                        "delta_range": "0.30-0.40" if risk_tolerance == "high" else "0.25-0.35",
                        "dte_range": "7-14天",
                        "strike_range": f"${strike_price:,}-${int(floors['regular']):,}",
                        "position_size": f"${position_size:,.0f}",
                        "max_contracts": max(max_contracts, 1)
                    },
                    "expected_apr": "300-500%",
                    "max_loss": f"${position_size * 0.8:,.0f}",
                    "reasoning": "逆境中短期反弹概率较高，但需快进快出"
                }
            ],
            "risk_management": {
                "stop_loss": f"价格跌破 ${int(floors['extreme']):,} 时立即止损",
                "roll_strategy": "设置自动止损，不扛单",
                "max_position": f"最多持有 {max(max_contracts, 1)} 张合约",
                "time_limit": "持仓不超过7天"
            }
        }
    
    def _generate_panic_advice(self, spot: float, profile: Dict, currency: str) -> Dict:
        """恐慌市场建议 - 止损为主"""
        floors = self.risk_framework._get_floors()
        
        return {
            "status": "PANIC",
            "advice": [
                f"当前价格 ${spot:,.0f} 已进入恐慌区间",
                "🚨 极端行情，强烈建议止损或对冲",
                "保护本金是第一优先级"
            ],
            "recommended_actions": [
                {
                    "action": "止损或对冲",
                    "parameters": {
                        "primary_action": "平掉所有裸空头寸",
                        "hedge_action": "买入ATM Put保护",
                        "cash_position": "保持70%以上现金"
                    },
                    "reasoning": "恐慌市中，任何抄底行为都极其危险"
                }
            ],
            "risk_management": {
                "stop_loss": "立即执行",
                "roll_strategy": "不要滚仓，直接止损",
                "hedge_suggestion": "必须对冲，或清仓观望",
                "warning": "⚠️ 此时卖出期权风险极高，建议完全退出"
            }
        }
