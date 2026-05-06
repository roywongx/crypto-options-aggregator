# Services - Flow Classifier
import re
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

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
    """流向分类 - 基于期权希腊字母和行权价相对现货位置

    优先级: delta 值 > strike/spot 价内价外判断
    当 delta 不可用 (≈0) 时，用 strike vs spot 关系判断 moneyness。
    """
    if not direction or direction == "unknown" or not option_type:
        return "unknown"

    d = abs(delta or 0)
    is_put = option_type.upper() in ("PUT", "P")
    is_call = option_type.upper() in ("CALL", "C")

    # 用 strike/spot 判断 moneyness（delta=0 时作为 fallback）
    if spot > 0 and strike > 0:
        otm_pct = abs(strike - spot) / spot
        if is_put:
            itm = strike > spot
            deep_itm = itm and otm_pct > 0.10  # 行权价偏离现货 >10%
        elif is_call:
            itm = strike < spot
            deep_itm = itm and otm_pct > 0.10
        else:
            return "unknown"
    else:
        # 无 spot/strike 信息，靠 delta 近似
        itm = d >= 0.50
        deep_itm = d >= 0.70
        otm_pct = 0

    # delta 可信时用 delta 主判，以价内外为辅助分界
    use_delta = d > 0.05

    if direction == "buy":
        if is_put:
            if use_delta and d >= 0.70:
                return "buy_put_deep_itm"
            if deep_itm:
                return "buy_put_deep_itm"
            if use_delta and d >= 0.40:
                return "buy_put_atm"
            if itm and not deep_itm:
                return "buy_put_atm"
            return "buy_put_otm"
        elif is_call:
            if use_delta and d >= 0.40:
                return "buy_call_atm_itm"
            if itm and not deep_itm:
                return "buy_call_atm_itm"
            return "buy_call_otm"

    elif direction == "sell":
        if is_put:
            if use_delta and d >= 0.70:
                return "sell_put_deep_itm"
            if deep_itm:
                return "sell_put_deep_itm"
            if use_delta and d >= 0.40:
                return "sell_put_atm_itm"
            if itm and not deep_itm:
                return "sell_put_atm_itm"
            return "sell_put_otm"
        elif is_call:
            if use_delta and d >= 0.40:
                return "sell_call_itm"
            if itm and not deep_itm:
                return "sell_call_itm"
            return "sell_call_otm"

    return "unknown"

def _severity_from_notional(notional: float) -> str:
    if notional >= 5_000_000:
        return "mega"
    if notional >= 2_000_000:
        return "high"
    if notional >= 500_000:
        return "medium"
    if notional >= 100_000:
        return "low"
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

    volume = float(trade.get('amount', 0) or 0)
    if not volume:
        volume = float(trade.get('volume', 0) or 0)
    # 合理性校验：单笔交易合约数不应超过 100,000
    if volume > 100_000:
        logger.warning(
            "Trade volume exceeds reasonable limit: %s | instrument=%s",
            volume, ins_name
        )
        volume = 0

    notional_usd = float(trade.get('underlying_notional_usd', 0) or 0)
    if not notional_usd:
        notional_usd = float(trade.get('notional_usd', 0) or 0)
    if not notional_usd and volume:
        index_price = float(trade.get('index_price', 0) or 0)
        if index_price > 0:
            notional_usd = volume * index_price
    # 合理性校验：期权名义价值不应超过 10 亿美元
    MAX_REASONABLE_NOTIONAL = 1_000_000_000
    if notional_usd > MAX_REASONABLE_NOTIONAL:
        logger.warning(
            "Trade notional exceeds reasonable limit: %s | strike=%s | volume=%s | instrument=%s",
            notional_usd, strike, volume, ins_name
        )
        # 尝试用 volume * strike 估算
        fallback = volume * float(strike or 0) if volume and strike else 0
        notional_usd = fallback if 0 < fallback <= MAX_REASONABLE_NOTIONAL else 0

    premium_usd = float(trade.get('premium_usd', 0) or 0)
    if not premium_usd and volume:
        option_price = float(trade.get('price', 0) or trade.get('trade_price', 0) or 0)
        if option_price > 0:
            index_price = float(trade.get('index_price', 0) or 0)
            if index_price > 0:
                premium_usd = volume * option_price * index_price

    delta = float(trade.get('delta', 0) or 0)
    flow_label = trade.get('flow_label', '')
    if not flow_label or flow_label == 'unknown':
        # 自动推断流向标签
        from services.spot_price import get_spot_price
        sp = get_spot_price(currency) or 0
        flow_label = _classify_flow_heuristic(direction, option_type, delta, strike, sp)
    severity = trade.get('severity', '') or _severity_from_notional(notional_usd)

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
        'notional_usd': round(float(notional_usd), 2),
        'premium_usd': round(float(premium_usd), 2),
        'delta': float(delta),
        'instrument_name': ins_name,
        'severity': severity,
    }

def get_flow_label_info(flow_key: str) -> tuple:
    """获取流向标签的中文名称和描述"""
    info = FLOW_LABEL_MAP.get(flow_key, FLOW_LABEL_MAP["unknown"])
    return info
