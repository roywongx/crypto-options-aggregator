"""v6.5: 统一配置管理 - 支持 .env 动态重载

使用方式:
    from config import config
    print(config.APR_MIN_FILTER)  # 读取配置
    
    # 动态重载（部署时无需重启服务）
    config.reload()

环境变量优先级:
    1. 系统环境变量 (os.environ)
    2. .env 文件 (如果存在)
    3. 默认值 (代码中定义)
"""

import os
from pathlib import Path
from typing import Dict, Any


def _load_env_file() -> Dict[str, str]:
    """加载 .env 文件（如果存在）"""
    env_vars = {}
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip().strip('"').strip("'")
    return env_vars


def _get_env(key: str, default: Any = None, env_vars: Dict[str, str] = None) -> Any:
    """获取环境变量值（支持类型转换）
    
    优先级: os.environ > .env 文件 > 默认值
    """
    # 1. 检查系统环境变量
    if key in os.environ:
        value = os.environ[key]
    # 2. 检查 .env 文件
    elif env_vars and key in env_vars:
        value = env_vars[key]
    else:
        return default
    
    # 类型转换
    if isinstance(default, bool):
        return value.lower() in ("true", "1", "yes", "on")
    elif isinstance(default, int):
        try:
            return int(value)
        except ValueError:
            return default
    elif isinstance(default, float):
        try:
            return float(value)
        except ValueError:
            return default
    return value


