"""
Pytest 全局配置和共享 Fixtures
"""

import pytest
import sys
from pathlib import Path

# 将 dashboard 目录添加到 Python 路径
DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"
sys.path.insert(0, str(DASHBOARD_DIR))


@pytest.fixture
def sample_contracts():
    """提供一组测试用的期权合约数据（包含 apr 字段）"""
    return [
        {
            "instrument_name": "BTC-27DEC24-80000-P",
            "strike": 80000,
            "option_type": "P",
            "expiration_date": "2024-12-27",
            "dte": 27,
            "bid": 500,
            "ask": 520,
            "mark_price": 510,
            "premium_usd": 510,
            "apr": 25.0,
            "iv": 0.45,
            "delta": -0.35,
            "theta": -12.5,
            "volume": 150,
            "open_interest": 2500,
            "underlying_price": 78200
        },
        {
            "instrument_name": "BTC-27DEC24-75000-P",
            "strike": 75000,
            "option_type": "P",
            "expiration_date": "2024-12-27",
            "dte": 27,
            "bid": 200,
            "ask": 210,
            "mark_price": 205,
            "premium_usd": 205,
            "apr": 30.0,
            "iv": 0.50,
            "delta": -0.18,
            "theta": -8.3,
            "volume": 80,
            "open_interest": 1200,
            "underlying_price": 78200
        },
        {
            "instrument_name": "BTC-27DEC24-85000-C",
            "strike": 85000,
            "option_type": "C",
            "expiration_date": "2024-12-27",
            "dte": 27,
            "bid": 300,
            "ask": 320,
            "mark_price": 310,
            "premium_usd": 310,
            "apr": 20.0,
            "iv": 0.42,
            "delta": 0.28,
            "theta": -10.2,
            "volume": 200,
            "open_interest": 1800,
            "underlying_price": 78200
        }
    ]


@pytest.fixture
def mock_spot_price():
    """提供测试用的现货价格"""
    return 78200.0


@pytest.fixture
def test_db_path(tmp_path):
    """提供临时数据库路径"""
    return str(tmp_path / "test.db")
