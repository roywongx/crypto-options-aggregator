# Trae CN 修复指南 — crypto-options-aggregator 深度审查修复

> 审查时间: 2026-05-01
> 仓库: https://github.com/roywongx/crypto-options-aggregator
> 分阶段执行，每阶段独立可验证

---

## 总览

| 阶段 | 内容 | 预估工时 | 优先级 |
|------|------|---------|--------|
| Phase 1 | 致命Bug修复 (grid_manager缺失+json导入) | 30min | P0 |
| Phase 2 | API层修复 (错误处理+安全+验证) | 1h | P1 |
| Phase 3 | 核心引擎修复 (保证金统一+策略计算) | 1.5h | P1 |
| Phase 4 | 数据层加固 (DB安全+线程安全) | 1h | P2 |
| Phase 5 | 代码清理 (死代码+硬编码+print) | 1h | P2 |
| Phase 6 | 文档+测试补齐 | 2h | P3 |

---

## Phase 1: 致命Bug修复 (P0 — 立即执行)

### Bug 1.1: grid_manager.py 不存在，4个API端点全部崩溃

**严重程度:** CRITICAL — 4个 Grid API 端点启动后调用即 ImportError
**文件:** `dashboard/routers/grid.py`
**影响端点:** GET /api/grid/list, GET /api/grid/detail, POST /api/grid/adjust, POST /api/grid/close

**问题:**
grid.py 中 4 处 `from services.grid_manager import GridManager`，但 `dashboard/services/grid_manager.py` 文件不存在。任何调用这4个端点的请求都会返回 500 Internal Server Error。

**修复方案: 创建 grid_manager.py**

创建文件 `dashboard/services/grid_manager.py`:

