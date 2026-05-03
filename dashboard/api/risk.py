"""风险评估 API"""
import logging
from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["risk"])


def _calc_max_pain_sync(currency: str = "BTC"):
    """同步版本的最大痛点计算（委托给 routers.maxpain 统一实现）"""
    import asyncio
    from routers.maxpain import _calc_max_pain_internal
    return asyncio.run(_calc_max_pain_internal(currency))


def get_risk_overview_sync(currency: str = "BTC"):
    """同步版本的风险评估（供其他模块调用）"""
    from services.risk_framework import RiskFramework
    from services.spot_price import get_spot_price
    from services.unified_risk_assessor import UnifiedRiskAssessor
    from services.onchain_metrics import OnChainMetrics
    from services.derivative_metrics import DerivativeMetrics
    from services.pressure_test import PressureTestEngine
    from services.ai_sentiment import AISentimentAnalyzer
    from db.connection import execute_read
    from datetime import datetime, timezone, timedelta

    spot = get_spot_price(currency)
    status = RiskFramework.get_status(spot)
    floors = RiskFramework._get_floors()

    assessor = UnifiedRiskAssessor()
    risk_data = assessor.assess_comprehensive_risk(spot, currency)

    # 获取最大痛点数据（使用同步版本）
    put_wall = None
    gamma_flip = None
    nearest_mp = None
    try:
        pain_data = _calc_max_pain_sync(currency)
        if pain_data and not pain_data.get("error"):
            expiries = pain_data.get("expiries", [])
            nearest_mp = pain_data.get("nearest_mp")
            
            for exp in expiries:
                # 提取 Put Wall（Put OI 最大的行权价）
                gex_curve = exp.get("gex_curve", [])
                max_put_oi = 0
                max_put_oi_strike = None
                for g in gex_curve:
                    put_oi_at = g.get("put_oi_at_strike", 0)
                    if put_oi_at > max_put_oi:
                        max_put_oi = put_oi_at
                        max_put_oi_strike = g.get("strike")
                if max_put_oi_strike:
                    put_wall = {
                        "strike": max_put_oi_strike,
                        "oi": max_put_oi,
                        "expiry": exp.get("expiry"),
                        "dte": exp.get("dte")
                    }
                
                # 提取 Gamma Flip
                flip = exp.get("flip_point")
                if flip:
                    gamma_flip = {
                        "strike": flip,
                        "expiry": exp.get("expiry"),
                        "dte": exp.get("dte")
                    }
                    break
    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
        logger.warning("获取最大痛点数据失败: %s", e)

    advice = []
    actions = []

    if put_wall and spot < put_wall["strike"]:
        advice.append(f"🛡️ Put Wall防线: ${put_wall['strike']:,.0f} (OI={put_wall['oi']:,.0f}) — 机构在此布防")
    if gamma_flip:
        if spot > gamma_flip["strike"]:
            advice.append(f"✅ Gamma Flip ${gamma_flip['strike']:,.0f} — 价格在多头Gamma区，波动受抑")
        else:
            advice.append(f"⚠️ Gamma Flip ${gamma_flip['strike']:,.0f} — 价格在空头Gamma区，波动放大")

    if status == "NORMAL":
        advice.append(f"当前价格 ${spot:,.0f} 处于常规区间。")
        advice.append("建议：以获取稳定 APR 为目标，保持低杠杆。")
        actions.append("卖出 OTM Put (Delta 0.15-0.25)")
    elif status == "NEAR_FLOOR":
        advice.append(f"当前价格 ${spot:,.0f} 接近常规底 ${floors['regular']:,.0f}。")
        advice.append("建议：可适当增加仓位，博取高 Theta 收益。")
        actions.append("卖出 ATM/ITM Put 并准备滚仓")
    elif status == "ADVERSE":
        advice.append(f"市场处于逆境区 (${spot:,.0f} < ${floors['regular']:,.0f})。")
        advice.append("建议：启用后备资金，高杠杆快平仓，积极执行 Rolling Down & Out。")
        actions.append("将持仓滚动至支撑区间")
    elif status == "PANIC":
        advice.append(f"⚠️ 警告：价格已破极限底 ${floors['extreme']:,.0f}！")
        advice.append("核心指令：止损并保留本金。不要在此区域接货。")
        actions.append("平掉所有 Put 仓位，保持现金")

    position_guidance = {
        "NORMAL": {"max_position_pct": 30, "suggested_delta_range": "0.15-0.25", "suggested_dte": "14-35"},
        "NEAR_FLOOR": {"max_position_pct": 40, "suggested_delta_range": "0.20-0.35", "suggested_dte": "7-28"},
        "ADVERSE": {"max_position_pct": 15, "suggested_delta_range": "0.10-0.20", "suggested_dte": "14-45"},
        "PANIC": {"max_position_pct": 0, "suggested_delta_range": "N/A", "suggested_dte": "N/A"}
    }
    pos_guide = position_guidance.get(status, position_guidance["NORMAL"])

    # 获取链上指标数据
    try:
        onchain_data = OnChainMetrics.get_all_metrics(currency)
    except (RuntimeError, ConnectionError, TimeoutError) as e:
        logger.warning("OnChain metrics failed: %s", e)
        onchain_data = {"error": "获取链上指标失败"}

    # 获取衍生品市场数据
    try:
        derivative_data = DerivativeMetrics.get_all_metrics()
    except (RuntimeError, ConnectionError, TimeoutError) as e:
        logger.warning("Derivative metrics failed: %s", e)
        derivative_data = {"error": "获取衍生品数据失败"}

    # 获取 DVOL 数据用于压力测试
    dvol_data = {}
    try:
        from services.dvol_analyzer import get_dvol_from_deribit
        dvol_data = get_dvol_from_deribit(currency)
    except (RuntimeError, ConnectionError, TimeoutError) as e:
        logger.warning("获取 DVOL 数据失败: %s", e)

    # 获取压力测试数据
    try:
        sigma = dvol_data.get("current", 50) / 100 if isinstance(dvol_data, dict) and not dvol_data.get("error") else 0.50
        pressure_test_data = PressureTestEngine.stress_test(
            S=spot, K=spot, T=30/365, r=0.05, sigma=sigma, option_type="P"
        )
    except (ValueError, TypeError, RuntimeError) as e:
        logger.warning("Pressure test failed: %s", e)
        pressure_test_data = {"error": "获取压力测试数据失败"}

    # 获取 AI 情绪分析数据
    try:
        since = datetime.now(timezone.utc) - timedelta(days=7)
        since_str = since.strftime('%Y-%m-%d %H:%M:%S')
        rows = execute_read("""
            SELECT direction, option_type, strike, volume, delta, notional_usd, timestamp
            FROM large_trades_history
            WHERE currency = ? AND timestamp >= ?
            ORDER BY timestamp DESC LIMIT 100
        """, (currency, since_str))
        
        trades = []
        for row in rows:
            trades.append({
                "direction": row[0],
                "option_type": row[1],
                "strike": row[2],
                "volume": row[3],
                "delta": row[4],
                "notional_usd": row[5],
                "timestamp": row[6]
            })
        
        ai_sentiment_data = AISentimentAnalyzer.analyze_market_sentiment(trades, spot)
    except (RuntimeError, ValueError, TypeError) as e:
        logger.warning("AI sentiment analysis failed: %s", e)
        ai_sentiment_data = {"error": "获取AI情绪分析失败"}

    return {
        "currency": currency,
        "spot": spot,
        "status": status,
        "composite_score": risk_data["composite_score"],
        "risk_level": risk_data["risk_level"],
        "components": risk_data["components"],
        "recommendations": risk_data["recommendations"],
        "floors": floors,
        "advice": advice,
        "recommended_actions": actions,
        "position_guidance": pos_guide,
        "timestamp": risk_data["timestamp"],
        "put_wall": put_wall,
        "gamma_flip": gamma_flip,
        "max_pain": nearest_mp,
        "onchain_metrics": onchain_data,
        "derivative_metrics": derivative_data,
        "pressure_test": pressure_test_data,
        "ai_sentiment": ai_sentiment_data
    }


