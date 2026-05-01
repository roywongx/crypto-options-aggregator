# P1 架构债务修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 系统性解决 crypto-options-aggregator 的架构债务，提升可维护性、性能和稳定性

**Architecture:** 按责任拆分巨型文件，统一技术栈，建立结构化日志和错误处理规范

**Tech Stack:** Python 3.11+, httpx, sqlite3 + JSON1, vanilla JS (ES modules)

---

## 现状分析

| 问题 | 影响 | 优先级 |
|------|------|--------|
| scan_engine.py 1027 行，async/sync 双版本重复代码 ~140 行 | 维护成本高，修改需改两处 | P1 |
| app.js 198KB/4051 行，无模块化 | 加载慢，协作难，无错误边界 | P1 |
| 79 个裸 `except Exception:`（29 个文件） | 静默吞错，调试困难 | P1 |
| dashboard 用 monitor.db，Deribit monitor 用独立 .sqlite3 | 数据孤岛，备份复杂 | P2 |
| scan_records.contracts_data 存 JSON TEXT（每行数百 KB） | 查询慢，无法索引 | P2 |
| 混用 requests + httpx（24 处 import） | 依赖冗余，行为不一致 | P2 |

---

## Task 1: 拆分 scan_engine.py 重复代码

**Files:**
- Create: `dashboard/services/large_trades_fetcher.py`
- Modify: `dashboard/services/scan_engine.py:556-695` (删除 `_fetch_large_trades_async`)
- Modify: `dashboard/services/scan_engine.py:698-837` (删除 `_fetch_large_trades`)
- Test: `tests/test_large_trades_fetcher.py`

**设计:**
- 提取公共的 SQL 查询、结果解析、Deribit API 补充逻辑到独立函数
- `_fetch_large_trades_async` 调用公共逻辑 + `await get_spot_price_async()`
- `_fetch_large_trades` 调用公共逻辑 + `get_spot_price()`
- 消除 ~140 行重复

- [ ] **Step 1: 编写公共提取函数和测试**

```python
# large_trades_fetcher.py
import logging
from typing import List, Dict, Any
from datetime import datetime, timedelta
from db.connection import get_db_connection

logger = logging.getLogger(__name__)


def _build_large_trades_query(currency: str, days: int, limit: int):
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    sql = """
        SELECT instrument_name, direction, notional_usd, volume, strike,
               option_type, flow_label, delta, premium_usd, severity
        FROM large_trades_history
        WHERE currency = ? AND timestamp > ?
          AND instrument_name IS NOT NULL AND instrument_name != ''
          AND instrument_name != '(EMPTY)' AND strike > 100
        ORDER BY notional_usd DESC LIMIT ?
    """
    return sql, (currency, since, limit)


def _parse_large_trades_rows(rows, spot: float) -> List[Dict[str, Any]]:
    results = []
    seen = set()
    for r in rows:
        inst = (r[0] or '').strip()
        strike = r[4] or 0
        if not inst or strike <= 100 or inst in seen:
            continue
        seen.add(inst)
        results.append({
            "instrument_name": inst,
            "direction": r[1] or '',
            "notional_usd": r[2] or 0,
            "volume": r[3] or 0,
            "strike": strike,
            "option_type": r[5] or '',
            "flow_label": r[6] or '',
            "delta": r[7] or 0,
            "premium_usd": r[8] or 0,
            "severity": r[9] or 0,
            "spot": spot,
        })
    return results


def fetch_large_trades_sync(currency: str, days: int = 7, limit: int = 50, spot_fetcher=None):
    from services.spot_price import get_spot_price
    spot = spot_fetcher() if spot_fetcher else get_spot_price(currency)
    sql, params = _build_large_trades_query(currency, days, limit)
    conn = get_db_connection(read_only=True)
    cursor = conn.cursor()
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()
    return _parse_large_trades_rows(rows, spot)


async def fetch_large_trades_async(currency: str, days: int = 7, limit: int = 50, spot_fetcher=None):
    from services.spot_price import get_spot_price_async
    spot = await spot_fetcher() if spot_fetcher else await get_spot_price_async(currency)
    sql, params = _build_large_trades_query(currency, days, limit)
    conn = get_db_connection(read_only=True)
    cursor = conn.cursor()
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()
    return _parse_large_trades_rows(rows, spot)
```

