"""AI Co-Pilot API"""
import logging
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/copilot", tags=["copilot"])


@router.post("/chat")
async def copilot_chat(message: str, currency: str = "BTC"):
    """
    AI Copilot 对话接口
    自动注入当前市场上下文（DVOL、恐惧贪婪指数、大宗交易、持仓数据）
    """
    from services.ai_router import ai_chat
    from services.macro_data import get_fear_greed_index, get_funding_rate
    from services.dvol_analyzer import get_dvol_from_deribit

    context_parts = []

    try:
        fg = await run_in_threadpool(get_fear_greed_index)
        context_parts.append(f"恐惧贪婪指数: {fg.get('value', 'N/A')} ({fg.get('classification', '')})")
    except (RuntimeError, ConnectionError, TimeoutError) as e:
        logger.debug("Copilot fear/greed fetch failed: %s", e)

    try:
        fr = await run_in_threadpool(get_funding_rate, currency)
        rate = fr.get('current_rate')
        if rate is not None:
            context_parts.append(f"{currency}资金费率: {rate:.4f}%")
    except (RuntimeError, ConnectionError, TimeoutError) as e:
        logger.debug("Copilot funding rate fetch failed: %s", e)

    try:
        dvol = await run_in_threadpool(get_dvol_from_deribit, currency)
        context_parts.append(f"DVOL: {dvol.get('current', 0):.1f} (信号: {dvol.get('signal', '')})")
    except (RuntimeError, ConnectionError, TimeoutError) as e:
        logger.debug("Copilot DVOL fetch failed: %s", e)

    context = "\n".join(context_parts) if context_parts else "当前市场数据获取中"

    system_prompt = f"""你是一位专业的期权交易 AI 助手，专注于 Sell Put、Covered Call 和 Wheel 策略。
当前市场上下文:
{context}

请基于当前市场数据，给出简洁、专业的交易建议。
如果用户的问题与期权交易无关，请礼貌回答并引导到期权交易话题。
回答请用中文，不超过 300 字。"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message}
    ]

    response = await run_in_threadpool(ai_chat, messages, preset="chinese", temperature=0.5, max_tokens=500)

    return {"response": response, "context": context_parts, "currency": currency}
