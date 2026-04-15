from fastapi import APIRouter, Query
from typing import List, Optional
from pydantic import BaseModel
import json

from services.grid_engine import (
    recommend_grid, get_vol_direction_signal,
    simulate_scenario, calculate_heatmap_data,
    calculate_grid_levels
)
from models.grid import GridDirection

router = APIRouter(prefix="/api/grid", tags=["grid"])

class ScenarioRequest(BaseModel):
    currency: str = "BTC"
    grid_levels: List[dict]
    target_prices: List[float]
    position_size: float = 1.0

def _get_contracts_from_db(currency: str):
    """从数据库获取最近的合约数据"""
    try:
        from db.connection import get_db_connection

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT contracts_data FROM scan_records
            WHERE currency = ? AND timestamp > datetime('now', '-1 day')
            ORDER BY timestamp DESC LIMIT 1
        """, (currency,))
        row = cursor.fetchone()

        if row and row[0]:
            try:
                contracts = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                return contracts
            except Exception as e:
                import sys
                print(f"[WARN] _get_contracts_from_db: parse error: {e}", file=sys.stderr)
                return []
        return []
    except Exception as e:
        import sys
        print(f"[ERROR] _get_contracts_from_db: {e}", file=sys.stderr)
        return []

@router.get("/recommend")
async def get_grid_recommend(
    currency: str = Query("BTC", pattern="^(BTC|ETH|SOL)$"),
    put_count: int = Query(5, ge=1, le=10),
    call_count: int = Query(3, ge=1, le=10),
    min_dte: int = Query(7, ge=1, le=90),
    max_dte: int = Query(45, ge=1, le=180),
    min_apr: float = Query(15.0, ge=0, le=200),
    use_smart: bool = Query(False, description="是否使用智能推荐")
):
    try:
        from main import get_spot_price

        spot_price = get_spot_price(currency)
        if not spot_price or spot_price <= 0:
            spot_price = 83000.0

        contracts = _get_contracts_from_db(currency)

        # 智能推荐模式：根据市场状态自动调整参数
        if use_smart:
            vol_signal = get_vol_direction_signal(contracts, currency)
            dvol_pct = vol_signal.dvol_percentile
            
            # 根据 DVOL 分位数调整参数
            if dvol_pct > 70:
                # 高波动环境：激进策略
                min_dte = max(7, min_dte)
                max_dte = max(45, max_dte)
                min_apr = max(10.0, min_apr - 5)
                put_count = min(put_count + 2, 10)
                call_count = min(call_count + 2, 10)
            elif dvol_pct < 30:
                # 低波动环境：保守策略
                min_dte = max(14, min_dte)
                max_dte = min(30, max_dte)
                min_apr = max(20.0, min_apr + 5)
                put_count = max(3, put_count - 2)
                call_count = max(2, call_count - 1)
            
            # 根据波动方向调整 Put/Call 比例
            if vol_signal.signal == "FAVOR_PUT":
                put_count = max(put_count, call_count + 2)
            elif vol_signal.signal == "FAVOR_CALL":
                call_count = max(call_count, put_count - 1)

        recommendation = recommend_grid(
            contracts=contracts,
            currency=currency,
            spot_price=spot_price,
            put_count=put_count,
            call_count=call_count,
            min_dte=min_dte,
            max_dte=max_dte,
            min_apr=min_apr
        )

        return {
            "currency": recommendation.currency,
            "spot_price": recommendation.spot_price,
            "timestamp": recommendation.timestamp,
            "smart_mode": use_smart,
            "market_context": {
                "dvol_percentile": vol_signal.dvol_percentile if use_smart else None,
                "vol_signal": vol_signal.signal if use_smart else None,
                "adjusted_reason": vol_signal.reason if use_smart else None
            },
            "put_levels": [
                {
                    "direction": l.direction.value,
                    "strike": l.strike,
                    "expiry": l.expiry,
                    "dte": l.dte,
                    "premium_usd": l.premium_usd,
                    "apr": l.apr,
                    "distance_pct": round(l.distance_pct, 2),
                    "iv": l.iv,
                    "delta": l.delta,
                    "oi": l.oi,
                    "volume": l.volume,
                    "liquidity_score": round(l.liquidity_score, 2),
                    "recommendation": l.recommendation.name,
                    "reason": l.reason,
                    "suggested_position_pct": _calc_suggested_position(l)
                }
                for l in recommendation.put_levels
            ],
            "call_levels": [
                {
                    "direction": l.direction.value,
                    "strike": l.strike,
                    "expiry": l.expiry,
                    "dte": l.dte,
                    "premium_usd": l.premium_usd,
                    "apr": l.apr,
                    "distance_pct": round(l.distance_pct, 2),
                    "iv": l.iv,
                    "delta": l.delta,
                    "oi": l.oi,
                    "volume": l.volume,
                    "liquidity_score": round(l.liquidity_score, 2),
                    "recommendation": l.recommendation.name,
                    "reason": l.reason,
                    "suggested_position_pct": _calc_suggested_position(l)
                }
                for l in recommendation.call_levels
            ],
            "dvol_signal": recommendation.dvol_signal,
            "recommended_ratio": recommendation.recommended_ratio,
            "total_potential_premium": recommendation.total_potential_premium,
            "strategy_advice": _generate_strategy_advice(recommendation, use_smart)
        }
    except Exception as e:
        import sys
        print(f"[ERROR] /api/grid/recommend: {e}", file=sys.stderr)
        return {"error": str(e)}

def _calc_suggested_position(level) -> int:
    """根据合约评分计算建议仓位百分比"""
    score = level.recommendation
    if score == "BEST":
        return 20
    elif score == "GOOD":
        return 15
    elif score == "OK":
        return 10
    elif score == "CAUTION":
        return 5
    return 0

def _generate_strategy_advice(recommendation, use_smart: bool) -> dict:
    """生成策略建议"""
    total_premium = recommendation.total_potential_premium
    put_count = len(recommendation.put_levels)
    call_count = len(recommendation.call_levels)
    
    # 计算平均 APR 和 DTE
    all_levels = recommendation.put_levels + recommendation.call_levels
    avg_apr = sum(l.apr for l in all_levels) / len(all_levels) if all_levels else 0
    avg_dte = sum(l.dte for l in all_levels) / len(all_levels) if all_levels else 0
    
    # 根据市场状态生成建议
    if use_smart:
        advice_text = f"智能推荐模式已启用。当前 DVOL 分位{recommendation.dvol_signal}，建议{recommendation.recommended_ratio}配置。"
    else:
        advice_text = f"手动配置模式。共推荐{put_count + call_count}个合约，总权利金${total_premium:,.0f}。"
    
    return {
        "summary": advice_text,
        "key_metrics": {
            "avg_apr": round(avg_apr, 1),
            "avg_dte": round(avg_dte, 0),
            "total_premium": round(total_premium, 2),
            "put_count": put_count,
            "call_count": call_count
        },
        "action_items": [
            "根据建议仓位分配资金",
            "设置价格提醒，关注关键支撑/阻力位",
            "定期检查合约状态，适时调整",
            "注意风险控制，避免过度杠杆"
        ],
        "risk_warnings": [
            "价格突破行权价可能被行权",
            "极端行情下亏损可能放大",
            "注意到期日管理，避免遗忘"
        ]
    }

@router.get("/vol-direction")
async def get_vol_direction(
    currency: str = Query("BTC", pattern="^(BTC|ETH|SOL)$")
):
    try:
        contracts = _get_contracts_from_db(currency)
        signal = get_vol_direction_signal(contracts, currency)

        return {
            "dvol_current": signal.dvol_current,
            "dvol_30d_avg": signal.dvol_30d_avg,
            "dvol_percentile": signal.dvol_percentile,
            "skew": signal.skew,
            "signal": signal.signal,
            "reason": signal.reason,
            "suggested_ratio": signal.suggested_ratio
        }
    except Exception as e:
        import sys
        print(f"[ERROR] /api/grid/vol-direction: {e}", file=sys.stderr)
        return {"error": str(e)}

@router.post("/scenario")
async def post_grid_scenario(request: ScenarioRequest):
    try:
        from services.grid_engine import GridLevel, GridDirection as GD

        grid_levels = []
        for gl in request.grid_levels:
            direction = GD.PUT if gl.get("direction", "").lower() == "put" else GD.CALL
            grid_levels.append(GridLevel(
                direction=direction,
                strike=float(gl.get("strike", 0)),
                expiry=gl.get("expiry", ""),
                dte=int(gl.get("dte", 0)),
                premium_usd=float(gl.get("premium_usd", 0)),
                apr=float(gl.get("apr", 0)),
                distance_pct=float(gl.get("distance_pct", 0)),
                iv=float(gl.get("iv", 0)),
                delta=float(gl.get("delta", 0)),
                oi=int(gl.get("oi", 0)),
                volume=int(gl.get("volume", 0)),
                liquidity_score=float(gl.get("liquidity_score", 0)),
                recommendation=None,
                reason=""
            ))

        results = []
        for target_price in request.target_prices:
            result = simulate_scenario(
                grid_levels=grid_levels,
                spot_price=83000.0,
                target_price=target_price,
                position_size=request.position_size
            )
            results.append(result)

        return {"scenarios": results}
    except Exception as e:
        import sys
        print(f"[ERROR] /api/grid/scenario: {e}", file=sys.stderr)
        return {"error": str(e)}

@router.get("/heatmap")
async def get_grid_heatmap(
    currency: str = Query("BTC", pattern="^(BTC|ETH|SOL)$")
):
    try:
        from main import get_spot_price

        spot_price = get_spot_price(currency)
        if not spot_price or spot_price <= 0:
            spot_price = 83000.0

        contracts = _get_contracts_from_db(currency)

        put_levels = calculate_grid_levels(
            contracts, spot_price, GridDirection.PUT, 5, 7, 45, 15.0
        )
        call_levels = calculate_grid_levels(
            contracts, spot_price, GridDirection.CALL, 3, 7, 45, 15.0
        )

        heatmap = calculate_heatmap_data(contracts, spot_price, put_levels, call_levels)

        return {
            "spot_price": spot_price,
            "heatmap": heatmap
        }
    except Exception as e:
        import sys
        print(f"[ERROR] /api/grid/heatmap: {e}", file=sys.stderr)
        return {"error": str(e)}

@router.get("/revenue-summary")
async def get_revenue_summary(
    currency: str = Query("BTC", pattern="^(BTC|ETH|SOL)$"),
    days: int = Query(30, ge=1, le=365)
):
    try:
        from db.connection import get_db_connection

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT timestamp, contracts_data FROM scan_records
            WHERE currency = ? AND timestamp > datetime('now', ?||' days')
            ORDER BY timestamp ASC
        """, (currency, -days))

        rows = cursor.fetchall()

        total_premium = 0.0
        scan_count = len(rows)

        for row in rows:
            if row[1]:
                try:
                    contracts = json.loads(row[1]) if isinstance(row[1], str) else row[1]
                    for c in contracts:
                        premium = float(c.get("premium_usd", c.get("premium", 0)))
                        total_premium += premium
                except Exception as e:
                    import sys
                    print(f"[WARN] revenue-summary: parse error: {e}", file=sys.stderr)
                    continue

        avg_daily_premium = total_premium / max(1, scan_count)
        annualized_premium = avg_daily_premium * 365
        annualized_rate = (annualized_premium / 83000.0) * 100

        return {
            "currency": currency,
            "period_days": days,
            "total_premium": round(total_premium, 2),
            "avg_daily_premium": round(avg_daily_premium, 2),
            "annualized_premium": round(annualized_premium, 2),
            "annualized_rate_pct": round(annualized_rate, 2),
            "scan_count": scan_count
        }
    except Exception as e:
        import sys
        print(f"[ERROR] /api/grid/revenue-summary: {e}", file=sys.stderr)
        return {"error": str(e)}


