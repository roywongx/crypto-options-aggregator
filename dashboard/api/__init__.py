"""
API 路由模块

将 main.py 中的 API 端点按功能拆分到独立模块：
- scan.py: 扫描相关端点
- dashboard.py: 仪表盘聚合端点
- paper_trading.py: 模拟盘端点
- mcp.py: MCP Server 端点
- exchanges.py: 交易所抽象层端点
- datahub.py: DataHub 端点
- health.py: 健康检查端点
- macro.py: 宏观数据端点
- refresh.py: 数据刷新端点
- strategy.py: 策略计算端点
- sandbox.py: 沙盘推演端点
- risk.py: 风险评估端点
"""

from fastapi import APIRouter

from .scan import router as scan_router
from .dashboard import router as dashboard_router
from .paper_trading import router as paper_trading_router
from .mcp import router as mcp_router
from .exchanges import router as exchanges_router
from .datahub import router as datahub_router
from .health import router as health_router
from .macro import router as macro_router
from .refresh import router as refresh_router
from .strategy import router as strategy_router
from .sandbox import router as sandbox_router
from .risk import router as risk_router
from .llm_analyst import router as llm_analyst_router

__all__ = [
    "scan_router",
    "dashboard_router",
    "paper_trading_router",
    "mcp_router",
    "exchanges_router",
    "datahub_router",
    "health_router",
    "macro_router",
    "refresh_router",
    "strategy_router",
    "sandbox_router",
    "risk_router",
    "llm_analyst_router",
]
