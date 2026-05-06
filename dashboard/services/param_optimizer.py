"""
Hyper Parameter Optimizer v2.0 — Freqtrade Hyperopt-compatible

Two engines:
- grid_search: exhaustive over discrete space (720 combos max)
- bayesian_search: TPE/Gaussian Process via scikit-optimize (with graceful fallback)

Loss functions (Freqtrade-aligned):
- sortino_loss:  penalizes downside deviation only  (preferred for options selling)
- calmar_loss:   penalizes max drawdown vs return  (conservative)
- sharpe_loss:   penalizes total volatility        (balanced)
- weighted_loss: our original composite score     (backward-compatible)
"""
import math
import time
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable, Tuple
from itertools import product

logger = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    success: bool = False
    best_params: Dict[str, Any] = field(default_factory=dict)
    best_score: float = 0.0
    top_n: List[Dict] = field(default_factory=list)
    total_combos_tested: int = 0
    elapsed_seconds: float = 0.0
    search_space: Dict[str, Any] = field(default_factory=dict)
    objective: str = "sortino_loss"
    loss_value: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    max_drawdown_pct: float = 0.0
    note: str = ""
    method: str = "grid"


# ── Search space definitions ──────────────────────────────────────
DEFAULT_SEARCH_SPACE: Dict[str, List] = {
    "max_delta":    [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40],
    "min_dte":      [3, 7, 14, 21],
    "max_dte":      [21, 28, 35, 45, 60],
    "min_apr":      [5.0, 8.0, 12.0, 15.0, 20.0, 25.0, 30.0],
    "margin_ratio": [0.15, 0.18, 0.20, 0.22, 0.25],
}

QUICK_SPACE: Dict[str, List] = {
    "max_delta":    [0.15, 0.25, 0.35],
    "min_dte":      [7, 14, 21],
    "max_dte":      [21, 35, 45],
    "min_apr":      [8.0, 15.0, 25.0],
    "margin_ratio": [0.18, 0.20, 0.22],
}

# Bayesian search bounds — continuous ranges
BAYESIAN_BOUNDS: List[Tuple[float, float]] = [
    (0.10, 0.40),    # max_delta
    (3.0, 21.0),     # min_dte
    (21.0, 60.0),    # max_dte
    (5.0, 30.0),     # min_apr
    (0.15, 0.25),    # margin_ratio
]
BAYESIAN_DIM_NAMES = ["max_delta", "min_dte", "max_dte", "min_apr", "margin_ratio"]

# ── Loss functions (Freqtrade-aligned) ──────────────────────────


def sortino_loss(returns: List[float], annualization_factor: float = 365.0) -> float:
    """Sortino ratio loss: penalizes downside deviation only.
    Returns negative Sortino → minimize = better Sortino.
    For options selling, this is preferred because upside volatility is profitable.
    """
    if len(returns) < 2:
        return 999.0
    mean_ret = sum(returns) / len(returns)
    downside = [r for r in returns if r < 0]
    if not downside:
        return -mean_ret * 100  # no downside → great score
    if len(downside) == 1:
        # Single downside point → weak estimate
        downside_std = (downside[0] ** 2) ** 0.5
    else:
        downside_std = math.sqrt(sum(r ** 2 for r in downside) / len(downside))
    if downside_std <= 0:
        return 999.0
    sortino = (mean_ret / downside_std) * math.sqrt(annualization_factor)
    return -sortino


def calmar_loss(returns: List[float], max_drawdown: float) -> float:
    """Calmar ratio loss: annualized return / max drawdown.
    Returns negative Calmar → minimize = better Calmar.
    Conservative: heavy penalty for large drawdowns.
    """
    if max_drawdown <= 0 or len(returns) < 2:
        return 999.0
    mean_ret = sum(returns) / len(returns)
    annualized_return = mean_ret * 365
    calmar = annualized_return / max_drawdown
    return -calmar


def sharpe_loss(returns: List[float], annualization_factor: float = 365.0) -> float:
    """Sharpe ratio loss: penalizes total volatility equally.
    Returns negative Sharpe → minimize = better Sharpe.
    """
    if len(returns) < 2:
        return 999.0
    mean_ret = sum(returns) / len(returns)
    if len(returns) > 1:
        std_ret = math.sqrt(sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1))
    else:
        std_ret = 1.0
    if std_ret <= 0:
        return 999.0
    sharpe = (mean_ret / std_ret) * math.sqrt(annualization_factor)
    return -sharpe


