# AI 研判中心 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace debate section + copilot floating chat with unified "AI 研判中心" — LLM-powered comprehensive analysis, Bull/Bear debate, and data anomaly detection on top of existing 5 rule-based agents.

**Architecture:** New `services/llm_analyst.py` (LLMAnalystEngine) gathers full data context, runs existing rule engine, then makes 4 LLM calls (synthesis, bull, bear, audit) via `ai_router.ai_chat_with_config()`. New `api/llm_analyst.py` exposes 3 endpoints. Frontend replaces debateSection + copilotWidget with unified section. LLM config stored in SQLite instead of localStorage.

**Tech Stack:** FastAPI, SQLite (WAL), LiteLLM via `ai_router`, Chart.js, Pydantic

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `services/llm_analyst.py` | Create | LLMAnalystEngine: data gathering, 4 LLM calls, result assembly |
| `api/llm_analyst.py` | Create | 3 API endpoints (analyze, config, test) |
| `db/schema.py` | Modify | Add `llm_config` and `llm_analysis_results` tables |
| `api/__init__.py` | Modify | Register `llm_analyst_router` |
| `main.py` | Modify | Import + include `llm_analyst_router` |
| `static/index.html` | Modify | Replace debateSection + copilotWidget with AI 研判中心 section |
| `static/app.js` | Modify | Add LLM analyst JS, remove debate/copilot JS |
| `tests/test_llm_analyst.py` | Create | Unit tests for LLMAnalystEngine |

---

### Task 1: SQLite Schema — llm_config + llm_analysis_results tables

**Files:**
- Modify: `db/schema.py`
- Test: manual verification via Python REPL

- [ ] **Step 1: Add schema constants to `db/schema.py`**

Read `db/schema.py` first. Add two new schema constants after the existing `SCHEMA_MAX_PAIN_HISTORY`:

```python
SCHEMA_LLM_CONFIG = """
CREATE TABLE IF NOT EXISTS llm_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    api_key TEXT DEFAULT '',
    base_url TEXT DEFAULT '',
    model TEXT DEFAULT '',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""

SCHEMA_LLM_ANALYSIS_RESULTS = """
CREATE TABLE IF NOT EXISTS llm_analysis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    currency TEXT NOT NULL,
    mode TEXT DEFAULT 'full',
    result_json TEXT,
    llm_config_json TEXT,
    success INTEGER DEFAULT 1,
    timestamp DATETIME NOT NULL
)
"""
```

- [ ] **Step 2: Add indexes**

Add to the `INDEXES` list in `schema.py`:

```python
"CREATE INDEX IF NOT EXISTS idx_llm_analysis_currency_timestamp ON llm_analysis_results(currency, timestamp DESC)",
```

- [ ] **Step 3: Register tables in `init_database_schema()`**

Find the function `init_database_schema()` in `schema.py`. Add the two new CREATE TABLE statements to the `table_sqls` list alongside existing tables:

```python
SCHEMA_LLM_CONFIG,
SCHEMA_LLM_ANALYSIS_RESULTS,
```

- [ ] **Step 4: Verify schema creation**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -c "from db.schema import init_database_schema; init_database_schema(); print('OK')"`

Expected: `OK` (no errors)

- [ ] **Step 5: Commit**

```bash
git add db/schema.py
git commit -m "feat: add llm_config and llm_analysis_results SQLite tables"
```

---

### Task 2: LLMAnalystEngine — `_prepare_context()` data gathering

**Files:**
- Create: `services/llm_analyst.py`
- Create: `tests/test_llm_analyst.py`

- [ ] **Step 1: Write failing test for context preparation**

Create `tests/test_llm_analyst.py`:

```python
"""Tests for LLM Analyst Engine"""
import json
import pytest
from unittest.mock import patch, MagicMock


class TestPrepareContext:
    """Test _prepare_context data gathering"""

    @patch("services.llm_analyst._gather_market_data")
    @patch("services.llm_analyst.OnChainMetrics")
    @patch("services.llm_analyst.DerivativeMetrics")
    @patch("services.llm_analyst.get_all_macro_data")
    @patch("services.llm_analyst.IVTermStructureAnalyzer")
    def test_prepare_context_returns_all_sections(
        self, mock_iv, mock_macro, mock_deriv, mock_onchain, mock_gather
    ):
        from services.llm_analyst import LLMAnalystEngine

        mock_gather.return_value = {
            "currency": "BTC",
            "spot": 100000,
            "dvol": {"current": 55.0, "z_score": 0.5, "percentile": 60, "signal": "neutral", "trend": "stable"},
            "large_trades": [{"side": "buy", "notional_usd": 500000}],
            "contracts": [{"strike": 95000, "premium_usd": 2000, "option_type": "P", "dte": 30, "delta": -0.25, "open_interest": 100, "spread_pct": 2.0, "apr": 25.0}],
            "max_pain": 98000,
            "risk_status": "GREEN",
            "risk_label": "🟢 安全",
            "risk_desc": "正常",
            "errors": [],
        }
        mock_onchain.get_all_metrics.return_value = {"mvrv": 1.5, "nupl": 0.4, "convergence_score": 60}
        mock_deriv.get_all_metrics.return_value = {"sharpe_7d": 0.5, "vol_ratio": 1.2, "overheating": False}
        mock_macro.return_value = {"fear_greed": {"value": 50, "classification": "Neutral"}, "funding_rate": {"current_rate": 0.01}}
        mock_iv.return_value.analyze.return_value = {"state": "contango", "slope": 0.02, "curvature": 0.01, "vrp": 5.0}

        engine = LLMAnalystEngine()
        ctx = engine._prepare_context("BTC")

        assert ctx["currency"] == "BTC"
        assert ctx["spot"] == 100000
        assert "dvol" in ctx
        assert "onchain" in ctx
        assert "derivatives" in ctx
        assert "macro" in ctx
        assert "iv_term" in ctx
        assert "contracts" in ctx
        assert "large_trades" in ctx
        assert "max_pain" in ctx
        assert "risk" in ctx

    @patch("services.llm_analyst._gather_market_data")
    def test_prepare_context_handles_missing_data(self, mock_gather):
        from services.llm_analyst import LLMAnalystEngine

        mock_gather.return_value = {
            "currency": "BTC", "spot": 0, "dvol": {}, "large_trades": [],
            "contracts": [], "max_pain": 0, "risk_status": "UNKNOWN",
            "risk_label": "", "risk_desc": "", "errors": ["spot failed"],
        }

        engine = LLMAnalystEngine()
        ctx = engine._prepare_context("BTC")

        assert ctx["currency"] == "BTC"
        assert ctx["spot"] == 0
        assert isinstance(ctx["onchain"], dict)
        assert isinstance(ctx["derivatives"], dict)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -m pytest tests/test_llm_analyst.py::TestPrepareContext -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.llm_analyst'`

- [ ] **Step 3: Implement LLMAnalystEngine with `_prepare_context()`**

Create `services/llm_analyst.py`:

