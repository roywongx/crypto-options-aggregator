import math
import json
import sys
from typing import List, Optional, Dict, Any
from datetime import datetime
from models.grid import (
    GridLevel, GridRecommendation, GridScenario,
    GridDirection, RecommendationLevel, VolDirectionSignal
)

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    from scipy.stats import norm
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

def _norm_cdf(x: float) -> float:
    if HAS_SCIPY:
        return norm.cdf(x)
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def _calc_grid_score(
    apr: float,
    distance_pct: float,
    oi: int,
    volume: int,
    dte: int
) -> float:
    apr_score = min(apr / 100.0, 1.0)
    safety_score = 1.0 - min(abs(distance_pct) / 15.0, 1.0)
    liquidity_score = min((oi / 500.0 + volume / 100.0), 1.0) / 2.0

    if 14 <= dte <= 21:
        theta_score = 1.0
    elif dte < 14:
        theta_score = 0.5 + (dte / 14.0) * 0.5
    else:
        theta_score = max(0.3, 1.0 - (dte - 21) / 30.0)

    score = apr_score * 0.35 + safety_score * 0.30 + liquidity_score * 0.20 + theta_score * 0.15
    return score

def _score_to_level(score: float) -> RecommendationLevel:
    if score >= 0.75:
        return RecommendationLevel.BEST
    elif score >= 0.60:
        return RecommendationLevel.GOOD
    elif score >= 0.45:
        return RecommendationLevel.OK
    elif score >= 0.30:
        return RecommendationLevel.CAUTION
    return RecommendationLevel.SKIP

def _generate_reason(apr: float, distance_pct: float, oi: int, dte: int, iv: float) -> str:
    reasons = []
    if apr > 50:
        reasons.append("高收益")
    if abs(distance_pct) > 10:
        reasons.append("安全距离充足")
    if oi < 100:
        reasons.append("流动性不足")
    if dte < 10:
        reasons.append("短期Theta加速")
    elif dte > 30:
        reasons.append("长期权利金较高")
    if iv > 70:
        reasons.append("高波动率环境")
    return "; ".join(reasons) if reasons else "均衡配置"

def calculate_grid_levels(
    contracts: List[Dict[str, Any]],
    spot_price: float,
    direction: GridDirection,
    count: int = 5,
    min_dte: int = 7,
    max_dte: int = 45,
    min_apr: float = 15.0
) -> List[GridLevel]:
    if not contracts or spot_price <= 0:
        return []

    target_type = "P" if direction == GridDirection.PUT else "C"

    candidates = []
    for c in contracts:
        c_type = c.get("type", "")
        if c_type.upper() != target_type:
            continue

        try:
            strike = float(c.get("strike", 0))
            if strike <= 0:
                continue

            dte = int(c.get("dte", 0))
            if not (min_dte <= dte <= max_dte):
                continue

            premium_usd = float(c.get("premium_usd", c.get("premium", 0)))
            if premium_usd <= 0:
                continue

            apr = float(c.get("apr", 0))
            if apr < min_apr:
                continue

            distance_pct = ((strike - spot_price) / spot_price) * 100
            if direction == GridDirection.PUT and distance_pct > 0:
                continue
            if direction == GridDirection.CALL and distance_pct < 0:
                continue

            iv = float(c.get("iv", c.get("iv_percentile", 50)))
            delta = abs(float(c.get("delta", 0.5)))
            oi = int(c.get("open_interest", c.get("oi", 0)))
            volume = int(c.get("volume_24h", c.get("volume", 0)))
            expiry = c.get("expiry", c.get("expiration_date", ""))
            if not expiry:
                expiry = c.get("symbol", "").split("-")[-1] if "-" in c.get("symbol", "") else ""

            score = _calc_grid_score(apr, distance_pct, oi, volume, dte)
            recommendation = _score_to_level(score)
            reason = _generate_reason(apr, distance_pct, oi, dte, iv)

            liquidity_score = min((oi / 500.0 + volume / 100.0), 1.0) / 2.0

            candidates.append(GridLevel(
                direction=direction,
                strike=strike,
                expiry=expiry,
                dte=dte,
                premium_usd=premium_usd,
                apr=apr,
                distance_pct=distance_pct,
                iv=iv,
                delta=delta,
                oi=oi,
                volume=volume,
                liquidity_score=liquidity_score,
                recommendation=recommendation,
                reason=reason
            ))
        except (ValueError, TypeError):
            continue

    candidates.sort(key=lambda x: _calc_grid_score(x.apr, x.distance_pct, x.oi, x.volume, x.dte), reverse=True)

    selected = []
    seen_strikes = set()
    for c in candidates:
        strike_pct_diff = 0
        for s in seen_strikes:
            diff = abs(c.strike - s) / s * 100
            if diff < 2:
                strike_pct_diff = diff
                break

        if strike_pct_diff < 2:
            continue

        selected.append(c)
        seen_strikes.add(c.strike)

        if len(selected) >= count:
            break

    if len(selected) < count and min_apr > 5:
        return calculate_grid_levels(
            contracts, spot_price, direction, count,
            min_dte, max_dte, min_apr - 10
        )

    return selected

