"""宏观数据 API"""
import logging
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["macro"])


@router.get("/macro")
async def get_macro(currency: str = "BTC"):
    """获取宏观数据（恐惧贪婪指数、资金费率、美股宏观数据、现货价格）"""
    from services.macro_data import get_all_macro_data
    from services.spot_price import get_spot_price
    from services.dvol_analyzer import get_dvol_from_deribit
    from services.risk_framework import RiskFramework
    from db.connection import execute_read
    import json

    result = await run_in_threadpool(get_all_macro_data)

    # 添加现货价格
    try:
        spot = await run_in_threadpool(get_spot_price, currency)
        result["spot_price"] = spot
    except (RuntimeError, ValueError) as e:
        logger.warning("Macro spot price failed: %s", e)
        result["spot_price"] = None

    # 添加 DVOL 数据
    try:
        dvol = await run_in_threadpool(get_dvol_from_deribit, currency)
        if isinstance(dvol, dict):
            result["dvol_current"] = dvol.get("current", 0)
            result["dvol_z_score"] = dvol.get("z_score", 0)
            result["dvol_signal"] = dvol.get("signal", "")
            result["dvol_trend"] = dvol.get("trend", "")
            result["dvol_trend_label"] = dvol.get("trend_label", "")
            result["dvol_confidence"] = dvol.get("confidence", "")
            result["dvol_interpretation"] = dvol.get("interpretation", "")
    except (RuntimeError, ConnectionError, TimeoutError) as e:
        logger.warning("Macro DVOL failed: %s", e)
        result["dvol_current"] = 0
        result["dvol_z_score"] = 0
        result["dvol_signal"] = ""

    # 添加合约数量
    try:
        rows = await run_in_threadpool(execute_read, """
            SELECT contracts_data FROM scan_records
            WHERE currency = ? ORDER BY timestamp DESC LIMIT 1
        """, (currency,))
        if rows and rows[0][0]:
            contracts = json.loads(rows[0][0])
            result["contracts_count"] = len(contracts)
        else:
            result["contracts_count"] = 0
    except (json.JSONDecodeError, RuntimeError) as e:
        logger.warning("Macro contracts count failed: %s", e)
        result["contracts_count"] = 0

    # 添加风险等级
    try:
        result["risk_level"] = RiskFramework.get_status(result.get("spot_price", 0))
    except (ValueError, TypeError) as e:
        logger.warning("Macro risk level failed: %s", e)
        result["risk_level"] = "unknown"

    result["success"] = True
    result["currency"] = currency

    return result


@router.get("/macro-data")
async def get_macro_data(currency: str = "BTC"):
    """获取宏观数据（兼容旧端点）"""
    return await get_macro(currency)
