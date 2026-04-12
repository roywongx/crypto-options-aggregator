"""
统一风险评估器
整合价格、波动率、链上数据等多维度风险
"""
from typing import Dict, Any
from services.risk_framework import RiskFramework


class UnifiedRiskAssessor:
    def __init__(self):
        self.risk_framework = RiskFramework()
    
    def assess_comprehensive_risk(self, spot: float, currency: str = "BTC") -> Dict[str, Any]:
        """综合风险评估"""
        
        # 1. 价格风险
        price_risk = self._assess_price_risk(spot)
        
        # 2. 波动率风险
        volatility_risk = self._assess_volatility_risk(currency)
        
        # 3. 市场情绪风险
        sentiment_risk = self._assess_sentiment_risk(currency)
        
        # 4. 流动性风险
        liquidity_risk = self._assess_liquidity_risk(currency)
        
        # 综合评分 (0-100, 越高风险越大)
        composite_score = (
            price_risk["score"] * 0.4 +
            volatility_risk["score"] * 0.3 +
            sentiment_risk["score"] * 0.2 +
            liquidity_risk["score"] * 0.1
        )
        
        # 风险等级
        if composite_score < 30:
            risk_level = "LOW"
        elif composite_score < 60:
            risk_level = "MEDIUM"
        elif composite_score < 80:
            risk_level = "HIGH"
        else:
            risk_level = "EXTREME"
        
        return {
            "composite_score": round(composite_score, 1),
            "risk_level": risk_level,
            "components": {
                "price_risk": price_risk,
                "volatility_risk": volatility_risk,
                "sentiment_risk": sentiment_risk,
                "liquidity_risk": liquidity_risk
            },
            "recommendations": self._generate_risk_recommendations(composite_score),
            "timestamp": __import__('datetime').datetime.now().isoformat()
        }
    
    def _assess_price_risk(self, spot: float) -> Dict[str, Any]:
        """价格风险评估"""
        status = self.risk_framework.get_status(spot)
        
        risk_scores = {
            "NORMAL": 20,
            "NEAR_FLOOR": 50,
            "ADVERSE": 75,
            "PANIC": 95
        }
        
        floors = self.risk_framework._get_floors()
        
        return {
            "score": risk_scores.get(status, 50),
            "status": status,
            "factors": [
                f"当前价格: ${spot:,.0f}",
                f"常规支撑: ${floors['regular']:,.0f}",
                f"极端支撑: ${floors['extreme']:,.0f}",
                f"风险状态: {status}"
            ]
        }
    
    def _assess_volatility_risk(self, currency: str) -> Dict[str, Any]:
        """波动率风险评估"""
        try:
            # 尝试从服务获取DVOL数据
            from services.dvol_analyzer import get_dvol_from_deribit
            dvol_data = get_dvol_from_deribit(currency)
            dvol = dvol_data.get("current", 50)
            z_score = dvol_data.get("z_score", 0)
            
            # 计算波动率风险分数
            if dvol > 80:
                score = 90
            elif dvol > 60:
                score = 70
            elif dvol > 40:
                score = 40
            elif dvol > 20:
                score = 20
            else:
                score = 10
            
            # Z-score 调整
            if abs(z_score) > 2:
                score = min(100, score + 20)
            
            return {
                "score": score,
                "dvol": dvol,
                "z_score": z_score,
                "signal": dvol_data.get("signal", "正常区间"),
                "factors": [
                    f"DVOL: {dvol:.1f}%",
                    f"Z-Score: {z_score:.2f}",
                    f"信号: {dvol_data.get('signal', '')}"
                ]
            }
        except Exception as e:
            # 如果无法获取DVOL，使用默认值
            return {
                "score": 50,
                "dvol": 50,
                "z_score": 0,
                "signal": "数据不可用",
                "factors": ["无法获取波动率数据"],
                "error": str(e)
            }
    
    def _assess_sentiment_risk(self, currency: str) -> Dict[str, Any]:
        """市场情绪风险评估"""
        # 这里可以接入恐惧贪婪指数、社交媒体情绪等
        # 暂时使用基于价格位置的简单估算
        try:
            status = self.risk_framework.get_status(__import__('services').trades.fetch_deribit_summaries(currency)[0].get('mark_price', 50000))
            
            sentiment_scores = {
                "NORMAL": 30,  # 正常情绪
                "NEAR_FLOOR": 50,  # 谨慎情绪
                "ADVERSE": 75,  # 恐慌情绪
                "PANIC": 90  # 极度恐慌
            }
            
            return {
                "score": sentiment_scores.get(status, 50),
                "factors": [
                    f"基于价格位置的情绪评估",
                    f"当前市场情绪: {status}"
                ]
            }
        except:
            return {
                "score": 40,
                "factors": ["情绪数据待接入"]
            }
    
    def _assess_liquidity_risk(self, currency: str) -> Dict[str, Any]:
        """流动性风险评估"""
        # 这里可以接入买卖价差、深度等数据
        # 暂时使用默认值
        return {
            "score": 30,
            "factors": [
                "流动性正常",
                "Deribit市场深度充足"
            ]
        }
    
    def _generate_risk_recommendations(self, composite_score: float) -> list:
        """根据综合风险分数生成建议"""
        if composite_score < 30:
            return [
                "✅ 风险较低，可适当增加仓位",
                "📈 适合卖出 OTM Put 获取权利金",
                "🎯 可考虑稍高的 Delta 值 (0.20-0.30)"
            ]
        elif composite_score < 60:
            return [
                "⚠️ 风险中等，保持标准仓位",
                "📊 建议卖出 ATM 附近期权 (Delta 0.15-0.25)",
                "🛡️ 注意设置止损，控制最大亏损"
            ]
        elif composite_score < 80:
            return [
                "🔴 风险较高，减少仓位",
                "📉 建议卖出 ITM 期权或降低 Delta (< 0.20)",
                "🚨 准备应对策略，考虑对冲保护",
                "⏰ 缩短持仓周期，快进快出"
            ]
        else:
            return [
                "🚨 风险极高，建议清仓或对冲",
                "❌ 避免卖出裸期权",
                "💰 保持现金，等待市场稳定",
                "📉 如有持仓，立即止损"
            ]
