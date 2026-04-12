# Services - Flow Classifier
import re
from typing import Dict, Any, Optional

FLOW_LABEL_MAP = {
    "sell_put_deep_itm": ("保护性对冲", "深度ITM Sell Put，强烈看涨愿意接货"),
    "sell_put_atm_itm": ("收权利金", "ATM/ITM Sell Put，温和看涨+稳定收权"),
    "sell_put_otm": ("备兑开仓", "OTM Sell Put，纯收权利金，最激进"),
    "buy_put_deep_itm": ("保护性买入", "深度ITM Buy Put，机构对冲防下跌"),
    "buy_put_atm": ("看跌投机", "ATM Buy Put，短线看跌或对冲"),
    "buy_put_otm": ("看跌投机", "OTM Buy Put，纯粹投机看跌"),
    "sell_call_otm": ("备兑开仓", "OTM Sell Call，备兑开仓收权"),
    "sell_call_itm": ("改仓操作", "ITM Sell Call，改仓操作"),
    "buy_call_atm_itm": ("追涨建仓", "ATM/ITM Buy Call，顺势追涨看涨"),
    "buy_call_otm": ("看涨投机", "OTM Buy Call，低成本博反弹"),
    "unknown": ("未知流向", "无法判断交易意图"),
}

def _classify_flow_heuristic(direction: str, option_type: str, delta: float, strike: float, spot: float) -> str:
    """流向分类 - 基于期权希腊字母和行权价相对现货位置"""
    if not direction or direction == "unknown" or not option_type:
        return "unknown"

    d = abs(delta or 0)

    if direction == "buy":
        if option_type.upper() in ("PUT", "P"):
            if d >= 0.70:
                return "buy_put_deep_itm"
            elif d >= 0.40:
                return "buy_put_atm"
            else:
                return "buy_put_otm"
        elif option_type.upper() in ("CALL", "C"):
            if d >= 0.40:
                return "buy_call_atm_itm"
            else:
                return "buy_call_otm"

    elif direction == "sell":
        if option_type.upper() in ("PUT", "P"):
            if d >= 0.70:
                return "sell_put_deep_itm"
            elif d >= 0.40:
                return "sell_put_atm_itm"
            else:
                return "sell_put_otm"
        elif option_type.upper() in ("CALL", "C"):
            if d >= 0.40:
                return "sell_call_itm"
            else:
                return "sell_call_otm"

    return "unknown"

def _severity_from_notional(notional: float) -> str:
    if notional >= 2_000_000:
        return "high"
    if notional >= 500_000:
        return "medium"
    return "info"

def parse_trade_alert(trade: Dict[str, Any], currency: str, timestamp: str) -> Dict[str, Any]:
    """解析交易提醒"""
    title = trade.get('title', '')
    message = trade.get('message', '')

    source = 'Unknown'
    if 'Deribit' in message or 'deribit' in message.lower():
        source = 'Deribit'
    elif 'Binance' in message or 'binance' in message.lower():
        source = 'Binance'

    direction = trade.get('direction', 'unknown')
    if direction == 'unknown':
        if any(w in message.lower() for w in ['buy', '买入', '购买']):
            direction = 'buy'
        elif any(w in message.lower() for w in ['sell', '卖出', '出售']):
            direction = 'sell'

    ins_name = trade.get('instrument_name') or trade.get('symbol') or ''
    strike = trade.get('strike')
    option_type = None

    if ins_name:
        ins_match = re.search(r'-(\d+)-([PC])$', str(ins_name))
        if ins_match:
            try:
                strike = float(ins_match.group(1))
                option_type = 'PUT' if ins_match.group(2) == 'P' else 'CALL'
            except ValueError:
                pass

    if not strike:
        msg_match = re.search(r'(?:strike|行权价)?[:\s]*(\d{3}(?:,\d{3})*)\s*(?:PUT|CALL|-[PC])', message, re.IGNORECASE)
        if msg_match:
            try:
                strike = float(msg_match.group(1).replace(',', ''))
            except ValueError:
                pass

    if not option_type:
        if 'PUT' in message.upper() or 'put' in message.lower():
            option_type = 'PUT'
        elif 'CALL' in message.upper() or 'call' in message.lower():
            option_type = 'CALL'

    volume = trade.get('amount', 0) or trade.get('volume', 0) or 0
    # Only parse from message if volume is still 0 and message contains contract count
    if not volume or volume == 0:
        # First try to find explicit contract count (e.g., "100 contracts", "100张")
        contract_match = re.search(r'(\d+(?:,\d{3})*)\s*(?:contracts?|张)', message, re.IGNORECASE)
        if contract_match:
            try:
                volume = float(contract_match.group(1).replace(',', ''))
            except ValueError:
                pass
        # If no contract count found, try to extract from crypto amount (e.g., "5.2 BTC worth")
        if not volume:
            crypto_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:BTC|ETH|SOL)\s*(?:worth|价值)', message, re.IGNORECASE)
            if crypto_match:
                try:
                    volume = float(crypto_match.group(1))
                except ValueError:
                    pass
        # Note: We intentionally do NOT parse $ amounts as volume, since that's notional value

    notional_usd = trade.get('underlying_notional_usd', 0) or 0
    if not notional_usd or notional_usd == 0:
        notional_match = re.search(r'\$([\d,]+(?:\.\d+)?)\s*(?:USD|usd)?', message)
        if notional_match:
            try:
                notional_usd = float(notional_match.group(1).replace(',', ''))
                if 'M' in message.upper():
                    notional_usd *= 1_000_000
                elif 'K' in message.upper():
                    notional_usd *= 1_000
            except ValueError:
                pass

    delta = trade.get('delta', 0) or 0
    flow_label = trade.get('flow_label', '')

    return {
        'timestamp': timestamp,
        'currency': currency,
        'source': source,
        'title': title,
        'message': message,
        'direction': direction,
        'strike': strike,
        'volume': volume,
        'option_type': option_type,
        'flow_label': flow_label,
        'notional_usd': float(notional_usd) if notional_usd else 0,
        'delta': float(delta) if delta else 0,
        'instrument_name': ins_name,
    }

def get_flow_label_info(flow_key: str) -> tuple:
    """获取流向标签的中文名称和描述"""
    info = FLOW_LABEL_MAP.get(flow_key, FLOW_LABEL_MAP["unknown"])
    return info
