"""
统一 HTTP 客户端封装
- 同步请求使用 httpx.Client
- 异步请求使用 httpx.AsyncClient
- 统一超时和重试配置 (指数退避: 1s, 2s, 4s)
"""
import logging
import time
from typing import Optional, Dict, Any, Callable

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
DEFAULT_RETRIES = 2
INITIAL_BACKOFF = 1.0   # seconds
BACKOFF_MULTIPLIER = 2.0
RETRYABLE_STATUSES = (429, 500, 502, 503, 504)

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


def _should_retry(response: Optional[httpx.Response], exc: Optional[Exception]) -> bool:
    """判断是否应该重试"""
    if response is not None:
        return response.status_code in RETRYABLE_STATUSES
    if exc is not None:
        return isinstance(exc, (httpx.HTTPError, httpx.TimeoutException, ConnectionError, TimeoutError))
    return False


def http_get(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
    retries: int = DEFAULT_RETRIES,
) -> httpx.Response:
    """同步 GET 请求（带指数退避重试）"""
    client = get_sync_client()
    t = httpx.Timeout(timeout, connect=5.0) if timeout else DEFAULT_TIMEOUT
    last_exc: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            resp = client.get(url, params=params, headers=headers, timeout=t)
            if resp.status_code < 500:
                resp.raise_for_status()
                return resp
            if attempt < retries and resp.status_code in RETRYABLE_STATUSES:
                delay = INITIAL_BACKOFF * (BACKOFF_MULTIPLIER ** attempt)
                logger.debug("HTTP GET %s → %d, retrying in %.1fs (attempt %d/%d)",
                             url[:80], resp.status_code, delay, attempt + 1, retries)
                time.sleep(delay)
                continue
            resp.raise_for_status()
            return resp
        except (httpx.HTTPError, httpx.TimeoutException, ConnectionError, TimeoutError) as e:
            last_exc = e
            if attempt < retries:
                delay = INITIAL_BACKOFF * (BACKOFF_MULTIPLIER ** attempt)
                logger.debug("HTTP GET %s → %s, retrying in %.1fs (attempt %d/%d)",
                             url[:80], type(e).__name__, delay, attempt + 1, retries)
                time.sleep(delay)
            else:
                logger.warning("HTTP GET %s failed after %d retries: %s", url[:80], retries, e)
                raise

    raise last_exc or RuntimeError(f"HTTP GET {url} failed")


async def async_http_get(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
    retries: int = DEFAULT_RETRIES,
) -> httpx.Response:
    """异步 GET 请求（带指数退避重试）"""
    import asyncio
    client = get_async_client()
    t = httpx.Timeout(timeout, connect=5.0) if timeout else DEFAULT_TIMEOUT
    last_exc: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            resp = await client.get(url, params=params, headers=headers, timeout=t)
            if resp.status_code < 500:
                resp.raise_for_status()
                return resp
            if attempt < retries and resp.status_code in RETRYABLE_STATUSES:
                delay = INITIAL_BACKOFF * (BACKOFF_MULTIPLIER ** attempt)
                logger.debug("HTTP GET %s → %d, retrying in %.1fs (attempt %d/%d)",
                             url[:80], resp.status_code, delay, attempt + 1, retries)
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            return resp
        except (httpx.HTTPError, httpx.TimeoutException, ConnectionError, TimeoutError) as e:
            last_exc = e
            if attempt < retries:
                delay = INITIAL_BACKOFF * (BACKOFF_MULTIPLIER ** attempt)
                logger.debug("HTTP GET %s → %s, retrying in %.1fs (attempt %d/%d)",
                             url[:80], type(e).__name__, delay, attempt + 1, retries)
                await asyncio.sleep(delay)
            else:
                logger.warning("HTTP GET %s failed after %d retries: %s", url[:80], retries, e)
                raise

    raise last_exc or RuntimeError(f"HTTP GET {url} failed")


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
