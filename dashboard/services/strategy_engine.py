"""
策略推荐引擎 v2 — 统一过滤、评分、推荐
合并 strategy_calc / unified_strategy_engine / grid_engine 核心逻辑
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from services.shared_calculations import (
    norm_cdf, calc_win_rate, calc_liquidity_score, calc_theta_decay,
    score_to_recommendation_level,
)

logger = logging.getLogger(__name__)


# ── dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class FilterResult:
    contracts: List[dict] = field(default_factory=list)
    total_before: int = 0
    after_hard: int = 0
    after_dvol: int = 0
    after_strategy: int = 0
    dvol_adjustments: Dict[str, str] = field(default_factory=dict)
    dvol_regime: str = "normal"
    empty_reason: str = ""


@dataclass
class ScoreResult:
    total: float = 0.0
    ev: float = 0.0
    apr: float = 0.0
    liquidity: float = 0.0
    theta: float = 0.0
    recommendation: str = "SKIP"


@dataclass
class RecommendationResult:
    success: bool = False
    currency: str = "BTC"
    spot_price: float = 0.0
    dvol_snapshot: Dict[str, Any] = field(default_factory=dict)
    filter_summary: Dict[str, Any] = field(default_factory=dict)
    recommendations: List[dict] = field(default_factory=list)
    timestamp: str = ""


# ── constants ────────────────────────────────────────────────────────────────

MIN_OPEN_INTEREST = 10
MAX_SPREAD_PCT = 25.0
MIN_DTE = 1
MIN_PREMIUM = 0


# ── ContractFilter ───────────────────────────────────────────────────────────

class ContractFilter:
    DVOL_PROFILES = {
        "low":    {"max_delta": 0.40, "min_dte": 7,  "max_dte": 60, "min_apr": 8.0},
        "normal": {"max_delta": 0.30, "min_dte": 14, "max_dte": 45, "min_apr": 10.0},
        "high":   {"max_delta": 0.25, "min_dte": 7,  "max_dte": 30, "min_apr": 12.0},
    }

    def _hard_filter(self, contracts: List[dict]) -> List[dict]:
        return [
            c for c in contracts
            if c.get("open_interest", 0) >= MIN_OPEN_INTEREST
            and c.get("spread_pct", 100) <= MAX_SPREAD_PCT
            and c.get("dte", 0) >= MIN_DTE
            and c.get("premium_usd", 0) > MIN_PREMIUM
        ]

    def _classify_dvol(self, z_score: float) -> str:
        if z_score < -1:
            return "low"
        elif z_score > 1:
            return "high"
        return "normal"

    def get_dvol_adjusted_params(self, overrides: dict, dvol_snapshot: dict) -> dict:
        z_score = dvol_snapshot.get("z_score", 0)
        regime = self._classify_dvol(z_score)
        profile = dict(self.DVOL_PROFILES[regime])
        if overrides:
            profile.update({k: v for k, v in overrides.items() if v is not None})
        profile["regime"] = regime
        return profile

    def _dvol_filter(self, contracts: List[dict], params: dict) -> List[dict]:
        max_delta = params.get("max_delta", 0.30)
        min_dte = params.get("min_dte", 14)
        max_dte = params.get("max_dte", 45)
        return [
            c for c in contracts
            if abs(c.get("delta", 0)) <= max_delta
            and min_dte <= c.get("dte", 0) <= max_dte
        ]

    def strategy_filter(self, contracts: List[dict], mode: str, option_type: str,
                        spot: float, old_strike: Optional[float]) -> List[dict]:
        if mode == "new":
            if option_type == "PUT":
                return [c for c in contracts if c.get("strike", 0) <= spot * 0.95]
            else:
                return [c for c in contracts if c.get("strike", 0) >= spot * 1.05]
        elif mode == "roll":
            if old_strike is None:
                return []
            if option_type == "PUT":
                return [c for c in contracts if c.get("strike", 0) < old_strike * 0.98]
            else:
                return [c for c in contracts if c.get("strike", 0) > old_strike * 1.02]
        elif mode == "wheel":
            return [c for c in contracts if spot * 0.8 <= c.get("strike", 0) <= spot * 1.2]
        elif mode == "grid":
            return contracts
        return contracts

    def filter(self, contracts: List[dict], overrides: dict, dvol_snapshot: dict,
               mode: str = "new", option_type: str = "PUT", spot: float = 0,
               old_strike: Optional[float] = None) -> FilterResult:
        result = FilterResult(contracts=[], total_before=len(contracts))
        after_hard = self._hard_filter(contracts)
        result.after_hard = len(after_hard)

        params = self.get_dvol_adjusted_params(overrides or {}, dvol_snapshot)
        result.dvol_regime = params.get("regime", "normal")
        default = self.DVOL_PROFILES["normal"]
        for key in ("max_delta", "min_dte", "max_dte", "min_apr"):
            if params.get(key) != default.get(key):
                result.dvol_adjustments[key] = (
                    f"{default[key]} → {params[key]} ({result.dvol_regime}波动)"
                )
            else:
                result.dvol_adjustments[key] = f"{params[key]} (未变)"

        after_dvol = self._dvol_filter(after_hard, params)
        result.after_dvol = len(after_dvol)

        after_strategy = self.strategy_filter(after_dvol, mode, option_type, spot, old_strike)
        result.after_strategy = len(after_strategy)

        # Empty result fallback: relax one DVOL tier
        if not after_strategy and after_dvol:
            fallback_params = self._fallback_dvol(params, dvol_snapshot)
            after_dvol_fallback = self._dvol_filter(after_hard, fallback_params)
            after_strategy = self.strategy_filter(
                after_dvol_fallback, mode, option_type, spot, old_strike
            )
            if after_strategy:
                result.dvol_adjustments["_fallback"] = "已放松DVOL一个等级"
                result.after_strategy = len(after_strategy)

        if not after_strategy:
            result.empty_reason = (
                f"当前{result.dvol_regime}波动环境下无符合条件的{option_type}合约"
            )

        result.contracts = after_strategy
        return result

    def _fallback_dvol(self, params: dict, dvol_snapshot: dict) -> dict:
        regime = params.get("regime", "normal")
        fallback_map = {"high": "normal", "normal": "low", "low": "low"}
        fallback_regime = fallback_map[regime]
        fallback = dict(self.DVOL_PROFILES[fallback_regime])
        fallback["regime"] = fallback_regime
        return fallback


# ── StrategyScorer ───────────────────────────────────────────────────────────

class StrategyScorer:
    W_EV = 0.40
    W_APR = 0.25
    W_LIQ = 0.20
    W_THETA = 0.15

    def score(self, contract: dict, spot_price: float,
              margin_ratio: float = 0.2) -> ScoreResult:
        result = ScoreResult()
        strike = contract.get("strike", 0)
        premium = contract.get("premium_usd", 0) or contract.get("premium", 0)
        dte = contract.get("dte", 30)
        delta = abs(contract.get("delta", 0))
        apr = contract.get("apr", 0)
        oi = contract.get("open_interest", 0)
        spread = contract.get("spread_pct", 100)
        option_type = contract.get("option_type", "P")

        margin = max(strike * 0.1, (strike - premium) * margin_ratio)

        result.ev = self._calc_ev(option_type, strike, premium, delta, margin, spot_price)
        result.apr = min(apr / 100.0, 1.0)
        result.liquidity = self._calc_liquidity(oi, spread)
        result.theta = self._calc_theta_efficiency(premium, dte, margin)

        result.total = (
            result.ev * self.W_EV
            + result.apr * self.W_APR
            + result.liquidity * self.W_LIQ
            + result.theta * self.W_THETA
        )
        result.recommendation = self._classify_score(result.total)
        return result

    def _calc_ev(self, option_type, strike, premium, delta, margin, spot):
        if option_type in ("P", "PUT"):
            win_rate = 1 - delta
            max_profit = premium
            max_loss = strike - premium
        else:
            win_rate = delta
            max_profit = premium
            max_loss = spot * 2 - strike
        ev = (win_rate * max_profit) - ((1 - win_rate) * max_loss)
        ev_normalized = ev / margin if margin > 0 else 0
        return max(min(ev_normalized / 0.10, 1.0), 0.0)

    def _calc_liquidity(self, oi, spread_pct):
        oi_score = min(oi / 500.0, 1.0)
        spread_score = max(1 - spread_pct / 10.0, 0.0)
        return oi_score * 0.6 + spread_score * 0.4

    def _calc_theta_efficiency(self, premium, dte, margin):
        if dte <= 0 or margin <= 0:
            return 0.0
        daily_theta = premium / dte
        annualized = daily_theta * 365 / margin
        return min(annualized / 0.50, 1.0)

    def _classify_score(self, score):
        if score >= 0.75:
            return "BEST"
        elif score >= 0.55:
            return "GOOD"
        elif score >= 0.40:
            return "OK"
        elif score >= 0.25:
            return "CAUTION"
        return "SKIP"


# ── StrategyEngine ───────────────────────────────────────────────────────────

class StrategyEngine:
    def __init__(self):
        self.filter = ContractFilter()
        self.scorer = StrategyScorer()

    def recommend(self, contracts, currency, mode, option_type, spot_price,
                  capital, max_results, dvol_snapshot, overrides=None, old_strike=None):
        result = RecommendationResult(currency=currency, spot_price=spot_price)
        z_score = dvol_snapshot.get("z_score", 0)
        result.dvol_snapshot = {
            "current": dvol_snapshot.get("current", 0),
            "z_score": z_score,
            "regime": self.filter._classify_dvol(z_score),
        }

        filter_result = self.filter.filter(
            contracts, overrides or {}, dvol_snapshot,
            mode=mode, option_type=option_type, spot=spot_price, old_strike=old_strike,
        )
        result.filter_summary = {
            "total_contracts": filter_result.total_before,
            "after_hard_filter": filter_result.after_hard,
            "after_dvol_filter": filter_result.after_dvol,
            "after_strategy_filter": filter_result.after_strategy,
            "dvol_adjustments": filter_result.dvol_adjustments,
        }

        if not filter_result.contracts:
            result.success = False
            result.filter_summary["reason"] = "no_contracts"
            result.filter_summary["message"] = filter_result.empty_reason or "当前条件下无可用合约"
            return result

        margin_ratio = (overrides or {}).get("margin_ratio", 0.2)
        scored = []
        for c in filter_result.contracts:
            score = self.scorer.score(c, spot_price, margin_ratio)
            scored.append(self._build_recommendation(c, score, spot_price, capital))

        scored.sort(key=lambda x: x["scores"]["total"], reverse=True)
        result.recommendations = scored[:max_results]
        result.success = True
        result.timestamp = datetime.now(timezone.utc).isoformat()
        return result

    def grid(self, contracts, currency, spot_price, capital, levels,
             interval_pct, dvol_snapshot, overrides=None):
        result = RecommendationResult(currency=currency, spot_price=spot_price)
        result.dvol_snapshot = {
            "current": dvol_snapshot.get("current", 0),
            "z_score": dvol_snapshot.get("z_score", 0),
            "regime": self.filter._classify_dvol(dvol_snapshot.get("z_score", 0)),
        }

        filter_result = self.filter.filter(
            contracts, overrides or {}, dvol_snapshot,
            mode="wheel", option_type="PUT", spot=spot_price,
        )
        result.filter_summary = {
            "total_contracts": filter_result.total_before,
            "after_hard_filter": filter_result.after_hard,
            "after_dvol_filter": filter_result.after_dvol,
            "after_strategy_filter": filter_result.after_strategy,
            "dvol_adjustments": filter_result.dvol_adjustments,
        }

        if not filter_result.contracts:
            result.success = False
            result.filter_summary["reason"] = "no_contracts"
            return result

        grid_strikes = [
            spot_price * (1 - interval_pct * i / 100) for i in range(1, levels + 1)
        ]
        margin_ratio = (overrides or {}).get("margin_ratio", 0.2)
        grid_results = []
        for target_strike in grid_strikes:
            best = self._find_nearest(filter_result.contracts, target_strike)
            if best:
                score = self.scorer.score(best, spot_price, margin_ratio)
                rec = self._build_recommendation(best, score, spot_price, capital)
                rec["grid_level"] = len(grid_results) + 1
                rec["target_strike"] = round(target_strike)
                grid_results.append(rec)

        grid_results.sort(key=lambda x: x["scores"]["total"], reverse=True)
        result.recommendations = grid_results
        result.success = True
        result.timestamp = datetime.now(timezone.utc).isoformat()
        return result

    def _build_recommendation(self, contract, score, spot, capital):
        strike = contract.get("strike", 0)
        premium = contract.get("premium_usd", 0) or contract.get("premium", 0)
        margin = max(strike * 0.1, (strike - premium) * 0.2)
        return {
            "platform": contract.get("platform", ""),
            "option_type": "PUT" if contract.get("option_type", "P") in ("P", "PUT") else "CALL",
            "strike": strike,
            "expiry": contract.get("expiry", ""),
            "dte": contract.get("dte", 0),
            "delta": contract.get("delta", 0),
            "premium_usd": premium,
            "premium_pct": round(premium / strike * 100, 2) if strike > 0 else 0,
            "apr": contract.get("apr", 0),
            "open_interest": contract.get("open_interest", 0),
            "spread_pct": contract.get("spread_pct", 0),
            "margin_required": round(margin, 2),
            "capital_efficiency": round(premium / margin * 100, 1) if margin > 0 else 0,
            "scores": {
                "total": round(score.total, 4),
                "ev": round(score.ev, 4),
                "apr": round(score.apr, 4),
                "liquidity": round(score.liquidity, 4),
                "theta": round(score.theta, 4),
                "recommendation": score.recommendation,
            },
            "risk": {
                "max_loss": round(margin - premium, 2),
                "breakeven": (
                    round(strike - premium, 2)
                    if contract.get("option_type") in ("P", "PUT")
                    else round(strike + premium, 2)
                ),
                "prob_profit": round(1 - abs(contract.get("delta", 0)), 2),
            },
        }

    def _find_nearest(self, contracts, target):
        if not contracts:
            return None
        return min(contracts, key=lambda c: abs(c.get("strike", 0) - target))
