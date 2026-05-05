"""services/unified_recommendation_engine.py
统一投资推荐引擎 v1.0

不改动现有 19 个规则引擎，作为统一包装层，为所有仪表盘面板提供：
  1. 规则推荐（自动）— 信号灯 + 多因子评分报告
  2. LLM 分析（可选）— Prompt 构建 + 结果格式化

调用链: API → engine.analyze(panel_id, data) → PanelConfig.rule_fns → SignalCalc → ReportBuilder
"""
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 数据模型
# ============================================================

@dataclass
class RuleResult:
    """单个规则函数的输出"""
    name: str = ""
    score: float = 0       # 0-100, 越高越好/越安全
    max_score: float = 100.0
    verdict: str = ""      # 一句话中文判断
    reasoning: List[str] = field(default_factory=list)


# ============================================================
# 信号计算器
# ============================================================

class SignalCalculator:
    """将规则评分聚合为信号灯"""

    BULLISH_THRESHOLD = 60   # >= 60 看多
    BEARISH_THRESHOLD = 40   # <= 40 看空 (exclusive)
    CAUTION_EXTREME = 15     # 任一规则 <= 15 触发 caution

    @staticmethod
    def weighted(results: List[RuleResult],
                 weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """加权平均 → 信号灯"""
        if not results:
            return SignalCalculator._make_signal("neutral", "数据不足", 0)

        if weights is None:
            weights = {r.name: 1.0 / len(results) for r in results}

        total_w = sum(weights.get(r.name, 1.0 / len(results)) for r in results)
        if total_w == 0:
            total_w = 1

        scored = sum(
            r.score * weights.get(r.name, 1.0 / len(results)) / total_w
            for r in results
        )

        any_extreme = any(r.score <= SignalCalculator.CAUTION_EXTREME for r in results)
        if any_extreme:
            return SignalCalculator._make_signal("caution", "存在极端风险因子", round(scored))

        if scored >= SignalCalculator.BULLISH_THRESHOLD:
            return SignalCalculator._make_signal("bullish", "综合评分偏多", round(scored))
        elif scored < SignalCalculator.BEARISH_THRESHOLD:
            return SignalCalculator._make_signal("bearish", "综合评分偏空", round(scored))
        else:
            return SignalCalculator._make_signal("neutral", "综合评分中性", round(scored))

    @staticmethod
    def worst_case(results: List[RuleResult]) -> Dict[str, Any]:
        """最差因子法（风险类面板用）"""
        if not results:
            return SignalCalculator._make_signal("neutral", "数据不足", 0)
        worst = min(results, key=lambda r: r.score)
        if worst.score <= SignalCalculator.CAUTION_EXTREME:
            return SignalCalculator._make_signal("caution", "关键风险因子告警", round(worst.score))
        elif worst.score <= 40:
            return SignalCalculator._make_signal("bearish", "存在显著风险", round(worst.score))
        elif worst.score >= 65:
            return SignalCalculator._make_signal("bullish", "风险可控", round(worst.score))
        else:
            return SignalCalculator._make_signal("neutral", "风险中性", round(worst.score))

    @staticmethod
    def majority(results: List[RuleResult]) -> Dict[str, Any]:
        """多数投票法（行情方向判断用）"""
        if not results:
            return SignalCalculator._make_signal("neutral", "数据不足", 0)
        bulls = sum(1 for r in results if r.score >= 60)
        bears = sum(1 for r in results if r.score <= 40)
        avg_score = round(sum(r.score for r in results) / len(results))
        if bulls > bears and bulls > len(results) / 3:
            return SignalCalculator._make_signal("bullish", "多数信号看多", avg_score)
        elif bears > bulls and bears > len(results) / 3:
            return SignalCalculator._make_signal("bearish", "多数信号看空", avg_score)
        else:
            return SignalCalculator._make_signal("neutral", "信号分歧", avg_score)

    @staticmethod
    def _make_signal(signal: str, text: str, confidence: int) -> Dict[str, Any]:
        emoji_map = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡", "caution": "⚠️"}
        return {
            "signal": signal,
            "signal_emoji": emoji_map.get(signal, "⚪"),
            "signal_text": text,
            "confidence": max(0, min(100, confidence)),
        }


# ============================================================
# 报告构建器
# ============================================================

class ReportBuilder:

    @staticmethod
    def build(results: List[RuleResult], action: str = "") -> Dict[str, Any]:
        """将规则评分组装为结构化报告"""
        if not results:
            return {
                "summary": "",
                "factors": [],
                "logic_chain": [],
                "suggested_action": "",
                "risk_flags": [],
                "refs": [],
            }

        factors = [
            {"name": r.name, "score": round(r.score, 1), "max": r.max_score, "verdict": r.verdict}
            for r in results
        ]

        logic_chain = []
        step = 1
        for r in results:
            for reason in r.reasoning:
                logic_chain.append(f"{step}. {reason}")
                step += 1

        risk_flags = [r.verdict for r in results if r.score <= 25]
        summary_parts = [f"{r.name}: {r.verdict}" for r in results]
        summary = "；".join(summary_parts)

        if not action:
            scores_avg = sum(r.score for r in results) / len(results)
            if scores_avg >= 60:
                action = "综合评分偏多，可考虑积极操作"
            elif scores_avg <= 40:
                action = "综合评分偏空，建议谨慎或观望"
            else:
                action = "综合评分中性，建议等待更明确信号"

        return {
            "summary": summary,
            "factors": factors,
            "logic_chain": logic_chain,
            "suggested_action": action,
            "risk_flags": risk_flags,
            "refs": [],
        }


# ============================================================
# LLM Prompt 构建器
# ============================================================

class _SafeDict(dict):
    """dict subclass that returns {key} for missing keys during str.format_map"""
    def __missing__(self, key):
        return '{' + key + '}'


class LLMPromptBuilder:

    @staticmethod
    def build(panel_id: str, rule_report: Dict[str, Any],
              data_snapshot: Dict[str, Any], currency: str = "BTC") -> Dict[str, str]:
        """为面板构建完整 LLM prompt（复用 panel_analyzers 的模板）"""
        from services.panel_analyzers import get_llm_prompt
        template = get_llm_prompt(panel_id)

        spot = data_snapshot.get("spot", 0)
        dvol = data_snapshot.get("dvol", 0)
        dvol_z = data_snapshot.get("dvol_z", 0)

        rule_scores_text = "\n".join(
            f"- {f['name']}: {f['score']}/100 ({f['verdict']})"
            for f in rule_report.get("factors", [])
        )

        data_text = LLMPromptBuilder._format_data_snapshot(data_snapshot)

        format_args = _SafeDict({
            "currency": currency, "spot": spot, "dvol": dvol, "dvol_z": dvol_z,
            "rule_scores": rule_scores_text, "data_snapshot": data_text,
            "panel_id": panel_id,
            **data_snapshot,
        })

        return {
            "synthesis": template.get("synthesis", "").format_map(format_args),
            "bull_context": template.get("bull_context", "").format_map(format_args),
            "bear_context": template.get("bear_context", "").format_map(format_args),
            "judge_criteria": template.get("judge_criteria", "").format_map(format_args),
        }

    @staticmethod
    def _format_data_snapshot(data: Dict[str, Any]) -> str:
        parts = []
        for k, v in data.items():
            if isinstance(v, (int, float, str)):
                parts.append(f"- {k}: {v}")
            elif isinstance(v, list):
                parts.append(f"- {k}: [{len(v)} items]")
            elif isinstance(v, dict):
                parts.append(f"- {k}: { {sk: sv for sk, sv in list(v.items())[:5]} }")
        return "\n".join(parts)


# ============================================================
# 统一推荐引擎
# ============================================================

class UnifiedRecommendationEngine:
    """统一投资推荐编排引擎"""

    def __init__(self):
        from services.panel_analyzers import PANEL_CONFIGS
        self.panels = PANEL_CONFIGS
        logger.info("UnifiedRecommendationEngine loaded %d panels", len(self.panels))

    def analyze(self, panel_id: str, data: Dict[str, Any],
                currency: str = "BTC") -> Dict[str, Any]:
        """分析单个面板，返回三层标准输出"""
        t0 = time.perf_counter()
        config = self.panels.get(panel_id)
        if not config:
            raise ValueError(f"Unknown panel: {panel_id}")

        results: List[RuleResult] = []
        for rule_def in config.get("rules", []):
            try:
                fn = rule_def["fn"]
                r = fn(data, {})
                results.append(r)
            except Exception as e:
                logger.warning("Rule %s for panel %s failed: %s",
                               rule_def.get("id", "?"), panel_id, e)
                results.append(RuleResult(
                    name=rule_def.get("name", rule_def.get("id", "unknown")),
                    score=0, verdict=f"规则执行异常: {e}", reasoning=[]
                ))

        formula = config.get("signal_formula", "weighted_score")
        if formula == "worst_case":
            signal = SignalCalculator.worst_case(results)
        elif formula == "majority":
            signal = SignalCalculator.majority(results)
        else:
            weights = {rule_def["name"]: rule_def.get("weight", 1.0)
                       for rule_def in config.get("rules", [])}
            signal = SignalCalculator.weighted(results, weights)

        action_template = config.get("default_action", "")
        report = ReportBuilder.build(results, action_template)

        computation_ms = round((time.perf_counter() - t0) * 1000, 2)

        return {
            "panel_id": panel_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data_snapshot": data,
            "signal": signal,
            "report": report,
            "llm_analysis": None,
            "meta": {"rules_version": "1.0", "computation_ms": computation_ms},
        }

    def analyze_all(self, data: Dict[str, Any],
                    currency: str = "BTC") -> Dict[str, Dict[str, Any]]:
        """分析全部注册面板"""
        results = {}
        for panel_id in self.panels:
            try:
                results[panel_id] = self.analyze(panel_id, data, currency)
            except Exception as e:
                logger.error("Panel %s analyze_all failed: %s", panel_id, e)
        return results
