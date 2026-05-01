"""健康检查 API"""
import logging
import time
from datetime import datetime, timezone
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from db.connection import get_db_connection
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health_check():
    """API 健康检查端点"""
    health = {
        "status": "healthy",
        "timestamp": time.time(),
        "checks": {}
    }
    
    # 检查数据库连接
    try:
        conn = get_db_connection(read_only=True)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        health["checks"]["database"] = "ok"
    except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
        health["checks"]["database"] = f"error: {str(e)}"
        health["status"] = "degraded"
    
    # 检查后台扫描状态
    try:
        cursor.execute("SELECT MAX(timestamp) FROM scan_records")
        row = cursor.fetchone()
        if row and row[0]:
            try:
                # 使用 timezone-aware UTC 解析，避免时区偏差
                last_scan_dt = datetime.strptime(str(row[0]), '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                age = time.time() - last_scan_dt.timestamp()
            except (ValueError, TypeError):
                try:
                    last_scan = float(row[0])
                    age = time.time() - last_scan
                except (ValueError, TypeError):
                    age = 9999
            health["checks"]["last_scan_age_seconds"] = round(age, 1)
            if age > config.SCAN_INTERVAL_SECONDS * 2:
                health["checks"]["scan_status"] = "stale"
                health["status"] = "degraded"
            else:
                health["checks"]["scan_status"] = "fresh"
        else:
            health["checks"]["scan_status"] = "no_data"
    except (RuntimeError, ValueError, TypeError) as e:
        health["checks"]["scan_status"] = f"error: {str(e)}"
        health["status"] = "degraded"
    
    # 检查现货价格缓存
    try:
        from services.spot_price import _spot_cache, _CACHE_TTL_SECONDS
        now = time.time()
        fresh_count = sum(1 for _, (p, t) in _spot_cache.items() if now - t < _CACHE_TTL_SECONDS)
        health["checks"]["spot_cache_fresh"] = fresh_count
    except (ImportError, AttributeError, TypeError) as e:
        logger.debug("Health check spot cache failed: %s", e)
        health["checks"]["spot_cache"] = "unknown"
    
    status_code = 200 if health["status"] == "healthy" else 503
    return JSONResponse(content=health, status_code=status_code)