@router.get("/risk/llm-insight")
async def get_llm_risk_insight(currency: str = Query(default="BTC")):
    """LLM 智能研判端点 — 基于全量风险数据生成分析报告"""
    try:
        from services.llm_analyst import LLMAnalystEngine
        from services.ai_router import ai_chat_with_config
        import json as _json

        risk_data = await run_in_threadpool(get_risk_overview_sync, currency.upper())

        prompt = f"""你是加密货币风险分析师。基于以下风险数据，给出分析。

数据：
{_json.dumps(risk_data, ensure_ascii=False, indent=2)}

输出 JSON：
{{
  "narrative": "200字以内的风险总评",
  "anomalies": ["异常1", "异常2"],
  "recommendations": ["建议1", "建议2"],
  "confidence": 0-100
}}"""

        custom_config = LLMAnalystEngine()._get_custom_config()
        response = ai_chat_with_config(
            [{"role": "user", "content": prompt}],
            preset="analysis", temperature=0.3, max_tokens=1500,
            custom_config=custom_config
        )

        if not response:
            return {"narrative": "LLM 服务未配置或无响应，请检查 LLM 配置。", "anomalies": [], "recommendations": [], "confidence": 0}

        parsed = LLMAnalystEngine()._parse_json_response(response)
        return parsed or {"narrative": response, "anomalies": [], "recommendations": [], "confidence": 50}

    except Exception as e:
        logger.warning("LLM insight failed: %s", e)
        return {"narrative": f"LLM 服务不可用: {e}", "anomalies": [], "recommendations": [], "confidence": 0}


@router.get("/risk/assess")
async def get_risk_assessment(currency: str = Query(default="BTC")):
    """风险评估"""
    return await run_in_threadpool(get_risk_overview_sync, currency)


@router.get("/risk/overview")
async def get_risk_overview(currency: str = Query(default="BTC")):
    """统一风险中枢 - 合并风险评估与抄底建议"""
    return await run_in_threadpool(get_risk_overview_sync, currency)
