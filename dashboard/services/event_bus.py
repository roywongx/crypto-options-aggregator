"""
Unified Event Bus - 统一数据流架构
功能:
- 基于 asyncio.Queue 的发布/订阅数据流
- WebSocket 实时数据推送
- 替代同步轮询，实现毫秒级数据获取
- 支持多主题订阅 (Topic-based Pub/Sub)
"""
import asyncio
import json
import logging
import time
from typing import Dict, List, Callable, Any, Optional, Set
from enum import Enum
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)


class EventType(Enum):
    SPOT_PRICE = "spot_price"
    OPTIONS_CHAIN = "options_chain"
    DVOL = "dvol"
    FUNDING_RATE = "funding_rate"
    LARGE_TRADE = "large_trade"
    FEAR_GREED = "fear_greed"
    PORTFOLIO_UPDATE = "portfolio_update"
    RISK_ALERT = "risk_alert"
    SYSTEM_STATUS = "system_status"


class Event:
    def __init__(
        self,
        event_type: EventType,
        data: Dict[str, Any],
        timestamp: float = None,
        source: str = "",
        metadata: Dict = None
    ):
        self.event_type = event_type
        self.data = data
        self.timestamp = timestamp or time.time()
        self.source = source
        self.metadata = metadata or {}
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "data": self.data,
            "timestamp": self.timestamp,
            "source": self.source,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'Event':
        return cls(
            event_type=EventType(d["event_type"]),
            data=d["data"],
            timestamp=d.get("timestamp", time.time()),
            source=d.get("source", ""),
            metadata=d.get("metadata", {})
        )


class EventPublisher:
    def __init__(self, event_bus: 'EventBus'):
        self._bus = event_bus
    
    async def publish(self, event_type: EventType, data: Dict[str, Any], source: str = ""):
        event = Event(event_type=event_type, data=data, source=source)
        await self._bus.publish(event)


class EventSubscriber:
    def __init__(self, event_bus: 'EventBus'):
        self._bus = event_bus
        self._queue: asyncio.Queue = asyncio.Queue()
        self._active = True
        self._subscribed_topics: Set[EventType] = set()
    
    def subscribe(self, *topics: EventType):
        for topic in topics:
            self._subscribed_topics.add(topic)
            self._bus._add_subscriber(topic, self)
    
    def unsubscribe(self, *topics: EventType):
        for topic in topics:
            self._subscribed_topics.discard(topic)
            self._bus._remove_subscriber(topic, self)
    
    async def receive(self, timeout: float = 1.0) -> Optional[Event]:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
    
    async def receive_all(self, max_count: int = 100, timeout: float = 0.1) -> List[Event]:
        events = []
        cutoff = time.time() + timeout
        while len(events) < max_count and time.time() < cutoff:
            event = await self.receive(timeout=0.05)
            if event:
                events.append(event)
            else:
                break
        return events
    
    def close(self):
        self._active = False