```python
"""
LLM 分析师引擎 — AI 研判中心核心
叠加在 5 个规则 agent 之上的 LLM 综合分析层
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


@dataclass
class LLMAnalysisResult:
    success: bool = False
    currency: str = ""
    timestamp: str = ""
    rule_reports: Dict[str, Any] = field(default_factory=dict)
    synthesis: Dict[str, Any] = field(default_factory=dict)
    debate: Optional[Dict[str, Any]] = None
    audit: Dict[str, Any] = field(default_factory=dict)
    llm_config: Dict[str, Any] = field(default_factory=dict)


class LLMAnalystEngine:
    """LLM 综合分析师引擎"""

    def _prepare_context(self, currency: str) -> Dict[str, Any]:
        """收集全量数据并组装为结构化 JSON，供所有 LLM 调用使用"""
        ctx: Dict[str, Any] = {"currency": currency, "errors": []}

        # 复用 debate engine 的数据收集
        from services.options_debate_engine import _gather_market_data
        md = _gather_market_data(currency)
        ctx["spot"] = md.get("spot", 0)
        ctx["dvol"] = md.get("dvol", {})
        ctx["large_trades"] = md.get("large_trades", [])
        ctx["contracts"] = md.get("contracts", [])
        ctx["max_pain"] = md.get("max_pain", 0)
        ctx["risk"] = {
            "status": md.get("risk_status", "UNKNOWN"),
            "label": md.get("risk_label", ""),
            "desc": md.get("risk_desc", ""),
        }
        ctx["errors"].extend(md.get("errors", []))

        # 链上指标
        try:
            from services.onchain_metrics import OnChainMetrics
            ctx["onchain"] = OnChainMetrics.get_all_metrics()
        except (RuntimeError, ConnectionError, TimeoutError, Exception) as e:
            logger.warning("llm analyst onchain failed: %s", e)
            ctx["onchain"] = {}
            ctx["errors"].append(f"onchain: {e}")

        # 衍生品指标
        try:
            from services.derivative_metrics import DerivativeMetrics
            ctx["derivatives"] = DerivativeMetrics.get_all_metrics()
        except (RuntimeError, ConnectionError, TimeoutError, Exception) as e:
            logger.warning("llm analyst derivatives failed: %s", e)
            ctx["derivatives"] = {}
            ctx["errors"].append(f"derivatives: {e}")

        # 宏观数据
        try:
            from services.macro_data import get_all_macro_data
            ctx["macro"] = get_all_macro_data()
        except (RuntimeError, ConnectionError, TimeoutError, Exception) as e:
            logger.warning("llm analyst macro failed: %s", e)
            ctx["macro"] = {}
            ctx["errors"].append(f"macro: {e}")

        # IV 期限结构
        try:
            from services.iv_term_structure import IVTermStructureAnalyzer
            ctx["iv_term"] = IVTermStructureAnalyzer().analyze(currency)
        except (RuntimeError, ConnectionError, TimeoutError, Exception) as e:
            logger.warning("llm analyst iv_term failed: %s", e)
            ctx["iv_term"] = {}
            ctx["errors"].append(f"iv_term: {e}")

        # 策略引擎结果
        try:
            from services.strategy_engine import StrategyEngine
            from services.dvol_analyzer import get_dvol_from_deribit
            dvol = ctx["dvol"]
            se = StrategyEngine()
            rec = se.recommend(
                ctx["contracts"], currency, "new", "PUT", ctx["spot"],
                100000, 5, dvol
            )
            ctx["strategy_summary"] = {
                "filter_summary": rec.filter_summary,
                "top_recommendations": [
                    {"strike": r["strike"], "premium": r["premium_usd"], "apr": r["apr"],
                     "score": r["scores"]["total"], "rec": r["scores"]["recommendation"]}
                    for r in (rec.recommendations or [])[:3]
                ],
            }
        except (RuntimeError, ValueError, TypeError, Exception) as e:
            logger.warning("llm analyst strategy failed: %s", e)
            ctx["strategy_summary"] = {}
            ctx["errors"].append(f"strategy: {e}")

        return ctx

    def run_full_analysis(self, currency: str, mode: str = "full") -> LLMAnalysisResult:
        """一键全流程分析"""
        result = LLMAnalysisResult(
            currency=currency,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # 1. 准备上下文
        context = self._prepare_context(currency)

        # 2. 运行规则引擎
        from services.options_debate_engine import run_debate
        rule_result = run_debate(currency, quick=False)
        result.rule_reports = {
            "reports": rule_result.get("reports", []),
            "synthesis": rule_result.get("synthesis", {}),
            "market_summary": rule_result.get("market_data_summary", {}),
        }

        # 3. LLM 综合分析
        result.synthesis = self._llm_synthesize(context, result.rule_reports)

        # 4. LLM 多空辩论（full 模式）
        if mode == "full" and result.synthesis.get("success"):
            result.debate = self._llm_debate(context, result.synthesis)

        # 5. LLM 异常检测
        result.audit = self._llm_audit(context, result.rule_reports, result.synthesis)

        result.success = True
        result.llm_config = self._get_llm_config_info()
        return result

    def _get_llm_config_info(self) -> Dict[str, Any]:
        """获取当前 LLM 配置元信息"""
        try:
            from db.connection import execute_read
            rows = execute_read(
                "SELECT api_key, base_url, model FROM llm_config WHERE id=1"
            )
            if rows and rows[0]:
                return {
                    "has_api_key": bool(rows[0][0]),
                    "base_url": rows[0][1] or "",
                    "model": rows[0][2] or "",
                }
        except Exception:
            pass
        return {"has_api_key": False, "base_url": "", "model": ""}

    def _get_custom_config(self) -> Optional[Dict[str, str]]:
        """从数据库读取 LLM 配置，返回 custom_config 格式"""
        try:
            from db.connection import execute_read
            rows = execute_read(
                "SELECT api_key, base_url, model FROM llm_config WHERE id=1"
            )
            if rows and rows[0]:
                api_key = rows[0][0] or ""
                base_url = rows[0][1] or ""
                model = rows[0][2] or ""
                if api_key:
                    cfg = {"api_key": api_key}
                    if base_url:
                        cfg["base_url"] = base_url
                    if model:
                        cfg["model"] = model
                    return cfg
        except Exception as e:
            logger.warning("Failed to read LLM config: %s", e)
        return None

    def _llm_synthesize(self, context: Dict, rule_reports: Dict) -> Dict[str, Any]:
        """LLM 综合分析师 — 留给 Task 3 实现"""
        return {"success": False, "placeholder": "synthesis not implemented"}

    def _llm_debate(self, context: Dict, synthesis: Dict) -> Dict[str, Any]:
        """LLM 多空辩论 — 留给 Task 4 实现"""
        return {"success": False, "placeholder": "debate not implemented"}

    def _llm_audit(self, context: Dict, rule_reports: Dict, synthesis: Dict) -> Dict[str, Any]:
        """LLM 异常检测 — 留给 Task 5 实现"""
        return {"success": False, "placeholder": "audit not implemented"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -m pytest tests/test_llm_analyst.py::TestPrepareContext -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add services/llm_analyst.py tests/test_llm_analyst.py
git commit -m "feat: add LLMAnalystEngine with _prepare_context data gathering"
```

---

### Task 3: LLMAnalystEngine — `_llm_synthesize()` comprehensive analyst

**Files:**
- Modify: `services/llm_analyst.py`
- Modify: `tests/test_llm_analyst.py`

- [ ] **Step 1: Write failing tests for synthesis**

Add to `tests/test_llm_analyst.py`:

```python
class TestLlmSynthesize:
    """Test _llm_synthesize LLM call"""

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_synthesize_parses_valid_json(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = json.dumps({
            "market_assessment": "BTC 在关键支撑位",
            "strategy_recommendation": "Sell Put 95000",
            "risk_warning": "注意波动率上升",
            "confidence": 75,
        })

        engine = LLMAnalystEngine()
        ctx = {"currency": "BTC", "spot": 100000, "dvol": {}, "contracts": [],
               "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
               "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
               "strategy_summary": {}}
        rule_reports = {"reports": [], "synthesis": {}, "market_summary": {}}

        result = engine._llm_synthesize(ctx, rule_reports)

        assert result["success"] is True
        assert result["market_assessment"] == "BTC 在关键支撑位"
        assert result["confidence"] == 75
        mock_ai.assert_called_once()

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_synthesize_handles_llm_failure(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = None

        engine = LLMAnalystEngine()
        ctx = {"currency": "BTC", "spot": 100000, "dvol": {}, "contracts": [],
               "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
               "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
               "strategy_summary": {}}
        rule_reports = {"reports": [], "synthesis": {}, "market_summary": {}}

        result = engine._llm_synthesize(ctx, rule_reports)

        assert result["success"] is False
        assert "error" in result

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_synthesize_handles_malformed_json(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = "这不是 JSON"

        engine = LLMAnalystEngine()
        ctx = {"currency": "BTC", "spot": 100000, "dvol": {}, "contracts": [],
               "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
               "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
               "strategy_summary": {}}
        rule_reports = {"reports": [], "synthesis": {}, "market_summary": {}}

        result = engine._llm_synthesize(ctx, rule_reports)

        assert result["success"] is False
        assert "raw_response" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -m pytest tests/test_llm_analyst.py::TestLlmSynthesize -v`
Expected: FAIL (placeholder returns)

- [ ] **Step 3: Implement `_llm_synthesize()`**

Replace the placeholder `_llm_synthesize` in `services/llm_analyst.py`:

