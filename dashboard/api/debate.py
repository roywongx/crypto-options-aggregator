"""AI 辩论分析 API — 多智能体期权决策辩论"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/debate", tags=["debate"])


@router.post("/analyze")
async def debate_analyze(currency: str = "BTC"):
    """
    运行完整的多智能体辩论分析

    返回 5 个分析智能体的独立报告 + 加权合成的最终建议。
    确定性分析，无 LLM 调用。
    """
    from fastapi.concurrency import run_in_threadpool
    from services.options_debate_engine import run_debate, save_debate_result

    try:
        result = await run_in_threadpool(run_debate, currency.upper(), False)
    except (RuntimeError, ValueError, TypeError) as e:
        logger.error("debate analyze failed for %s: %s", currency, e)
        raise HTTPException(status_code=500, detail=f"辩论分析失败: {e}")

    # 尝试保存结果
    try:
        await run_in_threadpool(save_debate_result, result)
    except (RuntimeError, ValueError, TypeError) as e:
        logger.debug("debate save failed (non-critical): %s", e)

    return result


@router.get("/quick")
async def debate_quick(currency: str = Query("BTC", description="币种")):
    """
    快速辩论分析（跳过资金流向分析，更快响应）

    适合需要快速决策的场景。
    """
    from fastapi.concurrency import run_in_threadpool
    from services.options_debate_engine import run_debate

    try:
        result = await run_in_threadpool(run_debate, currency.upper(), True)
    except (RuntimeError, ValueError, TypeError) as e:
        logger.error("debate quick failed for %s: %s", currency, e)
        raise HTTPException(status_code=500, detail=f"快速辩论失败: {e}")

    return result


@router.get("/history")
async def debate_history(
    currency: str = Query("BTC", description="币种"),
    limit: int = Query(10, ge=1, le=50, description="返回条数"),
):
    """获取最近的辩论历史记录"""
    try:
        from db.connection import execute_read
        rows = execute_read(
            """SELECT currency, spot_price, overall_score, recommendation,
                      recommendation_label, consensus, reports_json, synthesis_json, timestamp
               FROM debate_results
               WHERE currency = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (currency.upper(), limit)
        )
        if not rows:
            return {"currency": currency.upper(), "history": [], "message": "暂无历史记录"}

        history = []
        for row in rows:
            reports = json.loads(row[6]) if row[6] else []
            synthesis = json.loads(row[7]) if row[7] else {}
            history.append({
                "currency": row[0],
                "spot_price": row[1],
                "overall_score": row[2],
                "recommendation": row[3],
                "recommendation_label": row[4],
                "consensus": row[5],
                "reports": reports,
                "synthesis": synthesis,
                "timestamp": row[8],
            })

        return {"currency": currency.upper(), "history": history, "count": len(history)}

    except (RuntimeError, ValueError, TypeError) as e:
        logger.warning("debate history query failed: %s", e)
        # 表可能不存在
        return {"currency": currency.upper(), "history": [], "error": str(e)}
