"""
AI 驱动的大宗交易情绪分析系统 v2.0
基于多维交叉分析和机构行为模式识别的大宗交易意图分析

分析框架:
1. 多维意图分类引擎 (6 种意图)
2. 机构行为模式识别 (Block Trade, Sweep, Multi-leg)
3. 市场微观结构分析 (Order Flow, Gamma Impact)
4. 动态阈值系统 (波动率自适应)
5. 信号强度分级 (强/中/弱)
6. 策略建议生成引擎

无需 LLM API，本地规则引擎离线可用
"""
import logging
import math
from typing import Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

class AISentimentAnalyzer:
    """AI 驱动的大宗交易情绪分析引擎 v2.0"""
    
    # 意图判定规则库 - 6 维分类
    INTENT_RULES = {
        "directional_speculation": {
            "name": "方向性投机",
            "icon": "🎯",
            "color": "text-red-400",
            "description": "单边押注价格方向，高风险高收益",
            "risk_level": "HIGH"
        },
        "institutional_hedging": {
            "name": "机构对冲",
            "icon": "🛡️",
            "color": "text-blue-400",
            "description": "保护性对冲现有持仓，降低组合风险",
            "risk_level": "MEDIUM"
        },
        "arbitrage": {
            "name": "套利交易",
            "icon": "⚖️",
            "color": "text-green-400",
            "description": "利用价差进行无风险/低风险套利",
            "risk_level": "LOW"
        },
        "market_maker_adjust": {
            "name": "做市商调仓",
            "icon": "🔄",
            "color": "text-yellow-400",
            "description": "做市商调整期权库存，管理 Delta 敞口",
            "risk_level": "MEDIUM"
        },
        "income_generation": {
            "name": "收益增强",
            "icon": "💰",
            "color": "text-purple-400",
            "description": "通过卖权收取时间价值，Theta 收益策略",
            "risk_level": "MEDIUM"
        },
        "volatility_play": {
            "name": "波动率博弈",
            "icon": "📊",
            "color": "text-cyan-400",
            "description": "押注波动率方向（升波/降波），而非价格方向",
            "risk_level": "HIGH"
        }
    }
    
    # 机构行为模式
    INSTITUTIONAL_PATTERNS = {
        "block_trade_split": "大宗分单（规避市场冲击）",
        "sweep_order": "扫单（跨行权价快速建仓）",
        "multi_leg_strategy": "多腿组合策略（价差/跨式）",
        "roll_operation": "滚仓操作（移仓换月）",
        "delta_hedge": "动态 Delta 对冲"
    }
    
    @classmethod
    def analyze_trade_intent(cls, trade: Dict, spot_price: float, market_iv: Optional[float] = None) -> Dict:
        """
        分析单笔大宗交易的意图（多维交叉分析）
        
        Args:
            trade: 交易数据
            spot_price: 当前现货价格
            market_iv: 市场隐含波动率（可选）
        
        Returns:
            意图分析结果（含信号强度和模式识别）
        """
        score = {intent: 0.0 for intent in cls.INTENT_RULES}
        reasoning = []
        signals = []
        
        # 解析字段
        trade_type = trade.get("option_type", trade.get("type", "")).upper()
        if trade_type in ("PUT", "P"):
            trade_type = "P"
        elif trade_type in ("CALL", "C"):
            trade_type = "C"
        else:
            trade_type = "C"  # 默认
        
        delta = trade.get("delta", 0)
        abs_delta = abs(delta)
        premium = trade.get("premium_usd", trade.get("premium", 0)) or trade.get("notional_usd", trade.get("notional", 0))
        strike = trade.get("strike", 0)
        spot = trade.get("underlying_price", trade.get("index_price", spot_price))
        amount = trade.get("volume", trade.get("amount", 0))
        iv = trade.get("iv", 0) or trade.get("implied_vol", 0)
        dte = trade.get("dte", trade.get("days_to_expiry", 30))
        side = trade.get("side", trade.get("direction", "")).upper()
        
        if spot <= 0:
            spot = spot_price
        
        # ===== 维度 1: Moneyness 分析 =====
        moneyness_score = cls._analyze_moneyness(trade_type, strike, spot, score, reasoning)
        
        # ===== 维度 2: Delta 行为分析 =====
        delta_score = cls._analyze_delta(trade_type, delta, abs_delta, amount, score, reasoning)
        
        # ===== 维度 3: 波动率分析 =====
        vol_score = cls._analyze_volatility(trade_type, iv, market_iv, dte, score, reasoning, signals)
        
        # ===== 维度 4: 名义价值和规模分析 =====
        size_score = cls._analyze_size(premium, amount, spot, score, reasoning, signals)
        
        # ===== 维度 5: 期限结构分析 =====
        term_score = cls._analyze_term_structure(dte, trade_type, score, reasoning)
        
        # ===== 维度 6: 订单流分析 =====
        flow_score = cls._analyze_order_flow(side, trade_type, delta, amount, premium, spot, score, reasoning, signals)
        
        # ===== 机构行为模式识别 =====
        pattern = cls._identify_institutional_pattern(trade_type, strike, spot, amount, premium, dte, iv, trade)
        if pattern:
            reasoning.append(f"模式识别: {pattern}")
            if pattern in ("block_trade_split", "sweep_order"):
                score["institutional_hedging"] += 15
            elif pattern == "multi_leg_strategy":
                score["arbitrage"] += 15
            elif pattern == "roll_operation":
                score["income_generation"] += 10
        
        # ===== Gamma 敞口影响分析 =====
        gamma_impact = cls._estimate_gamma_impact(trade_type, abs_delta, amount, strike, spot)
        if gamma_impact > 1000000:  # >$1M Gamma 敞口
            signals.append({"type": "warning", "text": f"估计 Gamma 敞口 ${gamma_impact/1e6:.1f}M，可能影响短期价格行为"})
            score["market_maker_adjust"] += 10
        
        # ===== 交叉分析修正 =====
        cls._apply_cross_analysis(trade_type, side, abs_delta, premium, dte, iv, spot, score, reasoning)
        
        # 判定主导意图
        dominant_intent = max(score.items(), key=lambda x: x[1])
        intent_key = dominant_intent[0]
        total_score = sum(v for v in score.values() if v > 0)
        
        # 置信度计算：主导得分占比 + 信号数量加成
        if total_score > 0:
            base_confidence = dominant_intent[1] / total_score * 100
            signal_bonus = min(len(signals) * 5, 15)
            confidence = min(base_confidence + signal_bonus, 98)
        else:
            confidence = 20
        
        # 信号强度分级
        if confidence > 75:
            signal_strength = "STRONG"
        elif confidence > 50:
            signal_strength = "MEDIUM"
        else:
            signal_strength = "WEAK"
        
        intent_info = cls.INTENT_RULES.get(intent_key, {})
        
        return {
            "intent": intent_key,
            "intent_name": intent_info.get("name", "未知"),
            "intent_icon": intent_info.get("icon", "❓"),
            "intent_color": intent_info.get("color", "text-gray-400"),
            "intent_description": intent_info.get("description", ""),
            "intent_risk_level": intent_info.get("risk_level", "MEDIUM"),
            "confidence": round(confidence, 1),
            "signal_strength": signal_strength,
            "reasoning": reasoning[:5],
            "signals": signals,
            "pattern_detected": pattern,
            "score_breakdown": {k: round(v, 1) for k, v in sorted(score.items(), key=lambda x: x[1], reverse=True)},
            "gamma_impact_estimate": round(gamma_impact, 0)
        }
    
    @classmethod
    def _analyze_moneyness(cls, trade_type: str, strike: float, spot: float, 
                           score: Dict, reasoning: List) -> float:
        """Moneyness 维度分析"""
        if spot <= 0 or strike <= 0:
            return 0
        
        moneyness = strike / spot
        
        if trade_type == "P":
            if moneyness > 1.2:  # 深度 ITM Put
                score["institutional_hedging"] += 35
                reasoning.append(f"深度 ITM Put (moneyness={moneyness:.2f})，典型保护性对冲")
            elif moneyness > 1.05:  # 轻度 ITM Put
                score["institutional_hedging"] += 20
                reasoning.append(f"轻度 ITM Put，对冲意图明显")
            elif moneyness > 0.95:  # ATM Put
                score["directional_speculation"] += 15
                score["income_generation"] += 15
                reasoning.append("ATM Put，方向性博弈或收益增强")
            elif moneyness > 0.85:  # 轻度 OTM Put
                score["directional_speculation"] += 25
                reasoning.append(f"轻度 OTM Put (moneyness={moneyness:.2f})，看跌方向押注")
            else:  # 深度 OTM Put
                score["directional_speculation"] += 20
                score["income_generation"] += 10
                reasoning.append(f"深度 OTM Put (moneyness={moneyness:.2f})，尾部风险对冲或投机")
        else:  # Call
            if moneyness < 0.8:  # 深度 ITM Call
                score["arbitrage"] += 25
                reasoning.append(f"深度 ITM Call (moneyness={moneyness:.2f})，合成多头套利")
            elif moneyness < 0.95:  # 轻度 ITM Call
                score["directional_speculation"] += 20
                reasoning.append(f"ITM Call，看涨方向押注")
            elif moneyness > 1.15:  # 深度 OTM Call
                score["directional_speculation"] += 30
                reasoning.append(f"深度 OTM Call (moneyness={moneyness:.2f})，看涨投机/彩票")
            else:  # ATM/轻度 OTM Call
                score["income_generation"] += 20
                score["directional_speculation"] += 10
                reasoning.append("ATM/轻度 OTM Call，收益增强为主")
        
        return moneyness
    
    @classmethod
    def _analyze_delta(cls, trade_type: str, delta: float, abs_delta: float, 
                       amount: float, score: Dict, reasoning: List) -> float:
        """Delta 行为维度分析"""
        if abs_delta > 0.8:
            if trade_type == "P":
                score["income_generation"] += 30
                reasoning.append(f"高 Delta Put ({delta:.2f})，深度实值卖权收权利金")
            else:
                score["directional_speculation"] += 25
                reasoning.append(f"高 Delta Call ({delta:.2f})，强烈看涨方向押注")
        elif abs_delta > 0.5:
            if trade_type == "P":
                score["institutional_hedging"] += 20
                reasoning.append(f"中高 Delta Put ({delta:.2f})，对冲意图")
            else:
                score["directional_speculation"] += 20
                reasoning.append(f"中高 Delta Call ({delta:.2f})，看涨方向")
        elif abs_delta < 0.15:
            score["volatility_play"] += 20
            score["income_generation"] += 15
            reasoning.append(f"极低 Delta ({delta:.2f})，波动率博弈或尾部对冲")
        elif abs_delta < 0.25:
            score["income_generation"] += 20
            reasoning.append(f"低 Delta ({delta:.2f})，时间价值收益策略")
        
        return abs_delta
    
    @classmethod
    def _analyze_volatility(cls, trade_type: str, iv: float, market_iv: Optional[float], 
                            dte: float, score: Dict, reasoning: List, signals: List) -> float:
        """波动率维度分析"""
        if iv <= 0:
            return 0
        
        # 相对波动率分析
        if market_iv and market_iv > 0:
            iv_percentile = (iv - market_iv) / market_iv * 100
            
            if iv_percentile > 30:  # IV 显著高于市场
                score["volatility_play"] += 25
                reasoning.append(f"IV {iv:.0f}% 远高于市场 {market_iv:.0f}% (+{iv_percentile:.0f}%)，波动率博弈")
                signals.append({"type": "warning", "text": f"隐含波动率溢价 +{iv_percentile:.0f}%，市场可能过度定价"})
            elif iv_percentile < -20:  # IV 显著低于市场
                score["arbitrage"] += 20
                score["income_generation"] += 15
                reasoning.append(f"IV {iv:.0f}% 低于市场 {market_iv:.0f}%，可能被低估")
            else:
                score["directional_speculation"] += 10
        else:
            # 绝对波动率分析
            if iv > 80:
                score["volatility_play"] += 20
                reasoning.append(f"超高 IV ({iv:.0f}%)，恐慌情绪下波动率交易")
            elif iv > 60:
                score["income_generation"] += 15
                reasoning.append(f"高 IV ({iv:.0f}%)，卖权收益丰厚")
            elif iv < 30:
                score["directional_speculation"] += 10
                reasoning.append(f"低 IV 环境 ({iv:.0f}%)，方向性交易成本低")
        
        return iv
    
    @classmethod
    def _analyze_size(cls, premium: float, amount: float, spot: float, 
                      score: Dict, reasoning: List, signals: List) -> float:
        """名义价值和规模维度分析"""
        if premium <= 0:
            return 0
        
        # 按 BTC 现价标准化
        btc_equiv = premium / spot if spot > 0 else 0
        
        if premium > 10_000_000 or btc_equiv > 100:  # >$10M 或 >100 BTC
            score["institutional_hedging"] += 30
            reasoning.append(f"巨鲸交易 (${premium/1e6:.1f}M / {btc_equiv:.0f} BTC)，机构行为概率极高")
            signals.append({"type": "danger", "text": f"${premium/1e6:.1f}M 超级大单，需关注后续影响"})
        elif premium > 5_000_000 or btc_equiv > 50:
            score["institutional_hedging"] += 20
            reasoning.append(f"大单 (${premium/1e6:.1f}M)，机构特征明显")
        elif premium > 1_000_000 or btc_equiv > 10:
            score["income_generation"] += 15
            reasoning.append(f"中等大单 (${premium/1e6:.1f}M)，收益增强策略")
        elif premium > 100_000:
            score["directional_speculation"] += 10
            reasoning.append(f"标准大单 (${premium/1e3:.0f}K)")
        
        return premium
    
    @classmethod
    def _analyze_term_structure(cls, dte: float, trade_type: str, 
                                score: Dict, reasoning: List) -> float:
        """期限结构维度分析"""
        if dte <= 0:
            return 0
        
        if dte <= 7:  # 超短期（周内到期）
            score["directional_speculation"] += 25
            score["volatility_play"] += 15
            reasoning.append(f"超短期 ({dte:.0f}DTE)，Gamma 博弈/事件驱动")
        elif dte <= 30:  # 短期
            score["income_generation"] += 20
            score["directional_speculation"] += 15
            reasoning.append(f"短期 ({dte:.0f}DTE)，Theta 衰减加速期")
        elif dte <= 90:  # 中期
            score["institutional_hedging"] += 15
            score["directional_speculation"] += 10
            reasoning.append(f"中期 ({dte:.0f}DTE)，标准对冲/方向期限")
        else:  # 长期（LEAPS）
            score["institutional_hedging"] += 25
            score["directional_speculation"] += 15
            reasoning.append(f"长期 LEAPS ({dte:.0f}DTE)，战略性头寸布局")
        
        return dte
    
    @classmethod
    def _analyze_order_flow(cls, side: str, trade_type: str, delta: float, 
                            amount: float, premium: float, spot: float,
                            score: Dict, reasoning: List, signals: List) -> float:
        """订单流维度分析"""
        # 判断买卖方向
        is_buy = "BUY" in side or "B" in side
        is_sell = "SELL" in side or "S" in side
        
        if not is_buy and not is_sell:
            # 未知方向，根据 Delta 推断
            if delta > 0:
                is_buy = True
            else:
                is_sell = True
        
        if is_buy:
            if trade_type == "P":
                score["directional_speculation"] += 20
                reasoning.append("买入 Put，看跌方向")
            else:
                score["directional_speculation"] += 25
                reasoning.append("买入 Call，看涨方向")
        elif is_sell:
            if trade_type == "P":
                score["income_generation"] += 25
                score["institutional_hedging"] += 10
                reasoning.append("卖出 Put，收取权利金/支撑位布局")
            else:
                score["income_generation"] += 20
                score["volatility_play"] += 10
                reasoning.append("卖出 Call，收取权利金/阻力位布局")
        
        # 订单流失衡信号
        if amount > 200 and premium > spot * 5:  # 大单且名义价值高
            flow_type = "BUY" if is_buy else "SELL"
            signals.append({"type": "warning", "text": f"订单流失衡: {amount:.0f} 张 {flow_type} {trade_type}"})
        
        return 1.0 if (is_buy or is_sell) else 0.0
    
    @classmethod
    def _identify_institutional_pattern(cls, trade_type: str, strike: float, spot: float,
                                        amount: float, premium: float, dte: float, 
                                        iv: float, trade: Dict) -> Optional[str]:
        """识别机构行为模式"""
        if spot <= 0:
            return None
        
        moneyness = strike / spot if spot > 0 else 1
        
        # 大宗分单检测
        if amount > 100 and 50 <= amount <= 300:
            if premium > 500_000:
                return "block_trade_split"
        
        # 扫单检测（跨行权价快速建仓特征）
        sweep_flag = trade.get("sweep", trade.get("is_sweep", False))
        if sweep_flag:
            return "sweep_order"
        
        # 多腿策略检测（如果有 legs 字段）
        legs = trade.get("legs", trade.get("strategy_legs", []))
        if isinstance(legs, list) and len(legs) >= 2:
            return "multi_leg_strategy"
        
        # 滚仓操作检测
        roll_flag = trade.get("is_roll", trade.get("roll_operation", False))
        if roll_flag:
            return "roll_operation"
        
        # 动态对冲检测（高频小额）
        if amount < 50 and dte > 60 and abs(moneyness - 1.0) < 0.05:
            return "delta_hedge"
        
        return None
    
    @classmethod
    def _estimate_gamma_impact(cls, trade_type: str, abs_delta: float, amount: float, 
                               strike: float, spot: float) -> float:
        """估计 Gamma 敞口对市场的潜在影响"""
        if spot <= 0 or strike <= 0 or amount <= 0:
            return 0
        
        # 简化 Gamma 估计：Gamma ≈ (1/S) × N'(d1) / (σ√T)
        # 这里使用 Delta 近似：ATM 附近 Gamma 最大
        moneyness = strike / spot
        delta_distance = abs(abs_delta - 0.5)
        
        # Gamma 峰值在 ATM，远离 ATM 递减
        gamma_factor = max(0, 1 - delta_distance * 2)
        
        # 估计 Gamma 敞口 = 张数 × 合约乘数 × 价格 × Gamma因子
        notional = amount * spot  # 名义价值
        gamma_exposure = notional * gamma_factor * 0.1  # 简化系数
        
        return gamma_exposure
    
    @classmethod
    def _apply_cross_analysis(cls, trade_type: str, side: str, abs_delta: float, 
                              premium: float, dte: float, iv: float, spot: float,
                              score: Dict, reasoning: List):
        """交叉分析修正 - 多维度组合判定"""
        # 场景1: 大额卖出深度 OTM Put + 低 IV → 强烈收益增强信号
        if ("卖出" in " ".join(reasoning) or "SELL" in side) and trade_type == "P":
            if abs_delta < 0.2 and iv < 50 and premium > spot * 3:
                score["income_generation"] += 20
                reasoning.append("交叉确认：大单卖深度 OTM Put + 低 IV → 收益增强")
        
        # 场景2: 买入短期 OTM Call + 高 IV → 事件驱动投机
        if "买入" in " ".join(reasoning) or "BUY" in side:
            if trade_type == "C" and abs_delta < 0.3 and iv > 60 and dte < 14:
                score["volatility_play"] += 15
                reasoning.append("交叉确认：短期 OTM Call + 高 IV → 事件驱动波动率交易")
        
        # 场景3: 长期 ITM Put + 大额 → 机构战略性对冲
        if trade_type == "P" and abs_delta > 0.6 and dte > 90 and premium > spot * 10:
            score["institutional_hedging"] += 15
            reasoning.append("交叉确认：长期深度 ITM Put + 大额 → 战略性对冲")
        
        # 场景4: 短期 ATM Straddle 特征 → 波动率博弈
        if abs(abs_delta - 0.5) < 0.1 and dte < 7:
            score["volatility_play"] += 20
            reasoning.append("交叉确认：短期 ATM → Gamma/波动率博弈")

    @classmethod
    def analyze_market_sentiment(cls, large_trades: List[Dict], spot_price: float, 
                                  market_iv: Optional[float] = None) -> Dict:
        """
        分析市场整体情绪（增强版）
        
        Args:
            large_trades: 大额交易列表
            spot_price: 当前现货价格
            market_iv: 市场隐含波动率（可选）
        
        Returns:
            市场情绪摘要（含深度分析和策略建议）
        """
        if not large_trades:
            return {
                "overall_sentiment": "中性",
                "sentiment_icon": "➖",
                "dominant_intent": {"name": "无数据", "icon": "➖", "color": "text-gray-400"},
                "confidence": 0,
                "intent_distribution": {},
                "key_signals": [],
                "ai_recommendation": "暂无大宗数据，无法分析",
                "market_flow_summary": {"total_trades": 0, "total_premium": 0},
                "risk_warning": []
            }
        
        # 分析每笔交易的意图
        intents = []
        put_count = 0
        call_count = 0
        total_premium = 0
        put_premium = 0
        call_premium = 0
        put_notional = 0
        call_notional = 0
        institutional_count = 0
        strong_signals = []
        
        for trade in large_trades[:50]:
            analysis = cls.analyze_trade_intent(trade, spot_price, market_iv)
            intents.append(analysis)

            trade_type = trade.get("option_type", trade.get("type", "")).upper()
            # 优先使用 notional_usd，如果没有则使用 premium_usd
            notional = trade.get("notional_usd", trade.get("notional", 0)) or 0
            premium = trade.get("premium_usd", trade.get("premium", 0)) or 0
            total_premium += premium

            if trade_type in ("P", "PUT"):
                put_count += 1
                put_premium += premium
                put_notional += notional
            else:
                call_count += 1
                call_premium += premium
                call_notional += notional
            
            if analysis["intent"] == "institutional_hedging":
                institutional_count += 1
            
            # 收集强信号
            if analysis["signal_strength"] == "STRONG":
                strong_signals.append({
                    "intent": analysis["intent_name"],
                    "confidence": analysis["confidence"],
                    "reasoning": analysis["reasoning"][:2],
                    "premium": premium
                })
        
        total_trades = put_count + call_count
        # 使用名义价值计算 Put/Call 占比（与 _flow_analyst 保持一致）
        total_notional = put_notional + call_notional
        put_pct = put_notional / total_notional * 100 if total_notional > 0 else 50
        call_pct = call_notional / total_notional * 100 if total_notional > 0 else 50
        
        # 意图分布统计
        intent_counts = {}
        for intent in intents:
            key = intent["intent"]
            if key not in intent_counts:
                intent_counts[key] = {"count": 0, "total_confidence": 0, "total_premium": 0, "info": intent}
            intent_counts[key]["count"] += 1
            intent_counts[key]["total_confidence"] += intent["confidence"]
            intent_counts[key]["total_premium"] += intent.get("gamma_impact_estimate", 0)
        
        # 计算主导意图
        dominant = max(intent_counts.items(), key=lambda x: x[1]["count"])
        dom_info = dominant[1]["info"]
        avg_confidence = dominant[1]["total_confidence"] / dominant[1]["count"] if dominant[1]["count"] > 0 else 0
        
        # 生成关键信号
        signals = cls._generate_market_signals(put_pct, call_pct, intent_counts, total_trades, institutional_count)
        
        # 风险预警
        risk_warnings = cls._generate_risk_warnings(strong_signals, put_pct, institutional_count, total_premium, spot_price)
        
        # AI 建议
        recommendation = cls._generate_recommendation(dom_info, signals, put_pct, total_premium, intent_counts)
        
        return {
            "overall_sentiment": dom_info["intent_name"],
            "sentiment_icon": dom_info["intent_icon"],
            "dominant_intent": {
                "name": dom_info["intent_name"],
                "icon": dom_info["intent_icon"],
                "color": dom_info["intent_color"],
                "description": dom_info.get("intent_description", ""),
                "risk_level": dom_info.get("intent_risk_level", "MEDIUM")
            },
            "confidence": round(avg_confidence, 1),
            "put_call_ratio": {
                "put_pct": round(put_pct, 1),
                "call_pct": round(call_pct, 1),
                "put_premium": round(put_premium, 0),
                "call_premium": round(call_premium, 0),
                "put_call_premium_ratio": round(put_premium / call_premium, 2) if call_premium > 0 else 0
            },
            "intent_distribution": {
                k: {
                    "count": v["count"],
                    "avg_confidence": round(v["total_confidence"] / v["count"], 1) if v["count"] > 0 else 0,
                    "pct": round(v["count"] / total_trades * 100, 1) if total_trades > 0 else 0,
                    "total_premium": round(v["total_premium"], 0)
                }
                for k, v in sorted(intent_counts.items(), key=lambda x: x[1]["count"], reverse=True)
            },
            "key_signals": signals,
            "strong_signals": strong_signals[:5],
            "risk_warnings": risk_warnings,
            "total_premium": round(total_premium, 0),
            "total_trades_analyzed": len(intents),
            "ai_recommendation": recommendation,
            "market_flow_summary": {
                "total_trades": total_trades,
                "total_premium": round(total_premium, 0),
                "institutional_ratio": round(institutional_count / total_trades * 100, 1) if total_trades > 0 else 0,
                "avg_trade_size": round(total_premium / total_trades, 0) if total_trades > 0 else 0
            }
        }
    
    @classmethod
    def _generate_market_signals(cls, put_pct: float, call_pct: float, intent_counts: Dict, 
                                  total_trades: int, institutional_count: int) -> List[Dict]:
        """生成市场关键信号"""
        signals = []
        
        # Put/Call 情绪信号
        if put_pct > 70:
            signals.append({"type": "danger", "text": f"🔴 极度看跌：Put 占比 {put_pct:.0f}%，恐慌情绪蔓延"})
        elif put_pct > 60:
            signals.append({"type": "warning", "text": f"⚠️ 偏空：Put 占比 {put_pct:.0f}%，看跌情绪占优"})
        elif call_pct > 65:
            signals.append({"type": "success", "text": f"🟢 看涨：Call 占比 {call_pct:.0f}%，看涨情绪强烈"})
        elif call_pct > 55:
            signals.append({"type": "info", "text": f"🔵 偏多：Call 占比 {call_pct:.0f}%，温和看涨"})
        
        # 机构行为信号
        inst_ratio = institutional_count / total_trades * 100 if total_trades > 0 else 0
        if inst_ratio > 40:
            signals.append({"type": "warning", "text": f"🛡️ 机构防御：{institutional_count} 笔对冲交易 ({inst_ratio:.0f}%)，机构在降低风险暴露"})
        elif inst_ratio > 25:
            signals.append({"type": "info", "text": f"📊 机构活跃：{institutional_count} 笔机构相关交易，市场参与者积极调仓"})
        
        # 意图集中度信号
        dominant_count = max(v["count"] for v in intent_counts.values()) if intent_counts else 0
        dominance_ratio = dominant_count / total_trades * 100 if total_trades > 0 else 0
        if dominance_ratio > 60:
            dominant_intent = max(intent_counts.items(), key=lambda x: x[1]["count"])[0]
            intent_name = cls.INTENT_RULES.get(dominant_intent, {}).get("name", dominant_intent)
            signals.append({"type": "warning", "text": f"🎯 意图高度集中：{intent_name} 占比 {dominance_ratio:.0f}%，市场共识强烈"})
        
        # 波动率博弈信号
        vol_count = intent_counts.get("volatility_play", {}).get("count", 0)
        if vol_count > total_trades * 0.3:
            signals.append({"type": "info", "text": f"📊 波动率焦点：{vol_count} 笔波动率交易，市场关注点从方向转向波动"})
        
        # 大单集中度
        spec_count = intent_counts.get("directional_speculation", {}).get("count", 0)
        if spec_count > total_trades * 0.4:
            signals.append({"type": "warning", "text": f"🎯 投机活跃：{spec_count} 笔方向性投机，短期波动可能加剧"})
        
        return signals
    
    @classmethod
    def _generate_risk_warnings(cls, strong_signals: List, put_pct: float, 
                                 institutional_count: int, total_premium: float, spot_price: float) -> List[Dict]:
        """生成风险预警"""
        warnings = []
        
        # 大额集中风险
        large_premium_signals = [s for s in strong_signals if s.get("premium", 0) > 5_000_000]
        if len(large_premium_signals) > 2:
            warnings.append({
                "level": "HIGH",
                "text": f"检测到 {len(large_premium_signals)} 笔 >$5M 大单，需警惕市场冲击"
            })
        
        # 看跌情绪极端
        if put_pct > 75:
            warnings.append({
                "level": "HIGH",
                "text": f"Put 占比 {put_pct:.0f}% 达到极端水平，可能是恐慌性抛售或重大利空预期"
            })
        
        # 机构集体对冲
        if institutional_count > 10:
            warnings.append({
                "level": "MEDIUM",
                "text": f"{institutional_count} 笔机构对冲交易，可能预示机构层面风险事件"
            })
        
        return warnings
    
    @classmethod
    def _generate_recommendation(cls, dominant_intent: Dict, signals: List, 
                                  put_pct: float, total_premium: float, intent_counts: Dict) -> str:
        """生成 AI 策略建议（增强版）"""
        intent = dominant_intent.get("intent", "")
        intent_name = dominant_intent.get("name", "")
        
        recommendations = []
        
        # 基于主导意图的建议
        if intent == "institutional_hedging":
            recommendations.append("机构正在积极对冲风险，建议收紧 Sell Put Delta 暴露")
            recommendations.append("考虑买入保护性 Put 以降低尾部风险")
            if put_pct > 60:
                recommendations.append("Put 买盘旺盛，Sell Put 需等待波动率回落")
        
        elif intent == "directional_speculation":
            if put_pct > 60:
                recommendations.append("看跌投机资金大量入场，避免裸卖 OTM Put")
                recommendations.append("考虑跟随买入 Put 保护现有头寸")
            else:
                recommendations.append("看涨投机资金活跃，可适当 Sell Call 收取时间价值")
                recommendations.append("注意上行风险，设置严格止盈")
        
        elif intent == "income_generation":
            recommendations.append("市场偏好收取权利金，Sell Put/Call 策略当前流行")
            vol_count = intent_counts.get("volatility_play", {}).get("count", 0)
            if vol_count > 5:
                recommendations.append("注意：波动率博弈活跃，IV 可能快速上升")
            recommendations.append("选择高 IV 合约以获取更佳风险收益比")
        
        elif intent == "volatility_play":
            recommendations.append("波动率成为市场焦点，建议关注 Vega 暴露")
            recommendations.append("考虑 Straddle/Strangle 策略捕捉波动")
            recommendations.append("注意：波动率均值回归特性，避免追高 IV")
        
        elif intent == "arbitrage":
            recommendations.append("套利交易活跃，市场定价效率较高")
            recommendations.append("寻找结构性套利机会（Calendar Spread, Box Spread）")
        
        elif intent == "market_maker_adjust":
            recommendations.append("做市商频繁调仓，流动性可能暂时下降")
            recommendations.append("避免大单冲击，采用分单策略")
            recommendations.append("关注 Gamma Flip 位置变化")
        
        # 信号驱动的附加建议
        if put_pct > 70:
            recommendations.append("⚠️ 极度看跌情绪，保持防御性头寸")
        
        total_premium_m = total_premium / 1e6
        if total_premium_m > 50:
            recommendations.append(f"总名义价值 ${total_premium_m:.0f}M，市场活跃度高，注意流动性变化")
        
        if not recommendations:
            return "市场意图分散，暂无明显方向性信号，建议维持现有策略"
        
        return " | ".join(recommendations)
