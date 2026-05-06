"""
Portfolio Risk Engine v1.0 — Freqtrade-inspired risk management

Features:
- Delta-Normal VaR / CVaR (95% confidence)
- Strike concentration risk
- Drawdown circuit breaker
- DVOL-dynamic stop-loss
"""
import math
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class PortfolioRiskResult:
    var_95: float = 0.0
    cvar_95: float = 0.0
    var_95_pct: float = 0.0
    cvar_95_pct: float = 0.0
    concentration_risk: str = "LOW"
    max_strike_band_ratio: float = 0.0
    drawdown_from_peak: float = 0.0
    circuit_breaker_tripped: bool = False
    circuit_breaker_reason: str = ""
    stop_loss_price: float = 0.0
    total_margin_used: float = 0.0
    total_premium: float = 0.0
    position_count: int = 0
    risk_level: str = "NORMAL"
    details: Dict = field(default_factory=dict)


class PortfolioRisk:
    """投资组合级别风险管理"""

    CONFIDENCE_Z = {"90": 1.282, "95": 1.645, "99": 2.326}

    @staticmethod
    def calc_var(
        positions: List[Dict],
        spot: float,
        iv: float,
        confidence: str = "95",
    ) -> PortfolioRiskResult:
        """
        Delta-Normal VaR 计算

        公式: VaR = spot * Σ|delta_i * oi_i * strike_i| * iv / √365 * z
        CVaR = VaR × 1.25 (crypto fat-tail adjustment)
        """
        if spot <= 0 or not positions:
            return PortfolioRiskResult()

        z = PortfolioRisk.CONFIDENCE_Z.get(confidence, 1.645)
        iv_decimal = iv / 100.0
        daily_vol = iv_decimal / math.sqrt(365)

        total_delta_exposure = 0.0
        total_margin = 0.0
        total_premium = 0.0
        strikes_used: List[float] = []
        total_notional = 0.0

        for p in positions:
            delta = abs(p.get("delta", 0.3))
            strike = p.get("strike", 0)
            qty = p.get("qty", 1)
            # Each position: notional = strike * qty, delta-weighted exposure
            total_delta_exposure += delta * strike * qty
            total_notional += strike * qty
            total_margin += p.get("margin_required", strike * 0.2 * qty)
            total_premium += p.get("premium_usd", 0) * qty
            strikes_used.append(strike)

        # Daily VaR = total_delta_exposure * daily_vol * z_confidence
        # This gives us the dollar VaR for a one-day move
        daily_var = total_delta_exposure * daily_vol * z
        cvar_95 = daily_var * 1.25
        var_95_pct = (daily_var / total_notional * 100) if total_notional > 0 else 0
        cvar_95_pct = (cvar_95 / total_notional * 100) if total_notional > 0 else 0

        # Strike concentration: band ratio > 50% = DANGER
        concentration, max_band_ratio = PortfolioRisk._check_concentration(strikes_used, spot)

        result = PortfolioRiskResult(
            var_95=round(daily_var, 0),
            cvar_95=round(cvar_95, 0),
            var_95_pct=round(var_95_pct, 3),
            cvar_95_pct=round(cvar_95_pct, 3),
            concentration_risk=concentration,
            max_strike_band_ratio=round(max_band_ratio, 2),
            total_margin_used=round(total_margin, 2),
            total_premium=round(total_premium, 2),
            position_count=len(positions),
        )

        # Risk level
        if var_95_pct > 5.0 or concentration == "DANGER":
            result.risk_level = "HIGH"
        elif var_95_pct > 2.0 or concentration == "CAUTION":
            result.risk_level = "ELEVATED"
        else:
            result.risk_level = "NORMAL"

        return result

    @staticmethod
    def _check_concentration(strikes: List[float], spot: float) -> tuple:
        """行权价集中度检查"""
        if not strikes or spot <= 0:
            return "LOW", 0.0

        band_width = spot * 0.05
        bands = {}
        for s in strikes:
            band_key = round(s / band_width) * band_width
            bands[band_key] = bands.get(band_key, 0) + 1

        max_in_band = max(bands.values())
        total = len(strikes)
        max_ratio = max_in_band / total if total > 0 else 0

        if max_ratio > 0.5:
            return "DANGER", max_ratio
        elif max_ratio > 0.3:
            return "CAUTION", max_ratio
        return "LOW", max_ratio

    @staticmethod
    def check_drawdown(current_equity: float, peak_equity: float, threshold: float = 0.20) -> tuple:
        """回撤熔断检查"""
        if peak_equity <= 0:
            return False, 0.0, ""
        drawdown = (peak_equity - current_equity) / peak_equity
        if drawdown >= threshold:
            return True, round(drawdown, 4), f"回撤 {drawdown:.1%} 超过熔断线 {threshold:.0%}"
        return False, round(drawdown, 4), ""

    @staticmethod
    def calc_dynamic_stop_loss(
        spot: float,
        dvol: float,
        drawdown: float = 0.0,
        dvol_multiplier: float = 0.70,
    ) -> float:
        """DVOL 动态止损价位"""
        dvol_factor = max(dvol / 100.0, 0.15)
        drawdown_factor = max(drawdown * 1.5, 0)
        stop_ratio = max(dvol_factor * dvol_multiplier, drawdown_factor)
        stop_ratio = min(stop_ratio, 0.50)  # Cap at 50% drop
        return round(spot * (1.0 - stop_ratio), 2)

    @staticmethod
    def calc_sharpe_sortino_calmar(
        returns: List[float],
        annualization: float = 365.0,
    ) -> Dict[str, float]:
        """Calculate Sharpe, Sortino, and Calmar ratios from a return series.

        Freqtrade-aligned: all three metrics in one call.
        """
        if len(returns) < 2:
            return {"sharpe": 0, "sortino": 0, "calmar": 0, "max_drawdown_pct": 0}

        mean_ret = sum(returns) / len(returns)
        if len(returns) > 1:
            total_std = math.sqrt(sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1))
        else:
            total_std = 1.0
        sharpe = (mean_ret / total_std * math.sqrt(annualization)) if total_std > 0 else 0.0

        downside = [r for r in returns if r < 0]
        if downside and len(downside) > 1:
            d_std = math.sqrt(sum(r ** 2 for r in downside) / len(downside))
            sortino = (mean_ret / d_std * math.sqrt(annualization)) if d_std > 0 else 0.0
        elif not downside:
            sortino = mean_ret * annualization * 0.5
        else:
            sortino = 0.0

        peak = 0.0
        cum_return = 0.0
        max_dd = 0.0
        for r in returns:
            cum_return += r
            peak = max(peak, cum_return)
            dd = (peak - cum_return) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

        calmar = (mean_ret * annualization) / max_dd if max_dd > 0 else 0.0

        return {
            "sharpe": round(sharpe, 3),
            "sortino": round(sortino, 3),
            "calmar": round(calmar, 4),
            "max_drawdown_pct": round(max_dd * 100, 2),
        }

    @staticmethod
    def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
        """
        凯利公式: f* = (bp - q) / b
        b = avg_win / avg_loss (赔率)
        p = win_rate (胜率)
        q = 1 - p
        """
        p = max(0.01, min(0.99, win_rate))
        q = 1.0 - p
        if avg_loss <= 0:
            return 1.0
        b = avg_win / avg_loss
        kelly = (b * p - q) / b if b > 0 else 0.0
        # Use half-Kelly for safety
        return max(0.0, min(kelly * 0.5, 0.25))