```python
def _llm_synthesize(self, context: Dict, rule_reports: Dict) -> Dict[str, Any]:
    """LLM 综合分析师 — 资深期权策略师"""
    from services.ai_router import ai_chat_with_config

    system_prompt = """你是一位资深加密货币期权策略师。基于以下全量市场数据和规则引擎分析报告，给出综合研判。

要求：
1. market_assessment: 市场整体评估（多空力量、关键位、趋势判断）
2. strategy_recommendation: 策略建议（具体操作、行权价、DTE、理由）
3. risk_warning: 风险提示（需要关注的风险因素）
4. confidence: 信心度（0-100）

请严格返回 JSON 格式，不要添加其他文字。"""

    # 组装数据摘要
    data_summary = self._build_data_summary(context)
    rule_summary = self._build_rule_summary(rule_reports)

    user_prompt = f"""=== 币种: {context['currency']} ===

=== 市场数据 ===
{data_summary}

=== 规则引擎分析 ===
{rule_summary}

请基于以上数据给出综合研判 JSON。"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    custom_config = self._get_custom_config()

    try:
        response = ai_chat_with_config(
            messages, preset="analysis", temperature=0.3,
            max_tokens=2000, custom_config=custom_config
        )
        if not response:
            return {"success": False, "error": "LLM 无响应，请检查 API Key 配置"}

        # 解析 JSON
        parsed = self._parse_json_response(response)
        if parsed is None:
            return {"success": False, "error": "LLM 返回格式异常", "raw_response": response[:500]}

        parsed["success"] = True
        return parsed

    except (RuntimeError, ConnectionError, TimeoutError, ValueError) as e:
        logger.error("llm_synthesize failed: %s", e)
        return {"success": False, "error": str(e)}

def _build_data_summary(self, ctx: Dict) -> str:
    """将全量上下文数据格式化为可读文本"""
    parts = []
    parts.append(f"现货价格: ${ctx.get('spot', 0):,.0f}")

    dvol = ctx.get("dvol", {})
    if dvol:
        parts.append(f"DVOL: {dvol.get('current', 0):.1f} (Z-Score: {dvol.get('z_score', 0):.2f}, 信号: {dvol.get('signal', '')}, 趋势: {dvol.get('trend', '')})")

    risk = ctx.get("risk", {})
    if risk.get("status"):
        parts.append(f"风险状态: {risk.get('label', '')} — {risk.get('desc', '')}")

    onchain = ctx.get("onchain", {})
    if onchain:
        parts.append(f"链上指标: MVRV={onchain.get('mvrv', 'N/A')}, NUPL={onchain.get('nupl', 'N/A')}, 汇合评分={onchain.get('convergence_score', 'N/A')}")

    deriv = ctx.get("derivatives", {})
    if deriv:
        parts.append(f"衍生品: Sharpe7d={deriv.get('sharpe_7d', 'N/A')}, 成交量比={deriv.get('vol_ratio', 'N/A')}, 过热={deriv.get('overheating', 'N/A')}")

    macro = ctx.get("macro", {})
    if macro:
        fg = macro.get("fear_greed", {})
        fr = macro.get("funding_rate", {})
        parts.append(f"宏观: 恐惧贪婪={fg.get('value', 'N/A')}({fg.get('classification', '')}), 资金费率={fr.get('current_rate', 'N/A')}")

    iv = ctx.get("iv_term", {})
    if iv:
        parts.append(f"IV期限结构: 状态={iv.get('state', 'N/A')}, 斜率={iv.get('slope', 'N/A')}, VRP={iv.get('vrp', 'N/A')}")

    parts.append(f"最大痛点: ${ctx.get('max_pain', 0):,.0f}")

    trades = ctx.get("large_trades", [])
    parts.append(f"大单数量: {len(trades)}")
    if trades:
        for t in trades[:5]:
            if isinstance(t, dict):
                parts.append(f"  {t.get('side', '')} ${t.get('notional_usd', 0):,.0f} @ {t.get('strike', '')}")

    contracts = ctx.get("contracts", [])
    parts.append(f"期权合约数: {len(contracts)}")

    strategy = ctx.get("strategy_summary", {})
    if strategy:
        recs = strategy.get("top_recommendations", [])
        if recs:
            parts.append("策略引擎推荐 TOP3:")
            for r in recs:
                parts.append(f"  Strike=${r.get('strike', 0):,.0f} Premium=${r.get('premium', 0):,.0f} APR={r.get('apr', 0):.1f}% Score={r.get('score', 0):.3f} → {r.get('rec', '')}")

    return "\n".join(parts)

def _build_rule_summary(self, rule_reports: Dict) -> str:
    """格式化规则引擎报告"""
    parts = []
    reports = rule_reports.get("reports", [])
    for r in reports:
        parts.append(f"[{r.get('name', '')}] 分数:{r.get('score', 0)} 置信度:{r.get('confidence', 0)}% 判定:{r.get('verdict', '')}")
        for pt in r.get("key_points", []):
            parts.append(f"  - {pt}")

    synthesis = rule_reports.get("synthesis", {})
    if synthesis:
        parts.append(f"\n[规则引擎综合] 评分:{synthesis.get('overall_score', 0)} 建议:{synthesis.get('recommendation_label', '')} 共识:{synthesis.get('consensus', '')}")

    return "\n".join(parts)

def _parse_json_response(self, response: str) -> Optional[Dict]:
    """从 LLM 响应中提取 JSON"""
    # 尝试直接解析
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 块
    import re
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试找到第一个 { 到最后一个 }
    start = response.find('{')
    end = response.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(response[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -m pytest tests/test_llm_analyst.py::TestLlmSynthesize -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/llm_analyst.py tests/test_llm_analyst.py
git commit -m "feat: implement _llm_synthesize comprehensive analyst with prompt assembly"
```

---

### Task 4: LLMAnalystEngine — `_llm_debate()` Bull/Bear debate

**Files:**
- Modify: `services/llm_analyst.py`
- Modify: `tests/test_llm_analyst.py`

- [ ] **Step 1: Write failing tests for debate**

Add to `tests/test_llm_analyst.py`:

```python
class TestLlmDebate:
    """Test _llm_debate Bull/Bear LLM calls"""

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_debate_parses_bull_bear_judge(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        # 3 calls: bull, bear, judge
        mock_ai.side_effect = [
            json.dumps({"bullish_case": "链上数据显示底部", "key_drivers": ["MVRV低位", "资金流入"], "target_scenarios": ["120000"], "confidence": 70}),
            json.dumps({"bearish_case": "宏观风险加大", "key_risks": ["利率上升", "流动性收紧"], "downside_scenarios": ["85000"], "confidence": 60}),
            json.dumps({"judge_verdict": "多头略占优", "winner": "bull", "bull_confidence": 70, "bear_confidence": 60, "reasoning": "链上数据支撑更强"}),
        ]

        engine = LLMAnalystEngine()
        ctx = {"currency": "BTC", "spot": 100000, "dvol": {}, "contracts": [],
               "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
               "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
               "strategy_summary": {}}
        synthesis = {"success": True, "market_assessment": "中性偏多"}

        result = engine._llm_debate(ctx, synthesis)

        assert result["success"] is True
        assert result["bull"]["bullish_case"] == "链上数据显示底部"
        assert result["bear"]["bearish_case"] == "宏观风险加大"
        assert result["judge"]["winner"] == "bull"
        assert mock_ai.call_count == 3

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_debate_handles_partial_failure(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        # bull succeeds, bear fails, judge uses partial data
        mock_ai.side_effect = [
            json.dumps({"bullish_case": "理由", "key_drivers": [], "target_scenarios": [], "confidence": 60}),
            None,  # bear fails
            json.dumps({"judge_verdict": "数据不足", "winner": "inconclusive", "bull_confidence": 60, "bear_confidence": 0, "reasoning": "空头分析失败"}),
        ]

        engine = LLMAnalystEngine()
        ctx = {"currency": "BTC", "spot": 100000, "dvol": {}, "contracts": [],
               "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
               "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
               "strategy_summary": {}}
        synthesis = {"success": True, "market_assessment": "中性"}

        result = engine._llm_debate(ctx, synthesis)

        assert result["success"] is True
        assert result["bull"]["success"] is True
        assert result["bear"]["success"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -m pytest tests/test_llm_analyst.py::TestLlmDebate -v`
Expected: FAIL (placeholder returns)

- [ ] **Step 3: Implement `_llm_debate()`**

Replace the placeholder `_llm_debate` in `services/llm_analyst.py`:

```python
def _llm_debate(self, context: Dict, synthesis: Dict) -> Dict[str, Any]:
    """LLM 多空辩论 — Bull/Bear/Judge 三次调用"""
    from services.ai_router import ai_chat_with_config

    data_summary = self._build_data_summary(context)
    custom_config = self._get_custom_config()

    base_context = f"""=== 币种: {context['currency']} ===
=== 市场数据 ===
{data_summary}

=== 综合分析师报告 ===
{json.dumps(synthesis, ensure_ascii=False, indent=2)}"""

    # Bull Agent
    bull_prompt = [
        {"role": "system", "content": """你是一位看多分析师。基于全量市场数据和综合分析报告，构建最强的看多论点。
返回 JSON:
{"bullish_case": "看多核心论点", "key_drivers": ["驱动因素1", ...], "target_scenarios": ["目标价位1", ...], "confidence": 0-100}"""},
        {"role": "user", "content": base_context},
    ]

    # Bear Agent
    bear_prompt = [
        {"role": "system", "content": """你是一位看空分析师。基于全量市场数据和综合分析报告，构建最强的看空论点。
返回 JSON:
{"bearish_case": "看空核心论点", "key_risks": ["风险因素1", ...], "downside_scenarios": ["下行目标1", ...], "confidence": 0-100}"""},
        {"role": "user", "content": base_context},
    ]

    # 并行执行 bull 和 bear（串行调用，但独立处理错误）
    bull_result = {"success": False}
    bear_result = {"success": False}

    try:
        bull_resp = ai_chat_with_config(
            bull_prompt, preset="analysis", temperature=0.4,
            max_tokens=1500, custom_config=custom_config
        )
        if bull_resp:
            parsed = self._parse_json_response(bull_resp)
            if parsed:
                parsed["success"] = True
                bull_result = parsed
    except (RuntimeError, ConnectionError, TimeoutError, ValueError) as e:
        logger.warning("bull agent failed: %s", e)
        bull_result = {"success": False, "error": str(e)}

    try:
        bear_resp = ai_chat_with_config(
            bear_prompt, preset="analysis", temperature=0.4,
            max_tokens=1500, custom_config=custom_config
        )
        if bear_resp:
            parsed = self._parse_json_response(bear_resp)
            if parsed:
                parsed["success"] = True
                bear_result = parsed
    except (RuntimeError, ConnectionError, TimeoutError, ValueError) as e:
        logger.warning("bear agent failed: %s", e)
        bear_result = {"success": False, "error": str(e)}

    # Judge Agent
    judge_context = f"""{base_context}

=== Bull Agent 分析 ===
{json.dumps(bull_result, ensure_ascii=False, indent=2)}

=== Bear Agent 分析 ===
{json.dumps(bear_result, ensure_ascii=False, indent=2)}"""

    judge_prompt = [
        {"role": "system", "content": """你是辩论裁判。评估多空双方论点，给出裁决。
返回 JSON:
{"judge_verdict": "裁决摘要", "winner": "bull|bear|draw", "bull_confidence": 0-100, "bear_confidence": 0-100, "reasoning": "裁判理由"}"""},
        {"role": "user", "content": judge_context},
    ]

    judge_result = {"success": False}
    try:
        judge_resp = ai_chat_with_config(
            judge_prompt, preset="analysis", temperature=0.2,
            max_tokens=1500, custom_config=custom_config
        )
        if judge_resp:
            parsed = self._parse_json_response(judge_resp)
            if parsed:
                parsed["success"] = True
                judge_result = parsed
    except (RuntimeError, ConnectionError, TimeoutError, ValueError) as e:
        logger.warning("judge agent failed: %s", e)
        judge_result = {"success": False, "error": str(e)}

    return {
        "success": bull_result.get("success") or bear_result.get("success"),
        "bull": bull_result,
        "bear": bear_result,
        "judge": judge_result,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -m pytest tests/test_llm_analyst.py::TestLlmDebate -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add services/llm_analyst.py tests/test_llm_analyst.py
git commit -m "feat: implement _llm_debate Bull/Bear/Judge multi-agent debate"
```

