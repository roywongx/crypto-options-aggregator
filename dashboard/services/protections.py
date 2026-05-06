"""
Protection Plugins System v2.0 — Freqtrade Protections fully aligned

Dual-mode StoplossGuard:
- proactive: checks if spot breached position stop-loss price (our original)
- reactive: counts historical stoploss exits → lock trading (Freqtrade native)

Guards:
- StoplossGuard (dual mode)
- MaxDrawdownGuard (global circuit breaker)
- ConsecutiveLossGuard (loss streak → cooldown)
- OvertradingGuard (max positions cap)
- VaRGuard (portfolio VaR threshold)
- ConcentrationGuard (strike clustering risk)
"""
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


@dataclass
class ProtectionResult:
    tripped: bool = False
    guard_name: str = ""
    reason: str = ""
    suggested_action: str = ""
    severity: str = "info"
    until: Optional[str] = None  # ISO datetime when lock expires (Freqtrade-compatible)
    lock_side: str = "*"
    metrics: Dict = field(default_factory=dict)


class IProtection:
    """Base guard interface (Freqtrade IProtection aligned)"""

    name: str = "base"
    has_global_stop: bool = False
    has_local_stop: bool = False

    def short_desc(self) -> str:
        return self.name

    def check(self, **kwargs) -> ProtectionResult:
        raise NotImplementedError

    def _calculate_lock_end(self, minutes: int = 60) -> datetime:
        """Calculate when the lock expires (Freqtrade compatible)"""
        return datetime.now(timezone.utc) + timedelta(minutes=minutes)


# ── Guard 1: StoplossGuard (dual mode) ─────────────────────────

class StoplossGuard(IProtection):
    """Max stoploss count guard (Freqtrade native reactive mode) +
    Dynamic stop-loss price check (proactive mode)."""

    name = "stoploss_guard"
    has_global_stop = True
    has_local_stop = True

    def __init__(self, trade_limit: int = 10, lookback_hours: int = 24, profit_limit: float = 0.0):
        self._trade_limit = trade_limit
        self._lookback_hours = lookback_hours
        self._profit_limit = profit_limit
        self._trade_history: List[Dict] = []

    def record_trade(self, pnl_usd: float, exit_reason: str = "", timestamp: Optional[datetime] = None):
        """Record a closed trade for reactive stoploss counting."""
        ts = timestamp or datetime.now(timezone.utc)
        self._trade_history.append({
            "pnl_usd": pnl_usd,
            "exit_reason": exit_reason,
            "timestamp": ts,
        })
        # Prune old records
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._lookback_hours)
        self._trade_history = [t for t in self._trade_history if t["timestamp"] > cutoff]

    def _count_stoplosses(self) -> int:
        """Count stoploss exits within lookback window (Freqtrade reactive mode)."""
        stoploss_reasons = {"STOP_LOSS", "TRAILING_STOP_LOSS", "LIQUIDATION", "STOPLOSS_ON_EXCHANGE"}
        return sum(
            1 for t in self._trade_history
            if t["exit_reason"] in stoploss_reasons
            and t["pnl_usd"] <= self._profit_limit
        )

    def check(self, mode: str = "reactive", **kwargs) -> ProtectionResult:
        """
        Dual-mode check.

        reactive (Freqtrade native): count stoploss exits → lock if > trade_limit
        proactive (our original): check if spot breached calculated stop-loss prices
        """
        if mode == "proactive":
            return self._check_proactive(**kwargs)
        return self._check_reactive(**kwargs)

    def _check_reactive(self, **kwargs) -> ProtectionResult:
        """Reactive: count historical stoplosses (Freqtrade aligned)."""
        count = self._count_stoplosses()
        if count >= self._trade_limit:
            until = self._calculate_lock_end(60)
            return ProtectionResult(
                tripped=True, guard_name=self.name,
                reason=f"{count} 次止损退出超过阈值 {self._trade_limit} (过去{self._lookback_hours}h)",
                suggested_action=f"暂停所有开仓至 {until.strftime('%H:%M')}",
                severity="critical", until=until.isoformat(), lock_side="*",
                metrics={"stoploss_count": count, "threshold": self._trade_limit},
            )
        return ProtectionResult(guard_name=self.name, metrics={"stoploss_count": count, "status": "normal"})

    def _check_proactive(self, positions: List[Dict] = None, spot: float = 0, dvol: float = 50, **kwargs) -> ProtectionResult:
        """Proactive: compute stop-loss prices and check if breached."""
        from config import config
        if not positions or spot <= 0:
            return ProtectionResult(guard_name=self.name)

        tripped = []
        for p in positions:
            strike = p.get("strike", 0)
            if strike <= 0:
                continue
            option_type = str(p.get("option_type", "")).upper()
            if option_type in ("PUT", "P"):
                dvol_factor = max(dvol / 100.0, 0.15)
                stop_ratio = min(dvol_factor * config.STOP_LOSS_DVOL_MULTIPLIER, 0.50)
                stop_price = strike * (1.0 - stop_ratio)
                if spot <= stop_price:
                    loss_est = (strike - spot) - p.get("premium_usd", 0)
                    tripped.append({
                        "strike": strike, "stop_price": round(stop_price, 0),
                        "spot": spot, "estimated_loss": round(max(0, loss_est), 2),
                        "reason": f"${spot:,.0f} < ${stop_price:,.0f} (DVOL={dvol:.0f})",
                    })
            elif option_type in ("CALL", "C"):
                stop_price = strike * 1.15
                if spot >= stop_price:
                    tripped.append({
                        "strike": strike, "stop_price": round(stop_price, 0),
                        "spot": spot,
                        "reason": f"${spot:,.0f} > ${stop_price:,.0f} (卖CALL风险)",
                    })

        if tripped:
            return ProtectionResult(
                tripped=True, guard_name=self.name,
                reason=f"{len(tripped)} 个仓位触发止损",
                suggested_action="平仓触发止损的仓位",
                severity="critical",
                metrics={"stopped_positions": tripped},
            )
        return ProtectionResult(guard_name=self.name, metrics={"positions_checked": len(positions)})