@router.get("/presets")
async def get_grid_presets():
    """获取网格策略预设配置（增强版）"""
    return {
        "presets": [
            {
                "id": "conservative",
                "name": "保守型",
                "description": "低风险，稳定收益 - 适合震荡市",
                "put_count": 3,
                "call_count": 2,
                "min_dte": 14,
                "max_dte": 30,
                "min_apr": 20.0,
                "suggested_position": "30-50%",
                "features": ["安全距离充足", "流动性好", "Theta 衰减快"]
            },
            {
                "id": "balanced",
                "name": "均衡型",
                "description": "平衡风险与收益 - 适合温和波动",
                "put_count": 5,
                "call_count": 3,
                "min_dte": 7,
                "max_dte": 45,
                "min_apr": 15.0,
                "suggested_position": "50-70%",
                "features": ["收益适中", "分散风险", "灵活调整"]
            },
            {
                "id": "aggressive",
                "name": "激进型",
                "description": "高风险，高收益 - 适合高波动环境",
                "put_count": 7,
                "call_count": 5,
                "min_dte": 7,
                "max_dte": 60,
                "min_apr": 10.0,
                "suggested_position": "70-100%",
                "features": ["权利金收入高", "覆盖范围广", "需密切监控"]
            },
            {
                "id": "smart",
                "name": "智能推荐",
                "description": "根据当前市场状态自动调整参数",
                "auto_adjust": True,
                "features": ["动态调整", "市场适应", "最优配置"]
            }
        ],
        "parameter_guide": {
            "min_dte": {
                "label": "最短到期天数",
                "description": "小于此天数的合约不会被推荐",
                "suggested_range": "7-21 天",
                "tips": "较短 DTE Theta 衰减快，但风险较高；较长 DTE 权利金高，但资金占用时间长"
            },
            "max_dte": {
                "label": "最长到期天数",
                "description": "大于此天数的合约不会被推荐",
                "suggested_range": "30-60 天",
                "tips": "限制最大 DTE 可避免资金长期占用，提高资金利用率"
            },
            "min_apr": {
                "label": "最低年化收益率",
                "description": "低于此 APR 的合约不会被推荐",
                "suggested_range": "10-30%",
                "tips": "较高的 APR 要求会减少可选合约数量，但提高整体收益质量"
            },
            "put_count": {
                "label": "Put 网格数量",
                "description": "推荐多少个 Put 合约",
                "suggested_range": "3-7 个",
                "tips": "较多数量可分散风险，但管理复杂度增加"
            },
            "call_count": {
                "label": "Call 网格数量",
                "description": "推荐多少个 Call 合约",
                "suggested_range": "2-5 个",
                "tips": "Call 端通常少于 Put 端，因为上涨风险理论上无限"
            }
        }
    }
