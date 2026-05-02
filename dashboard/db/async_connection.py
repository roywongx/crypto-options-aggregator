"""
异步数据库连接包装器
将同步的 sqlite3 操作放到线程池中执行，避免阻塞 FastAPI 事件循环

使用方式:
    from db.async_connection import execute_read_async, execute_write_async
    rows = await execute_read_async("SELECT * FROM table WHERE id = ?", (id,))
"""

import logging
from typing import Any, List
from fastapi.concurrency import run_in_threadpool
from db.connection import execute_read as _sync_read
from db.connection import execute_write as _sync_write
from db.connection import execute_transaction as _sync_transaction

logger = logging.getLogger(__name__)


async def execute_read_async(query: str, params: tuple = ()) -> List[Any]:
    """
    异步执行只读查询（在线程池中运行，不阻塞事件循环）

    Args:
        query: SQL 查询语句
        params: 查询参数（防 SQL 注入）

    Returns:
        查询结果列表
    """
    return await run_in_threadpool(_sync_read, query, params)


async def execute_write_async(query: str, params: tuple = ()) -> Any:
    """
    异步执行写操作（在线程池中运行）

    Args:
        query: SQL 语句
        params: 语句参数

    Returns:
        最后插入行的 ID
    """
    return await run_in_threadpool(_sync_write, query, params)


async def execute_transaction_async(stmts: List[tuple]) -> Any:
    """
    异步执行事务（在线程池中运行）

    Args:
        stmts: [(query, params), ...] 的列表

    Returns:
        最后一条语句的 lastrowid
    """
    return await run_in_threadpool(_sync_transaction, stmts)
