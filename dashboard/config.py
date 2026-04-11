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

    DB_PATH_ENV = os.getenv("DASHBOARD_DB_PATH", "")
    API_KEY = os.getenv("DASHBOARD_API_KEY", "")

    @classmethod
    def db_path(cls):
        from pathlib import Path
        return cls.DB_PATH_ENV or str(Path(__file__).parent / "data" / "monitor.db")

config = Config()
