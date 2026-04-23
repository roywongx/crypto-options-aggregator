"""宏观数据 API"""
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["macro"])


@router.get("/macro")
async def get_macro(currency: str = "BTC"):
    """获取宏观数据（恐惧贪婪指数、资金费率、美股宏观数据）"""
    from services.macro_data import get_all_macro_data
    return get_all_macro_data()


@router.get("/macro-data")
async def get_macro_data(currency: str = "BTC"):
    """获取宏观数据（兼容旧端点）"""
    from services.macro_data import get_all_macro_data
    return get_all_macro_data()
