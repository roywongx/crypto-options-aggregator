"""交易所抽象层 API"""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/exchanges", tags=["exchanges"])


@router.get("/list")
async def list_exchanges():
    """获取已注册的交易所列表"""
    from services.exchange_abstraction import registry
    return {"exchanges": registry.list_exchanges()}


@router.get("/chain")
async def get_exchange_chain(
    exchange: str = "binance",
    currency: str = "BTC",
    option_type: str = "PUT",
    min_dte: int = 5,
    max_dte: int = 45,
    max_delta: float = 0.6,
    min_volume: float = 0,
    max_spread_pct: float = 20.0
):
    """通过统一接口获取期权链"""
    from services.exchange_abstraction import registry, ExchangeType, OptionType

    try:
        ex_type = ExchangeType(exchange.lower())
        exchange_adapter = registry.get(ex_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"不支持的交易所: {exchange}")

    try:
        opt_type = OptionType(option_type.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的 option_type: {option_type}")

    chain = await exchange_adapter.get_options_chain(
        currency=currency,
        option_type=opt_type,
        min_dte=min_dte,
        max_dte=max_dte,
        max_delta=max_delta,
        min_volume=min_volume,
        max_spread_pct=max_spread_pct
    )

    return {
        "exchange": exchange,
        "currency": currency,
        "option_type": option_type,
        "count": len(chain),
        "contracts": [c.to_dict() for c in chain]
    }


@router.get("/multi-chain")
async def get_multi_exchange_chain(
    currency: str = "BTC",
    option_type: str = "PUT",
    min_dte: int = 5,
    max_dte: int = 45,
    max_delta: float = 0.6
):
    """同时获取多个交易所的期权链"""
    from services.exchange_abstraction import registry, OptionType

    try:
        opt_type = OptionType(option_type.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的 option_type: {option_type}")

    summary = await registry.get_multi_exchange_summary(
        currency=currency,
        option_type=opt_type,
        min_dte=min_dte,
        max_dte=max_dte,
        max_delta=max_delta
    )

    return summary


@router.get("/dvol")
async def get_exchange_dvol(currency: str = "BTC", exchange: str = "deribit"):
    """获取指定交易所的 DVOL"""
    from services.exchange_abstraction import registry, ExchangeType

    try:
        ex_type = ExchangeType(exchange.lower())
        exchange_adapter = registry.get(ex_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"不支持的交易所: {exchange}")

    dvol = await exchange_adapter.get_dvol(currency)
    return {"exchange": exchange, "currency": currency, "dvol": dvol}
