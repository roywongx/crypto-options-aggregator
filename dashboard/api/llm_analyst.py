"""AI 研判中心 API — LLM 综合分析端点"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/llm-analyst", tags=["llm-analyst"])


class AnalyzeRequest(BaseModel):
    currency: str = Field(default="BTC", description="币种")
    mode: str = Field(default="full", description="分析模式: full 或 quick")


class ConfigRequest(BaseModel):
    api_key: str = Field(description="LLM API Key")
    base_url: str = Field(default="", description="Base URL (可选)")
    model: str = Field(default="", description="模型名 (可选)")


class TestConnectionRequest(BaseModel):
    api_key: str = Field(description="API Key")
    base_url: str = Field(default="", description="Base URL")
    model: str = Field(default="", description="模型名")


@router.post("/analyze")
async def llm_analyze(request: AnalyzeRequest):
    """全流程 LLM 分析（规则→综合→辩论→审计）"""
    from fastapi.concurrency import run_in_threadpool
    from services.llm_analyst import LLMAnalystEngine

    engine = LLMAnalystEngine()

    try:
        result = await run_in_threadpool(
            engine.run_full_analysis, request.currency.upper(), request.mode
        )
    except (RuntimeError, ValueError, TypeError) as e:
        logger.error("llm analyze failed for %s: %s", request.currency, e)
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")

    # 保存结果
    try:
        from db.connection import execute_write
        execute_write(
            """INSERT INTO llm_analysis_results (currency, mode, result_json, llm_config_json, success, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                request.currency.upper(),
                request.mode,
                json.dumps(result, default=str, ensure_ascii=False),
                json.dumps(result.llm_config, ensure_ascii=False),
                1 if result.success else 0,
                datetime.now(timezone.utc).isoformat(),
            )
        )
    except (RuntimeError, ValueError, TypeError) as e:
        logger.debug("llm analysis save failed (non-critical): %s", e)

    return {
        "success": result.success,
        "currency": result.currency,
        "timestamp": result.timestamp,
        "rule_reports": result.rule_reports,
        "synthesis": result.synthesis,
        "debate": result.debate,
        "audit": result.audit,
        "llm_config": result.llm_config,
    }


@router.get("/history")
async def llm_history(
    currency: str = "BTC",
    limit: int = 10,
):
    """获取历史 LLM 分析结果"""
    try:
        from db.connection import execute_read
        rows = execute_read(
            """SELECT currency, mode, result_json, success, timestamp
               FROM llm_analysis_results
               WHERE currency = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (currency.upper(), limit)
        )
        if not rows:
            return {"currency": currency.upper(), "history": [], "message": "暂无历史记录"}

        history = []
        for row in rows:
            result = json.loads(row[2]) if row[2] else {}
            history.append({
                "currency": row[0],
                "mode": row[1],
                "success": bool(row[3]),
                "timestamp": row[4],
                "synthesis": result.get("synthesis", {}),
                "audit": result.get("audit", {}),
            })
        return {"currency": currency.upper(), "history": history, "count": len(history)}

    except (RuntimeError, ValueError, TypeError) as e:
        logger.warning("llm history query failed: %s", e)
        return {"currency": currency.upper(), "history": [], "error": str(e)}


@router.get("/config")
async def get_llm_config():
    """获取当前 LLM 配置"""
    from services.llm_analyst import LLMAnalystEngine
    engine = LLMAnalystEngine()
    config = engine.load_config()
    # 隐藏 API Key 中间部分
    masked = dict(config)
    if masked.get("api_key") and len(masked["api_key"]) > 8:
        masked["api_key"] = masked["api_key"][:4] + "****" + masked["api_key"][-4:]
    elif masked.get("api_key"):
        masked["api_key"] = "****"
    return masked


@router.post("/config")
async def save_llm_config(request: ConfigRequest):
    """保存 LLM 配置"""
    from services.llm_analyst import LLMAnalystEngine
    engine = LLMAnalystEngine()
    success = engine.save_config(request.api_key, request.base_url, request.model)
    if not success:
        raise HTTPException(status_code=500, detail="保存配置失败")
    return {"success": True, "message": "配置已保存"}


@router.post("/test")
async def test_llm_connection(request: TestConnectionRequest):
    """测试 LLM 连接"""
    from fastapi.concurrency import run_in_threadpool
    from services.llm_analyst import LLMAnalystEngine

    engine = LLMAnalystEngine()
    config = {
        "api_key": request.api_key,
        "base_url": request.base_url,
        "model": request.model,
    }

    result = await run_in_threadpool(engine.test_connection, config)
    return result
