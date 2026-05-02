"""
后台任务管理器
统一管理定时扫描、DataHub 等后台任务，解耦 main.py
"""

import asyncio
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class BackgroundTaskManager:
    """后台任务管理器

    统一管理所有后台异步任务：
    - DataHub WebSocket 服务
    - 定时扫描任务
    - 其他后台作业
    """

    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}
        self._running = False

    async def start(self):
        """启动所有后台任务"""
        if self._running:
            logger.warning("后台任务管理器已在运行")
            return

        self._running = True

        # 启动 DataHub
        await self._start_datahub()

        # 启动定时扫描
        await self._start_scan_task()

        logger.info("后台任务管理器已启动")

    async def stop(self):
        """停止所有后台任务"""
        if not self._running:
            return

        self._running = False

        for name, task in list(self._tasks.items()):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                logger.info("后台任务 '%s' 已停止", name)

        self._tasks.clear()
        logger.info("后台任务管理器已停止")

    async def _start_datahub(self):
        """启动 DataHub WebSocket 服务"""
        try:
            from services.datahub import start_datahub_services
            task = asyncio.create_task(start_datahub_services())
            self._tasks['datahub'] = task
            logger.info("DataHub 服务已启动")
        except (ImportError, RuntimeError, ValueError) as e:
            logger.warning("DataHub 启动失败，将使用 REST fallback: %s", e)

    async def _start_scan_task(self):
        """启动定时扫描任务"""
        from config import config
        from models.contracts import QuickScanParams
        from services.scan_engine import quick_scan

        async def background_scan():
            logger.info("启动后台定时扫描任务，间隔 %d 秒", config.SCAN_INTERVAL_SECONDS)
            while self._running:
                try:
                    await asyncio.sleep(config.SCAN_INTERVAL_SECONDS)

                    # 对所有支持的币种执行扫描
                    for currency in ["BTC", "ETH", "SOL"]:
                        if not self._running:
                            break
                        try:
                            params = QuickScanParams(currency=currency, option_type="ALL")
                            await quick_scan(params)
                            logger.info("定时扫描完成: %s", currency)
                        except (RuntimeError, ValueError, TypeError) as e:
                            logger.error("定时扫描失败 %s: %s", currency, str(e))

                except asyncio.CancelledError:
                    logger.info("后台扫描任务已取消")
                    break
                except (RuntimeError, ValueError, TypeError) as e:
                    logger.error("后台扫描任务异常: %s", str(e))
                    await asyncio.sleep(60)  # 异常后等待1分钟再继续

        task = asyncio.create_task(background_scan())
        self._tasks['scan'] = task
        logger.info("后台扫描任务已创建")

    def get_status(self) -> Dict[str, Dict]:
        """获取任务状态"""
        return {
            name: {
                "done": task.done(),
                "cancelled": task.cancelled() if task.done() else False,
            }
            for name, task in self._tasks.items()
        }


# 全局实例（模块级单例）
_task_manager: Optional[BackgroundTaskManager] = None


def get_task_manager() -> BackgroundTaskManager:
    """获取后台任务管理器单例"""
    global _task_manager
    if _task_manager is None:
        _task_manager = BackgroundTaskManager()
    return _task_manager


def reset_task_manager():
    """重置任务管理器（主要用于测试）"""
    global _task_manager
    _task_manager = None
