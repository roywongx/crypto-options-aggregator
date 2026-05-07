"""个人投资组合 API 路由"""
import json
import logging
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.portfolio_service import get_portfolio

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["portfolio"])


class PortfolioLLMRequest(BaseModel):
    force_refresh: bool = False


@router.get("/portfolio")
async def api_get_portfolio():
    """获取用户完整投资组合概览

    包含: 期权持仓、现货余额、理财产品、合约账户
    需要 .env 中配置 BINANCE_API_KEY + BINANCE_SECRET_KEY
    """
    return get_portfolio()


@router.post("/portfolio/llm")
async def api_portfolio_llm_analysis(body: PortfolioLLMRequest):
    """LLM 深度分析你的投资组合

    结合实时市场数据（DVOL、MaxPain、资金费率、Greeks），
    对持仓期权进行逐张分析，评估风险并给出建议。
    """
    portfolio = get_portfolio()
    if portfolio.get("error"):
        return StreamingResponse(
            _error_stream(portfolio["error"]),
            media_type="text/event-stream",
        )

    # 收集市场数据
    market_data = _collect_market_context(portfolio.get("spot_price_btc", 0))

    # 构建提示词
    prompt = _build_portfolio_prompt(portfolio, market_data)

    return StreamingResponse(
        _llm_stream(prompt),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ============================================================
# 辅助函数
# ============================================================

def _collect_market_context(spot: float) -> dict:
    """收集当前市场数据"""
    ctx = {"spot": spot}
    try:
        from services.dvol_analyzer import get_dvol_from_deribit
        dvol = get_dvol_from_deribit("BTC")
        if dvol:
            ctx["dvol"] = dvol.get("current", 0)
            ctx["dvol_z"] = dvol.get("z_score", 0)
            ctx["dvol_signal"] = dvol.get("signal", "normal")
    except Exception:
        pass

    try:
        from services.macro_data import get_fear_greed_index
        fg = get_fear_greed_index()
        if fg:
            ctx["fear_greed"] = fg.get("value", 50)
    except Exception:
        pass

    try:
        from services.max_pain import get_max_pain
        mp = get_max_pain("BTC", auto_calc=True)
        if mp > 0:
            ctx["max_pain"] = mp
    except Exception:
        pass

    return ctx


def _build_portfolio_prompt(portfolio: dict, market: dict) -> list:
    """构建组合分析提示词"""
    spot = market.get("spot", 0)
    opts = portfolio.get("options", {})
    summary = opts.get("summary", {})
    positions = opts.get("positions", [])

    positions_text = ""
    for p in positions:
        positions_text += (
            f"- {p['symbol']}: {p['side']} {p['option_type']} "
            f"行权价 ${p['strike']:,.0f}, 到期 {p['expiry']} ({p['dte']}天), "
            f"入场 ${p['entry_price']:,.0f}, 现价 ${p['mark_price']:,.0f}, "
            f"浮盈 +${p['unrealized_pnl']:,.0f} ({p['pnl_pct']}%), "
            f"{p['otm_itm']}, 距现货 {p['distance_spot_pct']}%\n"
        )

    system_prompt = """你是一位专业的加密货币期权投资顾问。用户是你的客户，请用中文分析他的投资组合。
你的分析必须基于数据和逻辑，不给出买卖建议，但指出风险和机会。

分析框架：
1. 组合概览：总体仓位、盈亏、风险收益
2. 到期日分析：最近到期合约的风险，各到期日的收益分布
3. 行权价分析：哪些行权价处于危险区间，距现货价格的距离
4. 希腊字母：基于价格变动、时间流逝、波动率变化的风险敏感性
5. 市场环境：结合当前 DVOL、Fear&Greed、MaxPain 判断持仓是否与市场匹配
6. 尾部风险：极端行情下的最大损失估算和情景分析
7. 仓位建议：是否过度集中，是否有改善空间"""

    user_prompt = f"""请分析我的期权投资组合：

=== 市场数据 ===
BTC 现货: ${spot:,.0f}
DVOL: {market.get('dvol', 'N/A')} (z-score: {market.get('dvol_z', 'N/A')})
恐惧贪婪指数: {market.get('fear_greed', 'N/A')}/100
MaxPain: ${market.get('max_pain', 'N/A'):,} (如果可用)

=== 期权持仓 ({summary.get('count', 0)} 张) ===
已收权利金: ${summary.get('total_premium_usd', 0):,.0f}
当前市值: ${summary.get('total_mark_value_usd', 0):,.0f}
未实现盈亏: +${summary.get('total_unrealized_pnl_usd', 0):,.0f} ({summary.get('total_pnl_pct', 0)}%)
Short: {summary.get('short_count', 0)} | Long: {summary.get('long_count', 0)}
Puts: {summary.get('puts_count', 0)} | Calls: {summary.get('calls_count', 0)}
最近到期: {summary.get('nearest_expiry_dte', 'N/A')} 天

=== 逐张持仓 ===
{positions_text}

请用 Markdown 格式给出完整的组合分析报告。重点分析：
1. 5月15日到期的 BTC-260515-75000-P 只剩8天，theta衰减如何？
2. 所有Put的行权价 (73k-76k) 距离现货 ({spot:,.0f}) 还有多少安全边际？
3. 如果BTC跌到MaxPain ({market.get('max_pain', 'N/A')}) 附近，组合会怎样？
4. DVOL当前水平对卖Put策略的影响？
5. 组合的最大风险是什么？"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


async def _llm_stream(messages: list):
    """调用 LLM 并流式返回 — 使用线程池避免阻塞事件循环"""
    import asyncio
    yield f"data: {json.dumps({'type': 'start'})}\n\n"

    # 加载 LLM 配置（在线程中执行 DB 读取）
    from db.connection import execute_read
    try:
        llm_cfg = await asyncio.to_thread(
            execute_read, "SELECT api_key, base_url, model FROM llm_config WHERE id=1"
        )
        if llm_cfg and llm_cfg[0]:
            from services.llm_analyst import _decrypt_key
            api_key = _decrypt_key(llm_cfg[0]["api_key"] or "")
            base_url = llm_cfg[0]["base_url"] or ""
            model = llm_cfg[0]["model"] or ""
        else:
            api_key = base_url = model = ""
    except Exception:
        api_key = base_url = model = ""

    try:
        from services.ai_router import ai_chat_with_config

        # 关键修复: ai_chat_with_config 是同步阻塞调用（OpenAI SDK → HTTP）。
        # 在 async generator 内直接调用会阻塞 uvicorn 事件循环，
        # 导致 SSE 流中断、网络超时。必须在线程池中执行。
        result = await asyncio.to_thread(
            ai_chat_with_config,
            messages=messages,
            preset="analysis",
            max_tokens=4000,
            custom_config={"api_key": api_key, "base_url": base_url, "model": model} if api_key else None,
        )
        if result:
            yield f"data: {json.dumps({'type': 'content', 'text': result}, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'error', 'text': 'LLM 服务不可用，请检查 API 配置'}, ensure_ascii=False)}\n\n"
    except Exception as e:
        logger.warning("Portfolio LLM failed: %s", e)
        yield f"data: {json.dumps({'type': 'error', 'text': f'分析失败: {str(e)}'}, ensure_ascii=False)}\n\n"

    yield f"data: {json.dumps({'type': 'done'})}\n\n"


async def _error_stream(msg: str):
    yield f"data: {json.dumps({'type': 'error', 'text': msg})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"
