"""
衍生品市场指标服务 v1.0
基于 Binance Futures API + Deribit API 的衍生品过热检测系统

核心指标:
1. Sharpe Ratio: 风险调整后回报（Binance 日K线）
2. Funding Rate 热度: 资金费率情绪（Binance Futures）
3. 期权 OI 变化: 未平仓合约总量趋势（Deribit）
4. 期货/现货成交量比率: 杠杆热度（Binance）

学术参考:
- Sharpe Ratio < -1: 历史底部信号
- Funding Rate > 0.1%: 多头过热
- Funding Rate < -0.05%: 空头过度
- Futures/Spot Volume > 3: 杠杆过高
"""
import httpx
import logging
import math
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from services.api_retry import request_with_retry

logger = logging.getLogger(__name__)


class DerivativeMetrics:
    """衍生品市场指标服务"""
    
    @classmethod
    def get_all_metrics(cls) -> Dict[str, Any]:
        """获取所有衍生品指标"""
        # 获取 Sharpe Ratio
        sharpe_14d, sharpe_30d = cls._calc_sharp_ratio()
        
        # 获取资金费率
        funding_rate, funding_signal = cls._get_funding_rate()
        
        # 获取期货/现货成交量比率
        vol_ratio, vol_ratio_signal = cls._calc_futures_spot_volume_ratio()
        
        # 获取衍生品过热综合评分
        overheating = cls._assess_derivatives_overheating(
            sharpe_14d=sharpe_14d, sharpe_30d=sharpe_30d,
            funding_rate=funding_rate, vol_ratio=vol_ratio
        )
        
        return {
            "sharpe_ratio_14d": round(sharpe_14d, 2) if sharpe_14d is not None else None,
            "sharpe_ratio_30d": round(sharpe_30d, 2) if sharpe_30d is not None else None,
            "sharpe_signal_14d": cls._interpret_sharpe(sharpe_14d) if sharpe_14d is not None else None,
            "sharpe_signal_30d": cls._interpret_sharpe(sharpe_30d) if sharpe_30d is not None else None,
            "funding_rate": round(funding_rate, 5) if funding_rate is not None else None,
            "funding_rate_pct": round(funding_rate * 100, 3) if funding_rate is not None else None,
            "funding_signal": funding_signal,
            "futures_spot_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
            "futures_spot_signal": vol_ratio_signal,
            "overheating_assessment": overheating,
            "timestamp": datetime.now().isoformat()
        }
    
    @classmethod
    def _calc_sharp_ratio(cls) -> Tuple[Optional[float], Optional[float]]:
        """
        计算 Sharpe Ratio（7天/30天）
        Sharpe = (平均收益 - 无风险利率) / 收益标准差
        
        简化版: 假设无风险利率 = 0
        使用 Binance 日K线数据
        
        学术参考:
        - Sharpe < -1: 极端负值（历史底部信号）
        - -1 ~ 0: 负回报区（可能是底部）
        - 0 ~ 1: 正回报区（正常）
        - > 1: 优异回报（可能过热）
        """
        try:
            # 获取 90 天 K线（用于计算 7天 和 30天 Sharpe）
            resp = request_with_retry(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "1d", "limit": 90},
                timeout=10, verify=False, max_retries=2
            )
            klines = resp.json()
            
            if len(klines) < 30:
                return None, None
            
            # 计算日收益率
            closes = [float(k[4]) for k in klines]
            returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
            
            # 7 天 Sharpe
            returns_14d = returns[-14:]
            sharpe_14d = cls._calc_single_sharpe(returns_14d)
            
            # 30 天 Sharpe
            returns_30d = returns[-30:]
            sharpe_30d = cls._calc_single_sharpe(returns_30d)
            
            return sharpe_14d, sharpe_30d
        except (ValueError, TypeError, ZeroDivisionError, RuntimeError) as e:
            logger.warning("Sharpe Ratio计算失败: %s", e)
        
        return None, None
    
    @classmethod
    def _calc_single_sharpe(cls, returns: List[float]) -> Optional[float]:
        """计算单个 Sharpe Ratio"""
        if not returns or len(returns) < 2:
            return None
        
        # 平均日收益
        avg_return = sum(returns) / len(returns)
        
        # 日收益标准差
        variance = sum((r - avg_return) ** 2 for r in returns) / (len(returns) - 1)
        std_dev = math.sqrt(variance)
        
        if std_dev == 0:
            return 0.0
        
        # Sharpe = (avg_return / std_dev) * sqrt(365)
        sharpe = (avg_return / std_dev) * math.sqrt(365)
        
        return round(sharpe, 2)
    
    @classmethod
    def _interpret_sharpe(cls, sharpe: Optional[float]) -> str:
        """解读 Sharpe Ratio 信号"""
        if sharpe is None:
            return "--"
        if sharpe < -2:
            return "极端负值（历史底部）"
        elif sharpe < -1:
            return "显著负值（底部信号）"
        elif sharpe < 0:
            return "负回报（可能底部）"
        elif sharpe < 1:
            return "正回报（正常）"
        elif sharpe < 2:
            return "优异回报（警惕）"
        else:
            return "极度优异（可能过热）"
    
    @classmethod
    def _get_funding_rate(cls) -> Tuple[Optional[float], Optional[str]]:
        """
        获取资金费率（Funding Rate）
        Binance Futures 每 8 小时结算一次
        
        正常范围: 0.01% (0.0001)
        过热阈值: > 0.1% (0.001) 或 < -0.05% (-0.0005)
        """
        try:
            resp = request_with_retry(
                "https://fapi.binance.com/fapi/v1/premiumIndex",
                params={"symbol": "BTCUSDT"},
                timeout=10, verify=False, max_retries=2
            )
            data = resp.json()
            
            funding_rate = float(data.get("lastFundingRate", 0))
            
            # 信号判定
            if funding_rate > 0.002:
                signal = "极度多头过热（警惕回调）"
            elif funding_rate > 0.001:
                signal = "多头过热（杠杆过高）"
            elif funding_rate > 0.0005:
                signal = "多头偏多（正常偏高）"
            elif funding_rate > 0.0001:
                signal = "轻微多头（正常）"
            elif funding_rate > -0.0001:
                signal = "中性（正常范围）"
            elif funding_rate > -0.0005:
                signal = "轻微空头（正常偏低）"
            elif funding_rate > -0.001:
                signal = "空头偏多（关注反弹）"
            else:
                signal = "极度空头（可能底部）"
            
            return funding_rate, signal
        except (ValueError, TypeError, ZeroDivisionError, RuntimeError) as e:
            logger.warning("资金费率获取失败: %s", e)
        
        return None, None
    
    @classmethod
    def _calc_futures_spot_volume_ratio(cls) -> Tuple[Optional[float], Optional[str]]:
        """
        计算期货/现货成交量比率
        用于衡量市场杠杆热度
        
        数据来源:
        - 期货 24h 成交量: Binance Futures
        - 现货 24h 成交量: Binance Spot
        
        阈值:
        - > 5: 极度杠杆化
        - 3 ~ 5: 杠杆偏高
        - 1.5 ~ 3: 正常
        - < 1.5: 杠杆较低（现货主导）
        """
        try:
            # 现货 24h 成交量
            spot_resp = request_with_retry(
                "https://api.binance.com/api/v3/ticker/24hr",
                params={"symbol": "BTCUSDT"},
                timeout=10, verify=False, max_retries=2
            )
            spot_data = spot_resp.json()
            spot_volume = float(spot_data.get("volume", 0))  # BTC 数量
            
            # 期货 24h 成交量
            futures_resp = request_with_retry(
                "https://fapi.binance.com/fapi/v1/ticker/24hr",
                params={"symbol": "BTCUSDT"},
                timeout=10, verify=False, max_retries=2
            )
            futures_data = futures_resp.json()
            futures_volume = float(futures_data.get("volume", 0))  # BTC 数量
            
            if spot_volume <= 0:
                return None, None
            
            ratio = futures_volume / spot_volume
            
            # 信号判定
            if ratio > 5:
                signal = "极度杠杆化（过热风险）"
            elif ratio > 3:
                signal = "杠杆偏高（警惕）"
            elif ratio > 1.5:
                signal = "正常范围"
            else:
                signal = "现货主导（健康）"
            
            return round(ratio, 2), signal
        except (ValueError, TypeError, ZeroDivisionError, RuntimeError) as e:
            logger.warning("期货/现货比率获取失败: %s", e)
        
        return None, None
    
    @classmethod
    def _assess_derivatives_overheating(cls, sharpe_14d=None, sharpe_30d=None,
                                          funding_rate=None, vol_ratio=None) -> Dict[str, Any]:
        """
        衍生品市场过热综合评估
        
        评分规则:
        - Sharpe < -1: +10（底部信号）
        - Sharpe > 2: -10（过热信号）
        - Funding Rate > 0.001: -10（多头过热）
        - Funding Rate < -0.001: +10（空头过度，可能底部）
        - Vol Ratio > 3: -5（杠杆偏高）
        """
        score = 0
        signals = []
        
        # 1. Sharpe 14d 评分
        if sharpe_14d is not None:
            if sharpe_14d < -1:
                score += 10
                signals.append(("🔴", f"Sharpe 14d={sharpe_14d}（底部）", "bottom"))
            elif sharpe_14d < 0:
                score += 3
                signals.append(("🟡", f"Sharpe 14d={sharpe_14d}（负值）", "neutral"))
            elif sharpe_14d < 2:
                score -= 2
                signals.append(("🟢", f"Sharpe 14d={sharpe_14d}（正常）", "neutral"))
            else:
                score -= 10
                signals.append(("⚠️", f"Sharpe 14d={sharpe_14d}（过热）", "top"))
        
        # 2. Sharpe 30d 评分
        if sharpe_30d is not None:
            if sharpe_30d < -1:
                score += 10
                signals.append(("🔴", f"Sharpe 30d={sharpe_30d}（底部）", "bottom"))
            elif sharpe_30d < 0:
                score += 3
                signals.append(("🟡", f"Sharpe 30d={sharpe_30d}（负值）", "neutral"))
            elif sharpe_30d < 2:
                score -= 2
                signals.append(("🟢", f"Sharpe 30d={sharpe_30d}（正常）", "neutral"))
            else:
                score -= 10
                signals.append(("⚠️", f"Sharpe 30d={sharpe_30d}（过热）", "top"))
        
        # 3. 资金费率评分
        if funding_rate is not None:
            if funding_rate > 0.002:
                score -= 10
                signals.append(("⚠️", "资金费率极度多头", "top"))
            elif funding_rate > 0.001:
                score -= 7
                signals.append(("⚠️", "资金费率过热", "top"))
            elif funding_rate < -0.001:
                score += 8
                signals.append(("🔴", "资金费率极度空头（底部）", "bottom"))
            elif funding_rate < -0.0005:
                score += 5
                signals.append(("🟡", "资金费率偏空", "bottom"))
            else:
                score -= 1
                signals.append(("🟢", "资金费率正常", "neutral"))
        
        # 4. 期货/现货比率评分
        if vol_ratio is not None:
            if vol_ratio > 5:
                score -= 5
                signals.append(("⚠️", "杠杆极度偏高", "top"))
            elif vol_ratio > 3:
                score -= 3
                signals.append(("⚠️", "杠杆偏高", "top"))
            elif vol_ratio > 1.5:
                score -= 1
                signals.append(("🟢", "杠杆正常", "neutral"))
            else:
                score += 3
                signals.append(("🟢", "现货主导（健康）", "neutral"))
        
        # 综合判定
        if score >= 15:
            level = "STRONG_BOTTOM"
            name = "衍生品底部信号"
            icon = "🔴"
            color = "text-red-400"
            advice = "衍生品市场显示底部特征，关注做多机会"
        elif score >= 5:
            level = "BOTTOM"
            name = "潜在底部"
            icon = "🟡"
            color = "text-yellow-400"
            advice = "衍生品指标偏负面，可能接近底部"
        elif score >= -5:
            level = "NEUTRAL"
            name = "中性"
            icon = "⚪"
            color = "text-gray-400"
            advice = "衍生品市场处于正常状态"
        elif score >= -15:
            level = "OVERHEATED"
            name = "过热警告"
            icon = "⚠️"
            color = "text-orange-400"
            advice = "衍生品过热，注意风险，降低杠杆"
        else:
            level = "EXTREME_OVERHEAT"
            name = "极度过热"
            icon = "🔴"
            color = "text-red-400"
            advice = "衍生品极度过热，立即降低仓位暴露"
        
        return {
            "score": score,
            "level": level,
            "name": name,
            "icon": icon,
            "color": color,
            "advice": advice,
            "signals": signals
        }
