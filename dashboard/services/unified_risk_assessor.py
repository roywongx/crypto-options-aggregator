"""
统一风险评估器
整合价格、波动率、链上数据等多维度风险
"""
from typing import Dict, Any
from services.risk_framework import RiskFramework
import concurrent.futures
import time


class UnifiedRiskAssessor:
    def __init__(self):
        self.risk_framework = RiskFramework()

    def assess_comprehensive_risk(self, spot: float, currency: str = "BTC") -> Dict[str, Any]:
        # 并行执行独立的评估任务
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_price = executor.submit(self._assess_price_risk, spot)
            future_volatility = executor.submit(self._assess_volatility_risk, currency)
            future_sentiment = executor.submit(self._assess_sentiment_risk, spot, currency)
            future_liquidity = executor.submit(self._assess_liquidity_risk, currency)

            price_risk = future_price.result()
            volatility_risk = future_volatility.result()
            sentiment_risk = future_sentiment.result()
            liquidity_risk = future_liquidity.result()

        composite_score = (
            price_risk["score"] * 0.30 +
            volatility_risk["score"] * 0.35 +
            sentiment_risk["score"] * 0.20 +
            liquidity_risk["score"] * 0.15
        )

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
        status = self.risk_framework.get_status(spot)
        risk_scores = {"NORMAL": 20, "NEAR_FLOOR": 50, "ADVERSE": 75, "PANIC": 95}
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
        try:
            from services.dvol_analyzer import get_dvol_from_deribit
            dvol_data = get_dvol_from_deribit(currency)
            dvol = dvol_data.get("current", 50)
            z_score = dvol_data.get("z_score", 0)

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
            return {
                "score": 50,
                "dvol": 50,
                "z_score": 0,
                "signal": "数据不可用",
                "factors": ["无法获取波动率数据"],
                "error": str(e)
            }

    def _assess_sentiment_risk(self, spot: float, currency: str) -> Dict[str, Any]:
        factors = []
        score = 40

        try:
            from services.macro_data import get_fear_greed_index, get_fear_greed_risk_multiplier
            fg_data = get_fear_greed_index()
            fng_value = fg_data.get("value", 50)
            fng_label = fg_data.get("classification", "Neutral")
            factors.append(f"恐惧贪婪指数: {fng_value} ({fng_label})")

            multiplier = get_fear_greed_risk_multiplier(fng_value)
            score = int(score * multiplier)

            if fng_value is not None and fng_value <= 20:
                score = min(score, 70)
        except Exception:
            factors.append("恐惧贪婪指数: 获取失败")

        try:
            from services.dvol_analyzer import get_dvol_from_deribit
            dvol_data = get_dvol_from_deribit(currency)
            dvol = dvol_data.get("current", 50)
            dvol_z = dvol_data.get("z_score", 0)
            factors.append(f"DVOL恐慌代理: {dvol:.1f}% (Z={dvol_z:.2f})")
            if dvol_z > 2:
                score = min(100, score + 15)
            elif dvol_z < -2:
                score = max(0, score - 10)
        except Exception:
            pass

        if not factors:
            status = self.risk_framework.get_status(spot)
            sentiment_scores = {"NORMAL": 30, "NEAR_FLOOR": 50, "ADVERSE": 75, "PANIC": 90}
            score = sentiment_scores.get(status, 40)
            factors.append(f"基于价格位置的情绪评估: {status}")

        return {"score": score, "factors": factors}

    def _assess_liquidity_risk(self, currency: str) -> Dict[str, Any]:
        score = 30
        factors = []

        try:
            from db.connection import execute_read
            rows = execute_read("SELECT contracts_data FROM scan_records WHERE currency = ? ORDER BY timestamp DESC LIMIT 1", (currency,))
            if rows and rows[0][0]:
                import json
                contracts = json.loads(rows[0][0])
                if contracts:
                    spreads = [c.get("spread_pct", 0) for c in contracts if c.get("spread_pct", 0) > 0]
                    ois = [c.get("open_interest", 0) for c in contracts]

                    if spreads:
                        avg_spread = sum(spreads) / len(spreads)
                        factors.append(f"平均买卖价差: {avg_spread:.2f}%")
                        if avg_spread > 5:
                            score = min(100, score + 40)
                        elif avg_spread > 2:
                            score = min(100, score + 20)

                    low_oi_count = sum(1 for oi in ois if oi < 50)
                    if low_oi_count > len(contracts) * 0.5:
                        score = min(100, score + 25)
                        factors.append(f"低持仓量合约占比: {low_oi_count}/{len(contracts)}")
                    else:
                        factors.append(f"持仓量分布正常 ({len(contracts)}个合约)")
                else:
                    factors.append("暂无合约数据")
            else:
                factors.append("暂无扫描数据")
        except Exception:
            factors.append("流动性数据获取失败")

        return {"score": score, "factors": factors}

    def _generate_risk_recommendations(self, composite_score: float) -> list:
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