def get_vol_direction_signal(
    contracts: List[Dict[str, Any]],
    currency: str = "BTC"
) -> VolDirectionSignal:
    if not contracts:
        return VolDirectionSignal(
            dvol_current=50.0,
            dvol_30d_avg=50.0,
            dvol_percentile=50.0,
            skew={"put_iv_avg": 50, "call_iv_avg": 50, "skew_pct": 0, "interpretation": "数据不足"},
            signal="NEUTRAL",
            reason="无合约数据",
            suggested_ratio="5:5"
        )

    put_ivs = []
    call_ivs = []
    dte_30_count = 0
    total_iv = 0

    for c in contracts:
        try:
            iv = float(c.get("iv", c.get("iv_percentile", 50)))
            dte = int(c.get("dte", 0))
            c_type = c.get("type", "").upper()

            if c_type == "P":
                put_ivs.append(iv)
            elif c_type == "C":
                call_ivs.append(iv)

            if 25 <= dte <= 35:
                dte_30_count += 1
                total_iv += iv
        except (ValueError, TypeError):
            continue

    dvol_current = sum(put_ivs) / len(put_ivs) if put_ivs else 50.0
    dvol_30d_avg = total_iv / dte_30_count if dte_30_count > 0 else dvol_current

    dvol_percentile = 50.0
    if dvol_30d_avg > 0:
        dvol_percentile = min(100, max(0, (dvol_current / dvol_30d_avg - 0.5) * 200 + 50))

    put_iv_avg = sum(put_ivs) / len(put_ivs) if put_ivs else 50.0
    call_iv_avg = sum(call_ivs) / len(call_ivs) if call_ivs else 50.0
    skew_pct = put_iv_avg - call_iv_avg

    if skew_pct > 3:
        interpretation = "市场偏恐惧，Put端溢价"
    elif skew_pct < -3:
        interpretation = "市场偏乐观，Call端溢价"
    else:
        interpretation = "市场相对均衡"

    if dvol_percentile > 70 and skew_pct > 3:
        signal = "FAVOR_PUT"
        suggested_ratio = "6:4"
        reason = f"DVOL分位{dvol_percentile:.0f}%偏高，Put端IV溢价{skew_pct:.1f}%，建议偏重Sell Put收租"
    elif dvol_percentile < 30 and skew_pct < -3:
        signal = "FAVOR_CALL"
        suggested_ratio = "4:6"
        reason = f"DVOL分位{dvol_percentile:.0f}%偏低，市场平静，建议偏重Sell Call收溢价"
    else:
        signal = "NEUTRAL"
        suggested_ratio = "5:5"
        reason = f"DVOL分位{dvol_percentile:.0f}%，偏度{skew_pct:.1f}%，建议均衡配置"

    return VolDirectionSignal(
        dvol_current=round(dvol_current, 2),
        dvol_30d_avg=round(dvol_30d_avg, 2),
        dvol_percentile=round(dvol_percentile, 1),
        skew={
            "put_iv_avg": round(put_iv_avg, 2),
            "call_iv_avg": round(call_iv_avg, 2),
            "skew_pct": round(skew_pct, 2),
            "interpretation": interpretation
        },
        signal=signal,
        reason=reason,
        suggested_ratio=suggested_ratio
    )

