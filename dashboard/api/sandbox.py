"""沙盘推演 API"""
import logging
from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["sandbox"])


class SandboxParams(BaseModel):
    currency: str = Field(default="BTC")
    option_type: str = Field(default="PUT")
    current_strike: float = Field(default=55000)
    current_qty: float = Field(default=1)
    avg_premium: float = Field(default=1000)
    avg_dte: int = Field(default=30)
    crash_price: float = Field(default=45000)
    margin_ratio: float = Field(default=0.2)
    min_dte: int = Field(default=7)
    max_dte: int = Field(default=45)
    min_apr: float = Field(default=15)
    max_contracts: int = Field(default=3)
    reserve_capital: float = Field(default=50000)


@router.post("/sandbox/simulate")
async def sandbox_simulate(params: SandboxParams):
    """沙盘推演：模拟崩盘情景，搜索恢复策略"""
    from services.martingale_sandbox import MartingaleSandboxEngine
    from services.spot_price import get_spot_price

    try:
        spot = get_spot_price(params.currency)
    except (RuntimeError, ValueError) as e:
        logger.warning("Sandbox spot price failed: %s", e)
        spot = 0

    if spot < 1000:
        spot = params.crash_price * 1.5

    opt_type = params.option_type.upper()
    strike = params.current_strike
    qty = params.current_qty

    # Step 1: 计算崩盘损失
    loss_info = MartingaleSandboxEngine.calculate_loss(
        strike=strike, crash_price=params.crash_price, qty=qty,
        avg_premium=params.avg_premium, dte=params.avg_dte, option_type=opt_type
    )

    old_margin = strike * params.margin_ratio * qty

    # Step 2: 搜索恢复候选合约（从最近扫描记录获取）
    all_contracts = []
    try:
        from db.connection import execute_read
        import json
        rows = execute_read(
            "SELECT contracts_data FROM scan_records WHERE currency = ? ORDER BY timestamp DESC LIMIT 1",
            (params.currency,)
        )
        if rows and rows[0][0]:
            all_contracts = json.loads(rows[0][0])
    except (RuntimeError, ValueError, TypeError) as e:
        logger.debug("Sandbox contract fetch failed: %s", e)
        all_contracts = []

    candidates = MartingaleSandboxEngine.search_recovery_candidates(
        contracts=all_contracts, crash_price=params.crash_price, spot=spot,
        margin_ratio=params.margin_ratio, min_dte=params.min_dte, max_dte=params.max_dte,
        min_apr=params.min_apr, max_contracts=params.max_contracts, option_type=opt_type
    )

    # Step 3: 计算每个候选的恢复方案
    recovery_plans = []
    for c in candidates[:10]:
        plan = MartingaleSandboxEngine.calculate_recovery_plan(
            candidate=c, total_loss=loss_info["total_loss"],
            reserve_capital=params.reserve_capital, old_margin=old_margin,
            max_contracts=params.max_contracts
        )
        if plan:
            recovery_plans.append(plan)

    recovery_plans.sort(key=lambda x: x["net_recovery"], reverse=True)
    best_plan = recovery_plans[0] if recovery_plans else None

    # Step 4: 安全评估
    safety = MartingaleSandboxEngine.generate_safety_assessment(
        loss_info=loss_info, reserve_capital=params.reserve_capital, best_plan=best_plan
    )

    return {
        "crash_scenario": {
            "from_price": round(spot, 0),
            "to_price": params.crash_price,
            "drop_pct": round((params.crash_price - spot) / max(spot, 1) * 100, 1),
        },
        "position": {
            "strike": strike,
            "option_type": opt_type,
            "quantity": qty,
            "avg_premium": params.avg_premium,
            "old_margin": round(old_margin, 0),
        },
        "loss_analysis": loss_info,
        "safety_assessment": safety,
        "recovery_plans": recovery_plans[:8],
        "best_plan": best_plan,
        "total_candidates": len(candidates),
        "status": safety["level"],
    }


@router.get("/bottom-fishing/advice")
async def get_bottom_fishing_advice(currency: str = "BTC"):
    """抄底建议"""
    from api.risk import get_risk_overview_sync
    return get_risk_overview_sync(currency)
