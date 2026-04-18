"""
异步 HTTP 客户端工具
基于 httpx.AsyncClient，替代 requests + ThreadPoolExecutor 模式
"""
import asyncio
import httpx
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# 全局异步 HTTP 客户端（单例）
_async_client: Optional[httpx.AsyncClient] = None

def get_async_client() -> httpx.AsyncClient:
    """获取全局异步 HTTP 客户端（单例模式）"""
    global _async_client
    if _async_client is None or _async_client.is_closed:
        _async_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            http2=False,
            follow_redirects=True
        )
    return _async_client

async def async_get(url: str, params: Dict[str, Any] = None, 
                    timeout: float = 15.0, max_retries: int = 2,
                    headers: Dict[str, str] = None) -> dict:
    """异步 GET 请求，带自动重试"""
    client = get_async_client()
    last_exc = None
    
    for attempt in range(max_retries + 1):
        try:
            resp = await client.get(url, params=params, timeout=timeout, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, httpx.RequestError) as e:
            last_exc = e
            if attempt < max_retries:
                wait = 0.5 * (2 ** attempt)
                logger.warning("async_get retry %d/%d for %s: %s", attempt + 1, max_retries, url, str(e))
                await asyncio.sleep(wait)
            else:
                logger.error("async_get failed for %s after %d attempts: %s", url, max_retries + 1, str(e))
    
    raise last_exc

async def async_post(url: str, json: Dict[str, Any] = None,
                    timeout: float = 15.0, max_retries: int = 2,
                    headers: Dict[str, str] = None) -> dict:
    """异步 POST 请求，带自动重试"""
    client = get_async_client()
    last_exc = None
    
    for attempt in range(max_retries + 1):
        try:
            resp = await client.post(url, json=json, timeout=timeout, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, httpx.RequestError) as e:
            last_exc = e
            if attempt < max_retries:
                wait = 0.5 * (2 ** attempt)
                logger.warning("async_post retry %d/%d for %s: %s", attempt + 1, max_retries, url, str(e))
                await asyncio.sleep(wait)
            else:
                logger.error("async_post failed for %s after %d attempts: %s", url, max_retries + 1, str(e))
    
    raise last_exc

async def close_async_client():
    """关闭异步 HTTP 客户端（应用关闭时调用）"""
    global _async_client
    if _async_client and not _async_client.is_closed:
        await _async_client.aclose()
        _async_client = None
