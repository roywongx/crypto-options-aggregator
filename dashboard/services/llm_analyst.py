"""
LLM 分析师引擎 — AI 研判中心核心
叠加在 5 个规则 agent 之上的 LLM 综合分析层
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Module-level imports so @patch("services.llm_analyst.X") works in tests
from services.options_debate_engine import _gather_market_data
from services.onchain_metrics import OnChainMetrics
from services.derivative_metrics import DerivativeMetrics
from services.macro_data import get_all_macro_data
from services.iv_term_structure import IVTermStructureAnalyzer
from services.ai_router import ai_chat_with_config


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
        """LLM 综合分析师 — 资深期权策略师"""
        system_prompt = """你是一位资深加密货币期权策略师。基于以下全量市场数据和规则引擎分析报告，给出综合研判。

要求：
1. market_assessment: 市场整体评估（多空力量、关键位、趋势判断）
2. strategy_recommendation: 策略建议（具体操作、行权价、DTE、理由）
3. risk_warning: 风险提示（需要关注的风险因素）
4. confidence: 信心度（0-100）

请严格返回 JSON 格式，不要添加其他文字。"""

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

            parsed = self._parse_json_response(response)
            if parsed is None:
                return {"success": False, "error": "LLM 返回格式异常", "raw_response": response[:500]}

            required = {"market_assessment", "strategy_recommendation", "risk_warning", "confidence"}
            if not required.issubset(parsed.keys()):
                return {"success": False, "error": "LLM response missing required fields", "raw_response": response[:500]}

            parsed["success"] = True
            return parsed

        except Exception as e:
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

    def _llm_debate(self, context: Dict, synthesis: Dict) -> Dict[str, Any]:
        """LLM 多空辩论 — Bull/Bear/Judge 三次调用"""
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

        # Execute bull and bear (serial, independent error handling)
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
        except Exception as e:
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
        except Exception as e:
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
        except Exception as e:
            logger.warning("judge agent failed: %s", e)
            judge_result = {"success": False, "error": str(e)}

        return {
            "success": bull_result.get("success") or bear_result.get("success"),
            "bull": bull_result,
            "bear": bear_result,
            "judge": judge_result,
        }

    def _llm_audit(self, context: Dict, rule_reports: Dict, synthesis: Dict) -> Dict[str, Any]:
        """LLM 异常检测 — 留给 Task 5 实现"""
        return {"success": False, "placeholder": "audit not implemented"}
