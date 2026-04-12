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
        import sqlite3
        from pathlib import Path

        db_path = Path(__file__).parent.parent / "data" / "options.db"
        if not db_path.exists():
            return []

        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT contracts_data FROM scan_records
            WHERE currency = ? AND timestamp > datetime('now', '-1 day')
            ORDER BY timestamp DESC LIMIT 1
        """, (currency,))
        row = cursor.fetchone()
        conn.close()

        if row and row[0]:
            try:
                contracts = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                return contracts
            except:
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
    min_apr: float = Query(15.0, ge=0, le=200)
):
    try:
        from main import get_spot_price

        spot_price = get_spot_price(currency)
        if not spot_price or spot_price <= 0:
            spot_price = 83000.0

        contracts = _get_contracts_from_db(currency)

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
                    "reason": l.reason
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
                    "reason": l.reason
                }
                for l in recommendation.call_levels
            ],
            "dvol_signal": recommendation.dvol_signal,
            "recommended_ratio": recommendation.recommended_ratio,
            "total_potential_premium": recommendation.total_potential_premium
        }
    except Exception as e:
        import sys
        print(f"[ERROR] /api/grid/recommend: {e}", file=sys.stderr)
        return {"error": str(e)}

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
        import sqlite3
        from pathlib import Path

        db_path = Path(__file__).parent.parent / "data" / "options.db"
        if not db_path.exists():
            return {"error": "Database not found"}

        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT timestamp, contracts_data FROM scan_records
            WHERE currency = ? AND timestamp > datetime('now', ?||' days')
            ORDER BY timestamp ASC
        """, (currency, -days))

        rows = cursor.fetchall()
        conn.close()

        total_premium = 0.0
        scan_count = len(rows)

        for row in rows:
            if row[1]:
                try:
                    contracts = json.loads(row[1]) if isinstance(row[1], str) else row[1]
                    for c in contracts:
                        premium = float(c.get("premium_usd", c.get("premium", 0)))
                        total_premium += premium
                except:
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
