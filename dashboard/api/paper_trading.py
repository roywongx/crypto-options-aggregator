"""模拟盘交易 API"""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/paper", tags=["paper_trading"])


@router.get("/portfolio")
async def get_paper_portfolio(currency: str = "BTC"):
    """获取模拟盘组合概览"""
    from services.paper_trading import get_portfolio_summary, init_paper_trading_db
    init_paper_trading_db()
    return get_portfolio_summary(currency)


@router.get("/trades")
async def get_paper_trades(currency: str = "BTC", limit: int = 50):
    """获取模拟盘历史交易"""
    from services.paper_trading import get_trade_history, init_paper_trading_db
    init_paper_trading_db()
    return get_trade_history(currency, limit)


@router.post("/open")
async def paper_open(
    currency: str = "BTC",
    option_type: str = "PUT",
    strike: float = 55000,
    qty: float = 1,
    premium: float = 1000,
    expiry: str = "",
    margin_ratio: float = 0.2,
    notes: str = ""
):
    """模拟开仓"""
    from services.paper_trading import paper_open_position, init_paper_trading_db
    init_paper_trading_db()
    return paper_open_position(currency, option_type, strike, qty, premium, expiry, margin_ratio, notes)


@router.post("/close")
async def paper_close(position_id: int, close_premium: float, notes: str = ""):
    """模拟平仓"""
    from services.paper_trading import paper_close_position, init_paper_trading_db
    init_paper_trading_db()
    return paper_close_position(position_id, close_premium, notes)


@router.get("/roll-suggestion")
async def get_roll_suggestion(position_id: int):
    """获取滚仓建议"""
    from services.paper_trading import get_roll_suggestion, init_paper_trading_db
    init_paper_trading_db()
    return get_roll_suggestion(position_id)
