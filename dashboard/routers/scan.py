# Routers - Scan
# DEPRECATED: 此文件是死代码，routers 从未被 main.py include
# 所有路由已在 main.py 中直接定义。此文件保留仅作为参考，未来版本可删除。
from fastapi import APIRouter, Query, Body
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import json

router = APIRouter(prefix="/api", tags=["scan"])

class ScanParams(BaseModel):
    currency: str = "BTC"
    min_dte: int = 14
    max_dte: int = 25
    max_delta: float = 0.4
    margin_ratio: float = 0.2
    option_type: str = "PUT"
    strike: Optional[float] = None
    strike_range: Optional[str] = None

@router.post("/scan")
async def scan_options(params: ScanParams = None):
    """POST /api/scan - 扫描期权 (deprecated)"""
    from main import run_options_scan
    if params is None:
        params = ScanParams()
    return await run_options_scan(params)

@router.post("/quick-scan")
async def quick_scan(params: ScanParams = None):
    """POST /api/quick-scan - 快速扫描"""
    from main import _quick_scan_sync
    if params is None:
        params = ScanParams()
    from models.contracts import QuickScanParams
    quick_params = QuickScanParams(**params.model_dump())
    return _quick_scan_sync(quick_params)

@router.get("/latest")
async def get_latest_scan(currency: str = Query(default="BTC")):
    """GET /api/latest - 获取最新扫描"""
    from main import get_latest_scan as _get_latest
    return await _get_latest(currency)

@router.get("/stats")
async def get_stats():
    """GET /api/stats - 获取统计信息"""
    from main import get_stats as _get_stats
    return await _get_stats()

@router.get("/export/csv")
async def export_csv(currency: str = Query(default="BTC"), hours: int = Query(default=168)):
    """GET /api/export/csv - 导出CSV"""
    from main import export_scan_csv as _export
    return await _export(currency, hours)
