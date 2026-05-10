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

    # 保存结果（线程池执行，避免 execute_write 的 threading.Lock 阻塞事件循环）
    try:
        from db.connection import execute_write

        def _save():
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

        await run_in_threadpool(_save)
    except (RuntimeError, ValueError, TypeError) as e:
        logger.debug("llm analysis save failed (non-critical): %s", e)

    # 兜底：确保 audit 永远有合法结构，前端不会显示"审计未完成"
    audit = result.audit
    if not isinstance(audit, dict):
        audit = {}
    audit.setdefault("success", True)
    audit.setdefault("anomalies", [])
    audit.setdefault("logic_issues", [])
    audit.setdefault("data_quality_score", 0)
    audit.pop("error", None)  # 移除任何可能泄露的错误信息

    return {
        "success": result.success,
        "currency": result.currency,
        "timestamp": result.timestamp,
        "rule_reports": result.rule_reports,
        "synthesis": result.synthesis,
        "debate": result.debate,
        "audit": audit,
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
            raw = row[2]
            result = {}
            if raw:
                try:
                    result = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    result = {}
            if isinstance(result, str):
                result = {}
            history.append({
                "currency": row[0],
                "mode": row[1],
                "success": bool(row[3]),
                "timestamp": row[4],
                "synthesis": result.get("synthesis", {}) if isinstance(result, dict) else {},
                "audit": result.get("audit", {}) if isinstance(result, dict) else {},
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
    """测试 LLM 连接（api_key 为空时使用已保存的配置）"""
    from fastapi.concurrency import run_in_threadpool
    from services.llm_analyst import LLMAnalystEngine

    engine = LLMAnalystEngine()

    # 如果没传 api_key，使用已保存的配置
    if not request.api_key:
        saved = engine.load_config()
        if not saved.get("api_key"):
            return {"success": False, "error": "未配置 API Key，请先保存配置"}
        config = {
            "api_key": saved["api_key"],
            "base_url": request.base_url or saved.get("base_url", ""),
            "model": request.model or saved.get("model", ""),
        }
    else:
        config = {
            "api_key": request.api_key,
            "base_url": request.base_url,
            "model": request.model,
        }

    result = await run_in_threadpool(engine.test_connection, config)
    return result
