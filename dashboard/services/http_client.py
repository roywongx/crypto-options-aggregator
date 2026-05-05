"""
统一 HTTP 客户端封装
- 同步请求使用 httpx.Client
- 异步请求使用 httpx.AsyncClient
- 统一超时和重试配置
"""
import logging
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
DEFAULT_RETRIES = 2

_sync_client: Optional[httpx.Client] = None
_async_client: Optional[httpx.AsyncClient] = None


def get_sync_client() -> httpx.Client:
    """获取全局同步 HTTP 客户端"""
    global _sync_client
    if _sync_client is None:
        _sync_client = httpx.Client(timeout=DEFAULT_TIMEOUT)
    return _sync_client


def get_async_client() -> httpx.AsyncClient:
    """获取全局异步 HTTP 客户端"""
    global _async_client
    if _async_client is None:
        _async_client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    return _async_client


def http_get(url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, timeout: Optional[float] = None) -> httpx.Response:
    """同步 GET 请求"""
    client = get_sync_client()
    t = httpx.Timeout(timeout, connect=5.0) if timeout else DEFAULT_TIMEOUT
    return client.get(url, params=params, headers=headers, timeout=t)


async def async_http_get(url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, timeout: Optional[float] = None) -> httpx.Response:
    """异步 GET 请求"""
    client = get_async_client()
    t = httpx.Timeout(timeout, connect=5.0) if timeout else DEFAULT_TIMEOUT
    return await client.get(url, params=params, headers=headers, timeout=t)


def close_sync_client() -> None:
    """关闭同步客户端"""
    global _sync_client
    if _sync_client:
        _sync_client.close()
        _sync_client = None


async def close_async_client() -> None:
    """关闭异步客户端"""
    global _async_client
    if _async_client:
        await _async_client.aclose()
        _async_client = None
