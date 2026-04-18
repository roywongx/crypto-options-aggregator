"""
统一策略推荐引擎 v1.0
整合策略计算器（滚仓/新建）和网格策略引擎

核心设计：
1. 统一筛选器 - 所有模式共享
2. 统一评分框架 - 模式特定的评分算法
3. 统一输出模型 - 标准化的推荐结果
4. 三种模式：Roll（滚仓）、New（新建）、Grid（网格）
"""
import math
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

from services.shared_calculations import (
    calc_grid_score, score_to_recommendation_level,
    calc_win_rate, black_scholes_price, calc_liquidity_score,
    score_to_rating, norm_cdf
)
from services.risk_framework import RiskFramework


class StrategyMode(Enum):
    ROLL = "roll"
    NEW = "new"
    GRID = "grid"


class OptionType(Enum):
    PUT = "PUT"
    CALL = "CALL"


@dataclass
class StrategyParams:
    """统一策略参数"""
    currency: str = "BTC"
    mode: StrategyMode = StrategyMode.NEW
    option_type: OptionType = OptionType.PUT
    reserve_capital: float = 50000.0
    target_max_delta: float = 0.35
    min_dte: int = 7
    max_dte: int = 90
    margin_ratio: float = 0.20

    old_strike: Optional[float] = None
    old_qty: float = 1.0
    close_cost_total: float = 0.0
    max_qty_multiplier: float = 3.0

    target_apr: float = 200.0

    put_count: int = 5
    call_count: int = 0
    min_apr: float = 15.0


@dataclass
class StrategyMetrics:
    """策略指标（统一）"""
    apr: float = 0.0
    roi: float = 0.0
    win_rate: float = 50.0
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    max_profit: float = 0.0
    max_loss: float = 0.0
    margin_required: float = 0.0
    net_credit: float = 0.0
    gross_credit: float = 0.0
    capital_efficiency: float = 0.0
    distance_pct: float = 0.0
    liquidity_score: float = 0.0
    bs_price: Optional[float] = None
    theta_decay: Optional[float] = None

    new_qty: float = 0.0
    break_even_qty: float = 0.0
    effective_premium: float = 0.0

    recommendation_level: str = "OK"
    reason: str = ""


@dataclass
class StrategyRecommendation:
    """统一策略推荐结果"""
    symbol: str
    platform: str
    strike: float
    expiry: str
    dte: int
    option_type: str
    premium_usd: float
    iv: float
    open_interest: int
    volume: int
    score: float
    metrics: StrategyMetrics
    risk_assessment: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


class ContractFilter:
    """统一合约筛选器"""

    @staticmethod
    def filter_by_type(contracts: List[Dict], option_type: str) -> List[Dict]:
        """按期权类型筛选"""
        target = option_type.upper()
        valid_types = {"P", "PUT"} if target == "PUT" else {"C", "CALL"}
        return [c for c in contracts if c.get("option_type", "").upper() in valid_types]

    @staticmethod
    def filter_by_dte(contracts: List[Dict], min_dte: int, max_dte: int) -> List[Dict]:
        """按到期天数筛选"""
        return [c for c in contracts if min_dte <= c.get("dte", 0) <= max_dte]

    @staticmethod
    def filter_by_delta(contracts: List[Dict], max_delta: float) -> List[Dict]:
        """按 Delta 筛选"""
        return [c for c in contracts if abs(c.get("delta", 1)) <= max_delta]

    @staticmethod
    def filter_by_apr(contracts: List[Dict], min_apr: float) -> List[Dict]:
        """按 APR 筛选"""
        return [c for c in contracts if c.get("apr", 0) >= min_apr]

    @staticmethod
    def filter_by_premium(contracts: List[Dict], min_premium: float = 0) -> List[Dict]:
        """按权利金筛选"""
        return [c for c in contracts if c.get("premium_usd", c.get("premium", 0)) > min_premium]

    @staticmethod
    def filter_by_oi(contracts: List[Dict], min_oi: float = 10) -> List[Dict]:
        """按未平仓合约筛选"""
        return [c for c in contracts if c.get("open_interest", c.get("oi", 0)) >= min_oi]

    @staticmethod
    def filter_roll_candidates(
        contracts: List[Dict],
        old_strike: float,
        option_type: str
    ) -> List[Dict]:
        """
        滚仓模式候选筛选
        """
        is_put = option_type.upper() == "PUT"
        candidates = []
        for c in contracts:
            strike = c.get("strike", 0)
            if is_put and strike >= old_strike:
                continue
            if not is_put and strike <= old_strike:
                continue
            candidates.append(c)
        return candidates