class ParamOptimizer:
    """Bayesian + Grid hyperparameter optimizer (Freqtrade Hyperopt compatible)"""

    def __init__(self):
        self._cache: Dict[str, List[Dict]] = {}

    # ── Scoring helpers ──────────────────────────────────────────

    def _evaluate_plans(
        self,
        plans: List[Dict],
        objective: str = "sortino_loss",
    ) -> Dict[str, float]:
        """Extract metrics from strategy plans for loss calculation."""
        if not plans:
            return {"loss": 999.0, "sharpe": 0, "sortino": 0, "calmar": 0, "max_dd": 0}

        # Build pseudo-return series from APR and win_rate
        # Daily expected return = APR / 365 * win_rate
        returns = []
        for p in plans:
            m = p.get("metrics", p) if isinstance(p, dict) else {}
            apr = m.get("apr", 0)
            win_rate = m.get("win_rate", 50) / 100.0
            # Daily expected return (decimal)
            daily_ret = (apr / 100.0) * win_rate / 365.0
            returns.append(daily_ret)

        if not returns:
            return {"loss": 999.0, "sharpe": 0, "sortino": 0, "calmar": 0, "max_dd": 0}

        mean_ret = sum(returns) / len(returns)
        sharpe_val = 0
        sortino_val = 0
        calmar_val = 0

        if len(returns) > 1:
            total_std = math.sqrt(sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1))
            sharpe_val = (mean_ret / total_std * math.sqrt(365)) if total_std > 0 else 0

        downside = [r for r in returns if r < 0]
        if downside and len(downside) > 1:
            d_std = math.sqrt(sum(r ** 2 for r in downside) / len(downside))
            sortino_val = (mean_ret / d_std * math.sqrt(365)) if d_std > 0 else 0
        elif not downside:
            sortino_val = mean_ret * 365 * 10

        # Max drawdown proxy: worst APR in the batch
        worst_ret = min(returns)
        max_dd = abs(worst_ret) * 100
        calmar_val = (mean_ret * 365) / max_dd if max_dd > 0 else 0

        # Compute loss
        if objective == "calmar_loss":
            loss = calmar_loss(returns, max_dd)
        elif objective == "sharpe_loss":
            loss = sharpe_loss(returns)
        elif objective == "sortino_loss":
            loss = sortino_loss(returns)
        else:  # weighted_score — invert so lower = better
            avg_score = sum(
                p.get("score", p.get("metrics", {}).get("roi", 0))
                if isinstance(p, dict) else 0
                for p in plans
            ) / len(plans)
            loss = -avg_score

        return {
            "loss": round(loss, 6),
            "sharpe": round(sharpe_val, 3),
            "sortino": round(sortino_val, 3),
            "calmar": round(calmar_val, 4),
            "max_dd": round(max_dd, 4),
        }

    def _run_single(
        self,
        params_dict: Dict,
        contracts: List[Dict],
        spot: float,
        option_type: str,
        reserve_capital: float,
        target_apr: float,
        objective: str,
    ) -> Optional[Dict]:
        """Execute one parameter combination and return scored result."""
        from services.unified_strategy_engine import (
            UnifiedStrategyEngine, StrategyParams, StrategyMode, OptionType
        )
        engine = UnifiedStrategyEngine()
        ot = OptionType.PUT if option_type.upper() == "PUT" else OptionType.CALL

        try:
            sp = StrategyParams(
                currency="BTC", mode=StrategyMode.NEW, option_type=ot,
                reserve_capital=reserve_capital,
                target_max_delta=params_dict["max_delta"],
                min_dte=params_dict["min_dte"],
                max_dte=params_dict["max_dte"],
                min_apr=params_dict["min_apr"],
                margin_ratio=params_dict["margin_ratio"],
                target_apr=target_apr, put_count=5,
            )
            result = engine.execute(contracts, sp, spot)
            plans = result.get("plans", [])
            if not plans:
                return None

            metrics = self._evaluate_plans(plans, objective)
            return {
                "params": params_dict,
                "loss": metrics["loss"],
                "plan_count": len(plans),
                "avg_apr": round(sum(p["metrics"]["apr"] for p in plans) / len(plans), 1),
                "avg_win_rate": round(sum(p["metrics"]["win_rate"] for p in plans) / len(plans), 1),
                "sharpe": metrics["sharpe"],
                "sortino": metrics["sortino"],
                "calmar": metrics["calmar"],
                "max_dd": metrics["max_dd"],
                "top_strike": plans[0]["strike"],
                "top_premium": plans[0]["premium_usd"],
            }
        except Exception as e:
            logger.debug("Eval failed for %s: %s", params_dict, e)
            return None

    # ── Grid search ──────────────────────────────────────────────

    def grid_search(
        self,
        contracts: List[Dict],
        spot: float,
        option_type: str = "PUT",
        reserve_capital: float = 100000.0,
        target_apr: float = 200.0,
        search_space: Optional[Dict[str, List]] = None,
        top_n: int = 20,
        objective: str = "sortino_loss",
    ) -> OptimizationResult:
        """Exhaustive grid search over discrete parameter space."""
        space = search_space or DEFAULT_SEARCH_SPACE
        keys = list(space.keys())
        values = list(space.values())
        combos = list(product(*values))

        max_combos = 720
        if len(combos) > max_combos:
            sampled = []
            for v in values:
                n = len(v)
                if n <= 3:
                    sampled.append(v)
                else:
                    sampled.append([v[0], v[n // 2], v[-1]])
            combos = list(product(*sampled))
            logger.info("Grid reduced %d → %d combos", len(list(product(*values))), len(combos))

        logger.info("Grid search: %d combos, obj=%s", len(combos), objective)
        start_time = time.time()
        all_results: List[Dict] = []

        for combo in combos:
            pd = dict(zip(keys, combo))
            if pd["min_dte"] >= pd["max_dte"]:
                continue
            r = self._run_single(pd, contracts, spot, option_type, reserve_capital, target_apr, objective)
            if r:
                all_results.append(r)

        all_results.sort(key=lambda x: x["loss"])
        top_results = all_results[:top_n]
        elapsed = time.time() - start_time

        best = top_results[0] if top_results else {"loss": 999, "params": {}, "sharpe": 0, "sortino": 0, "calmar": 0, "max_dd": 0}
        return OptimizationResult(
            success=len(top_results) > 0,
            best_params=best["params"],
            best_score=best.get("avg_apr", 0),
            top_n=top_results,
            total_combos_tested=len(all_results),
            elapsed_seconds=round(elapsed, 2),
            objective=objective,
            loss_value=best["loss"],
            sharpe=best.get("sharpe", 0),
            sortino=best.get("sortino", 0),
            calmar=best.get("calmar", 0),
            max_drawdown_pct=best.get("max_dd", 0),
            note=f"Grid search: {len(space)}D, {len(all_results)} combos",
            method="grid",
        )

    # ── Bayesian optimization ────────────────────────────────────

    def bayesian_search(
        self,
        contracts: List[Dict],
        spot: float,
        option_type: str = "PUT",
        reserve_capital: float = 100000.0,
        target_apr: float = 200.0,
        objective: str = "sortino_loss",
        n_calls: int = 50,
        n_initial_points: int = 15,
    ) -> OptimizationResult:
        """Bayesian optimization via scikit-optimize (TPE/Gaussian Process).
        Falls back to grid search if scikit-optimize is not installed.
        """
        try:
            import skopt
            from skopt import gp_minimize
            from skopt.space import Real, Integer
            from skopt.utils import use_named_args
            _HAS_SKOPT = True
        except ImportError:
            logger.warning("scikit-optimize not installed, falling back to grid search")
            return self.grid_search(contracts, spot, option_type, reserve_capital, target_apr, objective=objective)

        logger.info("Bayesian search: %d calls, obj=%s", n_calls, objective)
        start_time = time.time()

        # Define search space
        dimensions = [
            Real(0.10, 0.40, name="max_delta"),
            Integer(3, 21, name="min_dte"),
            Integer(21, 60, name="max_dte"),
            Real(5.0, 30.0, name="min_apr"),
            Real(0.15, 0.25, name="margin_ratio"),
        ]

        all_evals: List[Dict] = []

        @use_named_args(dimensions)
        def objective_func(max_delta, min_dte, max_dte, min_apr, margin_ratio) -> float:
            if min_dte >= max_dte:
                return 999.0
            pd = {
                "max_delta": float(max_delta),
                "min_dte": int(min_dte),
                "max_dte": int(max_dte),
                "min_apr": round(float(min_apr), 1),
                "margin_ratio": round(float(margin_ratio), 2),
            }
            r = self._run_single(pd, contracts, spot, option_type, reserve_capital, target_apr, objective)
            if r:
                all_evals.append(r)
                return r["loss"]
            return 999.0

        try:
            result = gp_minimize(
                objective_func,
                dimensions,
                n_calls=n_calls,
                n_initial_points=n_initial_points,
                random_state=42,
                n_jobs=1,
                noise=0.01,
            )
        except Exception as e:
            logger.warning("Bayesian optimization failed: %s, using grid fallback", e)
            return self.grid_search(contracts, spot, option_type, reserve_capital, target_apr, objective=objective)

        elapsed = time.time() - start_time
        all_evals.sort(key=lambda x: x["loss"])
        top = all_evals[:20]

        # Round integer params
        best_p = result.x
        best_params = {
            "max_delta": round(float(best_p[0]), 2),
            "min_dte": int(best_p[1]),
            "max_dte": int(best_p[2]),
            "min_apr": round(float(best_p[3]), 1),
            "margin_ratio": round(float(best_p[4]), 2),
        }

        best_eval = top[0] if top else {"loss": 999, "sharpe": 0, "sortino": 0, "calmar": 0, "max_dd": 0, "avg_apr": 0}
        return OptimizationResult(
            success=len(top) > 0,
            best_params=best_params,
            best_score=best_eval.get("avg_apr", 0),
            top_n=top,
            total_combos_tested=len(all_evals),
            elapsed_seconds=round(elapsed, 2),
            objective=objective,
            loss_value=best_eval["loss"],
            sharpe=best_eval.get("sharpe", 0),
            sortino=best_eval.get("sortino", 0),
            calmar=best_eval.get("calmar", 0),
            max_drawdown_pct=best_eval.get("max_dd", 0),
            note=f"Bayesian (GP): {n_calls} calls, best loss={best_eval['loss']:.3f}",
            method="bayesian_gp",
        )

    # ── Convenience methods  ─────────────────────────────────────

    def quick_search(
        self,
        contracts: List[Dict],
        spot: float,
        option_type: str = "PUT",
    ) -> OptimizationResult:
        """Quick grid search (~81 combos)"""
        return self.grid_search(contracts, spot, option_type, search_space=QUICK_SPACE, top_n=10)

    def auto(
        self,
        contracts: List[Dict],
        spot: float,
        option_type: str = "PUT",
        mode: str = "bayesian",
    ) -> OptimizationResult:
        """Auto-select best optimization method."""
        if mode == "bayesian":
            return self.bayesian_search(contracts, spot, option_type)
        elif mode == "full":
            return self.grid_search(contracts, spot, option_type, objective="sortino_loss")
        else:
            return self.quick_search(contracts, spot, option_type)

    @staticmethod
    def suggest_from_dvol(dvol: float) -> Dict[str, Any]:
        """Heuristic parameter suggestion based on DVOL level."""
        if dvol > 70:
            return {
                "max_delta": 0.20, "min_dte": 7, "max_dte": 21,
                "min_apr": 25.0, "margin_ratio": 0.22,
                "rationale": "DVOL 高位，收紧参数降低风险"
            }
        elif dvol > 50:
            return {
                "max_delta": 0.30, "min_dte": 14, "max_dte": 35,
                "min_apr": 15.0, "margin_ratio": 0.20,
                "rationale": "DVOL 中位，标准参数"
            }
        else:
            return {
                "max_delta": 0.40, "min_dte": 7, "max_dte": 60,
                "min_apr": 8.0, "margin_ratio": 0.18,
                "rationale": "DVOL 低位，放宽参数追求收益"
            }