class Config:
    """配置类 - 支持动态重载"""
    
    def __init__(self):
        self._env_vars = _load_env_file()
        self._load_all()
    
    def reload(self):
        """重新加载配置（从 .env 文件和环境变量）"""
        self._env_vars = _load_env_file()
        self._load_all()
    
    def _load_all(self):
        """加载所有配置项"""
        env = self._env_vars
        
        # === API 配置 ===
        self.DERIBIT_API_URL = _get_env("DERIBIT_API_URL", "https://www.deribit.com/api/v2", env)
        self.BINANCE_EAPI_URL = _get_env("BINANCE_EAPI_URL", "https://eapi.binance.com/eapi/v1", env)
        self.COINGECKO_URL = _get_env("COINGECKO_URL", "https://api.coingecko.com/api/v3", env)
        
        # === 超时配置 ===
        self.REQUEST_TIMEOUT = _get_env("REQUEST_TIMEOUT", 20, env)
        self.FETCH_TIMEOUT = _get_env("FETCH_TIMEOUT", 15, env)
        
        # === 缓存配置 ===
        self.CACHE_TTL_SECONDS = _get_env("CACHE_TTL_SECONDS", 60, env)
        self.DATA_RETENTION_DAYS = _get_env("DATA_RETENTION_DAYS", 90, env)
        
        # === 扫描配置 ===
        self.LARGE_TRADE_THRESHOLD_USD = _get_env("LARGE_TRADE_THRESHOLD_USD", 500_000, env)
        self.SCAN_CLEANUP_LIMIT_CONTRACTS = _get_env("SCAN_CLEANUP_LIMIT_CONTRACTS", 30, env)
        self.SCAN_CLEANUP_LIMIT_TRADES = _get_env("SCAN_CLEANUP_LIMIT_TRADES", 20, env)
        self.SCAN_INTERVAL_SECONDS = _get_env("SCAN_INTERVAL_SECONDS", 300, env)
        
        # === 并发配置 ===
        self.MAX_WORKERS_SCAN = _get_env("MAX_WORKERS_SCAN", 4, env)
        self.MAX_WORKERS_CHARTS = _get_env("MAX_WORKERS_CHARTS", 8, env)
        self.MAX_WORKERS_WIND = _get_env("MAX_WORKERS_WIND", 10, env)
        
        # === 过滤配置（可通过 .env 动态调整） ===
        self.APR_MIN_FILTER = _get_env("APR_MIN_FILTER", 1.0, env)
        self.APR_MAX_FILTER = _get_env("APR_MAX_FILTER", 500.0, env)
        self.SPREAD_PCT_MAX = _get_env("SPREAD_PCT_MAX", 10.0, env)
        self.MIN_NET_CREDIT_USD = _get_env("MIN_NET_CREDIT_USD", 10.0, env)
        self.ROLL_SLIPPAGE_PCT = _get_env("ROLL_SLIPPAGE_PCT", 0.05, env)
        self.ROLL_SAFETY_BUFFER_PCT = _get_env("ROLL_SAFETY_BUFFER_PCT", 0.10, env)
        
        # === 风险地板配置 ===
        self.BTC_REGULAR_FLOOR = _get_env("BTC_REGULAR_FLOOR", 65000.0, env)
        self.BTC_EXTREME_FLOOR = _get_env("BTC_EXTREME_FLOOR", 55000.0, env)
        
        # === DVOL 阈值配置 ===
        self.DVOL_PANIC_THRESHOLD = _get_env("DVOL_PANIC_THRESHOLD", 80, env)
        self.DVOL_LOW_THRESHOLD = _get_env("DVOL_LOW_THRESHOLD", 20, env)
        self.DVOL_Z_HIGH = _get_env("DVOL_Z_HIGH", 2.0, env)
        self.DVOL_Z_MID = _get_env("DVOL_Z_MID", 1.0, env)
        
        # === 成交量/流动性阈值 ===
        self.MIN_VOLUME_FILTER = _get_env("MIN_VOLUME_FILTER", 5, env)
        self.MAX_SPREAD_PCT = _get_env("MAX_SPREAD_PCT", 10.0, env)
        self.LARGE_TRADE_ZSCORE_THRESHOLD = _get_env("LARGE_TRADE_ZSCORE_THRESHOLD", 2.0, env)
        
        # === Risk Framework 配置 ===
        self.RISK_FLOOR_MULTIPLIER = _get_env("RISK_FLOOR_MULTIPLIER", 1.1, env)
        self.RISK_SCORE_EXTREME = _get_env("RISK_SCORE_EXTREME", 1.2, env)
        self.RISK_SCORE_REGULAR = _get_env("RISK_SCORE_REGULAR", 1.1, env)
        self.RISK_SCORE_ABOVE_SPOT = _get_env("RISK_SCORE_ABOVE_SPOT", 0.8, env)
        self.RISK_CACHE_TTL_SECONDS = _get_env("RISK_CACHE_TTL_SECONDS", 14400, env)
        
        # === 计算引擎配置 ===
        self.CALC_APR_MAX = _get_env("CALC_APR_MAX", 200.0, env)
        self.CALC_POP_MAX = _get_env("CALC_POP_MAX", 100.0, env)
        self.CALC_BREAKEVEN_MAX = _get_env("CALC_BREAKEVEN_MAX", 20.0, env)
        self.CALC_LIQUIDITY_MAX = _get_env("CALC_LIQUIDITY_MAX", 100.0, env)
        self.CALC_WEIGHT_APR = _get_env("CALC_WEIGHT_APR", 0.25, env)
        self.CALC_WEIGHT_POP = _get_env("CALC_WEIGHT_POP", 0.25, env)
        self.CALC_WEIGHT_BREAKEVEN = _get_env("CALC_WEIGHT_BREAKEVEN", 0.20, env)
        self.CALC_WEIGHT_LIQUIDITY = _get_env("CALC_WEIGHT_LIQUIDITY", 0.15, env)
        self.CALC_WEIGHT_IV = _get_env("CALC_WEIGHT_IV", 0.15, env)
        
        # === 数据库/认证配置 ===
        self.DB_PATH_ENV = _get_env("DASHBOARD_DB_PATH", "", env)
        self.API_KEY = _get_env("DASHBOARD_API_KEY", "", env)
        self.ENV = _get_env("DASHBOARD_ENV", "development", env)
        
        # === 策略预设（不可通过 .env 修改，保持代码一致性） ===
        self.STRATEGY_PRESETS = {
            "PUT": {
                "conservative": {"max_delta": 0.20, "min_dte": 30, "max_dte": 45, "margin_ratio": 0.18, "min_apr": 12.0},
                "standard":     {"max_delta": 0.30, "min_dte": 14, "max_dte": 35, "margin_ratio": 0.20, "min_apr": 15.0},
                "aggressive":   {"max_delta": 0.40, "min_dte": 7,  "max_dte": 28, "margin_ratio": 0.22, "min_apr": 20.0}
            },
            "CALL": {
                "conservative": {"max_delta": 0.30, "min_dte": 30, "max_dte": 45, "margin_ratio": 0.18, "min_apr": 10.0},
                "standard":     {"max_delta": 0.45, "min_dte": 14, "max_dte": 35, "margin_ratio": 0.20, "min_apr": 12.0},
                "aggressive":   {"max_delta": 0.55, "min_dte": 7,  "max_dte": 28, "margin_ratio": 0.22, "min_apr": 18.0}
            }
        }
    
    def db_path(self) -> str:
        """获取数据库路径"""
        return self.DB_PATH_ENV or str(Path(__file__).parent / "data" / "monitor.db")
    
    def to_dict(self) -> Dict[str, Any]:
        """导出配置为字典（用于调试）"""
        return {
            k: v for k, v in self.__dict__.items()
            if not k.startswith("_")
        }


# 全局配置实例
config = Config()
