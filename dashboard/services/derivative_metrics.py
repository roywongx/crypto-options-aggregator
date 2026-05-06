"""
加密原生衍生品指标服务 v2.0
基于 Binance Futures/Spot API 的衍生品市场分析系统

核心指标（8个）:
1. 永续基差 (Perp Basis): 年化资金成本
2. OI-价格背离 (OI-Price Divergence): 量价关系
3. 资金费率波动率 (Funding Rate Volatility): 情绪稳定性
4. 清算热力等级 (Liquidation Heat): 加密特有"痛苦指数"
5. 稳定币交易所储备 (Stablecoin Exchange Reserve): 买盘火力
6. 期货/现货成交量比 (Futures/Spot Ratio): 保留但重校准阈值
7. Sharpe Ratio: 保留原有逻辑
8. 综合过热评估: 加密原生评分
"""
import hashlib
import hmac
import math
import time
import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
from urllib.parse import urlencode

from services.api_retry import request_with_retry
from services.crypto_thresholds import CryptoThresholds
from services.perp_basis_analyzer import PerpBasisAnalyzer
from db.connection import execute_read, execute_write
from config import config

logger = logging.getLogger(__name__)


class DerivativeMetrics:
    """加密原生衍生品市场指标服务"""

    # ============================================================
    # 指标 1: 永续基差
    # ============================================================

    @classmethod
    def _get_perp_basis(cls, currency: str = "BTC") -> Dict[str, Any]:
        try:
            return PerpBasisAnalyzer.analyze(currency)
        except Exception as e:
            logger.warning("Perp basis failed: %s", e)
            return {"error": str(e), "basis_annualized": 0, "perp_price": 0, "spot_price": 0}

    # ============================================================
    # 指标 2: OI-价格背离
    # ============================================================

    @classmethod
    def _get_oi_price_divergence(cls, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        try:
            oi_resp = request_with_retry(
                "https://fapi.binance.com/fapi/v1/openInterest",
                params={"symbol": symbol},
                timeout=10, verify=False, max_retries=2
            )
            current_oi = float(oi_resp.json().get("openInterest", 0))

            price_resp = request_with_retry(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": symbol},
                timeout=10, verify=False, max_retries=2
            )
            current_price = float(price_resp.json().get("price", 0))

            rows = execute_read(
                "SELECT open_interest_usd, price FROM oi_history "
                "WHERE currency='BTC' ORDER BY timestamp DESC LIMIT 1"
            )

            oi_24h_ago = current_oi
            price_24h_ago = current_price
            if rows:
                oi_24h_ago = rows[0]["open_interest_usd"] or current_oi
                price_24h_ago = rows[0]["price"] or current_price

            oi_change_pct = ((current_oi - oi_24h_ago) / oi_24h_ago * 100) if oi_24h_ago > 0 else 0
            price_change_pct = ((current_price - price_24h_ago) / price_24h_ago * 100) if price_24h_ago > 0 else 0

            execute_write(
                "INSERT INTO oi_history (timestamp, currency, open_interest_usd, price, oi_change_24h_pct, price_change_24h_pct) "
                "VALUES (?, 'BTC', ?, ?, ?, ?)",
                (datetime.now().isoformat(), current_oi, current_price,
                 round(oi_change_pct, 2), round(price_change_pct, 2))
            )

            oi_direction = "flat"
            price_direction = "flat"
            if oi_change_pct > 1.5:
                oi_direction = "up"
            elif oi_change_pct < -1.5:
                oi_direction = "down"
            if price_change_pct > 0.5:
                price_direction = "up"
            elif price_change_pct < -0.5:
                price_direction = "down"

            divergence = "none"
            if oi_direction == "up" and price_direction == "down":
                divergence = "bearish"
            elif oi_direction == "up" and price_direction == "flat":
                divergence = "breakout_looming"
            elif oi_direction == "down" and price_direction == "up":
                divergence = "short_squeeze"
            elif oi_direction == "down" and price_direction == "down":
                divergence = "long_capitulation"
            elif oi_direction == "up" and price_direction == "up":
                divergence = "bullish"

            labels = {
                "bearish": "OI↑价格↓（空头加仓=看空）",
                "short_squeeze": "OI↓价格↑（空头平仓=逼空风险）",
                "bullish": "OI↑价格↑（多头加仓=看多）",
                "long_capitulation": "OI↓价格↓（多头平仓=多杀多）",
                "breakout_looming": "OI↑价格→（分歧加大=即将突破）",
                "none": "无背离",
            }

            return {
                "current_oi": current_oi,
                "current_price": current_price,
                "oi_change_24h_pct": round(oi_change_pct, 2),
                "price_change_24h_pct": round(price_change_pct, 2),
                "oi_direction": oi_direction,
                "price_direction": price_direction,
                "divergence": divergence,
                "divergence_label": labels.get(divergence, "无背离"),
            }
        except Exception as e:
            logger.warning("OI-price divergence fetch failed: %s", e)
            return {"error": str(e), "divergence": "unknown"}

    # ============================================================
    # 指标 3: 资金费率波动率
    # ============================================================

    @classmethod
    def _get_funding_volatility(cls, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        try:
            rows = execute_read(
                "SELECT funding_rate FROM perp_basis_history WHERE currency='BTC' "
                "ORDER BY timestamp DESC LIMIT 21"
            )
            if not rows or len(rows) < 5:
                resp = request_with_retry(
                    "https://fapi.binance.com/fapi/v1/premiumIndex",
                    params={"symbol": symbol},
                    timeout=10, verify=False, max_retries=2
                )
                current_rate = float(resp.json().get("lastFundingRate", 0))
                return {
                    "current_funding_rate_pct": round(current_rate * 100, 4),
                    "volatility_7d_pct": 0.0,
                    "signal": "insufficient_data",
                    "label": "数据不足（需>5个数据点）",
                }

            rates = [r["funding_rate"] for r in rows if r["funding_rate"] is not None]
            if len(rates) < 5:
                return {"error": "insufficient_data"}

            mean_rate = sum(rates) / len(rates)
            variance = sum((r - mean_rate) ** 2 for r in rates) / (len(rates) - 1)
            std_rate = math.sqrt(variance)
            volatility_pct = round(std_rate * 100, 4)

            fixed = CryptoThresholds.get_fixed_threshold("funding_volatility", volatility_pct)

            return {
                "current_funding_rate_pct": round(rates[0] * 100, 4),
                "volatility_7d_pct": volatility_pct,
                "mean_funding_rate_7d_pct": round(mean_rate * 100, 4),
                "data_points": len(rates),
                "signal": fixed.get("signal", "normal"),
                "label": fixed.get("label", ""),
            }
        except Exception as e:
            logger.warning("Funding volatility calc failed: %s", e)
            return {"error": str(e)}

    # ============================================================
    # 指标 4: 清算热力等级
    # ============================================================

    @classmethod
    def _get_liquidation_heat(cls, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """获取清算热力图数据（需要 Binance API Key）

        Binance forceOrders 端点需要签名认证。通过 .env 配置:
          BINANCE_API_KEY=xxx
          BINANCE_SECRET_KEY=xxx

        未配置时返回空值。
        """
        zero_result = {
            "total_liquidation_1h_usd": 0,
            "long_liquidation_usd": 0,
            "short_liquidation_usd": 0,
            "direction_bias": 0,
            "heat_level": "L0",
            "label": "近期无强平 — 市场稳定",
        }

        api_key = config.BINANCE_API_KEY
        secret_key = config.BINANCE_SECRET_KEY
        if not api_key or not secret_key:
            return {**zero_result, "label": "需 API Key"}

        try:
            import requests as req_lib

            base_url = "https://fapi.binance.com"
            endpoint = "/fapi/v1/forceOrders"

            # 获取最近 6 小时的清算订单
            now_ms = int(time.time() * 1000)
            six_hours_ago_ms = now_ms - 6 * 3600 * 1000

            params = {
                "symbol": symbol,
                "startTime": six_hours_ago_ms,
                "limit": 100,
                "recvWindow": 60000,
                "timestamp": now_ms,
            }

            # Build query string and sign with HMAC-SHA256
            query_string = urlencode(params)
            signature = hmac.new(
                secret_key.encode("utf-8"),
                query_string.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            query_string += f"&signature={signature}"

            headers = {"X-MBX-APIKEY": api_key}
            url = f"{base_url}{endpoint}?{query_string}"

            resp = req_lib.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                logger.warning("Binance forceOrders returned %d: %s", resp.status_code, resp.text[:200])
                return {
                    **zero_result,
                    "label": f"API 错误 {resp.status_code}",
                }

            orders = resp.json()
            if not isinstance(orders, list):
                return zero_result

            total_usd = 0.0
            long_usd = 0.0
            short_usd = 0.0

            for order in orders:
                # avgPrice * executedQty = notional in USDT
                avg_price = float(order.get("avgPrice", 0) or 0)
                qty = float(order.get("executedQty", 0) or 0)
                notional = avg_price * qty

                side = str(order.get("side", "")).upper()
                if side == "LONG":
                    long_usd += notional
                elif side == "SHORT":
                    short_usd += notional
                total_usd += notional

            # 方向偏差: + = 多头被清算更多, - = 空头被清算更多
            direction_bias = round(long_usd - short_usd, 2)

            # 热力等级判定
            l2 = config.LIQUIDATION_HEAT_L2_THRESHOLD
            l3 = config.LIQUIDATION_HEAT_L3_THRESHOLD
            if total_usd >= l3:
                heat_level = "L3"
                label = f"🔥 极端清算 ({total_usd/1e6:.0f}M)"
            elif total_usd >= l2:
                heat_level = "L2"
                label = f"高热清算 ({total_usd/1e6:.1f}M)"
            elif total_usd > 0:
                heat_level = "L1"
                label = f"温和清算 ({total_usd/1e3:.0f}K)"
            else:
                heat_level = "L0"
                label = "近期无清算"

            logger.info("Liquidation heat: %s | total=$%s long=$%s short=$%s bias=$%s",
                        heat_level, round(total_usd), round(long_usd), round(short_usd), direction_bias)

            return {
                "total_liquidation_1h_usd": round(total_usd),
                "long_liquidation_usd": round(long_usd),
                "short_liquidation_usd": round(short_usd),
                "direction_bias": direction_bias,
                "heat_level": heat_level,
                "label": label,
            }

        except Exception as e:
            logger.warning("Binance forceOrders failed: %s", e)
            return {
                **zero_result,
                "label": f"获取失败: {str(e)[:30]}",
            }

    # ============================================================
    # 指标 5: 稳定币交易所储备
    # ============================================================

    @classmethod
    def _get_stablecoin_reserve(cls) -> Dict[str, Any]:
        try:
            resp = request_with_retry(
                "https://api.coingecko.com/api/v3/coins/tether",
                params={"localization": "false", "tickers": "false", "community_data": "false",
                        "developer_data": "false"},
                timeout=15, verify=False, max_retries=1
            )
            if resp.status_code == 200:
                data = resp.json()
                market_cap = data.get("market_data", {}).get("market_cap", {}).get("usd", 0)
                if market_cap > 0:
                    estimated_reserve = market_cap * 0.175
                    rows = execute_read(
                        "SELECT balance FROM stablecoin_reserve_history ORDER BY timestamp DESC LIMIT 1"
                    )
                    prev_balance = estimated_reserve
                    if rows:
                        prev_balance = rows[0]["balance"] or estimated_reserve
                    change_7d = ((estimated_reserve - prev_balance) / prev_balance * 100) if prev_balance > 0 else 0

                    execute_write(
                        "INSERT INTO stablecoin_reserve_history (timestamp, exchange, asset, balance, change_7d_pct) "
                        "VALUES (?, 'global', 'USDT', ?, ?)",
                        (datetime.now().isoformat(), estimated_reserve, round(change_7d, 2))
                    )

                    fixed = CryptoThresholds.get_fixed_threshold("stablecoin_flow", change_7d)
                    return {
                        "estimated_reserve_usdt": round(estimated_reserve),
                        "change_7d_pct": round(change_7d, 2),
                        "signal": fixed.get("signal", "neutral"),
                        "label": fixed.get("label", "中性"),
                        "source": "coingecko_estimated",
                    }
        except Exception as e:
            logger.debug("Stablecoin reserve via CoinGecko failed: %s", e)

        return {"estimated_reserve_usdt": 0, "change_7d_pct": 0,
                "signal": "unknown", "label": "数据不可用", "source": "none"}

    # ============================================================
    # 指标 6: 期货/现货成交量比（加密校准阈值）
    # ============================================================

    @classmethod
    def _get_futures_spot_volume_ratio(cls, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        try:
            spot_resp = request_with_retry(
                "https://api.binance.com/api/v3/ticker/24hr",
                params={"symbol": symbol},
                timeout=10, verify=False, max_retries=2
            )
            spot_volume = float(spot_resp.json().get("volume", 0))

            futures_resp = request_with_retry(
                "https://fapi.binance.com/fapi/v1/ticker/24hr",
                params={"symbol": symbol},
                timeout=10, verify=False, max_retries=2
            )
            futures_volume = float(futures_resp.json().get("volume", 0))

            if spot_volume <= 0:
                return {"error": "spot_volume_zero", "ratio": 0}

            ratio = round(futures_volume / spot_volume, 2)
            fixed = CryptoThresholds.get_fixed_threshold("futures_spot_ratio", ratio)

            return {
                "futures_volume": futures_volume,
                "spot_volume": spot_volume,
                "ratio": ratio,
                "signal": fixed.get("signal", "normal"),
                "label": fixed.get("label", "正常加密市场"),
            }
        except Exception as e:
            logger.warning("Futures/spot ratio fetch failed: %s", e)
            return {"error": str(e), "ratio": 0}

    # ============================================================
    # 指标 7: Sharpe Ratio（保留原逻辑）
    # ============================================================

    @classmethod
    def _get_sharpe_ratio(cls) -> Tuple[Optional[float], Optional[float]]:
        try:
            resp = request_with_retry(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "1d", "limit": 90},
                timeout=10, verify=False, max_retries=2
            )
            klines = resp.json()
            if len(klines) < 30:
                return None, None

            closes = [float(k[4]) for k in klines]
            returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]

            returns_14d = returns[-14:]
            sharpe_14d = cls._calc_single_sharpe(returns_14d)

            returns_30d = returns[-30:]
            sharpe_30d = cls._calc_single_sharpe(returns_30d)

            return sharpe_14d, sharpe_30d
        except Exception as e:
            logger.warning("Sharpe Ratio calc failed: %s", e)
            return None, None

    @classmethod
    def _calc_single_sharpe(cls, returns) -> Optional[float]:
        if not returns or len(returns) < 2:
            return None
        avg_return = sum(returns) / len(returns)
        variance = sum((r - avg_return) ** 2 for r in returns) / (len(returns) - 1)
        std_dev = math.sqrt(variance)
        if std_dev == 0:
            return 0.0
        return round((avg_return / std_dev) * math.sqrt(365), 2)

    @classmethod
    def _interpret_sharpe(cls, sharpe: Optional[float]) -> str:
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

    # ============================================================
    # 综合评估 + 公共 API（向后兼容）
    # ============================================================

    @classmethod
    def get_all_metrics(cls, currency: str = "BTC") -> Dict[str, Any]:
        """获取所有衍生品指标（向后兼容旧API）"""
        perp_basis = cls._get_perp_basis(currency)
        oi_div = cls._get_oi_price_divergence()
        fund_vol = cls._get_funding_volatility()
        liq_heat = cls._get_liquidation_heat()
        stablecoin = cls._get_stablecoin_reserve()
        vol_ratio = cls._get_futures_spot_volume_ratio()
        sharpe_14d, sharpe_30d = cls._get_sharpe_ratio()

        assessment = cls._assess_crypto_overheating(
            perp_basis=perp_basis,
            oi_div=oi_div,
            fund_vol=fund_vol,
            liq_heat=liq_heat,
            stablecoin=stablecoin,
            vol_ratio=vol_ratio,
            sharpe_14d=sharpe_14d,
        )

        return {
            # 新指标
            "perp_basis": perp_basis,
            "oi_price_divergence": oi_div,
            "funding_volatility": fund_vol,
            "liquidation_heat": liq_heat,
            "stablecoin_reserve": stablecoin,
            "futures_spot_ratio": vol_ratio,
            # 旧指标（向后兼容）
            "sharpe_ratio_14d": sharpe_14d,
            "sharpe_ratio_30d": sharpe_30d,
            "sharpe_signal_14d": cls._interpret_sharpe(sharpe_14d),
            "sharpe_signal_30d": cls._interpret_sharpe(sharpe_30d),
            "funding_rate": perp_basis.get("funding_rate"),
            "funding_rate_pct": perp_basis.get("funding_rate_pct"),
            "funding_signal": _legacy_funding_signal(perp_basis.get("funding_rate", 0)),
            "futures_spot_signal": vol_ratio.get("label", ""),
            "overheating_assessment": assessment,
            "timestamp": datetime.now().isoformat(),
        }

    @classmethod
    def _assess_crypto_overheating(cls, perp_basis, oi_div, fund_vol,
                                    liq_heat, stablecoin, vol_ratio, sharpe_14d) -> Dict[str, Any]:
        """加密原生过热综合评估"""
        score = 0
        signals = []

        # 1. 永续基差
        basis = perp_basis.get("basis_annualized", 0) if isinstance(perp_basis, dict) else 0
        if basis > config.PERP_BASIS_THRESHOLD_EXTREME:
            score -= 10
            signals.append({"emoji": "⚠️", "text": f"永续基差{basis}%极度投机", "type": "overheat"})
        elif basis > config.PERP_BASIS_THRESHOLD_HIGH:
            score -= 5
            signals.append({"emoji": "⚠️", "text": f"永续基差{basis}%偏高", "type": "overheat"})
        elif basis < -2:
            score += 5
            signals.append({"emoji": "🔴", "text": f"永续基差{basis}%（现货溢价=看空）", "type": "bearish"})
        else:
            signals.append({"emoji": "🟢", "text": f"永续基差{basis}%正常", "type": "neutral"})

        # 2. OI-价格背离
        div = oi_div.get("divergence", "none") if isinstance(oi_div, dict) else "none"
        if div == "bearish":
            score -= 7
            signals.append({"emoji": "🔴", "text": "OI↑价格↓空头加仓", "type": "bearish"})
        elif div == "long_capitulation":
            score -= 5
            signals.append({"emoji": "⚠️", "text": "OI↓价格↓多杀多", "type": "bearish"})
        elif div == "short_squeeze":
            score += 5
            signals.append({"emoji": "🟢", "text": "OI↓价格↑逼空进行中", "type": "bullish"})
        elif div == "bullish":
            score += 3
            signals.append({"emoji": "🟢", "text": "OI↑价格↑多头加仓", "type": "bullish"})

        # 3. 资金费率波动率
        fv_signal = fund_vol.get("signal", "normal") if isinstance(fund_vol, dict) else "normal"
        if fv_signal == "extreme":
            score -= 5
            signals.append({"emoji": "⚠️", "text": "费率波动剧烈（拐点预警）", "type": "overheat"})

        # 4. 清算热力
        liq_level = liq_heat.get("heat_level", "L0") if isinstance(liq_heat, dict) else "L0"
        liq_bias = liq_heat.get("direction_bias", 0) if isinstance(liq_heat, dict) else 0
        if liq_level == "L3":
            score -= 8
            signals.append({"emoji": "🔴", "text": "L3清算高压", "type": "risk"})
        elif liq_level == "L2":
            score -= 4
            signals.append({"emoji": "⚠️", "text": "L2中度清算压力", "type": "risk"})
        if liq_bias > 0.3:
            score += 3
            signals.append({"emoji": "🟢", "text": "多头痛苦（潜在底部）", "type": "bottom"})

        # 5. 稳定币储备
        sc_signal = stablecoin.get("signal", "neutral") if isinstance(stablecoin, dict) else "neutral"
        if sc_signal == "strong_inflow":
            score += 4
            signals.append({"emoji": "🟢", "text": "稳定币大量流入", "type": "bullish"})
        elif sc_signal in ("outflow", "strong_outflow"):
            score -= 3
            signals.append({"emoji": "⚠️", "text": "稳定币流出", "type": "bearish"})

        # 6. 期货/现货比（降权）
        ratio = vol_ratio.get("ratio", 0) if isinstance(vol_ratio, dict) else 0
        if ratio > config.FUTURES_SPOT_RATIO_EXTREME:
            score -= 3
            signals.append({"emoji": "⚠️", "text": f"比值{ratio}x偏高", "type": "overheat"})

        # 综合判定
        if score >= 8:
            level, name, icon, color, advice = "STRONG_BOTTOM", "衍生品底部信号强", "🟢", "text-green-400", "衍生品信号偏多"
        elif score >= 3:
            level, name, icon, color, advice = "BOTTOM", "潜在底部", "🟢", "text-green-400", "衍生品指标偏正面"
        elif score >= -3:
            level, name, icon, color, advice = "NEUTRAL", "中性", "⚪", "text-gray-400", "衍生品市场处于正常状态"
        elif score >= -8:
            level, name, icon, color, advice = "OVERHEATED", "过热警告", "⚠️", "text-orange-400", "衍生品过热，注意风险"
        else:
            level, name, icon, color, advice = "EXTREME_OVERHEAT", "极度过热", "🔴", "text-red-400", "衍生品极度过热"

        return {
            "score": score,
            "level": level,
            "name": name,
            "icon": icon,
            "color": color,
            "advice": advice,
            "signals": signals,
            "note": "基于加密原生指标体系（永续基差+OI背离+清算数据+稳定币流动+费率波动率）",
        }


def _legacy_funding_signal(funding_rate: float) -> str:
    """向后兼容的资金费率信号（旧 API 调用者依赖此字段）"""
    if funding_rate > 0.002:
        return "极度多头过热（警惕回调）"
    elif funding_rate > 0.001:
        return "多头过热（杠杆过高）"
    elif funding_rate > 0.0005:
        return "多头偏多（正常偏高）"
    elif funding_rate > 0.0001:
        return "轻微多头（正常）"
    elif funding_rate > -0.0001:
        return "中性（正常范围）"
    elif funding_rate > -0.0005:
        return "轻微空头（正常偏低）"
    elif funding_rate > -0.001:
        return "空头偏多（关注反弹）"
    else:
        return "极度空头（可能底部）"
