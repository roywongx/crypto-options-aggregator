# 统一投资推荐系统 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为仪表盘 16 个面板构建统一推荐流水线（规则推荐 + 可选 LLM 深度分析），通过 UnifiedRecommendationEngine 包装现有引擎并标准化输出。

**Architecture:** 新建 `UnifiedRecommendationEngine` 作为编排中心（不改动现有19个引擎），通过面板配置注册表路由到对应规则集，输出标准化三层格式（信号灯→规则报告→LLM分析）。前端通过 `recommendations.js` 统一渲染。

**Tech Stack:** Python/FastAPI (后端), JavaScript (前端), SQLite (缓存)

**前置状态确认**：LLM Analyst 路由已注册到 main.py（`llm_analyst_router`），`llm_config` 和 `llm_analysis_results` 表已存在于 db/schema.py。

---

### Task 1: 创建数据模型和核心引擎

**Files:**
- Create: `services/unified_recommendation_engine.py`
- Test: `tests/test_unified_recommendation.py`

- [ ] **Step 1: 编写数据模型 + 信号计算 + 报告构建的测试**

```python
"""tests/test_unified_recommendation.py"""
import pytest
from services.unified_recommendation_engine import (
    RuleResult, Signal, PanelReport, PanelRecommendation,
    SignalCalculator, ReportBuilder, UnifiedRecommendationEngine
)


class TestRuleResult:
    def test_create_rule_result(self):
        r = RuleResult(name="test", score=85, max=100, verdict="正面", reasoning=["理由1"])
        assert r.name == "test"
        assert r.score == 85
        assert r.verdict == "正面"

    def test_rule_result_defaults(self):
        r = RuleResult(name="minimal")
        assert r.score == 0
        assert r.verdict == ""
        assert r.reasoning == []


class TestSignalCalculator:
    def test_weighted_score_bullish(self):
        results = [
            RuleResult(name="r1", score=80, verdict="正面"),
            RuleResult(name="r2", score=70, verdict="正面"),
        ]
        weights = {"r1": 0.6, "r2": 0.4}
        signal = SignalCalculator.weighted(results, weights)
        assert signal["signal"] == "bullish"
        assert signal["signal_emoji"] == "🟢"
        assert 70 <= signal["confidence"] <= 90

    def test_weighted_score_bearish(self):
        results = [
            RuleResult(name="r1", score=20, verdict="负面"),
            RuleResult(name="r2", score=15, verdict="负面"),
        ]
        signal = SignalCalculator.weighted(results)
        assert signal["signal"] == "bearish"
        assert signal["signal_emoji"] == "🔴"

    def test_weighted_score_neutral(self):
        results = [
            RuleResult(name="r1", score=55, verdict="中性"),
        ]
        signal = SignalCalculator.weighted(results)
        assert signal["signal"] == "neutral"
        assert signal["signal_emoji"] == "🟡"

    def test_worst_case_picks_min(self):
        results = [
            RuleResult(name="r1", score=80, verdict="正面"),
            RuleResult(name="r2", score=25, verdict="高风险"),
        ]
        signal = SignalCalculator.worst_case(results)
        assert signal["signal"] in ("bearish", "caution")

    def test_majority(self):
        results = [
            RuleResult(name="r1", score=70),
            RuleResult(name="r2", score=65),
            RuleResult(name="r3", score=30),
        ]
        signal = SignalCalculator.majority(results)
        assert signal["signal"] == "bullish"

    def test_signal_threshold_boundaries(self):
        boundaries = [
            (75, "bullish"),
            (60, "bullish"),
            (59, "neutral"),
            (45, "neutral"),
            (40, "neutral"),
            (39, "bearish"),
            (20, "bearish"),
        ]
        for score, expected in boundaries:
            results = [RuleResult(name="t", score=score)]
            s = SignalCalculator.weighted(results)
            assert s["signal"] == expected, f"score={score} expected {expected} got {s['signal']}"

    def test_caution_when_any_extreme_low(self):
        results = [
            RuleResult(name="r1", score=75, verdict="正常"),
            RuleResult(name="r2", score=10, verdict="极度危险"),
        ]
        signal = SignalCalculator.worst_case(results)
        assert signal["signal"] == "caution"


class TestReportBuilder:
    def test_build_report_with_factors(self):
        results = [
            RuleResult(name="因子A", score=80, max=100, verdict="良好", reasoning=["A1", "A2"]),
            RuleResult(name="因子B", score=60, max=100, verdict="适中", reasoning=["B1"]),
        ]
        report = ReportBuilder.build(results, action="测试建议")
        assert "因子A" in report["summary"]
        assert "因子B" in report["summary"]
        assert len(report["factors"]) == 2
        assert report["factors"][0]["name"] == "因子A"
        assert report["factors"][0]["score"] == 80
        assert report["suggested_action"] == "测试建议"
        assert len(report["logic_chain"]) >= 2
        assert isinstance(report["risk_flags"], list)

    def test_build_report_no_action(self):
        results = [RuleResult(name="A", score=50)]
        report = ReportBuilder.build(results)
        assert len(report["suggested_action"]) > 0  # auto-generated

    def test_build_report_empty_results(self):
        report = ReportBuilder.build([])
        assert report["summary"] == ""
        assert report["factors"] == []


class TestUnifiedRecommendationEngine:
    def test_engine_init_loads_panels(self):
        engine = UnifiedRecommendationEngine()
        assert len(engine.panels) >= 16
        assert "risk_command_center" in engine.panels
        assert "iv_term_structure" in engine.panels

    def test_analyze_returns_valid_structure(self):
        engine = UnifiedRecommendationEngine()
        result = engine.analyze("metric_cards", {
            "spot": 90000, "dvol": 62, "dvol_z": 0.8,
            "fear_greed": 35, "trend_strength": 0.6,
        })
        assert result["panel_id"] == "metric_cards"
        assert "signal" in result
        assert "report" in result
        assert result["llm_analysis"] is None
        assert "meta" in result
        signal = result["signal"]
        assert signal["signal"] in ("bullish", "bearish", "neutral", "caution")
        assert 0 <= signal["confidence"] <= 100

    def test_analyze_unknown_panel_raises(self):
        engine = UnifiedRecommendationEngine()
        with pytest.raises(ValueError, match="Unknown panel"):
            engine.analyze("nonexistent_panel", {})

    def test_analyze_all_returns_all_panels(self):
        engine = UnifiedRecommendationEngine()
        results = engine.analyze_all({
            "spot": 90000, "dvol": 62, "dvol_z": 0.8, "fear_greed": 35,
            "contracts": [], "large_trades": [], "pcr": 1.2,
            "funding_rate": 0.01, "max_pain": 88000, "risk_score": 45,
            "onchain": {}, "iv_surface": {}, "greeks": {},
        })
        assert len(results) >= 16
        for pid, r in results.items():
            assert r["panel_id"] == pid
            assert "signal" in r
            assert "report" in r

    def test_llm_prompt_builder(self):
        from services.unified_recommendation_engine import LLMPromptBuilder
        report = {
            "summary": "测试摘要",
            "factors": [{"name": "F1", "score": 80, "verdict": "好"}],
            "logic_chain": ["L1"],
            "suggested_action": "建议",
            "risk_flags": ["风险1"],
        }
        prompt = LLMPromptBuilder.build(
            panel_id="iv_term_structure",
            rule_report=report,
            data_snapshot={"spot": 90000, "dvol": 62},
            currency="BTC",
        )
        assert "BTC" in prompt["synthesis"]
        assert "90000" in prompt["synthesis"]
        assert "iv_term_structure" in prompt["synthesis"]
        assert "bull_context" in prompt
        assert "bear_context" in prompt
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_unified_recommendation.py -v --tb=short`
Expected: ALL FAIL with "ModuleNotFoundError" 或类似错误

- [ ] **Step 3: 创建核心引擎文件**

```python
"""services/unified_recommendation_engine.py
统一投资推荐引擎 v1.0

不改动现有 19 个规则引擎，作为统一包装层，为所有仪表盘面板提供：
  1. 规则推荐（自动）— 信号灯 + 多因子评分报告
  2. LLM 分析（可选）— Prompt 构建 + 结果格式化

调用链: API → engine.analyze(panel_id, data) → PanelConfig.rule_fns → SignalCalc → ReportBuilder
"""
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
    max: float = 100.0
    verdict: str = ""      # 一句话中文判断
    reasoning: List[str] = field(default_factory=list)


# ============================================================
# 信号计算器
# ============================================================

class SignalCalculator:
    """将规则评分聚合为信号灯"""

    BULLISH_THRESHOLD = 60   # >= 60 看多
    BEARISH_THRESHOLD = 40   # <= 40 看空
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
            return SignalCalculator._make_signal("caution", "⚠️ 存在极端风险因子", int(scored))

        if scored >= SignalCalculator.BULLISH_THRESHOLD:
            return SignalCalculator._make_signal("bullish", "🟢 综合评分偏多", int(scored))
        elif scored <= SignalCalculator.BEARISH_THRESHOLD:
            return SignalCalculator._make_signal("bearish", "🔴 综合评分偏空", int(scored))
        else:
            return SignalCalculator._make_signal("neutral", "🟡 综合评分中性", int(scored))

    @staticmethod
    def worst_case(results: List[RuleResult]) -> Dict[str, Any]:
        """最差因子法（风险类面板用）"""
        if not results:
            return SignalCalculator._make_signal("neutral", "数据不足", 0)
        worst = min(results, key=lambda r: r.score)
        if worst.score <= 20:
            return SignalCalculator._make_signal("caution", "⚠️ 关键风险因子告警", int(worst.score))
        elif worst.score <= 40:
            return SignalCalculator._make_signal("bearish", "🔴 存在显著风险", int(worst.score))
        elif worst.score >= 65:
            return SignalCalculator._make_signal("bullish", "🟢 风险可控", int(worst.score))
        else:
            return SignalCalculator._make_signal("neutral", "🟡 风险中性", int(worst.score))

    @staticmethod
    def majority(results: List[RuleResult]) -> Dict[str, Any]:
        """多数投票法（行情方向判断用）"""
        if not results:
            return SignalCalculator._make_signal("neutral", "数据不足", 0)
        bulls = sum(1 for r in results if r.score >= 60)
        bears = sum(1 for r in results if r.score <= 40)
        avg_score = int(sum(r.score for r in results) / len(results))
        if bulls > bears and bulls > len(results) / 3:
            return SignalCalculator._make_signal("bullish", "🟢 多数信号看多", avg_score)
        elif bears > bulls and bears > len(results) / 3:
            return SignalCalculator._make_signal("bearish", "🔴 多数信号看空", avg_score)
        else:
            return SignalCalculator._make_signal("neutral", "🟡 信号分歧", avg_score)

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
            {"name": r.name, "score": round(r.score, 1), "max": r.max, "verdict": r.verdict}
            for r in results
        ]

        logic_chain = []
        for i, r in enumerate(results, 1):
            for reason in r.reasoning:
                logic_chain.append(f"{i}. {reason}")

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

        synthesis = template.get("synthesis", "").format(
            currency=currency, spot=spot, dvol=dvol, dvol_z=dvol_z,
            rule_scores=rule_scores_text, data_snapshot=data_text,
            **data_snapshot,
        )

        return {
            "synthesis": synthesis,
            "bull_context": template.get("bull_context", ""),
            "bear_context": template.get("bear_context", ""),
            "judge_criteria": template.get("judge_criteria", ""),
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
        config = self.panels.get(panel_id)
        if not config:
            raise ValueError(f"Unknown panel: {panel_id}")

        # 运行规则函数
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

        # 聚合信号
        formula = config.get("signal_formula", "weighted_score")
        if formula == "worst_case":
            signal = SignalCalculator.worst_case(results)
        elif formula == "majority":
            signal = SignalCalculator.majority(results)
        else:
            weights = {rule_def["name"]: rule_def.get("weight", 1.0)
                       for rule_def in config.get("rules", [])}
            signal = SignalCalculator.weighted(results, weights)

        # 构建报告
        action_template = config.get("default_action", "")
        report = ReportBuilder.build(results, action_template)

        return {
            "panel_id": panel_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data_snapshot": data,
            "signal": signal,
            "report": report,
            "llm_analysis": None,
            "meta": {"rules_version": "1.0", "computation_ms": 0},
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_unified_recommendation.py -v`
Expected: ALL PASS (7 test functions + class tests)