- [ ] **Step 2: 修改 scan_engine.py 调用新模块**
- [ ] **Step 3: 运行测试验证**

---

## Task 2: app.js 模块化拆分

**Files:**
- Create: `dashboard/static/js/utils.js` (safeHTML, $, API_BASE, fetch 封装)
- Create: `dashboard/static/js/charts.js` (dvolChart, chartPeriods, 所有 Chart.js 逻辑)
- Create: `dashboard/static/js/scan.js` (扫描逻辑、合约表格、排序、分页)
- Create: `dashboard/static/js/strategy.js` (策略预设、滚仓、修复计算器)
- Create: `dashboard/static/js/copilot.js` (Copilot 聊天、AI 路由)
- Modify: `dashboard/static/app.js` (保留入口和全局状态，import 各模块)
- Modify: `dashboard/static/index.html` (添加 `<script type="module">`)

**设计:**
- `utils.js`: 纯工具函数，无状态
- `charts.js`: 所有 Chart.js 实例和更新逻辑
- `scan.js`: 扫描按钮、合约表格渲染、自动刷新
- `strategy.js`: 策略预设切换、滚仓建议、倍投修复
- `copilot.js`: AI 聊天窗口、消息渲染
- `app.js`: 全局状态（currentData, currentSpotPrice）+ 模块初始化

- [ ] **Step 1: 提取 utils.js**
- [ ] **Step 2: 提取 charts.js**
- [ ] **Step 3: 提取 scan.js**
- [ ] **Step 4: 提取 strategy.js**
- [ ] **Step 5: 提取 copilot.js**
- [ ] **Step 6: 重构 app.js 为入口文件**
- [ ] **Step 7: 更新 index.html 引用**
- [ ] **Step 8: 手动验证页面功能正常**

---

## Task 3: 结构化日志替换裸 except

**Files:**
- Modify: 29 个文件中的 79 处 `except Exception:`
- Create: `dashboard/services/log_config.py` (统一日志格式)

**规范:**
1. 区分异常类型：
   - `sqlite3.OperationalError` → 数据库问题，重试或报警
   - `requests.HTTPError / httpx.HTTPError` → API 问题，记录状态码
   - `json.JSONDecodeError` → 数据解析问题，记录原始片段
   - `ValueError / KeyError` → 业务逻辑问题，记录上下文
2. 禁止裸 `except Exception:`，至少 `except Exception as e:` + `logger.exception()`
3. 禁止 `print()`，全部改用 `logger.info/debug/warning/error`

**高优先级文件（异常吞错最严重）：**
- `dashboard/services/spot_price.py` (5 处)
- `dashboard/api/scan.py` (5 处)
- `dashboard/api/risk.py` (5 处)
- `dashboard/routers/status.py` (6 处)
- `dashboard/services/scan_engine.py` (7 处)

- [ ] **Step 1: 创建 log_config.py 统一格式**
- [ ] **Step 2: 修复 spot_price.py**
- [ ] **Step 3: 修复 scan_engine.py**
- [ ] **Step 4: 修复 scan.py + risk.py + status.py**
- [ ] **Step 5: 批量修复剩余文件（每次 3-5 个）**
- [ ] **Step 6: 全局搜索验证无裸 except 残留**

---

## Task 4: 双数据库统一（ monitor.db ）

**Files:**
- Modify: `deribit-options-monitor/deribit_options_monitor.py`
- Modify: `dashboard/db/schema.py`
- Modify: `dashboard/db/connection.py`

**方案:**
- Deribit monitor 的数据表（`deribit_trades`, `deribit_positions` 等）迁移到 `monitor.db`
- 在 `schema.py` 中新增 Deribit 表定义
- `deribit_options_monitor.py` 改用 `dashboard/db/connection.py` 的 `execute_write()`
- 保留独立 sqlite3 作为降级方案（环境变量 `DERIBIT_USE_LOCAL_DB=1`）

