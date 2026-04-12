"""v5.7: 统一配置管理 - 消除硬编码散落"""
import os

class Config:
    DERIBIT_API_URL = "https://www.deribit.com/api/v2"
    BINANCE_EAPI_URL = "https://eapi.binance.com/eapi/v1"
    COINGECKO_URL = "https://api.coingecko.com/api/v3"

    REQUEST_TIMEOUT = 20
    FETCH_TIMEOUT = 15

    CACHE_TTL_SECONDS = 60
    DATA_RETENTION_DAYS = 90

    LARGE_TRADE_THRESHOLD_USD = 500_000
    SCAN_CLEANUP_LIMIT_CONTRACTS = 30
    SCAN_CLEANUP_LIMIT_TRADES = 20

    MAX_WORKERS_SCAN = 4
    MAX_WORKERS_CHARTS = 8
    MAX_WORKERS_WIND = 10

    APR_MIN_FILTER = 1.0
    APR_MAX_FILTER = 500.0
    SPREAD_PCT_MAX = 10.0
    MIN_NET_CREDIT_USD = 10.0
    ROLL_SLIPPAGE_PCT = 0.05
    ROLL_SAFETY_BUFFER_PCT = 0.10

    # v6.0: BTC Risk Floors
    BTC_REGULAR_FLOOR = 55000.0
    BTC_EXTREME_FLOOR = 45000.0

    # v6.0.1: DVOL Threshold Constants
    DVOL_PANIC_THRESHOLD = 80      # 极度恐慌/高波动的 DVOL 分位阈值
    DVOL_LOW_THRESHOLD = 20        # 低波动的 DVOL 分位阈值
    DVOL_Z_HIGH = 2.0              # Z-Score 异常偏高阈值
    DVOL_Z_MID = 1.0               # Z-Score 偏高阈值

    # v6.0.1: Volume/Liquidity Thresholds
    MIN_VOLUME_FILTER = 5           # 最小成交量过滤
    MAX_SPREAD_PCT = 10.0          # 最大买卖价差百分比
    LARGE_TRADE_ZSCORE_THRESHOLD = 2.0  # 大单交易的 Z-Score 阈值

    DB_PATH_ENV = os.getenv("DASHBOARD_DB_PATH", "")
    API_KEY = os.getenv("DASHBOARD_API_KEY", "")

    # v6.3: Strategy Presets
    STRATEGY_PRESETS = {
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

    @classmethod
    def db_path(cls):
        from pathlib import Path
        return cls.DB_PATH_ENV or str(Path(__file__).parent / "data" / "monitor.db")

config = Config()
