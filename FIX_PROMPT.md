# 修复任务提示词

你是一个高级 Python 后端工程师，需要修复 crypto-options-aggregator 项目中的审计问题。

## 项目背景

这是一个 FastAPI + SQLite 的加密货币期权聚合交易仪表盘，支持 Binance + Deribit 实时数据。

**架构**: FastAPI 单 worker + DataHub 内存缓存 + SQLite 持久化 + WebSocket 实时推送

## 修复优先级

按以下顺序修复，每个修复完成后运行测试确认无回归：

```
pytest dashboard/tests/ -v
```

---

## 第一批：严重问题（必须修复）

### 1. API 密钥加密存储

**文件**: `dashboard/services/llm_analyst.py`

```python
# 在文件顶部添加
from cryptography.fernet import Fernet

# 从环境变量或 config 获取加密密钥
_ENCRYPTION_KEY = os.environ.get("LLM_KEY_ENCRYPTION_KEY") or Fernet.generate_key()
_fernet = Fernet(_ENCRYPTION_KEY)

def _encrypt_key(key: str) -> str:
    return _fernet.encrypt(key.encode()).decode()

def _decrypt_key(encrypted: str) -> str:
    return _fernet.decrypt(encrypted.encode()).decode()
```

修改 `save_config()` 中写入时调用 `_encrypt_key(api_key)`，`_get_custom_config()` 读取时调用 `_decrypt_key()`。

### 2. Config.to_dict() 过滤敏感字段

**文件**: `dashboard/config.py`

```python
_SENSITIVE_KEYS = {"KEY", "SECRET", "PASSWORD", "TOKEN"}

def to_dict(self) -> dict:
    return {
        k: v for k, v in self.__class__.__dict__.items()
        if not k.startswith("_")
        and not any(s in k.upper() for s in _SENSITIVE_KEYS)
        and not callable(v)
    }
```

### 3. DataHub 读操作加锁

**文件**: `dashboard/services/datahub.py`

```python
async def get_snapshot(self, topic: str) -> dict:
    async with self._lock:
        return dict(self._topic_data.get(topic, {}))

async def get_options_chain_snapshot(self) -> dict:
    async with self._lock:
        return dict(self._options_chain_cache)
```

或使用 copy-on-write：在 `publish()` 时替换整个引用而非原地 mutate。

### 4. httpx.AsyncClient 复用

**文件**: `dashboard/services/datahub.py`

```python
class BinanceWSConnector:
    def __init__(self, ...):
        self._client = httpx.AsyncClient(timeout=30.0)

    async def run(self):
        try:
            while self._running:
                resp = await self._client.get(url)
                ...
                await asyncio.sleep(30)
        finally:
            await self._client.aclose()
```

### 5. 保存 create_task 引用

**文件**: `dashboard/services/datahub.py`

```python
async def start(self):
    ...
    self._cleanup_handle = asyncio.create_task(self._cleanup_task())

async def stop(self):
    if hasattr(self, '_cleanup_handle'):
        self._cleanup_handle.cancel()
        try:
            await self._cleanup_handle
        except asyncio.CancelledError:
            pass
```

### 6. scan_engine 写入增加 Pydantic 验证

**文件**: `dashboard/services/scan_engine.py`

```python
from pydantic import BaseModel, validator

class TradeAlert(BaseModel):
    exchange: str
    symbol: str
    side: str
    price: float
    quantity: float
    timestamp: str

    @validator("price", "quantity")
    def must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("must be positive")
        return v
```

在 `parse_trade_alert()` 返回后用 `TradeAlert(**data)` 验证。

### 7. 模拟盘重构为三账户模型

**文件**: `dashboard/services/paper_trading.py`

核心改动：

```sql
-- paper_account 表增加字段
ALTER TABLE paper_account ADD COLUMN locked_margin REAL DEFAULT 0;
```

```python
# 开仓逻辑
new_cash = account["current_cash"] + premium_total  # 收到权利金
new_locked = account["locked_margin"] + margin_required  # 锁定保证金

# 检查: new_cash >= 0 (不能透支)
# 更新: current_cash = new_cash, locked_margin = new_locked

# 平仓逻辑
new_cash = account["current_cash"] - close_premium_total  # 支付平仓成本
new_locked = account["locked_margin"] - original_margin  # 释放保证金

# 持仓摘要
total_equity = current_cash + locked_margin + unrealized_pnl
available = current_cash  # 可用 = 现金（保证金已单独跟踪）
```

---

## 第二批：中等问题

### 8. 消除 sys.path.insert

**文件**: `dashboard/main.py`, `dashboard/services/scan_engine.py`

在项目根目录创建 `pyproject.toml`：
```toml
[project]
name = "crypto-options-dashboard"
version = "1.0.0"
packages = [{include = "dashboard"}]
```

然后 `pip install -e .`，删除所有 `sys.path.insert`。

### 9. 拆分 quick_scan