- [ ] **Step 1: 在 schema.py 添加 Deribit 表定义**
- [ ] **Step 2: 修改 deribit_options_monitor.py 使用统一连接**
- [ ] **Step 3: 验证数据写入 monitor.db 正确**
- [ ] **Step 4: 删除独立 .sqlite3 文件并更新文档**

---

## Task 5: JSON blob 规范化

**Files:**
- Modify: `dashboard/db/schema.py`
- Modify: `dashboard/services/scan_engine.py`（写入逻辑）
- Modify: `dashboard/api/scan.py`（读取逻辑）

**方案（渐进式）：**
- 短期：启用 SQLite JSON1 扩展，对 `contracts_data` 添加虚拟列索引
- 中期：拆分为 `scan_contracts` 独立表（scan_id, symbol, strike, expiry, apr, delta...）
- 长期：完全淘汰 JSON blob

**JSON1 索引示例：**
```sql
-- 添加虚拟列用于查询
ALTER TABLE scan_records ADD COLUMN top_apr REAL GENERATED ALWAYS AS (
    json_extract(top_contracts_data, '$[0].apr')
) VIRTUAL;
CREATE INDEX idx_scan_top_apr ON scan_records(top_apr);
```

- [ ] **Step 1: 验证 SQLite 编译时启用 JSON1**
- [ ] **Step 2: 添加虚拟列和索引**
- [ ] **Step 3: 修改查询使用虚拟列替代 json_extract 全表扫描**
- [ ] **Step 4: （可选）创建 scan_contracts 独立表并迁移数据**

---

## Task 6: 统一 HTTP 库为 httpx

**Files:**
- Modify: `dashboard/services/spot_price.py`
- Modify: `dashboard/services/macro_data.py`
- Modify: `dashboard/services/trades.py`
- Modify: `dashboard/services/support_calculator.py`
- Modify: `dashboard/services/onchain_metrics.py`
- Modify: `dashboard/services/derivative_metrics.py`
- Modify: `dashboard/routers/status.py`
- Modify: `dashboard/services/api_retry.py`
- Modify: `binance_options.py`
- Modify: `deribit-options-monitor/deribit_options_monitor.py`

**方案:**
- 所有 `import requests` 替换为 `import httpx`
- `requests.get()` → `httpx.get()`（同步）
- `requests.Session()` → `httpx.Client()`
- 保留 `async_http.py` 中的 `httpx.AsyncClient()` 不变
- 统一超时和重试配置

- [ ] **Step 1: 创建 http_client.py 统一封装**
- [ ] **Step 2: 替换 spot_price.py + trades.py**
- [ ] **Step 3: 替换 macro_data.py + support_calculator.py**
- [ ] **Step 4: 替换 onchain_metrics.py + derivative_metrics.py**
- [ ] **Step 5: 替换 api_retry.py + status.py**
- [ ] **Step 6: 替换 binance_options.py + deribit monitor**
- [ ] **Step 7: 从 requirements.txt 移除 requests**

---

## 执行顺序建议

```
Phase 1（高影响，低风险）:
  Task 1: 拆分 scan_engine.py 重复代码
  Task 3: 结构化日志替换裸 except

Phase 2（中等影响，中等风险）:
  Task 6: 统一 HTTP 库为 httpx
  Task 2: app.js 模块化拆分

Phase 3（长期优化）:
  Task 4: 双数据库统一
  Task 5: JSON blob 规范化
```

---

## 验证清单

- [ ] `python -m unittest discover -s tests -v` 全部通过
- [ ] 手动验证 dashboard 页面加载正常
- [ ] 手动验证扫描、滚仓、Copilot 功能正常
- [ ] `grep -r "except Exception:" dashboard/ | wc -l` 接近 0
- [ ] `grep -r "import requests" dashboard/ | wc -l` 接近 0
- [ ] `grep -r "print(" dashboard/services/ | wc -l` 接近 0