# ── Guard 2: MaxDrawdownGuard (Freqtrade aligned) ──────────────

class MaxDrawdownGuard(IProtection):
    """Global max drawdown circuit breaker (Freqtrade MaxDrawdown aligned)."""

    name = "max_drawdown_guard"
    has_global_stop = True

    def check(self, current_equity: float = 0, peak_equity: float = 0,
              trade_limit: int = 1, **kwargs) -> ProtectionResult:
        from config import config
        if peak_equity <= 0 or current_equity <= 0:
            return ProtectionResult(guard_name=self.name)

        drawdown = (peak_equity - current_equity) / peak_equity
        threshold = config.MAX_DRAWDOWN_THRESHOLD

        if drawdown >= threshold:
            until = self._calculate_lock_end(120)
            return ProtectionResult(
                tripped=True, guard_name=self.name,
                reason=f"回撤 {drawdown:.1%} > {threshold:.0%} (${current_equity:,.0f} / ${peak_equity:,.0f})",
                suggested_action=f"全对锁仓至 {until.strftime('%H:%M')}，评估风险敞口",
                severity="critical", until=until.isoformat(), lock_side="*",
                metrics={"drawdown": round(drawdown, 4), "current": current_equity, "peak": peak_equity},
            )
        return ProtectionResult(guard_name=self.name, metrics={"drawdown": round(drawdown, 4), "status": "normal"})


# ── Guard 3: ConsecutiveLossGuard ──────────────────────────────