class EventBus:
    """统一事件总线
    
    实现发布/订阅模式，让 WebSocket 实时数据通过事件总线分发到各个组件。
    替代同步轮询，实现毫秒级数据获取。
    """
    
    def __init__(self):
        self._subscribers: Dict[EventType, List[EventSubscriber]] = defaultdict(list)
        self._handlers: Dict[EventType, List[Callable]] = defaultdict(list)
        self._snapshot_cache: Dict[EventType, Dict] = {}
        self._snapshot_timestamps: Dict[EventType, float] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._event_log: List[Event] = []
        self._max_log_size = 1000
    
    def register_handler(self, event_type: EventType, handler: Callable):
        self._handlers[event_type].append(handler)
    
    def _add_subscriber(self, topic: EventType, subscriber: EventSubscriber):
        if subscriber not in self._subscribers[topic]:
            self._subscribers[topic].append(subscriber)
    
    def _remove_subscriber(self, topic: EventType, subscriber: EventSubscriber):
        if subscriber in self._subscribers[topic]:
            self._subscribers[topic].remove(subscriber)
    
    async def publish(self, event: Event):
        async with self._lock:
            self._snapshot_cache[event.event_type] = event.data
            self._snapshot_timestamps[event.event_type] = event.timestamp
            
            self._event_log.append(event)
            if len(self._event_log) > self._max_log_size:
                self._event_log = self._event_log[-self._max_log_size:]
        
        for handler in self._handlers.get(event.event_type, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
                logger.error("Event handler error for %s: %s", event.event_type.value, str(e))
        
        for subscriber in self._subscribers.get(event.event_type, []):
            if subscriber._active:
                try:
                    await subscriber._queue.put(event)
                except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError) as e:
                    logger.error("Failed to deliver event to subscriber: %s", str(e))
    
    def get_snapshot(self, event_type: EventType) -> Optional[Dict]:
        return self._snapshot_cache.get(event_type)
    
    def get_snapshot_age(self, event_type: EventType) -> float:
        ts = self._snapshot_timestamps.get(event_type, 0)
        return time.time() - ts
    
    def get_all_snapshots(self) -> Dict[str, Dict]:
        return {
            k.value: v
            for k, v in self._snapshot_cache.items()
        }
    
    def get_event_history(
        self,
        event_type: Optional[EventType] = None,
        limit: int = 50
    ) -> List[Dict]:
        events = self._event_log
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return [e.to_dict() for e in events[-limit:]]
    
    async def start_background_publishers(self):
        self._running = True
        
        asyncio.create_task(self._spot_price_publisher())
        asyncio.create_task(self._dvol_publisher())
        asyncio.create_task(self._funding_rate_publisher())
        
        logger.info("EventBus background publishers started")
    
    async def _spot_price_publisher(self):
        while self._running:
            try:
                from services.spot_price import get_spot_price_async
                for currency in ["BTC", "ETH"]:
                    spot = await get_spot_price_async(currency)
                    if spot:
                        await self.publish(
                            EventType.SPOT_PRICE,
                            {"currency": currency, "price": spot, "timestamp": datetime.utcnow().isoformat()},
                            source="spot_price_service"
                        )
                await asyncio.sleep(3)
            except (RuntimeError, ValueError, TypeError) as e:
                logger.error("Spot price publisher error: %s", str(e))
                await asyncio.sleep(5)
    
    async def _dvol_publisher(self):
        while self._running:
            try:
                from services.dvol_analyzer import get_dvol_from_deribit
                for currency in ["BTC", "ETH"]:
                    dvol = await asyncio.to_thread(get_dvol_from_deribit, currency)
                    if dvol:
                        await self.publish(
                            EventType.DVOL,
                            {"currency": currency, "dvol": dvol, "timestamp": datetime.utcnow().isoformat()},
                            source="dvol_service"
                        )
                await asyncio.sleep(30)
            except (RuntimeError, ValueError, TypeError) as e:
                logger.error("DVOL publisher error: %s", str(e))
                await asyncio.sleep(30)
    
    async def _funding_rate_publisher(self):
        while self._running:
            try:
                from services.macro_data import get_funding_rate
                for currency in ["BTC", "ETH"]:
                    fr = get_funding_rate(currency)
                    if fr and fr.get("current_rate") is not None:
                        await self.publish(
                            EventType.FUNDING_RATE,
                            {
                                "currency": currency,
                                "rate": fr["current_rate"],
                                "timestamp": datetime.utcnow().isoformat()
                            },
                            source="funding_rate_service"
                        )
                await asyncio.sleep(60)
            except (RuntimeError, ValueError, TypeError) as e:
                logger.error("Funding rate publisher error: %s", str(e))
                await asyncio.sleep(60)
    
    def stop(self):
        self._running = False
        logger.info("EventBus stopped")


event_bus = EventBus()