"""
加密原生阈值注册表 — 混合阈值系统
- 核心指标：从 DB 历史数据计算滚动百分位
- 辅助指标：使用加密校准的固定阈值
"""
import logging
from typing import Dict, Any

from db.connection import execute_read

logger = logging.getLogger(__name__)


class CryptoThresholds:
    """加密原生阈值管理器"""

    @classmethod
    def get_percentile_threshold(cls, metric_name: str, current_value: float,
                                 currency: str = "BTC", window_days: int = 30) -> Dict[str, Any]:
        """获取百分位阈值（从 DB 历史数据计算）"""
        table_map = {
            "perp_basis": ("perp_basis_history", "basis_annualized"),
            "futures_spot_ratio": ("perp_basis_history", "basis_annualized"),
        }

        if metric_name not in table_map:
            return {"pct": 50, "signal": "unknown", "status": "no_threshold"}

        table, column = table_map[metric_name]
        try:
            rows = execute_read(
                f"SELECT {column} FROM {table} WHERE currency=? "
                "AND timestamp >= datetime('now', ? || ' days') "
                f"ORDER BY {column} ASC",
                (currency, f"-{window_days}")
            )
            if not rows or len(rows) < 10:
                return {"pct": 50, "signal": "neutral", "status": "insufficient_data",
                        "window_days": window_days, "data_points": len(rows) if rows else 0}

            values = [r[column] for r in rows]
            n = len(values)
            rank = sum(1 for v in values if v < current_value)
            pct = round(rank / n * 100, 1)

            if pct >= 90:
                signal = "extreme_high"
            elif pct >= 75:
                signal = "high"
            elif pct >= 25:
                signal = "normal"
            elif pct >= 10:
                signal = "low"
            else:
                signal = "extreme_low"

            return {"pct": pct, "signal": signal, "status": "ok",
                    "window_days": window_days, "data_points": n,
                    "min": round(values[0], 4), "max": round(values[-1], 4),
                    "median": round(values[n // 2], 4)}
        except Exception as e:
            logger.warning("Percentile calc for %s failed: %s", metric_name, e)
            return {"pct": 50, "signal": "neutral", "status": f"error: {e}"}

    @classmethod
    def get_fixed_threshold(cls, metric_name: str, value: float) -> Dict[str, Any]:
        """获取固定阈值判定（加密校准）"""
        thresholds = {
            "perp_basis": [
                (30.0, "extreme_high", "极端投机区间"),
                (15.0, "high", "杠杆偏高"),
                (8.0, "normal_high", "温和看多"),
                (0.0, "normal", "正常Contango"),
                (-999.0, "negative", "现货溢价（看空信号）"),
            ],
            "futures_spot_ratio": [
                (25.0, "extreme_high", "极端（低流动性山寨币级别）"),
                (15.0, "very_high", "高杠杆（关注但不等于过热）"),
                (8.0, "high", "杠杆偏高"),
                (3.0, "normal", "正常加密市场（永续结构）"),
                (0.0, "low", "现货主导（熊市/横盘正常）"),
            ],
            "funding_rate_pct": [
                (0.2, "extreme_long", "极度多头过热"),
                (0.1, "long_overheat", "多头过热"),
                (0.05, "long_bias", "多头偏多"),
                (0.01, "slight_long", "轻微多头（正常）"),
                (-0.05, "neutral", "中性"),
                (-0.1, "short_bias", "空头偏多"),
                (-999.0, "extreme_short", "极度空头（可能底部）"),
            ],
            "funding_volatility": [
                (0.1, "extreme", "极端波动（潜在拐点）"),
                (0.05, "high", "情绪反复"),
                (0.01, "normal", "正常波动"),
                (0.0, "stable", "稳定（市场共识强）"),
            ],
            "liquidation_heat": [
                (20_000_000, "L3", "高压（可能触发连锁清算）"),
                (5_000_000, "L2", "中度压力"),
                (1_000_000, "L1", "轻度压力"),
                (0, "L0", "正常"),
            ],
            "stablecoin_flow": [
                (5.0, "strong_inflow", "强买盘预期"),
                (2.0, "mild_inflow", "温和看多"),
                (-2.0, "neutral", "中性"),
                (-5.0, "outflow", "资金撤退"),
                (-999.0, "strong_outflow", "防御信号"),
            ],
        }

        if metric_name not in thresholds:
            return {"signal": "unknown", "label": ""}

        for threshold, signal, label in thresholds[metric_name]:
            if value >= threshold:
                return {"signal": signal, "label": label, "value": value, "threshold": threshold}

        return {"signal": "unknown", "label": ""}

    @classmethod
    def hybrid_assess(cls, metric_name: str, current_value: float,
                      currency: str = "BTC") -> Dict[str, Any]:
        """混合评估：核心指标用百分位 + 固定阈值双重判定"""
        percentile = cls.get_percentile_threshold(metric_name, current_value, currency)
        fixed = cls.get_fixed_threshold(metric_name, current_value)

        pct_signal = percentile.get("signal", "neutral")
        fix_signal = fixed.get("signal", "normal")

        if pct_signal in ("extreme_high", "extreme_low") and fix_signal not in ("normal", "slight_long"):
            confidence = "high"
        elif pct_signal in ("high", "low") and fix_signal not in ("normal",):
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "metric": metric_name,
            "value": current_value,
            "percentile": percentile,
            "fixed_threshold": fixed,
            "confidence": confidence,
            "verdict": fixed.get("label", ""),
        }
