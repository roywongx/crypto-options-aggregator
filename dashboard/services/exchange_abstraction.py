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
from datetime import datetime, timezone

from services.http_client import get_async_client

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
        from datetime import datetime, timezone
        try:
            # 尝试解析 Deribit 格式: 15MAY26
            exp_date = datetime.strptime(self.expiry, '%d%b%y')
            return max(1, (exp_date - datetime.now(timezone.utc)).days)
        except (ValueError, TypeError):
            try:
                # 尝试解析标准格式: 2025-06-27
                exp_date = datetime.strptime(self.expiry, '%Y-%m-%d')
                return max(1, (exp_date - datetime.now(timezone.utc)).days)
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
        margin_est = max(self.strike * 0.2, (self.strike - self.premium_usd) * 0.2)
        return (self.premium_usd / max(margin_est, 1)) * (365 / self.dte) * 100

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

    # ── Rate limiting ──
    _last_request_time: float = 0.0
    _min_interval: float = 0.2  # 200ms between requests

    async def _rate_limit(self):
        """简单的请求间隔控制"""
        import time as _time
        now = _time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_time = _time.time()

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

    async def get_dvol(self, currency: str = "BTC") -> float:
        """获取 DVOL 指数（默认调用 Deribit，子类可覆盖）

        Args:
            currency: BTC/ETH

        Returns:
            float: DVOL 指数值
        """
        from services.dvol_analyzer import get_dvol_from_deribit
        try:
            return await asyncio.to_thread(get_dvol_from_deribit, currency) or 0
        except Exception:
            return 0.0

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

    @abstractmethod
    async def get_historical_klines(
        self,
        currency: str = "BTC",
        interval: str = "1d",
        limit: int = 365,
        start_time: Optional[int] = None,
    ) -> List[Dict]:
        """获取历史 K 线数据（用于回测）

        Returns:
            [{"date": "2025-01-01", "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}, ...]
        """
        ...

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
        now = datetime.now(timezone.utc)
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
        except (OSError, IOError, RuntimeError) as e:
            logger.error("Binance get_dvol error: %s", str(e))
            return 0

    async def get_spot_price(self, currency: str = "BTC") -> float:
        from services.spot_price import get_spot_price
        try:
            spot = await asyncio.to_thread(get_spot_price, currency)
            return spot or 0
        except (OSError, IOError, RuntimeError) as e:
            logger.error("Binance get_spot_price error: %s", str(e))
            return 0

    async def get_funding_rate(self, currency: str = "BTC") -> float:
        import httpx
        try:
            client = get_async_client()
            resp = await client.get(
                "https://fapi.binance.com/fapi/v1/premiumIndex",
                params={"symbol": f"{currency}USDT"}
            )
            resp.raise_for_status()
            data = resp.json()
            return float(data.get("lastFundingRate", 0))
        except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
            logger.error("Binance get_funding_rate error: %s", str(e))
            return 0

    async def get_open_interest(self, currency: str = "BTC") -> Dict[str, float]:
        import httpx
        try:
            client = get_async_client()
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
        except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
            logger.error("Binance get_open_interest error: %s", str(e))
            return {}

    async def get_historical_klines(
        self,
        currency: str = "BTC",
        interval: str = "1d",
        limit: int = 365,
        start_time: Optional[int] = None,
    ) -> List[Dict]:
        await self._rate_limit()
        try:
            symbol = f"{currency}USDT"
            params = {"symbol": symbol, "interval": interval, "limit": min(limit, 500)}
            if start_time:
                params["startTime"] = start_time
            client = get_async_client()
            resp = await client.get("https://api.binance.com/api/v3/klines", params=params)
            resp.raise_for_status()
            klines = []
            for item in resp.json():
                klines.append({
                    "date": datetime.fromtimestamp(item[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "open": float(item[1]), "high": float(item[2]),
                    "low": float(item[3]), "close": float(item[4]),
                    "volume": float(item[5]),
                })
            return sorted(klines, key=lambda k: k["date"])
        except Exception as e:
            logger.warning("Binance klines fetch failed: %s", e)
            return []

    def _calculate_expiry(self, dte: float) -> str:
        from datetime import timedelta
        expiry = datetime.now(timezone.utc) + timedelta(days=dte)
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
        def _fetch():
            from services.monitors import get_deribit_monitor
            mon = get_deribit_monitor()
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
        except (OSError, IOError, RuntimeError) as e:
            logger.error("Deribit get_dvol error: %s", str(e))
            return 0

    async def get_spot_price(self, currency: str = "BTC") -> float:
        from services.spot_price import get_spot_price_deribit
        try:
            spot = await asyncio.to_thread(get_spot_price_deribit, currency)
            return spot or 0
        except (OSError, IOError, RuntimeError) as e:
            logger.error("Deribit get_spot_price error: %s", str(e))
            return 0

    async def get_funding_rate(self, currency: str = "BTC") -> float:
        try:
            client = get_async_client()
            resp = await client.get(
                f"{self._base_url}/public/get_funding_rate",
                params={"instrument_name": f"{currency}-PERPETUAL"}
            )
            resp.raise_for_status()
            data = resp.json()
            return float(data.get("result", 0))
        except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
            logger.error("Deribit get_funding_rate error: %s", str(e))
            return 0

    async def get_open_interest(self, currency: str = "BTC") -> Dict[str, float]:
        try:
            client = get_async_client()
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
        except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
            logger.error("Deribit get_open_interest error: %s", str(e))
            return {}

    async def get_historical_klines(
        self,
        currency: str = "BTC",
        interval: str = "1d",
        limit: int = 365,
        start_time: Optional[int] = None,
    ) -> List[Dict]:
        await self._rate_limit()
        try:
            # Deribit uses resolution: 1D, start_timestamp (ms), count
            params = {"resolution": interval.upper(), "limit": min(limit, 1000)}
            if start_time:
                params["start_timestamp"] = start_time
            client = get_async_client()
            resp = await client.get(
                f"{self._base_url}/public/get_tradingview_chart_data",
                params={"instrument_name": f"{currency}-PERPETUAL", **params}
            )
            resp.raise_for_status()
            data = resp.json().get("result", {})
            ticks = data.get("ticks", [])
            opens = data.get("open", [])
            highs = data.get("high", [])
            lows = data.get("low", [])
            closes = data.get("close", [])
            volumes = data.get("volume", [])
            klines = []
            for i, t in enumerate(ticks):
                dt = datetime.fromtimestamp(t / 1000, tz=timezone.utc)
                klines.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "open": float(opens[i]) if i < len(opens) else 0,
                    "high": float(highs[i]) if i < len(highs) else 0,
                    "low": float(lows[i]) if i < len(lows) else 0,
                    "close": float(closes[i]) if i < len(closes) else 0,
                    "volume": float(volumes[i]) if i < len(volumes) else 0,
                })
            return klines
        except Exception as e:
            logger.warning("Deribit klines fetch failed: %s", e)
            return []


