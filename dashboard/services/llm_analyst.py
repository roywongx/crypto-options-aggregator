"""
LLM 分析师引擎 — AI 研判中心核心
叠加在 5 个规则 agent 之上的 LLM 综合分析层
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


def _get_or_create_fernet() -> Fernet:
    """获取 Fernet 加密实例，密钥持久化到文件以便跨重启解密"""
    env_key = os.environ.get("LLM_ENCRYPTION_KEY")
    if env_key:
        return Fernet(env_key.encode() if isinstance(env_key, str) else env_key)

    key_file = Path(__file__).parent.parent / "data" / ".llm_key"
    if key_file.exists():
        return Fernet(key_file.read_bytes())

    key = Fernet.generate_key()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(key)
    logger.info("Generated new Fernet key at %s", key_file)
    return Fernet(key)


_fernet = _get_or_create_fernet()


def _encrypt_key(key: str) -> str:
    if not key:
        return ""
    return _fernet.encrypt(key.encode()).decode()


def _decrypt_key(encrypted: str) -> str:
    if not encrypted:
        return ""
    try:
        return _fernet.decrypt(encrypted.encode()).decode()
    except Exception:
        # 旧版明文密钥，返回原值（调用方会重新加密保存）
        logger.info("Detected legacy plaintext API key, will migrate on next save")
        return encrypted

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
            md = {"spot": 0, "dvol": {}, "large_trades": [], "contracts": [], "max_pain": 0, "risk_status": "UNKNOWN", "risk_label": "", "risk_desc": "", "data_timestamp": "", "errors": [f"gather_market_data: {e}"]}
        ctx["spot"] = md.get("spot", 0)
        ctx["dvol"] = md.get("dvol", {})
        ctx["data_timestamp"] = md.get("data_timestamp", "")
        # DVOL fallback: 如果 API 调用失败返回空 {}，使用 DB scan_records 中的历史值
        if not ctx["dvol"] or not ctx["dvol"].get("current"):
            db_dvol_val = md.get("db_dvol", 0)
            if db_dvol_val and db_dvol_val > 0:
                ctx["dvol"] = {
                    "current": db_dvol_val,
                    "z_score": md.get("db_dvol_z", 0),
                    "signal": md.get("db_dvol_signal", "来自数据库缓存"),
                    "trend": "→", "trend_label": "缓存",
                    "confidence": "低 (数据库缓存)", "interpretation": "",
                    "data_points": 0, "percentile_7d": 50.0,
                    "source": "db_fallback",
                }
        ctx["large_trades"] = md.get("large_trades", [])
        ctx["contracts"] = md.get("contracts", [])
        ctx["max_pain"] = md.get("max_pain", 0) or 0
        # max_pain fallback: 直接调用 Deribit OI 官方算法
        if ctx["max_pain"] <= 0:
            try:
                from services.max_pain import get_max_pain
                mp = get_max_pain(currency, auto_calc=True)
                if mp > 0:
                    ctx["max_pain"] = mp
            except Exception:
                pass
        ctx["risk"] = {
            "status": md.get("risk_status", "UNKNOWN"),
            "label": md.get("risk_label", ""),
            "desc": md.get("risk_desc", ""),
        }
        ctx["errors"].extend(md.get("errors", []))

        # 链上指标
        try:
            ctx["onchain"] = OnChainMetrics.get_all_metrics()
        except Exception as e:
            logger.warning("llm analyst onchain failed: %s", e)
            ctx["onchain"] = {}
            ctx["errors"].append(f"onchain: {e}")

        # 衍生品指标
        try:
            ctx["derivatives"] = DerivativeMetrics.get_all_metrics()
        except Exception as e:
            logger.warning("llm analyst derivatives failed: %s", e)
            ctx["derivatives"] = {}
            ctx["errors"].append(f"derivatives: {e}")

        # 宏观数据
        try:
            ctx["macro"] = get_all_macro_data()
        except Exception as e:
            logger.warning("llm analyst macro failed: %s", e)
            ctx["macro"] = {}
            ctx["errors"].append(f"macro: {e}")

        # IV 期限结构 — 先获取 Deribit 数据，再调用分析器
        try:
            from services.trades import fetch_deribit_summaries
            from services.instrument import _parse_inst_name
            summaries = fetch_deribit_summaries(currency)
            if summaries:
                term_data = []
                for s in summaries:
                    meta = _parse_inst_name(s.get("instrument_name", ""))
                    if not meta or meta.dte < 1:
                        continue
                    iv = float(s.get("mark_iv") or 0)
                    oi = float(s.get("open_interest") or 0)
                    if iv < 10 or oi < 10:
                        continue
                    term_data.append({
                        "strike": meta.strike, "dte": meta.dte,
                        "expiry": meta.expiry, "option_type": meta.option_type,
                        "iv": iv, "oi": oi,
                    })
                if term_data and len(term_data) >= 2:
                    # 按 expiry 分组取 ATM IV
                    expiries = {}
                    for p in term_data:
                        ekey = p["expiry"]
                        if ekey not in expiries:
                            expiries[ekey] = {"dte": p["dte"], "expiry": p["expiry"], "ivs": []}
                        expiries[ekey]["ivs"].append(p["iv"])
                    ts_data = []
                    for ed in sorted(expiries.values(), key=lambda x: x["dte"]):
                        avg_iv = sum(ed["ivs"]) / len(ed["ivs"]) if ed["ivs"] else 0
                        if avg_iv > 0:
                            ts_data.append({"dte": ed["dte"], "avg_iv": round(avg_iv, 1), "expiry": ed["expiry"]})
                    if len(ts_data) >= 2:
                        ctx["iv_term"] = IVTermStructureAnalyzer.analyze_term_structure(
                            ts_data, ctx["spot"]
                        )
                    else:
                        ctx["iv_term"] = {"error": "不足2个有效到期日", "state": "NO_DATA"}
                else:
                    ctx["iv_term"] = {"error": "无有效 Deribit 数据", "state": "NO_DATA"}
            else:
                ctx["iv_term"] = {"error": "Deribit summaries 为空", "state": "NO_DATA"}
        except Exception as e:
            logger.warning("llm analyst iv_term failed: %s", e)
            ctx["iv_term"] = {"error": str(e), "state": "NO_DATA"}
            ctx["errors"].append(f"iv_term: {e}")

        # 策略引擎结果 — 使用 UnifiedStrategyEngine（与推荐面板一致）
        try:
            from services.unified_strategy_engine import (
                UnifiedStrategyEngine, StrategyParams, StrategyMode, OptionType
            )
            dvol = ctx["dvol"]
            dvol_val = dvol.get("current", 50)
            # DVOL 自适应参数调整
            if dvol_val > 70:
                params = StrategyParams(currency=currency, mode=StrategyMode.NEW, option_type=OptionType.PUT,
                    reserve_capital=100000, target_max_delta=0.20, min_dte=7, max_dte=21,
                    margin_ratio=0.22, min_apr=25.0, put_count=5)
            elif dvol_val > 50:
                params = StrategyParams(currency=currency, mode=StrategyMode.NEW, option_type=OptionType.PUT,
                    reserve_capital=100000, target_max_delta=0.30, min_dte=14, max_dte=35,
                    margin_ratio=0.20, min_apr=15.0, put_count=5)
            else:
                params = StrategyParams(currency=currency, mode=StrategyMode.NEW, option_type=OptionType.PUT,
                    reserve_capital=100000, target_max_delta=0.40, min_dte=7, max_dte=60,
                    margin_ratio=0.18, min_apr=8.0, put_count=5)
            ue = UnifiedStrategyEngine()
            rec = ue.execute(ctx["contracts"], params, ctx["spot"])
            recs = rec.get("plans", [])
            ctx["strategy_summary"] = {
                "engine": "UnifiedStrategyEngine",
                "mode": rec.get("mode", "new"),
                "params": rec.get("params", {}),
                "total_scanned": rec.get("meta", {}).get("total_contracts_scanned", 0),
                "plans_found": rec.get("meta", {}).get("plans_found", 0),
                "top_recommendations": [
                    {"strike": r["strike"], "premium": r["premium_usd"], "apr": r["metrics"]["apr"],
                     "score": r["score"], "win_rate": r["metrics"]["win_rate"],
                     "max_loss": r["metrics"]["max_loss"]}
                    for r in recs[:3]
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

        # 5. LLM 异常检测 — 双层：确定性永远成功，LLM 可选
        try:
            result.audit = self._llm_audit(context, result.rule_reports, result.synthesis)
        except Exception as e:
            logger.error("Audit crashed, falling back to deterministic only: %s", e)
            result.audit = self._deterministic_audit(context, result.rule_reports)

        # 兜底：确保 audit 永远有合法结构
        if not isinstance(result.audit, dict):
            result.audit = {"success": True, "anomalies": [], "logic_issues": [], "data_quality_score": 0, "audit_method": "fallback"}
        result.audit.setdefault("success", True)
        result.audit.setdefault("anomalies", [])
        result.audit.setdefault("logic_issues", [])
        result.audit.setdefault("data_quality_score", 0)
        # 移除可能从前端渲染路径泄露的 error 字段
        result.audit.pop("error", None)

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
        """从数据库读取 LLM 配置，返回 custom_config 格式（解密 API Key）"""
        try:
            from db.connection import execute_read
            rows = execute_read(
                "SELECT api_key, base_url, model FROM llm_config WHERE id=1"
            )
            if rows and rows[0]:
                encrypted_key = rows[0][0] or ""
                base_url = rows[0][1] or ""
                model = rows[0][2] or ""
                if encrypted_key:
                    api_key = _decrypt_key(encrypted_key)
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

    def load_config(self) -> Dict[str, str]:
        """从数据库加载 LLM 配置（解密 API Key）"""
        try:
            from db.connection import execute_read
            rows = execute_read(
                "SELECT api_key, base_url, model FROM llm_config WHERE id=1"
            )
            if rows and rows[0]:
                encrypted_key = rows[0][0] or ""
                return {
                    "api_key": _decrypt_key(encrypted_key),
                    "base_url": rows[0][1] or "",
                    "model": rows[0][2] or "",
                }
        except Exception as e:
            logger.warning("load_config failed: %s", e)
        return {"api_key": "", "base_url": "", "model": ""}

    def save_config(self, api_key: str, base_url: str = "", model: str = "") -> bool:
        """保存 LLM 配置到数据库，API Key 加密存储"""
        try:
            from db.connection import execute_write
            encrypted_key = _encrypt_key(api_key) if api_key else ""
            execute_write(
                """INSERT OR REPLACE INTO llm_config (id, api_key, base_url, model, updated_at)
                   VALUES (1, ?, ?, ?, datetime('now'))""",
                (encrypted_key, base_url, model)
            )
            return True
        except Exception as e:
            logger.error("save_config failed: %s", e)
            return False

    def test_connection(self, config: Dict[str, str]) -> Dict[str, Any]:
        """测试 LLM 连接"""
        import time

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
                preset="fast", temperature=0, max_tokens=100,
                custom_config=custom_config or None
            )
            latency = int((time.time() - start) * 1000)

            if response and len(response.strip()) > 0:
                return {"success": True, "latency_ms": latency, "model": config.get("model", "default"), "reply": response[:100]}
            else:
                return {"success": False, "error": f"模型返回异常: {response[:100] if response else '无响应'}", "latency_ms": latency}

        except Exception as e:
            latency = int((time.time() - start) * 1000)
            return {"success": False, "error": str(e), "latency_ms": latency}

    def _llm_synthesize(self, context: Dict, rule_reports: Dict) -> Dict[str, Any]:
        """LLM 综合分析师 — 资深期权策略师"""
        system_prompt = """你是一位资深加密货币期权策略师。基于全量市场数据和规则引擎分析报告，给出综合研判。

