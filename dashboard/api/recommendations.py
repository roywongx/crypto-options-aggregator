"""统一推荐 API 路由"""
import json
import hashlib
import logging
import time
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from db.connection import execute_read
from services.unified_recommendation_engine import UnifiedRecommendationEngine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["recommendations"])

# 全量数据缓存（避免每次请求都打 Deribit API）
_full_data_cache: dict = {}  # {currency: (timestamp, contracts_list)}

# _collect_panel_data 短期缓存（页面加载时 summary + batch 会同时调用）
_panel_data_cache: dict = {}  # {currency: (timestamp, data_dict)}
_PANEL_DATA_CACHE_TTL = 10  # 10秒，覆盖同一页面加载的多次调用


class BatchRequest(BaseModel):
    panels: list[str] = Field(default_factory=list)
    currency: str = Field(default="BTC")


class LLMAnalysisRequest(BaseModel):
    currency: str = Field(default="BTC")
    force_refresh: bool = Field(default=False)


# ============================================================
# 单例引擎
# ============================================================

_engine: UnifiedRecommendationEngine | None = None


def _get_engine() -> UnifiedRecommendationEngine:
    global _engine
    if _engine is None:
        _engine = UnifiedRecommendationEngine()
    return _engine


def _fetch_full_contracts(currency: str, spot: float) -> list:
    """从 Deribit 获取全量分析合约（带 60s 缓存）"""
    now = time.time()
    cached = _full_data_cache.get(currency)
    if cached and (now - cached[0]) < 60:
        return cached[1]

    try:
        from services.trades import fetch_deribit_summaries
        from services.instrument import _parse_inst_name
        from services.dvol_analyzer import calc_delta_bs
        from services.shared_calculations import black_scholes_price

        raw_summaries = fetch_deribit_summaries(currency)
        if not raw_summaries:
            return []

        contracts = []
        for s in raw_summaries:
            meta = _parse_inst_name(s.get("instrument_name", ""))
            if not meta or meta.dte < 1:
                continue
            iv = float(s.get("mark_iv") or 0)
            oi = float(s.get("open_interest") or 0)
            if iv <= 0 or oi < 1:
                continue
            strike = meta.strike
            underlying = float(s.get("underlying_price", spot)) or spot
            raw_delta = s.get("delta")
            delta_val = abs(float(raw_delta)) if raw_delta and float(raw_delta or 0) != 0 else abs(calc_delta_bs(strike, underlying, iv, meta.dte, meta.option_type))
            prem = float(s.get("mark_price") or 0)
            prem_usd = prem * underlying
            cv = strike * 0.2
            apr = (prem_usd / cv) * (365 / meta.dte) * 100 if cv > 0 else 0
            bs_greeks = black_scholes_price(meta.option_type, strike, underlying, meta.dte, iv)
            dist = abs(strike - spot) / spot * 100 if spot > 0 else 0
            contracts.append({
                "symbol": s.get("instrument_name", ""),
                "platform": "Deribit",
                "expiry": meta.expiry,
                "dte": meta.dte,
                "option_type": meta.option_type,
                "strike": strike,
                "apr": round(apr, 1),
                "premium_usd": round(prem_usd, 2),
                "premium": round(prem, 2),
                "delta": round(delta_val, 3),
                "theta": round(bs_greeks["theta"], 4),
                "gamma": round(bs_greeks["gamma"], 6),
                "vega": round(bs_greeks["vega"], 4),
                "iv": round(iv, 1),
                "open_interest": round(oi, 0),
                "distance_spot_pct": round(dist, 1),
            })

        _full_data_cache[currency] = (now, contracts)
        logger.debug("Full contracts for %s: %d (cached 60s)", currency, len(contracts))
        return contracts
    except Exception as e:
        logger.warning("Full contracts fetch failed: %s", e)
        return []


# ============================================================
# 数据获取辅助函数
# ============================================================