```python
"""
Grid Manager - 网格持仓管理服务
管理网格策略的创建、查询、调整和关闭
"""
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from db.connection import execute_read, execute_write, execute_transaction

logger = logging.getLogger(__name__)


def _ensure_grid_table():
    """确保 grid_positions 表存在"""
    try:
        execute_write("""
            CREATE TABLE IF NOT EXISTS grid_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                currency TEXT NOT NULL DEFAULT 'BTC',
                direction TEXT NOT NULL,
                strike REAL NOT NULL,
                expiry TEXT DEFAULT '',
                margin_ratio REAL DEFAULT 0.2,
                grid_count INTEGER DEFAULT 4,
                grid_range_pct REAL DEFAULT 0.15,
                total_capital REAL DEFAULT 0,
                status TEXT DEFAULT 'active',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                closed_at DATETIME,
                close_reason TEXT DEFAULT '',
                config_json TEXT DEFAULT '{}',
                notes TEXT DEFAULT ''
            )
        """)
        execute_write("""
            CREATE INDEX IF NOT EXISTS idx_grid_currency_status 
            ON grid_positions(currency, status)
        """)
    except (RuntimeError, ValueError, TypeError) as e:
        logger.warning("Grid table init failed: %s", e)


class GridManager:
    """网格持仓管理器"""

    def __init__(self):
        _ensure_grid_table()

    def list_positions(self, currency: str = "BTC") -> Dict[str, Any]:
        """获取网格持仓列表"""
        rows = execute_read("""
            SELECT id, currency, direction, strike, expiry, margin_ratio,
                   grid_count, grid_range_pct, total_capital, status,
                   created_at, updated_at, notes
            FROM grid_positions
            WHERE currency = ? AND status = 'active'
            ORDER BY created_at DESC
        """, (currency,))

        positions = []
        for row in rows:
            positions.append({
                "id": row[0],
                "currency": row[1],
                "direction": row[2],
                "strike": row[3],
                "expiry": row[4],
                "margin_ratio": row[5],
                "grid_count": row[6],
                "grid_range_pct": row[7],
                "total_capital": row[8],
                "status": row[9],
                "created_at": row[10],
                "updated_at": row[11],
                "notes": row[12],
            })

        return {
            "success": True,
            "currency": currency,
            "count": len(positions),
            "positions": positions
        }

    def get_position_detail(self, position_id: int) -> Optional[Dict[str, Any]]:
        """获取网格详情"""
        rows = execute_read("""
            SELECT id, currency, direction, strike, expiry, margin_ratio,
                   grid_count, grid_range_pct, total_capital, status,
                   created_at, updated_at, closed_at, close_reason,
                   config_json, notes
            FROM grid_positions WHERE id = ?
        """, (position_id,))

        if not rows:
            return None

        row = rows[0]
        return {
            "id": row[0],
            "currency": row[1],
            "direction": row[2],
            "strike": row[3],
            "expiry": row[4],
            "margin_ratio": row[5],
            "grid_count": row[6],
            "grid_range_pct": row[7],
            "total_capital": row[8],
            "status": row[9],
            "created_at": row[10],
            "updated_at": row[11],
            "closed_at": row[12],
            "close_reason": row[13],
            "config": json.loads(row[14]) if row[14] else {},
            "notes": row[15],
        }

    def adjust_position(
        self, position_id: int, new_strike: float,
        new_expiry: str = "", reason: str = ""
    ) -> Dict[str, Any]:
        """调整网格参数（滚仓）"""
        detail = self.get_position_detail(position_id)
        if not detail:
            return {"error": "网格持仓不存在"}

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        execute_write("""
            UPDATE grid_positions 
            SET strike = ?, expiry = ?, updated_at = ?, notes = ?
            WHERE id = ?
        """, (new_strike, new_expiry or detail["expiry"], now, reason, position_id))

        return {
            "success": True,
            "position_id": position_id,
            "old_strike": detail["strike"],
            "new_strike": new_strike,
            "message": f"网格已调整: {detail['strike']} -> {new_strike}"
        }

    def close_position(self, position_id: int, close_reason: str = "manual") -> Dict[str, Any]:
        """关闭网格持仓"""
        detail = self.get_position_detail(position_id)
        if not detail:
            return {"error": "网格持仓不存在"}

        if detail["status"] != "active":
            return {"error": f"网格已处于 {detail['status']} 状态"}

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        execute_write("""
            UPDATE grid_positions
            SET status = 'closed', closed_at = ?, close_reason = ?, updated_at = ?
            WHERE id = ?
        """, (now, close_reason, now, position_id))

        return {
            "success": True,
            "position_id": position_id,
            "message": f"网格已关闭: {close_reason}"
        }

    def create_position(
        self, currency: str, direction: str, strike: float,
        expiry: str = "", margin_ratio: float = 0.2,
        grid_count: int = 4, grid_range_pct: float = 0.15,
        total_capital: float = 0, config: dict = None
    ) -> Dict[str, Any]:
        """创建网格持仓"""
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        config_json = json.dumps(config or {}, ensure_ascii=False)

        row_id = execute_write("""
            INSERT INTO grid_positions
            (currency, direction, strike, expiry, margin_ratio, grid_count,
             grid_range_pct, total_capital, status, created_at, updated_at, config_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        """, (currency, direction, strike, expiry, margin_ratio, grid_count,
              grid_range_pct, total_capital, now, now, config_json))

        return {
            "success": True,
            "position_id": row_id,
            "message": f"网格已创建: {direction} @ {strike}"
        }
```

**验证:** 启动服务后调用 `GET /api/grid/list?currency=BTC` 应返回 200 + JSON。

---

### Bug 1.2: support_calculator.py 缺少 import json

**严重程度:** CRITICAL — _get_on_chain_price() 中 json.JSONDecodeError 会抛 NameError
**文件:** `dashboard/services/support_calculator.py`

**问题:** 第 134 行 `except (json.JSONDecodeError, ...)` 但文件头没有 `import json`。

**修复:** 在文件头添加:
```python
import json
```
在第 5 行 `import httpx` 之后添加。

**验证:** `python -c "from dashboard.services.support_calculator import DynamicSupportCalculator"` 不报错。

---

### Bug 1.3: api/risk.py 用 print() 而非 logger

**严重程度:** HIGH — 生产环境日志不可控
**文件:** `dashboard/api/risk.py` 第 175 行

**修复:**
```python
# 修改前
print(f"获取最大痛点数据失败: {e}")

# 修改后
logger.warning("获取最大痛点数据失败: %s", e)
```

---

## Phase 2: API层修复 (P1)

### Bug 2.1: config.py API_KEY 行截断

**严重程度:** HIGH — 认证可能失效
**文件:** `dashboard/config.py` 第 47 行

**问题:** 显示为 `API_KEY=os.get...EY", "")` — 需要确认实际文件内容是否完整。