核心约束：
1. 你的策略建议必须与规则引擎的综合研判方向一致。规则引擎判定为"观望"时，不得建议高风险的主动策略（如 Short Strangle/裸卖空）。
2. 如果规则引擎判定为"观望或小仓位操作"，你的建议只能是小仓位 Sell Put 或 Buy Call，风险暴露不超过保证金的 20%。
3. 最大痛点（Max Pain）是关键参考位 — 行权价设在痛点下方=跌破支撑时亏损；行权价设在痛点上方=更安全但权利金更低。需明确解释你的行权价选择理由。
4. 如果 PCR < 0.5（看涨主导）但大单卖出占主导，需要解释这一矛盾并说明你的判断依据。

输出要求：
1. market_assessment: 市场整体评估（多空力量对比、关键技术位、波动率体制、趋势判断）
2. strategy_recommendation: 策略建议（具体操作类型、行权价选择理由、建议DTE、与规则引擎方向的一致性说明）
3. risk_warning: 风险提示（需要关注的风险因素、最大亏损情景）
4. confidence: 信心度（0-100，基于数据完整性和信号一致性）
5. rule_engine_alignment: 与规则引擎的一致性说明（"完全一致"/"方向一致但更激进"/"方向一致但更保守"/"存在分歧，理由:..."）

请严格返回 JSON 格式，不要添加其他文字。"""

        data_summary = self._build_data_summary(context)
        rule_summary = self._build_rule_summary(rule_reports)

        user_prompt = f"""=== 币种: {context['currency']} ===

=== 市场数据 ===
{data_summary}

=== 规则引擎分析 ===
{rule_summary}