def _collect_panel_data(currency: str = "BTC") -> dict:
    """并行收集所有面板数据 (ThreadPoolExecutor, 8 workers, 30s timeout)

    带 10 秒短期缓存：页面加载时 summary + batch 端点同时调用，
    第二次调用直接从缓存返回，避免重复的 Deribit API 请求。
    """
    # 短期缓存命中 → 直接返回
    now = time.time()
    cached = _panel_data_cache.get(currency)
    if cached and (now - cached[0]) < _PANEL_DATA_CACHE_TTL:
        logger.debug("Panel data cache hit for %s (age=%.1fs)", currency, now - cached[0])
        return cached[1]

    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
    data: dict = {"spot": 0, "dvol": 0, "dvol_z": 0, "currency": currency}

    results = {}

    def _fetch_spot():
        try:
            from services.spot_price import get_spot_price
            return ("spot", get_spot_price(currency))
        except Exception as e:
            logger.warning("spot fetch failed: %s", e)
            return ("spot", 0)

    def _fetch_dvol():
        try:
            from services.dvol_analyzer import get_dvol_from_deribit
            dvol = get_dvol_from_deribit(currency)
            if dvol:
                return ("dvol_data", {
                    "dvol": dvol.get("current_dvol", 0) or 0,
                    "dvol_z": dvol.get("z_score", 0) or 0,
                    "dvol_signal": dvol.get("signal", "normal"),
                })
            return ("dvol_data", {"dvol": 0, "dvol_z": 0, "dvol_signal": "normal"})
        except Exception as e:
            logger.warning("dvol fetch failed: %s", e)
            return ("dvol_data", {"dvol": 0, "dvol_z": 0, "dvol_signal": "normal"})

    def _fetch_fear_greed():
        try:
            from services.macro_data import get_fear_greed_index
            fg_result = get_fear_greed_index()
            if fg_result:
                return ("fear_greed", fg_result.get("value", 50) or 50)
            return ("fear_greed", 50)
        except Exception as e:
            logger.warning("fear_greed fetch failed: %s", e)
            return ("fear_greed", 50)

    def _fetch_max_pain():
        try:
            from services.max_pain import get_max_pain
            mp = get_max_pain(currency, auto_calc=True)
            return ("max_pain", mp if mp > 0 else 0)
        except Exception as e:
            logger.warning("max_pain fetch failed: %s", e)
            return ("max_pain", 0)

    def _fetch_large_trades():
        try:
            trades_rows = execute_read(
                "SELECT direction, volume, notional_usd, strike, option_type FROM large_trades_history "
                "WHERE currency=? ORDER BY timestamp DESC LIMIT 50",
                (currency,),
            )
            if trades_rows:
                trades = [dict(r) for r in trades_rows]
                put_count = sum(1 for t in trades if str(t.get("option_type", "")).upper() in ("PUT", "P"))
                call_count = sum(1 for t in trades if str(t.get("option_type", "")).upper() in ("CALL", "C"))
                pcr = round(put_count / call_count, 2) if call_count > 0 else 1.0
                return ("trades_data", {"large_trades": trades, "pcr": pcr})
            return ("trades_data", {"large_trades": [], "pcr": 1.0})
        except Exception as e:
            logger.warning("large_trades fetch failed: %s", e)
            return ("trades_data", {"large_trades": [], "pcr": 1.0})

    def _fetch_derivatives():
        try:
            from services.derivative_metrics import DerivativeMetrics
            deriv = DerivativeMetrics.get_all_metrics(currency)
            return ("derivatives", deriv)
        except Exception as e:
            logger.warning("Derivative metrics fetch failed: %s", e)
            return ("derivatives", {})

    # Phase 1 — parallel independent fetches
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_fetch_spot): "spot",
            executor.submit(_fetch_dvol): "dvol",
            executor.submit(_fetch_fear_greed): "fear_greed",
            executor.submit(_fetch_max_pain): "max_pain",
            executor.submit(_fetch_large_trades): "trades",
            executor.submit(_fetch_derivatives): "derivatives",
        }
        for future in as_completed(futures, timeout=30):
            try:
                key, value = future.result(timeout=25)
                results[key] = value
            except (FuturesTimeoutError, RuntimeError) as e:
                logger.warning("Panel data %s timed out: %s", futures[future], e)

    # Unpack phase 1 results
    data["spot"] = results.get("spot", 0)
    if "dvol_data" in results:
        data["dvol"] = results["dvol_data"]["dvol"]
        data["dvol_z"] = results["dvol_data"]["dvol_z"]
        data["dvol_signal"] = results["dvol_data"]["dvol_signal"]
    data["fear_greed"] = results.get("fear_greed", 50)
    data["max_pain"] = results.get("max_pain", 0)
    if "trades_data" in results:
        data["large_trades"] = results["trades_data"]["large_trades"]
        data["pcr"] = results["trades_data"]["pcr"]

    # Phase 2 — contracts (depends on spot from phase 1)
    spot_val = data.get("spot", 0)
    full_contracts = _fetch_full_contracts(currency, spot_val)
    if full_contracts:
        data["contracts"] = full_contracts
    else:
        try:
            contract_rows = execute_read(
                "SELECT contracts_data, timestamp FROM scan_records "
                "WHERE currency=? ORDER BY timestamp DESC LIMIT 1",
                (currency,),
            )
            if contract_rows:
                row = dict(contract_rows[0])
                contracts_json = row["contracts_data"] or "[]"
                data["contracts"] = json.loads(contracts_json) if isinstance(contracts_json, str) else (contracts_json or [])
            else:
                data["contracts"] = []
        except Exception as e:
            logger.warning("contracts fetch failed: %s", e)
            data["contracts"] = []

    # Phase 3 — IV Smile (depends on contracts + spot)
    if data.get("contracts") and data.get("spot"):
        try:
            from services.iv_smile import IVSmileAnalyzer
            smile_result = IVSmileAnalyzer.analyze(data["contracts"], data["spot"], currency)
            if smile_result.get("analysis"):
                metrics = smile_result["analysis"].get("metrics", {})
                data["skew"] = round(metrics.get("skew_25d", 0), 2)
                data["kurtosis"] = round(metrics.get("curvature", 0), 2)
                data["put_skew_pct"] = round(metrics.get("put_skew_pct", 0), 2)
                data["call_skew_pct"] = round(metrics.get("call_skew_pct", 0), 2)
                data["atm_iv"] = round(metrics.get("atm_iv", 0), 2)
                data["smile_form"] = smile_result["analysis"].get("form", "unknown")
                data["smile_sentiment"] = smile_result["analysis"].get("sentiment", {}).get("label", "")
        except Exception as e:
            logger.debug("IV Smile analysis failed: %s", e)

    # Phase 4 — Unpack derivatives
    deriv = results.get("derivatives", {})
    data["perp_basis"] = deriv.get("perp_basis", {})
    data["oi_price_divergence"] = deriv.get("oi_price_divergence", {})
    data["funding_volatility"] = deriv.get("funding_volatility", {})
    data["liquidation_heat"] = deriv.get("liquidation_heat", {})
    data["stablecoin_reserve"] = deriv.get("stablecoin_reserve", {})
    data["futures_spot_ratio"] = deriv.get("futures_spot_ratio", {})

    # 展开嵌套字典为 LLM 模板占位符（使用新键名，不覆盖原始 dict）
    if isinstance(data.get("perp_basis"), dict):
        data["basis_annualized"] = data["perp_basis"].get("basis_annualized", 0)
        data["perp_price"] = data["perp_basis"].get("perp_price", 0)
    if isinstance(data.get("oi_price_divergence"), dict):
        data["oi_divergence"] = data["oi_price_divergence"].get("divergence_label", "无数据")
    if isinstance(data.get("funding_volatility"), dict):
        data["funding_vol_7d"] = data["funding_volatility"].get("volatility_7d_pct", 0)
    if isinstance(data.get("liquidation_heat"), dict):
        data["liquidation_total_usd"] = data["liquidation_heat"].get("total_liquidation_1h_usd", 0)
    if isinstance(data.get("futures_spot_ratio"), dict):
        data["futures_spot_ratio_val"] = data["futures_spot_ratio"].get("ratio", 0)
    if isinstance(data.get("stablecoin_reserve"), dict):
        data["stablecoin_flow"] = data["stablecoin_reserve"].get("label", "未知")

    # 写入短期缓存（覆盖同一页面加载的多次调用）
    _panel_data_cache[currency] = (time.time(), data)
    return data


