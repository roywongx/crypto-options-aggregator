"""
IV 期限结构分析引擎 v2.0
基于学术研究的波动率期限结构分析框架

学术参考:
1. Hull & White (1987): 波动率期限结构的均值回归特性
2. Derman & Kani (1994): 局部波动率曲面建模
3. Bakshi, Kapadia & Madan (2003): 波动率风险溢价 (VRP)
4. Christoffersen, Jacobs & Heston (2014): 期限结构形态与市场状态
5. Binance Research (2024): 加密货币期权 IV 期限结构实证研究

核心分析维度:
1. 形态分类 (Contango / Backwardation / Hump / Mixed)
2. 斜率与曲率测量
3. 波动率风险溢价 (VRP) 估计
4. 市场状态判定 (恐慌 / 正常 / 贪婪)
5. 策略建议生成
"""
import math
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class IVTermStructureAnalyzer:
    """IV 期限结构分析引擎"""
    
    # 期限结构形态定义
    STRUCTURE_TYPES = {
        "normal_contango": {
            "name": "正常 Contango",
            "icon": "📈",
            "color": "text-green-400",
            "description": "远期 IV > 近期 IV，市场情绪稳定",
            "market_state": "NORMAL",
            "action": "适合 Sell 近期期权收取时间价值"
        },
        "steep_contango": {
            "name": "陡峭 Contango",
            "icon": "📊",
            "color": "text-cyan-400",
            "description": "远期 IV 显著高于近期，预期波动率上升",
            "market_state": "CALM_BULLISH",
            "action": "Calendar Spread：卖近买远获取 Vega 收益"
        },
        "backwardation": {
            "name": "倒挂 Backwardation",
            "icon": "⚠️",
            "color": "text-red-400",
            "description": "近期 IV > 远期 IV，市场恐慌信号",
            "market_state": "PANIC",
            "action": "避免 Sell 近期 Put，考虑买入保护性 Put"
        },
        "severe_backwardation": {
            "name": "严重倒挂",
            "icon": "🔴",
            "color": "text-red-300",
            "description": "近期 IV 远高于远期，极端恐慌",
            "market_state": "CRISIS",
            "action": "全面防御：降低 Delta 暴露，持有现金"
        },
        "hump_shaped": {
            "name": "驼峰形态",
            "icon": "🏔️",
            "color": "text-yellow-400",
            "description": "中期 IV 最高，市场预期中期事件驱动",
            "market_state": "EVENT_DRIVEN",
            "action": "关注中期事件，避免持有事件到期前的卖方头寸"
        },
        "flat": {
            "name": "平坦结构",
            "icon": "➖",
            "color": "text-gray-400",
            "description": "各期限 IV 相近，市场方向不明",
            "market_state": "UNCERTAIN",
            "action": "观望为主，降低仓位"
        }
    }
    
    @classmethod
    def analyze_term_structure(cls, term_data: List[Dict], spot: float, 
                                hist_vol: Optional[float] = None, 
                                dvol_index: Optional[float] = None) -> Dict:
        """
        全面分析 IV 期限结构
        
        Args:
            term_data: [{dte: int, avg_iv: float}, ...] 按 DTE 排序
            spot: 当前现货价格
            hist_vol: 历史波动率 (30天)
            dvol_index: Deribit DVOL 指数
        
        Returns:
            完整分析报告
        """
        if not term_data or len(term_data) < 2:
            return {"error": "数据不足，至少需要 2 个期限点"}
        
        # 排序确保按 DTE 递增
        data = sorted(term_data, key=lambda x: x["dte"])
        
        # ===== 1. 形态分类 =====
        structure_type = cls._classify_structure(data)
        
        # ===== 2. 斜率分析 =====
        slope_analysis = cls._analyze_slope(data)
        
        # ===== 3. 曲率分析 (Convexity) =====
        curvature = cls._analyze_curvature(data)
        
        # ===== 4. IV 分位数定位 =====
        iv_levels = cls._classify_iv_levels(data)
        
        # ===== 5. 波动率风险溢价 (VRP) 估计 =====
        vrp = cls._estimate_vrp(data, hist_vol)
        
        # ===== 6. 市场状态判定 =====
        market_state = cls._assess_market_state(data, structure_type, slope_analysis, iv_levels)
        
        # ===== 7. 策略建议 =====
        recommendations = cls._generate_recommendations(structure_type, slope_analysis, 
                                                         curvature, iv_levels, vrp, market_state)
        
        # ===== 8. 期限溢价分析 =====
        term_premium = cls._analyze_term_premium(data)
        
        return {
            "structure_type": structure_type,
            "slope": slope_analysis,
            "curvature": curvature,
            "iv_levels": iv_levels,
            "vrp": vrp,
            "market_state": market_state,
            "recommendations": recommendations,
            "term_premium": term_premium,
            "data_points": len(data),
            "term_data": data,
        }
    
    @classmethod
    def _classify_structure(cls, data: List[Dict]) -> Dict:
        """
        分类期限结构形态
        基于 Hull (2018) 和 Christoffersen et al. (2014) 的分类方法
        """
        if len(data) < 3:
            # 数据点不足，简化判定
            front_iv = data[0]["avg_iv"]
            back_iv = data[-1]["avg_iv"]
            slope_pct = (back_iv - front_iv) / front_iv * 100 if front_iv > 0 else 0
            
            if front_iv > back_iv:
                st = "backwardation" if slope_pct > -5 else "flat"
            else:
                st = "contango"
            
            return cls.STRUCTURE_TYPES.get(st, cls.STRUCTURE_TYPES["flat"])
        
        ivs = [d["avg_iv"] for d in data if d.get("avg_iv") and d["avg_iv"] > 0]
        if len(ivs) < 3:
            return cls.STRUCTURE_TYPES["flat"]
        
        # 计算相邻点斜率
        slopes = []
        for i in range(len(ivs) - 1):
            s = (ivs[i+1] - ivs[i]) / max(ivs[i], 1) * 100
            slopes.append(s)
        
        avg_slope = sum(slopes) / len(slopes)
        front_slope = slopes[0] if slopes else 0
        back_slope = slopes[-1] if len(slopes) > 1 else 0
        
        # 判定规则
        # 1. 严重倒挂：近期 IV > 远期 且 近期 IV 极高
        if ivs[0] > ivs[-1] and ivs[0] > 70:
            return cls.STRUCTURE_TYPES["severe_backwardation"]
        
        # 2. 倒挂：前段斜率为负且绝对值显著
        if front_slope < -3 and avg_slope < 0:
            return cls.STRUCTURE_TYPES["backwardation"]
        
        # 3. 驼峰：先升后降（存在局部最大值）
        if front_slope > 2 and back_slope < -2:
            return cls.STRUCTURE_TYPES["hump_shaped"]
        
        # 4. 陡峭 Contango：整体上升且斜率显著
        if avg_slope > 5 and all(s > 2 for s in slopes[:3] if len(slopes) >= 3):
            return cls.STRUCTURE_TYPES["steep_contango"]
        
        # 5. 平坦：各点差异 < 5%
        iv_range = max(ivs) - min(ivs)
        if iv_range < min(ivs) * 0.05:
            return cls.STRUCTURE_TYPES["flat"]
        
        # 6. 默认：正常 Contango
        return cls.STRUCTURE_TYPES["normal_contango"]
    
    @classmethod
    def _analyze_slope(cls, data: List[Dict]) -> Dict:
        """
        斜率分析：测量期限结构的倾斜程度
        学术依据：斜率反映市场对远期波动率的预期
        """
        ivs = [d["avg_iv"] for d in data if d.get("avg_iv") and d["avg_iv"] > 0]
        dtes = [d["dte"] for d in data if d.get("avg_iv") and d["avg_iv"] > 0]
        
        if len(ivs) < 2:
            return {"value": 0, "percent": 0, "front_iv": 0, "back_iv": 0}
        
        front_iv = ivs[0]
        back_iv = ivs[-1]
        front_dte = dtes[0]
        back_dte = dtes[-1]
        
        slope_pct = (back_iv - front_iv) / front_iv * 100 if front_iv > 0 else 0
        slope_per_day = (back_iv - front_iv) / (back_dte - front_dte) if back_dte != front_dte else 0
        
        # 斜率分档
        if slope_pct > 20:
            grade = "VERY_STEEP"
            desc = "极度陡峭：远期波动率预期远高于近期"
        elif slope_pct > 10:
            grade = "STEEP"
            desc = "陡峭：预期波动率上升"
        elif slope_pct > 3:
            grade = "NORMAL"
            desc = "正常：温和 Contango"
        elif slope_pct > -3:
            grade = "FLAT"
            desc = "平坦：期限溢价几乎为零"
        elif slope_pct > -10:
            grade = "INVERTED"
            desc = "倒挂：近期恐慌"
        else:
            grade = "SEVERELY_INVERTED"
            desc = "严重倒挂：极度恐慌"
        
        return {
            "value": round(slope_per_day, 4),
            "percent": round(slope_pct, 1),
            "front_iv": round(front_iv, 1),
            "back_iv": round(back_iv, 1),
            "front_dte": front_dte,
            "back_dte": back_dte,
            "grade": grade,
            "description": desc,
        }
    
    @classmethod
    def _analyze_curvature(cls, data: List[Dict]) -> Dict:
        """
        曲率分析：测量期限结构的凹凸性
        学术依据：正曲率 = 驼峰 = 事件驱动预期
        """
        ivs = [d["avg_iv"] for d in data if d.get("avg_iv") and d["avg_iv"] > 0]
        
        if len(ivs) < 3:
            return {"value": 0, "type": "INSUFFICIENT_DATA"}
        
        # 使用二阶差分近似曲率
        second_diffs = []
        for i in range(1, len(ivs) - 1):
            sd = ivs[i+1] - 2*ivs[i] + ivs[i-1]
            second_diffs.append(sd)
        
        avg_curvature = sum(second_diffs) / len(second_diffs)
        
        # 最大值位置
        max_iv = max(ivs)
        max_idx = ivs.index(max_iv)
        max_dte_pct = max_idx / (len(ivs) - 1) if len(ivs) > 1 else 0
        
        if avg_curvature > 5:
            ctype = "HUMP"
            desc = f"驼峰形态：中期 IV 最高 ({max_iv:.1f}%)，市场预期中期事件"
        elif avg_curvature < -3:
            ctype = "U_SHAPED"
            desc = "U 型结构：两端 IV 高于中间"
        else:
            ctype = "LINEAR"
            desc = "近似线性结构"
        
        return {
            "value": round(avg_curvature, 2),
            "type": ctype,
            "description": desc,
            "max_iv": round(max_iv, 1),
            "max_iv_position_pct": round(max_dte_pct * 100, 0),
        }
    
    @classmethod
    def _classify_iv_levels(cls, data: List[Dict]) -> Dict:
        """
        IV 绝对水平分类
        基于历史经验：
        - DVOL < 40: 低波环境
        - DVOL 40-60: 正常
        - DVOL 60-80: 高波
        - DVOL > 80: 极端
        """
        ivs = [d["avg_iv"] for d in data if d.get("avg_iv") and d["avg_iv"] > 0]
        if not ivs:
            return {"avg_iv": 0, "min_iv": 0, "max_iv": 0, "regime": "UNKNOWN"}
        
        avg_iv = sum(ivs) / len(ivs)
        
        if avg_iv > 80:
            regime = "EXTREME"
            desc = "极端高波：市场极度恐慌"
        elif avg_iv > 60:
            regime = "HIGH"
            desc = "高波动率环境"
        elif avg_iv > 40:
            regime = "NORMAL"
            desc = "正常波动率"
        elif avg_iv > 30:
            regime = "LOW"
            desc = "低波动率环境"
        else:
            regime = "VERY_LOW"
            desc = "极低波动率：市场过度平静"
        
        return {
            "avg_iv": round(avg_iv, 1),
            "min_iv": round(min(ivs), 1),
            "max_iv": round(max(ivs), 1),
            "range": round(max(ivs) - min(ivs), 1),
            "regime": regime,
            "description": desc,
            "iv_spread": round(max(ivs) - min(ivs), 1),
        }
    
    @classmethod
    def _estimate_vrp(cls, data: List[Dict], hist_vol: Optional[float] = None) -> Dict:
        """
        波动率风险溢价 (Volatility Risk Premium) 估计
        VRP = IV - HV
        
        学术参考: Bakshi, Kapadia & Madan (2003)
        - VRP > 0: 期权被高估（卖方有利）
        - VRP < 0: 期权被低估（买方有利）
        
        加密货币市场特征:
        - BTC 长期 VRP 约 10-20%
        - 恐慌时 VRP 可飙升至 30%+
        - 平静期 VRP 约 5-10%
        """
        ivs = [d["avg_iv"] for d in data if d.get("avg_iv") and d["avg_iv"] > 0]
        if not ivs:
            return {"value": 0, "percent": 0, "signal": "NO_DATA"}
        
        avg_iv = sum(ivs) / len(ivs)
        
        if hist_vol and hist_vol > 0:
            vrp = avg_iv - hist_vol
            vrp_pct = vrp / hist_vol * 100 if hist_vol > 0 else 0
            
            if vrp > 25:
                signal = "HIGH_SELL_EDGE"
                desc = f"VRP +{vrp:.1f}% ({vrp_pct:.0f}%)，期权显著高估，卖方优势大"
            elif vrp > 10:
                signal = "SELL_EDGE"
                desc = f"VRP +{vrp:.1f}% ({vrp_pct:.0f}%)，期权被高估，适合 Sell"
            elif vrp > 0:
                signal = "SLIGHT_SELL_EDGE"
                desc = f"VRP +{vrp:.1f}%，期权轻微高估"
            elif vrp > -10:
                signal = "FAIR"
                desc = f"VRP {vrp:.1f}%，期权定价合理"
            else:
                signal = "BUY_EDGE"
                desc = f"VRP {vrp:.1f}%，期权被低估，买方优势大"
        else:
            # 无历史波动率时，用近期 IV 作为隐含基准
            vrp = avg_iv - ivs[0] if ivs else 0
            vrp_pct = 0
            signal = "NO_HV_DATA"
            desc = "缺乏历史波动率数据，无法计算 VRP"
        
        return {
            "value": round(vrp, 1),
            "percent": round(vrp_pct, 0),
            "implied_vol": round(avg_iv, 1),
            "hist_vol": round(hist_vol, 1) if hist_vol else None,
            "signal": signal,
            "description": desc,
        }
    
    @classmethod
    def _assess_market_state(cls, data: List[Dict], structure_type: Dict,
                              slope: Dict, iv_levels: Dict) -> Dict:
        """
        综合市场状态评估
        结合形态、斜率、IV 水平综合判定
        """
        score = 0
        signals = []
        
        # 形态信号
        if structure_type["name"] == "严重倒挂":
            score -= 40
            signals.append("🔴 期限结构严重倒挂")
        elif structure_type["name"] == "倒挂 Backwardation":
            score -= 25
            signals.append("⚠️ 期限结构倒挂")
        elif structure_type["name"] in ("陡峭 Contango", "正常 Contango"):
            score += 15
            signals.append("🟢 期限结构正常")
        
        # IV 水平信号
        if iv_levels["regime"] == "EXTREME":
            score -= 30
            signals.append("🔴 极端高波动率")
        elif iv_levels["regime"] == "HIGH":
            score -= 15
            signals.append("⚠️ 高波动率")
        elif iv_levels["regime"] == "VERY_LOW":
            score += 10
            signals.append("🟢 极低波动率")
        
        # 斜率信号
        if slope.get("grade") == "SEVERELY_INVERTED":
            score -= 25
        elif slope.get("grade") == "INVERTED":
            score -= 15
        elif slope.get("grade") in ("STEEP", "VERY_STEEP"):
            score += 10
        
        # 综合判定
        if score >= 25:
            state = "BULLISH_CALM"
            state_name = "牛市平静"
            state_icon = "🟢"
            state_color = "text-green-400"
            advice = "市场情绪稳定，适合积极收取权利金"
        elif score >= 10:
            state = "NORMAL"
            state_name = "正常"
            state_icon = "🟡"
            state_color = "text-yellow-400"
            advice = "市场状态正常，维持现有策略"
        elif score >= -10:
            state = "CAUTIOUS"
            state_name = "谨慎"
            state_icon = "🟠"
            state_color = "text-orange-400"
            advice = "市场波动加大，建议降低仓位"
        elif score >= -30:
            state = "FEAR"
            state_name = "恐惧"
            state_icon = "🔴"
            state_color = "text-red-400"
            advice = "市场恐慌情绪蔓延，防御为主"
        else:
            state = "PANIC"
            state_name = "恐慌"
            state_icon = "💀"
            state_color = "text-red-300"
            advice = "市场极度恐慌，优先保本"
        
        return {
            "state": state,
            "name": state_name,
            "icon": state_icon,
            "color": state_color,
            "advice": advice,
            "signals": signals,
            "composite_score": score,
        }
    
    @classmethod
    def _generate_recommendations(cls, structure_type: Dict, slope: Dict,
                                   curvature: Dict, iv_levels: Dict,
                                   vrp: Dict, market_state: Dict) -> List[Dict]:
        """
        基于学术研究的策略建议生成
        """
        recs = []
        
        # 1. 基于形态的建议
        if structure_type["name"] in ("倒挂 Backwardation", "严重倒挂"):
            recs.append({
                "type": "warning",
                "title": "⚠️ 恐慌信号",
                "body": "期限结构倒挂表明市场对近期风险极度担忧。历史数据显示，严重倒挂往往是短期底部的信号（恐慌极点 = 买入机会），但短期内仍有下行风险。",
                "action": "避免 Sell 近期 Put，考虑买入近月 Put 保护"
            })
        elif structure_type["name"] == "陡峭 Contango":
            recs.append({
                "type": "opportunity",
                "title": "📊 Calendar Spread 机会",
                "body": "陡峭 Contango 意味着远期 IV 溢价显著。卖出近月期权同时买入远月期权可以获取期限结构收敛的收益。",
                "action": "卖出近月 ATM Put + 买入远月 ATM Put"
            })
        elif structure_type["name"] == "驼峰形态":
            recs.append({
                "type": "info",
                "title": "🏔️ 事件驱动关注",
                "body": "中期 IV 最高表明市场在定价某个中期事件（如美联储会议、减半事件等）。",
                "action": "在事件前降低卖方头寸，事件后可考虑 Sell 波动率"
            })
        
        # 2. 基于 VRP 的建议
        if vrp.get("signal") == "HIGH_SELL_EDGE":
            recs.append({
                "type": "opportunity",
                "title": "💰 卖方优势显著",
                "body": f"VRP +{vrp.get('value', 0):.1f}%，期权价格显著高于实际波动率，卖方具有统计优势。",
                "action": "积极 Sell Put/Call 获取风险溢价"
            })
        elif vrp.get("signal") == "BUY_EDGE":
            recs.append({
                "type": "info",
                "title": "📈 买方机会",
                "body": "VRP 为负，期权价格低于实际波动率，买方具有统计优势。",
                "action": "考虑买入期权而非卖出"
            })
        
        # 3. 基于 IV 水平的建议
        if iv_levels["regime"] == "HIGH":
            recs.append({
                "type": "opportunity",
                "title": "💰 高 IV 环境",
                "body": f"平均 IV {iv_levels['avg_iv']:.1f}% 处于高位，权利金丰厚。但注意高 IV 通常不可持续，均值回归概率大。",
                "action": "Sell 期权获取高权利金，但准备 IV 回落时的调整"
            })
        elif iv_levels["regime"] == "VERY_LOW":
            recs.append({
                "type": "warning",
                "title": "📉 低 IV 陷阱",
                "body": f"平均 IV 仅 {iv_levels['avg_iv']:.1f}%，权利金微薄。历史表明低 IV 环境后常伴随波动率爆发。",
                "action": "减少 Sell 仓位，考虑买入跨式组合"
            })
        
        # 4. 基于市场状态的综合建议
        if market_state["state"] in ("FEAR", "PANIC"):
            recs.append({
                "type": "warning",
                "title": market_state["icon"] + " " + market_state["name"] + " 市场",
                "body": market_state["advice"],
                "action": "降低仓位，保留现金，等待市场稳定"
            })
        
        if not recs:
            recs.append({
                "type": "info",
                "title": "ℹ️ 市场状态正常",
                "body": "期限结构健康，波动率水平适中。",
                "action": "维持现有策略，积极收取时间价值"
            })
        
        return recs
    
    @classmethod
    def _analyze_term_premium(cls, data: List[Dict]) -> Dict:
        """
        期限溢价分析：测量每个月的溢价变化
        """
        if len(data) < 2:
            return {"premiums": []}
        
        premiums = []
        for i in range(1, len(data)):
            prev = data[i-1]
            curr = data[i]
            if prev.get("avg_iv") and curr.get("avg_iv") and prev["avg_iv"] > 0:
                premium = curr["avg_iv"] - prev["avg_iv"]
                premium_pct = premium / prev["avg_iv"] * 100
                dte_diff = curr["dte"] - prev["dte"]
                premiums.append({
                    "from_dte": prev["dte"],
                    "to_dte": curr["dte"],
                    "dte_diff": dte_diff,
                    "iv_from": round(prev["avg_iv"], 1),
                    "iv_to": round(curr["avg_iv"], 1),
                    "premium": round(premium, 1),
                    "premium_pct": round(premium_pct, 1),
                    "premium_per_day": round(premium / dte_diff, 4) if dte_diff > 0 else 0,
                })
        
        return {
            "premiums": premiums,
            "total_premium": round(sum(p["premium"] for p in premiums), 1),
            "avg_premium_per_day": round(
                sum(p["premium_per_day"] for p in premiums) / len(premiums), 4
            ) if premiums else 0,
        }
