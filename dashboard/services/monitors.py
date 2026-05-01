"""
统一 Monitor 单例管理器
集中管理所有 exchange monitor 的创建和复用，避免重复实例化
"""

import os
import sys
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 全局单例缓存
_monitor_cache = {}


def get_deribit_monitor():
    """
    获取 DeribitOptionsMonitor 单例（线程安全）
    
    所有模块统一使用此函数获取 monitor 实例，
    避免重复创建导致连接池和缓存丢失。
    
    Returns:
        DeribitOptionsMonitor 实例
    """
    if 'deribit' not in _monitor_cache:
        # 添加 deribit-options-monitor 到路径
        deribit_path = os.path.join(os.path.dirname(__file__), '..', '..', 'deribit-options-monitor')
        if deribit_path not in sys.path:
            sys.path.insert(0, deribit_path)
        
        try:
            from deribit_options_monitor import DeribitOptionsMonitor
            _monitor_cache['deribit'] = DeribitOptionsMonitor()
            logger.info("DeribitOptionsMonitor 单例已创建")
        except ImportError as e:
            logger.error("无法导入 DeribitOptionsMonitor: %s", e)
            raise
    
    return _monitor_cache['deribit']


def get_monitor(monitor_type: str):
    """
    通用 monitor 获取函数
    
    Args:
        monitor_type: monitor 类型，如 'deribit'
    
    Returns:
        对应类型的 monitor 实例
    """
    if monitor_type == 'deribit':
        return get_deribit_monitor()
    else:
        raise ValueError(f"不支持的 monitor 类型: {monitor_type}")


def clear_monitor_cache():
    """清除所有 monitor 缓存（主要用于测试）"""
    global _monitor_cache
    _monitor_cache.clear()
    logger.info("Monitor 缓存已清除")


def get_monitor_status() -> dict:
    """获取当前 monitor 状态（用于调试）"""
    return {
        'cached_types': list(_monitor_cache.keys()),
        'cache_size': len(_monitor_cache)
    }