---

### Task 5: LLMAnalystEngine — `_llm_audit()` anomaly detection

**Files:**
- Modify: `services/llm_analyst.py`
- Modify: `tests/test_llm_analyst.py`

- [ ] **Step 1: Write failing tests for audit**

Add to `tests/test_llm_analyst.py`:

```python
class TestLlmAudit:
    """Test _llm_audit anomaly detection"""

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_audit_parses_anomalies(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = json.dumps({
            "anomalies": [
                {"severity": "warning", "source": "DVOL", "description": "DVOL与IV不一致", "suggestion": "检查数据源"}
            ],
            "logic_issues": [],
            "data_quality_score": 85,
        })

        engine = LLMAnalystEngine()
        ctx = {"currency": "BTC", "spot": 100000, "dvol": {}, "contracts": [],
               "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
               "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
               "strategy_summary": {}}
        rule_reports = {"reports": [], "synthesis": {}, "market_summary": {}}
        synthesis = {"success": True}

        result = engine._llm_audit(ctx, rule_reports, synthesis)

        assert result["success"] is True
        assert len(result["anomalies"]) == 1
        assert result["anomalies"][0]["severity"] == "warning"
        assert result["data_quality_score"] == 85

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_audit_handles_llm_failure(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = None

        engine = LLMAnalystEngine()
        ctx = {"currency": "BTC", "spot": 100000, "dvol": {}, "contracts": [],
               "onchain": {}, "derivatives": {}, "macro": {}, "iv_term": {},
               "large_trades": [], "max_pain": 0, "risk": {}, "errors": [],
               "strategy_summary": {}}
        rule_reports = {"reports": [], "synthesis": {}, "market_summary": {}}
        synthesis = {"success": True}

        result = engine._llm_audit(ctx, rule_reports, synthesis)

        assert result["success"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -m pytest tests/test_llm_analyst.py::TestLlmAudit -v`
Expected: FAIL

- [ ] **Step 3: Implement `_llm_audit()`**

Replace the placeholder `_llm_audit` in `services/llm_analyst.py`:

