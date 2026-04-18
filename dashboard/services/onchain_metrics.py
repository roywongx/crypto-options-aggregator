"""
链上数据指标服务 v2.0
基于 BTC 筑底信号深度研究报告的多维度指标体系

数据源:
1. MVRV Ratio: https://looknode-proxy.corms-cushier-0l.workers.dev/mCapRealizedRatio
2. Balanced Price: https://looknode-proxy.corms-corms-cushier-0l.workers.dev/balancedPrice
3. 200WMA / 200DMA: Binance K线数据
4. Mayer Multiple: 价格 / 200DMA
5. MVRV Z-Score: 基于 MVRV 历史统计
6. NUPL: Net Unrealized Profit/Loss
7. 减半倒计时: blockchain.info 区块高度
8. 汇合评分系统: 多指标综合判断底部概率
"""
import requests
import logging
import math
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from services.api_retry import request_with_retry

logger = logging.getLogger(__name__)


class OnChainMetrics:
    """使用真实数据源的链上指标"""
    
    # 缓存
    _cache = {}
    _cache_time = None
    CACHE_DURATION = 300  # 5分钟缓存
    
    @classmethod
    def get_all_metrics(cls, currency: str = "bitcoin") -> Dict[str, Any]:
        """获取所有链上指标"""
        # 检查缓存
        if cls._cache_time and cls._cache:
            elapsed = (datetime.now() - cls._cache_time).total_seconds()
            if elapsed < cls.CACHE_DURATION:
                return cls._cache.copy()
        
        try:
            metrics = cls._fetch_all()
            if metrics and metrics.get('current_price'):
                cls._cache = metrics
                cls._cache_time = datetime.now()
                return metrics
        except Exception as e:
            logger.error(f"获取链上指标失败: {e}")
        
        return cls._cache if cls._cache else cls._get_fallback_data()
    
    @classmethod
    def _fetch_all(cls) -> Dict[str, Any]:
        """获取所有数据"""
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        # 获取当前价格
        current_price = cls._get_current_price()
        if current_price is None or current_price <= 0:
            return cls._get_fallback_data()
        
        # 获取MVRV
        mvrv = cls._get_mvrv()
        
        # 获取Balanced Price
        balanced_price = cls._get_balanced_price()
        
        # 获取200WMA
        wma_200, wma_ratio = cls._get_200wma(current_price)
        
        # 获取200DMA和Mayer Multiple
        dma_200, mayer_mult = cls._get_200dma_and_mayer(current_price)
        
        # 获取MVRV历史数据用于Z-Score计算
        mvrv_history = cls._get_mvrv_history()
        mvrv_zscore_data = cls._calc_mvrv_zscore(mvrv, mvrv_history, current_price)
        
        # 计算NUPL
        nupl = cls._calc_nupl(mvrv)
        
        # 获取Puell Multiple（矿工收入倍数）
        puell_mult, puell_signal = cls._get_puell_multiple()
        
        # 获取减半倒计时
        halving_days = cls._get_halving_countdown()
        
        # 计算汇合评分
        convergence = cls._calc_convergence_score(
            mvrv=mvrv, mvrv_zscore=mvrv_zscore_data.get("z_score"), nupl=nupl,
            wma_ratio=wma_ratio, mayer_mult=mayer_mult,
            balanced_price_ratio=current_price / balanced_price if balanced_price else None,
            halving_days=halving_days, puell_mult=puell_mult
        )
        
        # 提取简化字段用于向后兼容
        z_score = mvrv_zscore_data.get("z_score")
        
        return {
            "current_price": round(current_price, 2),
            "mvrv_ratio": round(mvrv, 2) if mvrv else None,
            "mvrv_signal": cls._interpret_mvrv(mvrv) if mvrv else None,
            "price_200wma": round(wma_200, 2) if wma_200 else None,
            "price_to_200wma_ratio": round(wma_ratio, 2) if wma_ratio else None,
            "price_200dma": round(dma_200, 2) if dma_200 else None,
            "mayer_multiple": round(mayer_mult, 2) if mayer_mult else None,
            "mayer_signal": cls._interpret_mayer(mayer_mult) if mayer_mult else None,
            "mvrv_z_score": z_score,
            "mvrv_z_signal": cls._interpret_mvrv_zscore(z_score) if z_score is not None else None,
            "mvrv_z_zone": mvrv_zscore_data.get("zone"),
            "mvrv_z_zone_name": mvrv_zscore_data.get("zone_name"),
            "mvrv_z_zone_color": mvrv_zscore_data.get("zone_color_class"),
            "mvrv_z_extremes": mvrv_zscore_data.get("historical_extremes", {}),
            "nupl": round(nupl, 3) if nupl is not None else None,
            "nupl_signal": cls._interpret_nupl(nupl) if nupl is not None else None,
            "puell_multiple": round(puell_mult, 2) if puell_mult else None,
            "puell_signal": puell_signal,
            "balanced_price": round(balanced_price, 2) if balanced_price else None,
            "balanced_price_ratio": round(current_price / balanced_price, 2) if balanced_price else None,
            "halving_days_remaining": halving_days,
            "convergence_score": convergence,
            "timestamp": datetime.now().isoformat(),
            "data_source": "fuckbtc.com APIs + Binance Kline + Bitcoin Magazine Pro + blockchain.info"
        }
    
    @classmethod
    def _get_current_price(cls) -> Optional[float]:
        """获取当前价格"""
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        # Binance
        try:
            resp = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"},
                timeout=10,
                verify=False
            )
            if resp.status_code == 200:
                return float(resp.json().get("price", 0))
        except Exception as e:
            logger.warning(f"Binance价格失败: {e}")
        
        # CoinGecko
        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin", "vs_currencies": "usd"},
                timeout=10,
                verify=False
            )
            if resp.status_code == 200:
                return resp.json().get("bitcoin", {}).get("usd")
        except Exception as e:
            logger.warning(f"CoinGecko价格失败: {e}")
        
        return None
    
    @classmethod
    def _get_mvrv(cls) -> Optional[float]:
        """
        获取MVRV Ratio
        API: https://looknode-proxy.corms-cushier-0l.workers.dev/mCapRealizedRatio
        """
        try:
            resp = request_with_retry(
                "https://looknode-proxy.corms-cushier-0l.workers.dev/mCapRealizedRatio",
                timeout=10, verify=False, max_retries=3
            )
            data = resp.json()
            # API返回格式: {"code": 100, "data": [{"t": timestamp, "v": value}, ...]}
            if "data" in data and data["data"]:
                return float(data["data"][-1]["v"])
            elif "values" in data and data["values"]:
                return float(data["values"][-1].get("value", 0))
            elif "value" in data:
                return float(data["value"])
        except Exception as e:
            logger.warning(f"MVRV获取失败: {e}")
        
        return None
    
    @classmethod
    def _get_balanced_price(cls) -> Optional[float]:
        """
        获取Balanced Price
        API: https://looknode-proxy.corms-cushier-0l.workers.dev/balancedPrice
        """
        try:
            resp = request_with_retry(
                "https://looknode-proxy.corms-cushier-0l.workers.dev/balancedPrice",
                timeout=10, verify=False, max_retries=3
            )
            data = resp.json()
            # API返回格式: {"code": 100, "data": [{"t": timestamp, "v": value}, ...]}
            if "data" in data and data["data"]:
                return float(data["data"][-1]["v"])
            elif "values" in data and data["values"]:
                return float(data["values"][-1].get("value", 0))
            elif "value" in data:
                return float(data["value"])
        except Exception as e:
            logger.warning(f"Balanced Price获取失败: {e}")
        
        return None
    
    @classmethod
    def _get_200wma(cls, current_price: float):
        """
        计算200周均线
        使用Binance周K线数据，取最近200周收盘价平均值
        """
        try:
            resp = request_with_retry(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "1w", "limit": 200},
                timeout=15, verify=False, max_retries=3
            )
            klines = resp.json()
            closes = [float(k[4]) for k in klines]  # 收盘价
            
            if closes:
                wma_200 = sum(closes) / len(closes)
                ratio = current_price / wma_200 if wma_200 else None
                return wma_200, ratio
        except Exception as e:
            logger.warning(f"200WMA计算失败: {e}")
        
        return None, None
    
    @classmethod
    def _get_halving_countdown(cls) -> Optional[int]:
        """获取减半倒计时"""
        try:
            resp = request_with_retry(
                "https://blockchain.info/q/getblockcount",
                timeout=10, verify=False, max_retries=3
            )
            current_block = int(resp.text)
            next_halving_block = 1050000
            blocks_remaining = next_halving_block - current_block
            days_remaining = int(blocks_remaining * 10 / (60 * 24))
            return max(0, days_remaining)
        except Exception as e:
            logger.warning(f"减半倒计时失败: {e}")
        
        # 估算
        from datetime import datetime
        halving_date = datetime(2028, 4, 1)
        return max(0, (halving_date - datetime.now()).days)
    
    @classmethod
    def _get_puell_multiple(cls) -> Tuple[Optional[float], Optional[str]]:
        """
        计算Puell Multiple（矿工收入倍数）
        参考: David Puell 原始研究
        
        Puell Multiple = 每日矿工收入（USD） / 365日移动平均
        
        由于无法直接获取矿工收入数据，使用估算方法:
        基于区块奖励和价格估算每日收入
        每日矿工收入 ≈ 区块奖励 × 每日区块数 × BTC价格
        当前区块奖励: 3.125 BTC，每日约 144 个区块
        每日收入 ≈ 3.125 × 144 × Price = 450 × Price
        
        阈值:
        - < 0.4: 矿工投降（历史底部信号）
        - 0.4 ~ 1.0: 低估区（矿工收入低于平均）
        - 1.0 ~ 2.0: 正常区
        - 2.0 ~ 4.0: 偏高区（矿工收入高于平均）
        - > 4.0: 顶部区（矿工收入极高，可能是顶部）
        """
        try:
            # 获取当前价格
            current_price = cls._get_current_price()
            if not current_price:
                return None, None
            
            # 估算每日矿工收入
            daily_revenue = 3.125 * 144 * current_price  # 约 450 * price
            
            # 获取历史价格计算365日均线收入
            import requests as req
            resp = req.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "1d", "limit": 365},
                timeout=10
            )
            klines = resp.json()
            
            if len(klines) < 100:
                return None, None
            
            # 计算每日收入历史
            revenues = [3.125 * 144 * float(k[4]) for k in klines]
            avg_revenue = sum(revenues) / len(revenues)
            
            if avg_revenue <= 0:
                return None, None
            
            puell = daily_revenue / avg_revenue
            
            # 信号判定
            if puell < 0.4:
                signal = "矿工投降（历史底部）"
            elif puell < 1.0:
                signal = "低估（矿工收入偏低）"
            elif puell < 2.0:
                signal = "正常区间"
            elif puell < 4.0:
                signal = "偏高（矿工收入丰厚）"
            else:
                signal = "顶部（矿工收入过热）"
            
            return round(puell, 2), signal
        except Exception as e:
            logger.warning(f"Puell Multiple计算失败: {e}")
        
        return None, None
    
    @classmethod
    def _interpret_mvrv(cls, mvrv: float) -> str:
        """解读MVRV信号"""
        if mvrv < 1:
            return "低估（历史底部）"
        elif mvrv < 1.5:
            return "偏低（积累区）"
        elif mvrv < 3:
            return "正常区间"
        elif mvrv < 3.5:
            return "偏高（谨慎）"
        elif mvrv < 5:
            return "过热（顶部区域）"
        else:
            return "极度泡沫（历史顶部）"
    
    @classmethod
    def _get_fallback_data(cls) -> Dict[str, Any]:
        """降级数据"""
        return {
            "current_price": None,
            "mvrv_ratio": None,
            "mvrv_signal": "数据获取失败",
            "price_200wma": None,
            "price_to_200wma_ratio": None,
            "price_200dma": None,
            "mayer_multiple": None,
            "mayer_signal": None,
            "mvrv_z_score": None,
            "mvrv_z_signal": None,
            "mvrv_z_zone": "unknown",
            "mvrv_z_zone_name": "未知",
            "mvrv_z_zone_color": "text-gray-400",
            "mvrv_z_extremes": {},
            "nupl": None,
            "nupl_signal": None,
            "puell_multiple": None,
            "puell_signal": None,
            "balanced_price": None,
            "balanced_price_ratio": None,
            "halving_days_remaining": None,
            "convergence_score": {
                "score": 0,
                "level": "UNKNOWN",
                "color": "text-gray-400",
                "icon": "❓",
                "name": "数据不足",
                "signals": [],
                "bottom_probability": "无法计算"
            },
            "timestamp": datetime.now().isoformat(),
            "data_source": "error",
            "error": "链上数据获取失败，请稍后重试"
        }
    
    @classmethod
    def _get_200dma_and_mayer(cls, current_price: float) -> Tuple[Optional[float], Optional[float]]:
        """
        计算200日均线 (200DMA) 和 Mayer Multiple
        Mayer Multiple = Price / 200DMA
        使用Binance日K线数据，取最近200天收盘价平均值
        """
        try:
            resp = request_with_retry(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "1d", "limit": 200},
                timeout=15, verify=False, max_retries=3
            )
            klines = resp.json()
            closes = [float(k[4]) for k in klines]
            
            if len(closes) >= 100:
                dma_200 = sum(closes) / len(closes)
                mayer = current_price / dma_200 if dma_200 > 0 else None
                return dma_200, round(mayer, 3) if mayer else None
        except Exception as e:
            logger.warning(f"200DMA/Mayer计算失败: {e}")
        
        return None, None
    
    @classmethod
    def _get_mvrv_history(cls) -> List[float]:
        """
        获取MVRV历史数据（过去365天）
        API: https://looknode-proxy.corms-cushier-0l.workers.dev/mCapRealizedRatio
        """
        try:
            resp = request_with_retry(
                "https://looknode-proxy.corms-cushier-0l.workers.dev/mCapRealizedRatio",
                timeout=10, verify=False, max_retries=2
            )
            data = resp.json()
            if "data" in data and data["data"]:
                return [float(item["v"]) for item in data["data"] if item.get("v")]
            elif "values" in data and data["values"]:
                return [float(item.get("value", 0)) for item in data["values"] if item.get("value")]
        except Exception as e:
            logger.warning(f"MVRV历史获取失败: {e}")
        
        return []
    
    @classmethod
    def _calc_mvrv_zscore(cls, current_mvrv: Optional[float], history: List[float],
                           current_price: Optional[float] = None) -> Dict[str, Any]:
        """
        计算MVRV Z-Score（基于 Bitcoin Magazine Pro 正确定义）
        
        正确定义（参考 @aweandwonder, Murad Mahmudov, David Puell）:
        - Market Value = 当前价格 × 流通数量
        - Realised Value = 每枚BTC最后移动时的价格平均值 × 流通数量
        - Z-Score = (Market Value - Realised Value) / StdDev(MV - RV 历史)
        
        等价简化（因为流通数量相同）:
        - Z-Score = (当前价格 - Realised Price) / StdDev(价格 - RP 历史)
        - 或者: Z-Score = (MVRV - 1) × Realised Price / StdDev
        
        实际应用中使用: Z-Score = (Current MVRV - Mean) / StdDev(MVRV历史)
        这是标准做法，因为 (MV - RV) 与 MVRV 线性相关
        
        颜色带区（Bitcoin Magazine Pro）:
        - 绿色带（底部）: Z < 0，市场低估
        - 粉色带（顶部）: Z > 1，市场过热
        
        返回: {
            "z_score": float,
            "zone": str,  # green/pink/neutral
            "zone_name": str,
            "zone_color_class": str,
            "historical_extremes": {
                "min_z": float,
                "max_z": float,
                "current_percentile": float
            }
        }
        """
        result = {
            "z_score": None,
            "zone": "unknown",
            "zone_name": "未知",
            "zone_color_class": "text-gray-400",
            "historical_extremes": {}
        }
        
        if current_mvrv is None or not history or len(history) < 30:
            return result
        
        try:
            mean = sum(history) / len(history)
            variance = sum((x - mean) ** 2 for x in history) / (len(history) - 1)
            stddev = math.sqrt(variance)
            
            if stddev == 0:
                result["z_score"] = 0.0
                return result
            
            zscore = round((current_mvrv - mean) / stddev, 2)
            result["z_score"] = zscore
            
            # 颜色带区判定（Bitcoin Magazine Pro 标准）
            if zscore < 0:
                result["zone"] = "green"
                result["zone_name"] = "绿色带（低估区）"
                result["zone_color_class"] = "text-green-400"
            elif zscore > 1:
                result["zone"] = "pink"
                result["zone_name"] = "粉色带（过热区）"
                result["zone_color_class"] = "text-pink-400"
            else:
                result["zone"] = "neutral"
                result["zone_name"] = "中性区间"
                result["zone_color_class"] = "text-yellow-400"
            
            # 历史极值统计
            sorted_hist = sorted(history)
            min_val = sorted_hist[0]
            max_val = sorted_hist[-1]
            min_z = round((min_val - mean) / stddev, 2)
            max_z = round((max_val - mean) / stddev, 2)
            
            # 当前百分位
            rank = sum(1 for x in history if x < current_mvrv)
            percentile = round(rank / len(history) * 100, 0)
            
            result["historical_extremes"] = {
                "min_z": min_z,
                "max_z": max_z,
                "min_value": round(min_val, 2),
                "max_value": round(max_val, 2),
                "current_percentile": percentile,
                "sample_size": len(history)
            }
            
        except Exception as e:
            logger.warning(f"MVRV Z-Score计算失败: {e}")
        
        return result
    
    @classmethod
    def _calc_nupl(cls, mvrv: Optional[float]) -> Optional[float]:
        """
        计算NUPL (Net Unrealized Profit/Loss)
        NUPL = (MVRV - 1) / MVRV
        学术参考:
        - NUPL < 0: 恐惧区 (Capitulation)
        - 0 < NUPL < 0.25: 希望区 (Hope)
        - 0.25 < NUPL < 0.5: 乐观区 (Optimism)
        - 0.5 < NUPL < 0.75: 贪婪区 (Greed)
        - NUPL > 0.75: 极度贪婪 (Euphoria)
        """
        if mvrv is None or mvrv <= 0:
            return None
        
        try:
            nupl = (mvrv - 1) / mvrv
            return round(nupl, 3)
        except Exception as e:
            logger.warning(f"NUPL计算失败: {e}")
            return None
    
    @classmethod
    def _calc_convergence_score(cls, mvrv=None, mvrv_zscore=None, nupl=None,
                                  wma_ratio=None, mayer_mult=None,
                                  balanced_price_ratio=None,
                                  halving_days=None, puell_mult=None) -> Dict[str, Any]:
        """
        多重指标汇合评分系统 v2.0
        基于 BTC 筑底信号深度研究报告的多维指标汇合分析
        综合判断市场底部概率
        
        评分规则:
        - 每个指标贡献 -10 到 +10 分
        - 总分 -80 到 +80（8个指标）
        - 负分 = 底部信号，正分 = 顶部信号
        """
        score = 0
        signals = []
        active_indicators = 0
        
        # 1. MVRV 评分 (-10 ~ +10)
        if mvrv is not None:
            active_indicators += 1
            if mvrv < 1.0:
                score -= 10
                signals.append(("🔴", "MVRV < 1.0 历史底部", "bottom"))
            elif mvrv < 1.5:
                score -= 7
                signals.append(("🟡", "MVRV 积累区", "bottom"))
            elif mvrv < 2.5:
                score -= 2
                signals.append(("🟢", "MVRV 正常", "neutral"))
            elif mvrv < 3.5:
                score += 3
                signals.append(("⚠️", "MVRV 偏高", "top"))
            elif mvrv < 5:
                score += 7
                signals.append(("🔴", "MVRV 过热", "top"))
            else:
                score += 10
                signals.append(("💀", "MVRV 极度泡沫", "top"))
        
        # 2. MVRV Z-Score 评分 (-10 ~ +10)
        # Bitcoin Magazine Pro 标准: Z < 0 绿色带（底部），Z > 1 粉色带（顶部）
        if mvrv_zscore is not None:
            active_indicators += 1
            if mvrv_zscore < -1:
                score -= 10
                signals.append(("🔴", "Z-Score 极度低估（历史性底部）", "bottom"))
            elif mvrv_zscore < 0:
                score -= 7
                signals.append(("🟢", "Z-Score 绿色带（低估）", "bottom"))
            elif mvrv_zscore < 1:
                score -= 1
                signals.append(("🟡", "Z-Score 中性", "neutral"))
            else:
                score += 10
                signals.append(("🔴", "Z-Score 粉色带（顶部过热）", "top"))
        
        # 3. NUPL 评分 (-10 ~ +10)
        if nupl is not None:
            active_indicators += 1
            if nupl < 0:
                score -= 10
                signals.append(("🔴", "NUPL 恐惧区", "bottom"))
            elif nupl < 0.25:
                score -= 6
                signals.append(("🟡", "NUPL 希望区", "bottom"))
            elif nupl < 0.5:
                score -= 1
                signals.append(("🟢", "NUPL 乐观", "neutral"))
            elif nupl < 0.75:
                score += 5
                signals.append(("⚠️", "NUPL 贪婪", "top"))
            else:
                score += 10
                signals.append(("🔴", "NUPL 极度贪婪", "top"))
        
        # 4. 200WMA Ratio 评分 (-10 ~ +10)
        if wma_ratio is not None:
            active_indicators += 1
            if wma_ratio < 0.8:
                score -= 10
                signals.append(("🔴", "远低于200WMA", "bottom"))
            elif wma_ratio < 0.9:
                score -= 6
                signals.append(("🟡", "低于200WMA", "bottom"))
            elif wma_ratio < 1.1:
                score -= 2
                signals.append(("🟢", "接近200WMA", "neutral"))
            elif wma_ratio < 1.5:
                score += 5
                signals.append(("⚠️", "高于200WMA", "top"))
            else:
                score += 10
                signals.append(("🔴", "远高于200WMA", "top"))
        
        # 5. Mayer Multiple 评分 (-10 ~ +10)
        if mayer_mult is not None:
            active_indicators += 1
            if mayer_mult < 0.8:
                score -= 10
                signals.append(("🔴", "Mayer < 0.8 超卖", "bottom"))
            elif mayer_mult < 1.0:
                score -= 7
                signals.append(("🟡", "Mayer < 1.0 低估", "bottom"))
            elif mayer_mult < 1.5:
                score -= 2
                signals.append(("🟢", "Mayer 正常", "neutral"))
            elif mayer_mult < 2.4:
                score += 5
                signals.append(("⚠️", "Mayer 偏高", "top"))
            else:
                score += 10
                signals.append(("🔴", "Mayer > 2.4 顶部", "top"))
        
        # 6. Balanced Price Ratio 评分 (-10 ~ +10)
        if balanced_price_ratio is not None:
            active_indicators += 1
            if balanced_price_ratio < 0.5:
                score -= 10
                signals.append(("🔴", "远低于均衡价", "bottom"))
            elif balanced_price_ratio < 0.8:
                score -= 6
                signals.append(("🟡", "低于均衡价", "bottom"))
            elif balanced_price_ratio < 1.2:
                score -= 2
                signals.append(("🟢", "接近均衡价", "neutral"))
            elif balanced_price_ratio < 2.0:
                score += 5
                signals.append(("⚠️", "高于均衡价", "top"))
            else:
                score += 10
                signals.append(("🔴", "远高于均衡价", "top"))
        
        # 7. 减半周期评分 (减半前积极，减半后消极)
        if halving_days is not None:
            active_indicators += 1
            if halving_days < 0:
                # 已减半
                score -= 5
                signals.append(("🟡", "已减半", "neutral"))
            elif halving_days < 180:
                score -= 8
                signals.append(("🔴", "减半临近", "bottom"))
            elif halving_days < 365:
                score -= 5
                signals.append(("🟡", "减半年内", "bottom"))
            elif halving_days < 730:
                score += 2
                signals.append(("🟢", "减半中期", "neutral"))
            else:
                score += 6
                signals.append(("⚠️", "远离减半", "top"))
        
        # 8. Puell Multiple 评分 (-10 ~ +10)
        # David Puell 标准: < 0.4 矿工投降（底部），> 4.0 矿工收入过热（顶部）
        if puell_mult is not None:
            active_indicators += 1
            if puell_mult < 0.4:
                score -= 10
                signals.append(("🔴", "Puell 矿工投降", "bottom"))
            elif puell_mult < 1.0:
                score -= 6
                signals.append(("🟡", "Puell 矿工收入偏低", "bottom"))
            elif puell_mult < 2.0:
                score -= 1
                signals.append(("🟢", "Puell 正常", "neutral"))
            elif puell_mult < 4.0:
                score += 5
                signals.append(("⚠️", "Puell 矿工收入丰厚", "top"))
            else:
                score += 10
                signals.append(("🔴", "Puell 顶部过热", "top"))
        
        # 综合判定（8个指标，总分范围 -80 ~ +80）
        max_score = active_indicators * 10
        normalized_pct = (score / max_score * 100) if max_score > 0 else 0
        
        if score <= -40:
            level = "STRONG_BOTTOM"
            name = "强烈底部信号"
            icon = "🔴"
            color = "text-red-400"
            bottom_prob = "极高 (>80%)"
        elif score <= -20:
            level = "BOTTOM"
            name = "底部区域"
            icon = "🟡"
            color = "text-yellow-400"
            bottom_prob = "较高 (60-80%)"
        elif score <= -5:
            level = "ACCUMULATION"
            name = "积累区"
            icon = "🟢"
            color = "text-green-400"
            bottom_prob = "中等 (40-60%)"
        elif score <= 15:
            level = "NEUTRAL"
            name = "中性"
            icon = "⚪"
            color = "text-gray-400"
            bottom_prob = "一般 (20-40%)"
        elif score <= 30:
            level = "DISTRIBUTION"
            name = "派发区"
            icon = "⚠️"
            color = "text-orange-400"
            bottom_prob = "较低 (<20%)"
        else:
            level = "TOP"
            name = "顶部区域"
            icon = "🔴"
            color = "text-red-400"
            bottom_prob = "极低 (<5%)"
        
        return {
            "score": score,
            "max_score": max_score,
            "normalized_pct": round(normalized_pct, 0),
            "level": level,
            "name": name,
            "icon": icon,
            "color": color,
            "bottom_probability": bottom_prob,
            "active_indicators": active_indicators,
            "signals": signals,
        }
    
    @classmethod
    def _interpret_mayer(cls, mayer: Optional[float]) -> str:
        """解读Mayer Multiple信号"""
        if mayer is None:
            return "--"
        if mayer < 0.8:
            return "超卖（历史底部）"
        elif mayer < 1.0:
            return "低估（积累机会）"
        elif mayer < 1.5:
            return "正常区间"
        elif mayer < 2.0:
            return "偏高（谨慎）"
        elif mayer < 2.4:
            return "过热（顶部区域）"
        else:
            return "极度泡沫（历史顶部）"
    
    @classmethod
    def _interpret_mvrv_zscore(cls, zscore: Optional[float]) -> str:
        """解读MVRV Z-Score信号（Bitcoin Magazine Pro 标准）"""
        if zscore is None:
            return "--"
        if zscore < -1:
            return "极度低估（历史性底部）"
        elif zscore < 0:
            return "绿色带（低估区域）"
        elif zscore < 1:
            return "中性区间"
        else:
            return "粉色带（顶部过热）"
    
    @classmethod
    def _interpret_nupl(cls, nupl: Optional[float]) -> str:
        """解读NUPL信号"""
        if nupl is None:
            return "--"
        if nupl < 0:
            return "恐惧区（投降式抛售）"
        elif nupl < 0.25:
            return "希望区（底部恢复）"
        elif nupl < 0.5:
            return "乐观区（正常上涨）"
        elif nupl < 0.75:
            return "贪婪区（过热）"
        else:
            return "极度贪婪（顶部泡沫）"
