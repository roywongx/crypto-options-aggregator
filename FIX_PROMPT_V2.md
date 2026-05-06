# 第二轮修复任务提示词

你是一个高级 Python 后端工程师，需要修复 crypto-options-aggregator 项目第二轮审计发现的问题。

## 项目背景

- FastAPI + SQLite 加密货币期权聚合交易仪表盘
- 112 个 Python 文件，31,086 行代码
- 单 worker 模式运行
- 上轮已修复 25/27 个问题，本轮新发现 21 个问题

## 修复顺序

按优先级：严重 → 中等 → 轻微。每修复一个运行测试确认无回归：

```bash
cd dashboard && pytest tests/ -v
```

---

## 第一批：严重问题 (6 个)

### C1. options_debate_engine.py — ThreadPoolExecutor 提升为全局

**文件**: `dashboard/services/options_debate_engine.py`

```python
# 在模块级添加
from concurrent.futures import ThreadPoolExecutor
_debate_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="debate")

# 第 98 行替换
# 原: with ThreadPoolExecutor(max_workers=4) as executor:
# 改: 直接使用 _debate_executor
results = list(_debate_executor.map(_fetch_single, sources))
```

### C2. paper_trading.py — 修复 _get_account() 索引歧义

**文件**: `dashboard/services/paper_trading.py:422-423`

```python
# 替换 SELECT * 为明确列名
cursor.execute(
    "SELECT id, initial_capital, current_cash, locked_margin, currency "
    "FROM paper_account WHERE id = 1"
)
r = cursor.fetchone()
if r is None:
    return None
return {
    "id": r[0],
    "initial_capital": r[1],
    "current_cash": r[2],
    "locked_margin": r[3] if len(r) > 3 else 0,
    "currency": r[4] if len(r) > 4 else "BTC",
}
```

或更好的方案：使用 `sqlite3.Row` 并通过列名访问。

### C3. large_trades_fetcher.py — 修复数据库连接泄漏

**文件**: `dashboard/services/large_trades_fetcher.py:303-307, 355-359`

```python
# 同步版本 (303-307)
conn = get_db_connection(read_only=True)
try:
    cursor = conn.cursor()
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()
finally:
    conn.close()

# 异步版本 (355-359) 同样模式
```

### C4. exchange_abstraction.py — 复用 httpx.AsyncClient

**文件**: `dashboard/services/exchange_abstraction.py`

```python
# 在文件顶部导入
from services.http_client import get_async_client

# 替换所有 async with httpx.AsyncClient() as client:
# 为
client = get_async_client()
resp = await client.get(url, params=params)
```

需要修改 10+ 处。

### C5. perp_basis_analyzer.py — 恢复 SSL 验证

**文件**: `dashboard/services/perp_basis_analyzer.py:32, 39, 55`

```python
# 移除 verify=False
# 原: timeout=10, verify=False, max_retries=2
# 改: timeout=10, max_retries=2
```

同样检查 `derivative_metrics.py` 中的类似问题。

### C6. api_retry.py — 传递 verify 参数或移除

**文件**: `dashboard/services/api_retry.py:48, 69`

```python
# 方案 A: 传递参数
def request_with_retry(url, params=None, timeout=10, max_retries=3, verify=True):
    ...
    return http_get(url, params=params, timeout=timeout, verify=verify)

# 方案 B: 移除参数（推荐，因为 http_client 已处理）
def request_with_retry(url, params=None, timeout=10, max_retries=3):
    ...
```

---

## 第二批：中等问题 (8 个)

### M1-M3. 模块级缓存添加线程锁

**文件**: `dashboard/services/macro_data.py`, `onchain_metrics.py`, `grid_manager.py`

```python
import threading

# macro_data.py
_fg_lock = threading.Lock()
_fg_cache = {}

def get_fg_index():
    with _fg_lock:
        if _fg_cache_time and (datetime.now() - _fg_cache_time).seconds < _fg_cache_ttl:
            return _fg_cache.get("data")
    # ... fetch and cache
    with _fg_lock:
        _fg_cache["data"] = result
        _fg_cache_time = datetime.now()
```

同样模式应用于 `onchain_metrics.py` 和 `grid_manager.py`。

### M4. portfolio_service.py — ThreadPoolExecutor 提升为全局

```python
_portfolio_executor = ThreadPoolExecutor(max_workers=7, thread_name_prefix="portfolio")

# 替换
# 原: with ThreadPoolExecutor(max_workers=7) as pool:
# 改: 直接使用 _portfolio_executor
```

### M5. scan_engine.py — 拆分 quick_scan() (245 行)