class UnifiedScorer:
    """统一评分框架"""

    @staticmethod
    def score_roll(
        candidate: Dict,
        spot: float,
        close_cost: float,
        new_qty: float,
        max_qty: int,
        slippage_pct: float = 0.02,
        safety_buffer: float = 0.05
    ) -> float:
        """滚仓模式评分"""
        prem = candidate.get("premium_usd", 0)
        effective_prem = prem * (1 - slippage_pct)
        gross_credit = new_qty * effective_prem
        net_credit = gross_credit - close_cost

        if net_credit <= 0:
            return 0.0

        strike = candidate.get("strike", 0)
        margin_ratio = candidate.get("_margin_ratio", 0.20)
        margin_req = new_qty * strike * margin_ratio

        if margin_req <= 0:
            return 0.0

        capital_efficiency = net_credit / margin_req
        delta = abs(candidate.get("delta", 0))
        delta_penalty = max(0, (delta - 0.25) * 2)
        dte = candidate.get("dte", 30)
        dte_weight = min(1.0, dte / 45.0)
        rf_modifier = RiskFramework.get_score_modifier(strike, spot)

        score = capital_efficiency * (1 - delta_penalty) * (0.5 + 0.5 * dte_weight) * rf_modifier
        return score

    @staticmethod
    def score_new(
        candidate: Dict,
        spot: float,
        target_apr: float
    ) -> float:
        """新建模式评分"""
        apr = candidate.get("apr", 0)
        delta = abs(candidate.get("delta", 0))
        dte = candidate.get("dte", 30)
        strike = candidate.get("strike", 0)
        oi = candidate.get("open_interest", candidate.get("oi", 0))
        volume = candidate.get("volume_24h", candidate.get("volume", 0))

        apr_score = min(apr / target_apr, 1.0)
        risk_score = max(0, 1 - delta * 2)

        if 14 <= dte <= 21:
            dte_score = 1.0
        elif dte < 14:
            dte_score = 0.5 + (dte / 14.0) * 0.5
        else:
            dte_score = max(0.3, 1.0 - (dte - 21) / 30.0)

        liquidity_score = min((oi / 500.0 + volume / 100.0), 1.0)
        rf_modifier = RiskFramework.get_score_modifier(strike, spot)

        score = (apr_score * 0.40 + risk_score * 0.25 + dte_score * 0.20 + liquidity_score * 0.15) * rf_modifier
        return score

    @staticmethod
    def score_grid(
        apr: float,
        distance_pct: float,
        oi: int,
        volume: int,
        dte: int
    ) -> float:
        """网格模式评分（复用共享计算）"""
        return calc_grid_score(apr, distance_pct, oi, volume, dte)


