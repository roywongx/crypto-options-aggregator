# Dashboard services - instrument parsing
import sys
import re
from datetime import datetime

def parse_instrument_name(inst):
    """
    解析 Deribit instrument name
    返回: {"currency", "expiry", "strike", "option_type", "dte"}
    """
    try:
        from dashboard.main import _get_deribit_monitor
        mon = _get_deribit_monitor()
        meta = mon._parse_instrument_name(inst)
        ot = meta.option_type.upper()
        ot = 'C' if ot.startswith('C') else 'P' if ot.startswith('P') else ot
        return {"currency": meta.currency, "expiry": inst.split("-")[1],
                "strike": float(meta.strike), "option_type": ot, "dte": meta.dte}
    except Exception as e:
        print(f"[ERROR] {os.path.basename(file_path)}: {{e}}", file=sys.stderr)

    # Fallback: regex parsing
    m = re.match(r'([A-Z]+)-(\d+[A-Z]{3}\d+)-(\d+)-([PC])', inst)
    if not m:
        return None
    currency, expiry_str, strike_str, opt_type = m.groups()
    try:
        exp_date = datetime.strptime(expiry_str, '%d%b%y')
        dte = max(1, (exp_date - datetime.utcnow()).days)
    except Exception:
        print(f"[ERROR] instrument.py: {e}", file=sys.stderr)
        dte = 30
    return {"currency": currency, "expiry": expiry_str, "strike": float(strike_str),
            "option_type": opt_type, "dte": dte}

# 向后兼容别名
_parse_inst_name = parse_instrument_name
