"""MCP Server API"""
from typing import Dict, Any
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class MCPExecuteRequest(BaseModel):
    tool_name: str
    params: Dict[str, Any] = Field(default_factory=dict)


class MCPChatRequest(BaseModel):
    query: str
    currency: str = Field(default="BTC")


@router.get("/tools")
async def mcp_list_tools():
    """列出所有可用的 MCP 工具"""
    from services.mcp_server import mcp_registry
    return {"tools": mcp_registry.list_tools()}


@router.post("/execute")
async def mcp_execute_tool(request: MCPExecuteRequest):
    """
    执行 MCP 工具

    外部 AI（Cursor/Claude Desktop）可通过此接口调用本地工具：
    - get_market_overview: 获取市场概览
    - calculate_greeks: 计算希腊字母
    - analyze_large_trades: 分析大宗交易
    - suggest_roll_strategy: 滚仓建议
    - get_risk_assessment: 风险评估
    - get_paper_portfolio: 模拟盘信息
    """
    from services.mcp_server import mcp_registry

    result = await mcp_registry.execute_tool(request.tool_name, request.params)
    return result


@router.post("/chat")
async def mcp_chat(request: MCPChatRequest):
    """
    MCP 对话接口 - 专为外部 AI 设计

    AI 可以直接调用此接口，自动分析市场并给出建议。
    示例: "帮我看看现在的盘面适不适合做 Sell Put"
    """
    from services.ai_router import ai_chat
    from services.mcp_server import mcp_registry

    try:
        market_result = await mcp_registry.execute_tool("get_market_overview", {"currency": request.currency})
    except Exception as e:
        market_result = {"success": False, "data": {}}

    market_context = ""
    if market_result and isinstance(market_result, dict) and market_result.get("success"):
        data = market_result.get("data", {})
        parts = []
        if data and isinstance(data, dict):
            if "dvol" in data:
                parts.append(f"DVOL: {data['dvol']}")
            if "fear_greed" in data:
                fg = data["fear_greed"]
                if fg and isinstance(fg, dict):
                    parts.append(f"恐惧贪婪指数: {fg.get('value', 'N/A')} ({fg.get('classification', '')})")
            if "funding_rate" in data:
                fr = data["funding_rate"]
                if fr and isinstance(fr, dict) and fr.get("current_rate") is not None:
                    parts.append(f"资金费率: {fr['current_rate']:.4f}%")
        market_context = "\n".join(parts) if parts else "市场数据获取中"

    system_prompt = f"""你是一位专业的期权交易 AI 助手。当前市场数据:\n{market_context}\n\n请基于数据给出简洁、专业的交易建议。回答用中文，不超过 300 字。"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": request.query}
    ]

    response = await run_in_threadpool(
        ai_chat, messages, preset="chinese", temperature=0.5, max_tokens=500
    )

    return {
        "response": response,
        "market_context": market_context,
        "currency": request.currency
    }