class UnifiedStrategyEngine:
    """统一策略推荐引擎"""

    SLIPPAGE_PCT = 0.02
    SAFETY_BUFFER_PCT = 0.05
    MIN_NET_CREDIT_USD = 10.0

    def __init__(self):
        self.filter = ContractFilter()
        self.scorer = UnifiedScorer()

    def _extract_contract_data(self, c: Dict) -> Dict[str, Any]:
        """标准化合约数据（仅包含 StrategyRecommendation 需要的字段）"""
        return {
            "symbol": c.get("symbol", ""),
            "platform": c.get("platform", ""),
            "strike": c.get("strike", 0),
            "expiry": c.get("expiry", ""),
            "dte": c.get("dte", 0),
            "option_type": c.get("option_type", ""),
            "premium_usd": c.get("premium_usd", c.get("premium", 0)),
            "iv": c.get("iv", 50),
            "open_interest": c.get("open_interest", c.get("oi", 0)),
            "volume": c.get("volume_24h", c.get("volume", 0))
        }

    def _calculate_margin(self, strike: float, premium: float, option_type: str, margin_ratio: float) -> float:
        """计算保证金要求"""
        if option_type.upper() == "PUT":
            return strike * margin_ratio
        else:
            return premium * 10

    def recommend_roll(
        self,
        contracts: List[Dict],
        params: StrategyParams,
        spot: float
    ) -> List[StrategyRecommendation]:
        """滚仓模式推荐"""
        if params.old_strike is None:
            raise ValueError("滚仓模式需要提供 old_strike")

        candidates = self.filter.filter_by_type(contracts, params.option_type.value)
        candidates = self.filter.filter_by_dte(candidates, params.min_dte, params.max_dte)
        candidates = self.filter.filter_by_delta(candidates, params.target_max_delta)
        candidates = self.filter.filter_roll_candidates(candidates, params.old_strike, params.option_type.value)

        results = []
        for c in candidates:
            prem = c.get("premium_usd", c.get("premium", 0))
            if prem <= 0:
                continue

            effective_prem = prem * (1 - self.SLIPPAGE_PCT)

            break_even_qty = math.ceil(params.close_cost_total / effective_prem)
            min_qty_for_profit = math.ceil(
                params.close_cost_total / effective_prem * (1 + self.SAFETY_BUFFER_PCT)
            )
            max_allowed_qty = int(params.old_qty * params.max_qty_multiplier)

            if break_even_qty > max_allowed_qty:
                continue

            new_qty = max(min_qty_for_profit, break_even_qty)
            strike = c.get("strike", 0)
            margin_req = self._calculate_margin(strike, prem, params.option_type.value, params.margin_ratio) * new_qty

            if margin_req > params.reserve_capital:
                continue

            gross_credit = new_qty * effective_prem
            net_credit = gross_credit - params.close_cost_total

            if net_credit < self.MIN_NET_CREDIT_USD:
                continue

            c["_margin_ratio"] = params.margin_ratio
            score = self.scorer.score_roll(
                c, spot, params.close_cost_total, new_qty,
                max_allowed_qty, self.SLIPPAGE_PCT, self.SAFETY_BUFFER_PCT
            )

            dte = c.get("dte", 30)
            annualized_roi = (net_credit / margin_req * 365 / max(dte, 1)) if margin_req > 0 else 0
            delta = abs(c.get("delta", 0))
            capital_efficiency = net_credit / margin_req if margin_req > 0 else 0

            option_type_short = "P" if params.option_type == OptionType.PUT else "C"
            win_rate = calc_win_rate(option_type_short, "sell", strike, prem, spot, c.get("iv", 50), dte)
            bs_data = black_scholes_price(option_type_short, strike, spot, dte, c.get("iv", 50))

            contract_data = self._extract_contract_data(c)

            results.append(StrategyRecommendation(
                **contract_data,
                score=round(score, 4),
                metrics=StrategyMetrics(
                    apr=c.get("apr", 0),
                    roi=round(annualized_roi, 1),
                    win_rate=round(win_rate * 100, 1),
                    delta=round(delta, 3),
                    gamma=bs_data.get("gamma", 0),
                    theta=bs_data.get("theta", 0),
                    vega=bs_data.get("vega", 0),
                    max_profit=round(net_credit, 2),
                    max_loss=0.0,
                    margin_required=round(margin_req, 2),
                    net_credit=round(net_credit, 2),
                    gross_credit=round(gross_credit, 2),
                    capital_efficiency=round(capital_efficiency, 4),
                    distance_pct=round((strike - spot) / spot * 100, 1),
                    liquidity_score=min(100, c.get("open_interest", 0) / 5),
                    bs_price=bs_data.get("premium"),
                    theta_decay=bs_data.get("theta"),
                    new_qty=new_qty,
                    break_even_qty=break_even_qty,
                    effective_premium=round(effective_prem, 2)
                ),
                risk_assessment={
                    "rf_modifier": round(RiskFramework.get_score_modifier(strike, spot), 2),
                    "delta_penalty": round(max(0, (delta - 0.25) * 2), 2)
                },
                extra={"mode": "roll"}
            ))

        results.sort(key=lambda x: (x.score, x.metrics.net_credit, -x.metrics.delta), reverse=True)
        return results[:15]

    def recommend_new(
        self,
        contracts: List[Dict],
        params: StrategyParams,
        spot: float
    ) -> List[StrategyRecommendation]:
        """新建模式推荐"""
        candidates = self.filter.filter_by_type(contracts, params.option_type.value)
        candidates = self.filter.filter_by_dte(candidates, params.min_dte, params.max_dte)
        candidates = self.filter.filter_by_delta(candidates, params.target_max_delta)
        candidates = self.filter.filter_by_premium(candidates)

        results = []
        for c in candidates:
            prem = c.get("premium_usd", c.get("premium", 0))
            if prem <= 0:
                continue

            strike = c.get("strike", 0)
            margin_req = self._calculate_margin(strike, prem, params.option_type.value, params.margin_ratio)

            if margin_req > params.reserve_capital:
                continue

            gross_credit = prem
            apr = c.get("apr", 0)
            dte = c.get("dte", 30)
            annualized_roi = (gross_credit / margin_req * 365 / max(dte, 1)) if margin_req > 0 else 0
            delta = abs(c.get("delta", 0))
            capital_efficiency = gross_credit / margin_req if margin_req > 0 else 0

            score = self.scorer.score_new(c, spot, params.target_apr)

            option_type_short = "P" if params.option_type == OptionType.PUT else "C"
            win_rate = calc_win_rate(option_type_short, "sell", strike, prem, spot, c.get("iv", 50), dte)
            bs_data = black_scholes_price(option_type_short, strike, spot, dte, c.get("iv", 50))

            contract_data = self._extract_contract_data(c)

            results.append(StrategyRecommendation(
                **contract_data,
                score=round(score, 4),
                metrics=StrategyMetrics(
                    apr=apr,
                    roi=round(annualized_roi, 1),
                    win_rate=round(win_rate * 100, 1),
                    delta=round(delta, 3),
                    gamma=bs_data.get("gamma", 0),
                    theta=bs_data.get("theta", 0),
                    vega=bs_data.get("vega", 0),
                    max_profit=round(gross_credit, 2),
                    max_loss=0.0,
                    margin_required=round(margin_req, 2),
                    gross_credit=round(gross_credit, 2),
                    capital_efficiency=round(capital_efficiency, 4),
                    distance_pct=round((strike - spot) / spot * 100, 1),
                    liquidity_score=min(100, c.get("open_interest", 0) / 5),
                    bs_price=bs_data.get("premium"),
                    theta_decay=bs_data.get("theta")
                ),
                risk_assessment={
                    "rf_modifier": round(RiskFramework.get_score_modifier(strike, spot), 2)
                },
                extra={"mode": "new"}
            ))

        results.sort(key=lambda x: (x.score, x.metrics.roi), reverse=True)
        return results[:15]

    def recommend_grid(
        self,
        contracts: List[Dict],
        params: StrategyParams,
        spot: float
    ) -> Dict[str, Any]:
        """网格模式推荐"""
        from services.grid_engine import (
            calculate_grid_levels, get_vol_direction_signal,
            GridDirection
        )

        put_levels = calculate_grid_levels(
            contracts, spot, GridDirection.PUT,
            params.put_count, params.min_dte, params.max_dte, params.min_apr
        )

        call_levels = calculate_grid_levels(
            contracts, spot, GridDirection.CALL,
            params.call_count, params.min_dte, params.max_dte, params.min_apr
        )

        vol_signal = get_vol_direction_signal(contracts, params.currency)
        total_premium = sum(p.premium_usd for p in put_levels) + sum(c.premium_usd for c in call_levels)

        def _grid_level_to_recommendation(level, direction: str) -> StrategyRecommendation:
            contract_data = self._extract_contract_data({
                "symbol": "",
                "platform": "",
                "strike": level.strike,
                "expiry": level.expiry,
                "dte": level.dte,
                "option_type": level.direction.value,
                "premium_usd": level.premium_usd,
                "iv": level.iv,
                "delta": level.delta,
                "open_interest": level.oi,
                "volume": level.volume,
                "apr": level.apr
            })

            rec_level = level.recommendation.name if hasattr(level.recommendation, 'name') else str(level.recommendation)
            score = calc_grid_score(level.apr, level.distance_pct, level.oi, level.volume, level.dte)

            return StrategyRecommendation(
                **contract_data,
                score=round(score, 4),
                metrics=StrategyMetrics(
                    apr=level.apr,
                    roi=round((level.premium_usd / (level.strike * params.margin_ratio)) * (365 / max(level.dte, 1)) * 100, 1),
                    win_rate=getattr(level, 'win_rate', 50.0),
                    delta=level.delta,
                    distance_pct=round(level.distance_pct, 2),
                    liquidity_score=round(level.liquidity_score * 100, 1),
                    bs_price=getattr(level, 'bs_price', None),
                    theta_decay=getattr(level, 'theta_decay', None),
                    recommendation_level=rec_level,
                    reason=level.reason
                ),
                risk_assessment={"mode": "grid"},
                extra={
                    "mode": "grid",
                    "direction": direction,
                    "suggested_position_pct": self._calc_suggested_position(rec_level)
                }
            )

        put_recs = [_grid_level_to_recommendation(l, "PUT") for l in put_levels]
        call_recs = [_grid_level_to_recommendation(l, "CALL") for l in call_levels]

        return {
            "currency": params.currency,
            "spot_price": spot,
            "timestamp": datetime.utcnow().isoformat(),
            "put_levels": put_recs,
            "call_levels": call_recs,
            "dvol_signal": vol_signal.signal,
            "recommended_ratio": vol_signal.suggested_ratio,
            "total_potential_premium": round(total_premium, 2),
            "vol_signal": {
                "dvol_percentile": vol_signal.dvol_percentile if hasattr(vol_signal, 'dvol_percentile') else None,
                "signal": vol_signal.signal,
                "reason": vol_signal.reason
            }
        }

    @staticmethod
    def _calc_suggested_position(recommendation_level: str) -> int:
        """根据推荐等级计算建议仓位百分比"""
        position_map = {
            "BEST": 20,
            "GOOD": 15,
            "OK": 10,
            "CAUTION": 5,
            "SKIP": 0
        }
        return position_map.get(recommendation_level, 0)

    def execute(
        self,
        contracts: List[Dict],
        params: StrategyParams,
        spot: float
    ) -> Dict[str, Any]:
        """执行统一策略推荐"""
        if params.mode == StrategyMode.ROLL:
            results = self.recommend_roll(contracts, params, spot)
            return {
                "success": True,
                "mode": "roll",
                "params": {
                    "currency": params.currency,
                    "option_type": params.option_type.value,
                    "old_strike": params.old_strike,
                    "old_qty": params.old_qty,
                    "close_cost_total": params.close_cost_total,
                    "reserve_capital": params.reserve_capital
                },
                "plans": [self._rec_to_dict(r) for r in results],
                "meta": {
                    "total_contracts_scanned": len(contracts),
                    "plans_found": len(results)
                }
            }
        elif params.mode == StrategyMode.NEW:
            results = self.recommend_new(contracts, params, spot)
            return {
                "success": True,
                "mode": "new",
                "params": {
                    "currency": params.currency,
                    "option_type": params.option_type.value,
                    "reserve_capital": params.reserve_capital,
                    "target_apr": params.target_apr
                },
                "plans": [self._rec_to_dict(r) for r in results],
                "meta": {
                    "total_contracts_scanned": len(contracts),
                    "plans_found": len(results)
                }
            }
        elif params.mode == StrategyMode.GRID:
            return self.recommend_grid(contracts, params, spot)
        else:
            raise ValueError(f"未知的策略模式: {params.mode}")

    def _rec_to_dict(self, rec: StrategyRecommendation) -> Dict[str, Any]:
        """将 StrategyRecommendation 转换为字典"""
        return {
            "symbol": rec.symbol,
            "platform": rec.platform,
            "strike": rec.strike,
            "expiry": rec.expiry,
            "dte": rec.dte,
            "option_type": rec.option_type,
            "premium_usd": rec.premium_usd,
            "iv": rec.iv,
            "open_interest": rec.open_interest,
            "volume": rec.volume,
            "score": rec.score,
            "metrics": {
                "apr": rec.metrics.apr,
                "roi": rec.metrics.roi,
                "win_rate": rec.metrics.win_rate,
                "delta": rec.metrics.delta,
                "gamma": rec.metrics.gamma,
                "theta": rec.metrics.theta,
                "vega": rec.metrics.vega,
                "max_profit": rec.metrics.max_profit,
                "max_loss": rec.metrics.max_loss,
                "margin_required": rec.metrics.margin_required,
                "net_credit": rec.metrics.net_credit,
                "gross_credit": rec.metrics.gross_credit,
                "capital_efficiency": rec.metrics.capital_efficiency,
                "distance_pct": rec.metrics.distance_pct,
                "liquidity_score": rec.metrics.liquidity_score,
                "bs_price": rec.metrics.bs_price,
                "theta_decay": rec.metrics.theta_decay,
                "new_qty": rec.metrics.new_qty if hasattr(rec.metrics, 'new_qty') else None,
                "break_even_qty": rec.metrics.break_even_qty if hasattr(rec.metrics, 'break_even_qty') else None,
                "effective_premium": rec.metrics.effective_premium if hasattr(rec.metrics, 'effective_premium') else None,
                "recommendation_level": rec.metrics.recommendation_level if hasattr(rec.metrics, 'recommendation_level') else None,
                "reason": rec.metrics.reason if hasattr(rec.metrics, 'reason') else None
            },
            "risk_assessment": rec.risk_assessment,
            "extra": rec.extra
        }
