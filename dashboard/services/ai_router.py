"""
LiteLLM AI 路由服务 - 支持多模型切换
一行代码路由到 OpenAI/DeepSeek/Claude/Gemini 等
"""
import os
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# ============================================================
# LiteLLM 封装
# ============================================================

try:
    from litellm import completion
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    logger.warning("LiteLLM 未安装，AI 路由功能不可用 (pip install litellm)")


# 预设模型路由配置
MODEL_PRESETS = {
    "analysis": {
        # 复杂推理分析 -> Claude/Gemini
        "model": "claude-sonnet-4-20250514",
        "fallback": ["gemini-2.0-flash", "deepseek-chat"],
    },
    "code": {
        # 代码/计算 -> DeepSeek-Coder
        "model": "deepseek/deepseek-coder",
        "fallback": ["gpt-4o-mini"],
    },
    "fast": {
        # 快速回复 -> GPT-4o-mini
        "model": "gpt-4o-mini",
        "fallback": ["gemini-2.0-flash"],
    },
    "chinese": {
        # 中文优化 -> DeepSeek
        "model": "deepseek/deepseek-chat",
        "fallback": ["gpt-4o-mini"],
    }
}


def ai_chat(
    messages: List[Dict[str, str]],
    preset: str = "fast",
    temperature: float = 0.7,
    max_tokens: int = 1000
) -> Optional[str]:
    """
    通用 AI 聊天接口
    
    Args:
        messages: [{"role": "user", "content": "..."}]
        preset: analysis | code | fast | chinese
        temperature: 0-1
        max_tokens: 最大输出长度
    
    Returns:
        AI 回复文本，或 None
    """
    if not LITELLM_AVAILABLE:
        logger.warning("LiteLLM 未安装")
        return None
    
    preset_config = MODEL_PRESETS.get(preset, MODEL_PRESETS["fast"])
    model = preset_config["model"]
    fallbacks = preset_config.get("fallback", [])
    
    # 从环境变量获取 API Key
    api_key = os.environ.get("LITELLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.warning("未设置 LITELLM_API_KEY 或 OPENAI_API_KEY")
        return None
    
    try:
        response = completion(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
        )
        return response.choices[0].message.content

    except (ImportError, RuntimeError, ConnectionError, TimeoutError) as e:
        # 尝试 fallback
        for fallback_model in fallbacks:
            try:
                response = completion(
                    model=fallback_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    api_key=api_key,
                )
                return response.choices[0].message.content
            except (ImportError, RuntimeError, ConnectionError, TimeoutError) as fb_e:
                logger.debug("AI fallback %s failed: %s", fallback_model, fb_e)
                continue

        logger.warning("AI 回复失败 (所有模型): %s", e)
        return None


def analyze_large_trades(trade_summary: str, currency: str = "BTC") -> Optional[str]:
    """
    分析大宗交易，给出市场解读
    
    Args:
        trade_summary: 交易数据摘要
        currency: 币种
    
    Returns:
        AI 分析文本
    """
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
        temperature=0.3,
        max_tokens=500
    )


def suggest_roll_strategy(
    current_position: str,
    market_conditions: str,
    dvol_signal: str = "",
    funding_rate: str = ""
) -> Optional[str]:
    """
    智能滚仓策略建议
    
    Args:
        current_position: 当前持仓信息
        market_conditions: 市场条件
        dvol_signal: DVOL 信号
        funding_rate: 资金费率
    
    Returns:
        策略建议
    """
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
        preset="chinese",
        temperature=0.3,
        max_tokens=400
    )


# ============================================================
# 简单 OpenAI 兼容接口 (无需 LiteLLM)
# ============================================================

def simple_openai_chat(
    messages: List[Dict[str, str]],
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 1000
) -> Optional[str]:
    """
    简单 OpenAI 兼容接口 (不需要 LiteLLM)
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    
    try:
        import httpx
        import json
        
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=30
        )
        
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        else:
            logger.warning("OpenAI API 错误: %s", response.text)
            return None
            
    except Exception as e:
        logger.warning("OpenAI 请求失败: %s", str(e))
        return None