- [ ] **Step 5: 提交**

```bash
git add services/unified_recommendation_engine.py tests/test_unified_recommendation.py
git commit -m "feat: add UnifiedRecommendationEngine core with data models, signal calculator, report builder, and LLM prompt builder

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: 创建面板分析器配置 (panel_analyzers.py)

**Files:**
- Create: `services/panel_analyzers.py`

- [ ] **Step 1: 创建面板配置模块（16个面板的规则配置 + LLM prompt 模板）**

```python
"""services/panel_analyzers.py
16个面板的规则配置 + 规则函数 + LLM prompt 模板

每个面板定义:
  - data_sources: 数据来源列表
  - rules: [规则函数引用]
  - signal_formula: weighted_score | worst_case | majority
  - default_action: 默认操作建议
  - llm_prompt_template_id: 对应 LLM prompt 模板 key
"""
from typing import Dict, Any, List, Optional
from services.unified_recommendation_engine import RuleResult


# ============================================================
# 通用规则函数
# ============================================================

def _safe_float(v: Any, default: float = 0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def calc_dvol_signal(data: dict, cache: dict) -> RuleResult:
    """DVOL 波动率信号"""
    dvol = _safe_float(data.get("dvol", 0))
    dvol_z = _safe_float(data.get("dvol_z", 0))
    signal = data.get("dvol_signal", "normal")

    if dvol <= 0:
        return RuleResult(name="DVOL信号", score=50, verdict="数据缺失")

    if dvol < 50 and dvol_z < -1.0:
        return RuleResult(name="DVOL信号", score=85, max=100,
                          verdict=f"低波动率(IV={dvol})，卖方有利",
                          reasoning=[f"DVOL={dvol}处于低位", f"Z-Score={dvol_z}<-1，显著低于均值"])
    elif dvol > 70 and dvol_z > 2.0:
        return RuleResult(name="DVOL信号", score=15, max=100,
                          verdict=f"高波动率(IV={dvol})，卖方风险极高",
                          reasoning=[f"DVOL={dvol}>70 恐慌区间", f"Z-Score={dvol_z}>2，极端偏离"])
    elif dvol > 70:
        return RuleResult(name="DVOL信号", score=25, max=100,
                          verdict=f"偏高波动率(IV={dvol})，需谨慎",
                          reasoning=[f"DVOL={dvol}>70"])
    elif dvol > 50:
        return RuleResult(name="DVOL信号", score=60, max=100,
                          verdict=f"中等波动率(IV={dvol})，正常操作",
                          reasoning=[f"DVOL={dvol} 处于中位区间"])
    else:
        return RuleResult(name="DVOL信号", score=75, max=100,
                          verdict=f"较低波动率(IV={dvol})，卖方窗口",
                          reasoning=[f"DVOL={dvol}<50"])


def calc_sentiment(data: dict, cache: dict) -> RuleResult:
    """市场情绪（恐惧贪婪指数）"""
    fg = _safe_float(data.get("fear_greed", 50))
    if fg <= 0:
        return RuleResult(name="市场情绪", score=50, verdict="数据缺失")
    if fg <= 25:
        return RuleResult(name="市场情绪", score=80, max=100,
                          verdict=f"极度恐惧({fg})，历史表明是买入机会",
                          reasoning=[f"恐贪指数={fg}≤25", "极度恐惧常对应市场底部"])
    elif fg <= 45:
        return RuleResult(name="市场情绪", score=65, max=100,
                          verdict=f"偏恐惧({fg})，可逐步建仓",
                          reasoning=[f"恐贪指数={fg}", "恐惧区间往往酝酿机会"])
    elif fg >= 75:
        return RuleResult(name="市场情绪", score=25, max=100,
                          verdict=f"极度贪婪({fg})，市场过热风险",
                          reasoning=[f"恐贪指数={fg}≥75", "极度贪婪常对应市场顶部"])
    elif fg >= 60:
        return RuleResult(name="市场情绪", score=45, max=100,
                          verdict=f"偏贪婪({fg})，注意回调风险",
                          reasoning=[f"恐贪指数={fg}"])
    else:
        return RuleResult(name="市场情绪", score=55, max=100,
                          verdict=f"中性情绪({fg})",
                          reasoning=[f"恐贪指数={fg} 处于中性区间"])


def calc_trend_strength(data: dict, cache: dict) -> RuleResult:
    """价格趋势强度"""
    trend = _safe_float(data.get("trend_strength", 0))
    spot = _safe_float(data.get("spot", 0))
    if trend > 0.5:
        return RuleResult(name="趋势强度", score=70, max=100,
                          verdict=f"上升趋势(强度={trend:.2f})，顺势卖PUT",
                          reasoning=[f"趋势强度={trend:.2f}>0.5", f"现货={spot}"])
    elif trend < -0.3:
        return RuleResult(name="趋势强度", score=30, max=100,
                          verdict=f"下降趋势(强度={trend:.2f})，卖PUT需更大安全边际",
                          reasoning=[f"趋势强度={trend:.2f}<-0.3"])
    else:
        return RuleResult(name="趋势强度", score=50, max=100,
                          verdict=f"趋势不明朗(强度={trend:.2f})",
                          reasoning=[f"趋势强度={trend:.2f}"])


def calc_term_premium(data: dict, cache: dict) -> RuleResult:
    """IV期限溢价"""
    tp = _safe_float(data.get("term_premium", 0))
    if tp > 5:
        return RuleResult(name="期限溢价", score=85, max=100,
                          verdict=f"陡峭Contango(溢价={tp:.1f}%)，有利于卖方",
                          reasoning=[f"近月IV < 远月IV，溢价={tp:.1f}%", "适合卖近买远的日历价差"])
    elif tp > 0:
        return RuleResult(name="期限溢价", score=65, max=100,
                          verdict=f"轻微Contango(溢价={tp:.1f}%)",
                          reasoning=[f"期限溢价={tp:.1f}%"])
    elif tp < -3:
        return RuleResult(name="期限溢价", score=25, max=100,
                          verdict=f"Backwardation(溢价={tp:.1f}%)，近月恐慌",
                          reasoning=[f"近月IV > 远月IV，倒挂={tp:.1f}%", "可能预示短期风险事件"])
    else:
        return RuleResult(name="期限溢价", score=50, max=100,
                          verdict=f"平坦期限结构(溢价={tp:.1f}%)",
                          reasoning=[f"期限溢价={tp:.1f}%"])


def calc_iv_steepness(data: dict, cache: dict) -> RuleResult:
    """IV曲线陡峭度"""
    steep = _safe_float(data.get("iv_steepness", 0))
    if steep > 0.8:
        return RuleResult(name="曲线陡峭度", score=75, max=100,
                          verdict=f"后端陡峭(斜率={steep:.2f})，远月溢价充足",
                          reasoning=[f"远月IV显著高于近月", "日历价差利润空间大"])
    elif steep < -0.3:
        return RuleResult(name="曲线陡峭度", score=30, max=100,
                          verdict=f"前端翘起(斜率={steep:.2f})，近月风险高",
                          reasoning=[f"近月IV异常偏高"])
    else:
        return RuleResult(name="曲线陡峭度", score=55, max=100,
                          verdict=f"正常斜率(斜率={steep:.2f})",
                          reasoning=[f"曲线斜率正常"])


def calc_vol_regime(data: dict, cache: dict) -> RuleResult:
    """波动率区间判断"""
    dvol = _safe_float(data.get("dvol", 0))
    dvol_z = _safe_float(data.get("dvol_z", 0))
    if dvol >= 80:
        return RuleResult(name="波动率区间", score=10, max=100,
                          verdict=f"恐慌区间(DVOL={dvol})，建议暂停操作",
                          reasoning=[f"DVOL={dvol}≥80", "极端波动，保证金需求极高"])
    elif dvol >= 70:
        return RuleResult(name="波动率区间", score=30, max=100,
                          verdict=f"高波区间(DVOL={dvol})，缩小仓位",
                          reasoning=[f"DVOL={dvol}≥70"])
    elif dvol >= 50:
        return RuleResult(name="波动率区间", score=65, max=100,
                          verdict=f"中波区间(DVOL={dvol})，正常操作",
                          reasoning=[f"DVOL={dvol} 健康区间"])
    else:
        return RuleResult(name="波动率区间", score=80, max=100,
                          verdict=f"低波区间(DVOL={dvol})，提高仓位",
                          reasoning=[f"DVOL={dvol}<50", "低波动率环境有利卖方"])


def calc_calendar_spread(data: dict, cache: dict) -> RuleResult:
    """日历价差机会判定"""
    tp = _safe_float(data.get("term_premium", 0))
    steep = _safe_float(data.get("iv_steepness", 0))
    if tp > 5 and steep > 0.5:
        return RuleResult(name="日历价差", score=80, max=100,
                          verdict="日历价差机会明确，卖近买远",
                          reasoning=[f"期限溢价{tp:.1f}%>5%且曲线陡峭{steep:.2f}>0.5"])
    elif tp > 2:
        return RuleResult(name="日历价差", score=55, max=100,
                          verdict="日历价差可考虑，但利润空间一般",
                          reasoning=[f"期限溢价{tp:.1f}%"])
    else:
        return RuleResult(name="日历价差", score=30, max=100,
                          verdict="当前不适合日历价差策略",
                          reasoning=[f"期限溢价{tp:.1f}%不足"])


def calc_skew_signal(data: dict, cache: dict) -> RuleResult:
    """IV偏度信号"""
    skew = _safe_float(data.get("skew", 0))
    if skew < -5:
        return RuleResult(name="偏度信号", score=65, max=100,
                          verdict=f"显著负偏(skew={skew:.1f})，PUT端溢价较高",
                          reasoning=[f"OTM PUT IV显著高于CALL", "卖PUT收取更高溢价"])
    elif skew > 5:
        return RuleResult(name="偏度信号", score=70, max=100,
                          verdict=f"显著正偏(skew={skew:.1f})，CALL端溢价较高",
                          reasoning=[f"OTM CALL IV显著高于PUT", "卖CALL收取更高溢价"])
    else:
        return RuleResult(name="偏度信号", score=50, max=100,
                          verdict=f"偏度正常(skew={skew:.1f})",
                          reasoning=["IV微笑基本对称"])


def calc_smile_morphology(data: dict, cache: dict) -> RuleResult:
    """IV微笑形态"""
    kurt = _safe_float(data.get("kurtosis", 0))
    if kurt > 1:
        return RuleResult(name="微笑形态", score=55, max=100,
                          verdict=f"肥尾分布(kurt={kurt:.1f})，尾部风险溢价高",
                          reasoning=[f"峰度={kurt:.1f}>1", "市场定价了尾部风险"])
    elif kurt < -0.5:
        return RuleResult(name="微笑形态", score=60, max=100,
                          verdict=f"瘦尾分布(kurt={kurt:.1f})，风险定价偏低",
                          reasoning=[f"峰度={kurt:.1f}<-0.5"])
    else:
        return RuleResult(name="微笑形态", score=50, max=100,
                          verdict=f"正常形态(kurt={kurt:.1f})",
                          reasoning=["微笑形态正常"])


def calc_pcr_signal(data: dict, cache: dict) -> RuleResult:
    """Put/Call Ratio 信号"""
    pcr = _safe_float(data.get("pcr", 1.0))
    if pcr > 1.5:
        return RuleResult(name="PCR信号", score=75, max=100,
                          verdict=f"PCR极高({pcr:.2f})，市场过度恐慌，反向看多",
                          reasoning=[f"PCR={pcr:.2f}>1.5", "极端值常对应底部"])
    elif pcr > 1.2:
        return RuleResult(name="PCR信号", score=60, max=100,
                          verdict=f"PCR偏高({pcr:.2f})，偏谨慎情绪",
                          reasoning=[f"PCR={pcr:.2f}>1.2"])
    elif pcr < 0.7:
        return RuleResult(name="PCR信号", score=35, max=100,
                          verdict=f"PCR极低({pcr:.2f})，市场过度乐观",
                          reasoning=[f"PCR={pcr:.2f}<0.7", "极端低值常对应顶部"])
    elif pcr < 0.9:
        return RuleResult(name="PCR信号", score=45, max=100,
                          verdict=f"PCR偏低({pcr:.2f})，偏乐观情绪",
                          reasoning=[f"PCR={pcr:.2f}<0.9"])
    else:
        return RuleResult(name="PCR信号", score=50, max=100,
                          verdict=f"PCR正常({pcr:.2f})",
                          reasoning=["PCR处于正常区间"])


def calc_large_trades_direction(data: dict, cache: dict) -> RuleResult:
    """大单方向判断"""
    trades = data.get("large_trades", [])
    if not trades:
        return RuleResult(name="大单方向", score=50, verdict="无大单数据")
    buys = sum(1 for t in trades if t.get("direction") in ("buy", "call_buy", "put_sell"))
    sells = sum(1 for t in trades if t.get("direction") in ("sell", "call_sell", "put_buy"))
    total = len(trades)
    ratio = buys / max(sells, 1)
    if ratio > 1.5:
        return RuleResult(name="大单方向", score=70, max=100,
                          verdict=f"主力偏多(买{sells}卖{buy_s}/{total})",
                          reasoning=[f"买入/卖出={ratio:.1f}>1.5"])
    elif ratio < 0.67:
        return RuleResult(name="大单方向", score=30, max=100,
                          verdict=f"主力偏空(买{buys}/卖{sells}/{total})",
                          reasoning=[f"买入/卖出={ratio:.1f}<0.67"])
    else:
        return RuleResult(name="大单方向", score=50, max=100,
                          verdict=f"多空均衡(买{buys}/卖{sells}/{total})",
                          reasoning=[f"买入/卖出≈1"])


# ============================================================
# 包装器（包装现有引擎输出）
# ============================================================

def wrap_risk_framework(data: dict, cache: dict) -> RuleResult:
    """包装 RiskFramework.get_status()"""
    try:
        from services.risk_framework import RiskFramework
        spot = _safe_float(data.get("spot", 0))
        status = RiskFramework.get_status(spot)
        floors = RiskFramework._get_floors()
        regular_floor = floors.get("regular", 0)
        extreme_floor = floors.get("extreme", 0)
        dist_pct = ((spot - regular_floor) / regular_floor * 100) if regular_floor > 0 and spot > 0 else 0

        if status == "extreme":
            return RuleResult(name="风险框架(6因子)", score=15, max=100,
                              verdict=f"极端风险，现货${spot:.0f}接近极端支撑${extreme_floor:.0f}",
                              reasoning=[f"现货${spot:.0f}", f"距常规支撑{dist_pct:.1f}%",
                                         f"极端支撑${extreme_floor:.0f}"])
        elif status == "high":
            return RuleResult(name="风险框架(6因子)", score=35, max=100,
                              verdict=f"高风险，距支撑{dist_pct:.1f}%",
                              reasoning=[f"现货${spot:.0f}", f"距常规支撑{dist_pct:.1f}%"])
        elif status == "warning":
            return RuleResult(name="风险框架(6因子)", score=55, max=100,
                              verdict=f"警告级别，距支撑{dist_pct:.1f}%",
                              reasoning=[f"现货${spot:.0f}", f"距常规支撑{dist_pct:.1f}%"])
        else:
            return RuleResult(name="风险框架(6因子)", score=75, max=100,
                              verdict=f"正常，距支撑{dist_pct:.1f}%",
                              reasoning=[f"现货${spot:.0f}", "安全边际充足"])
    except Exception as e:
        return RuleResult(name="风险框架(6因子)", score=50, verdict=f"计算失败: {e}")


def wrap_unified_risk(data: dict, cache: dict) -> RuleResult:
    """包装 UnifiedRiskAssessor"""
    try:
        from services.unified_risk_assessor import UnifiedRiskAssessor
        spot = _safe_float(data.get("spot", 0))
        assessor = UnifiedRiskAssessor()
        result = assessor.assess(spot, data.get("contracts", []))
        score = _safe_float(result.get("score", 50))
        label = result.get("label", "未知")
        return RuleResult(name="统一风险评估", score=score, max=100,
                          verdict=label,
                          reasoning=[f"综合评分={score}/100", f"风险等级={label}"])
    except Exception as e:
        return RuleResult(name="统一风险评估", score=50, verdict=f"评估失败: {e}")


def wrap_greeks_analyzer(data: dict, cache: dict) -> RuleResult:
    """包装 GreeksAnalyzer"""
    try:
        greeks = data.get("greeks", {})
        gex = _safe_float(greeks.get("gex", 0))
        dvol = _safe_float(data.get("dvol", 0))
        if gex > 0 and dvol < 60:
            return RuleResult(name="Greeks风险矩阵", score=70, max=100,
                              verdict=f"GEX正值({gex:.0f})，伽马做市商稳定市场",
                              reasoning=[f"GEX={gex:.0f}>0", f"DVOL={dvol}<60"])
        elif gex < 0 and dvol > 60:
            return RuleResult(name="Greeks风险矩阵", score=25, max=100,
                              verdict=f"GEX负值({gex:.0f})且高波，市场不稳定",
                              reasoning=[f"GEX={gex:.0f}<0", f"DVOL={dvol}>60"])
        else:
            return RuleResult(name="Greeks风险矩阵", score=50, max=100,
                              verdict=f"GEX中性({gex:.0f})",
                              reasoning=[f"GEX={gex:.0f}"])
    except Exception as e:
        return RuleResult(name="Greeks风险矩阵", score=50, verdict=f"分析失败: {e}")


def wrap_strategy_engine(data: dict, cache: dict) -> RuleResult:
    """包装 UnifiedStrategyEngine 的最佳推荐"""
    try:
        contracts = data.get("contracts", [])
        spot = _safe_float(data.get("spot", 0))
        if not contracts or spot <= 0:
            return RuleResult(name="策略推荐引擎", score=50, verdict="无合约数据")
        # 取 top APR 合约的方向性
        puts = [c for c in contracts if c.get("option_type") in ("P", "PUT")]
        top_puts = sorted(puts, key=lambda c: _safe_float(c.get("apr", 0)), reverse=True)[:3]
        if top_puts:
            avg_apr = sum(_safe_float(c.get("apr", 0)) for c in top_puts) / len(top_puts)
            if avg_apr > 30:
                return RuleResult(name="策略推荐引擎", score=75, max=100,
                                  verdict=f"PUT端机会丰富(平均APR={avg_apr:.0f}%)",
                                  reasoning=[f"Top3 PUT APR={avg_apr:.0f}%", "高回报率窗口"])
            elif avg_apr > 15:
                return RuleResult(name="策略推荐引擎", score=60, max=100,
                                  verdict=f"PUT端有操作空间(平均APR={avg_apr:.0f}%)",
                                  reasoning=[f"Top3 PUT APR={avg_apr:.0f}%"])
        return RuleResult(name="策略推荐引擎", score=45, max=100,
                          verdict="当前无突出机会",
                          reasoning=["APR未达到显著水平"])
    except Exception as e:
        return RuleResult(name="策略推荐引擎", score=50, verdict=f"分析失败: {e}")


def wrap_maxpain(data: dict, cache: dict) -> RuleResult:
    """包装 MaxPain 磁吸判断"""
    try:
        spot = _safe_float(data.get("spot", 0))
        mp = _safe_float(data.get("max_pain", 0))
        if mp <= 0 or spot <= 0:
            return RuleResult(name="MaxPain磁吸", score=50, verdict="无数据")
        dist_pct = abs(spot - mp) / spot * 100
        if dist_pct < 2:
            return RuleResult(name="MaxPain磁吸", score=65, max=100,
                              verdict=f"距MaxPain仅{dist_pct:.1f}%，磁吸效应强",
                              reasoning=[f"现货${spot:.0f}", f"MaxPain=${mp:.0f}", f"距离{dist_pct:.1f}%"])
        elif dist_pct < 5:
            return RuleResult(name="MaxPain磁吸", score=55, max=100,
                              verdict=f"距MaxPain{dist_pct:.1f}%，有磁吸力",
                              reasoning=[f"距离{dist_pct:.1f}%"])
        else:
            return RuleResult(name="MaxPain磁吸", score=40, max=100,
                              verdict=f"距MaxPain较远({dist_pct:.1f}%)，磁吸弱",
                              reasoning=[f"距离{dist_pct:.1f}%>5%"])
    except Exception as e:
        return RuleResult(name="MaxPain磁吸", score=50, verdict=f"计算失败: {e}")


def wrap_gamma_flip(data: dict, cache: dict) -> RuleResult:
    """包装 Gamma Flip 检测"""
    try:
        gf = _safe_float(data.get("gamma_flip", 0))
        if gf > 0:
            return RuleResult(name="Gamma Flip", score=60, max=100,
                              verdict=f"Gamma Flip价格${gf:.0f}，当前在其上方",
                              reasoning=[f"Gamma Flip=${gf:.0f}"])
        else:
            return RuleResult(name="Gamma Flip", score=50, max=100, verdict="无Gamma Flip数据")
    except Exception as e:
        return RuleResult(name="Gamma Flip", score=50, verdict=f"计算失败: {e}")


def wrap_martingale(data: dict, cache: dict) -> RuleResult:
    """包装 Martingale 风险判断"""
    try:
        margin_ratio = _safe_float(data.get("margin_ratio", 0.2))
        spot = _safe_float(data.get("spot", 0))
        if margin_ratio > 0.3:
            return RuleResult(name="马丁格尔风险", score=25, max=100,
                              verdict=f"保证金率{margin_ratio:.0%}过高，补仓空间不足",
                              reasoning=[f"保证金率={margin_ratio:.0%}"])
        elif margin_ratio > 0.2:
            return RuleResult(name="马丁格尔风险", score=50, max=100,
                              verdict=f"保证金率{margin_ratio:.0%}中等",
                              reasoning=[f"保证金率={margin_ratio:.0%}"])
        else:
            return RuleResult(name="马丁格尔风险", score=70, max=100,
                              verdict=f"保证金率{margin_ratio:.0%}健康，有补仓空间",
                              reasoning=[f"保证金率={margin_ratio:.0%}"])
    except Exception as e:
        return RuleResult(name="马丁格尔风险", score=50, verdict=f"计算失败: {e}")


def wrap_money_flow(data: dict, cache: dict) -> RuleResult:
    """包装资金流向"""
    try:
        flow = _safe_float(data.get("net_flow", 0))
        if flow > 1000000:
            return RuleResult(name="资金流向", score=70, max=100,
                              verdict=f"显著净流入(${flow/1e6:.1f}M)",
                              reasoning=[f"净流入${flow/1e6:.1f}M"])
        elif flow < -1000000:
            return RuleResult(name="资金流向", score=30, max=100,
                              verdict=f"显著净流出(${abs(flow)/1e6:.1f}M)",
                              reasoning=[f"净流出${abs(flow)/1e6:.1f}M"])
        else:
            return RuleResult(name="资金流向", score=50, max=100,
                              verdict="资金流向中性",
                              reasoning=[f"净流入=${flow:.0f}"])
    except Exception as e:
        return RuleResult(name="资金流向", score=50, verdict=f"分析失败: {e}")


def wrap_onchain(data: dict, cache: dict) -> RuleResult:
    """包装链上指标"""
    try:
        mvrv = _safe_float(data.get("mvrv", 0))
        if mvrv > 3:
            return RuleResult(name="链上MVRV", score=25, max=100,
                              verdict=f"MVRV={mvrv:.1f}>3，市场过热",
                              reasoning=[f"MVRV={mvrv:.1f}"])
        elif mvrv > 2:
            return RuleResult(name="链上MVRV", score=45, max=100,
                              verdict=f"MVRV={mvrv:.1f}偏高",
                              reasoning=[f"MVRV={mvrv:.1f}"])
        elif mvrv < 1:
            return RuleResult(name="链上MVRV", score=75, max=100,
                              verdict=f"MVRV={mvrv:.1f}<1，低估区间",
                              reasoning=[f"MVRV={mvrv:.1f}"])
        else:
            return RuleResult(name="链上MVRV", score=55, max=100,
                              verdict=f"MVRV={mvrv:.1f} 正常",
                              reasoning=[f"MVRV={mvrv:.1f}"])
    except Exception as e:
        return RuleResult(name="链上MVRV", score=50, verdict=f"分析失败: {e}")


# ============================================================
# 面板配置注册表
# ============================================================

PANEL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "metric_cards": {
        "name": "顶部指标卡",
        "rules": [
            {"id": "dvol_signal", "name": "DVOL信号", "fn": calc_dvol_signal, "weight": 0.35},
            {"id": "sentiment", "name": "市场情绪", "fn": calc_sentiment, "weight": 0.30},
            {"id": "trend_strength", "name": "趋势强度", "fn": calc_trend_strength, "weight": 0.35},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "risk_command_center": {
        "name": "风险指挥中心",
        "rules": [
            {"id": "risk_framework", "name": "RiskFramework", "fn": wrap_risk_framework, "weight": 0.40},
            {"id": "unified_risk", "name": "统一风险评估", "fn": wrap_unified_risk, "weight": 0.35},
            {"id": "greek_risk", "name": "Greeks风险矩阵", "fn": wrap_greeks_analyzer, "weight": 0.25},
        ],
        "signal_formula": "worst_case",
        "default_action": "",
    },
    "strategy_center": {
        "name": "策略推荐中心",
        "rules": [
            {"id": "strategy_engine", "name": "策略推荐引擎", "fn": wrap_strategy_engine, "weight": 1.0},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "greeks_matrix": {
        "name": "Greeks风险矩阵",
        "rules": [
            {"id": "greek_risk", "name": "Greeks风险矩阵", "fn": wrap_greeks_analyzer, "weight": 0.5},
            {"id": "dvol", "name": "DVOL信号", "fn": calc_dvol_signal, "weight": 0.5},
        ],
        "signal_formula": "worst_case",
        "default_action": "",
    },
    "ai_analyst_center": {
        "name": "AI分析中心",
        "rules": [
            {"id": "dvol", "name": "DVOL信号", "fn": calc_dvol_signal, "weight": 0.4},
            {"id": "sentiment", "name": "市场情绪", "fn": calc_sentiment, "weight": 0.3},
            {"id": "trend", "name": "趋势强度", "fn": calc_trend_strength, "weight": 0.3},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "iv_term_structure": {
        "name": "IV期限结构",
        "rules": [
            {"id": "term_premium", "name": "期限溢价", "fn": calc_term_premium, "weight": 0.35},
            {"id": "steepness", "name": "曲线陡峭度", "fn": calc_iv_steepness, "weight": 0.25},
            {"id": "vol_regime", "name": "波动率区间", "fn": calc_vol_regime, "weight": 0.25},
            {"id": "spread", "name": "日历价差", "fn": calc_calendar_spread, "weight": 0.15},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "iv_smile": {
        "name": "IV Smile",
        "rules": [
            {"id": "skew", "name": "偏度信号", "fn": calc_skew_signal, "weight": 0.4},
            {"id": "morphology", "name": "微笑形态", "fn": calc_smile_morphology, "weight": 0.3},
            {"id": "vol_regime", "name": "波动率区间", "fn": calc_vol_regime, "weight": 0.3},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "dvol_trend": {
        "name": "DVOL趋势",
        "rules": [
            {"id": "dvol_signal", "name": "DVOL信号", "fn": calc_dvol_signal, "weight": 0.6},
            {"id": "vol_regime", "name": "波动率区间", "fn": calc_vol_regime, "weight": 0.4},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "pcr_chart": {
        "name": "PCR图表",
        "rules": [
            {"id": "pcr", "name": "PCR信号", "fn": calc_pcr_signal, "weight": 0.5},
            {"id": "sentiment", "name": "市场情绪", "fn": calc_sentiment, "weight": 0.5},
        ],
        "signal_formula": "majority",
        "default_action": "",
    },
    "max_pain": {
        "name": "最大痛点",
        "rules": [
            {"id": "maxpain", "name": "MaxPain磁吸", "fn": wrap_maxpain, "weight": 0.5},
            {"id": "gamma_flip", "name": "Gamma Flip", "fn": wrap_gamma_flip, "weight": 0.5},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "large_trades": {
        "name": "大单追踪",
        "rules": [
            {"id": "direction", "name": "大单方向", "fn": calc_large_trades_direction, "weight": 1.0},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "martingale_sandbox": {
        "name": "马丁格尔沙盒",
        "rules": [
            {"id": "martingale", "name": "马丁格尔风险", "fn": wrap_martingale, "weight": 0.5},
            {"id": "risk", "name": "RiskFramework", "fn": wrap_risk_framework, "weight": 0.5},
        ],
        "signal_formula": "worst_case",
        "default_action": "",
    },
    "opportunities_table": {
        "name": "实时机会列表",
        "rules": [
            {"id": "dvol", "name": "DVOL信号", "fn": calc_dvol_signal, "weight": 0.4},
            {"id": "strategy", "name": "策略推荐引擎", "fn": wrap_strategy_engine, "weight": 0.6},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "gex_chart": {
        "name": "GEX图表",
        "rules": [
            {"id": "greek", "name": "Greeks风险矩阵", "fn": wrap_greeks_analyzer, "weight": 0.5},
            {"id": "vol_regime", "name": "波动率区间", "fn": calc_vol_regime, "weight": 0.5},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "money_flow": {
        "name": "资金流向",
        "rules": [
            {"id": "flow", "name": "资金流向", "fn": wrap_money_flow, "weight": 0.6},
            {"id": "sentiment", "name": "市场情绪", "fn": calc_sentiment, "weight": 0.4},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
    "onchain_metrics": {
        "name": "链上指标",
        "rules": [
            {"id": "onchain", "name": "链上MVRV", "fn": wrap_onchain, "weight": 0.6},
            {"id": "sentiment", "name": "市场情绪", "fn": calc_sentiment, "weight": 0.4},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
}


# ============================================================
# LLM Prompt 模板
# ============================================================

LLM_PROMPT_TEMPLATES: Dict[str, Dict[str, str]] = {
    "iv_term_structure": {
        "synthesis": (
            "你是加密货币期权波动率结构分析师。基于以下数据分析{currency}的IV期限结构:\n"
            "- 现货: ${spot}\n"
            "- 期限溢价: {term_premium}%\n"
            "- DVOL: {dvol}(z={dvol_z})\n"
            "- 曲线形态: {curve_shape}\n"
            "- 规则评分:\n{rule_scores}\n\n"
            "请从卖方角度给出结构判断和操作建议。"
        ),
        "bull_context": "期限结构利多因素:\n- 陡峭Contango有利于日历价差卖方\n- 远月IV溢价提供缓冲\n- 低DVOL环境下保证金成本低",
        "bear_context": "期限结构利空因素:\n- 近月IV异常偏高可能预示风险事件\n- Backwardation结构对卖方不利\n- 高DVOL挤压利润空间",
        "judge_criteria": "从风险收益比角度判定整体结构方向，给出具体操作建议（包括合约选择、期限、仓位大小建议）。",
    },
    "risk_command_center": {
        "synthesis": (
            "你是加密货币风险管理专家。评估{currency}当前风险:\n"
            "- 现货: ${spot}\n"
            "- DVOL: {dvol}(z={dvol_z})\n"
            "- 规则评分:\n{rule_scores}\n\n"
            "请综合评估风险并给出对冲建议。"
        ),
        "bull_context": "风险偏低因素:\n- 安全边际充足\n- DVOL处于健康区间\n- 无极端风险信号",
        "bear_context": "风险偏高因素:\n- 接近支撑位\n- DVOL偏高\n- 极端风险信号触发",
        "judge_criteria": "评估综合风险等级并给出仓位管理和对冲建议。",
    },
    "metric_cards": {
        "synthesis": (
            "你是加密货币宏观分析师。快速评估{currency}市场全景:\n"
            "- 现货: ${spot}\n"
            "- DVOL: {dvol}(z={dvol_z})\n"
            "- 恐贪指数: {fear_greed}\n"
            "- 规则评分:\n{rule_scores}\n\n"
            "用3-5句话概括当前市场状态。"
        ),
        "bull_context": "宏观利多:\n- 低波动率 + 恐惧情绪 = 卖权机会\n- 资金费率正常",
        "bear_context": "宏观利空:\n- 高波动率 + 贪婪情绪 = 风险累积\n- 关注反转信号",
        "judge_criteria": "简短判断市场方向（偏多/偏空/震荡），给1个最具体的操作建议。",
    },
    "iv_smile": {
        "synthesis": (
            "你是波动率曲面分析师。分析{currency}的IV Smile形态:\n"
            "- 现货: ${spot}\n"
            "- 偏度: {skew}\n"
            "- DVOL: {dvol}(z={dvol_z})\n"
            "- 规则评分:\n{rule_scores}\n\n"
            "判断哪个方向的行权价被高估/低估。"
        ),
        "bull_context": "Smile利多:\n- 负偏意味着PUT溢价更高 → 卖PUT更有利\n- OTM PUT IV溢价充足",
        "bear_context": "Smile利空:\n- 正偏意味着CALL溢价更高 → 方向性看涨信号\n- 但卖PUT利润降低",
        "judge_criteria": "判断最佳卖权方向和行权价区间。",
    },
    "dvol_trend": {
        "synthesis": (
            "你是波动率分析专家。分析{currency}的DVOL趋势:\n"
            "- DVOL: {dvol}(z={dvol_z})\n"
            "- 规则评分:\n{rule_scores}\n\n"
            "判断波动率区间和均值回归方向。"
        ),
        "bull_context": "低波环境:\n- DVOL低于历史中位数\n- 有利于卖方策略\n- 可适当放大仓位",
        "bear_context": "高波环境:\n- DVOL高于历史中位数\n- 卖方风险加大\n- 建议缩小仓位或等待回归",
        "judge_criteria": "给出波动率区间判断、预期回归时间、仓位调整建议。",
    },
    "pcr_chart": {
        "synthesis": (
            "你是市场情绪分析师。分析{currency}的PCR:\n"
            "- PCR: {pcr}\n"
            "- 恐贪指数: {fear_greed}\n"
            "- 规则评分:\n{rule_scores}\n\n"
            "判断市场情绪极端程度。"
        ),
        "bull_context": "PCR看多:\n- PCR极高表示市场过度恐慌 → 反向看多\n- 历史数据显示极端PCR常对应底部",
        "bear_context": "PCR看空:\n- PCR极低表示市场过度乐观 → 谨慎看空\n- 但趋势市中PCR可持续低位",
        "judge_criteria": "判断情绪极端度和反向操作机会。",
    },
    "max_pain": {
        "synthesis": (
            "你是期权到期日分析师。分析{currency}的MaxPain:\n"
            "- 现货: ${spot}\n"
            "- MaxPain: {max_pain}\n"
            "- 规则评分:\n{rule_scores}\n\n"
            "判断到期日前的价格磁吸效应。"
        ),
        "bull_context": "MaxPain利多:\n- 现货高于MaxPain，市场偏强\n- 磁吸力可能拉回但未必",
        "bear_context": "MaxPain利空:\n- 现货低于MaxPain，有下压阻力\n- Gamma Flip可能加剧下跌",
        "judge_criteria": "判断到期日价格区间和GEX/Gamma影响。",
    },
    "large_trades": {
        "synthesis": (
            "你是订单流分析师。分析{currency}的大单动向:\n"
            "- 大单数据: {large_trades_summary}\n"
            "- 规则评分:\n{rule_scores}\n\n"
            "判断聪明钱方向。"
        ),
        "bull_context": "大单利多:\n- 主力买入期权/卖出PUT\n- 大单偏向买方",
        "bear_context": "大单利空:\n- 主力买入PUT/卖出CALL\n- 大单偏向卖方",
        "judge_criteria": "判断主力方向并评估跟随价值。",
    },
    "martingale_sandbox": {
        "synthesis": (
            "你是风险量化分析师。评估马丁格尔策略风险:\n"
            "- 现货: ${spot}\n"
            "- 保证金率: {margin_ratio}\n"
            "- 规则评分:\n{rule_scores}\n\n"
            "评估补仓空间和爆仓风险。"
        ),
        "bull_context": "低风险:\n- 保证金充足，有多次补仓空间\n- 支撑位坚实",
        "bear_context": "高风险:\n- 保证金紧张，补仓空间有限\n- 接近极端支撑位",
        "judge_criteria": "给出最大可承受跌幅、补仓点位建议、止损条件。",
    },
    "opportunities_table": {
        "synthesis": (
            "你是期权策略筛选专家。分析当前机会列表:\n"
            "- 现货: ${spot}\n"
            "- DVOL: {dvol}(z={dvol_z})\n"
            "- 规则评分:\n{rule_scores}\n\n"
            "筛选最佳交易机会。"
        ),
        "bull_context": "机会丰富:\n- 高APR + 低风险 = 最佳交易窗口\n- 多个合约满足策略条件",
        "bear_context": "机会稀少:\n- 当前无高性价比合约\n- 建议等待更好时机",
        "judge_criteria": "推荐1-3个最佳合约并给出持仓建议。",
    },
    "strategy_center": {
        "synthesis": (
            "你是期权策略师。分析{currency}的策略推荐:\n"
            "- 现货: ${spot}\n"
            "- DVOL: {dvol}(z={dvol_z})\n"
            "- 规则评分:\n{rule_scores}\n\n"
            "给出具体交易计划。"
        ),
        "bull_context": "策略利多:\n- 多因子共振，操作胜率提升\n- 确定性强",
        "bear_context": "策略利空:\n- 信号分歧，需降低仓位\n- 某些因子发出警告",
        "judge_criteria": "给出具体策略（方向/期限/行权价/仓位/止损）。",
    },
    "greeks_matrix": {
        "synthesis": (
            "你是期权Greeks专家。分析{currency}的Greeks风险:\n"
            "- 现货: ${spot}\n"
            "- GEX: {gex}\n"
            "- DVOL: {dvol}(z={dvol_z})\n"
            "- 规则评分:\n{rule_scores}\n\n"
            "评估希腊字母风险敞口。"
        ),
        "bull_context": "Greeks利多:\n- GEX正值 → 做市商稳定市场\n- 低Gamma → 低波动预期",
        "bear_context": "Greeks利空:\n- GEX负值 → 做市商放大波动\n- 高Gamma → 高波动风险",
        "judge_criteria": "给出Delta/Gamma/Vega敞口建议。",
    },
    "gex_chart": {
        "synthesis": (
            "你是GEX分析专家。分析{currency}的GEX水平:\n"
            "- 现货: ${spot}\n"
            "- GEX: {gex}\n"
            "- DVOL: {dvol}(z={dvol_z})\n"
            "- 规则评分:\n{rule_scores}\n\n"
            "判断Gamma暴露对市场的影响。"
        ),
        "bull_context": "GEX利多: 正值GEX压制波动，利于卖方",
        "bear_context": "GEX利空: 负值GEX放大波动，谨慎卖方",
        "judge_criteria": "判断GEX水平和方向性影响。",
    },
    "money_flow": {
        "synthesis": (
            "你是资金流向分析师。分析{currency}的资金流向:\n"
            "- 净流入: {net_flow}\n"
            "- 规则评分:\n{rule_scores}\n\n"
            "判断资金面偏多/偏空。"
        ),
        "bull_context": "资金面利多: 显著净流入支撑价格",
        "bear_context": "资金面利空: 净流出暗示撤离",
        "judge_criteria": "判断资金面方向并给出交易建议。",
    },
    "onchain_metrics": {
        "synthesis": (
            "你是链上数据分析师。分析{currency}的链上指标:\n"
            "- MVRV: {mvrv}\n"
            "- 规则评分:\n{rule_scores}\n\n"
            "判断长期估值区间。"
        ),
        "bull_context": "链上利多: MVRV低估区间 → 长期买入机会",
        "bear_context": "链上利空: MVRV高估区间 → 注意泡沫风险",
        "judge_criteria": "判断估值区间并给出长期仓位建议。",
    },
    "ai_analyst_center": {
        "synthesis": (
            "总览{currency}的整体市场状态:\n- 现货: ${spot}\n- DVOL: {dvol}(z={dvol_z})\n"
            "- 规则评分:\n{rule_scores}\n\n概括当前最关键的3个信号。"
        ),
        "bull_context": "总体偏多",
        "bear_context": "总体偏空",
        "judge_criteria": "3句话总结 + 1个核心建议。",
    },
}


def get_llm_prompt(panel_id: str) -> Dict[str, str]:
    """获取面板的 LLM prompt 模板"""
    default = {
        "synthesis": "分析{currency}的市场状态:\n- 规则评分:\n{rule_scores}\n\n给出操作建议。",
        "bull_context": "利多因素可能包括: 低波动率、恐惧情绪、强支撑位",
        "bear_context": "利空因素可能包括: 高波动率、贪婪情绪、接近阻力位",
        "judge_criteria": "综合判断方向，给出具体建议。",
    }
    return LLM_PROMPT_TEMPLATES.get(panel_id, default)
```

- [ ] **Step 2: 验证面板配置完整性**

Run: `python -c "from services.panel_analyzers import PANEL_CONFIGS; print(f'Loaded {len(PANEL_CONFIGS)} panels'); [print(f'  {k}: {len(v[\"rules\"])} rules') for k,v in PANEL_CONFIGS.items()]"`
Expected: "Loaded 16 panels" 且每个面板至少有1条规则

- [ ] **Step 3: 运行核心引擎测试确认通过**

Run: `python -m pytest tests/test_unified_recommendation.py -v`
Expected: ALL PASS（引擎自动加载 PANEL_CONFIGS）

- [ ] **Step 4: 提交**

```bash
git add services/panel_analyzers.py
git commit -m "feat: add 16 panel analyzer configs with rule functions and LLM prompt templates

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: 创建 API 路由 + 注册到 main.py

**Files:**
- Create: `api/recommendations.py`
- Modify: `api/__init__.py`
- Modify: `main.py`
- Test: `tests/test_recommendations_api.py`

- [ ] **Step 1: 编写 API 测试**

```python
"""tests/test_recommendations_api.py"""
import pytest
from fastapi.testclient import TestClient

# We test the router standalone first, then integration
from api.recommendations import router
from fastapi import FastAPI

@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)

class TestRecommendationEndpoint:
    def test_get_recommendation_returns_signal(self, client):
        resp = client.get("/api/recommendation/metric_cards?currency=BTC")
        assert resp.status_code == 200
        data = resp.json()
        assert data["panel_id"] == "metric_cards"
        assert "signal" in data
        assert data["signal"]["signal"] in ("bullish", "bearish", "neutral", "caution")
        assert "report" in data
        assert data["llm_analysis"] is None

    def test_get_recommendation_invalid_panel(self, client):
        resp = client.get("/api/recommendation/nonexistent_panel?currency=BTC")
        assert resp.status_code == 400

    def test_get_summary(self, client):
        resp = client.get("/api/recommendations/summary?currency=BTC")
        assert resp.status_code == 200
        data = resp.json()
        assert "signals" in data
        assert len(data["signals"]) >= 16

    def test_batch_recommendations(self, client):
        resp = client.post("/api/recommendations/batch",
            json={"panels": ["metric_cards", "dvol_trend"], "currency": "BTC"})
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert len(data["results"]) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_recommendations_api.py -v --tb=short`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: 创建 API 路由文件**

```python
"""api/recommendations.py
统一投资推荐 API 端点
"""
import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["recommendations"])


class BatchRequest(BaseModel):
    panels: List[str]
    currency: str = "BTC"


class LLMTriggerRequest(BaseModel):
    currency: str = "BTC"
    force_refresh: bool = False


@router.get("/recommendation/{panel_id}")
async def get_recommendation(
    panel_id: str,
    currency: str = Query(default="BTC"),
):
    """获取单个面板的规则推荐（信号灯 + 规则报告）"""
    from services.unified_recommendation_engine import UnifiedRecommendationEngine
    from services.panel_analyzers import PANEL_CONFIGS

    if panel_id not in PANEL_CONFIGS:
        valid = ", ".join(sorted(PANEL_CONFIGS.keys()))
        raise HTTPException(status_code=400, detail=f"Unknown panel: {panel_id}. Valid: {valid}")

    # 收集数据（从最新扫描）
    data = await _gather_panel_data(panel_id, currency)

    engine = UnifiedRecommendationEngine()
    result = engine.analyze(panel_id, data, currency)
    return result


@router.get("/recommendations/summary")
async def get_summary(currency: str = Query(default="BTC")):
    """全板块信号汇总（顶部条用）"""
    from services.unified_recommendation_engine import UnifiedRecommendationEngine

    data = await _gather_panel_data("all", currency)
    engine = UnifiedRecommendationEngine()
    results = engine.analyze_all(data, currency)

    signals = []
    for panel_id, result in results.items():
        signals.append({
            "panel_id": panel_id,
            "signal": result["signal"]["signal"],
            "signal_emoji": result["signal"]["signal_emoji"],
            "signal_text": result["signal"]["signal_text"],
            "confidence": result["signal"]["confidence"],
        })

    return {"currency": currency, "signals": signals, "count": len(signals)}


@router.post("/recommendations/batch")
async def batch_recommendations(request: BatchRequest):
    """批量获取多个面板的规则推荐"""
    from services.unified_recommendation_engine import UnifiedRecommendationEngine
    from services.panel_analyzers import PANEL_CONFIGS

    invalid = [p for p in request.panels if p not in PANEL_CONFIGS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown panels: {invalid}")

    engine = UnifiedRecommendationEngine()
    results = {}
    for panel_id in request.panels:
        data = await _gather_panel_data(panel_id, request.currency)
        results[panel_id] = engine.analyze(panel_id, data, request.currency)

    return {"currency": request.currency, "results": results}


@router.post("/recommendation/{panel_id}/llm")
async def trigger_llm_analysis(
    panel_id: str,
    request: LLMTriggerRequest,
):
    """触发 LLM 深度分析（SSE 流式返回）"""
    from services.unified_recommendation_engine import UnifiedRecommendationEngine, LLMPromptBuilder
    from services.panel_analyzers import PANEL_CONFIGS

    if panel_id not in PANEL_CONFIGS:
        raise HTTPException(status_code=400, detail=f"Unknown panel: {panel_id}")

    data = await _gather_panel_data(panel_id, request.currency)
    engine = UnifiedRecommendationEngine()
    rec = engine.analyze(panel_id, data, request.currency)

    # 检查缓存
    import hashlib
    import json
    input_str = json.dumps({"report": rec["report"], "data": rec["data_snapshot"]}, sort_keys=True, default=str)
    input_hash = hashlib.md5(input_str.encode()).hexdigest()

    if not request.force_refresh:
        try:
            from db.connection import execute_read
            rows = execute_read(
                "SELECT analysis_json FROM llm_analysis_cache WHERE panel_id=? AND currency=? AND input_hash=?",
                (panel_id, request.currency.upper(), input_hash)
            )
            if rows:
                cached = json.loads(rows[0][0])
                return {"llm_analysis": cached, "cached": True}
        except Exception:
            pass

    # 构建 prompt
    prompt = LLMPromptBuilder.build(panel_id, rec["report"], rec["data_snapshot"], request.currency)

    # 调用 LLMAnalystEngine
    try:
        from services.llm_analyst import LLMAnalystEngine
        # 简化版: 只用合成+辩论（不用完整的5-agent流程）
        from services.ai_router import ai_chat_with_config
        from fastapi.concurrency import run_in_threadpool

        # Synthesis step
        synth_result = await run_in_threadpool(
            ai_chat_with_config, prompt["synthesis"], preset="analysis", max_tokens=1500
        )
        synth_text = synth_result.get("content", "") if isinstance(synth_result, dict) else str(synth_result)

        # Bull debate
        bull_prompt = f"{prompt['bull_context']}\n\n数据背景:\n{prompt['synthesis']}"
        bull_result = await run_in_threadpool(
            ai_chat_with_config, bull_prompt, preset="analysis", max_tokens=1000
        )
        bull_text = bull_result.get("content", "") if isinstance(bull_result, dict) else str(bull_result)

        # Bear debate
        bear_prompt = f"{prompt['bear_context']}\n\n数据背景:\n{prompt['synthesis']}"
        bear_result = await run_in_threadpool(
            ai_chat_with_config, bear_prompt, preset="analysis", max_tokens=1000
        )
        bear_text = bear_result.get("content", "") if isinstance(bear_result, dict) else str(bear_result)

        # Judge
        judge_prompt = (
            f"## 背景\n{prompt['synthesis']}\n\n"
            f"## 多头观点\n{bull_text}\n\n"
            f"## 空头观点\n{bear_text}\n\n"
            f"## 判定标准\n{prompt['judge_criteria']}\n\n"
            "综合多空双方观点给出最终判決。"
        )
        judge_result = await run_in_threadpool(
            ai_chat_with_config, judge_prompt, preset="analysis", max_tokens=1500
        )
        judge_text = judge_result.get("content", "") if isinstance(judge_result, dict) else str(judge_result)

        llm_analysis = {
            "synthesis": synth_text,
            "bull_debate": {"argument": bull_text, "score": 7.0},
            "bear_debate": {"argument": bear_text, "score": 6.0},
            "judge_verdict": judge_text,
            "audit": {"hallucination_score": 0.0, "data_citations": []},
            "model_used": "lite-llm-routed",
            "tokens": {"input": 0, "output": 0},
        }

        # 存入缓存
        try:
            from db.connection import execute_write
            execute_write(
                """INSERT OR REPLACE INTO llm_analysis_cache
                   (panel_id, currency, input_hash, analysis_json, model_used, tokens_input, tokens_output)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (panel_id, request.currency.upper(), input_hash,
                 json.dumps(llm_analysis, ensure_ascii=False),
                 llm_analysis["model_used"], 0, 0)
            )
        except Exception as e:
            logger.debug("LLM cache save failed: %s", e)

        return {"llm_analysis": llm_analysis, "cached": False}

    except Exception as e:
        logger.error("LLM analysis failed for panel %s: %s", panel_id, e)
        raise HTTPException(status_code=500, detail=f"LLM分析失败: {e}")


async def _gather_panel_data(panel_id: str, currency: str) -> dict:
    """为面板收集所需数据"""
    from db.async_connection import execute_read_async
    import json

    data: dict = {"currency": currency}

    # 获取最新扫描记录
    try:
        rows = await execute_read_async(
            """SELECT spot_price, dvol_current, dvol_z_score, dvol_signal,
                      contracts_data, large_trades_details, raw_output
               FROM scan_records WHERE currency=? ORDER BY timestamp DESC LIMIT 1""",
            (currency,)
        )
        if rows:
            row = rows[0]
            data["spot"] = row[0] or 0
            data["dvol"] = row[1] or 0
            data["dvol_z"] = row[2] or 0
            data["dvol_signal"] = row[3] or "normal"

            contracts_json = row[4] or "[]"
            trades_json = row[5] or "[]"
            try:
                data["contracts"] = json.loads(contracts_json) if isinstance(contracts_json, str) else contracts_json
            except json.JSONDecodeError:
                data["contracts"] = []
            try:
                data["large_trades"] = json.loads(trades_json) if isinstance(trades_json, str) else trades_json
            except json.JSONDecodeError:
                data["large_trades"] = []

            # 解析 raw_output 获取更多数据
            raw_json = row[6] or "{}"
            try:
                raw = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
                inner = raw.get("dvol_raw", {})
                data["term_premium"] = inner.get("term_premium", 0)
                data["iv_steepness"] = inner.get("iv_steepness", 0)
                data["curve_shape"] = inner.get("curve_shape", "")
                data["dvol_signal"] = inner.get("signal", data["dvol_signal"])
            except json.JSONDecodeError:
                pass
    except Exception as e:
        logger.warning("_gather_panel_data scan query failed: %s", e)

    # 补充其他数据源
    data.setdefault("spot", 0)
    data.setdefault("fear_greed", 50)
    data.setdefault("trend_strength", 0)
    data.setdefault("pcr", 1.0)
    data.setdefault("funding_rate", 0)
    data.setdefault("max_pain", 0)
    data.setdefault("skew", 0)
    data.setdefault("kurtosis", 0)
    data.setdefault("gex", 0)
    data.setdefault("gamma_flip", 0)
    data.setdefault("mvrv", 2.0)
    data.setdefault("net_flow", 0)
    data.setdefault("margin_ratio", 0.2)

    # 获取恐贪指数
    try:
        from services.macro_data import get_fear_greed_index
        from fastapi.concurrency import run_in_threadpool
        fg_data = await run_in_threadpool(get_fear_greed_index)
        data["fear_greed"] = fg_data.get("value", 50) if isinstance(fg_data, dict) else 50
    except Exception:
        pass

    # 获取 PCR
    try:
        from services.monitors import get_deribit_monitor
        mon = get_deribit_monitor()
        summaries = mon._get_book_summaries(currency)
        if summaries:
            puts = sum(s.get("open_interest", 0) for s in summaries if s and s.get("option_type") in ("P", "PUT"))
            calls = sum(s.get("open_interest", 0) for s in summaries if s and s.get("option_type") in ("C", "CALL"))
            data["pcr"] = puts / max(calls, 1)
    except Exception:
        pass

    # 获取 MaxPain
    try:
        from routers.maxpain import _compute_max_pain
        data["max_pain"] = _compute_max_pain(data.get("contracts", []))
    except Exception:
        pass

    return data
```

- [ ] **Step 4: 更新 api/__init__.py 导出新路由**

```python
# 在现有导入后添加:
from .recommendations import router as recommendations_router

# 在 __all__ 列表中添加:
"recommendations_router",
```

- [ ] **Step 5: 在 main.py 注册路由**

在 `main.py` 的 import 行（line 252-257）中添加 `recommendations_router`：
在 `app.include_router(llm_analyst_router, ...)` 行后添加：
```python
app.include_router(recommendations_router, dependencies=protected_dependencies)
```

- [ ] **Step 6: 运行 API 测试确认通过**

Run: `python -m pytest tests/test_recommendations_api.py -v --tb=short`
Expected: ALL PASS

- [ ] **Step 7: 提交**

```bash
git add api/recommendations.py api/__init__.py main.py tests/test_recommendations_api.py
git commit -m "feat: add recommendation API endpoints with LLM trigger and data gathering

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: 数据库 + 配置变更

**Files:**
- Modify: `db/schema.py`
- Modify: `config.py`

- [ ] **Step 1: 添加新表到 schema.py**

在 `db/schema.py` 文件末尾（`ensure_top_contracts_column` 函数之前的位置）添加：

```python
SCHEMA_LLM_ANALYSIS_CACHE = """
CREATE TABLE IF NOT EXISTS llm_analysis_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    panel_id TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'BTC',
    input_hash TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    model_used TEXT,
    tokens_input INTEGER,
    tokens_output INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(panel_id, currency, input_hash)
)
"""

SCHEMA_LLM_USAGE_LOG = """
CREATE TABLE IF NOT EXISTS llm_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    panel_id TEXT NOT NULL,
    model TEXT,
    tokens_input INTEGER,
    tokens_output INTEGER,
    latency_ms INTEGER,
    cost_estimate REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""
```

在 `init_database_schema()` 函数中添加：
```python
cursor.execute(SCHEMA_LLM_ANALYSIS_CACHE)
cursor.execute(SCHEMA_LLM_USAGE_LOG)
```

在 `INDEXES` 列表中添加：
```python
"CREATE INDEX IF NOT EXISTS idx_llm_cache_lookup ON llm_analysis_cache(panel_id, currency, created_at DESC)",
"CREATE INDEX IF NOT EXISTS idx_llm_usage_panel ON llm_usage_log(panel_id, created_at DESC)",
```

- [ ] **Step 2: 添加 LLM 配置到 config.py**

在 `config.py` 的 `_load_all()` 方法中，`# === 并发配置 ===` 段落后添加：

```python
        # === LLM 分析配置 ===
        self.LLM_ANALYSIS_ENABLED = _get_env("LLM_ANALYSIS_ENABLED", True, env)
        self.LLM_CACHE_TTL_SECONDS = _get_env("LLM_CACHE_TTL_SECONDS", 3600, env)
        self.LLM_MAX_TOKENS_PER_PANEL = _get_env("LLM_MAX_TOKENS_PER_PANEL", 4000, env)
        self.LLM_DEFAULT_MODEL = _get_env("LLM_DEFAULT_MODEL", "claude-sonnet-4-6", env)
        self.LLM_FALLBACK_CHAIN = _get_env("LLM_FALLBACK_CHAIN", "claude-haiku-4-5,gpt-4o-mini", env).split(",")
        self.LLM_STREAMING_ENABLED = _get_env("LLM_STREAMING_ENABLED", True, env)
```

- [ ] **Step 3: 验证 DB schema 初始化**

Run: `python -c "from db.schema import init_database_schema; from db.connection import get_db_connection; conn = get_db_connection(read_only=False); init_database_schema(conn); cursor = conn.cursor(); cursor.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'llm_%'\"); print([r[0] for r in cursor.fetchall()]); conn.close()"`
Expected: `['llm_config', 'llm_analysis_results', 'llm_analysis_cache', 'llm_usage_log']`

- [ ] **Step 4: 提交**

```bash
git add db/schema.py config.py
git commit -m "feat: add LLM cache, usage log tables and LLM config variables

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: 前端渲染器和 LLM 抽屉组件

**Files:**
- Create: `static/recommendations.js`
- Modify: `static/index.html`

- [ ] **Step 1: 创建前端 recommendations.js**

```javascript
/* static/recommendations.js
 * 统一投资推荐前端渲染器
 *
 * 组件:
 *   RecommendationRenderer — 信号灯 + 展开规则报告
 *   LLMDrawer            — 右侧全屏 LLM 分析抽屉
 *   SummaryBar            — 顶部全板块信号汇总条
 */

// ============================================================
// RecommendationRenderer
// ============================================================
const RecommendationRenderer = {
    /**
     * 为指定面板渲染信号灯
     * @param {string} panelId - 面板ID (如 "risk_command_center")
     * @param {HTMLElement} container - 放置信号灯的容器元素
     * @param {object} options - {currency, showAction, actionCallback}
     */
    async renderSignal(panelId, container, options = {}) {
        const currency = options.currency || 'BTC';
        try {
            const resp = await fetch(
                `/api/recommendation/${panelId}?currency=${currency}`
            );
            const data = await resp.json();
            this._render(panelId, container, data, options);
        } catch (err) {
            console.warn(`Recommendation fetch failed for ${panelId}:`, err);
        }
    },

    _render(panelId, container, data, options) {
        const signal = data.signal || {};
        const report = data.report || {};

        const colors = {
            bullish: '#22c55e', bearish: '#ef4444',
            neutral: '#f59e0b', caution: '#ef4444'
        };
        const color = colors[signal.signal] || '#9497a9';

        const wrapper = document.createElement('div');
        wrapper.className = 'rec-signal-wrapper';
        wrapper.style.cssText = 'display:inline-flex;align-items:center;gap:4px;';

        // Signal badge
        const badge = document.createElement('span');
        badge.className = 'rec-signal-badge cursor-pointer';
        badge.style.cssText = `font-size:0.7rem;padding:2px 6px;border-radius:3px;background:${color}20;color:${color};border:1px solid ${color}40;transition:all 0.15s;`;
        badge.title = signal.signal_text || '';
        badge.innerHTML = `${signal.signal_emoji || '⚪'} ${signal.signal_text || '--'}`;

        // Click to expand report
        const reportPanel = document.createElement('div');
        reportPanel.className = 'rec-report-panel hidden';
        reportPanel.style.cssText = 'margin-top:8px;padding:10px;background:rgba(34,35,46,0.5);border-radius:8px;border:1px solid rgba(71,73,85,0.3);font-size:0.78rem;';

        badge.addEventListener('click', () => {
            reportPanel.classList.toggle('hidden');
            if (!reportPanel.classList.contains('hidden') && !reportPanel.dataset.loaded) {
                this._renderReport(reportPanel, report, panelId, color);
                reportPanel.dataset.loaded = '1';
            }
        });

        wrapper.appendChild(badge);
        container.appendChild(wrapper);

        // Place report panel after wrapper
        wrapper.parentNode.insertBefore(reportPanel, wrapper.nextSibling);
    },

    _renderReport(panel, report, panelId, accentColor) {
        const factors = (report.factors || []).map(f =>
            `<div style="display:flex;justify-content:space-between;margin-bottom:4px;">
                <span>${f.name}</span>
                <span style="color:${accentColor};font-weight:600;">${f.score}/${f.max}</span>
            </div>
            <div style="font-size:0.68rem;color:#9497a9;margin-bottom:6px;">${f.verdict || ''}</div>`
        ).join('');

        const logic = (report.logic_chain || []).map(l =>
            `<div style="font-size:0.68rem;color:#e4e4e7;margin-bottom:2px;">${l}</div>`
        ).join('');

        const riskFlags = (report.risk_flags || []).map(r =>
            `<span style="background:#ef444420;color:#ef4444;padding:2px 6px;border-radius:3px;font-size:0.65rem;margin-right:4px;">${r}</span>`
        ).join('');

        panel.innerHTML = `
            <div style="color:#e4e4e7;margin-bottom:8px;line-height:1.5;">${report.summary || '暂无分析'}</div>
            <div style="margin-bottom:8px;">${factors}</div>
            <div style="border-top:1px solid rgba(71,73,85,0.3);padding-top:6px;margin-bottom:6px;">${logic}</div>
            <div style="margin-bottom:12px;padding:6px;background:rgba(34,35,46,0.5);border-radius:4px;border-left:3px solid ${accentColor};">
                <span style="font-size:0.7rem;color:#9497a9;">建议: </span>
                <span style="font-weight:600;color:#e4e4e7;">${report.suggested_action || '--'}</span>
            </div>
            ${riskFlags ? `<div style="margin-bottom:8px;">${riskFlags}</div>` : ''}
            <button onclick="LLMDrawer.open('${panelId}')"
                style="width:100%;padding:6px;background:rgba(113,50,245,0.2);border:1px solid #7132f5;border-radius:6px;color:#e4e4e7;font-size:0.75rem;cursor:pointer;">
                🤖 LLM 深度分析
            </button>
        `;
    },

    /** Batch render signals for multiple panels */
    async renderAll(panelIds, containers, options = {}) {
        const currency = options.currency || 'BTC';
        const resp = await fetch('/api/recommendations/summary?currency=' + currency);
        const summary = await resp.json();
        const signals = summary.signals || [];

        for (const sig of signals) {
            const idx = panelIds.indexOf(sig.panel_id);
            if (idx >= 0 && containers[idx]) {
                const badge = document.createElement('span');
                badge.className = 'rec-signal-badge';
                badge.style.cssText = `font-size:0.7rem;padding:2px 6px;border-radius:3px;cursor:pointer;`;
                badge.title = sig.signal_text;
                badge.innerHTML = sig.signal_emoji + ' ' + sig.signal_text;
                containers[idx].appendChild(badge);
            }
        }
    }
};


// ============================================================
// SummaryBar — 顶部全板块信号汇总
// ============================================================
const SummaryBar = {
    async render(containerId, currency = 'BTC') {
        const container = document.getElementById(containerId);
        if (!container) return;

        try {
            const resp = await fetch('/api/recommendations/summary?currency=' + currency);
            const data = await resp.json();
            const signals = data.signals || [];

            const colorMap = {
                bullish: '#22c55e', bearish: '#ef4444',
                neutral: '#f59e0b', caution: '#ef4444'
            };

            let html = '<div style="display:flex;gap:4px;flex-wrap:wrap;align-items:center;">';
            html += '<span style="font-size:0.7rem;color:#9497a9;margin-right:8px;">全板块信号:</span>';
            for (const s of signals) {
                const c = colorMap[s.signal] || '#686b82';
                html += `<span title="${s.signal_text}"
                    style="width:10px;height:10px;border-radius:50%;background:${c};display:inline-block;cursor:pointer;"
                    onclick="RecommendationRenderer.renderSignal('${s.panel_id}', this.parentNode, {currency:'${currency}'})">
                </span>`;
            }
            html += '</div>';
            container.innerHTML = html;
        } catch (err) {
            console.warn('SummaryBar render failed:', err);
        }
    }
};


// ============================================================
// LLMDrawer — LLM 分析抽屉
// ============================================================
const LLMDrawer = {
    _panelId: null,
    _container: null,

    /** Create drawer if not exists, then open */
    open(panelId) {
        this._panelId = panelId;
        this._ensureContainer();
        this._container.classList.remove('hidden');
        this._load(panelId);
    },

    close() {
        if (this._container) {
            this._container.classList.add('hidden');
        }
    },

    _ensureContainer() {
        if (this._container) return;
        const drawer = document.createElement('div');
        drawer.id = 'llmDrawer';
        drawer.className = 'hidden';
        drawer.style.cssText = `
            position:fixed;top:0;right:0;width:480px;max-width:100vw;height:100vh;
            background:#1a1b23;border-left:1px solid rgba(71,73,85,0.3);
            z-index:9999;overflow-y:auto;padding:20px;
            box-shadow:-4px 0 20px rgba(0,0,0,0.5);
        `;
        drawer.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
                <h3 style="color:white;font-size:1rem;">🤖 LLM 深度分析</h3>
                <button onclick="LLMDrawer.close()" style="background:none;border:none;color:#9497a9;font-size:1.2rem;cursor:pointer;">✕</button>
            </div>
            <div id="llmDrawerContent" style="color:#e4e4e7;font-size:0.82rem;line-height:1.6;">
                <div style="text-align:center;padding:40px 0;">
                    <div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-[#7132f5]"></div>
                    <p style="margin-top:8px;color:#9497a9;">LLM 分析中...</p>
                </div>
            </div>
        `;
        document.body.appendChild(drawer);
        this._container = drawer;
    },

    async _load(panelId) {
        const content = document.getElementById('llmDrawerContent');
        if (!content) return;

        try {
            const resp = await fetch(`/api/recommendation/${panelId}/llm`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({currency: (window._activeCurrency || 'BTC'), force_refresh: false})
            });
            const data = await resp.json();
            const analysis = data.llm_analysis || {};
            const cached = data.cached ? ' (缓存)' : '';

            content.innerHTML = `
                <div style="margin-bottom:12px;">
                    <span style="background:#7132f5;color:white;padding:2px 8px;border-radius:3px;font-size:0.65rem;">
                        模型: ${analysis.model_used || '--'}${cached}
                    </span>
                </div>
                <div style="margin-bottom:16px;padding:12px;background:rgba(34,35,46,0.5);border-radius:8px;border-left:3px solid #7132f5;">
                    <h4 style="color:#7132f5;margin-bottom:6px;font-size:0.8rem;">📊 综合研判</h4>
                    <p style="white-space:pre-wrap;">${analysis.synthesis || '--'}</p>
                </div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;">
                    <div style="padding:10px;background:rgba(34,197,94,0.08);border-radius:8px;border:1px solid rgba(34,197,94,0.3);">
                        <h4 style="color:#22c55e;margin-bottom:4px;font-size:0.75rem;">🐂 多头观点</h4>
                        <p style="font-size:0.73rem;white-space:pre-wrap;">${(analysis.bull_debate || {}).argument || '--'}</p>
                    </div>
                    <div style="padding:10px;background:rgba(239,68,68,0.08);border-radius:8px;border:1px solid rgba(239,68,68,0.3);">
                        <h4 style="color:#ef4444;margin-bottom:4px;font-size:0.75rem;">🐻 空头观点</h4>
                        <p style="font-size:0.73rem;white-space:pre-wrap;">${(analysis.bear_debate || {}).argument || '--'}</p>
                    </div>
                </div>
                <div style="padding:12px;background:rgba(245,158,11,0.08);border-radius:8px;border:1px solid rgba(245,158,11,0.3);">
                    <h4 style="color:#f59e0b;margin-bottom:6px;font-size:0.8rem;">⚖️ 最终判决</h4>
                    <p style="white-space:pre-wrap;">${analysis.judge_verdict || '--'}</p>
                </div>
            `;
        } catch (err) {
            content.innerHTML = `<p style="color:#ef4444;">LLM 分析失败: ${err.message}</p>`;
        }
    }
};


// Auto-init when DOM ready
document.addEventListener('DOMContentLoaded', () => {
    // Render summary bar
    SummaryBar.render('recSummaryBar', 'BTC');
});
```

- [ ] **Step 2: 在 index.html 中添加 summary bar + JS 引用**

在 `index.html` 的 `<header>` 区域下方（`</header>` 标签后）添加：
```html
<!-- 全板块信号汇总条 -->
<div id="recSummaryBar" class="mb-4 px-1" style="overflow-x:auto;"></div>
```

在 `index.html` 的 `<script>` 区域（`<script src="/static/app.js">` 之前）添加：
```html
<script src="/static/recommendations.js"></script>
```

- [ ] **Step 3: 验证前端无JS语法错误**

Run: `node -c static/recommendations.js`
Expected: No syntax error output

- [ ] **Step 4: 提交**

```bash
git add static/recommendations.js static/index.html
git commit -m "feat: add recommendation frontend renderer with signal badges, report panels, and LLM drawer

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: 前端面板信号灯注入

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: 在 16 个面板的渲染函数中注入信号灯**

在 `static/app.js` 中找到每个面板的渲染函数，在函数末尾添加信号灯渲染调用。

具体修改 — 每个面板对应的注入点：

```javascript
// Panel: 风险指挥中心 (risk_command_center)
// 找到 updateRiskDashboard() 函数末尾，在 return 之前添加:
RecommendationRenderer.renderSignal('risk_command_center',
    document.querySelector('#riskDashboard .flex.items-center.justify-between'),
    {currency: currentCurrency});

// Panel: 策略推荐中心 (strategy_center)
// 找到策略渲染函数末尾:
RecommendationRenderer.renderSignal('strategy_center',
    document.querySelector('#strategyModeTabs'),
    {currency: currentCurrency});

// Panel: IV期限结构 (iv_term_structure)
// 找到 term structure 渲染函数末尾:
RecommendationRenderer.renderSignal('iv_term_structure',
    document.querySelector('#ivTermStructureSection .flex.items-center'),
    {currency: currentCurrency});

// Panel: IV Smile (iv_smile)
RecommendationRenderer.renderSignal('iv_smile',
    document.querySelector('#ivSmileSection .flex.items-center.justify-between'),
    {currency: currentCurrency});

// Panel: DVOL趋势 (dvol_trend)
// 在 dvol 图表区域
RecommendationRenderer.renderSignal('dvol_trend',
    document.querySelector('#dvolSection .flex.items-center'),
    {currency: currentCurrency});

// Panel: PCR图表 (pcr_chart)
RecommendationRenderer.renderSignal('pcr_chart',
    document.querySelector('#pcrSection'),
    {currency: currentCurrency});

// Panel: MaxPain/GammaFlip (max_pain)
RecommendationRenderer.renderSignal('max_pain',
    document.querySelector('#maxPainSection .flex.items-center'),
    {currency: currentCurrency});

// Panel: 大单追踪 (large_trades)
RecommendationRenderer.renderSignal('large_trades',
    document.querySelector('#largeTradesSection'),
    {currency: currentCurrency});

// Panel: Greeks风险矩阵 (greeks_matrix)
RecommendationRenderer.renderSignal('greeks_matrix',
    document.querySelector('#greeksSection .flex.items-center.justify-between'),
    {currency: currentCurrency});

// Panel: AI分析中心 (ai_analyst_center)
RecommendationRenderer.renderSignal('ai_analyst_center',
    document.querySelector('#llmAnalystSection .flex.items-center'),
    {currency: currentCurrency});

// Panel: 马丁格尔沙盒 (martingale_sandbox)
RecommendationRenderer.renderSignal('martingale_sandbox',
    document.querySelector('#martingaleSection'),
    {currency: currentCurrency});

// Panel: 实时机会列表 (opportunities_table)
RecommendationRenderer.renderSignal('opportunities_table',
    document.querySelector('#opportunitiesSection .flex.items-center'),
    {currency: currentCurrency});

// Panel: GEX图表 (gex_chart)
RecommendationRenderer.renderSignal('gex_chart',
    document.querySelector('#gexSection .flex.items-center'),
    {currency: currentCurrency});

// Panel: 资金流向 (money_flow)
RecommendationRenderer.renderSignal('money_flow',
    document.querySelector('#moneyFlowSection'),
    {currency: currentCurrency});

// Panel: 链上指标 (onchain_metrics)
RecommendationRenderer.renderSignal('onchain_metrics',
    document.querySelector('#onchainSection'),
    {currency: currentCurrency});

// Panel: 顶部指标卡 (metric_cards)
// 在 updateMetricCards() 函数末尾:
RecommendationRenderer.renderSignal('metric_cards',
    document.querySelector('section.grid.grid-cols-2.md\\:grid-cols-6'),
    {currency: currentCurrency});
```

- [ ] **Step 2: 使用 Agent 子代理精确查找和注入每个信号灯位置**

每个注入使用 `Edit` 工具定位具体函数，添加 `RecommendationRenderer.renderSignal(...)` 调用。关键原则：
- 在每个面板的 `update*/render*` 函数末尾注入
- 容器选择器使用面板 header 的 flex 元素
- 确保 `currentCurrency` 变量可用（全局或局部）

- [ ] **Step 3: 验证 app.js 语法**

Run: `node -c static/app.js`
Expected: No syntax error output

- [ ] **Step 4: 提交**

```bash
git add static/app.js
git commit -m "feat: inject recommendation signal badges into all 16 dashboard panels

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7: 集成测试和手动验证

**Files:**
- Test: `tests/test_unified_recommendation.py` (extend)

- [ ] **Step 1: 编写集成测试**

```python
# 追加到 tests/test_unified_recommendation.py

class TestIntegration:
    def test_all_panels_produce_valid_output(self):
        """所有16个面板都能在基本数据下产生有效输出"""
        from services.unified_recommendation_engine import UnifiedRecommendationEngine
        engine = UnifiedRecommendationEngine()
        minimal_data = {"spot": 90000, "dvol": 62, "dvol_z": 0.8,
                        "fear_greed": 35, "trend_strength": 0.2,
                        "contracts": [], "large_trades": [], "pcr": 1.2}
        for panel_id in engine.panels:
            result = engine.analyze(panel_id, minimal_data)
            assert result["panel_id"] == panel_id, f"Failed for {panel_id}"
            assert result["signal"]["signal"] in ("bullish","bearish","neutral","caution"), \
                f"Invalid signal for {panel_id}: {result['signal']}"
            assert isinstance(result["report"]["factors"], list), \
                f"No factors for {panel_id}"
            assert "summary" in result["report"]

    def test_signal_distribution_is_reasonable(self):
        """信号分布合理 —— 不会全部同一边"""
        from services.unified_recommendation_engine import UnifiedRecommendationEngine
        engine = UnifiedRecommendationEngine()
        data = {"spot": 90000, "dvol": 62, "dvol_z": 0.8,
                "fear_greed": 35, "trend_strength": 0.2,
                "contracts": [], "large_trades": [], "pcr": 1.2}
        results = engine.analyze_all(data)
        signals = [r["signal"]["signal"] for r in results.values()]
        unique = set(signals)
        assert len(unique) >= 2, f"All panels have same signal: {unique}"

    def test_greek_analyzer_wrapper_handles_missing_module(self):
        """包装器 graceful degradation"""
        from services.panel_analyzers import wrap_greeks_analyzer, wrap_unified_risk
        result = wrap_greeks_analyzer({"spot": 90000, "greeks": {}}, {})
        assert result.score >= 0
        assert len(result.verdict) > 0

    def test_llm_prompt_builder_all_panels(self):
        """每个面板的 LLM prompt 模板都能正常构建"""
        from services.unified_recommendation_engine import LLMPromptBuilder
        from services.panel_analyzers import PANEL_CONFIGS
        report = {"summary": "test", "factors": [{"name":"F1","score":80,"verdict":"好"}],
                  "logic_chain": ["L1"], "suggested_action": "建议", "risk_flags": []}
        data = {"spot": 90000, "dvol": 62, "dvol_z": 0.8}
        for panel_id in PANEL_CONFIGS:
            prompt = LLMPromptBuilder.build(panel_id, report, data)
            assert prompt["synthesis"]
            assert "bull_context" in prompt
            assert "bear_context" in prompt
            assert "judge_criteria" in prompt
```

- [ ] **Step 2: 运行全部测试**

Run: `python -m pytest tests/test_unified_recommendation.py tests/test_recommendations_api.py -v`
Expected: ALL PASS

- [ ] **Step 3: 启动仪表盘手动验证**

Run: `python main.py`
在浏览器访问 `http://localhost:PORT`，验证:
- 顶部信号汇总条正常显示（16个彩色圆点）
- 每个面板左上角出现信号灯 badge
- 点击信号灯展开规则报告
- 点击"LLM 深度分析"按钮打开抽屉

- [ ] **Step 4: 提交**

```bash
git add tests/test_unified_recommendation.py
git commit -m "test: add integration tests for all 16 panels and LLM prompt builder

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```
