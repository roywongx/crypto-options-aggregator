"""扫描相关 API"""
import json
import io
import csv
import logging
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from db.connection import execute_read
from services.spot_price import get_spot_price
from services.risk_framework import RiskFramework

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["scan"])


def _get_spot_safe(currency: str) -> float:
    """安全获取现货价格，失败时返回0"""
    try:
        return get_spot_price(currency)
    except (RuntimeError, ValueError) as e:
        logger.warning("Spot price fetch failed for %s: %s", currency, e)
        return 0


class ScanParams(BaseModel):
    currency: str = Field(default="BTC", description="交易对")
    option_type: str = Field(default="PUT", description="期权类型")
    min_dte: int = Field(default=14, ge=1, le=90)
    max_dte: int = Field(default=35, ge=1, le=90)
    max_delta: float = Field(default=0.5, ge=0.01, le=0.99)
    strike: float = Field(default=None)
    margin_ratio: float = Field(default=0.2, ge=0.05, le=1.0)
    min_volume: float = Field(default=0.0)
    max_spread: float = Field(default=20.0)
    
    
class QuickScanParams(BaseModel):
    currency: str = Field(default="BTC")
    option_type: str = Field(default="PUT")
    min_dte: int = Field(default=5, ge=1)
    max_dte: int = Field(default=45, ge=1)
    max_delta: float = Field(default=0.6)
    strike: float = Field(default=None)
    margin_ratio: float = Field(default=0.2)


@router.post("/scan")
async def scan_options(params: ScanParams):
    """执行期权扫描"""
    from fastapi.concurrency import run_in_threadpool
    from services.scan_engine import run_options_scan

    result = await run_in_threadpool(run_options_scan, params)
    if not result.get('success'):
        raise HTTPException(status_code=500, detail=result.get('error', '扫描失败'))
    return result


@router.post("/quick-scan")
async def quick_scan_endpoint(params: QuickScanParams = None):
    """快速扫描端点"""
    from services.scan_engine import quick_scan
    return await quick_scan(params)


@router.get("/latest")
async def get_latest(currency: str = Query(default="BTC")):
    """获取最新扫描数据"""
    from db.connection import get_db_connection
    
    conn = get_db_connection(read_only=True)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, spot_price, dvol_current, dvol_z_score, dvol_signal,
               large_trades_count, large_trades_details, 
               COALESCE(top_contracts_data, contracts_data) as fast_contracts,
               contracts_data, raw_output
        FROM scan_records WHERE currency = ? ORDER BY timestamp DESC LIMIT 1
    """, (currency,))
    row = cursor.fetchone()

    if not row:
        return {
            "success": False,
            "currency": currency,
            "spot_price": _get_spot_safe(currency),
            "contracts": [],
            "large_trades_details": [],
            "large_trades_count": 0,
            "timestamp": None,
            "message": "暂无扫描数据，请先执行扫描"
        }

    try:
        contracts = json.loads(row[7]) if row[7] else []
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse contracts JSON: %s", e)
        contracts = []

    try:
        large_trades = json.loads(row[6]) if row[6] else []
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse large_trades JSON: %s", e)
        large_trades = []

    raw = {}
    if row[9]:
        try:
            raw = json.loads(row[9])
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse raw JSON: %s", e)
            raw = {}

    floors = RiskFramework._get_floors()
    regular_floor = floors.get("regular", 0)
    margin_ratio = 0.20

    for c in contracts:
        if c.get("margin_required") is None:
            strike = c.get("strike", 0)
            prem = c.get("premium_usd", 0) or c.get("premium", 0)
            c["margin_required"] = round(max(strike * 0.1, (strike - prem) * margin_ratio), 2)
        if c.get("capital_efficiency") is None:
            prem = c.get("premium_usd", 0) or c.get("premium", 0)
            margin = c.get("margin_required", 1)
            c["capital_efficiency"] = round(prem / margin * 100, 1) if margin > 0 else 0
        if c.get("support_distance_pct") is None and c.get("option_type") in ("P", "PUT") and regular_floor > 0:
            c["support_distance_pct"] = round((c.get("strike", 0) - regular_floor) / regular_floor * 100, 1)

    return {
        "success": True,
        "currency": currency,
        "spot_price": row[1] or _get_spot_safe(currency),
        "dvol_current": row[2] or 0,
        "dvol_z_score": row[3] or 0,
        "dvol_signal": row[4] or '',
        "dvol_interpretation": raw.get("interpretation", ""),
        "dvol_trend": raw.get("trend", ""),
        "dvol_trend_label": raw.get("trend_label", ""),
        "dvol_confidence": raw.get("confidence", ""),
        "dvol_percentile_7d": raw.get("percentile_7d", 50),
        "contracts": contracts,
        "large_trades_details": large_trades,
        "large_trades_count": row[5] or 0,
        "timestamp": row[0]
    }


@router.get("/export/csv")
async def export_csv(currency: str = Query(default="BTC"), hours: int = Query(default=168)):
    """导出 CSV"""
    rows = execute_read("""
        SELECT contracts_data FROM scan_records
        WHERE currency = ? AND timestamp > datetime('now', ? || ' hours')
        ORDER BY timestamp DESC
    """, (currency, str(-hours)))
    
    if not rows or not rows[0][0]:
        return JSONResponse(content={"error": "No data available"}, status_code=404)
    
    try:
        contracts = json.loads(rows[0][0])
    except json.JSONDecodeError as e:
        logger.error("CSV export data parse error: %s", e)
        return JSONResponse(content={"error": "Data parse error"}, status_code=500)
    
    output = io.StringIO()
    if contracts:
        writer = csv.DictWriter(output, fieldnames=contracts[0].keys())
        writer.writeheader()
        writer.writerows(contracts)
    
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=options_{currency}_{hours}h.csv"}
    )
