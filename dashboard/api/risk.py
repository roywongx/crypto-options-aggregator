"""风险评估 API"""
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api", tags=["risk"])


def get_risk_overview_sync(currency: str = "BTC"):
    """同步版本的风险评估（供其他模块调用）"""
    from services.risk_framework import RiskFramework
    from services.spot_price import get_spot_price
    from services.unified_risk_assessor import UnifiedRiskAssessor

    spot = get_spot_price(currency)
    status = RiskFramework.get_status(spot)
    floors = RiskFramework._get_floors()

    assessor = UnifiedRiskAssessor()
    risk_data = assessor.assess_comprehensive_risk(spot, currency)

    advice = []
    actions = []

    if status == "NORMAL":
        advice.append(f"当前价格 ${spot:,.0f} 处于常规区间。")
        advice.append("建议：以获取稳定 APR 为目标，保持低杠杆。")
        actions.append("卖出 OTM Put (Delta 0.15-0.25)")
    elif status == "NEAR_FLOOR":
        advice.append(f"当前价格 ${spot:,.0f} 接近常规底 ${floors['regular']:,.0f}。")
        advice.append("建议：可适当增加仓位，博取高 Theta 收益。")
        actions.append("卖出 ATM/ITM Put 并准备滚仓")
    elif status == "ADVERSE":
        advice.append(f"市场处于逆境区 (${spot:,.0f} < ${floors['regular']:,.0f})。")
        advice.append("建议：启用后备资金，高杠杆快平仓，积极执行 Rolling Down & Out。")
        actions.append("将持仓滚动至支撑区间")
    elif status == "PANIC":
        advice.append(f"⚠️ 警告：价格已破极限底 ${floors['extreme']:,.0f}！")
        advice.append("核心指令：止损并保留本金。不要在此区域接货。")
        actions.append("平掉所有 Put 仓位，保持现金")

    position_guidance = {
        "NORMAL": {"max_position_pct": 30, "suggested_delta_range": "0.15-0.25", "suggested_dte": "14-35"},
        "NEAR_FLOOR": {"max_position_pct": 40, "suggested_delta_range": "0.20-0.35", "suggested_dte": "7-28"},
        "ADVERSE": {"max_position_pct": 15, "suggested_delta_range": "0.10-0.20", "suggested_dte": "14-45"},
        "PANIC": {"max_position_pct": 0, "suggested_delta_range": "N/A", "suggested_dte": "N/A"}
    }
    pos_guide = position_guidance.get(status, position_guidance["NORMAL"])

    return {
        "currency": currency,
        "spot": spot,
        "status": status,
        "composite_score": risk_data["composite_score"],
        "risk_level": risk_data["risk_level"],
        "components": risk_data["components"],
        "recommendations": risk_data["recommendations"],
        "floors": floors,
        "advice": advice,
        "recommended_actions": actions,
        "position_guidance": pos_guide,
        "timestamp": risk_data["timestamp"]
    }


@router.get("/risk/assess")
async def get_risk_assessment(currency: str = Query(default="BTC")):
    """风险评估"""
    return get_risk_overview_sync(currency)


@router.get("/risk/overview")
async def get_risk_overview(currency: str = Query(default="BTC")):
    """统一风险中枢 - 合并风险评估与抄底建议"""
    return get_risk_overview_sync(currency)