```python
def _llm_audit(self, context: Dict, rule_reports: Dict, synthesis: Dict) -> Dict[str, Any]:
    """LLM 数据质量审计 — 异常检测"""
    from services.ai_router import ai_chat_with_config

    system_prompt = """你是数据质量审计师。审查以下加密货币期权分析数据，找出异常。

检查维度：
1. 数据源间一致性：DVOL vs IV、链上信号 vs 衍生品信号、价格 vs 成交量
2. 计算逻辑合理性：APR 是否异常（>200%?）、胜率是否合理（>95%?）、spread 是否正常
3. 数据完整性：是否有缺失字段、数据是否过期（timestamp > 1小时?）
4. 前端展示一致性：策略引擎输出 vs 原始数据是否匹配

输出 JSON：
{
  "anomalies": [
    {"severity": "critical|warning|info", "source": "数据源名", "description": "描述", "suggestion": "建议"}
  ],
  "logic_issues": [
    {"severity": "...", "component": "模块名", "description": "...", "suggestion": "..."}
  ],
  "data_quality_score": 0-100
}"""

    # 组装全量原始数据
    raw_data = {
        "currency": context["currency"],
        "spot": context.get("spot", 0),
        "dvol": context.get("dvol", {}),
        "onchain": context.get("onchain", {}),
        "derivatives": context.get("derivatives", {}),
        "macro": context.get("macro", {}),
        "iv_term": context.get("iv_term", {}),
        "max_pain": context.get("max_pain", 0),
        "risk": context.get("risk", {}),
        "contracts_count": len(context.get("contracts", [])),
        "contracts_sample": context.get("contracts", [])[:5],  # 前5条样本
        "large_trades_count": len(context.get("large_trades", [])),
        "large_trades_sample": context.get("large_trades", [])[:5],
        "strategy_summary": context.get("strategy_summary", {}),
        "data_errors": context.get("errors", []),
    }

    user_prompt = f"""=== 全量原始数据 ===
{json.dumps(raw_data, ensure_ascii=False, indent=2, default=str)}

=== 规则引擎报告 ===
{json.dumps(rule_reports, ensure_ascii=False, indent=2, default=str)}

=== LLM 综合分析 ===
{json.dumps(synthesis, ensure_ascii=False, indent=2, default=str)}

请审查以上数据，返回异常检测 JSON。"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    custom_config = self._get_custom_config()

    try:
        response = ai_chat_with_config(
            messages, preset="analysis", temperature=0.2,
            max_tokens=2000, custom_config=custom_config
        )
        if not response:
            return {"success": False, "error": "LLM 无响应", "anomalies": [], "logic_issues": [], "data_quality_score": 0}

        parsed = self._parse_json_response(response)
        if parsed is None:
            return {"success": False, "error": "LLM 返回格式异常", "raw_response": response[:500],
                    "anomalies": [], "logic_issues": [], "data_quality_score": 0}

        parsed.setdefault("anomalies", [])
        parsed.setdefault("logic_issues", [])
        parsed.setdefault("data_quality_score", 0)
        parsed["success"] = True
        return parsed

    except (RuntimeError, ConnectionError, TimeoutError, ValueError) as e:
        logger.error("llm_audit failed: %s", e)
        return {"success": False, "error": str(e), "anomalies": [], "logic_issues": [], "data_quality_score": 0}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -m pytest tests/test_llm_analyst.py::TestLlmAudit -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run all LLM analyst tests**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -m pytest tests/test_llm_analyst.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Commit**

```bash
git add services/llm_analyst.py tests/test_llm_analyst.py
git commit -m "feat: implement _llm_audit anomaly detection with data quality scoring"
```

---

### Task 6: LLM Config CRUD — save/load/test config

**Files:**
- Modify: `services/llm_analyst.py`
- Modify: `tests/test_llm_analyst.py`

- [ ] **Step 1: Write failing tests for config CRUD**

Add to `tests/test_llm_analyst.py`:

```python
class TestLLMConfig:
    """Test LLM config save/load/test"""

    def test_save_and_load_config(self):
        from services.llm_analyst import LLMAnalystEngine
        from db.connection import execute_write, execute_read

        engine = LLMAnalystEngine()

        # Save
        engine.save_config("sk-test123", "https://api.example.com/v1", "gpt-4o")

        # Load
        config = engine.load_config()
        assert config["api_key"] == "sk-test123"
        assert config["base_url"] == "https://api.example.com/v1"
        assert config["model"] == "gpt-4o"

    def test_load_config_empty(self):
        from services.llm_analyst import LLMAnalystEngine
        from db.connection import execute_write

        # Clear config
        execute_write("DELETE FROM llm_config WHERE id=1")

        engine = LLMAnalystEngine()
        config = engine.load_config()

        assert config["api_key"] == ""
        assert config["base_url"] == ""
        assert config["model"] == ""

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_test_connection_success(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = "OK"

        engine = LLMAnalystEngine()
        result = engine.test_connection({"api_key": "sk-test", "model": "gpt-4o-mini"})

        assert result["success"] is True
        assert "latency_ms" in result

    @patch("services.llm_analyst.ai_chat_with_config")
    def test_test_connection_failure(self, mock_ai):
        from services.llm_analyst import LLMAnalystEngine

        mock_ai.return_value = None

        engine = LLMAnalystEngine()
        result = engine.test_connection({"api_key": "sk-bad"})

        assert result["success"] is False
        assert "error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -m pytest tests/test_llm_analyst.py::TestLLMConfig -v`
Expected: FAIL

- [ ] **Step 3: Implement config methods**

Add to `LLMAnalystEngine` class in `services/llm_analyst.py`:

```python
def load_config(self) -> Dict[str, str]:
    """从数据库加载 LLM 配置"""
    try:
        from db.connection import execute_read
        rows = execute_read(
            "SELECT api_key, base_url, model FROM llm_config WHERE id=1"
        )
        if rows and rows[0]:
            return {
                "api_key": rows[0][0] or "",
                "base_url": rows[0][1] or "",
                "model": rows[0][2] or "",
            }
    except Exception as e:
        logger.warning("load_config failed: %s", e)
    return {"api_key": "", "base_url": "", "model": ""}

def save_config(self, api_key: str, base_url: str = "", model: str = "") -> bool:
    """保存 LLM 配置到数据库"""
    try:
        from db.connection import execute_write
        execute_write(
            """INSERT OR REPLACE INTO llm_config (id, api_key, base_url, model, updated_at)
               VALUES (1, ?, ?, ?, datetime('now'))""",
            (api_key, base_url, model)
        )
        return True
    except Exception as e:
        logger.error("save_config failed: %s", e)
        return False

def test_connection(self, config: Dict[str, str]) -> Dict[str, Any]:
    """测试 LLM 连接"""
    import time
    from services.ai_router import ai_chat_with_config

    custom_config = {}
    if config.get("api_key"):
        custom_config["api_key"] = config["api_key"]
    if config.get("base_url"):
        custom_config["base_url"] = config["base_url"]
    if config.get("model"):
        custom_config["model"] = config["model"]

    start = time.time()
    try:
        response = ai_chat_with_config(
            [{"role": "user", "content": "Reply with exactly: OK"}],
            preset="fast", temperature=0, max_tokens=10,
            custom_config=custom_config or None
        )
        latency = int((time.time() - start) * 1000)

        if response and "OK" in response.upper():
            return {"success": True, "latency_ms": latency, "model": config.get("model", "default")}
        else:
            return {"success": False, "error": f"模型返回异常: {response[:100] if response else '无响应'}", "latency_ms": latency}

    except (RuntimeError, ConnectionError, TimeoutError, ValueError) as e:
        latency = int((time.time() - start) * 1000)
        return {"success": False, "error": str(e), "latency_ms": latency}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -m pytest tests/test_llm_analyst.py::TestLLMConfig -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add services/llm_analyst.py tests/test_llm_analyst.py
git commit -m "feat: add LLM config CRUD and connection test"
```

---

### Task 7: API endpoints — `/api/llm-analyst/*`

**Files:**
- Create: `api/llm_analyst.py`
- Modify: `api/__init__.py`
- Modify: `main.py`

- [ ] **Step 1: Create `api/llm_analyst.py`**

```python
"""AI 研判中心 API — LLM 综合分析端点"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/llm-analyst", tags=["llm-analyst"])


class AnalyzeRequest(BaseModel):
    currency: str = Field(default="BTC", description="币种")
    mode: str = Field(default="full", description="分析模式: full 或 quick")


class ConfigRequest(BaseModel):
    api_key: str = Field(description="LLM API Key")
    base_url: str = Field(default="", description="Base URL (可选)")
    model: str = Field(default="", description="模型名 (可选)")


class TestConnectionRequest(BaseModel):
    api_key: str = Field(description="API Key")
    base_url: str = Field(default="", description="Base URL")
    model: str = Field(default="", description="模型名")


@router.post("/analyze")
async def llm_analyze(request: AnalyzeRequest):
    """全流程 LLM 分析（规则→综合→辩论→审计）"""
    from fastapi.concurrency import run_in_threadpool
    from services.llm_analyst import LLMAnalystEngine

    engine = LLMAnalystEngine()

    try:
        result = await run_in_threadpool(
            engine.run_full_analysis, request.currency.upper(), request.mode
        )
    except (RuntimeError, ValueError, TypeError) as e:
        logger.error("llm analyze failed for %s: %s", request.currency, e)
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")

    # 保存结果
    try:
        from db.connection import execute_write
        execute_write(
            """INSERT INTO llm_analysis_results (currency, mode, result_json, llm_config_json, success, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                request.currency.upper(),
                request.mode,
                json.dumps(result, default=str, ensure_ascii=False),
                json.dumps(result.llm_config, ensure_ascii=False),
                1 if result.success else 0,
                datetime.now(timezone.utc).isoformat(),
            )
        )
    except (RuntimeError, ValueError, TypeError) as e:
        logger.debug("llm analysis save failed (non-critical): %s", e)

    # 转为 dict 返回
    return {
        "success": result.success,
        "currency": result.currency,
        "timestamp": result.timestamp,
        "rule_reports": result.rule_reports,
        "synthesis": result.synthesis,
        "debate": result.debate,
        "audit": result.audit,
        "llm_config": result.llm_config,
    }


@router.get("/history")
async def llm_history(
    currency: str = "BTC",
    limit: int = 10,
):
    """获取历史 LLM 分析结果"""
    try:
        from db.connection import execute_read
        rows = execute_read(
            """SELECT currency, mode, result_json, success, timestamp
               FROM llm_analysis_results
               WHERE currency = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (currency.upper(), limit)
        )
        if not rows:
            return {"currency": currency.upper(), "history": [], "message": "暂无历史记录"}

        history = []
        for row in rows:
            result = json.loads(row[2]) if row[2] else {}
            history.append({
                "currency": row[0],
                "mode": row[1],
                "success": bool(row[3]),
                "timestamp": row[4],
                "synthesis": result.get("synthesis", {}),
                "audit": result.get("audit", {}),
            })
        return {"currency": currency.upper(), "history": history, "count": len(history)}

    except (RuntimeError, ValueError, TypeError) as e:
        logger.warning("llm history query failed: %s", e)
        return {"currency": currency.upper(), "history": [], "error": str(e)}


@router.get("/config")
async def get_llm_config():
    """获取当前 LLM 配置"""
    from services.llm_analyst import LLMAnalystEngine
    engine = LLMAnalystEngine()
    config = engine.load_config()
    # 隐藏 API Key 中间部分
    masked = dict(config)
    if masked.get("api_key") and len(masked["api_key"]) > 8:
        masked["api_key"] = masked["api_key"][:4] + "****" + masked["api_key"][-4:]
    elif masked.get("api_key"):
        masked["api_key"] = "****"
    return masked


@router.post("/config")
async def save_llm_config(request: ConfigRequest):
    """保存 LLM 配置"""
    from services.llm_analyst import LLMAnalystEngine
    engine = LLMAnalystEngine()
    success = engine.save_config(request.api_key, request.base_url, request.model)
    if not success:
        raise HTTPException(status_code=500, detail="保存配置失败")
    return {"success": True, "message": "配置已保存"}


@router.post("/test")
async def test_llm_connection(request: TestConnectionRequest):
    """测试 LLM 连接"""
    from fastapi.concurrency import run_in_threadpool
    from services.llm_analyst import LLMAnalystEngine

    engine = LLMAnalystEngine()
    config = {
        "api_key": request.api_key,
        "base_url": request.base_url,
        "model": request.model,
    }

    result = await run_in_threadpool(engine.test_connection, config)
    return result
```

- [ ] **Step 2: Register router in `api/__init__.py`**

Add import after existing imports:

```python
from .llm_analyst import router as llm_analyst_router
```

Add `"llm_analyst_router"` to `__all__`.

- [ ] **Step 3: Register router in `main.py`**

Add to the import block:

```python
llm_analyst_router
```

Add after existing `include_router` calls:

```python
app.include_router(llm_analyst_router, dependencies=protected_dependencies)
```

- [ ] **Step 4: Verify server starts**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -c "from api import llm_analyst_router; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add api/llm_analyst.py api/__init__.py main.py
git commit -m "feat: add /api/llm-analyst endpoints (analyze, history, config, test)"
```

---

### Task 8: Frontend HTML — AI 研判中心 section (replace debate + copilot)

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: Read current debate section boundaries**

Read `static/index.html` lines 919-1100 to identify the full debate section end.
Read `static/index.html` lines 1594-1650 to identify copilot widget + AI settings modal boundaries.

- [ ] **Step 2: Replace debate section with AI 研判中心**

Replace the entire `<section id="debateSection">` block (lines 919 to wherever it ends) with the new AI 研判中心 section:

```html
<!-- AI 研判中心 -->
<section id="llmAnalystSection" class="card-glass rounded-xl p-5 mb-6 border-l-4 border-purple-500">
    <div class="flex items-center justify-between mb-4">
        <div class="flex items-center gap-2">
            <i class="fas fa-brain text-purple-500"></i>
            <h3 class="font-semibold text-lg">AI 研判中心</h3>
            <span class="text-xs text-gray-400 ml-2">LLM 综合分析 · 多空辩论 · 数据审计</span>
        </div>
        <div class="flex items-center gap-2">
            <select id="llmCurrency" class="input-dark rounded-lg px-3 py-1.5 text-sm">
                <option value="BTC">BTC</option>
                <option value="ETH">ETH</option>
            </select>
            <button id="llmAnalyzeBtn" class="btn-primary px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-2">
                <i class="fas fa-play"></i>
                <span>开始分析</span>
            </button>
            <button id="llmQuickBtn" class="btn-secondary px-3 py-2 rounded-lg text-sm font-medium flex items-center gap-2" title="快速模式（跳过多空辩论）">
                <i class="fas fa-bolt"></i>
                <span>快速</span>
            </button>
        </div>
    </div>

    <!-- 进度指示器 -->
    <div id="llmProgress" class="hidden mb-4">
        <div class="flex items-center gap-3 mb-2">
            <div id="llmStep1" class="flex items-center gap-1.5 text-xs text-gray-400">
                <i class="fas fa-circle-notch fa-spin"></i> 规则分析
            </div>
            <div class="text-gray-600">→</div>
            <div id="llmStep2" class="flex items-center gap-1.5 text-xs text-gray-500">
                <i class="far fa-circle"></i> 综合研判
            </div>
            <div class="text-gray-600">→</div>
            <div id="llmStep3" class="flex items-center gap-1.5 text-xs text-gray-500">
                <i class="far fa-circle"></i> 多空辩论
            </div>
            <div class="text-gray-600">→</div>
            <div id="llmStep4" class="flex items-center gap-1.5 text-xs text-gray-500">
                <i class="far fa-circle"></i> 数据审计
            </div>
        </div>
        <div class="w-full h-1 bg-gray-700 rounded-full overflow-hidden">
            <div id="llmProgressBar" class="h-full bg-purple-500 rounded-full transition-all" style="width: 0%"></div>
        </div>
    </div>

    <!-- 空状态 -->
    <div id="llmEmpty" class="text-center py-8 text-gray-500">
        <i class="fas fa-brain text-4xl mb-3 text-gray-600"></i>
        <p>选择币种，点击"开始分析"启动 LLM 综合研判</p>
        <p class="text-xs mt-1 text-gray-600">需要配置 LLM API Key（面板底部）</p>
    </div>

    <!-- 结果区 -->
    <div id="llmResults" class="hidden">
        <!-- 综合研判卡片 -->
        <div id="llmSynthesisCard" class="mb-4 p-5 rounded-xl border-2 border-purple-500/40 bg-gradient-to-r from-purple-900/30 to-gray-900/30 hidden">
            <div class="flex items-center gap-2 mb-3">
                <i class="fas fa-chart-line text-purple-400"></i>
                <span class="font-semibold text-sm text-purple-300">综合研判</span>
                <span id="llmSynthesisConfidence" class="text-xs px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300 ml-auto"></span>
            </div>
            <div id="llmSynthesisContent" class="text-sm text-gray-300 space-y-3"></div>
        </div>

        <!-- 多空辩论卡片 -->
        <div id="llmDebateCard" class="mb-4 hidden">
            <div class="flex items-center gap-2 mb-3">
                <i class="fas fa-balance-scale text-yellow-400"></i>
                <span class="font-semibold text-sm text-yellow-300">多空辩论</span>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div class="p-4 rounded-lg border border-green-500/30 bg-green-900/10">
                    <div class="flex items-center gap-2 mb-2">
                        <span class="text-lg">🐂</span>
                        <span class="text-sm font-semibold text-green-400">Bull Agent</span>
                        <span id="llmBullConf" class="text-xs text-green-300 ml-auto"></span>
                    </div>
                    <div id="llmBullContent" class="text-xs text-gray-300 space-y-2"></div>
                </div>
                <div class="p-4 rounded-lg border border-red-500/30 bg-red-900/10">
                    <div class="flex items-center gap-2 mb-2">
                        <span class="text-lg">🐻</span>
                        <span class="text-sm font-semibold text-red-400">Bear Agent</span>
                        <span id="llmBearConf" class="text-xs text-red-300 ml-auto"></span>
                    </div>
                    <div id="llmBearContent" class="text-xs text-gray-300 space-y-2"></div>
                </div>
                <div class="p-4 rounded-lg border border-yellow-500/30 bg-yellow-900/10">
                    <div class="flex items-center gap-2 mb-2">
                        <span class="text-lg">⚖️</span>
                        <span class="text-sm font-semibold text-yellow-400">裁判裁决</span>
                    </div>
                    <div id="llmJudgeContent" class="text-xs text-gray-300 space-y-2"></div>
                </div>
            </div>
        </div>

        <!-- 数据审计卡片 -->
        <div id="llmAuditCard" class="mb-4 p-4 rounded-xl border border-gray-600/30 bg-gray-900/30 hidden">
            <div class="flex items-center justify-between mb-3">
                <div class="flex items-center gap-2">
                    <i class="fas fa-shield-alt text-blue-400"></i>
                    <span class="font-semibold text-sm text-blue-300">数据审计</span>
                </div>
                <div class="flex items-center gap-2">
                    <span class="text-xs text-gray-400">质量评分</span>
                    <span id="llmAuditScore" class="text-lg font-bold"></span>
                </div>
            </div>
            <div id="llmAuditScoreBar" class="w-full h-2 bg-gray-700 rounded-full mb-3 overflow-hidden">
                <div id="llmAuditScoreFill" class="h-full rounded-full transition-all"></div>
            </div>
            <div id="llmAuditContent" class="space-y-2"></div>
        </div>

        <!-- 规则 Agent 详情（可折叠） -->
        <div id="llmRuleAgentsCard" class="mb-4">
            <button id="llmToggleRuleAgents" class="flex items-center gap-2 text-sm text-gray-400 hover:text-gray-200 transition mb-2">
                <i class="fas fa-chevron-right text-xs transition-transform" id="llmRuleAgentsIcon"></i>
                <span>规则 Agent 详情</span>
                <span id="llmRuleAgentsCount" class="text-xs text-gray-500"></span>
            </button>
            <div id="llmRuleAgentsContent" class="hidden grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3"></div>
        </div>
    </div>

    <!-- LLM 配置面板（内嵌底部） -->
    <div class="mt-6 pt-4 border-t border-gray-700/50">
        <button id="llmConfigToggle" class="flex items-center gap-2 text-sm text-gray-400 hover:text-gray-200 transition mb-3">
            <i class="fas fa-cog"></i>
            <span>LLM 配置</span>
            <span id="llmConfigStatus" class="text-xs ml-2"></span>
        </button>
        <div id="llmConfigPanel" class="hidden space-y-3">
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div>
                    <label class="block text-xs text-gray-400 mb-1">API Key</label>
                    <input type="password" id="llmApiKey" placeholder="sk-..." class="w-full input-dark rounded-lg px-3 py-2 text-sm">
                </div>
                <div>
                    <label class="block text-xs text-gray-400 mb-1">Base URL <span class="text-gray-500">(可选)</span></label>
                    <input type="text" id="llmBaseUrl" placeholder="https://api.openai.com/v1" class="w-full input-dark rounded-lg px-3 py-2 text-sm">
                </div>
                <div>
                    <label class="block text-xs text-gray-400 mb-1">Model <span class="text-gray-500">(可选)</span></label>
                    <input type="text" id="llmModel" placeholder="gpt-4o-mini" class="w-full input-dark rounded-lg px-3 py-2 text-sm">
                </div>
            </div>
            <div class="flex items-center gap-2">
                <button id="llmSaveConfig" class="btn-primary px-4 py-2 rounded-lg text-sm">保存配置</button>
                <button id="llmTestConn" class="btn-secondary px-4 py-2 rounded-lg text-sm">测试连接</button>
                <span id="llmTestResult" class="text-xs ml-2"></span>
            </div>
        </div>
    </div>
</section>
```

- [ ] **Step 3: Remove copilot widget HTML**

Delete the entire copilot widget block (lines 1594-1630):
```html
<!-- AI Copilot Chat Widget -->
<div id="copilotWidget">...</div>
<div id="copilotChat">...</div>
```

- [ ] **Step 4: Remove AI settings modal HTML**

Delete the AI settings modal block (lines 1632-1670 approximately):
```html
<!-- AI 配置模态框 -->
<div id="aiSettingsModal">...</div>
```

- [ ] **Step 5: Verify HTML structure**

Open `static/index.html` in browser or run: `python -c "from pathlib import Path; html = Path('static/index.html').read_text(); print('llmAnalystSection' in html, 'copilotWidget' not in html, 'aiSettingsModal' not in html)"`
Expected: `True True True`

- [ ] **Step 6: Commit**

```bash
git add static/index.html
git commit -m "feat: replace debate section + copilot with AI 研判中心 HTML"
```

---

### Task 9: Frontend JS — LLM analyst logic, progress tracking, rendering

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Add LLM analyst functions**

Add new section to `app.js` (after the existing debate section, before the closing of the file):

```javascript
// =========================================================================
// AI 研判中心 (LLM Analyst)
// =========================================================================

function initLLMAnalystSection() {
    const analyzeBtn = document.getElementById('llmAnalyzeBtn');
    const quickBtn = document.getElementById('llmQuickBtn');
    const configToggle = document.getElementById('llmConfigToggle');
    const saveConfigBtn = document.getElementById('llmSaveConfig');
    const testConnBtn = document.getElementById('llmTestConn');
    const toggleRuleBtn = document.getElementById('llmToggleRuleAgents');

    if (analyzeBtn) analyzeBtn.addEventListener('click', () => runLLMAnalysis('full'));
    if (quickBtn) quickBtn.addEventListener('click', () => runLLMAnalysis('quick'));
    if (configToggle) configToggle.addEventListener('click', toggleLLMConfig);
    if (saveConfigBtn) saveConfigBtn.addEventListener('click', saveLLMConfig);
    if (testConnBtn) testConnBtn.addEventListener('click', testLLMConnection);
    if (toggleRuleBtn) toggleRuleBtn.addEventListener('click', toggleRuleAgents);

    // 加载配置状态
    loadLLMConfigStatus();
}

function toggleLLMConfig() {
    const panel = document.getElementById('llmConfigPanel');
    panel.classList.toggle('hidden');
}

function toggleRuleAgents() {
    const content = document.getElementById('llmRuleAgentsContent');
    const icon = document.getElementById('llmRuleAgentsIcon');
    content.classList.toggle('hidden');
    icon.style.transform = content.classList.contains('hidden') ? '' : 'rotate(90deg)';
}

async function loadLLMConfigStatus() {
    try {
        const resp = await safeFetch(`${API_BASE}/api/llm-analyst/config`);
        if (resp.ok) {
            const config = await resp.json();
            const status = document.getElementById('llmConfigStatus');
            if (config.api_key && config.api_key !== '****') {
                status.textContent = config.model ? `已配置 (${config.model})` : '已配置';
                status.className = 'text-xs ml-2 text-green-400';
            } else if (config.api_key === '****') {
                status.textContent = config.model ? `已配置 (${config.model})` : '已配置';
                status.className = 'text-xs ml-2 text-green-400';
                // 填充已有的 base_url 和 model
                if (config.base_url) document.getElementById('llmBaseUrl').value = config.base_url;
                if (config.model) document.getElementById('llmModel').value = config.model;
            } else {
                status.textContent = '未配置';
                status.className = 'text-xs ml-2 text-yellow-400';
            }
        }
    } catch (e) {
        // silent
    }
}

async function saveLLMConfig() {
    const apiKey = document.getElementById('llmApiKey').value.trim();
    const baseUrl = document.getElementById('llmBaseUrl').value.trim();
    const model = document.getElementById('llmModel').value.trim();

    if (!apiKey) {
        showAlert('请输入 API Key', 'warning');
        return;
    }

    try {
        const resp = await safeFetch(`${API_BASE}/api/llm-analyst/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: apiKey, base_url: baseUrl, model: model }),
        });

        if (resp.ok) {
            showAlert('配置已保存', 'success');
            document.getElementById('llmApiKey').value = '';
            loadLLMConfigStatus();
        } else {
            const err = await resp.json();
            showAlert('保存失败: ' + (err.detail || '未知错误'), 'error');
        }
    } catch (e) {
        showAlert('保存失败: ' + e.message, 'error');
    }
}

async function testLLMConnection() {
    const apiKey = document.getElementById('llmApiKey').value.trim();
    const baseUrl = document.getElementById('llmBaseUrl').value.trim();
    const model = document.getElementById('llmModel').value.trim();

    if (!apiKey) {
        showAlert('请先输入 API Key', 'warning');
        return;
    }

    const resultSpan = document.getElementById('llmTestResult');
    resultSpan.textContent = '测试中...';
    resultSpan.className = 'text-xs ml-2 text-gray-400';

    try {
        const resp = await safeFetch(`${API_BASE}/api/llm-analyst/test`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: apiKey, base_url: baseUrl, model: model }),
        });

        const data = await resp.json();
        if (data.success) {
            resultSpan.textContent = `连接成功 (${data.latency_ms}ms)`;
            resultSpan.className = 'text-xs ml-2 text-green-400';
        } else {
            resultSpan.textContent = `失败: ${data.error || '未知错误'}`;
            resultSpan.className = 'text-xs ml-2 text-red-400';
        }
    } catch (e) {
        resultSpan.textContent = '连接失败: ' + e.message;
        resultSpan.className = 'text-xs ml-2 text-red-400';
    }
}

async function runLLMAnalysis(mode) {
    const currency = document.getElementById('llmCurrency').value;
    const analyzeBtn = document.getElementById('llmAnalyzeBtn');
    const quickBtn = document.getElementById('llmQuickBtn');
    const progress = document.getElementById('llmProgress');
    const empty = document.getElementById('llmEmpty');
    const results = document.getElementById('llmResults');

    // UI 状态
    analyzeBtn.disabled = true;
    quickBtn.disabled = true;
    analyzeBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> <span>分析中...</span>';
    progress.classList.remove('hidden');
    empty.classList.add('hidden');
    results.classList.add('hidden');

    // 重置进度
    resetLLMProgress();

    try {
        const resp = await safeFetch(`${API_BASE}/api/llm-analyst/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ currency: currency, mode: mode }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || `HTTP ${resp.status}`);
        }

        const data = await resp.json();

        // 更新进度到完成
        setLLMStepComplete(1);
        setLLMStepComplete(2);
        if (mode === 'full') setLLMStepComplete(3);
        setLLMStepComplete(4);
        document.getElementById('llmProgressBar').style.width = '100%';

        // 渲染结果
        results.classList.remove('hidden');
        renderLLMSynthesis(data.synthesis);
        if (data.debate) renderLLMDebate(data.debate);
        renderLLMAudit(data.audit);
        renderLLMRuleAgents(data.rule_reports);

    } catch (e) {
        console.error('LLM analysis failed:', e);
        showAlert('分析失败: ' + e.message, 'error');
        empty.classList.remove('hidden');
    } finally {
        analyzeBtn.disabled = false;
        quickBtn.disabled = false;
        analyzeBtn.innerHTML = '<i class="fas fa-play"></i> <span>开始分析</span>';
        setTimeout(() => progress.classList.add('hidden'), 2000);
    }
}

