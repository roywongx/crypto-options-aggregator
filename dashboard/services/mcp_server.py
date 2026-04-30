"""
MCP Server - 模型上下文协议服务器
功能:
- 允许外部大模型（Cursor/Claude Desktop）通过 MCP 协议调用本地工具
- 包装核心计算函数为 MCP Tools
- 实现 AI 自主获取本地数据并给出交易建议
"""
import logging
import inspect
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
            name="get_highest_apr_put",
            description="获取指定 strike 和 DTE 范围内最高 APR 的 Sell Put 合约",
            handler=self._get_highest_apr_put
        )
        
        self.register_tool(
            name="calculate_roll_cost",
            description="计算滚仓成本和收益",
            handler=self._calculate_roll_cost
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
            result = handler(params)
            if inspect.isawaitable(result):
                result = await result
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
        T = dte / 365.0
        sigma = iv / 100.0
        r = 0.05
        return calculate_greeks_full(spot, strike, T, r, sigma, option_type)
    
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
            from services.spot_price import get_spot_price
            spot = get_spot_price(currency)
            assessment = assessor.assess_comprehensive_risk(spot, currency)
            result["assessment"] = assessment
        except Exception as e:
            result["error"] = str(e)
        
        result["currency"] = currency
        result["timestamp"] = datetime.utcnow().isoformat()
        return result
    
    async def _get_highest_apr_put(self, params: Dict) -> Dict:
        """获取指定 strike 和 DTE 范围内最高 APR 的 Sell Put 合约"""
        from services.spot_price import get_spot_price
        from services.quant_engine import bs_put_price, bs_delta
        
        currency = params.get("currency", "BTC")
        target_strike = params.get("strike")
        target_dte = params.get("dte", 30)
        margin_ratio = params.get("margin_ratio", 0.2)
        
        if not target_strike:
            return {"error": "缺少 strike 参数"}
        
        spot = get_spot_price(currency) or 0
        if not spot:
            return {"error": "无法获取现货价格"}
        
        dvol = None
        try:
            from services.dvol_analyzer import get_dvol_from_deribit
            dvol_data = get_dvol_from_deribit(currency)
            dvol = dvol_data.get("current", 50)
        except Exception:
            dvol = 50
        
        T = target_dte / 365.0
        sigma = dvol / 100.0 if dvol else 0.5
        r = 0.05
        
        premium = bs_put_price(spot, target_strike, T, r, sigma)
        delta = bs_delta(spot, target_strike, T, r, sigma, "P")
        
        margin_required = target_strike * margin_ratio
        apr = (premium / margin_required) / T * 100 if margin_required > 0 else 0
        
        return {
            "currency": currency,
            "spot": spot,
            "strike": target_strike,
            "dte": target_dte,
            "option_type": "PUT",
            "iv": round(dvol, 2),
            "premium": round(premium, 2),
            "delta": round(delta, 4),
            "margin_required": round(margin_required, 2),
            "apr": round(apr, 2),
            "distance_from_spot_pct": round((target_strike - spot) / spot * 100, 1)
        }

    async def _calculate_roll_cost(self, params: Dict) -> Dict:
        """计算滚仓成本和收益"""
        from services.spot_price import get_spot_price
        from services.quant_engine import bs_put_price, bs_delta
        
        currency = params.get("currency", "BTC")
        current_strike = params.get("current_strike")
        new_strike = params.get("new_strike")
        current_dte = params.get("current_dte", 30)
        new_dte = params.get("new_dte", 45)
        margin_ratio = params.get("margin_ratio", 0.2)
        
        if not current_strike or not new_strike:
            return {"error": "缺少 current_strike 或 new_strike 参数"}
        
        spot = get_spot_price(currency) or 0
        if not spot:
            return {"error": "无法获取现货价格"}
        
        dvol = None
        try:
            from services.dvol_analyzer import get_dvol_from_deribit
            dvol_data = get_dvol_from_deribit(currency)
            dvol = dvol_data.get("current", 50)
        except Exception:
            dvol = 50
        
        T_current = current_dte / 365.0
        T_new = new_dte / 365.0
        sigma = dvol / 100.0 if dvol else 0.5
        r = 0.05
        
        current_premium = bs_put_price(spot, current_strike, T_current, r, sigma)
        new_premium = bs_put_price(spot, new_strike, T_new, r, sigma)
        
        roll_credit = new_premium - current_premium
        roll_cost = -roll_credit if roll_credit < 0 else 0
        
        current_margin = current_strike * margin_ratio
        new_margin = new_strike * margin_ratio
        margin_delta = new_margin - current_margin
        
        current_apr = (current_premium / current_margin) / T_current * 100 if current_margin > 0 else 0
        new_apr = (new_premium / new_margin) / T_new * 100 if new_margin > 0 else 0
        
        return {
            "currency": currency,
            "spot": spot,
            "current_position": {
                "strike": current_strike,
                "dte": current_dte,
                "premium": round(current_premium, 2),
                "margin": round(current_margin, 2),
                "apr": round(current_apr, 2)
            },
            "new_position": {
                "strike": new_strike,
                "dte": new_dte,
                "premium": round(new_premium, 2),
                "margin": round(new_margin, 2),
                "apr": round(new_apr, 2)
            },
            "roll_analysis": {
                "roll_credit": round(roll_credit, 2),
                "roll_cost": round(roll_cost, 2),
                "margin_delta": round(margin_delta, 2),
                "apr_delta": round(new_apr - current_apr, 2),
                "recommendation": "favorable" if roll_credit > 0 else "costly"
            }
        }

    async def _get_paper_portfolio(self, params: Dict) -> Dict:
        currency = params.get("currency", "BTC")
        
        from services.paper_trading import get_portfolio_summary
        return get_portfolio_summary(currency)


mcp_registry = MCPToolRegistry()