**文件**: `dashboard/services/scan_engine.py:343-588`

拆分为：
- `_fetch_spot_price(datahub, currency) -> float`
- `_fetch_deribit_options(currency) -> list`
- `_fetch_binance_options(currency) -> list`
- `_apply_quality_filter(contracts, config) -> list`
- `_apply_strategy_filter(contracts, spot, config) -> list`
- `_persist_scan_results(currency, results, large_trades) -> None`
- `quick_scan(currency, scan_type) -> dict` — 只负责编排

### 10. 提取 IV 期限结构公共函数

**新建文件**: `dashboard/services/iv_term_structure.py`

```python
async def fetch_term_structure(exchange, currencies: list[str]) -> dict:
    """统一的 IV 期限结构计算"""
    summaries = await exchange.get_summary(currencies)
    # 按 expiry 分组，取 ATM IV，计算 term structure
    ...
    return {"term_structure": [...], "contango_backwardation": ...}
```

`scan_engine.py` 和 `llm_analyst.py` 统一调用此函数。

### 11. RiskFramework 缓存修复

**文件**: `dashboard/services/risk_framework.py`

```python
async def _get_floors(self):
    now = time.time()
    if now - self._cache_timestamp < self._cache_ttl:
        return self._cached_floors

    try:
        floors = await self._compute_dynamic_floors()
        self._cached_floors = floors
        self._cache_timestamp = now
        return floors
    except Exception:
        self._cache_timestamp = now  # 关键：失败也要更新时间戳
        return self._static_floors
```

### 12. 重连添加 jitter

**文件**: `dashboard/services/datahub.py`

```python
import random

delay = min(base_delay * (2 ** attempt), max_delay)
jitter = random.uniform(0, delay * 0.5)
await asyncio.sleep(delay + jitter)
```

### 13. paper_trading 并发控制

**文件**: `dashboard/services/paper_trading.py`

```python
import asyncio

_paper_lock = asyncio.Lock()

async def paper_open_position(...):
    async with _paper_lock:
        # 原有逻辑
        ...
```

### 14. ThreadPoolExecutor 清理

**文件**: `dashboard/main.py` lifespan

```python
from dashboard.services.scan_engine import _scan_executor

@asynccontextmanager
async def lifespan(app):
    yield
    _scan_executor.shutdown(wait=False)
```

---

## 第三批：轻微问题

### 15. 模拟盘使用 BS 模型

**文件**: `dashboard/services/paper_trading.py`

```python
from dashboard.services.quant_engine import bs_put_price, bs_call_price

def _estimate_current_premium(self, contract, spot, dte, iv):
    if contract["type"] == "put":
        return bs_put_price(spot, contract["strike"], dte/365, iv, 0.05)
    else:
        return bs_call_price(spot, contract["strike"], dte/365, iv, 0.05)
```

### 16. get_trade_history 使用 Row 字典访问

```python
# 替换 r[0], r[1]... 为
for r in rows:
    trade = {
        "id": r["id"],
        "symbol": r["symbol"],
        "side": r["side"],
        ...
    }
```

### 17. 魔法数字提取

**文件**: `dashboard/config.py`

```python
# 新增常量
OI_MIN_THRESHOLD = 10
DEFAULT_MARGIN_RATIO = 0.2
TIME_DECAY_DAILY_RATE = 0.02
MIN_IV_THRESHOLD = 0.0
```

### 18. 统一异常捕获

```python
# 替换
except (RuntimeError, ConnectionError, TimeoutError, Exception) as e:
# 为
except Exception as e:
```

### 19. K线数据解析修复

**文件**: `dashboard/services/exchange_abstraction.py:596-633`

```python
# Deribit 返回格式
data = result["result"]
for i in range(len(data["ticks"])):
    klines.append({
        "timestamp": data["ticks"][i],
        "open": data["open"][i],
        "high": data["high"][i],
        "low": data["low"][i],
        "close": data["close"][i],
        "volume": data["volume"][i],
    })
```

### 20. 数据库添加索引

```sql
CREATE INDEX IF NOT EXISTS idx_scan_records_currency_ts
ON scan_records(currency, timestamp);

CREATE INDEX IF NOT EXISTS idx_large_trades_currency_ts
ON large_trades_history(currency, timestamp);
```

---

## 验证清单

每完成一个修复，确认：

- [ ] `pytest dashboard/tests/ -v` 全部通过
- [ ] 手动启动 `python dashboard/main.py` 无报错
- [ ] 相关 API 端点返回正常
- [ ] 无新增 lint 警告

## 注意事项

1. **单 worker 模式**: 系统使用内存单例，不要引入多进程
2. **SQLite 限制**: 不支持 ALTER TABLE ADD COLUMN 的并发，需在启动时执行
3. **异步一致性**: 确保新增锁使用 `asyncio.Lock()` 而非 `threading.Lock()`
4. **不要破坏现有 API**: 所有修复应向后兼容