function resetLLMProgress() {
    for (let i = 1; i <= 4; i++) {
        const step = document.getElementById(`llmStep${i}`);
        step.className = 'flex items-center gap-1.5 text-xs text-gray-500';
        step.innerHTML = '<i class="far fa-circle"></i> ' + step.textContent.trim();
    }
    document.getElementById('llmProgressBar').style.width = '0%';
}

function setLLMStepComplete(stepNum) {
    const step = document.getElementById(`llmStep${stepNum}`);
    step.className = 'flex items-center gap-1.5 text-xs text-green-400';
    step.innerHTML = '<i class="fas fa-check-circle"></i> ' + step.textContent.trim();
    const progress = (stepNum / 4) * 100;
    document.getElementById('llmProgressBar').style.width = progress + '%';
}

function renderLLMSynthesis(synthesis) {
    const card = document.getElementById('llmSynthesisCard');
    const content = document.getElementById('llmSynthesisContent');
    const confSpan = document.getElementById('llmSynthesisConfidence');

    if (!synthesis || !synthesis.success) {
        card.classList.add('hidden');
        return;
    }

    card.classList.remove('hidden');

    const conf = synthesis.confidence || 0;
    confSpan.textContent = `信心度 ${conf}%`;
    confSpan.className = `text-xs px-2 py-0.5 rounded-full ${conf >= 70 ? 'bg-green-500/20 text-green-300' : conf >= 40 ? 'bg-yellow-500/20 text-yellow-300' : 'bg-red-500/20 text-red-300'} ml-auto`;

    let html = '';
    if (synthesis.market_assessment) {
        html += `<div><span class="text-purple-400 font-medium">市场评估：</span><span>${safeHTML(synthesis.market_assessment)}</span></div>`;
    }
    if (synthesis.strategy_recommendation) {
        html += `<div><span class="text-blue-400 font-medium">策略建议：</span><span>${safeHTML(synthesis.strategy_recommendation)}</span></div>`;
    }
    if (synthesis.risk_warning) {
        html += `<div><span class="text-red-400 font-medium">风险提示：</span><span>${safeHTML(synthesis.risk_warning)}</span></div>`;
    }
    content.innerHTML = html;
}

