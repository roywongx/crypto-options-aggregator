import logging
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

def _get_deribit_monitor():
    """获取 Deribit monitor 单例"""
    global _deribit_monitor_instance
    if _deribit_monitor_instance is None:
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'deribit-options-monitor'))
        from deribit_options_monitor import DeribitOptionsMonitor
        _deribit_monitor_instance = DeribitOptionsMonitor()
    return _deribit_monitor_instance

def _parse_inst_name(inst: str) -> Optional[InstrumentInfo]:
    """
    解析 Deribit instrument name
    例如: BTC-25APR25-70000-P
    返回 InstrumentInfo 或 None
    """
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
    if not m:
        return None
    currency, expiry_str, strike_str, opt_type = m.groups()
    try:
        exp_date = datetime.strptime(expiry_str, '%d%b%y')
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