# ============================================================
# API 端点
# ============================================================

@router.get("/recommendation/{panel_id}")
async def get_panel_recommendation(panel_id: str, currency: str = Query(default="BTC")):
    """获取单个面板的规则推荐（信号灯 + 规则报告）"""
    engine = _get_engine()
    if panel_id not in engine.panels:
        raise HTTPException(status_code=404, detail=f"Unknown panel: {panel_id}")

    data = _collect_panel_data(currency)
    try:
        result = engine.analyze(panel_id, data, currency)
        return result
    except Exception as e:
        logger.error("Recommendation for %s failed: %s", panel_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recommendation/{panel_id}/llm")
async def trigger_llm_analysis(panel_id: str, body: LLMAnalysisRequest):
    """触发 LLM 深度分析，返回 SSE 流（DeepSeek 思考模式 + DB 缓存）"""
    engine = _get_engine()
    if panel_id not in engine.panels:
        raise HTTPException(status_code=404, detail=f"Unknown panel: {panel_id}")

    data = _collect_panel_data(body.currency)
    rule_result = engine.analyze(panel_id, data, body.currency)

    from services.unified_recommendation_engine import LLMPromptBuilder
    prompt = LLMPromptBuilder.build(panel_id, rule_result["report"], data, body.currency)

    # 计算输入哈希，用于缓存查找
    prompt_json = json.dumps(prompt, sort_keys=True, ensure_ascii=False)
    input_hash = hashlib.sha256(prompt_json.encode()).hexdigest()[:16]

    from services.ai_router import ai_chat_with_config
    from db.connection import execute_write

    section_config = {
        "synthesis":     {"label_cn": "合成分析", "preset": "analysis"},
        "bull_context":  {"label_cn": "多头辩论", "preset": "debate"},
        "bear_context":  {"label_cn": "空头辩论", "preset": "debate"},
        "judge_criteria":{"label_cn": "最终判決", "preset": "audit"},
    }

    # 加载已保存的 LLM 配置
    try:
        llm_cfg_rows = execute_read(
            "SELECT api_key, base_url, model FROM llm_config WHERE id=1"
        )
        if llm_cfg_rows and llm_cfg_rows[0]:
            llm_api_key = llm_cfg_rows[0]["api_key"] or ""
            llm_base_url = llm_cfg_rows[0]["base_url"] or ""
            llm_model = llm_cfg_rows[0]["model"] or ""
        else:
            llm_api_key = llm_base_url = llm_model = ""
    except Exception as e:
        logger.debug("LLM config load failed: %s", e)
        llm_api_key = llm_base_url = llm_model = ""

    async def event_stream():
        import asyncio
        yield f"data: {json.dumps({'type': 'start', 'panel_id': panel_id, 'currency': body.currency})}\n\n"

        # 尝试从缓存读取
        cached = None
        if not body.force_refresh:
            try:
                cached_rows = execute_read(
                    "SELECT analysis_json FROM llm_analysis_cache WHERE panel_id=? AND currency=? AND input_hash=?",
                    (panel_id, body.currency, input_hash)
                )
                if cached_rows:
                    cached = json.loads(cached_rows[0]["analysis_json"])
                    logger.info("LLM cache hit for %s/%s", panel_id, body.currency)
            except Exception as e:
                logger.debug("LLM cache lookup failed: %s", e)

        analysis_sections = {}

        for section, config in section_config.items():
            content = prompt.get(section, "")
            if not content:
                continue

            # 使用缓存结果
            if cached and section in cached:
                cached_content = cached[section]
                yield f"data: {json.dumps({'type': 'step', 'label': section, 'label_cn': config['label_cn'], 'content': cached_content})}\n\n"
                continue

            yield f"data: {json.dumps({'type': 'step', 'label': section, 'label_cn': config['label_cn'], 'content': '正在思考...'})}\n\n"

            try:
                result = await asyncio.to_thread(
                    ai_chat_with_config,
                    messages=[
                        {"role": "system", "content": "你是一位专业的加密货币期权分析师。请用中文提供深度分析，使用 Markdown 格式。"},
                        {"role": "user", "content": content},
                    ],
                    preset=config["preset"],
                    max_tokens=3000 if section in ("bull_context", "bear_context") else 4000,
                    custom_config={"api_key": llm_api_key, "base_url": llm_base_url, "model": llm_model},
                )

                if result:
                    analysis_sections[section] = result
                    yield f"data: {json.dumps({'type': 'step', 'label': section, 'label_cn': config['label_cn'], 'content': result})}\n\n"
                else:
                    fallback = f'[LLM 服务暂不可用] 参考分析框架:\n\n{content[:500]}'
                    yield f"data: {json.dumps({'type': 'step', 'label': section, 'label_cn': config['label_cn'], 'content': fallback})}\n\n"
            except Exception as e:
                logger.warning("LLM call for %s/%s failed: %s", panel_id, section, e)
                yield f"data: {json.dumps({'type': 'step', 'label': section, 'label_cn': config['label_cn'], 'content': f'[分析失败: {str(e)}]'})}\n\n"

        # 写入缓存（有新结果时）
        if analysis_sections:
            try:
                execute_write(
                    """INSERT OR REPLACE INTO llm_analysis_cache (panel_id, currency, input_hash, analysis_json, model_used)
                       VALUES (?, ?, ?, ?, ?)""",
                    (panel_id, body.currency, input_hash, json.dumps(analysis_sections, ensure_ascii=False), "deepseek-v4-pro")
                )
            except Exception as e:
                logger.debug("LLM cache write failed: %s", e)

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/recommendations/summary")
async def get_recommendations_summary(currency: str = Query(default="BTC")):
    """全板块信号汇总（顶部条用）"""
    engine = _get_engine()
    data = _collect_panel_data(currency)
    results = engine.analyze_all(data, currency)

    summary = {}
    for panel_id, result in results.items():
        signal = result.get("signal", {})
        summary[panel_id] = {
            "name": engine.panels.get(panel_id, {}).get("name", panel_id),
            "signal": signal.get("signal", "neutral"),
            "signal_emoji": signal.get("signal_emoji", "⚪"),
            "signal_text": signal.get("signal_text", ""),
            "confidence": signal.get("confidence", 0),
        }
    return {"summary": summary, "timestamp": list(results.values())[0]["timestamp"] if results else None}


@router.post("/recommendations/batch")
async def batch_recommendations(body: BatchRequest):
    """批量获取多个面板的规则推荐"""
    engine = _get_engine()
    data = _collect_panel_data(body.currency)

    results = {}
    for panel_id in body.panels:
        if panel_id not in engine.panels:
            results[panel_id] = {"error": f"Unknown panel: {panel_id}"}
            continue
        try:
            results[panel_id] = engine.analyze(panel_id, data, body.currency)
        except Exception as e:
            logger.error("Batch recommendation for %s failed: %s", panel_id, e)
            results[panel_id] = {"error": str(e)}

    return {"results": results}