# ============================================================
# BYBIT Exchange Adapter
# ============================================================

class BybitExchange(BaseExchange):
    """Bybit 交易所适配器 — 期权链 + 现货 + 历史K线"""

    def __init__(self):
        self._base_url = "https://api.bybit.com"
        self._min_interval = 0.3  # 300ms rate limit

    @property
    def exchange_type(self) -> ExchangeType:
        return ExchangeType.BYBIT

    @property
    def name(self) -> str:
        return "Bybit"

    async def get_options_chain(
        self,
        currency: str,
        option_type: OptionType,
        min_dte: int = 5,
        max_dte: int = 45,
        max_delta: float = 0.6,
        min_volume: float = 0,
        max_spread_pct: float = 20.0,
        strike_range: Optional[tuple] = None,
    ) -> List[OptionContract]:
        await self._rate_limit()
        try:
            client = get_async_client()
            resp = await client.get(
                f"{self._base_url}/v5/market/tickers",
                params={"category": "option", "baseCoin": currency}
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("retCode") != 0:
                logger.warning("Bybit options chain error: %s", data.get("retMsg"))
                return []

            contracts = []
            now_dt = datetime.now(timezone.utc)
            for item in data.get("result", {}).get("list", []):
                symbol = item.get("symbol", "")
                # Parse Bybit symbol: BTC-27JUN25-90000-P
                parts = symbol.split("-")
                if len(parts) < 4:
                    continue
                try:
                    exp_date = datetime.strptime(parts[1], "%d%b%y").replace(tzinfo=timezone.utc)
                    dte = max(1, (exp_date - now_dt).days)
                except ValueError:
                    continue
                if not (min_dte <= dte <= max_dte):
                    continue

                opt_type_str = parts[-1]
                if option_type == OptionType.PUT and opt_type_str != "P":
                    continue
                if option_type == OptionType.CALL and opt_type_str != "C":
                    continue

                strike = float(parts[-2])
                mark_price = float(item.get("markPrice", 0))
                bid = float(item.get("bid1Price", 0))
                ask = float(item.get("ask1Price", 0))
                iv = float(item.get("markIv", 0)) * 100  # Bybit returns decimal
                delta = abs(float(item.get("delta", 0)))
                if delta > max_delta:
                    continue

                contracts.append(OptionContract(
                    symbol=symbol,
                    exchange=ExchangeType.BYBIT,
                    currency=currency,
                    option_type=option_type,
                    strike=strike,
                    expiry=exp_date.strftime("%Y-%m-%d"),
                    mark_price=mark_price,
                    bid=bid, ask=ask,
                    volume=float(item.get("volume24h", 0)),
                    open_interest=float(item.get("openInterest", 0)),
                    delta=delta,
                    iv=round(iv, 1),
                    spread_pct=((ask - bid) / mark_price * 100) if mark_price > 0 and bid > 0 else 0,
                    raw_data=item,
                    underlying_price=float(item.get("underlyingPrice", 0)),
                ))
            return contracts
        except (httpx.HTTPError, httpx.TimeoutException, RuntimeError, ValueError, KeyError) as e:
            logger.warning("Bybit options chain fetch failed: %s", e)
            return []

    async def get_historical_klines(
        self,
        currency: str = "BTC",
        interval: str = "1d",
        limit: int = 365,
        start_time: Optional[int] = None,
    ) -> List[Dict]:
        await self._rate_limit()
        try:
            symbol = f"{currency}USDT"
            params = {"category": "spot", "symbol": symbol, "interval": interval, "limit": min(limit, 200)}
            if start_time:
                params["start"] = start_time

            client = get_async_client()
            resp = await client.get(f"{self._base_url}/v5/market/kline", params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("retCode") != 0:
                return []

            klines = []
            for item in data.get("result", {}).get("list", []):
                klines.append({
                    "date": datetime.fromtimestamp(int(item[0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "open": float(item[1]), "high": float(item[2]),
                    "low": float(item[3]), "close": float(item[4]),
                    "volume": float(item[5]),
                })
            return sorted(klines, key=lambda k: k["date"])
        except (httpx.HTTPError, httpx.TimeoutException, RuntimeError, ValueError) as e:
            logger.warning("Bybit klines fetch failed: %s", e)
            return []

    async def get_dvol(self, currency: str = "BTC") -> float:
        from services.dvol_analyzer import get_dvol_from_deribit
        try:
            return await asyncio.to_thread(get_dvol_from_deribit, currency) or 0
        except Exception:
            return 0

    async def get_spot_price(self, currency: str = "BTC") -> float:
        await self._rate_limit()
        try:
            client = get_async_client()
            resp = await client.get(
                f"{self._base_url}/v5/market/tickers",
                params={"category": "spot", "symbol": f"{currency}USDT"}
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("result", {}).get("list", [])
            return float(items[0]["lastPrice"]) if items else 0
        except Exception as e:
            logger.warning("Bybit spot price failed: %s", e)
            return 0

    async def get_funding_rate(self, currency: str = "BTC") -> float:
        await self._rate_limit()
        try:
            client = get_async_client()
            resp = await client.get(
                f"{self._base_url}/v5/market/tickers",
                params={"category": "linear", "symbol": f"{currency}USDT"}
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("result", {}).get("list", [])
            return float(items[0].get("fundingRate", 0)) if items else 0
        except Exception as e:
            logger.warning("Bybit funding rate failed: %s", e)
            return 0

    async def get_open_interest(self, currency: str = "BTC") -> Dict[str, float]:
        await self._rate_limit()
        try:
            client = get_async_client()
            resp = await client.get(
                f"{self._base_url}/v5/market/tickers",
                params={"category": "option", "baseCoin": currency}
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("retCode") != 0:
                return {}
            return {
                item["symbol"]: float(item.get("openInterest", 0))
                for item in data.get("result", {}).get("list", [])
                if item.get("symbol")
            }
        except Exception as e:
            logger.warning("Bybit OI failed: %s", e)
            return {}


# ============================================================
# OKX Exchange Adapter
# ============================================================

class OkxExchange(BaseExchange):
    """OKX 交易所适配器 — 期权链 + 现货 + 历史K线"""

    def __init__(self):
        self._base_url = "https://www.okx.com"
        self._min_interval = 0.3

    @property
    def exchange_type(self) -> ExchangeType:
        return ExchangeType.OKX

    @property
    def name(self) -> str:
        return "OKX"

    async def get_options_chain(
        self,
        currency: str,
        option_type: OptionType,
        min_dte: int = 5,
        max_dte: int = 45,
        max_delta: float = 0.6,
        min_volume: float = 0,
        max_spread_pct: float = 20.0,
        strike_range: Optional[tuple] = None,
    ) -> List[OptionContract]:
        await self._rate_limit()
        okx_uly = f"{currency}-USD"
        try:
            client = get_async_client()
            resp = await client.get(
                f"{self._base_url}/api/v5/public/opt-summary",
                params={"uly": okx_uly, "expTime": ""}
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0":
                logger.warning("OKX options chain error: %s", data.get("msg"))
                return []

            contracts = []
            now_dt = datetime.now(timezone.utc)
            for item in data.get("data", []):
                inst_id = item.get("instId", "")
                # Parse OKX instId: BTC-USD-250627-90000-P
                parts = inst_id.split("-")
                if len(parts) < 5:
                    continue
                try:
                    exp_str = parts[2]
                    exp_date = datetime.strptime(exp_str, "%y%m%d").replace(tzinfo=timezone.utc)
                    dte = max(1, (exp_date - now_dt).days)
                except ValueError:
                    continue
                if not (min_dte <= dte <= max_dte):
                    continue

                opt_type_str = parts[-1]
                if option_type == OptionType.PUT and opt_type_str != "P":
                    continue
                if option_type == OptionType.CALL and opt_type_str != "C":
                    continue

                strike = float(parts[-2])
                mark_price = float(item.get("markPrice", 0))
                bid = float(item.get("bidPx", 0))
                ask = float(item.get("askPx", 0))
                iv = float(item.get("markVol", 0)) if item.get("markVol") else 50
                delta = abs(float(item.get("delta", 0)))
                if delta > max_delta:
                    continue

                contracts.append(OptionContract(
                    symbol=inst_id,
                    exchange=ExchangeType.OKX,
                    currency=currency,
                    option_type=option_type,
                    strike=strike,
                    expiry=exp_date.strftime("%Y-%m-%d"),
                    mark_price=mark_price,
                    bid=bid, ask=ask,
                    volume=float(item.get("vol24h", 0)),
                    open_interest=float(item.get("openInterest", 0)),
                    delta=delta,
                    iv=round(iv, 1),
                    spread_pct=((ask - bid) / mark_price * 100) if mark_price > 0 and bid > 0 else 0,
                    raw_data=item,
                    underlying_price=float(item.get("ulyPrice", 0)),
                ))
            return contracts
        except (httpx.HTTPError, httpx.TimeoutException, RuntimeError, ValueError, KeyError) as e:
            logger.warning("OKX options chain fetch failed: %s", e)
            return []

    async def get_historical_klines(
        self,
        currency: str = "BTC",
        interval: str = "1d",
        limit: int = 365,
        start_time: Optional[int] = None,
    ) -> List[Dict]:
        await self._rate_limit()
        try:
            inst_id = f"{currency}-USDT"
            params = {"instId": inst_id, "bar": interval, "limit": min(limit, 300)}
            if start_time:
                params["before"] = str(start_time)

            client = get_async_client()
            resp = await client.get(f"{self._base_url}/api/v5/market/candles", params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0":
                return []

            klines = []
            for item in data.get("data", []):
                # OKX format: [ts, open, high, low, close, vol, ...]
                klines.append({
                    "date": datetime.fromtimestamp(int(item[0]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "open": float(item[1]), "high": float(item[2]),
                    "low": float(item[3]), "close": float(item[4]),
                    "volume": float(item[5]),
                })
            return sorted(klines, key=lambda k: k["date"])
        except (httpx.HTTPError, httpx.TimeoutException, RuntimeError, ValueError) as e:
            logger.warning("OKX klines fetch failed: %s", e)
            return []

    async def get_dvol(self, currency: str = "BTC") -> float:
        from services.dvol_analyzer import get_dvol_from_deribit
        try:
            return await asyncio.to_thread(get_dvol_from_deribit, currency) or 0
        except Exception:
            return 0

    async def get_spot_price(self, currency: str = "BTC") -> float:
        await self._rate_limit()
        try:
            client = get_async_client()
            resp = await client.get(
                f"{self._base_url}/api/v5/market/ticker",
                params={"instId": f"{currency}-USDT"}
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data", [])
            return float(items[0]["last"]) if items else 0
        except Exception as e:
            logger.warning("OKX spot price failed: %s", e)
            return 0

    async def get_funding_rate(self, currency: str = "BTC") -> float:
        await self._rate_limit()
        try:
            client = get_async_client()
            resp = await client.get(
                f"{self._base_url}/api/v5/public/funding-rate",
                params={"instId": f"{currency}-USD-SWAP"}
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data", [])
            return float(items[0].get("fundingRate", 0)) if items else 0
        except Exception as e:
            logger.warning("OKX funding rate failed: %s", e)
            return 0

    async def get_open_interest(self, currency: str = "BTC") -> Dict[str, float]:
        await self._rate_limit()
        try:
            client = get_async_client()
            resp = await client.get(
                f"{self._base_url}/api/v5/public/opt-summary",
                params={"uly": f"{currency}-USD", "expTime": ""}
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0":
                return {}
            return {
                item["instId"]: float(item.get("openInterest", 0))
                for item in data.get("data", [])
                if item.get("instId")
            }
        except Exception as e:
            logger.warning("OKX OI failed: %s", e)
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
        self.register(BybitExchange())
        self.register(OkxExchange())

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
            except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
                logger.error("%s chain error: %s", ex_type.value, str(e))
                summary[ex_type.value] = []
        return summary

    async def get_multi_exchange_best_bid_ask(
        self,
        currency: str,
        option_type: OptionType,
    ) -> Dict[str, Any]:
        """跨交易所获取最佳买卖报价（流动性聚合）"""
        best_bid = {"price": 0.0, "exchange": ""}
        best_ask = {"price": float("inf"), "exchange": ""}
        all_quotes = {}

        for ex_type, exchange in self._exchanges.items():
            try:
                chain = await exchange.get_options_chain(currency, option_type)
                if not chain:
                    continue
                # Find the contract with tightest spread
                for c in chain:
                    if c.bid > best_bid["price"]:
                        best_bid = {"price": c.bid, "exchange": ex_type.value, "symbol": c.symbol}
                    if c.ask < best_ask["price"] and c.ask > 0:
                        best_ask = {"price": c.ask, "exchange": ex_type.value, "symbol": c.symbol}
                all_quotes[ex_type.value] = len(chain)
            except Exception as e:
                logger.debug("%s bid/ask fetch failed: %s", ex_type.value, e)
                all_quotes[ex_type.value] = 0

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": round(best_ask["price"] - best_bid["price"], 2) if best_ask["price"] > 0 and best_bid["price"] > 0 else 0,
            "contracts_found": all_quotes,
        }

    async def get_historical_klines_all(
        self,
        currency: str = "BTC",
        interval: str = "1d",
        limit: int = 365,
    ) -> Dict[str, List[Dict]]:
        """从所有交易所获取历史 K 线"""
        results = {}
        for ex_type, exchange in self._exchanges.items():
            try:
                klines = await exchange.get_historical_klines(currency, interval, limit)
                if klines:
                    results[ex_type.value] = klines
            except Exception as e:
                logger.debug("%s klines failed: %s", ex_type.value, e)
        return results


_registry: 'Optional[ExchangeRegistry]' = None


def get_exchange_registry() -> 'ExchangeRegistry':
    """惰性初始化交易所注册表，避免导入时某交易所不可用导致整个模块导入失败"""
    global _registry
    if _registry is None:
        _registry = ExchangeRegistry()
    return _registry


# 模块级惰性代理，兼容 `from exchange_abstraction import registry` 用法
class _RegistryProxy:
    """代理所有属性访问到惰性初始化的 ExchangeRegistry 单例"""
    def __getattr__(self, name):
        return getattr(get_exchange_registry(), name)
    def __repr__(self):
        return repr(get_exchange_registry())


registry = _RegistryProxy()