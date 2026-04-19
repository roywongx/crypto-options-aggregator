"""
MCP Server - 模型上下文协议服务器
功能:
- 允许外部大模型（Cursor/Claude Desktop）通过 MCP 协议调用本地工具
- 包装核心计算函数为 MCP Tools
- 实现 AI 自主获取本地数据并给出交易建议
"""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


class MCPToolRegistry:
    """MCP 工具注册表"""
    
    def __init__(self):
        self._tools: Dict[str, Dict] = {}
        self._register_default_tools()
    
    def _register_default_tools(self):
        self.register_tool(
            name="get_market_overview",
            description="获取当前市场概览数据（DVOL、恐惧贪婪指数、资金费率）",
            handler=self._get_market_overview
        )
        
        self.register_tool(
            name="calculate_greeks",
            description="计算期权希腊字母（Delta, Gamma, Theta, Vega, IV）",
            handler=self._calculate_greeks
        )
        
        self.register_tool(
            name="analyze_large_trades",
            description="分析最近的大宗交易并给出市场情绪判断",
            handler=self._analyze_large_trades
        )
        
        self.register_tool(
            name="suggest_roll_strategy",
            description="基于当前持仓和市场状况建议滚仓策略",
            handler=self._suggest_roll_strategy
        )
        
        self.register_tool(
            name="get_risk_assessment",
            description="获取当前风险框架评估和压力测试结果",
            handler=self._get_risk_assessment
        )
        
        self.register_tool(
            name="get_paper_portfolio",
            description="获取模拟盘组合状态和持仓信息",
            handler=self._get_paper_portfolio
        )
    
    def register_tool(self, name: str, description: str, handler):
        self._tools[name] = {
            "name": name,
            "description": description,
            "handler": handler
        }
    
    def list_tools(self) -> List[Dict]:
        return [
            {
                "name": tool["name"],
                "description": tool["description"]
            }
            for tool in self._tools.values()
        ]
    
    async def execute_tool(self, name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if name not in self._tools:
            return {"error": f"工具 '{name}' 不存在"}
        
        try:
            handler = self._tools[name]["handler"]
            result = await handler(params) if hasattr(handler, '__await__') else handler(params)
            return {"success": True, "data": result}
        except Exception as e:
            logger.error("MCP tool '%s' execution error: %s", name, str(e))
            return {"error": str(e)}
    
    async def _get_market_overview(self, params: Dict) -> Dict:
        currency = params.get("currency", "BTC")
        
        result = {}
        
        try:
            from services.dvol_analyzer import get_dvol_from_deribit
            dvol = get_dvol_from_deribit(currency)
            result["dvol"] = dvol
        except Exception as e:
            result["dvol_error"] = str(e)
        
        try:
            from services.macro_data import get_fear_greed_index
            fg = get_fear_greed_index()
            result["fear_greed"] = fg
        except Exception as e:
            result["fear_greed_error"] = str(e)
        
        try:
            from services.macro_data import get_funding_rate
            fr = get_funding_rate(currency)
            result["funding_rate"] = fr
        except Exception as e:
            result["funding_rate_error"] = str(e)
        
        result["currency"] = currency
        result["timestamp"] = datetime.utcnow().isoformat()
        return result
    
    async def _calculate_greeks(self, params: Dict) -> Dict:
        strike = params.get("strike")
        spot = params.get("spot")
        dte = params.get("dte", 30)
        iv = params.get("iv", 50)
        option_type = params.get("option_type", "P")
        
        if not strike or not spot:
            return {"error": "缺少 strike 或 spot 参数"}
        
        from services.quant_engine import calculate_greeks_full
        return calculate_greeks_full(option_type, strike, spot, iv / 100.0, dte)
    
    async def _analyze_large_trades(self, params: Dict) -> Dict:
        currency = params.get("currency", "BTC")
        days = params.get("days", 3)
        limit = params.get("limit", 30)
        
        from services.trades import fetch_large_trades
        trades = fetch_large_trades(currency, days=days, limit=limit)
        
        if not trades:
            return {"message": "无大宗交易数据", "count": 0}
        
        summary = []
        for t in trades[:10]:
            summary.append({
                "time": t.get("timestamp"),
                "direction": t.get("direction"),
                "option_type": t.get("option_type"),
                "strike": t.get("strike"),
                "notional": t.get("notional_usd", 0)
            })
        
        return {
            "count": len(trades),
            "recent_trades": summary,
            "total_notional": sum(float(t.get("notional_usd", 0)) for t in trades)
        }
    
    async def _suggest_roll_strategy(self, params: Dict) -> Dict:
        position_id = params.get("position_id")
        if not position_id:
            return {"error": "缺少 position_id 参数"}
        
        from services.paper_trading import get_roll_suggestion
        return get_roll_suggestion(position_id)
    
    async def _get_risk_assessment(self, params: Dict) -> Dict:
        currency = params.get("currency", "BTC")
        
        result = {}
        
        try:
            from services.unified_risk_assessor import UnifiedRiskAssessor
            assessor = UnifiedRiskAssessor()
            assessment = assessor.assess_currency(currency)
            result["assessment"] = assessment
        except Exception as e:
            result["error"] = str(e)
        
        result["currency"] = currency
        result["timestamp"] = datetime.utcnow().isoformat()
        return result
    
    async def _get_paper_portfolio(self, params: Dict) -> Dict:
        currency = params.get("currency", "BTC")
        
        from services.paper_trading import get_portfolio_summary
        return get_portfolio_summary(currency)


mcp_registry = MCPToolRegistry()