def recommend_grid(
    contracts: List[Dict[str, Any]],
    currency: str = "BTC",
    spot_price: float = None,
    put_count: int = 5,
    call_count: int = 3,
    min_dte: int = 7,
    max_dte: int = 45,
    min_apr: float = 15.0,
    prefer_short_dte: bool = True
) -> GridRecommendation:
    if not spot_price or spot_price <= 0:
        spot_price = 83000.0

    put_levels = calculate_grid_levels(
        contracts, spot_price, GridDirection.PUT,
        put_count, min_dte, max_dte, min_apr
    )

    call_levels = calculate_grid_levels(
        contracts, spot_price, GridDirection.CALL,
        call_count, min_dte, max_dte, min_apr
    )

    vol_signal = get_vol_direction_signal(contracts, currency)

    total_premium = sum(p.premium_usd for p in put_levels) + sum(c.premium_usd for c in call_levels)

    return GridRecommendation(
        currency=currency,
        spot_price=spot_price,
        timestamp=datetime.utcnow().isoformat(),
        put_levels=put_levels,
        call_levels=call_levels,
        dvol_signal=vol_signal.signal,
        recommended_ratio=vol_signal.suggested_ratio,
        total_potential_premium=round(total_premium, 2)
    )

def simulate_scenario(
    grid_levels: List[GridLevel],
    spot_price: float,
    target_price: float,
    position_size: float = 1.0
) -> Dict[str, Any]:
    spot_pnl = (target_price - spot_price) * position_size

    level_results = []
    total_premium = 0
    total_exercise_loss = 0

    for level in grid_levels:
        premium = level.premium_usd * position_size
        total_premium += premium

        if level.direction == GridDirection.PUT:
            if target_price < level.strike:
                exercise_loss = (level.strike - target_price) * position_size * 100
                net_pnl = premium - exercise_loss
            else:
                net_pnl = premium
        else:
            if target_price > level.strike:
                exercise_loss = (target_price - level.strike) * position_size * 100
                net_pnl = premium - exercise_loss
            else:
                net_pnl = premium

        level_results.append({
            "strike": level.strike,
            "premium": round(premium, 2),
            "net_pnl": round(net_pnl, 2),
            "exercised": target_price < level.strike if level.direction == GridDirection.PUT else target_price > level.strike
        })

    total_pnl = total_premium - total_exercise_loss + (spot_pnl * 100 if spot_pnl > 0 else spot_pnl * 100)
    vs_hold_pnl = total_pnl - (spot_pnl * 100)

    return {
        "target_price": target_price,
        "spot_pnl": round(spot_pnl * 100, 2),
        "total_premium": round(total_premium, 2),
        "level_results": level_results,
        "total_pnl": round(total_pnl, 2),
        "vs_hold_pnl": round(vs_hold_pnl, 2)
    }

def calculate_heatmap_data(
    contracts: List[Dict[str, Any]],
    spot_price: float,
    put_levels: List[GridLevel],
    call_levels: List[GridLevel]
) -> List[Dict[str, Any]]:
    heatmap = []

    for level in put_levels:
        distance_pct = (level.strike - spot_price) / spot_price * 100
        risk_score = max(0, min(100, 50 + distance_pct * 2))

        heatmap.append({
            "strike": level.strike,
            "distance_pct": round(distance_pct, 2),
            "direction": "PUT",
            "risk_level": "high" if risk_score > 70 else "medium" if risk_score > 40 else "low",
            "risk_score": round(risk_score, 1),
            "apr": level.apr,
            "dte": level.dte
        })

    for level in call_levels:
        distance_pct = (spot_price - level.strike) / spot_price * 100
        risk_score = max(0, min(100, 50 + distance_pct * 2))

        heatmap.append({
            "strike": level.strike,
            "distance_pct": round(distance_pct, 2),
            "direction": "CALL",
            "risk_level": "high" if risk_score > 70 else "medium" if risk_score > 40 else "low",
            "risk_score": round(risk_score, 1),
            "apr": level.apr,
            "dte": level.dte
        })

    heatmap.sort(key=lambda x: x["strike"])
    return heatmap