class ConsecutiveLossGuard(IProtection):
    """Consecutive loss cooldown (Freqtrade CooldownPeriod aligned)."""

    name = "consecutive_loss_guard"
    has_global_stop = True

    def __init__(self):
        self._loss_streak: int = 0
        self._cooldown_until: Optional[datetime] = None

    def record_trade(self, pnl_usd: float):
        if pnl_usd < 0:
            self._loss_streak += 1
        else:
            self._loss_streak = 0
            self._cooldown_until = None

    def check(self, **kwargs) -> ProtectionResult:
        from config import config
        threshold = config.MAX_CONSECUTIVE_LOSSES

        if self._loss_streak >= threshold:
            if self._cooldown_until is None:
                self._cooldown_until = self._calculate_lock_end(240)  # 4 hour cooldown
            if datetime.now(timezone.utc) < self._cooldown_until:
                return ProtectionResult(
                    tripped=True, guard_name=self.name,
                    reason=f"连续亏损 {self._loss_streak} 次 (上限 {threshold})",
                    suggested_action=f"冷却期至 {self._cooldown_until.strftime('%H:%M')}，检查策略参数",
                    severity="critical", until=self._cooldown_until.isoformat(), lock_side="*",
                    metrics={"loss_streak": self._loss_streak, "cooldown_until": self._cooldown_until.isoformat()},
                )
            else:
                self._loss_streak = 0
                self._cooldown_until = None
        return ProtectionResult(guard_name=self.name, metrics={"loss_streak": self._loss_streak, "status": "normal"})

    def reset(self):
        self._loss_streak = 0
        self._cooldown_until = None


# ── Guard 4: OvertradingGuard ──────────────────────────────────

class OvertradingGuard(IProtection):
    """Max positions cap guard."""

    name = "overtrading_guard"
    has_global_stop = False
    has_local_stop = True

    def check(self, open_positions: int = 0, **kwargs) -> ProtectionResult:
        from config import config
        if open_positions > config.MAX_POSITIONS_OPEN:
            return ProtectionResult(
                tripped=True, guard_name=self.name,
                reason=f"持仓 {open_positions} > 上限 {config.MAX_POSITIONS_OPEN}",
                suggested_action="减少新开仓，优先平掉低APR仓位",
                severity="warning",
                metrics={"open_positions": open_positions, "max_allowed": config.MAX_POSITIONS_OPEN},
            )
        return ProtectionResult(guard_name=self.name, metrics={"open_positions": open_positions})


# ── Guard 5: VaRGuard ──────────────────────────────────────────

class VaRGuard(IProtection):
    """Portfolio VaR threshold guard."""

    name = "var_guard"
    has_global_stop = True

    def check(self, positions: List[Dict] = None, spot: float = 0, iv: float = 50, **kwargs) -> ProtectionResult:
        from services.portfolio_risk import PortfolioRisk
        if not positions or spot <= 0:
            return ProtectionResult(guard_name=self.name)

        risk = PortfolioRisk.calc_var(positions, spot, iv, confidence="95")
        var_pct = risk.var_95_pct

        if var_pct > 5.0:
            return ProtectionResult(
                tripped=True, guard_name=self.name,
                reason=f"VaR {var_pct:.2f}% > 5% 危险线 (95% CVaR=${risk.cvar_95:,.0f})",
                suggested_action="减仓或增加对冲，降低组合 Delta 敞口",
                severity="critical",
                metrics={"var_95": risk.var_95, "var_pct": var_pct, "cvar_95": risk.cvar_95},
            )
        elif var_pct > 2.0:
            return ProtectionResult(
                tripped=True, guard_name=self.name,
                reason=f"VaR {var_pct:.2f}% > 2% 预警线",
                suggested_action="谨慎新开仓，偏好低 Delta 合约",
                severity="warning",
                metrics={"var_95": risk.var_95, "var_pct": var_pct},
            )
        return ProtectionResult(guard_name=self.name, metrics={"var_pct": var_pct, "status": "normal"})


# ── Guard 6: ConcentrationGuard ────────────────────────────────

