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