function renderLLMDebate(debate) {
    const card = document.getElementById('llmDebateCard');
    if (!debate || !debate.success) {
        card.classList.add('hidden');
        return;
    }
    card.classList.remove('hidden');

    // Bull
    const bull = debate.bull || {};
    document.getElementById('llmBullConf').textContent = bull.success ? `${bull.confidence || 0}%` : '失败';
    let bullHtml = '';
    if (bull.bullish_case) bullHtml += `<p>${safeHTML(bull.bullish_case)}</p>`;
    if (bull.key_drivers && bull.key_drivers.length) {
        bullHtml += '<ul class="list-disc list-inside mt-1">';
        for (const d of bull.key_drivers) bullHtml += `<li>${safeHTML(d)}</li>`;
        bullHtml += '</ul>';
    }
    document.getElementById('llmBullContent').innerHTML = bullHtml || '<p class="text-gray-500">分析失败</p>';

    // Bear
    const bear = debate.bear || {};
    document.getElementById('llmBearConf').textContent = bear.success ? `${bear.confidence || 0}%` : '失败';
    let bearHtml = '';
    if (bear.bearish_case) bearHtml += `<p>${safeHTML(bear.bearish_case)}</p>`;
    if (bear.key_risks && bear.key_risks.length) {
        bearHtml += '<ul class="list-disc list-inside mt-1">';
        for (const r of bear.key_risks) bearHtml += `<li>${safeHTML(r)}</li>`;
        bearHtml += '</ul>';
    }
    document.getElementById('llmBearContent').innerHTML = bearHtml || '<p class="text-gray-500">分析失败</p>';

    // Judge
    const judge = debate.judge || {};
    let judgeHtml = '';
    if (judge.judge_verdict) judgeHtml += `<p class="font-medium">${safeHTML(judge.judge_verdict)}</p>`;
    if (judge.winner) {
        const winnerColors = { bull: 'text-green-400', bear: 'text-red-400', draw: 'text-yellow-400' };
        const winnerLabels = { bull: '多头胜', bear: '空头胜', draw: '平局' };
        judgeHtml += `<p class="${winnerColors[judge.winner] || 'text-gray-300'} font-bold mt-2">${winnerLabels[judge.winner] || judge.winner}</p>`;
    }
    if (judge.reasoning) judgeHtml += `<p class="mt-1 text-gray-400">${safeHTML(judge.reasoning)}</p>`;
    document.getElementById('llmJudgeContent').innerHTML = judgeHtml || '<p class="text-gray-500">裁决生成中</p>';
}

