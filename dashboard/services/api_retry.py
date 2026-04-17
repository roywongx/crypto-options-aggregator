"""
API重试工具
为外部API调用提供指数退避重试机制
"""
import time
import requests
import logging
from functools import wraps
from typing import Callable, Any, Optional

logger = logging.getLogger(__name__)

def retry_api(max_retries: int = 3, backoff_base: float = 1.0, exceptions: tuple = (requests.RequestException,)):
    """
    API重试装饰器
    
    Args:
        max_retries: 最大重试次数
        backoff_base: 初始退避时间（秒），每次重试翻倍
        exceptions: 需要重试的异常类型
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        wait_time = backoff_base * (2 ** attempt)
                        logger.warning(
                            f"API调用失败 (attempt {attempt + 1}/{max_retries}): {func.__name__} - {e}. "
                            f"将在 {wait_time:.1f}s 后重试..."
                        )
                        time.sleep(wait_time)
                    else:
                        logger.error(
                            f"API调用最终失败 ({max_retries} attempts): {func.__name__} - {e}"
                        )
            raise last_exception
        return wrapper
    return decorator

def request_with_retry(url: str, params: dict = None, timeout: int = 10, 
                       max_retries: int = 3, verify: bool = True) -> requests.Response:
    """
    带重试的requests.get封装
    
    Args:
        url: 请求URL
        params: 请求参数
        timeout: 超时时间
        max_retries: 最大重试次数
        verify: 是否验证SSL
    
    Returns:
        requests.Response
    
    Raises:
        requests.RequestException: 所有重试失败后抛出
    """
    last_exception = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout, verify=verify)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exception = e
            if attempt < max_retries - 1:
                wait_time = 1.0 * (2 ** attempt)
                logger.warning(f"Request failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {wait_time:.1f}s...")
                time.sleep(wait_time)
            else:
                logger.error(f"Request failed after {max_retries} attempts: {e}")
    raise last_exception
