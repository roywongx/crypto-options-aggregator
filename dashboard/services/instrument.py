import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone
import re

logger = logging.getLogger(__name__)

@dataclass
class InstrumentInfo:
    """解析后的期权合约信息"""
    currency: str
    expiry: str
    strike: float
    option_type: str  # 'C' or 'P'
    dte: int

_deribit_monitor_instance = None

def _set_deribit_monitor(monitor):
    """设置全局 Deribit monitor 实例"""
    global _deribit_monitor_instance
    _deribit_monitor_instance = monitor

# 引入兄弟项目 deribit-options-monitor
_DERIBIT_MONITOR_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'deribit-options-monitor')
if _DERIBIT_MONITOR_PATH not in sys.path:
    sys.path.insert(0, _DERIBIT_MONITOR_PATH)


def _get_deribit_monitor():
    """获取 Deribit monitor 单例"""
    global _deribit_monitor_instance
    if _deribit_monitor_instance is None:
        from deribit_options_monitor import DeribitOptionsMonitor
        _deribit_monitor_instance = DeribitOptionsMonitor()
    return _deribit_monitor_instance

def _parse_inst_name(inst: str) -> Optional[InstrumentInfo]:
    """
    解析期权 instrument name (支持 Deribit 和 Binance 格式)
    Deribit: BTC-25APR25-70000-P (DDMMMYY)
    Binance: BTC-260626-140000-C (YYMMDD)
    """
    # 先检测 Binance 格式（6 位纯数字日期），直接用 regex 解析
    m = re.match(r'([A-Z]+)-(\d{6})-(\d+)-([PC])', inst)
    if m:
        currency, expiry_str, strike_str, opt_type = m.groups()
        try:
            exp_date = datetime.strptime(expiry_str, '%y%m%d').replace(tzinfo=timezone.utc)
            dte = max(1, (exp_date - datetime.now(timezone.utc)).days)
        except (ValueError, TypeError):
            logger.debug("Binance DTE parse fallback for %s", expiry_str)
            dte = 30
        return InstrumentInfo(
            currency=currency,
            expiry=expiry_str,
            strike=float(strike_str),
            option_type=opt_type,
            dte=dte
        )

    # Deribit 格式: 先尝试 monitor，再尝试 regex
    try:
        mon = _get_deribit_monitor()
        meta = mon._parse_instrument_name(inst)
        ot = meta.option_type.upper()
        ot = 'C' if ot.startswith('C') else 'P' if ot.startswith('P') else ot
        return InstrumentInfo(
            currency=meta.currency,
            expiry=inst.split("-")[1],
            strike=float(meta.strike),
            option_type=ot,
            dte=meta.dte
        )
    except (ValueError, IndexError, AttributeError):
        pass

    m = re.match(r'([A-Z]+)-(\d+[A-Z]{3}\d+)-(\d+)-([PC])', inst)
    if m:
        currency, expiry_str, strike_str, opt_type = m.groups()
        try:
            exp_date = datetime.strptime(expiry_str, '%d%b%y').replace(tzinfo=timezone.utc)
            dte = max(1, (exp_date - datetime.now(timezone.utc)).days)
        except (ValueError, TypeError):
            logger.debug("DTE parse fallback for %s", expiry_str)
            dte = 30
        return InstrumentInfo(
            currency=currency,
            expiry=expiry_str,
            strike=float(strike_str),
            option_type=opt_type,
            dte=dte
        )

    return None