**检查方法:** 打开文件确认第 47 行完整内容应为:
```python
API_KEY = os.getenv("DASHBOARD_API_KEY", "")
```
如果不是，修正为此内容。

---

### Bug 2.2: strategy_calc.py — calc_roll_plan 保证金公式不一致

**严重程度:** HIGH — 同一合约在不同端点显示不同保证金
**文件:** `dashboard/services/strategy_calc.py`

**问题:** 
- PUT 保证金: `new_qty * strike * margin_ratio` (第 ~80 行)
- CALL 保证金: `new_qty * prem_usd * 10` (第 ~80 行) — 这个 `* 10` 完全没有依据

而项目已有统一的 `services/margin_calculator.py`，公式为:
- PUT: `max(strike * 0.1, (strike - premium) * margin_ratio)`
- CALL: `max(strike * 0.1, strike * margin_ratio - premium)`

**修复:** 在 `strategy_calc.py` 中:
```python
# 在文件头添加
from services.margin_calculator import calc_margin

# 在 calc_roll_plan 函数中，替换保证金计算:
# 修改前:
margin_req = new_qty * strike * margin_ratio if c_type == 'PUT' else new_qty * prem_usd * 10

# 修改后:
margin_req = new_qty * calc_margin(strike, effective_prem_usd, c_type, margin_ratio)
```

同样修复 `calc_new_plan` 函数中的保证金计算:
```python
# 修改前:
margin_req = strike * margin_ratio if option_type == 'PUT' else prem_usd * 10

# 修改后:
margin_req = calc_margin(strike, prem_usd, option_type, margin_ratio)
```

**验证:** 同一合约通过 `/api/strategy-calc` 和 `/api/scan` 返回的 `margin_required` 应一致。

---

### Bug 2.3: strategy_calc.py — current_premium_estimate 是瞎猜的

**严重程度:** MEDIUM — 滚仓净信用计算不准
**文件:** `dashboard/services/strategy_calc.py` calc_roll_plan 函数

**问题:** `current_premium_estimate = prem_usd * 0.8` — 用新合约权利金的 80% 估算当前持仓权利金，误差可达 50%+。

**修复方案:** 从数据库或 API 获取当前持仓的实际权利金:
```python
# 方案1: 从参数传入（推荐）
# 修改 calc_roll_plan 签名，增加 current_premium 参数

# 方案2: 从 Deribit API 获取当前持仓 mark_price
# 在 calc_roll_plan 中:
if current_strike > 0:
    # 尝试从已获取的 contracts 中找到当前持仓的 mark_price
    current_mark = None
    for c in contracts:
        if abs(c.get('strike', 0) - current_strike) < 1 and c.get('dte', 0) > 0:
            current_mark = c.get('mark_price', 0) * spot  # BTC -> USD
            break
    current_premium_estimate = current_mark if current_mark and current_mark > 0 else prem_usd * 0.8
else:
    current_premium_estimate = prem_usd * 0.8
```

---

### Bug 2.4: api/strategy.py — grid 模式返回占位符

**严重程度:** HIGH — 前端 grid 模式请求返回空数据
**文件:** `dashboard/api/strategy.py` 第 ~70 行

**问题:**
```python
elif mode == "grid":
    result = {"mode": "grid", "message": "网格策略计算待实现", "spot": spot}
```
Grid 模式没有调用 `unified_strategy_engine`，返回的是占位消息。

**修复:**
```python
elif mode == "grid":
    from services.unified_strategy_engine import UnifiedStrategyEngine, StrategyMode, OptionType, StrategyParams
    engine = UnifiedStrategyEngine()
    strategy_params = StrategyParams(
        currency=params.currency,
        mode=StrategyMode.GRID,
        option_type=OptionType.PUT if params.option_type == "PUT" else OptionType.CALL,
        margin_ratio=params.margin_ratio,
        min_dte=params.min_dte,
        max_dte=params.max_dte,
    )
    # 获取合约数据
    from services.exchange_abstraction import registry, ExchangeType
    from services.scan_engine import _get_deribit_monitor
    mon = _get_deribit_monitor()
    summaries = mon.get_btc_option_summaries() if params.currency == "BTC" else mon.get_eth_option_summaries()
    contracts = [mon._enrich_contract(s) for s in summaries if s]
    contracts = [c for c in contracts if c]
    result = engine.execute(contracts, strategy_params, spot)
```

---

