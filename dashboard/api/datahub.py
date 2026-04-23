"""DataHub & EventBus API"""
from datetime import datetime
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api", tags=["datahub"])


@router.get("/datahub/status")
async def get_datahub_status():
    """获取 DataHub WebSocket 连接状态"""
    from services.datahub import datahub, TOPIC_SPOT, TOPIC_DVOL, TOPIC_BTC_OPTIONS

    spot_snapshot = datahub.get_snapshot(TOPIC_SPOT, "BTC")
    dvol_snapshot = datahub.get_snapshot(TOPIC_DVOL, "BTC")
    options_snapshot = datahub.get_snapshot(TOPIC_BTC_OPTIONS)

    return {
        "status": "running" if datahub._running else "stopped",
        "spot_price": {
            "price": spot_snapshot.get("price") if spot_snapshot else None,
            "age_seconds": round(datahub.get_snapshot_age(TOPIC_SPOT), 1)
        },
        "dvol": {
            "current": dvol_snapshot.get("current") if dvol_snapshot else None,
            "age_seconds": round(datahub.get_snapshot_age(TOPIC_DVOL), 1)
        },
        "options_cache": {
            "btc_count": len(options_snapshot) if options_snapshot else 0,
            "age_seconds": round(datahub.get_snapshot_age(TOPIC_BTC_OPTIONS), 1)
        },
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/datahub/options-chain")
async def get_datahub_options_chain(currency: str = "BTC"):
    """
    从 DataHub 获取期权链（<10ms 响应）
    这是 quick_scan 的超快版本，直接从 WebSocket 缓存读取
    """
    from services.datahub import datahub, TOPIC_BTC_OPTIONS, TOPIC_ETH_OPTIONS

    topic = TOPIC_BTC_OPTIONS if currency == "BTC" else TOPIC_ETH_OPTIONS
    snapshot = datahub.get_snapshot(topic)

    if not snapshot:
        raise HTTPException(status_code=404, detail="期权链数据尚未就绪，请稍候重试")

    age = datahub.get_snapshot_age(topic)

    contracts = []
    for symbol, opt_data in snapshot.items():
        mark_price = opt_data.get("mark_price", 0)
        if mark_price <= 0:
            continue

        contracts.append({
            "symbol": symbol,
            "mark_price": mark_price,
            "iv": opt_data.get("iv", 0),
            "delta": opt_data.get("delta", 0),
            "gamma": opt_data.get("gamma", 0),
            "theta": opt_data.get("theta", 0),
            "vega": opt_data.get("vega", 0),
            "best_bid": opt_data.get("best_bid", 0),
            "best_ask": opt_data.get("best_ask", 0),
            "volume": opt_data.get("volume", 0),
            "open_interest": opt_data.get("open_interest", 0),
            "timestamp": opt_data.get("timestamp", "")
        })

    return {
        "currency": currency,
        "count": len(contracts),
        "data_age_seconds": round(age, 1),
        "contracts": sorted(contracts, key=lambda x: x.get("delta", 0), reverse=True)
    }


@router.get("/eventbus/snapshot")
async def get_eventbus_snapshot():
    """获取事件总线当前快照数据"""
    from services.event_bus import event_bus
    return {
        "snapshots": event_bus.get_all_snapshots(),
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/eventbus/history")
async def get_eventbus_history(
    event_type: str = "",
    limit: int = 50
):
    """获取事件历史"""
    from services.event_bus import event_bus, EventType

    et = None
    if event_type:
        try:
            et = EventType(event_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的事件类型: {event_type}")

    return {
        "events": event_bus.get_event_history(event_type=et, limit=limit),
        "count": limit
    }


@router.get("/eventbus/spot-price")
async def get_spot_price_from_bus(currency: str = "BTC"):
    """从事件总线获取最新现货价格（毫秒级响应）"""
    from services.event_bus import event_bus, EventType

    snapshot = event_bus.get_snapshot(EventType.SPOT_PRICE)
    if not snapshot:
        raise HTTPException(status_code=404, detail="现货价格数据尚未就绪")

    if snapshot.get("currency") != currency:
        raise HTTPException(status_code=404, detail=f"未找到 {currency} 的现货价格")

    age = event_bus.get_snapshot_age(EventType.SPOT_PRICE)
    return {
        "currency": currency,
        "price": snapshot.get("price"),
        "timestamp": snapshot.get("timestamp"),
        "data_age_seconds": round(age, 1)
    }


@router.get("/eventbus/dvol")
async def get_dvol_from_bus(currency: str = "BTC"):
    """从事件总线获取最新 DVOL（毫秒级响应）"""
    from services.event_bus import event_bus, EventType

    snapshot = event_bus.get_snapshot(EventType.DVOL)
    if not snapshot:
        raise HTTPException(status_code=404, detail="DVOL 数据尚未就绪")

    if snapshot.get("currency") != currency:
        raise HTTPException(status_code=404, detail=f"未找到 {currency} 的 DVOL")

    age = event_bus.get_snapshot_age(EventType.DVOL)
    return {
        "currency": currency,
        "dvol": snapshot.get("dvol"),
        "timestamp": snapshot.get("timestamp"),
        "data_age_seconds": round(age, 1)
    }
