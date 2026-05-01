"""
Exchange Abstraction Layer - 多交易所抽象层
功能:
- 统一 Binance / Deribit 接口
- 标准化期权链、DVOL、OI、现货价格获取
- 支持未来扩展 Bybit / OKX 等新交易所
"""
import logging
import asyncio
import httpx
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)


# ============================================================
# 数据模型
# ============================================================

class OptionType(Enum):
    CALL = "CALL"
    PUT = "PUT"


class ExchangeType(Enum):
    BINANCE = "binance"
    DERIBIT = "deribit"
    BYBIT = "bybit"
    OKX = "okx"


class OptionContract:
    """标准化期权合约数据"""
    def __init__(
        self,
        symbol: str,
        exchange: ExchangeType,
        currency: str,
        option_type: OptionType,
        strike: float,
        expiry: str,
        mark_price: float,
        bid: float = 0,
        ask: float = 0,
        volume: float = 0,
        open_interest: float = 0,
        delta: float = 0,
        gamma: float = 0,
        theta: float = 0,
        vega: float = 0,
        iv: float = 0,
        liquidity_score: float = 0,
        spread_pct: float = 0,
        raw_data: Dict = None,
        underlying_price: float = 0
    ):
        self.symbol = symbol
        self.exchange = exchange
        self.currency = currency
        self.option_type = option_type
        self.strike = strike
        self.expiry = expiry
        self.mark_price = mark_price
        self.bid = bid
        self.ask = ask
        self.volume = volume
        self.open_interest = open_interest
        self.delta = delta
        self.gamma = gamma
        self.theta = theta
        self.vega = vega
        self.iv = iv
        self.liquidity_score = liquidity_score
        self.spread_pct = spread_pct
        self.raw_data = raw_data or {}
        self.underlying_price = underlying_price

    @property
    def dte(self) -> int:
        """计算到期天数"""
        from datetime import datetime
        try:
            # 尝试解析 Deribit 格式: 15MAY26
            exp_date = datetime.strptime(self.expiry, '%d%b%y')
            return max(1, (exp_date - datetime.utcnow()).days)
        except (ValueError, TypeError):
            try:
                # 尝试解析标准格式: 2025-06-27
                exp_date = datetime.strptime(self.expiry, '%Y-%m-%d')
                return max(1, (exp_date - datetime.utcnow()).days)
            except (ValueError, TypeError):
                return 30

    @property
    def premium_usd(self) -> float:
        """权利金 (USD) - Deribit mark_price 是币本位，需要乘以现货价格"""
        if self.exchange == ExchangeType.DERIBIT and self.underlying_price > 0:
            return self.mark_price * self.underlying_price
        return self.mark_price

    @property
    def apr(self) -> float:
        """年化收益率估算"""
        if self.dte <= 0 or self.premium_usd <= 0:
            return 0
        return (self.premium_usd / max(self.strike, 1)) * (365 / self.dte) * 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange.value,
            "platform": self.exchange.value,
            "currency": self.currency,
            "option_type": self.option_type.value,
            "strike": self.strike,
            "expiry": self.expiry,
            "dte": self.dte,
            "mark_price": self.mark_price,
            "premium_usd": self.premium_usd,
            "premium": self.premium_usd,
            "bid": self.bid,
            "ask": self.ask,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "iv": self.iv,
            "apr": self.apr,
            "liquidity_score": self.liquidity_score,
            "spread_pct": self.spread_pct
        }


# ============================================================
# 抽象基类
# ============================================================