### Bug 2.5: 所有 except 块使用过于宽泛的异常元组

**严重程度:** MEDIUM — 错误被吞掉，调试困难
**影响文件:** 全项目约 30+ 处

**问题:** 大量 `except (RuntimeError, ValueError, TypeError, TimeoutError, ConnectionError)` 模式。虽然比 bare except 好，但仍过于宽泛。

**修复建议:** 按场景收窄:
```python
# 网络请求
except (httpx.HTTPError, httpx.ConnectError, httpx.TimeoutException) as e:

# 数据解析
except (ValueError, TypeError, KeyError) as e:

# 数据库
except sqlite3.OperationalError as e:

# 通用 fallback (最后手段)
except Exception as e:
    logger.error("Unexpected error: %s", e, exc_info=True)
```

---

## Phase 3: 核心引擎修复 (P1)

### Bug 3.1: unified_strategy_engine.py 中 margin 计算已统一 ✅

**状态:** 已修复 — `_calculate_margin` 正确委托给 `services/margin_calculator.py`

---

### Bug 3.2: risk_framework.py 中 margin 计算已统一 ✅

**状态:** 已修复 — `calc_margin_put` 和 `calc_margin_call` 正确委托给 `services/margin_calculator.py`

---

### Bug 3.3: grid_engine.py 中 simulate_scenario 的 *100 已修复 ✅

**状态:** 已修复 — 注释标注 "修复: 移除错误的 * 100"

---

### Bug 3.4: support_calculator.py 硬编码支撑位回退值

**严重程度:** MEDIUM — 回退值可能过时
**文件:** `dashboard/services/support_calculator.py`

**问题:** 
- `_get_200day_ma()` 回退值: `85000.0`
- `_get_fibonacci_levels()` 硬编码: `high=108000, low=60000`
- `_get_on_chain_price()` 回退值: `40000.0`
- `get_dynamic_floors()` 异常回退: `regular=55000, extreme=45000`

这些值会随时间过时。应使用 `constants.py` 中的 `DEFAULT_SPOT_FALLBACK` 并乘以合理系数。

**修复:**
```python
from constants import get_spot_fallback

def _get_200day_ma(self) -> float:
    # ... API 调用 ...
    fallback = get_spot_fallback(self.currency)
    return fallback * 0.85  # 200日均线通常低于当前价

def _get_fibonacci_levels(self) -> dict:
    # ... API 调用 ...
    spot = get_spot_fallback(self.currency)
    high, low = spot * 1.15, spot * 0.65  # 基于当前价估算范围

def _get_on_chain_price(self) -> float:
    # ... API 调用 ...
    return get_spot_fallback(self.currency) * 0.5  # Realized Price 通常远低于现价

def get_dynamic_floors(self) -> dict:
    # ... 
    fallback = get_spot_fallback(self.currency)
    return {
        "regular": fallback * 0.75,
        "extreme": fallback * 0.55,
        ...
    }
```

---

### Bug 3.5: grid_engine.py 中 get_vol_direction_signal 的 DVOL 计算逻辑有误

**严重程度:** MEDIUM — DVOL 分位数计算不准确
**文件:** `dashboard/services/grid_engine.py` get_vol_direction_signal 函数

**问题:** `dvol_percentile` 的计算方式:
```python
ratio = dvol_current / dvol_30d_avg
if ratio > 1.0:
    dvol_percentile = min(100, 50 + (ratio - 1.0) * 100)
else:
    dvol_percentile = max(0, 50 - (1.0 - ratio) * 100)
```
这不是真正的分位数计算。如果 dvol_current = 60, dvol_30d_avg = 50，ratio = 1.2，则 dvol_percentile = 70。但真正的含义应该是"当前 DVOL 在过去 30 天中处于第 70 百分位"。

**修复建议:** 使用历史 DVOL 数据计算真实分位数:
```python
def get_vol_direction_signal(contracts, currency="BTC"):
    # ... 现有代码 ...
    
    # 尝试从数据库获取历史 DVOL 数据计算真实分位数
    try:
        from db.connection import execute_read
        rows = execute_read("""
            SELECT current FROM dvol_history 
            WHERE currency = ? AND timestamp >= datetime('now', '-30 days')
            ORDER BY timestamp DESC
        """, (currency,))
        if rows and len(rows) >= 10:
            historical = sorted([r[0] for r in rows if r[0] and r[0] > 0])
            if historical:
                below = sum(1 for v in historical if v <= dvol_current)
                dvol_percentile = round(below / len(historical) * 100, 1)
    except (RuntimeError, ValueError, TypeError) as e:
        logger.debug("DVOL percentile from DB failed, using ratio method: %s", e)
        # 保留现有 ratio 方法作为 fallback
```

