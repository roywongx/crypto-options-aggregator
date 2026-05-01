"""
Pytest 配置文件
提供共享 fixtures 和配置
"""

import pytest
import sys
from pathlib import Path

# 确保 dashboard 在路径中
dashboard_path = Path(__file__).parent.parent / "dashboard"
if str(dashboard_path) not in sys.path:
    sys.path.insert(0, str(dashboard_path))


@pytest.fixture(scope="session")
def test_currency():
    """测试用默认币种"""
    return "BTC"


@pytest.fixture(scope="session")
def test_spot_price():
    """测试用现货价格"""
    return 80000.0


@pytest.fixture
def mock_contracts():
    """模拟期权合约数据"""
    return [
        {
            'symbol': 'BTC-80000-P',
            'strike': 80000,
            'option_type': 'PUT',
            'mark_price': 1000,
            'delta': -0.3,
            'apr': 50.0,
            'dte': 30,
            'oi': 100
        },
        {
            'symbol': 'BTC-85000-C',
            'strike': 85000,
            'option_type': 'CALL',
            'mark_price': 800,
            'delta': 0.3,
            'apr': 40.0,
            'dte': 30,
            'oi': 100
        },
        {
            'symbol': 'BTC-70000-P',
            'strike': 70000,
            'option_type': 'PUT',
            'mark_price': 500,
            'delta': -0.1,
            'apr': 60.0,
            'dte': 30,
            'oi': 100
        }
    ]


@pytest.fixture
def grid_params():
    """网格策略默认参数"""
    return {
        'spot': 80000,
        'put_count': 3,
        'call_count': 2,
        'min_dte': 7,
        'max_dte': 45,
        'min_apr': 0.3
    }