class ConcentrationGuard(IProtection):
    """Strike concentration risk guard."""

    name = "concentration_guard"
    has_global_stop = True

    def check(self, positions: List[Dict] = None, spot: float = 0, **kwargs) -> ProtectionResult:
        from services.portfolio_risk import PortfolioRisk
        from config import config
        if not positions or spot <= 0:
            return ProtectionResult(guard_name=self.name)

        strikes = [p.get("strike", 0) for p in positions if p.get("strike", 0) > 0]
        concentration, max_ratio = PortfolioRisk._check_concentration(strikes, spot)

        if concentration == "DANGER":
            return ProtectionResult(
                tripped=True, guard_name=self.name,
                reason=f"行权价集中度 {max_ratio:.0%} > {config.STRIKE_CONCENTRATION_DANGER:.0%}",
                suggested_action="分散行权价至不同档位，避免同一价位过度集中",
                severity="critical",
                metrics={"max_band_ratio": max_ratio, "concentration": concentration, "strikes": len(strikes)},
            )
        elif concentration == "CAUTION":
            return ProtectionResult(
                tripped=True, guard_name=self.name,
                reason=f"行权价集中度 {max_ratio:.0%} > {config.STRIKE_CONCENTRATION_CAUTION:.0%}",
                suggested_action="考虑在不同价位分散持仓",
                severity="warning",
                metrics={"max_band_ratio": max_ratio, "concentration": concentration},
            )
        return ProtectionResult(guard_name=self.name, metrics={"concentration": concentration, "max_band_ratio": max_ratio})


# ── Protection Manager ─────────────────────────────────────────

class ProtectionManager:
    """Unified protection manager — runs all guards"""

    def __init__(self):
        self.stoploss = StoplossGuard()
        self.drawdown = MaxDrawdownGuard()
        self.consecutive = ConsecutiveLossGuard()
        self.overtrading = OvertradingGuard()
        self.var = VaRGuard()
        self.concentration = ConcentrationGuard()

        self.guards: List[IProtection] = [
            self.stoploss, self.drawdown, self.consecutive,
            self.overtrading, self.var, self.concentration,
        ]

    def record_trade(self, pnl_usd: float, exit_reason: str = ""):
        """Record a trade result for all relevant guards."""
        self.stoploss.record_trade(pnl_usd, exit_reason)
        self.consecutive.record_trade(pnl_usd)

    def check_stoploss_proactive(self, positions: List[Dict], spot: float, dvol: float) -> ProtectionResult:
        """Convenience: run proactive stoploss check on active positions."""
        return self.stoploss.check(mode="proactive", positions=positions, spot=spot, dvol=dvol)

    def check_all(
        self,
        positions: List[Dict],
        spot: float,
        dvol: float,
        iv: float,
        current_equity: float,
        peak_equity: float,
        stoploss_mode: str = "reactive",
    ) -> Dict[str, ProtectionResult]:
        """Run all guards. Returns dict keyed by guard name."""
        results = {}
        # Stoploss runs with selected mode
        results[self.stoploss.name] = self.stoploss.check(
            mode=stoploss_mode, positions=positions, spot=spot, dvol=dvol
        )

        kwargs = dict(
            current_equity=current_equity, peak_equity=peak_equity,
            positions=positions, spot=spot, iv=iv,
            open_positions=len(positions),
        )
        for guard in self.guards:
            if guard.name == "stoploss_guard":
                continue  # already handled
            try:
                results[guard.name] = guard.check(**kwargs)
            except Exception as e:
                logger.warning("Guard %s failed: %s", guard.name, e)
                results[guard.name] = ProtectionResult(
                    guard_name=guard.name, reason=f"Error: {e}", severity="info"
                )
        return results

    @staticmethod
    def summarize(results: Dict[str, ProtectionResult]) -> Dict:
        """Summarize all guard results."""
        tripped = {k: v for k, v in results.items() if v.tripped}
        critical = {k: v for k, v in tripped.items() if v.severity == "critical"}
        warnings = {k: v for k, v in tripped.items() if v.severity == "warning"}
        return {
            "all_clear": len(tripped) == 0,
            "tripped_count": len(tripped),
            "critical_count": len(critical),
            "warning_count": len(warnings),
            "guards_tripped": list(tripped.keys()),
            "critical_guards": list(critical.keys()),
            "actions_needed": list(dict.fromkeys(v.suggested_action for v in tripped.values())),
            "locks": [
                {"guard": k, "until": v.until}
                for k, v in tripped.items() if v.until
            ],
        }

    def reset_all(self):
        """Reset all stateful guards."""
        self.consecutive.reset()