请基于以上数据给出综合研判 JSON。包含 market_assessment, strategy_recommendation, risk_warning, confidence, rule_engine_alignment 五个字段。"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        custom_config = self._get_custom_config()

        try:
            response = ai_chat_with_config(
                messages, preset="analysis", temperature=0.3,
                max_tokens=4000, custom_config=custom_config
            )
            if not response:
                return {"success": False, "error": "LLM 无响应，请检查 API Key 配置"}

            parsed = self._parse_json_response(response)
            if parsed is None:
                return {"success": False, "error": "LLM 返回格式异常", "raw_response": response[:500]}

            parsed.setdefault("market_assessment", "")
            parsed.setdefault("strategy_recommendation", "")
            parsed.setdefault("risk_warning", "")
            parsed.setdefault("confidence", 0)
            parsed.setdefault("rule_engine_alignment", "未提供")
            parsed["success"] = True
            return parsed

        except Exception as e:
            logger.error("llm_synthesize failed: %s", e)
            return {"success": False, "error": str(e)}

    def _build_data_summary(self, ctx: Dict) -> str:
        """将全量上下文数据格式化为结构化文本，包含聚合后的 Greeks、资金流向和时序对比"""
        parts = []
        spot = ctx.get("spot", 0) or 0

        # ============================================================
        # 1. 现货价格 + 时序变化
        # ============================================================
        parts.append(f"现货价格: ${spot:,.0f}")
        # 数据时间戳 + 新鲜度检查
        data_ts = ctx.get("data_timestamp", "")
        if data_ts:
            try:
                from datetime import datetime as _dt
                ts_dt = _dt.strptime(data_ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                age_min = (_dt.now(timezone.utc) - ts_dt).total_seconds() / 60
                if age_min > 60:
                    parts.append(f"  ⚠️ 数据时间: {data_ts} (已过期 {age_min:.0f} 分钟，以下分析基于旧数据，建议刷新)")
                else:
                    parts.append(f"  数据时间: {data_ts} (以下所有分析基于此时间点)")
            except (ValueError, TypeError):
                parts.append(f"  数据时间: {data_ts} (以下所有分析基于此时间点)")
        # 尝试从链上数据取24h价格变化
        onchain = ctx.get("onchain", {})
        if isinstance(onchain, dict) and onchain.get("current_price"):
            oc_price = float(onchain["current_price"])
            if oc_price > 0 and spot > 0:
                diff_pct = (spot - oc_price) / oc_price * 100
                parts.append(f"  链上参考价: ${oc_price:,.0f} (偏差 {diff_pct:+.2f}%)")

        # ============================================================
        # 2. DVOL + 体制 + 来源标注
        # ============================================================
        dvol = ctx.get("dvol", {})
        if isinstance(dvol, dict) and dvol.get("current"):
            source_tag = f" [来源:{dvol.get('source', 'API')}]" if dvol.get("source") else ""
            parts.append(
                f"DVOL: {dvol['current']:.1f} | Z-Score: {dvol.get('z_score', 0):.2f} | "
                f"信号: {dvol.get('signal', 'N/A')} | 趋势: {dvol.get('trend', 'N/A')}"
                f" | 7日分位: {dvol.get('percentile_7d', 50):.0f}%{source_tag}"
            )

        # ============================================================
        # 3. 风险框架
        # ============================================================
        risk = ctx.get("risk", {})
        if isinstance(risk, dict) and risk.get("status"):
            parts.append(f"风险状态: {risk.get('label', '')} ({risk.get('status', '')}) — {risk.get('desc', '')}")

        # ============================================================
        # 4. 链上指标（修复字段名 + 增加关键指标）
        # ============================================================
        if isinstance(onchain, dict) and onchain:
            mvrv = onchain.get("mvrv_ratio") or onchain.get("mvrv", "N/A")
            nupl = onchain.get("nupl", "N/A")
            conv = onchain.get("convergence_score", "N/A")
            mayer = onchain.get("mayer_multiple", "N/A")
            puell = onchain.get("puell_multiple", "N/A")
            wma_ratio = onchain.get("price_to_200wma_ratio", "N/A")
            parts.append(
                f"链上: MVRV={mvrv} | NUPL={nupl} | Mayer={mayer} | "
                f"Puell={puell} | 200WMA比={wma_ratio} | 汇合评分={conv}"
            )

        # ============================================================
        # 5. 衍生品指标（修复字段名）
        # ============================================================
        deriv = ctx.get("derivatives", {})
        if isinstance(deriv, dict) and deriv:
            sharpe14 = deriv.get("sharpe_ratio_14d", "N/A")
            sharpe30 = deriv.get("sharpe_ratio_30d", "N/A")
            vol_ratio = deriv.get("futures_spot_ratio", {})
            vol_label = vol_ratio.get("label", "N/A") if isinstance(vol_ratio, dict) else "N/A"
            overheating = deriv.get("overheating_assessment", {})
            overheat_score = overheating.get("score", "N/A") if isinstance(overheating, dict) else "N/A"
            perp_basis = deriv.get("perp_basis", {})
            basis_pct = perp_basis.get("basis_annualized", "N/A") if isinstance(perp_basis, dict) else "N/A"
            parts.append(
                f"衍生品: Sharpe14d={sharpe14} | Sharpe30d={sharpe30} | "
                f"期货/现货比={vol_label} | 过热评分={overheat_score} | 永续基差年化={basis_pct}%"
            )

        # ============================================================
        # 6. 宏观数据
        # ============================================================
        macro = ctx.get("macro", {})
        if isinstance(macro, dict):
            fg = macro.get("fear_greed", {})
            fr = macro.get("funding_rate", {})
            fg_val = fg.get("value", "N/A") if isinstance(fg, dict) else "N/A"
            fg_cls = fg.get("classification", "") if isinstance(fg, dict) else ""
            fr_val = fr.get("current_rate", "N/A") if isinstance(fr, dict) else "N/A"
            parts.append(
                f"宏观: 恐惧贪婪={fg_val}({fg_cls}) | 资金费率={fr_val}"
            )

        # ============================================================
        # 7. IV 期限结构
        # ============================================================
        iv = ctx.get("iv_term", {})
        if isinstance(iv, dict):
            iv_state = iv.get("state") or (iv.get("market_state", {}).get("state") if isinstance(iv.get("market_state"), dict) else None) or "N/A"
            iv_slope = iv.get("slope", "N/A")
            iv_vrp = iv.get("vrp", "N/A")
            iv_spot_vs_atm = iv.get("spot_vs_atm", "N/A")
            parts.append(
                f"IV期限结构: 状态={iv_state} | 斜率={iv_slope} | VRP={iv_vrp} | 现货vs ATM IV={iv_spot_vs_atm}"
            )

        # ============================================================
        # 8. 最大痛点 + 现货位置关系
        # ============================================================
        max_pain = ctx.get("max_pain", 0) or 0
        if max_pain > 0 and spot > 0:
            mp_dist = (spot - max_pain) / spot * 100
            mp_relation = "上方" if mp_dist > 0 else "下方"
            parts.append(
                f"最大痛点: ${max_pain:,.0f} (现货在痛点{mp_relation} {abs(mp_dist):.1f}%，"
                f"痛点{'构成支撑' if mp_dist > 0 else '构成阻力'})"
            )
        else:
            parts.append(f"最大痛点: ${max_pain:,.0f} (数据可能不可靠，源自OI估算)")

        # ============================================================
        # 9. 期权合约 Greeks 聚合分析（优先级1：核心新增）
        # ============================================================
        contracts = ctx.get("contracts", [])
        if contracts and spot > 0:
            parts.append(self._build_contracts_aggregation(contracts, spot))

        # ============================================================
        # 10. 大单资金流向聚合（优先级2：核心新增）
        # ============================================================
        trades = ctx.get("large_trades", [])
        if trades:
            parts.append(self._build_trade_flow_aggregation(trades, spot))
        else:
            parts.append("大单流向: 近期无大单记录")

        # ============================================================
        # 11. 策略引擎推荐
        # ============================================================
        strategy = ctx.get("strategy_summary", {})
        if isinstance(strategy, dict):
            recs = strategy.get("top_recommendations", [])
            if recs:
                parts.append("策略引擎推荐 TOP3:")
                for r in recs:
                    parts.append(
                        f"  Strike=${r.get('strike', 0):,.0f} Premium=${r.get('premium', 0):,.0f} "
                        f"APR={r.get('apr', 0):.1f}% Score={r.get('score', 0):.3f} → {r.get('rec', '')}"
                    )

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 辅助方法：合约 Greeks 聚合
    # ------------------------------------------------------------------
    @staticmethod
    def _build_contracts_aggregation(contracts: list, spot: float) -> str:
        """从 600+ 合约中提取 LLM 推理所需的关键 Greeks 摘要"""
        lines = [f"期权合约分析 (共{len(contracts)}个):"]

        # --- 按 Put/Call 分类 ---
        puts = [c for c in contracts if str(c.get("option_type", "")).upper() in ("PUT", "P")]
        calls = [c for c in contracts if str(c.get("option_type", "")).upper() in ("CALL", "C")]
        put_oi = sum(c.get("open_interest", 0) or 0 for c in puts)
        call_oi = sum(c.get("open_interest", 0) or 0 for c in calls)
        pcr_oi = put_oi / call_oi if call_oi > 0 else 0
        lines.append(f"  Put/Call OI比: {pcr_oi:.2f} (Put OI={put_oi:,.0f}, Call OI={call_oi:,.0f})")

        # --- ATM IV ---
        atm_contracts = [c for c in contracts if abs(c.get("strike", 0) - spot) / spot < 0.02]
        if atm_contracts:
            atm_ivs = [c.get("iv", 0) for c in atm_contracts if c.get("iv")]
            if atm_ivs:
                avg_atm_iv = sum(atm_ivs) / len(atm_ivs)
                lines.append(f"  ATM IV: {avg_atm_iv:.1f}% (基于{len(atm_contracts)}个近ATM合约)")

        # --- IV Skew (25-delta 近似) ---
        otm_puts = [c for c in puts if c.get("strike", 0) < spot * 0.95 and c.get("iv")]
        otm_calls = [c for c in calls if c.get("strike", 0) > spot * 1.05 and c.get("iv")]
        if otm_puts and otm_calls:
            avg_put_iv = sum(c["iv"] for c in otm_puts[:20]) / min(len(otm_puts), 20)
            avg_call_iv = sum(c["iv"] for c in otm_calls[:20]) / min(len(otm_calls), 20)
            skew = avg_put_iv - avg_call_iv
            skew_signal = "偏斜看跌(尾部风险定价偏高)" if skew > 3 else "偏斜看涨" if skew < -3 else "偏斜中性"
            lines.append(f"  IV Skew (Put-Call): {skew:+.1f}% — {skew_signal}")

        # --- Gamma 暴露集中度 ---
        near_expiry = [c for c in contracts if 0 < c.get("dte", 999) <= 7 and c.get("gamma")]
        if near_expiry:
            # 按行权价分组聚合 Gamma
            gamma_by_strike = {}
            for c in near_expiry:
                strike = c.get("strike", 0)
                gamma = float(c.get("gamma", 0) or 0)
                oi = float(c.get("open_interest", 0) or 0)
                if strike > 0 and gamma > 0 and oi > 0:
                    band = round(strike / 500) * 500  # 按$500分组
                    gamma_by_strike[band] = gamma_by_strike.get(band, 0) + gamma * oi

            if gamma_by_strike:
                top_gamma = sorted(gamma_by_strike.items(), key=lambda x: x[1], reverse=True)[:3]
                gamma_str = ", ".join(f"${k:,.0f}: {v:.4f}" for k, v in top_gamma)
                lines.append(f"  Gamma暴露TOP3 (近到期≤7天): {gamma_str}")

        # --- OI 集中度 ---
        if contracts:
            by_strike_oi = {}
            for c in contracts:
                strike = c.get("strike", 0)
                oi = float(c.get("open_interest", 0) or 0)
                if strike > 0 and oi > 0:
                    by_strike_oi[strike] = by_strike_oi.get(strike, 0) + oi
            if by_strike_oi:
                max_oi_strike = max(by_strike_oi, key=by_strike_oi.get)
                max_oi_val = by_strike_oi[max_oi_strike]
                dist_from_spot = (max_oi_strike - spot) / spot * 100
                lines.append(
                    f"  最大OI行权价: ${max_oi_strike:,.0f} (OI={max_oi_val:,.0f}, "
                    f"距现货{dist_from_spot:+.1f}%)"
                )

        # --- DTE 分布 ---
        dtes = [c.get("dte", 0) for c in contracts if c.get("dte", 0) > 0]
        if dtes:
            lines.append(
                f"  DTE范围: {min(dtes)}-{max(dtes)}天 "
                f"(短期≤7天: {sum(1 for d in dtes if d <= 7)}个, "
                f"中期8-30天: {sum(1 for d in dtes if 8 <= d <= 30)}个, "
                f"长期>30天: {sum(1 for d in dtes if d > 30)}个)"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 辅助方法：大单资金流向聚合
    # ------------------------------------------------------------------
    @staticmethod
    def _build_trade_flow_aggregation(trades: list, spot: float) -> str:
        """从大单列表中提取聚合统计，替代原始单笔数据"""
        lines = [f"大单流向分析 (共{len(trades)}笔):"]

        # --- 按方向聚合 ---
        buy_notional = sum(t.get("buy_notional", 0) or 0 for t in trades)
        sell_notional = sum(t.get("sell_notional", 0) or 0 for t in trades)
        total_notional = buy_notional + sell_notional
        if total_notional > 0:
            net_ratio = (buy_notional - sell_notional) / total_notional
            direction = "净买入主导" if net_ratio > 0.1 else "净卖出主导" if net_ratio < -0.1 else "买卖均衡"
            lines.append(
                f"  总名义价值: ${total_notional:,.0f} | "
                f"净买卖比: {net_ratio:+.2f} ({direction}) | "
                f"买入=${buy_notional:,.0f} 卖出=${sell_notional:,.0f}"
            )

        # --- PCR (Put/Call Ratio) ---
        buy_put = sum(t.get("buy_notional", 0) or 0 for t in trades
                      if str(t.get("option_type", "")).upper() in ("PUT", "P"))
        buy_call = sum(t.get("buy_notional", 0) or 0 for t in trades
                       if str(t.get("option_type", "")).upper() in ("CALL", "C"))
        sell_put = sum(t.get("sell_notional", 0) or 0 for t in trades
                       if str(t.get("option_type", "")).upper() in ("PUT", "P"))
        sell_call = sum(t.get("sell_notional", 0) or 0 for t in trades
                        if str(t.get("option_type", "")).upper() in ("CALL", "C"))

        if buy_call > 0 or buy_put > 0:
            pcr = buy_put / buy_call if buy_call > 0 else 999
            pcr_signal = "机构对冲防跌(>1.5)" if pcr > 1.5 else "看涨主导(<0.5)" if pcr < 0.5 else "中性(0.5-1.5)"
            lines.append(f"  PCR(买入Put/买入Call): {pcr:.2f} — {pcr_signal}")

        # --- Sell Put / Buy Call 力量对比 ---
        if buy_call > 0 or sell_put > 0:
            lines.append(
                f"  主动买入Call: ${buy_call:,.0f} | 主动卖出Put: ${sell_put:,.0f} | "
                f"主动买入Put: ${buy_put:,.0f} | 主动卖出Call: ${sell_call:,.0f}"
            )

        # --- 按与现货距离分组 ---
        near_spot = [t for t in trades if t.get("strike") and abs(t["strike"] - spot) / spot < 0.05]
        otm_range = [t for t in trades if t.get("strike") and 0.05 <= abs(t["strike"] - spot) / spot < 0.20]
        far_otm = [t for t in trades if t.get("strike") and abs(t["strike"] - spot) / spot >= 0.20]

        if near_spot:
            near_buy = sum(t.get("buy_notional", 0) or 0 for t in near_spot)
            near_sell = sum(t.get("sell_notional", 0) or 0 for t in near_spot)
            lines.append(f"  近现货(±5%): {len(near_spot)}笔, 买入${near_buy:,.0f} 卖出${near_sell:,.0f}")
        if otm_range:
            otm_buy = sum(t.get("buy_notional", 0) or 0 for t in otm_range)
            otm_sell = sum(t.get("sell_notional", 0) or 0 for t in otm_range)
            lines.append(f"  中距(±5-20%): {len(otm_range)}笔, 买入${otm_buy:,.0f} 卖出${otm_sell:,.0f}")
        if far_otm:
            far_buy = sum(t.get("buy_notional", 0) or 0 for t in far_otm)
            far_sell = sum(t.get("sell_notional", 0) or 0 for t in far_otm)
            lines.append(f"  远距(>±20%): {len(far_otm)}笔, 买入${far_buy:,.0f} 卖出${far_sell:,.0f}")

        # --- 大宗交易比例 ---
        block_trades = [t for t in trades if t.get("is_block")]
        if block_trades:
            lines.append(f"  大宗交易(block): {len(block_trades)}笔/{len(trades)}笔")

        return "\n".join(lines)

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
        """从 LLM 响应中提取 JSON，处理常见的 DeepSeek thinking 模式产物"""
        if not response:
            return None

        # 1. 直接解析
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # 2. 提取 ```json ... ``` 块
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # 3. 找到第一个 { 到最后一个 }
        start = response.find('{')
        end = response.rfind('}')
        if start != -1 and end != -1 and end > start:
            candidate = response[start:end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                # 4. 修复常见 LLM JSON 错误：尾部逗号、缺少逗号、单引号
                pass
            # 尝试更激进的修复
            try:
                fixed = self._fix_llm_json(candidate)
                if fixed:
                    return json.loads(fixed)
            except (json.JSONDecodeError, Exception):
                pass

        # 5. 如果以上都失败，记录响应前 500 字符并返回 None
        logger.warning("LLM JSON parse failed, raw response start: %s", response[:500])
        return None

    @staticmethod
    def _fix_llm_json(text: str) -> Optional[str]:
        """修复 LLM 常见的 JSON 语法错误：尾部逗号、注释行、Python 关键字"""
        import re as _re
        fixed = text

        # 1. 移除尾部逗号 (在 ] 或 } 前)
        fixed = _re.sub(r',\s*([]}])', r'\1', fixed)

        # 2. 移除整行注释（以 // 或 # 开头的行）
        lines = fixed.split('\n')
        repaired = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('//'):
                continue
            repaired.append(line)
        fixed = '\n'.join(repaired)

        # 3. 移除行内 // 注释（只移除 JSON 结构字符后面的，保护字符串内的 //）
        fixed = _re.sub(
            r'([\}\]\"\d,])\s*//[^\n]*$',
            r'\1',
            fixed,
            flags=_re.MULTILINE
        )

        # 4. 转换 Python 关键字为 JSON 关键字（不在字符串内的）
        fixed = _re.sub(r'(?<=:)\s*None\b', ' null', fixed)
        fixed = _re.sub(r'(?<=:)\s*True\b', ' true', fixed)
        fixed = _re.sub(r'(?<=:)\s*False\b', ' false', fixed)

        if fixed.strip().startswith('{'):
            return fixed
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
                max_tokens=3000, custom_config=custom_config
            )
            if bull_resp:
                parsed = self._parse_json_response(bull_resp)
                if parsed:
                    parsed.setdefault("bullish_case", "")
                    parsed.setdefault("key_drivers", [])
                    parsed.setdefault("target_scenarios", [])
                    parsed.setdefault("confidence", 0)
                    parsed["success"] = True
                    bull_result = parsed
        except Exception as e:
            logger.warning("bull agent failed: %s", e)
            bull_result = {"success": False, "error": str(e)}

        try:
            bear_resp = ai_chat_with_config(
                bear_prompt, preset="analysis", temperature=0.4,
                max_tokens=3000, custom_config=custom_config
            )
            if bear_resp:
                parsed = self._parse_json_response(bear_resp)
                if parsed:
                    parsed.setdefault("bearish_case", "")
                    parsed.setdefault("key_risks", [])
                    parsed.setdefault("downside_scenarios", [])
                    parsed.setdefault("confidence", 0)
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
                max_tokens=3000, custom_config=custom_config
            )
            if judge_resp:
                parsed = self._parse_json_response(judge_resp)
                if parsed:
                    parsed.setdefault("judge_verdict", "")
                    parsed.setdefault("winner", "draw")
                    parsed.setdefault("bull_confidence", 50)
                    parsed.setdefault("bear_confidence", 50)
                    parsed.setdefault("reasoning", "")
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

    def _deterministic_audit(self, context: Dict, rule_reports: Dict = None) -> Dict[str, Any]:
        """确定性数据质量审计 — 纯程序化检查，绝不依赖 LLM

        检查维度：
        1. 数据完整性 — 各数据源是否都存在且非空
        2. 数值合理性 — 核心指标是否在合理范围内
        3. 跨源一致性 — 不同数据源之间是否存在矛盾
        4. 时间新鲜度 — 数据是否可能过期
        5. 合约/交易数量 — 数据量是否足以支撑分析

        Returns:
            {"anomalies": [...], "logic_issues": [...], "data_quality_score": 0-100,
             "checks_detail": {...}, "success": True}
        """
        anomalies = []
        logic_issues = []
        checks = {}
        score = 100  # 起始满分，发现问题逐项扣分

        currency = context.get("currency", "BTC") or "BTC"
        spot = context.get("spot", 0) or 0

        # ================================================================
        # 1. 数据完整性检查
        # ================================================================
        completeness = {}

        # Spot 价格
        if spot <= 0:
            anomalies.append({
                "source": "spot_price", "severity": "critical",
                "description": f"现货价格缺失或为0 (spot={spot})",
                "suggestion": "检查 get_spot_price 和 exchange_abstraction 数据源"
            })
            completeness["spot"] = False
            score -= 20
        else:
            completeness["spot"] = True

        # DVOL
        dvol = context.get("dvol", {})
        if not dvol or (isinstance(dvol, dict) and not dvol.get("current")) or (isinstance(dvol, dict) and dvol.get("current") == 0):
            anomalies.append({
                "source": "dvol", "severity": "warning",
                "description": "DVOL 波动率数据缺失或为0",
                "suggestion": "检查 Deribit DVOL API 连接"
            })
            completeness["dvol"] = False
            score -= 10
        else:
            completeness["dvol"] = True

        # Onchain
        onchain = context.get("onchain", {})
        if not onchain or (isinstance(onchain, dict) and not any(k in onchain for k in ("mvrv", "nupl", "convergence_score"))):
            anomalies.append({
                "source": "onchain", "severity": "warning",
                "description": "链上指标缺失 (MVRV/NUPL 不可用)",
                "suggestion": "检查 Glassnode/CoinGecko API 连接"
            })
            completeness["onchain"] = False
            score -= 10
        else:
            completeness["onchain"] = True

        # Derivatives
        derivatives = context.get("derivatives", {})
        if not derivatives or (isinstance(derivatives, dict) and not derivatives):
            anomalies.append({
                "source": "derivatives", "severity": "warning",
                "description": "衍生品指标缺失",
                "suggestion": "检查衍生品数据 API 连接"
            })
            completeness["derivatives"] = False
            score -= 10
        else:
            completeness["derivatives"] = True

        # Macro
        macro = context.get("macro", {})
        if not macro or (isinstance(macro, dict) and not any(k in macro for k in ("fear_greed", "funding_rate"))):
            logic_issues.append({
                "component": "macro_data", "severity": "info",
                "description": "宏观数据缺失 (恐惧贪婪指数/资金费率)",
                "suggestion": "检查 alternative.me 和交易所 API"
            })
            completeness["macro"] = False
            score -= 5
        else:
            completeness["macro"] = True

        # IV Term Structure — state 存在 market_state.state 或顶层 state
        iv_term = context.get("iv_term", {})
        iv_has_state = (
            isinstance(iv_term, dict) and (
                iv_term.get("state") or
                (isinstance(iv_term.get("market_state"), dict) and iv_term["market_state"].get("state"))
            )
        )
        if not iv_term or not iv_has_state:
            logic_issues.append({
                "component": "iv_term", "severity": "info",
                "description": "IV 期限结构数据缺失",
                "suggestion": "检查期权链数据获取"
            })
            completeness["iv_term"] = False
            score -= 5
        else:
            completeness["iv_term"] = True

        # Contracts
        contracts = context.get("contracts", [])
        contracts_count = len(contracts) if contracts else 0
        if contracts_count == 0:
            anomalies.append({
                "source": "contracts", "severity": "critical",
                "description": "期权合约列表为空 — 无法进行任何分析",
                "suggestion": "检查交易所 API 和 WebSocket 连接"
            })
            completeness["contracts"] = False
            score -= 20
        elif contracts_count < 5:
            logic_issues.append({
                "component": "contracts", "severity": "warning",
                "description": f"合约数量过少 ({contracts_count} 个)，分析可信度降低",
                "suggestion": "等待更多期权到期日数据就绪"
            })
            completeness["contracts"] = True
            score -= 8
        else:
            completeness["contracts"] = True

        # Large Trades
        large_trades = context.get("large_trades", [])
        trades_count = len(large_trades) if large_trades else 0
        completeness["large_trades"] = trades_count > 0
        if trades_count == 0:
            logic_issues.append({
                "component": "large_trades", "severity": "info",
                "description": "近期无大单记录，资金流向分析可能不完整",
                "suggestion": "检查大单数据源 (Paradigm/Blocktrade)"
            })

        # Data errors from gather
        data_errors = context.get("errors", [])
        if data_errors:
            for err in data_errors:
                anomalies.append({
                    "source": "data_collection", "severity": "warning",
                    "description": f"数据收集错误: {str(err)[:200]}",
                    "suggestion": "检查对应数据源的 API 连接"
                })
                score -= 5

        checks["completeness"] = completeness

        # ================================================================
        # 2. 数值合理性检查
        # ================================================================
        sanity = {}

        # Spot 范围检查 (BTC: 1000~500000, ETH: 50~20000)
        if spot > 0:
            spot_ok = True
            if currency in ("BTC", "XBT"):
                if spot < 1000 or spot > 500000:
                    anomalies.append({
                        "source": "spot_price", "severity": "critical",
                        "description": f"BTC 现货价格异常: ${spot:,.0f}，超出合理范围",
                        "suggestion": "检查价格数据源是否正确"
                    })
                    spot_ok = False
                    score -= 15
            elif spot < 1 or spot > 500000:
                anomalies.append({
                    "source": "spot_price", "severity": "warning",
                    "description": f"现货价格可能异常: ${spot:,.0f}",
                    "suggestion": "核实价格数据源"
                })
                spot_ok = False
                score -= 10
            sanity["spot_range"] = spot_ok

        # DVOL 范围检查 (合理: 20-200)
        if isinstance(dvol, dict) and dvol.get("current"):
            dvol_val = float(dvol["current"])
            if dvol_val <= 0:
                anomalies.append({
                    "source": "dvol", "severity": "critical",
                    "description": f"DVOL 值为 {dvol_val}，不可能为零或负数",
                    "suggestion": "检查 DVOL 计算逻辑"
                })
                sanity["dvol_range"] = False
                score -= 10
            elif dvol_val < 20:
                logic_issues.append({
                    "component": "dvol", "severity": "info",
                    "description": f"DVOL 极低 ({dvol_val:.1f})，市场极度平静",
                    "suggestion": "确认波动率是否被压低或数据有误"
                })
                sanity["dvol_range"] = True
            elif dvol_val > 200:
                anomalies.append({
                    "source": "dvol", "severity": "warning",
                    "description": f"DVOL 极高 ({dvol_val:.1f})，可能有数据异常",
                    "suggestion": "对比其他波动率指标确认"
                })
                sanity["dvol_range"] = True
            else:
                sanity["dvol_range"] = True

        # Onchain MVRV 检查
        if isinstance(onchain, dict) and onchain.get("mvrv") is not None:
            try:
                mvrv = float(onchain["mvrv"])
                if mvrv < -5 or mvrv > 10:
                    logic_issues.append({
                        "component": "mvrv", "severity": "info",
                        "description": f"MVRV 处于极端区间 ({mvrv:.2f})",
                        "suggestion": "核实链上数据是否正确"
                    })
                sanity["mvrv_range"] = True
            except (ValueError, TypeError):
                pass

        # Fear & Greed 检查
        if isinstance(macro, dict):
            fg = macro.get("fear_greed", {})
            if isinstance(fg, dict):
                fg_val = fg.get("value")
                if fg_val is not None:
                    try:
                        fg_num = int(fg_val)
                        if fg_num < 0 or fg_num > 100:
                            anomalies.append({
                                "source": "fear_greed", "severity": "warning",
                                "description": f"恐惧贪婪指数越界: {fg_num} (应在0-100)",
                                "suggestion": "检查 alternative.me API 返回值"
                            })
                            score -= 5
                        sanity["fear_greed_range"] = True
                    except (ValueError, TypeError):
                        pass

        checks["sanity"] = sanity

        # ================================================================
        # 3. 跨源一致性检查
        # ================================================================
        consistency = {}

        # DVOL vs IV Term Structure — 两者应大致同向
        if isinstance(dvol, dict) and isinstance(iv_term, dict):
            dvol_signal = str(dvol.get("signal", "")).lower()
            iv_state = str(iv_term.get("state", "")).lower()

            # DVOL signal: "high"/"elevated" = high vol; "low"/"suppressed" = low vol
            # IV state: "contango"/"backwardation" check
            if dvol_signal and iv_state:
                dvol_high = any(w in dvol_signal for w in ("high", "elevated", "extreme"))
                dvol_low = any(w in dvol_signal for w in ("low", "suppressed"))
                iv_high = any(w in iv_state for w in ("backwardation", "elevated", "high"))
                iv_low = any(w in iv_state for w in ("contango", "suppressed", "low"))

                if dvol_high and iv_low:
                    anomalies.append({
                        "source": "dvol_vs_iv", "severity": "warning",
                        "description": f"DVOL 信号 ({dvol_signal}) 与 IV 期限结构 ({iv_state}) 矛盾",
                        "suggestion": "两者数据源可能存在计算偏差，需人工确认"
                    })
                    score -= 10
                    consistency["dvol_iv_align"] = False
                elif dvol_low and iv_high:
                    anomalies.append({
                        "source": "dvol_vs_iv", "severity": "warning",
                        "description": f"DVOL 信号 ({dvol_signal}) 与 IV 期限结构 ({iv_state}) 矛盾",
                        "suggestion": "检查波动率数据源时间窗口是否一致"
                    })
                    score -= 10
                    consistency["dvol_iv_align"] = False
                else:
                    consistency["dvol_iv_align"] = True

        # Onchain vs Derivatives 方向一致性
        if isinstance(onchain, dict) and isinstance(derivatives, dict):
            onchain_score = onchain.get("convergence_score")
            overheating = derivatives.get("overheating")

            if onchain_score is not None and overheating is not None:
                try:
                    oc = float(onchain_score)
                    oh = float(overheating)
                    # 如果 onchain 极度看涨但衍生品显示过热 → 矛盾
                    if oc > 0.7 and oh > 0.8:
                        logic_issues.append({
                            "component": "onchain_vs_derivatives", "severity": "warning",
                            "description": f"链上指标极度看涨 (score={oc:.2f}) 但衍生品显示过热 (overheat={oh:.2f})",
                            "suggestion": "市场可能接近顶部，注意风险"
                        })
                    consistency["onchain_deriv_align"] = False if (oc > 0.7 and oh > 0.8) else True
                except (ValueError, TypeError):
                    pass

        checks["consistency"] = consistency

        # ================================================================
        # 4. 数据新鲜度检查
        # ================================================================
        freshness = {}
        import time as _time
        now_ts = _time.time()

        # 检查合约中最近到期日的更新（间接判断数据新鲜度）
        if contracts:
            has_recent = False
            for c in contracts:
                if isinstance(c, dict):
                    ts = c.get("timestamp") or c.get("updated_at") or c.get("last_updated")
                    if ts:
                        try:
                            if isinstance(ts, str):
                                ts_parsed = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                                if ts_parsed.tzinfo is None:
                                    ts_parsed = ts_parsed.replace(tzinfo=timezone.utc)
                                ts_unix = ts_parsed.timestamp()
                            else:
                                ts_unix = float(ts)
                            if now_ts - ts_unix < 3600:  # 1 小时内
                                has_recent = True
                                break
                        except (ValueError, TypeError):
                            pass
            if not has_recent and len(contracts) > 5:
                logic_issues.append({
                    "component": "data_freshness", "severity": "warning",
                    "description": "合约数据可能超过1小时未更新",
                    "suggestion": "检查 WebSocket 连接和定时刷新任务"
                })
                freshness["contracts_recent"] = False
                score -= 5
            else:
                freshness["contracts_recent"] = True

        checks["freshness"] = freshness

        # ================================================================
        # 5. 计算最终评分
        # ================================================================
        score = max(0, min(100, score))

        # 评分修正：即使部分数据缺失，只要有核心数据就给基础分
        core_ok = completeness.get("spot") and completeness.get("contracts")
        if not core_ok:
            score = min(score, 30)  # 核心数据缺失，最多30分
        elif all(completeness.values()):
            score = min(score + 5, 100)  # 全数据 +5 奖励分

        return {
            "success": True,
            "anomalies": anomalies,
            "logic_issues": logic_issues,
            "data_quality_score": score,
            "checks_detail": {k: v for k, v in checks.items() if v},
            "audit_method": "deterministic",
        }

    def _llm_audit(self, context: Dict, rule_reports: Dict, synthesis: Dict) -> Dict[str, Any]:
        """双层数据质量审计 — 确定性检查 + 可选 LLM 深度分析

        Layer 1 (确定性): 程序化检查，绝不失败，作为基线结果
        Layer 2 (LLM 可选): 尝试 LLM 深度分析，失败时回退到 Layer 1 结果
        """
        # === Layer 1: 确定性审计（始终执行，绝不失败）===
        det_result = self._deterministic_audit(context, rule_reports)

        # === Layer 2: LLM 深度分析（可选，失败时静默回退）===
        system_prompt = """你是数据质量审计师。审查以下加密货币期权分析数据，找出异常。

检查维度：
1. 数据源间一致性：DVOL vs IV、链上信号 vs 衍生品信号、价格 vs 成交量
2. 计算逻辑合理性：APR 是否异常（>200%?）、胜率是否合理（>95%?）、spread 是否正常
3. 数据完整性：是否有缺失字段、数据是否过期
4. 前端展示一致性：策略引擎输出 vs 原始数据是否匹配

输出纯 JSON（不要包含任何其他文字）：
{
  "anomalies": [...],
  "logic_issues": [...],
  "data_quality_score": 0-100
}"""

        data_errors = context.get("errors", [])
        contracts_count = len(context.get("contracts", []))
        raw_summary = {
            "currency": context["currency"],
            "spot": context.get("spot", 0),
            "dvol": context.get("dvol", {}).get("current", 0) if isinstance(context.get("dvol"), dict) else context.get("dvol", 0),
            "data_errors": data_errors,
            "contracts_count": contracts_count,
            "large_trades_count": len(context.get("large_trades", [])),
            "max_pain": context.get("max_pain", 0),
        }

        rules_summary = {}
        # 处理 agent 报告列表 (reports)
        reports_list = rule_reports.get("reports", []) if isinstance(rule_reports, dict) else []
        for r in (reports_list if isinstance(reports_list, list) else []):
            if isinstance(r, dict):
                rules_summary[r.get("name", "unknown")] = {
                    "verdict": r.get("verdict", ""),
                    "score": r.get("score", 0),
                    "confidence": r.get("confidence", 0),
                    "factors_count": len(r.get("key_points", [])),
                    "key_points": r.get("key_points", []),
                }
        # 处理 synthesis
        syn = rule_reports.get("synthesis", {}) if isinstance(rule_reports, dict) else {}
        if isinstance(syn, dict):
            rules_summary["synthesis"] = {
                "verdict": syn.get("recommendation_label", syn.get("recommendation", "")),
                "score": syn.get("overall_score", 0),
                "entry_suggestions_count": len(syn.get("entry_suggestions", [])),
                "consensus": syn.get("consensus", ""),
                "conflict_range": syn.get("conflict_range", 0),
            }
        # 处理 market_summary
        ms = rule_reports.get("market_summary", {}) if isinstance(rule_reports, dict) else {}
        if isinstance(ms, dict):
            rules_summary["market_summary"] = {
                "verdict": ms.get("risk_status", ""),
                "dvol": ms.get("dvol", 0),
                "contracts_count": ms.get("contracts_count", 0),
                "spot": ms.get("spot", 0),
                "large_trades_count": ms.get("large_trades_count", 0),
            }

        user_prompt = f"""=== 数据摘要 ===
{json.dumps(raw_summary, ensure_ascii=False, indent=2, default=str)}

=== 数据质量问题 ===
{json.dumps(data_errors, ensure_ascii=False, indent=2, default=str)}

=== 确定性审计结果（参考，不要重复其发现）===
{json.dumps({"anomalies": det_result["anomalies"], "logic_issues": det_result["logic_issues"], "score": det_result["data_quality_score"]}, ensure_ascii=False, indent=2, default=str)}

=== 规则引擎摘要 ===
{json.dumps(rules_summary, ensure_ascii=False, indent=2, default=str)}

=== LLM 综合研判 ===
{json.dumps(synthesis, ensure_ascii=False, indent=2, default=str) if synthesis else "无"}

请在确定性审计基础上补充深度洞察。重点关注确定性审计未覆盖的逻辑问题和 LLM 特有洞察。仅返回 JSON。"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        custom_config = self._get_custom_config()

        llm_result = {"success": False}
        try:
            response = ai_chat_with_config(
                messages, preset="analysis", temperature=0.2,
                max_tokens=4000, custom_config=custom_config
            )
            if response:
                parsed = self._parse_json_response(response)
                if parsed is not None:
                    parsed.setdefault("anomalies", [])
                    parsed.setdefault("logic_issues", [])
                    parsed.setdefault("data_quality_score", 0)
                    if not isinstance(parsed.get("anomalies"), list):
                        parsed["anomalies"] = []
                    if not isinstance(parsed.get("logic_issues"), list):
                        parsed["logic_issues"] = []
                    if not isinstance(parsed.get("data_quality_score"), (int, float)):
                        parsed["data_quality_score"] = 0
                    parsed["success"] = True
                    llm_result = parsed
                else:
                    logger.warning("LLM audit parse failed, response: %s", response[:200])
            else:
                logger.warning("LLM audit returned empty response")
        except Exception as e:
            logger.warning("LLM audit call failed: %s", e)

        # === 合并结果：确定性审计为基线，LLM 结果为补充 ===
        merged_anomalies = list(det_result["anomalies"])
        merged_issues = list(det_result["logic_issues"])

        if llm_result.get("success"):
            # LLM 成功：去重后合并
            det_descs = {a.get("description", "") for a in merged_anomalies}
            det_issue_descs = {i.get("description", "") for i in merged_issues}

            for a in llm_result.get("anomalies", []):
                if isinstance(a, dict) and a.get("description", "") not in det_descs:
                    a.setdefault("severity", "info")
                    a.setdefault("source", "llm_insight")
                    merged_anomalies.append(a)

            for i in llm_result.get("logic_issues", []):
                if isinstance(i, dict) and i.get("description", "") not in det_issue_descs:
                    i.setdefault("severity", "info")
                    i.setdefault("component", "llm_insight")
                    merged_issues.append(i)

            # 加权评分：确定性 70% + LLM 30%
            final_score = round(det_result["data_quality_score"] * 0.7 +
                               llm_result["data_quality_score"] * 0.3)
            audit_method = "deterministic + llm"
        else:
            final_score = det_result["data_quality_score"]
            audit_method = "deterministic (LLM 不可用)"

        return {
            "success": True,
            "anomalies": merged_anomalies,
            "logic_issues": merged_issues,
            "data_quality_score": final_score,
            "checks_detail": det_result.get("checks_detail", {}),
            "audit_method": audit_method,
        }