```python
async def quick_scan(currency: str, scan_type: str = "quick") -> dict:
    """主编排函数"""
    spot = await _fetch_spot_price(currency)
    options = await _fetch_deribit_options(currency)
    binance = await _fetch_binance_options(currency)

    filtered = _apply_quality_filter(options + binance, spot)
    scored = _apply_strategy_filter(filtered, spot)

    _save_scan_results(currency, scored)
    return _build_scan_response(currency, spot, scored)

async def _fetch_spot_price(currency: str) -> float:
    ...

async def _fetch_deribit_options(currency: str) -> list:
    ...

async def _fetch_binance_options(currency: str) -> list:
    ...

def _apply_quality_filter(contracts: list, spot: float) -> list:
    ...

def _apply_strategy_filter(contracts: list, spot: float) -> list:
    ...

def _save_scan_results(currency: str, results: list) -> None:
    ...

def _build_scan_response(currency: str, spot: float, results: list) -> dict:
    ...
```

### M6. 提取 IV 期限结构公共函数

**新建文件**: `dashboard/services/iv_term_structure.py`

```python
class IVTermStructureAnalyzer:
    async def fetch(self, exchange, currencies: list[str]) -> dict:
        """统一的 IV 期限结构计算"""
        summaries = await exchange.get_summary(currencies)
        # 按 expiry 分组，取 ATM IV，计算 term structure
        ...
        return {"term_structure": [...], "contango_backwardation": ...}
```

`scan_engine.py` 和 `llm_analyst.py` 统一调用此函数，删除各自内联实现。

### M7. 统一策略引擎

以 `unified_strategy_engine.py` 为基础：
1. 将 `strategy_engine.py` 的 DVOL 自适应逻辑移入
2. 将 `grid_engine.py` 的网格专用逻辑移入
3. 更新所有调用点
4. 废弃旧文件

### M8. options_debate_engine.py — 缓存加锁

同 M1 模式。

---

## 第三批：轻微问题 (7 个)

### L1. pressure_test.py — 移除冗余包装

```python
# 删除 _norm_cdf 方法，直接使用
from services.shared_calculations import norm_cdf
```

### L2. flow_classifier.py — 重命名公开函数

```python
# 原: def _classify_flow_heuristic(...)
# 改: def classify_flow_heuristic(...)

# 更新 large_trades_fetcher.py 中的导入
from services.flow_classifier import classify_flow_heuristic
```

### L3. volatility_predictor.py — 提取阈值常量

```python
DIRECTION_THRESHOLD = 0.03

actual_direction = "up" if future_val > current_val * (1 + DIRECTION_THRESHOLD) else ...
```

### L4. martingale_sandbox.py — 参数化 IV 变化率

```python
def simulate_crash(self, iv_change: float = 0.70):
    ...
```

### L5. perp_basis_analyzer.py — 添加假设注释

```python
# 假设 8 小时资金费率周期（Binance 标准）
FUNDING_PERIOD_HOURS = 8
basis_annualized = round((perp_price / spot_price - 1) * (365 * 24 / FUNDING_PERIOD_HOURS) * 100, 2)
```

### L6. backtest_engine.py — 常量移入配置

```python
@dataclass
class BacktestParams:
    taker_fee: float = 0.0005
    early_exit_profit_pct: float = 0.50
    cooldown_after_losses: int = 3
    ...
```

### L7. param_optimizer.py — 支持动态搜索空间

```python
def get_search_space(regime: str = "normal") -> Dict[str, List]:
    if regime == "high_vol":
        return {"max_delta": [0.05, 0.10, 0.15, 0.20], ...}
    return DEFAULT_SEARCH_SPACE
```

---

## 设计改进（可选）

### D5. 数据库索引

```python
# 在 db/schema.py 或 main.py 启动时执行
CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_scan_records_ts ON scan_records(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_large_trades_ts ON large_trades_history(timestamp, currency)",
    "CREATE INDEX IF NOT EXISTS idx_dvol_history_ts ON dvol_history(timestamp, currency)",
    "CREATE INDEX IF NOT EXISTS idx_paper_positions_status ON paper_positions(status, currency)",
]
```

### D8. http_client.py shutdown 清理

```python
# 在 main.py 的 lifespan
@asynccontextmanager
async def lifespan(app):
    yield
    from services.http_client import close_sync_client, close_async_client
    close_sync_client()
    await close_async_client()
```

---

## 验证清单

- [ ] 所有 6 个严重问题修复完成
- [ ] 所有 8 个中等问题修复完成
- [ ] 所有 7 个轻微问题修复完成
- [ ] `pytest tests/ -v` 全部通过
- [ ] `python main.py` 启动无报错
- [ ] 无新增 lint 警告
