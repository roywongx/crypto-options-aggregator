"""
DataHub - 高性能 Pub/Sub 数据中心
功能:
- 持久 WebSocket 连接 Deribit/Binance，实时推送 tick 数据
- 替代 REST 轮询，实现毫秒级数据获取
- quick_scan 不再发起网络请求，直接从缓存读取
- 主题: topic_btc_options, topic_eth_options, topic_dvol, topic_spot, topic_orderbook
"""
import asyncio
import json
import logging
import time
import websockets
from typing import Dict, Any, Optional, List, Set
from datetime import datetime, timezone
from collections import defaultdict

logger = logging.getLogger(__name__)


# ============================================================
# 主题常量
# ============================================================
TOPIC_BTC_OPTIONS = "topic_btc_options"
TOPIC_ETH_OPTIONS = "topic_eth_options"
TOPIC_DVOL = "topic_dvol"
TOPIC_SPOT = "topic_spot"
TOPIC_ORDERBOOK = "topic_orderbook"
TOPIC_FUNDING = "topic_funding"


# ============================================================
# DataHub 核心
# ============================================================
class DataHub:
    """高性能 Pub/Sub 数据中心

    替代 REST 轮询，通过 WebSocket 长连接接收实时 tick 数据，
    将扫描时间从秒级降至 <10ms。
    """

    # 内存清理配置
    MAX_CACHE_AGE_HOURS = 48       # 缓存数据最大保留时间
    CLEANUP_INTERVAL_SECONDS = 3600  # 清理间隔 (1小时)
    MAX_CHAIN_SIZE = 5000          # 单币种期权链最大条目数

    def __init__(self):
        self._topic_data: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self._topic_timestamps: Dict[str, float] = defaultdict(float)
        self._subscribers: Dict[str, List[asyncio.Queue]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._running = False

        # 期权链缓存: {symbol: {mark_price, iv, delta, ...}}
        self._options_chain_cache: Dict[str, Dict[str, Dict]] = defaultdict(dict)
        # 记录每个合约的最后更新时间，用于清理过期合约
        self._chain_item_timestamps: Dict[str, Dict[str, float]] = defaultdict(dict)

    async def publish(self, topic: str, symbol: str, data: Dict[str, Any]):
        async with self._lock:
            self._topic_data[topic][symbol] = data
            self._topic_timestamps[topic] = time.time()
            subscribers = list(self._subscribers.get(topic, []))

        for queue in subscribers:
            try:
                await queue.put({"symbol": symbol, "data": data, "timestamp": time.time()})
            except (asyncio.CancelledError, RuntimeError) as e:
                logger.debug("Queue put failed: %s", e)

    async def update_options_chain(self, currency: str, chain_data: Dict[str, Dict]):
        now = time.time()
        async with self._lock:
            self._options_chain_cache[currency].update(chain_data)
            # 记录更新时间
            for symbol in chain_data:
                self._chain_item_timestamps[currency][symbol] = now
            self._topic_timestamps[f"options_{currency}"] = now

    async def _cleanup_expired_contracts(self):
        """清理过期合约，防止内存无限增长"""
        now = time.time()
        max_age = self.MAX_CACHE_AGE_HOURS * 3600

        async with self._lock:
            for currency in list(self._options_chain_cache.keys()):
                chain = self._options_chain_cache[currency]
                timestamps = self._chain_item_timestamps[currency]

                # 找出过期合约
                expired = [
                    symbol for symbol, ts in timestamps.items()
                    if now - ts > max_age
                ]

                for symbol in expired:
                    chain.pop(symbol, None)
                    timestamps.pop(symbol, None)

                if expired:
                    logger.info("清理 %s 过期合约 %d 个，剩余 %d 个",
                               currency, len(expired), len(chain))

                # 如果超过最大大小，清理最旧的
                if len(chain) > self.MAX_CHAIN_SIZE:
                    sorted_items = sorted(timestamps.items(), key=lambda x: x[1])
                    to_remove = len(chain) - self.MAX_CHAIN_SIZE
                    for symbol, _ in sorted_items[:to_remove]:
                        chain.pop(symbol, None)
                        timestamps.pop(symbol, None)
                    logger.info("清理 %s 旧合约 %d 个，限制在 %d 个",
                               currency, to_remove, self.MAX_CHAIN_SIZE)

    async def _cleanup_task(self):
        """后台清理任务"""
        while self._running:
            try:
                await asyncio.sleep(self.CLEANUP_INTERVAL_SECONDS)
                await self._cleanup_expired_contracts()
            except asyncio.CancelledError:
                break
            except (RuntimeError, ValueError) as e:
                logger.error("Cleanup task error: %s", e)
    
    def get_snapshot(self, topic: str, symbol: str = None) -> Optional[Dict]:
        if symbol:
            return self._topic_data.get(topic, {}).get(symbol)
        return self._topic_data.get(topic)
    
    def get_options_chain_snapshot(self, currency: str) -> Dict[str, Dict]:
        return dict(self._options_chain_cache.get(currency, {}))
    
    def get_snapshot_age(self, topic: str) -> float:
        return time.time() - self._topic_timestamps.get(topic, 0)
    
    async def subscribe(self, topic: str) -> asyncio.Queue:
        queue = asyncio.Queue()
        self._subscribers[topic].append(queue)
        return queue
    
    def unsubscribe(self, topic: str, queue: asyncio.Queue):
        if queue in self._subscribers.get(topic, []):
            self._subscribers[topic].remove(queue)
    
    async def start(self):
        self._running = True
        # 启动后台清理任务
        asyncio.create_task(self._cleanup_task())
        logger.info("DataHub started (with cleanup task)")

    def stop(self):
        self._running = False
        logger.info("DataHub stopped")


datahub = DataHub()


# ============================================================
# Deribit WebSocket 连接器
# ============================================================
class DeribitWSConnector:
    """Deribit 持久 WebSocket 连接
    
    实时接收:
    - mark price / IV / Greeks (ticker)
    - orderbook 更新
    - trade 成交
    """
    
    def __init__(self, hub: DataHub, currencies: List[str] = None):
        self._hub = hub
        self._currencies = currencies or ["BTC", "ETH"]
        self._ws = None
        self._reconnect_delay = 2
        self._max_reconnect_delay = 60
    
    async def run(self):
        while self._hub._running:
            try:
                await self._connect()
            except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
                logger.error("Deribit WS error: %s, reconnecting in %ds", str(e), self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
    
    async def _connect(self):
        uri = "wss://www.deribit.com/ws/api/v2"
        async with websockets.connect(uri, ping_interval=30, ping_timeout=10) as ws:
            self._ws = ws
            self._reconnect_delay = 2
            logger.info("Deribit WebSocket connected")
            
            await self._subscribe()
            await self._listen()
    
    async def _subscribe(self):
        channels = []
        for currency in self._currencies:
            channels.append(f"ticker.{currency}.option")
            channels.append(f"trade.{currency}.option")
        
        subscribe_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "public/subscribe",
            "params": {"channels": channels}
        }
        await self._ws.send(json.dumps(subscribe_msg))
        logger.info("Subscribed to Deribit channels: %s", channels)
    
    async def _listen(self):
        async for message in self._ws:
            try:
                data = json.loads(message)
                if "params" in data and "channel" in data["params"]:
                    await self._handle_message(data["params"])
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                logger.debug("Deribit message parse error: %s", str(e))
    
    async def _handle_message(self, params: Dict):
        channel = params.get("channel", "")
        tick_data = params.get("data", {})
        
        if not tick_data:
            return
        
        instrument = tick_data.get("instrument_name", "")
        if not instrument:
            return
        
        parts = instrument.split("-")
        if len(parts) < 2:
            return
        
        currency = parts[0]
        kind = parts[2] if len(parts) > 2 else ""
        
        if kind == "option":
            mark_price = float(tick_data.get("mark_price") or 0)
            iv = float(tick_data.get("mark_iv") or 0)
            delta = float(tick_data.get("delta") or 0)
            gamma = float(tick_data.get("gamma") or 0)
            theta = float(tick_data.get("theta") or 0)
            vega = float(tick_data.get("vega") or 0)
            
            option_data = {
                "symbol": instrument,
                "mark_price": mark_price,
                "iv": iv,
                "delta": delta,
                "gamma": gamma,
                "theta": theta,
                "vega": vega,
                "best_bid": float(tick_data.get("best_bid_amount", 0)),
                "best_ask": float(tick_data.get("best_ask_amount", 0)),
                "volume": float(tick_data.get("stats", {}).get("volume", 0)),
                "open_interest": float(tick_data.get("open_interest", 0)),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            if "ticker" in channel:
                await self._hub.publish(
                    TOPIC_BTC_OPTIONS if currency == "BTC" else TOPIC_ETH_OPTIONS,
                    instrument,
                    option_data
                )
        elif kind == "future" or "PERPETUAL" in instrument:
            if "ticker" in channel:
                spot_data = {
                    "currency": currency,
                    "price": float(tick_data.get("last_price", 0)),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                await self._hub.publish(TOPIC_SPOT, currency, spot_data)


# ============================================================
# Binance WebSocket 连接器
# ============================================================
class BinanceWSConnector:
    """Binance 持久 WebSocket 连接
    
    实时接收:
    - mark price / IV (eapi@markPrice)
    - orderbook 深度
    """
    
    def __init__(self, hub: DataHub, currencies: List[str] = None):
        self._hub = hub
        self._currencies = currencies or ["BTC", "ETH"]
        self._ws = None
        self._reconnect_delay = 2
        self._max_reconnect_delay = 60
    
    async def run(self):
        while self._hub._running:
            try:
                await self._connect()
            except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
                logger.error("Binance WS error: %s, reconnecting in %ds", str(e), self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
    
    async def _connect(self):
        streams = []
        for currency in self._currencies:
            lower_currency = currency.lower()
            streams.append(f"{lower_currency}@markPrice@1s")
        
        uri = f"wss://eapi.binance.com/eapi/stream?streams={'/'.join(streams)}"
        async with websockets.connect(uri, ping_interval=30, ping_timeout=10) as ws:
            self._ws = ws
            self._reconnect_delay = 2
            logger.info("Binance WebSocket connected: %s", uri)
            
            await self._listen()
    
    async def _listen(self):
        async for message in self._ws:
            try:
                data = json.loads(message)
                if "data" in data:
                    await self._handle_message(data["data"])
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                logger.debug("Binance message parse error: %s", str(e))
    
    async def _handle_message(self, data: Dict):
        symbol = data.get("s", "")
        mark_price = float(data.get("p", 0))
        iv = float(data.get("iv", 0))
        
        if mark_price <= 0:
            return
        
        currency = "BTC" if symbol.startswith("BTC") else ("ETH" if symbol.startswith("ETH") else "")
        if not currency:
            return
        
        option_data = {
            "symbol": symbol,
            "mark_price": mark_price,
            "iv": iv,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        topic = TOPIC_BTC_OPTIONS if currency == "BTC" else TOPIC_ETH_OPTIONS
        await self._hub.publish(topic, symbol, option_data)


# ============================================================
# DVOL 计算器 (基于 WebSocket tick 实时计算)
# ============================================================
class DvolCalculator:
    """基于实时 tick 数据计算 DVOL"""
    
    def __init__(self, hub: DataHub):
        self._hub = hub
        self._iv_samples: List[float] = []
        self._last_publish = 0
    
    async def run(self):
        while self._hub._running:
            try:
                await self._calculate_and_publish()
                await asyncio.sleep(10)
            except (ValueError, TypeError, ZeroDivisionError, RuntimeError) as e:
                logger.error("DVOL calc error: %s", str(e))
                await asyncio.sleep(30)
    
    async def _calculate_and_publish(self):
        btc_chain = self._hub.get_options_chain_snapshot("BTC")
        if not btc_chain:
            return
        
        iv_values = [
            d.get("iv", 0)
            for d in btc_chain.values()
            if d.get("iv", 0) > 0 and d.get("mark_price", 0) > 0
        ]
        
        if not iv_values:
            return
        
        dvol = sum(iv_values) / len(iv_values)
        
        await self._hub.publish(
            TOPIC_DVOL,
            "BTC",
            {
                "currency": "BTC",
                "current": round(dvol, 2),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sample_count": len(iv_values)
            }
        )


# ============================================================
# 启动所有连接器
# ============================================================
async def start_datahub_services():
    """启动 DataHub 所有后台服务"""
    await datahub.start()
    
    deribit_ws = DeribitWSConnector(datahub)
    binance_ws = BinanceWSConnector(datahub)
    dvol_calc = DvolCalculator(datahub)
    
    asyncio.create_task(deribit_ws.run())
    asyncio.create_task(binance_ws.run())
    asyncio.create_task(dvol_calc.run())
    
    logger.info("DataHub services started (Deribit WS + Binance WS + DVOL Calc)")
