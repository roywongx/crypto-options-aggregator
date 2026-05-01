"""模拟盘交易 API

修复 H-5: 添加 Pydantic 输入约束 (gt=0 等)
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/paper", tags=["paper_trading"])


class PaperOpenRequest(BaseModel):
    currency: str = "BTC"
    option_type: str = "PUT"
    strike: float = Field(gt=0)
    qty: float = Field(gt=0)
    premium: float = Field(gt=0)
    expiry: str = ""
    margin_ratio: float = Field(default=0.2, gt=0, le=1.0)
    notes: str = ""


class PaperCloseRequest(BaseModel):
    position_id: int = Field(gt=0)
    close_premium: float = Field(gt=0)
    notes: str = ""


@router.get("/portfolio")
async def get_paper_portfolio(currency: str = "BTC"):
    """获取模拟盘组合概览"""
    from services.paper_trading import get_portfolio_summary
    return get_portfolio_summary(currency)


@router.get("/trades")
async def get_paper_trades(currency: str = "BTC", limit: int = 50):
    """获取模拟盘历史交易"""
    from services.paper_trading import get_trade_history
    return get_trade_history(currency, limit)


@router.post("/open")
async def paper_open(req: PaperOpenRequest):
    """模拟开仓"""
    from services.paper_trading import paper_open_position
    return paper_open_position(
        req.currency, req.option_type, req.strike, req.qty,
        req.premium, req.expiry, req.margin_ratio, req.notes
    )


@router.post("/close")
async def paper_close(req: PaperCloseRequest):
    """模拟平仓"""
    from services.paper_trading import paper_close_position
    return paper_close_position(req.position_id, req.close_premium, req.notes)


@router.get("/roll-suggestion")
async def get_roll_suggestion(position_id: int):
    """获取滚仓建议"""
    from services.paper_trading import get_roll_suggestion
    return get_roll_suggestion(position_id)
