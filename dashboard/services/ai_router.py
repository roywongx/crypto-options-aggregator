"""
DeepSeek AI 路由服务 — 原生 DeepSeek v4 API 支持
支持思考模式 (thinking)、推理强度控制 (reasoning_effort)、JSON 结构化输出
"""
import os
import json
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# ============================================================
# 原生 DeepSeek API 调用 (使用 OpenAI SDK 兼容接口)
# ============================================================

DEEPSEEK_BASE_URL = os.environ.get("LLM_DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-pro"


def deepseek_chat(
    messages: List[Dict[str, str]],
    model: str = DEEPSEEK_DEFAULT_MODEL,
    max_tokens: int = 4000,
    thinking: bool = True,
    reasoning_effort: str = "high",
    response_format: Optional[Dict[str, str]] = None,
    stream: bool = False,
    api_key: str = "",
    base_url: str = "",
) -> Optional[str]:
    """
    原生 DeepSeek API 聊天 (OpenAI SDK 兼容, 支持思考模式)

    Args:
        messages: [{"role": "system/user/assistant", "content": "..."}]
        model: 模型 ID，默认 deepseek-v4-pro
        max_tokens: 最大输出 token (含推理 token)
        thinking: 启用思考模式 (temperature/top_p 等参数会被忽略)
        reasoning_effort: 推理强度 "high" | "max" (默认 max 用于复杂分析)
        response_format: {"type": "json_object"} 用于结构化输出
        stream: 启用 SSE 流式输出
        api_key: DeepSeek API Key
        base_url: DeepSeek API Base URL (默认 https://api.deepseek.com)

    Returns:
        AI 回复文本，或 None
    """
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai SDK 未安装 (pip install openai)")
        return _deepseek_chat_via_httpx(messages, model, max_tokens, thinking,
                                       reasoning_effort, response_format, stream,
                                       api_key, base_url)

    key = api_key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("LITELLM_API_KEY")
    if not key:
        logger.warning("未设置 DeepSeek API Key")
        return None

    url = base_url or DEEPSEEK_BASE_URL

    try:
        client = OpenAI(api_key=key, base_url=url)

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }

        if thinking:
            kwargs["extra_body"] = {
                "thinking": {"type": "enabled"},
                "reasoning_effort": reasoning_effort,
            }
        else:
            kwargs["temperature"] = 0.3

        if response_format:
            kwargs["response_format"] = response_format

        if stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}

        response = client.chat.completions.create(**kwargs)

        if stream:
            # 收集流式输出
            content_parts = []
            for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    content_parts.append(delta.content)
            return "".join(content_parts)
        else:
            msg = response.choices[0].message
            content = msg.content
            if content and content.strip():
                return content
            # content 为空时不回退到 reasoning_content（思维链文本，非 JSON）
            # 记录日志方便排查
            rc = getattr(msg, "reasoning_content", None)
            if rc:
                logger.warning(
                    "DeepSeek content is empty (len=%d), reasoning_content len=%d — "
                    "not falling back to reasoning_content as it is not the model output",
                    len(content or ""), len(rc or "")
                )
            else:
                logger.warning("DeepSeek returned empty content and no reasoning_content")
            return ""

    except Exception as e:
        logger.warning("DeepSeek API 调用失败 (%s): %s", type(e).__name__, e)
        return None


def _deepseek_chat_via_httpx(
    messages, model, max_tokens, thinking, reasoning_effort,
    response_format, stream, api_key, base_url
) -> Optional[str]:
    """降级方案: 直接 httpx 调用 DeepSeek API (不依赖 OpenAI SDK)"""
    import httpx as _httpx

    key = api_key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("LITELLM_API_KEY")
    if not key:
        return None

    url = (base_url or DEEPSEEK_BASE_URL).rstrip("/") + "/v1/chat/completions"

    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": stream,
    }

    if thinking:
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = reasoning_effort
    else:
        body["temperature"] = 0.3

    if response_format:
        body["response_format"] = response_format

    if stream:
        body["stream_options"] = {"include_usage": True}

    try:
        resp = _httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json",
            },
            json=body,
            timeout=120.0,
        )
        if resp.status_code == 200:
            if stream:
                # 手动解析 SSE
                content_parts = []
                for line in resp.text.split("\n"):
                    if line.startswith("data: ") and line.strip() != "data: [DONE]":
                        try:
                            chunk = json.loads(line[6:])
                            delta = (chunk.get("choices", [{}])[0].get("delta") or {})
                            if delta.get("content"):
                                content_parts.append(delta["content"])
                        except (json.JSONDecodeError, KeyError, IndexError):
                            pass
                return "".join(content_parts)
            else:
                data = resp.json()
                msg = data["choices"][0].get("message", {})
                content = msg.get("content")
                if content and content.strip():
                    return content
                rc = msg.get("reasoning_content")
                if rc:
                    logger.warning(
                        "DeepSeek httpx content empty, reasoning_content len=%d — "
                        "not falling back to reasoning_content", len(rc or "")
                    )
                return ""
        else:
            logger.warning("DeepSeek API HTTP %d: %s", resp.status_code, resp.text[:500])
            return None
    except Exception as e:
        logger.warning("DeepSeek API httpx 调用失败: %s", e)
        return None