---

### Bug 3.6: grid_engine.py — calculate_heatmap_data 的 risk_score 公式方向反了

**严重程度:** MEDIUM — 风险热力图 Put 端显示错误
**文件:** `dashboard/services/grid_engine.py` calculate_heatmap_data 函数

**问题:** Put 的 `distance_pct` 为负数（strike < spot），所以 `risk_score = 50 + distance_pct * 2` 会得到一个偏低的值。但 Put 越接近 spot 风险越高，公式应该反映这一点。

**修复:**
```python
for level in put_levels:
    distance_pct = (level.strike - spot_price) / spot_price * 100  # 负数
    # 距离越近（绝对值越小）风险越高
    risk_score = max(0, min(100, 100 - abs(distance_pct) * 5))
    
for level in call_levels:
    distance_pct = (level.strike - spot_price) / spot_price * 100  # 正数
    # 距离越近（绝对值越小）风险越高
    risk_score = max(0, min(100, 100 - abs(distance_pct) * 5))
```

---

## Phase 4: 数据层加固 (P2)

### Bug 4.1: scan_engine.py save_scan_record 已使用 execute_transaction ✅

**状态:** 已修复 — 使用 `execute_transaction(stmts)` 保证原子性和 _write_lock

---

### Bug 4.2: spot_price.py 缓存已加锁 ✅

**状态:** 已修复 — `_cache_lock = threading.Lock()` 并在所有读写处使用

---

### Bug 4.3: risk_framework.py 缓存已加锁 ✅

**状态:** 已修复 — `_cache_lock = threading.Lock()` 在 `_get_floors` 中使用

---

### Bug 4.4: support_calculator.py — verify=False 安全风险

**严重程度:** MEDIUM — 禁用 SSL 验证，中间人攻击风险
**文件:** `dashboard/services/support_calculator.py` 第 53, 70, 112, 126 行
**同样存在:** `dashboard/services/onchain_metrics.py` 第 168, 192, 217, 237, 387, 410 行

**问题:** 所有 `request_with_retry` 调用都使用 `verify=False`，禁用了 SSL 证书验证。

**修复:** 移除 `verify=False`，或改为 `verify=True`:
```python
# 修改前
resp = request_with_retry(url, params=..., timeout=10, verify=False, max_retries=3)

# 修改后
resp = request_with_retry(url, params=..., timeout=10, verify=True, max_retries=3)
```

如果某些 API 的 SSL 证书有问题，可以设置环境变量:
```bash
export SSL_CERT_FILE=/path/to/cert.pem
```

---

### Bug 4.5: db/schema.py — _is_duplicate_column_error 比之前改进 ✅

**状态:** 已修复 — 使用 `_is_duplicate_column_error(e)` 精确判断，不再吞掉所有 OperationalError

---

## Phase 5: 代码清理 (P2)

### Bug 5.1: options_aggregator.py 仍存在且被 scan_engine.py 引用

**严重程度:** MEDIUM — AUDIT_VERIFICATION.md 称已删除但实际未删
**文件:** `dashboard/services/scan_engine.py` 第 142 行

**问题:** `from options_aggregator import format_report` — 但 run_options_scan 是旧版扫描入口，quick_scan 不使用它。

**修复:** 确认 `run_options_scan` 是否仍被调用:
```bash
grep -rn 'run_options_scan' --include='*.py' dashboard/
```
如果只被 `api/scan.py` 的 `/api/scan` 端点调用，考虑:
1. 将 `format_report` 的逻辑内联到 scan_engine.py
2. 删除 options_aggregator.py 的依赖

---

### Bug 5.2: main.py 未使用的 import

**严重程度:** LOW
**文件:** `dashboard/main.py`

**问题:** 导入了 `json`, `subprocess`, `math`, `re` 但未使用。

**修复:** 移除未使用的 import:
```python
# 移除:
import json      # 仅在 _get_cached_contracts_count 中使用，可改为局部导入
import subprocess  # 未使用
import math       # 未使用
import re         # 未使用
```