function renderLLMAudit(audit) {
    const card = document.getElementById('llmAuditCard');
    if (!audit) {
        card.classList.add('hidden');
        return;
    }
    card.classList.remove('hidden');

    const score = audit.data_quality_score || 0;
    const scoreEl = document.getElementById('llmAuditScore');
    const fillEl = document.getElementById('llmAuditScoreFill');

    scoreEl.textContent = score;
    scoreEl.className = `text-lg font-bold ${score >= 80 ? 'text-green-400' : score >= 50 ? 'text-yellow-400' : 'text-red-400'}`;
    fillEl.style.width = score + '%';
    fillEl.className = `h-full rounded-full transition-all ${score >= 80 ? 'bg-green-500' : score >= 50 ? 'bg-yellow-500' : 'bg-red-500'}`;

    const content = document.getElementById('llmAuditContent');
    let html = '';

    const anomalies = audit.anomalies || [];
    const issues = audit.logic_issues || [];

    if (anomalies.length === 0 && issues.length === 0) {
        html = '<div class="text-green-400 text-sm"><i class="fas fa-check-circle mr-1"></i>未发现数据异常</div>';
    } else {
        for (const a of anomalies) {
            const sevColors = { critical: 'red', warning: 'yellow', info: 'blue' };
            const sevIcons = { critical: 'exclamation-triangle', warning: 'exclamation-circle', info: 'info-circle' };
            const color = sevColors[a.severity] || 'gray';
            const icon = sevIcons[a.severity] || 'info-circle';
            html += `<div class="flex items-start gap-2 p-2 rounded bg-${color}-900/20 border border-${color}-500/20 text-sm">`;
            html += `<i class="fas fa-${icon} text-${color}-400 mt-0.5"></i>`;
            html += `<div><span class="text-${color}-300 font-medium">[${safeHTML(a.source || '')}]</span> ${safeHTML(a.description || '')}`;
            if (a.suggestion) html += `<div class="text-xs text-gray-400 mt-1">建议: ${safeHTML(a.suggestion)}</div>`;
            html += `</div></div>`;
        }
        for (const i of issues) {
            const sevColors = { critical: 'red', warning: 'yellow', info: 'blue' };
            const color = sevColors[i.severity] || 'gray';
            html += `<div class="flex items-start gap-2 p-2 rounded bg-${color}-900/20 border border-${color}-500/20 text-sm">`;
            html += `<i class="fas fa-cog text-${color}-400 mt-0.5"></i>`;
            html += `<div><span class="text-${color}-300 font-medium">[${safeHTML(i.component || '')}]</span> ${safeHTML(i.description || '')}`;
            if (i.suggestion) html += `<div class="text-xs text-gray-400 mt-1">建议: ${safeHTML(i.suggestion)}</div>`;
            html += `</div></div>`;
        }
    }

    content.innerHTML = html;
}

function renderLLMRuleAgents(ruleReports) {
    const countSpan = document.getElementById('llmRuleAgentsCount');
    const content = document.getElementById('llmRuleAgentsContent');

    const reports = ruleReports?.reports || [];
    countSpan.textContent = `(${reports.length} 个 Agent)`;

    let html = '';
    const agentColors = {
        '🐂 多头分析师': { bg: 'green', icon: '🐂' },
        '🐻 空头分析师': { bg: 'red', icon: '🐻' },
        '📊 波动率分析师': { bg: 'blue', icon: '📊' },
        '🐋 资金流向分析师': { bg: 'purple', icon: '🐋' },
        '🛡️ 风险官': { bg: 'yellow', icon: '🛡️' },
    };

    for (const r of reports) {
        const colors = agentColors[r.name] || { bg: 'gray', icon: '🤖' };
        const score = r.score || 0;
        const scoreColor = score > 20 ? 'text-green-400' : score > 0 ? 'text-emerald-300' : score > -20 ? 'text-yellow-400' : 'text-red-400';

        html += `<div class="card-glass rounded-lg p-3 border-l-4 border-${colors.bg}-500/60">`;
        html += `<div class="flex items-center justify-between mb-2">`;
        html += `<div class="flex items-center gap-1.5"><span>${colors.icon}</span><span class="text-xs font-semibold">${safeHTML(r.name)}</span></div>`;
        html += `<span class="text-sm font-bold ${scoreColor}">${score > 0 ? '+' : ''}${score}</span>`;
        html += `</div>`;
        html += `<div class="text-[10px] text-gray-400 mb-1">${safeHTML(r.verdict || '')} · 置信度 ${r.confidence || 0}%</div>`;
        html += `<ul class="text-[10px] text-gray-300 space-y-0.5">`;
        for (const pt of (r.key_points || []).slice(0, 3)) {
            html += `<li>• ${safeHTML(pt)}</li>`;
        }
        html += `</ul></div>`;
    }

    content.innerHTML = html;
}
```

- [ ] **Step 2: Wire up event listeners in `setupEventListeners()`**

Find the `setupEventListeners()` function in `app.js`. Add the LLM analyst init call alongside existing inits:

```javascript
initLLMAnalystSection();
```

- [ ] **Step 3: Verify no syntax errors**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && node -c static/app.js`
Expected: no output (syntax OK)

- [ ] **Step 4: Commit**

```bash
git add static/app.js
git commit -m "feat: add AI 研判中心 frontend JS (analysis, progress, rendering)"
```

---

### Task 10: Remove old debate + copilot JS code

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Remove old debate functions**

Remove these functions from `app.js`:
- `initDebateSection()` (around line 4198)
- `runDebate()` (around line 4204)
- `renderDebateResults()` (around line 4241)
- `renderDebateVerdict()` (around line 4262)
- `renderDebateAgents()` (around line 4318)
- `renderDebateMarketSummary()` (around line 4373)
- `renderDebateErrors()` (search for it)
- `_summaryCard()` helper (search for it)

- [ ] **Step 2: Remove old copilot functions**

Remove these functions from `app.js`:
- `toggleCopilotChat()` (around line 2874)
- `sendCopilotMessage()` (around line 2918)
- Any other copilot-related functions

- [ ] **Step 3: Remove old copilot event listeners**

In `setupEventListeners()`, remove:
- `copilotToggle` click handler
- `copilotForm` submit handler
- `closeCopilotBtn` click handler
- `aiSettingsBtn` click handler
- `closeAiSettings` click handler
- `saveAiSettings` click handler
- `testAiConnection` click handler
- `debateRunBtn` click handler (if in setupEventListeners)

- [ ] **Step 4: Remove old debate init call**

Remove `initDebateSection()` call from wherever it's called (likely in `setupEventListeners()` or DOMContentLoaded).

- [ ] **Step 5: Remove `initCopilotChat()` or similar init calls**

- [ ] **Step 6: Verify no syntax errors**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && node -c static/app.js`
Expected: no output (syntax OK)

- [ ] **Step 7: Remove copilot API module**

Delete `api/copilot.py` file. Remove its import and registration from:
- `api/__init__.py`: remove `from .copilot import router as copilot_router` and `"copilot_router"` from `__all__`
- `main.py`: remove `copilot_router` from import and `app.include_router(copilot_router, ...)`

- [ ] **Step 8: Commit**

```bash
git add static/app.js api/__init__.py main.py
git rm api/copilot.py
git commit -m "refactor: remove old debate section and copilot widget code"
```

---

### Task 11: Integration test + final cleanup

**Files:**
- Modify: `services/llm_analyst.py` (if needed)
- Modify: `static/app.js` (if needed)

- [ ] **Step 1: Run all LLM analyst backend tests**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && python -m pytest tests/test_llm_analyst.py -v`
Expected: PASS (all tests)

- [ ] **Step 2: Verify server starts cleanly**

Run: `cd C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard && timeout 10 python -c "from main import app; print('App OK')"`
Expected: `App OK`

- [ ] **Step 3: Test API endpoints manually**

Run the server and test:
```bash
# Config endpoint
curl http://localhost:8000/api/llm-analyst/config

# History endpoint
curl http://localhost:8000/api/llm-analyst/history?currency=BTC

# Analyze endpoint (requires LLM config)
curl -X POST http://localhost:8000/api/llm-analyst/analyze -H "Content-Type: application/json" -d '{"currency":"BTC","mode":"quick"}'
```

- [ ] **Step 4: Verify frontend renders without errors**

Open browser, check:
1. AI 研判中心 section visible
2. Copilot widget gone
3. Old debate section gone
4. LLM config panel toggles open/close
5. No JS console errors

- [ ] **Step 5: Final commit with any fixes**

```bash
git add -A
git commit -m "fix: integration test fixes for AI 研判中心"
```