# ============================================================
# 兼容旧接口 — 保留 ai_chat_with_config 但内部走 DeepSeek
# ============================================================

def ai_chat_with_config(
    messages: List[Dict[str, str]],
    preset: str = "analysis",
    temperature: float = 0.3,
    max_tokens: int = 4000,
    custom_config: Dict[str, str] = None,
) -> Optional[str]:
    """
    统一 AI 聊天接口 (内部路由到 DeepSeek 原生 API)

    Args:
        messages: [{"role": "user", "content": "..."}]
        preset: analysis | fast | debate | audit (对应不同的 reasoning_effort)
        temperature: 忽略 (思考模式下无效)
        max_tokens: 最大输出 token (含推理)
        custom_config: {"api_key": "...", "base_url": "...", "model": "..."}

    Returns:
        AI 回复文本，或 None
    """
    api_key = ""
    base_url = ""
    model = DEEPSEEK_DEFAULT_MODEL
    thinking = True
    reasoning_effort = "high"

    # 根据 preset 调整推理强度
    if preset == "crypto_analyst":
        thinking = True
        reasoning_effort = "high"
        from services.crypto_market_context import CryptoMarketContext
        ctx = CryptoMarketContext.build({}, "BTC")
        system_override = CryptoMarketContext.to_prompt_text(ctx)
        # 注入市场上下文到 messages 前端
        if not any(m.get("role") == "system" and "加密市场" in m.get("content", "") for m in messages):
            messages = [{"role": "system", "content": system_override[:3000]}] + messages
    elif preset == "fast":
        thinking = True
        reasoning_effort = "medium"
    elif preset in ("analysis", "debate", "audit"):
        thinking = True
        reasoning_effort = "high"

    if custom_config:
        api_key = custom_config.get("api_key", "")
        base_url = custom_config.get("base_url", "")
        if custom_config.get("model"):
            model = custom_config["model"]

    # 判断是否需要 JSON 结构化输出
    response_format = None
    if preset in ("analysis", "debate", "audit"):
        # 在系统提示中要求 JSON 时，使用 json_object 模式提升一致性
        has_json_instruct = any(
            "json" in m.get("content", "").lower()
            for m in messages
            if m.get("role") == "system"
        )
        if has_json_instruct:
            response_format = {"type": "json_object"}
            # 重要: json_object 模式与 thinking 模式冲突
            # thinking 模式可能导致 content 为空，reasoning_content 包含思维链文本
            # 对 JSON 结构化输出请求禁用 thinking，确保返回纯 JSON
            thinking = False

    return deepseek_chat(
        messages=messages,
        model=model,
        max_tokens=max_tokens,
        thinking=thinking,
        reasoning_effort=reasoning_effort,
        response_format=response_format,
        api_key=api_key,
        base_url=base_url,
    )


def ai_chat(
    messages: List[Dict[str, str]],
    preset: str = "analysis",
    temperature: float = 0.3,
    max_tokens: int = 4000
) -> Optional[str]:
    """通用 AI 聊天接口 (兼容旧签名)"""
    return ai_chat_with_config(messages, preset, temperature, max_tokens)


def analyze_large_trades(trade_summary: str, currency: str = "BTC") -> Optional[str]:
    """分析大宗交易，给出市场解读"""
    prompt = f"""作为专业期权交易分析师，请分析以下{currency}大宗交易数据并给出市场解读:

{trade_summary}

请从以下维度分析:
1. 主要资金流向 (看多/看空/中性)
2. 机构行为意图 (对冲/投机/套利)
3. 当前市场情绪
4. 对期权策略的建议

请用简洁的中文回答，不超过200字。"""

    return ai_chat(
        [{"role": "user", "content": prompt}],
        preset="analysis",
        max_tokens=800
    )


def suggest_roll_strategy(
    current_position: str,
    market_conditions: str,
    dvol_signal: str = "",
    funding_rate: str = ""
) -> Optional[str]:
    """智能滚仓策略建议"""
    macro_context = ""
    if dvol_signal:
        macro_context += f"- DVOL: {dvol_signal}\n"
    if funding_rate:
        macro_context += f"- 资金费率: {funding_rate}\n"

    prompt = f"""作为专业期权滚仓策略师，请根据以下信息给出滚仓建议:

当前持仓: {current_position}
市场条件: {market_conditions}
{macro_context}

请提供:
1. 是否应该滚仓 (是/否/观望)
2. 推荐的目标行权价范围
3. 推荐的到期时间范围
4. 风险提示

请用简洁的中文回答，不超过150字。"""

    return ai_chat(
        [{"role": "user", "content": prompt}],
        preset="fast",
        max_tokens=600
    )


# ============================================================
# 简单 OpenAI 兼容接口 (无需 LiteLLM)
# ============================================================

def simple_openai_chat(
    messages: List[Dict[str, str]],
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 4000
) -> Optional[str]:
    """简单 OpenAI 兼容接口 (自动路由到配置的 API)"""
    return deepseek_chat(
        messages=messages,
        model=model or DEEPSEEK_DEFAULT_MODEL,
        max_tokens=max_tokens,
        thinking=True,
        reasoning_effort="high",
    )
