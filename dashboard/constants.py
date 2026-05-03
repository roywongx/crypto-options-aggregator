"""
Crypto Options Dashboard - 常量定义
集中管理所有硬编码默认值，避免多处散布
"""

import os

# 现货价格回退值（仅当 API 和数据库都不可用时使用）
# 可通过环境变量覆盖: DASHBOARD_SPOT_BTC, DASHBOARD_SPOT_ETH, DASHBOARD_SPOT_SOL
DEFAULT_SPOT_FALLBACK = {
    "BTC": float(os.getenv("DASHBOARD_SPOT_BTC", "83000")),
    "ETH": float(os.getenv("DASHBOARD_SPOT_ETH", "3500")),
    "SOL": float(os.getenv("DASHBOARD_SPOT_SOL", "150")),
}

def get_spot_fallback(currency: str) -> float:
    """获取指定币种的现货价格回退值（仅在无实时价格时使用）"""
    return DEFAULT_SPOT_FALLBACK.get(currency.upper(), 83000.0)


def get_dynamic_spot_price(currency: str, fallback: float = None) -> float:
    """
    获取动态现货价格（带多级回退机制）
    
    优先级：
    1. API 实时获取（get_spot_price）
    2. 数据库最近一次扫描记录的 spot_price
    3. 硬编码回退值（DEFAULT_SPOT_FALLBACK）
    
    参数:
        currency: 币种代码 (BTC/ETH/SOL)
        fallback: 自定义回退值（可选）
    
    返回:
        现货价格
    """
    from services.spot_price import get_spot_price
    
    # 第一优先级：API 实时获取
    spot = get_spot_price(currency)
    if spot and spot > 0:
        return spot
    
    # 第二优先级：从数据库获取最近一次扫描价格
    try:
        from db.connection import execute_read
        rows = execute_read("""
            SELECT spot_price FROM scan_records 
            WHERE currency = ? AND spot_price > 0
            ORDER BY timestamp DESC LIMIT 1
        """, (currency,))
        if rows and rows[0][0]:
            return float(rows[0][0])
    except (ValueError, TypeError, ZeroDivisionError, RuntimeError) as e:
        import logging
        logging.getLogger(__name__).debug("database spot price fallback failed: %s", str(e))
    
    # 第三优先级：使用回退值
    if fallback:
        return fallback
    return get_spot_fallback(currency)
