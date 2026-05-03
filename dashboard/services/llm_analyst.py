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

# Module-level imports so @patch("services.llm_analyst.X") works in tests
from services.options_debate_engine import _gather_market_data
from services.onchain_metrics import OnChainMetrics
from services.derivative_metrics import DerivativeMetrics
from services.macro_data import get_all_macro_data
from services.iv_term_structure import IVTermStructureAnalyzer


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
        try:
            md = _gather_market_data(currency)
        except Exception as e:
            logger.warning("llm analyst gather_market_data failed: %s", e)
            md = {"spot": 0, "dvol": {}, "large_trades": [], "contracts": [], "max_pain": 0, "risk_status": "UNKNOWN", "risk_label": "", "risk_desc": "", "errors": [f"gather_market_data: {e}"]}
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
            ctx["onchain"] = OnChainMetrics.get_all_metrics()
        except (RuntimeError, ConnectionError, TimeoutError, Exception) as e:
            logger.warning("llm analyst onchain failed: %s", e)
            ctx["onchain"] = {}
            ctx["errors"].append(f"onchain: {e}")

        # 衍生品指标
        try:
            ctx["derivatives"] = DerivativeMetrics.get_all_metrics()
        except (RuntimeError, ConnectionError, TimeoutError, Exception) as e:
            logger.warning("llm analyst derivatives failed: %s", e)
            ctx["derivatives"] = {}
            ctx["errors"].append(f"derivatives: {e}")

        # 宏观数据
        try:
            ctx["macro"] = get_all_macro_data()
        except (RuntimeError, ConnectionError, TimeoutError, Exception) as e:
            logger.warning("llm analyst macro failed: %s", e)
            ctx["macro"] = {}
            ctx["errors"].append(f"macro: {e}")

        # IV 期限结构
        try:
            ctx["iv_term"] = IVTermStructureAnalyzer().analyze(currency)
        except (RuntimeError, ConnectionError, TimeoutError, Exception) as e:
            logger.warning("llm analyst iv_term failed: %s", e)
            ctx["iv_term"] = {}
            ctx["errors"].append(f"iv_term: {e}")

        # 策略引擎结果
        try:
            from services.strategy_engine import StrategyEngine
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
