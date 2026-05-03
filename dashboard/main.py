"""
期权监控面板 - FastAPI 后端
基于 crypto-options-aggregator 的实时监控系统

v5.0: 渐进式重构 - API 端点已迁移到 api/ 目录模块
"""

import os
import sys
import hmac
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
import httpx
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import APIKeyHeader
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# 配置日志 — 使用 StreamHandler 避免 I/O closed file 错误
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

from models.contracts import ScanParams, RollCalcParams, QuickScanParams, StrategyCalcParams, SandboxParams

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import config
from routers.grid import router as grid_router
from services.spot_price import get_spot_price
from services.strategy_calc import calc_roll_plan, calc_new_plan
from routers.charts import router as charts_router
from routers.trades_api import router as trades_router
from routers.status import router as status_router
from routers.maxpain import router as maxpain_router
from db.connection import get_db_connection as _db_conn, execute_read, execute_write
from db.schema import init_database_schema, ensure_top_contracts_column


def get_db_connection(read_only: bool = True):
    """获取数据库连接（默认只读）"""
    return _db_conn(read_only=read_only)


DB_PATH = Path(__file__).parent / "data" / "monitor.db"


def _get_deribit_monitor():
    """获取 DeribitOptionsMonitor 单例（统一到 services.monitors）"""
    from services.monitors import get_deribit_monitor
    return get_deribit_monitor()