---

### Bug 5.3: SCAN_INTERVAL_SECONDS 定义重复

**严重程度:** LOW
**文件:** `dashboard/main.py` 第 ~108 行 和 `dashboard/config.py` 第 48 行

**修复:** main.py 中改为:
```python
from config import config
SCAN_INTERVAL_SECONDS = config.SCAN_INTERVAL_SECONDS
```

---

### Bug 5.4: AUDIT_VERIFICATION.md 内容不准确

**严重程度:** MEDIUM — 误导开发者
**问题:** 
- 称 `options_aggregator.py` 已删除 — 实际仍存在
- 称 `binance_options.py` 已删除 — 实际仍存在
- 称 main.py 1227 行 — 实际 311 行 (这个是对的)
- 称 `裸 except 语句: 0` — 确实为 0 ✅

**修复:** 更新 AUDIT_VERIFICATION.md 中的不准确条目。

---

### Bug 5.5: README.md 引用不存在的文件

**严重程度:** MEDIUM — 误导用户
**文件:** `README.md`

**问题:**
- 引用 `test_e2e.py` (不存在)
- 引用 `.env.example` (不存在)
- 引用 `Dockerfile` (不存在)

**修复:**
1. 创建 `.env.example`:
```
DASHBOARD_API_KEY=
DASHBOARD_DB_PATH=
CORS_ALLOWED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
FRED_API_KEY=
```

2. 更新 README 中的测试和部署章节，标注这些功能待实现。

---

### Bug 5.6: dashboard/requirements.txt 仍含 requests

**严重程度:** LOW
**文件:** `dashboard/requirements.txt`

**问题:** `requests>=2.28.0` 仍在列表中，但项目已迁移到 httpx。

**修复:** 移除 `requests>=2.28.0`。

---

## Phase 6: 文档+测试补齐 (P3)

### 6.1: 创建基础测试

创建 `tests/test_core.py`:

```python
"""核心计算逻辑单元测试"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard"))

from services.margin_calculator import calc_margin, calc_margin_put, calc_margin_call
from services.shared_calculations import (
    black_scholes_price, calc_win_rate, calc_grid_score,
    score_to_recommendation_level, norm_cdf
)


class TestMarginCalculator:
    def test_put_margin_basic(self):
        margin = calc_margin_put(80000, 2000, 0.2)
        assert margin > 0
        assert margin == max(80000 * 0.1, (80000 - 2000) * 0.2)

    def test_call_margin_basic(self):
        margin = calc_margin_call(80000, 2000, 0.2)
        assert margin > 0
        assert margin == max(80000 * 0.1, 80000 * 0.2 - 2000)

    def test_margin_never_negative(self):
        # 高权利金不应导致负保证金
        margin = calc_margin_put(80000, 79000, 0.2)
        assert margin > 0

    def test_margin_minimum_floor(self):
        # 保证金不应低于 strike * 0.1
        margin = calc_margin_put(80000, 100, 0.01)
        assert margin >= 80000 * 0.1


class TestBlackScholes:
    def test_put_price_positive(self):
        result = black_scholes_price("P", 80000, 83000, 30, 50)
        assert result["premium"] > 0
        assert result["delta"] < 0
        assert result["theta"] != 0

    def test_call_price_positive(self):
        result = black_scholes_price("C", 86000, 83000, 30, 50)
        assert result["premium"] > 0
        assert result["delta"] > 0

    def test_invalid_inputs(self):
        result = black_scholes_price("P", 0, 83000, 30, 50)
        assert result["premium"] == 0

    def test_deep_otm_put_near_zero(self):
        result = black_scholes_price("P", 40000, 83000, 7, 50)
        assert result["premium"] < 10  # 应该很低


class TestWinRate:
    def test_sell_put_otm_high_winrate(self):
        rate = calc_win_rate("P", "sell", 75000, 1000, 83000, 50, 30)
        assert rate > 0.7  # OTM Put 胜率应较高

    def test_sell_put_itm_low_winrate(self):
        rate = calc_win_rate("P", "sell", 85000, 1000, 83000, 50, 30)
        assert rate < 0.5  # ITM Put 胜率应较低


class TestGridScore:
    def test_high_apr_high_distance(self):
        score = calc_grid_score(50, 15, 500, 100, 18)
        assert 0 <= score <= 1

    def test_score_to_recommendation(self):
        assert score_to_recommendation_level(0.8) == "BEST"
        assert score_to_recommendation_level(0.65) == "GOOD"
        assert score_to_recommendation_level(0.5) == "OK"
        assert score_to_recommendation_level(0.35) == "CAUTION"
        assert score_to_recommendation_level(0.2) == "SKIP"


class TestNormCdf:
    def test_zero(self):
        assert abs(norm_cdf(0) - 0.5) < 0.001

    def test_positive(self):
        assert norm_cdf(1.96) > 0.97

    def test_negative(self):
        assert norm_cdf(-1.96) < 0.03
```

