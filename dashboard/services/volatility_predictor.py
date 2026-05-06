"""
Volatility Predictor v1.0 — FreqAI-inspired light ML

Uses EMA crossover + momentum on DVOL time series to predict
volatility direction (up/down/sideways) for the next 7 days.
No heavy ML dependencies — pure statistical ensemble.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class VolatilitySignal:
    direction: str = "sideways"  # up | down | sideways
    confidence: float = 50.0
    predicted_dvol_7d: float = 50.0
    current_dvol: float = 50.0
    current_z: float = 0.0
    mean_reversion_score: float = 0.0
    momentum_score: float = 0.0
    trend_strength: float = 0.0
    regime: str = "normal"
    suggested_params: Dict = field(default_factory=dict)
    details: Dict = field(default_factory=dict)


class VolatilityPredictor:
    """DVOL 方向预测器 — 整合均值回归 + 动量 + EMA 交叉"""

    FAST_EMA = 5       # days
    SLOW_EMA = 14      # days
    SIGNAL_EMA = 7     # days (for crossover smoothing)
    LOOKBACK_Z = 30    # days for z-score baseline
    MOMENTUM_WINDOW = 7

    def __init__(self):
        pass

    def predict(self, dvol_history: List[Dict], current_spot: float = 0.0) -> VolatilitySignal:
        """
        预测未来 7 天 DVOL 方向

        Args:
            dvol_history: [{"date": str, "dvol": float}, ...] sorted by date ascending
            current_spot: current spot price

        Returns:
            VolatilitySignal with direction, confidence, and suggested strategy params
        """
        if not dvol_history or len(dvol_history) < self.SLOW_EMA:
            return VolatilitySignal(
                direction="sideways",
                confidence=30.0,
                details={"reason": f"数据不足 (需要至少 {self.SLOW_EMA} 天)"},
            )

        values = [d["dvol"] for d in dvol_history if d.get("dvol", 0) > 0]
        if len(values) < self.SLOW_EMA:
            return VolatilitySignal(direction="sideways", confidence=30.0)

        current_dvol = values[-1]

        # 1) EMA crossover
        fast_ema = self._ema(values, self.FAST_EMA)
        slow_ema = self._ema(values, self.SLOW_EMA)
        crossover_score = (fast_ema - slow_ema) / slow_ema * 100 if slow_ema > 0 else 0

        # 2) Mean reversion (z-score based)
        baseline = values[-self.LOOKBACK_Z:] if len(values) >= self.LOOKBACK_Z else values
        mean_dvol = sum(baseline) / len(baseline)
        if len(baseline) > 1:
            std_dvol = (sum((v - mean_dvol) ** 2 for v in baseline) / (len(baseline) - 1)) ** 0.5
        else:
            std_dvol = 1
        z_score = (current_dvol - mean_dvol) / std_dvol if std_dvol > 0 else 0
        mean_reversion = -z_score * 0.3  # Negative: if z is high, expect reversion down

        # 3) Momentum (short-term rate of change)
        recent = values[-self.MOMENTUM_WINDOW:]
        momentum = (recent[-1] - recent[0]) / recent[0] * 100 if recent[0] > 0 else 0

        # 4) Trend strength (linear regression slope over last 14 days)
        lookback = values[-14:] if len(values) >= 14 else values
        n = len(lookback)
        if n >= 3:
            x_mean = (n - 1) / 2
            y_mean = sum(lookback) / n
            num = sum((i - x_mean) * (lookback[i] - y_mean) for i in range(n))
            den = sum((i - x_mean) ** 2 for i in range(n))
            trend_slope = num / den if den > 0 else 0
            trend_strength = trend_slope / y_mean * 100 if y_mean > 0 else 0
        else:
            trend_strength = 0

        # Ensemble score: weighted sum
        ensemble = (
            crossover_score * 0.35 +
            mean_reversion * 0.30 +
            momentum * 0.20 +
            trend_strength * 0.15
        )

        # Determine direction and confidence
        if ensemble > 8:
            direction = "up"
            confidence = min(90, 50 + abs(ensemble) * 2)
        elif ensemble < -8:
            direction = "down"
            confidence = min(90, 50 + abs(ensemble) * 2)
        else:
            direction = "sideways"
            confidence = max(30, 70 - abs(ensemble) * 3)

        # Predict DVOL in 7 days: current + ensemble drift capped
        predicted_7d = current_dvol + max(-15, min(15, ensemble * 0.5))
        predicted_7d = max(20, min(120, predicted_7d))

        # Regime classification and suggested params
        regime, suggested_params = self._regime_params(current_dvol, z_score)
        if direction == "up":
            suggested_params = {
                "max_delta": 0.20, "min_dte": 7, "max_dte": 21,
                "min_apr": 25.0, "margin_ratio": 0.22,
                "rationale": "DVOL 预计上升，收紧参数降低风险敞口"
            }
        elif direction == "down":
            suggested_params = {
                "max_delta": 0.40, "min_dte": 7, "max_dte": 60,
                "min_apr": 8.0, "margin_ratio": 0.18,
                "rationale": "DVOL 预计下降，放宽参数捕捉更多机会"
            }
        else:
            suggested_params = {
                "max_delta": 0.30, "min_dte": 14, "max_dte": 35,
                "min_apr": 15.0, "margin_ratio": 0.20,
                "rationale": "DVOL 预计横盘，使用标准参数"
            }

        return VolatilitySignal(
            direction=direction,
            confidence=round(confidence, 1),
            predicted_dvol_7d=round(predicted_7d, 1),
            current_dvol=round(current_dvol, 1),
            current_z=round(z_score, 2),
            mean_reversion_score=round(mean_reversion, 2),
            momentum_score=round(momentum, 2),
            trend_strength=round(trend_strength, 2),
            regime=regime,
            suggested_params=suggested_params,
            details={
                "fast_ema": round(fast_ema, 1),
                "slow_ema": round(slow_ema, 1),
                "crossover_score": round(crossover_score, 2),
                "ensemble_score": round(ensemble, 2),
                "data_points": len(values),
            },
        )

    @staticmethod
    def _ema(values: List[float], period: int) -> float:
        """Exponential moving average"""
        if len(values) < period:
            return sum(values) / len(values) if values else 0
        alpha = 2 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = alpha * v + (1 - alpha) * ema
        return ema

    @staticmethod
    def _regime_params(dvol: float, z_score: float) -> Tuple[str, Dict]:
        """Determine volatility regime and suggested strategy params"""
        if dvol > 80:
            regime = "extreme"
            params = {
                "max_delta": 0.15, "min_dte": 7, "max_dte": 14,
                "min_apr": 30.0, "margin_ratio": 0.25,
                "rationale": "DVOL 极端高位，高度保守"
            }
        elif dvol > 65:
            regime = "high"
            params = {
                "max_delta": 0.20, "min_dte": 7, "max_dte": 21,
                "min_apr": 25.0, "margin_ratio": 0.22,
                "rationale": "DVOL 高位，保守操作"
            }
        elif dvol > 50:
            regime = "elevated"
            params = {
                "max_delta": 0.25, "min_dte": 14, "max_dte": 28,
                "min_apr": 20.0, "margin_ratio": 0.22,
                "rationale": "DVOL 偏高，适度谨慎"
            }
        elif dvol > 35:
            regime = "normal"
            params = {
                "max_delta": 0.30, "min_dte": 14, "max_dte": 35,
                "min_apr": 15.0, "margin_ratio": 0.20,
                "rationale": "DVOL 正常，标准参数"
            }
        else:
            regime = "low"
            params = {
                "max_delta": 0.40, "min_dte": 7, "max_dte": 60,
                "min_apr": 8.0, "margin_ratio": 0.18,
                "rationale": "DVOL 低位，积极操作"
            }

        # Adjust by z-score if extreme
        if abs(z_score) > 2.5:
            params["rationale"] += f" (Z={z_score:.1f}极端，预期均值回归)"

        return regime, params

    @staticmethod
    def backtest_predictions(
        dvol_history: List[Dict],
        horizon: int = 7,
    ) -> Dict:
        """Backtest DVOL direction predictions against actual outcomes"""
        if len(dvol_history) < 30 + horizon:
            return {"success": False, "reason": "数据不足"}

        predictor = VolatilityPredictor()
        correct = 0
        total = 0

        for i in range(len(dvol_history) - horizon):
            past = dvol_history[:i + 1]
            future_val = dvol_history[i + horizon]["dvol"]
            current_val = dvol_history[i]["dvol"]
            actual_direction = "up" if future_val > current_val * 1.03 else ("down" if future_val < current_val * 0.97 else "sideways")

            signal = predictor.predict(past)
            if signal.direction == actual_direction:
                correct += 1
            total += 1

        accuracy = correct / total * 100 if total > 0 else 0
        return {
            "success": True,
            "accuracy": round(accuracy, 1),
            "total_predictions": total,
            "correct": correct,
            "horizon_days": horizon,
        }
