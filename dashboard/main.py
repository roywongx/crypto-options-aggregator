"""
期权监控面板 - FastAPI 后端
基于 crypto-options-aggregator 的实时监控系统

v5.0: 渐进式重构 - API 端点已迁移到 api/ 目录模块
"""

import os
import re
import hmac

from dotenv import load_dotenv
load_dotenv()
import collections
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
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from fastapi.security import APIKeyHeader
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# 配置日志 — 控制台 + 内存环形缓冲区（供 /api/logs 查看）
import collections
_LOG_BUFFER: collections.deque = collections.deque(maxlen=500)  # 保留最近 500 行


class _BufferHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        _LOG_BUFFER.append(msg)
        # 同时输出到 stdout（uvicorn 会捕获）
        print(msg, flush=True)


_buffer_handler = _BufferHandler()
_buffer_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(_buffer_handler)

# 收编 uvicorn/fastapi 等库的日志到缓冲区
for _name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "httpx", "websockets"):
    _lib_logger = logging.getLogger(_name)
    _lib_logger.handlers.clear()
    _lib_logger.propagate = True

logger = logging.getLogger(__name__)

from models.contracts import ScanParams, RollCalcParams, QuickScanParams, StrategyCalcParams, SandboxParams

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

        # 关闭扫描引擎线程池
        try:
            from services.scan_engine import _scan_executor
            _scan_executor.shutdown(wait=False)
            logger.info("扫描引擎线程池已关闭")
        except (ImportError, RuntimeError) as e:
            logger.debug("Scan executor shutdown failed: %s", e)

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
    """检查请求是否来自本地或 LAN（开发模式信任私有网络）"""
    client_host = request.client.host if request.client else ""
    if client_host in _LOCAL_HOSTS:
        return True
    # 开发模式：信任私有网络地址（192.168.x.x, 10.x.x.x, 172.16-31.x.x）
    if ENV == "development":
        if not client_host or client_host == "testclient":
            return True
        if client_host.startswith(("192.168.", "10.", "172.")):
            # 验证确实是私有 B 类范围 172.16.0.0/12
            if client_host.startswith("172."):
                try:
                    octets = client_host.split(".")
                    second = int(octets[1])
                    if 16 <= second <= 31:
                        return True
                except (ValueError, IndexError):
                    pass
            else:
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
            has_hash = bool(re.search(r'[.\-][a-f0-9]{8,}[.\-]|\.v\d+\.', path))
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
    mcp_router, exchanges_router, datahub_router, health_router, macro_router,
    refresh_router, strategy_router, sandbox_router, risk_router,
    llm_analyst_router, recommendations_router, portfolio_router
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
app.include_router(macro_router, dependencies=protected_dependencies)
app.include_router(refresh_router, dependencies=protected_dependencies)
app.include_router(strategy_router, dependencies=protected_dependencies)
app.include_router(sandbox_router, dependencies=protected_dependencies)
app.include_router(risk_router, dependencies=protected_dependencies)
app.include_router(llm_analyst_router, dependencies=protected_dependencies)
app.include_router(recommendations_router, dependencies=protected_dependencies)
app.include_router(portfolio_router, dependencies=protected_dependencies)


@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding='utf-8'))


@app.get("/api/logs")
async def view_logs(tail: int = 100, level: str = ""):
    """实时日志 JSON API"""
    lines = list(_LOG_BUFFER)
    if level:
        level = level.upper()
        lines = [l for l in lines if f"[{level}]" in l]
    tail_lines = lines[-tail:]
    return JSONResponse({"total": len(lines), "lines": tail_lines, "max_capacity": _LOG_BUFFER.maxlen})


@app.get("/logs", response_class=HTMLResponse)
async def log_viewer():
    """实时日志查看页面（每3秒自动刷新）"""
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Server Logs</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:#0d1117; color:#c9d1d9; font:13px/1.5 'Cascadia Code','Consolas',monospace; padding:12px 16px; }
  .bar { display:flex; gap:10px; align-items:center; margin-bottom:10px; position:sticky; top:0; background:#0d1117; padding:8px 0; z-index:1; border-bottom:1px solid #21262d; }
  .bar button, .bar select { background:#21262d; color:#c9d1d9; border:1px solid #30363d; border-radius:6px; padding:4px 12px; cursor:pointer; font-size:12px; }
  .bar button:hover { background:#30363d; }
  .bar .info { color:#8b949e; font-size:11px; margin-left:auto; }
  .log { white-space:pre-wrap; word-break:break-all; }
  .log .E { color:#f85149; } .log .W { color:#d2991d; } .log .I { color:#7ee787; } .log .D { color:#8b949e; }
  .log .ts { color:#484f58; }
</style>
</head>
<body>
<div class="bar">
  <span style="color:#58a6ff;font-weight:600;">📋 Server Logs</span>
  <select id="level" onchange="fetchLogs()">
    <option value="">全部级别</option>
    <option value="ERROR">ERROR</option>
    <option value="WARNING">WARNING</option>
    <option value="INFO">INFO</option>
    <option value="DEBUG">DEBUG</option>
  </select>
  <button onclick="fetchLogs()">🔄 刷新</button>
  <label style="font-size:12px;cursor:pointer"><input type="checkbox" id="auto" checked onchange="toggleAuto()"> 自动刷新</label>
  <span class="info" id="info"></span>
</div>
<div class="log" id="log">加载中...</div>
<script>
let timer;
function hl(line) {
  let cls='', txt=line;
  if (line.includes('[ERROR]')) cls='E';
  else if (line.includes('[WARNING]')) cls='W';
  else if (line.includes('[INFO]')) cls='I';
  else if (line.includes('[DEBUG]')) cls='D';
  // 时间戳变灰
  txt = txt.replace(/^(\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2},\\d{3})/, '<span class="ts">$1</span>');
  return '<span class="'+cls+'">'+txt+'</span>';
}
async function fetchLogs() {
  try {
    let lv = document.getElementById('level').value;
    let url = '/api/logs?tail=200' + (lv ? '&level='+lv : '');
    let r = await fetch(url); let d = await r.json();
    document.getElementById('log').innerHTML = d.lines.map(hl).join('\\n');
    document.getElementById('info').textContent = d.total+' 行 / 最多'+d.max_capacity+' | '+new Date().toLocaleTimeString();
    window.scrollTo(0, document.body.scrollHeight);
  } catch(e) { document.getElementById('log').textContent = '获取日志失败: '+e.message; }
}
function toggleAuto() {
  if (document.getElementById('auto').checked) { timer = setInterval(fetchLogs, 3000); }
  else { clearInterval(timer); }
}
fetchLogs();
timer = setInterval(fetchLogs, 3000);
</script>
</body>
</html>""")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(Path(__file__).parent / "static" / "favicon.svg")


# 启动服务器
if __name__ == "__main__":
    import uvicorn
    # 单worker模式：后台定时扫描任务需要在单worker中运行
    # 如需多worker，请移除main.py中的background_scan_async()启动代码
    port = int(os.environ.get("PORT", 8000))
    print(f"[STARTUP] Starting uvicorn server on 0.0.0.0:{port}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info", access_log=True)