**运行测试:**
```bash
cd /tmp/crypto-options-aggregator
pip install pytest
python -m pytest tests/test_core.py -v
```

---

### 6.2: 创建 .env.example

```
# Crypto Options Aggregator 配置示例
# 复制此文件为 .env 并填入实际值

# API 认证密钥 (留空则仅允许 localhost 访问)
DASHBOARD_API_KEY=

# 数据库路径 (留空使用默认路径)
DASHBOARD_DB_PATH=

# CORS 允许的源 (逗号分隔)
CORS_ALLOWED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000

# FRED API Key (宏观数据，可选)
FRED_API_KEY=

# 服务端口
PORT=8000
```

---

## 执行检查清单

执行完每个阶段后，运行以下验证:

```bash
# Phase 1 验证
cd /tmp/crypto-options-aggregator/dashboard
python -c "from services.grid_manager import GridManager; print('GridManager OK')"
python -c "from services.support_calculator import DynamicSupportCalculator; print('SupportCalc OK')"

# Phase 2 验证
python -c "from services.strategy_calc import calc_roll_plan; print('StrategyCalc OK')"
python -c "from config import config; print('API_KEY configured:', bool(config.API_KEY))"

# Phase 3 验证
python -c "from services.margin_calculator import calc_margin; assert calc_margin(80000, 2000, 'PUT') > 0; print('Margin OK')"

# Phase 4 验证
python -c "from db.connection import execute_read; print('DB OK')"

# Phase 5 验证
python -c "import ast; ast.parse(open('dashboard/main.py').read()); print('Syntax OK')"

# Phase 6 验证
python -m pytest tests/test_core.py -v
```

---

## 附录: 上次审查已修复的问题确认

| 问题 | 状态 |
|------|------|
| main.py 2636行 monolith | ✅ 已修复 (311行) |
| 裸 except: 语句 | ✅ 已修复 (0处) |
| CORS allow_origins=["*"] | ✅ 已修复 (白名单) |
| 无 API 认证 | ✅ 已修复 (X-API-Key) |
| _parse_inst_name 重复 | ✅ 已修复 (统一到 instrument.py) |
| STRATEGY_PRESETS 3处重复 | ✅ 已修复 (仅 config.py 1处) |
| calc_margin 负数 bug | ✅ 已修复 (统一到 margin_calculator.py) |
| spot_price 缓存无锁 | ✅ 已修复 (_cache_lock) |
| risk_framework 缓存无锁 | ✅ 已修复 (_cache_lock) |
| scan_engine DB 竞态 | ✅ 已修复 (execute_transaction) |
| grid router 返回 HTTP 200 错误 | ✅ 已修复 (HTTPException) |
| 循环导入 from main import | ✅ 已修复 (从 services 直接导入) |
| payoff API 无 Pydantic 验证 | ✅ 已修复 (所有端点有 BaseModel) |
| paper_trading API 无约束 | ✅ 已修复 (gt=0 等约束) |
| maintenance.py cleanup bug | ✅ 已修复 (cursor.rowcount) |

---

## 附录: 仍需关注但非紧急的问题

1. **无自动化测试** — Phase 6 补基础测试
2. **无 CI/CD** — 建议后续添加 GitHub Actions
3. **前端 app.js 4051行** — 建议后续拆分 ES 模块
4. **CDN 无 SRI hash** — 安全加固
5. **无 rate limiting** — 建议添加 slowapi
6. **API key 比较非 constant-time** — 低风险但建议用 hmac.compare_digest
7. **50处 broad except Exception** — 已从 79 处降下来，继续收窄
8. **Grid 可视化缺阶梯图** — 丸总核心需求，建议优先实现