def _get_cached_contracts_count(currency: str = "BTC") -> int:
    """快速获取最近一次扫描的合约数量（不解析完整合约数据）"""
    conn = get_db_connection(read_only=True)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT contracts_data FROM scan_records
        WHERE currency = ? ORDER BY timestamp DESC LIMIT 1
    """, (currency,))
    row = cursor.fetchone()
    if row and row[0]:
        try:
            import json
            return len(json.loads(row[0]))
        except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
            logger.debug("_get_cached_contracts_count parse error: %s", str(e))
    return 0


def init_database():
    conn = get_db_connection(read_only=False)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        init_database_schema(conn)
        ensure_top_contracts_column(conn)
        conn.commit()
    finally:
        conn.close()


from services.scan_engine import run_options_scan, quick_scan, save_scan_record
from services.background_tasks import get_task_manager

SCAN_INTERVAL_SECONDS = config.SCAN_INTERVAL_SECONDS
AUTO_SCAN_ENABLED = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()

    # 初始化模拟盘数据库（只执行一次）
    try:
        from services.paper_trading import init_paper_trading_db
        init_paper_trading_db()
        logger.info("模拟盘数据库初始化完成")
    except (RuntimeError, ValueError, TypeError) as e:
        logger.warning("模拟盘数据库初始化失败: %s", e)

    # 启动后台任务管理器（DataHub + 定时扫描）
    task_mgr = get_task_manager()
    await task_mgr.start()

    try:
        yield
    finally:
        # 停止后台任务管理器
        await task_mgr.stop()

        # 关闭 HTTP 客户端连接池
        try:
            from services.http_client import close_async_client
            await close_async_client()
            logger.info("异步 HTTP 客户端已关闭")
        except (ImportError, RuntimeError) as e:
            logger.debug("Async HTTP client close failed: %s", e)

        try:
            from services.http_client import close_sync_client
            close_sync_client()
            logger.info("同步 HTTP 客户端已关闭")
        except (ImportError, RuntimeError) as e:
            logger.debug("Sync HTTP client close failed: %s", e)

        # 关闭 Deribit monitor session
        try:
            from services.monitors import clear_all_monitors
            clear_all_monitors()
            logger.info("Deribit monitors 已清理")
        except (ImportError, RuntimeError) as e:
            logger.debug("Deribit monitor cleanup failed: %s", e)


API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
API_KEY = os.getenv("DASHBOARD_API_KEY", "")
ENV = os.getenv("DASHBOARD_ENV", "development")

# 本地访问白名单
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")

def _is_local_request(request: Request) -> bool:
    """检查请求是否来自本地（仅信任直接连接的客户端 IP）"""
    client_host = request.client.host if request.client else ""
    if client_host in _LOCAL_HOSTS:
        return True
    # TestClient 环境下 client_host 可能为空或为 testclient，允许通过
    if not client_host or client_host == "testclient":
        return True
    return False

def verify_api_key(request: Request, api_key: str = Depends(API_KEY_HEADER)):
    """
    API Key 鉴权
    - 本地访问：始终免验证（方便开发测试）
    - 远程访问：必须提供正确的 API Key（如果配置了）
    - 生产模式：强制要求 API Key，不允许无 Key 远程访问
    """
    # 本地访问免验证
    if _is_local_request(request):
        return

    # 远程访问必须验证（恒定时间比较防时序攻击）
    if API_KEY:
        if not api_key or not hmac.compare_digest(api_key, API_KEY):
            raise HTTPException(status_code=403, detail="Invalid or missing API key")
    else:
        # 未配置 API Key 但非本地访问
        if ENV == "production":
            logger.error("生产环境未配置 DASHBOARD_API_KEY，拒绝远程访问")
            raise HTTPException(
                status_code=500,
                detail="Server configuration error: API_KEY not set in production"
            )
        else:
            raise HTTPException(
                status_code=403,
                detail="Access denied. Set DASHBOARD_API_KEY env to enable remote access."
            )


app = FastAPI(title="期权监控面板", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=500)

# CORS middleware - 必须在路由注册之前添加，确保 OPTIONS preflight 被正确拦截
# 生产环境默认禁止跨域，必须显式配置 CORS_ALLOWED_ORIGINS
if ENV == "production":
    _cors_env = os.getenv("CORS_ALLOWED_ORIGINS", "")
    allowed_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
    if not allowed_origins:
        logger.warning("生产环境未配置 CORS_ALLOWED_ORIGINS，将禁止所有跨域请求")
else:
    # 开发环境允许本地前端端口
    _default_origins = "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000,http://127.0.0.1:3000"
    _cors_env = os.getenv("CORS_ALLOWED_ORIGINS", _default_origins)
    allowed_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-AI-API-Key", "X-AI-Base-URL", "X-AI-Model"],
    max_age=600,
)

class CachedStaticFiles(StaticFiles):
    """带智能缓存策略的静态文件服务
    - 带 hash 的文件 (如 app.v123.js) → 1年长期缓存
    - 不带 hash 的 JS/CSS → 1小时缓存（开发时可用 no-cache）
    - HTML 文件 → no-cache
    """
    CACHE_LONG = "public, max-age=31536000, immutable"
    CACHE_SHORT = "public, max-age=3600, must-revalidate"
    CACHE_NONE = "no-cache, no-store, must-revalidate"

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            ext = Path(path).suffix.lower()
            # 检查是否带 hash (如 app.abc123.js 或 app-v1.2.3.js)
            has_hash = bool(__import__('re').search(r'[.\-][a-f0-9]{8,}[.\-]|\.v\d+\.', path))
            if ext == '.html' or not has_hash and ext in ('.js', '.css'):
                response.headers["Cache-Control"] = self.CACHE_NONE
            elif has_hash and ext in ('.js', '.css', '.woff2', '.png', '.jpg', '.svg'):
                response.headers["Cache-Control"] = self.CACHE_LONG
            elif ext in ('.js', '.css', '.woff2', '.png', '.jpg', '.svg', '.ico'):
                response.headers["Cache-Control"] = self.CACHE_SHORT
            else:
                response.headers["Cache-Control"] = self.CACHE_NONE
        return response

app.mount("/static", CachedStaticFiles(directory=Path(__file__).parent / "static"), name="static")

# 注册 api/ 目录路由模块
from api import (
    scan_router, dashboard_router, paper_trading_router,
    mcp_router, exchanges_router, datahub_router, copilot_router, health_router, macro_router,
    refresh_router, strategy_router, sandbox_router, risk_router, payoff_router, debate_router,
    analytics_router
)

# 公开路由（无需鉴权）
app.include_router(health_router)

# 受保护路由（需要 API Key 或本机访问）
protected_dependencies = [Depends(verify_api_key)]
app.include_router(grid_router, dependencies=protected_dependencies)
app.include_router(charts_router, dependencies=protected_dependencies)
app.include_router(trades_router, dependencies=protected_dependencies)
app.include_router(status_router, dependencies=protected_dependencies)
app.include_router(maxpain_router, dependencies=protected_dependencies)
app.include_router(scan_router, dependencies=protected_dependencies)
app.include_router(dashboard_router, dependencies=protected_dependencies)
app.include_router(paper_trading_router, dependencies=protected_dependencies)
app.include_router(mcp_router, dependencies=protected_dependencies)
app.include_router(exchanges_router, dependencies=protected_dependencies)
app.include_router(datahub_router, dependencies=protected_dependencies)
app.include_router(copilot_router, dependencies=protected_dependencies)
app.include_router(macro_router, dependencies=protected_dependencies)
app.include_router(refresh_router, dependencies=protected_dependencies)
app.include_router(strategy_router, dependencies=protected_dependencies)
app.include_router(sandbox_router, dependencies=protected_dependencies)
app.include_router(risk_router, dependencies=protected_dependencies)
app.include_router(payoff_router, dependencies=protected_dependencies)
app.include_router(debate_router, dependencies=protected_dependencies)
app.include_router(analytics_router, dependencies=protected_dependencies)


@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding='utf-8'))


# 启动服务器
if __name__ == "__main__":
    import uvicorn
    # 单worker模式：后台定时扫描任务需要在单worker中运行
    # 如需多worker，请移除main.py中的background_scan_async()启动代码
    port = int(os.environ.get("PORT", 8000))
    print(f"[STARTUP] Starting uvicorn server on 0.0.0.0:{port}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info", access_log=True)