class BaseExchange(ABC):
    """交易所抽象基类
    
    所有交易所适配器必须继承此类并实现所有抽象方法。
    这样主逻辑可以统一调用，无需关心底层交易所差异。
    """

    @abstractmethod
    async def get_options_chain(
        self,
        currency: str,
        option_type: OptionType,
        min_dte: int = 5,
        max_dte: int = 45,
        max_delta: float = 0.6,
        min_volume: float = 0,
        max_spread_pct: float = 20.0,
        strike_range: Optional[tuple] = None
    ) -> List[OptionContract]:
        """获取期权链数据
        
        Args:
            currency: BTC/ETH
            option_type: CALL/PUT
            min_dte: 最小到期天数
            max_dte: 最大到期天数
            max_delta: 最大 Delta 绝对值
            min_volume: 最小成交量
            max_spread_pct: 最大买卖价差百分比
            strike_range: 行权价范围 (min, max)
        
        Returns:
            List[OptionContract]: 标准化期权合约列表
        """
        pass

    @abstractmethod
    async def get_dvol(self, currency: str = "BTC") -> float:
        """获取 DVOL 指数
        
        Args:
            currency: BTC/ETH
        
        Returns:
            float: DVOL 指数值
        """
        pass

    @abstractmethod
    async def get_spot_price(self, currency: str = "BTC") -> float:
        """获取现货价格
        
        Args:
            currency: BTC/ETH
        
        Returns:
            float: 现货价格 (USDT)
        """
        pass

    @abstractmethod
    async def get_funding_rate(self, currency: str = "BTC") -> float:
        """获取资金费率
        
        Args:
            currency: BTC/ETH
        
        Returns:
            float: 资金费率
        """
        pass

    @abstractmethod
    async def get_open_interest(self, currency: str = "BTC") -> Dict[str, float]:
        """获取未平仓合约数据
        
        Args:
            currency: BTC/ETH
        
        Returns:
            Dict[str, float]: 合约符号 -> OI 映射
        """
        pass

    @property
    @abstractmethod
    def exchange_type(self) -> ExchangeType:
        """返回交易所类型"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """返回交易所名称"""
        pass


# ============================================================
# Binance 实现
# ============================================================

class BinanceExchange(BaseExchange):
    """Binance 交易所适配器"""

    def __init__(self):
        self._exchange_info_cache = None
        self._exchange_info_cache_time = 0
        self._exchange_info_ttl = 3600  # 1小时

    @property
    def exchange_type(self) -> ExchangeType:
        return ExchangeType.BINANCE

    @property
    def name(self) -> str:
        return "Binance"

    async def get_options_chain(
        self,
        currency: str,
        option_type: OptionType,
        min_dte: int = 5,
        max_dte: int = 45,
        max_delta: float = 0.6,
        min_volume: float = 0,
        max_spread_pct: float = 20.0,
        strike_range: Optional[tuple] = None
    ) -> List[OptionContract]:
        import httpx
        import sys
        import os
        
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
        from binance_options import fetch_binance_options
        
        results = await asyncio.to_thread(
            fetch_binance_options,
            currency=currency,
            min_dte=min_dte,
            max_dte=max_dte,
            max_delta=max_delta,
            strike=None,
            strike_range=None,
            min_vol=min_volume,
            max_spread=max_spread_pct,
            margin_ratio=0.2,
            option_type=option_type.value
        )

        if isinstance(results, dict) and "error" in results:
            logger.error("Binance options chain error: %s", results["error"])
            return []

        contracts = []
        now = datetime.utcnow()
        for r in results:
            contracts.append(OptionContract(
                symbol=r["symbol"],
                exchange=ExchangeType.BINANCE,
                currency=currency,
                option_type=option_type,
                strike=r["strike"],
                expiry=self._calculate_expiry(r["dte"]),
                mark_price=r["premium_usdt"],
                bid=0,
                ask=0,
                volume=float(r.get("oi", 0)),
                open_interest=float(r.get("oi", 0)),
                delta=r["delta"],
                gamma=r.get("gamma", 0),
                theta=r.get("theta", 0),
                vega=r.get("vega", 0),
                iv=r.get("mark_iv", 0),
                liquidity_score=r.get("liquidity_score", 0),
                spread_pct=r.get("spread_pct", 0),
                raw_data=r
            ))
        return contracts

    async def get_dvol(self, currency: str = "BTC") -> float:
        from services.dvol_analyzer import get_dvol_from_deribit
        try:
            dvol = await asyncio.to_thread(get_dvol_from_deribit, currency)
            return dvol or 0
        except Exception as e:
            logger.error("Binance get_dvol error: %s", str(e))
            return 0

    async def get_spot_price(self, currency: str = "BTC") -> float:
        from services.spot_price import get_spot_price
        try:
            spot = await asyncio.to_thread(get_spot_price, currency)
            return spot or 0
        except Exception as e:
            logger.error("Binance get_spot_price error: %s", str(e))
            return 0

    async def get_funding_rate(self, currency: str = "BTC") -> float:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://fapi.binance.com/fapi/v1/premiumIndex",
                    params={"symbol": f"{currency}USDT"}
                )
                resp.raise_for_status()
                data = resp.json()
                return float(data.get("lastFundingRate", 0))
        except Exception as e:
            logger.error("Binance get_funding_rate error: %s", str(e))
            return 0

    async def get_open_interest(self, currency: str = "BTC") -> Dict[str, float]:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://eapi.binance.com/eapi/v1/openInterest",
                    params={"underlyingAsset": currency}
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    item["symbol"]: float(item.get("sumOpenInterest", 0))
                    for item in data
                }
        except Exception as e:
            logger.error("Binance get_open_interest error: %s", str(e))
            return {}

    def _calculate_expiry(self, dte: float) -> str:
        from datetime import timedelta
        expiry = datetime.utcnow() + timedelta(days=dte)
        return expiry.strftime("%Y-%m-%d")


# ============================================================
# Deribit 实现
# ============================================================

class DeribitExchange(BaseExchange):
    """Deribit 交易所适配器"""

    def __init__(self):
        self._base_url = "https://www.deribit.com/api/v2"

    @property
    def exchange_type(self) -> ExchangeType:
        return ExchangeType.DERIBIT

    @property
    def name(self) -> str:
        return "Deribit"

    async def get_options_chain(
        self,
        currency: str,
        option_type: OptionType,
        min_dte: int = 5,
        max_dte: int = 45,
        max_delta: float = 0.6,
        min_volume: float = 0,
        max_spread_pct: float = 20.0,
        strike_range: Optional[tuple] = None
    ) -> List[OptionContract]:
        import sys
        import os
        
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'deribit-options-monitor'))

        def _fetch():
            from deribit_options_monitor import DeribitOptionsMonitor
            mon = DeribitOptionsMonitor()
            try:
                return mon._get_book_summaries(currency)
            except (RuntimeError, ConnectionError, TimeoutError) as e:
                logger.warning("Deribit book summaries fetch failed: %s", e)
                return []

        results = await asyncio.to_thread(_fetch)

        contracts = []
        for r in results:
            inst_name = r.get("instrument_name", "")
            
            # Parse instrument name to get metadata
            from services.instrument import _parse_inst_name
            meta = _parse_inst_name(inst_name)
            if not meta:
                continue
            
            # Check option type filter
            if option_type == OptionType.CALL and meta.option_type != 'C':
                continue
            if option_type == OptionType.PUT and meta.option_type != 'P':
                continue
            
            # Check DTE filter
            dte = meta.dte
            if not (min_dte <= dte <= max_dte):
                continue

            # Get delta from API response or calculate
            delta = abs(r.get("delta", 0))
            if delta == 0:
                # Calculate approximate delta if not provided
                from services.dvol_analyzer import calc_delta_bs
                iv = r.get("mark_iv", 50)
                delta = abs(calc_delta_bs(meta.strike, r.get("underlying_price", 0), iv, dte, meta.option_type))
            if delta > max_delta:
                continue

            # Calculate spread percentage
            bid = r.get("bid_price", 0)
            ask = r.get("ask_price", 0)
            mark = r.get("mark_price", 0)
            spread_pct = 0
            if mark > 0 and bid > 0 and ask > 0:
                spread_pct = ((ask - bid) / mark) * 100
            if spread_pct > max_spread_pct:
                continue

            volume = r.get("volume", 0)
            if volume < min_volume:
                continue

            underlying_price = r.get("underlying_price", 0)
            contracts.append(OptionContract(
                symbol=inst_name,
                exchange=ExchangeType.DERIBIT,
                currency=currency,
                option_type=option_type,
                strike=meta.strike,
                expiry=meta.expiry,
                mark_price=mark,
                bid=bid,
                ask=ask,
                volume=volume,
                open_interest=r.get("open_interest", 0),
                delta=delta,
                gamma=r.get("gamma", 0),
                theta=r.get("theta", 0),
                vega=r.get("vega", 0),
                iv=r.get("mark_iv", 0),
                liquidity_score=100 - spread_pct if spread_pct <= 100 else 0,
                spread_pct=spread_pct,
                raw_data=r,
                underlying_price=underlying_price
            ))
        return contracts

    async def get_dvol(self, currency: str = "BTC") -> float:
        from services.dvol_analyzer import get_dvol_from_deribit
        try:
            dvol = await asyncio.to_thread(get_dvol_from_deribit, currency)
            return dvol or 0
        except Exception as e:
            logger.error("Deribit get_dvol error: %s", str(e))
            return 0

    async def get_spot_price(self, currency: str = "BTC") -> float:
        from services.spot_price import get_spot_price_deribit
        try:
            spot = await asyncio.to_thread(get_spot_price_deribit, currency)
            return spot or 0
        except Exception as e:
            logger.error("Deribit get_spot_price error: %s", str(e))
            return 0

    async def get_funding_rate(self, currency: str = "BTC") -> float:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self._base_url}/public/get_funding_rate",
                    params={"instrument_name": f"{currency}-PERPETUAL"}
                )
                resp.raise_for_status()
                data = resp.json()
                return float(data.get("result", 0))
        except Exception as e:
            logger.error("Deribit get_funding_rate error: %s", str(e))
            return 0

    async def get_open_interest(self, currency: str = "BTC") -> Dict[str, float]:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self._base_url}/public/get_book_summary_by_currency",
                    params={"currency": currency, "kind": "option"}
                )
                resp.raise_for_status()
                data = resp.json().get("result", [])
                return {
                    item["instrument_name"]: item.get("open_interest", 0)
                    for item in data
                }
        except Exception as e:
            logger.error("Deribit get_open_interest error: %s", str(e))
            return {}


# ============================================================
# Exchange Registry - 交易所注册表
# ============================================================

class ExchangeRegistry:
    """交易所注册表 - 管理和访问所有交易所适配器"""

    def __init__(self):
        self._exchanges: Dict[ExchangeType, BaseExchange] = {}
        self._register_defaults()

    def _register_defaults(self):
        self.register(BinanceExchange())
        self.register(DeribitExchange())

    def register(self, exchange: BaseExchange):
        self._exchanges[exchange.exchange_type] = exchange

    def get(self, exchange_type: ExchangeType) -> BaseExchange:
        if exchange_type not in self._exchanges:
            raise ValueError(f"Exchange {exchange_type.value} not registered")
        return self._exchanges[exchange_type]

    def list_exchanges(self) -> List[str]:
        return [ex.name for ex in self._exchanges.values()]

    async def get_multi_exchange_summary(
        self,
        currency: str,
        option_type: OptionType,
        **kwargs
    ) -> Dict[str, List[Dict]]:
        summary = {}
        for ex_type, exchange in self._exchanges.items():
            try:
                chain = await exchange.get_options_chain(
                    currency, option_type, **kwargs
                )
                summary[ex_type.value] = [c.to_dict() for c in chain]
            except Exception as e:
                logger.error(f"{ex_type.value} chain error: {str(e)}")
                summary[ex_type.value] = []
        return summary


registry = ExchangeRegistry()