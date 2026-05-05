# 加密原生 AI 分析框架重设计 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将统一推荐引擎的规则判断和 LLM 分析从传统金融思维切换到加密原生思维，消除期货/现货比假阳性等系统性误判。

**Architecture:** 新增 crypto_thresholds（混合阈值）、perp_basis_analyzer（永续基差）、crypto_market_context（市场上下文）三个服务层，重写 derivative_metrics（8指标替代4指标），注入加密市场结构性知识到 ai_router 和 LLM prompt 模板。

**Tech Stack:** Python 3.13 · SQLite WAL · Binance REST API · DeepSeek v4 API · httpx

---

### Task 1: Config 新增加密原生指标配置 + 修复 LLM_REASONING_EFFORT

**Files:**
- Modify: `dashboard/config.py:148-156`

- [ ] **Step 1: 添加加密原生指标配置项**

在 `config.py` 的 `_load_all` 方法中，`# === LLM 分析配置 ===` 块之后添加以下代码：

```python
        # === 加密原生指标配置 ===
        self.PERP_BASIS_THRESHOLD_HIGH = _get_env("PERP_BASIS_THRESHOLD_HIGH", 15.0, env)
        self.PERP_BASIS_THRESHOLD_EXTREME = _get_env("PERP_BASIS_THRESHOLD_EXTREME", 30.0, env)
        self.PERP_BASIS_PERCENTILE_WINDOW = _get_env("PERP_BASIS_PERCENTILE_WINDOW", 30, env)

        self.FUTURES_SPOT_RATIO_HIGH = _get_env("FUTURES_SPOT_RATIO_HIGH", 8.0, env)
        self.FUTURES_SPOT_RATIO_EXTREME = _get_env("FUTURES_SPOT_RATIO_EXTREME", 15.0, env)
        self.FUTURES_SPOT_RATIO_STILL_OK = _get_env("FUTURES_SPOT_RATIO_STILL_OK", 3.0, env)

        self.LIQUIDATION_HEAT_L2_THRESHOLD = _get_env("LIQUIDATION_HEAT_L2_THRESHOLD", 5_000_000, env)
        self.LIQUIDATION_HEAT_L3_THRESHOLD = _get_env("LIQUIDATION_HEAT_L3_THRESHOLD", 20_000_000, env)

        self.STABLECOIN_INFLOW_HIGH = _get_env("STABLECOIN_INFLOW_HIGH", 5.0, env)
        self.STABLECOIN_OUTFLOW_HIGH = _get_env("STABLECOIN_OUTFLOW_HIGH", -5.0, env)

        self.OI_DIVERGENCE_STD_THRESHOLD = _get_env("OI_DIVERGENCE_STD_THRESHOLD", 1.5, env)
        self.FUNDING_VOLATILITY_THRESHOLD = _get_env("FUNDING_VOLATILITY_THRESHOLD", 0.1, env)

        self.MARKET_CONTEXT_CACHE_TTL = _get_env("MARKET_CONTEXT_CACHE_TTL", 300, env)
```

- [ ] **Step 2: 修复 LLM_REASONING_EFFORT 默认值**

将原有的 `self.LLM_REASONING_EFFORT = _get_env("LLM_REASONING_EFFORT", "max", env)` 修改为 `"high"`。

找到 `self.LLM_REASONING_EFFORT` 所在行（约第 155 行），修改为：

```python
        self.LLM_REASONING_EFFORT = _get_env("LLM_REASONING_EFFORT", "high", env)
```

- [ ] **Step 3: 验证配置加载正常**

Run: `python -c "from config import config; print('PERP_BASIS_THRESHOLD_HIGH:', config.PERP_BASIS_THRESHOLD_HIGH); print('LLM_REASONING_EFFORT:', config.LLM_REASONING_EFFORT)"`

Expected: `PERP_BASIS_THRESHOLD_HIGH: 15.0` and `LLM_REASONING_EFFORT: high`

- [ ] **Step 4: Commit**

```bash
git add dashboard/config.py
git commit -m "feat(config): add crypto-native metric thresholds + fix reasoning_effort default"
```

---

### Task 2: Database Schema 新增 3 张数据表

**Files:**
- Modify: `dashboard/db/schema.py:168-205`
- Modify: `dashboard/db/maintenance.py:35-48`

- [ ] **Step 1: 在 schema.py 末尾添加新表定义**

在 `SCHEMA_LLM_USAGE_LOG` 定义之后添加：

```python
SCHEMA_PERP_BASIS_HISTORY = """
CREATE TABLE IF NOT EXISTS perp_basis_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    currency TEXT NOT NULL DEFAULT 'BTC',
    perp_price REAL NOT NULL,
    spot_price REAL NOT NULL,
    basis_annualized REAL NOT NULL,
    funding_rate REAL
)
"""

SCHEMA_OI_HISTORY = """
CREATE TABLE IF NOT EXISTS oi_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    currency TEXT NOT NULL DEFAULT 'BTC',
    open_interest_usd REAL NOT NULL,
    price REAL NOT NULL,
    oi_change_24h_pct REAL,
    price_change_24h_pct REAL
)
"""

SCHEMA_STABLECOIN_RESERVE_HISTORY = """
CREATE TABLE IF NOT EXISTS stablecoin_reserve_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    exchange TEXT NOT NULL DEFAULT 'binance',
    asset TEXT NOT NULL DEFAULT 'USDT',
    balance REAL NOT NULL,
    change_7d_pct REAL
)
"""
```

- [ ] **Step 2: 在 `init_database_schema` 中执行新表建表**

在 `cursor.execute(SCHEMA_LLM_USAGE_LOG)` 之后添加：

```python
    cursor.execute(SCHEMA_PERP_BASIS_HISTORY)
    cursor.execute(SCHEMA_OI_HISTORY)
    cursor.execute(SCHEMA_STABLECOIN_RESERVE_HISTORY)
```

- [ ] **Step 3: 添加新表索引**

在 `INDEXES` 列表末尾添加：

```python
    "CREATE INDEX IF NOT EXISTS idx_perp_basis_currency_timestamp ON perp_basis_history(currency, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_oi_currency_timestamp ON oi_history(currency, timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_stablecoin_reserve_timestamp ON stablecoin_reserve_history(timestamp DESC)",
```

- [ ] **Step 4: 在 maintenance.py 添加新表清理**

在 `cleanup_old_records` 函数中，`cursor.execute("DELETE FROM large_trades_history...")` 之后添加：

```python
    cursor.execute("DELETE FROM perp_basis_history WHERE timestamp < ?", (cutoff_date,))
    basis_deleted = cursor.rowcount

    cursor.execute("DELETE FROM oi_history WHERE timestamp < ?", (cutoff_date,))
    oi_deleted = cursor.rowcount

    cursor.execute("DELETE FROM stablecoin_reserve_history WHERE timestamp < ?", (cutoff_date,))
    stablecoin_deleted = cursor.rowcount
```

并更新返回字典：

```python
    return {
        "scans_deleted": scans_deleted,
        "trades_deleted": trades_deleted,
        "basis_deleted": basis_deleted,
        "oi_deleted": oi_deleted,
        "stablecoin_deleted": stablecoin_deleted,
        "cutoff_date": cutoff_date.isoformat()
    }
```

- [ ] **Step 5: 验证表创建成功**

Run: `python -c "from db.schema import init_database_schema; from db.connection import get_db_connection; conn = get_db_connection(read_only=False); init_database_schema(conn); cur = conn.cursor(); cur.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%perp%' OR name LIKE '%oi_%' OR name LIKE '%stablecoin%'\"); print([r[0] for r in cur.fetchall()])"`

Expected: `['perp_basis_history', 'oi_history', 'stablecoin_reserve_history']`

- [ ] **Step 6: Commit**

```bash
git add dashboard/db/schema.py dashboard/db/maintenance.py
git commit -m "feat(db): add perp_basis, oi, stablecoin_reserve history tables"
```

---

### Task 3: `crypto_thresholds.py` 混合阈值注册表

**Files:**
- Create: `dashboard/services/crypto_thresholds.py`

- [ ] **Step 1: 创建文件并实现阈值注册表类**

```python
"""
加密原生阈值注册表 — 混合阈值系统
- 核心指标：从 DB 历史数据计算滚动百分位
- 辅助指标：使用加密校准的固定阈值
"""
import logging
from typing import Dict, Any, Optional, Tuple

from db.connection import execute_read
from config import config

logger = logging.getLogger(__name__)


class CryptoThresholds:
    """加密原生阈值管理器"""

    @classmethod
    def get_percentile_threshold(cls, metric_name: str, current_value: float,
                                 currency: str = "BTC", window_days: int = 30) -> Dict[str, Any]:
        """获取百分位阈值（从 DB 历史数据计算）"""
        table_map = {
            "perp_basis": ("perp_basis_history", "basis_annualized"),
            "futures_spot_ratio": ("perp_basis_history", "basis_annualized"),
        }

        if metric_name not in table_map:
            return {"pct": 50, "signal": "unknown", "status": "no_threshold"}

        table, column = table_map[metric_name]
        try:
            rows = execute_read(
                f"SELECT {column} FROM {table} WHERE currency=? "
                "AND timestamp >= datetime('now', ? || ' days') "
                f"ORDER BY {column} ASC",
                (currency, f"-{window_days}")
            )
            if not rows or len(rows) < 10:
                return {"pct": 50, "signal": "neutral", "status": "insufficient_data",
                        "window_days": window_days, "data_points": len(rows) if rows else 0}

            values = [r[column] for r in rows]
            n = len(values)
            rank = sum(1 for v in values if v < current_value)
            pct = round(rank / n * 100, 1)

            if pct >= 90:
                signal = "extreme_high"
            elif pct >= 75:
                signal = "high"
            elif pct >= 25:
                signal = "normal"
            elif pct >= 10:
                signal = "low"
            else:
                signal = "extreme_low"

            return {"pct": pct, "signal": signal, "status": "ok",
                    "window_days": window_days, "data_points": n,
                    "min": round(values[0], 4), "max": round(values[-1], 4),
                    "median": round(values[n // 2], 4)}
        except Exception as e:
            logger.warning("Percentile calc for %s failed: %s", metric_name, e)
            return {"pct": 50, "signal": "neutral", "status": f"error: {e}"}

    @classmethod
    def get_fixed_threshold(cls, metric_name: str, value: float) -> Dict[str, Any]:
        """获取固定阈值判定（加密校准）"""
        thresholds = {
            "perp_basis": [
                (30.0, "extreme_high", "极端投机区间"),
                (15.0, "high", "杠杆偏高"),
                (8.0, "normal_high", "温和看多"),
                (0.0, "normal", "正常Contango"),
                (-999.0, "negative", "现货溢价（看空信号）"),
            ],
            "futures_spot_ratio": [
                (25.0, "extreme_high", "极端（低流动性山寨币级别）"),
                (15.0, "very_high", "高杠杆（关注但不等于过热）"),
                (8.0, "high", "杠杆偏高"),
                (3.0, "normal", "正常加密市场（永续结构）"),
                (0.0, "low", "现货主导（熊市/横盘正常）"),
            ],
            "funding_rate_pct": [
                (0.2, "extreme_long", "极度多头过热"),
                (0.1, "long_overheat", "多头过热"),
                (0.05, "long_bias", "多头偏多"),
                (0.01, "slight_long", "轻微多头（正常）"),
                (-0.05, "neutral", "中性"),
                (-0.1, "short_bias", "空头偏多"),
                (-999.0, "extreme_short", "极度空头（可能底部）"),
            ],
            "funding_volatility": [
                (0.1, "extreme", "极端波动（潜在拐点）"),
                (0.05, "high", "情绪反复"),
                (0.01, "normal", "正常波动"),
                (0.0, "stable", "稳定（市场共识强）"),
            ],
            "liquidation_heat": [
                (20_000_000, "L3", "高压（可能触发连锁清算）"),
                (5_000_000, "L2", "中度压力"),
                (1_000_000, "L1", "轻度压力"),
                (0, "L0", "正常"),
            ],
            "stablecoin_flow": [
                (5.0, "strong_inflow", "强买盘预期"),
                (2.0, "mild_inflow", "温和看多"),
                (-2.0, "neutral", "中性"),
                (-5.0, "outflow", "资金撤退"),
                (-999.0, "strong_outflow", "防御信号"),
            ],
            "oi_price_divergence": [
                (999.0, "oi_up_price_down", "OI↑价格↓（空头加仓=看空）"),
                (0.0, "oi_up_price_up", "OI↑价格↑（多头加仓=看多）"),
            ],
        }

        if metric_name not in thresholds:
            return {"signal": "unknown", "label": ""}

        for threshold, signal, label in thresholds[metric_name]:
            if value >= threshold:
                return {"signal": signal, "label": label, "value": value, "threshold": threshold}

        return {"signal": "unknown", "label": ""}

    @classmethod
    def hybrid_assess(cls, metric_name: str, current_value: float,
                      currency: str = "BTC") -> Dict[str, Any]:
        """混合评估：核心指标用百分位 + 固定阈值双重判定"""
        percentile = cls.get_percentile_threshold(metric_name, current_value, currency)
        fixed = cls.get_fixed_threshold(metric_name, current_value)

        pct_signal = percentile.get("signal", "neutral")
        fix_signal = fixed.get("signal", "normal")

        # 百分位极端 + 固定值确认 = 高置信度告警
        if pct_signal in ("extreme_high", "extreme_low") and fix_signal not in ("normal", "slight_long"):
            confidence = "high"
        elif pct_signal in ("high", "low") and fix_signal not in ("normal",):
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "metric": metric_name,
            "value": current_value,
            "percentile": percentile,
            "fixed_threshold": fixed,
            "confidence": confidence,
            "verdict": fixed.get("label", ""),
        }
```

- [ ] **Step 2: 验证模块导入**

Run: `python -c "from services.crypto_thresholds import CryptoThresholds; print(CryptoThresholds.get_fixed_threshold('perp_basis', 12.5))"`

Expected: `{'signal': 'high', 'label': '杠杆偏高', 'value': 12.5, 'threshold': 15.0}`

- [ ] **Step 3: Commit**

```bash
git add dashboard/services/crypto_thresholds.py
git commit -m "feat: add crypto-calibrated hybrid threshold registry"
```

---

### Task 4: `perp_basis_analyzer.py` 永续基差分析器

**Files:**
- Create: `dashboard/services/perp_basis_analyzer.py`

- [ ] **Step 1: 创建文件并实现基差分析器**

```python
"""
永续合约基差分析器
- 年化基差计算
- 基差历史记录（用于百分位计算）
- Contango/Backwardation 判断
"""
import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

from services.api_retry import request_with_retry
from services.crypto_thresholds import CryptoThresholds
from db.connection import execute_write
from config import config

logger = logging.getLogger(__name__)


class PerpBasisAnalyzer:
    """永续合约基差分析器"""

    BINANCE_PERP_TICKER = "https://fapi.binance.com/fapi/v1/ticker/price"
    BINANCE_SPOT_TICKER = "https://api.binance.com/api/v3/ticker/price"
    FUNDING_RATE_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"

    @classmethod
    def fetch_current(cls, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """获取当前永续/现货价格和基差"""
        try:
            perp_resp = request_with_retry(
                cls.BINANCE_PERP_TICKER,
                params={"symbol": symbol},
                timeout=10, verify=False, max_retries=2
            )
            perp_price = float(perp_resp.json().get("price", 0))

            spot_resp = request_with_retry(
                cls.BINANCE_SPOT_TICKER,
                params={"symbol": symbol},
                timeout=10, verify=False, max_retries=2
            )
            spot_price = float(spot_resp.json().get("price", 0))

            if perp_price <= 0 or spot_price <= 0:
                return {"error": "Invalid prices", "perp_price": perp_price, "spot_price": spot_price}

            # 年化基差 = (perp/spot - 1) * (365*24/8) * 100
            basis_annualized = round((perp_price / spot_price - 1) * (365 * 24 / 8) * 100, 2)

            # 获取资金费率
            funding_rate = 0.0
            try:
                fr_resp = request_with_retry(
                    cls.FUNDING_RATE_URL,
                    params={"symbol": symbol},
                    timeout=10, verify=False, max_retries=2
                )
                funding_rate = float(fr_resp.json().get("lastFundingRate", 0))
            except Exception as e:
                logger.warning("Funding rate fetch failed: %s", e)

            return {
                "perp_price": perp_price,
                "spot_price": spot_price,
                "basis_annualized": basis_annualized,
                "funding_rate": funding_rate,
                "funding_rate_pct": round(funding_rate * 100, 4),
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.warning("Perp basis fetch failed: %s", e)
            return {"error": str(e), "perp_price": 0, "spot_price": 0, "basis_annualized": 0, "funding_rate": 0}

    @classmethod
    def save_to_history(cls, currency: str, data: Dict[str, Any]) -> bool:
        """保存基差快照到历史表"""
        if data.get("error"):
            return False
        try:
            execute_write(
                """INSERT INTO perp_basis_history (timestamp, currency, perp_price, spot_price, basis_annualized, funding_rate)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (datetime.now().isoformat(), currency,
                 data["perp_price"], data["spot_price"],
                 data["basis_annualized"], data["funding_rate"])
            )
            return True
        except Exception as e:
            logger.warning("Save perp_basis_history failed: %s", e)
            return False

    @classmethod
    def analyze(cls, currency: str = "BTC",
                symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """完整基差分析"""
        data = cls.fetch_current(symbol)
        cls.save_to_history(currency, data)

        if data.get("error"):
            return {"error": data["error"]}

        basis = data["basis_annualized"]
        hybrid = CryptoThresholds.hybrid_assess("perp_basis", basis, currency)

        regime = "contango"
        if basis < -2:
            regime = "backwardation"
        elif basis < 0:
            regime = "mild_backwardation"
        elif basis < 8:
            regime = "mild_contango"
        elif basis < 15:
            regime = "contango"
        else:
            regime = "steep_contango"

        return {
            **data,
            "currency": currency,
            "hybrid_assessment": hybrid,
            "regime": regime,
            "percentile": hybrid.get("percentile", {}).get("pct", 50),
        }
```

- [ ] **Step 2: 验证分析器工作**

Run: `python -c "from services.perp_basis_analyzer import PerpBasisAnalyzer; r = PerpBasisAnalyzer.fetch_current(); print('basis:', r.get('basis_annualized'), 'perp:', r.get('perp_price'), 'spot:', r.get('spot_price'))"`

Expected: 返回实际 BTC 永续/现货价格和年化基差。

- [ ] **Step 3: Commit**

```bash
git add dashboard/services/perp_basis_analyzer.py
git commit -m "feat: add perpetual basis analyzer with hybrid thresholds"
```

---

### Task 5: 重写 `derivative_metrics.py` — 8 个加密原生指标

**Files:**
- Rewrite: `dashboard/services/derivative_metrics.py`

- [ ] **Step 1: 重写文件（完整替换）**

```python
"""
加密原生衍生品指标服务 v2.0
基于 Binance Futures/Spot API 的衍生品市场分析系统

核心指标（8个）:
1. 永续基差 (Perp Basis): 年化资金成本，替代粗糙的期货/现货成交量比
2. OI-价格背离 (OI-Price Divergence): 量价关系，方向性先行指标
3. 资金费率波动率 (Funding Rate Volatility): 情绪稳定性
4. 清算热力等级 (Liquidation Heat): 加密特有"痛苦指数"
5. 稳定币交易所储备 (Stablecoin Exchange Reserve): 买盘火力
6. 期货/现货成交量比 (Futures/Spot Ratio): 保留但重校准
7. OI 结构 (Call/Put OI Ratio): 期权市场偏向
8. 期货期限结构 (Futures Term Structure): 期限溢价

阈值策略：混合 — 基差/比值用滚动百分位 + 加密校准固定值
"""
import math
import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

from services.api_retry import request_with_retry
from services.crypto_thresholds import CryptoThresholds
from services.perp_basis_analyzer import PerpBasisAnalyzer
from db.connection import execute_read, execute_write
from config import config

logger = logging.getLogger(__name__)


class DerivativeMetrics:
    """加密原生衍生品市场指标服务"""

    # ============================================================
    # 指标 1: 永续基差
    # ============================================================

    @classmethod
    def _get_perp_basis(cls, currency: str = "BTC") -> Dict[str, Any]:
        """永续基差分析"""
        try:
            basis_data = PerpBasisAnalyzer.analyze(currency)
            return basis_data
        except Exception as e:
            logger.warning("Perp basis failed: %s", e)
            return {"error": str(e), "basis_annualized": 0, "perp_price": 0, "spot_price": 0}

    # ============================================================
    # 指标 2: OI-价格背离
    # ============================================================

    @classmethod
    def _get_oi_price_divergence(cls, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """OI-价格背离检测"""
        try:
            oi_resp = request_with_retry(
                "https://fapi.binance.com/fapi/v1/openInterest",
                params={"symbol": symbol},
                timeout=10, verify=False, max_retries=2
            )
            current_oi = float(oi_resp.json().get("openInterest", 0))

            price_resp = request_with_retry(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": symbol},
                timeout=10, verify=False, max_retries=2
            )
            current_price = float(price_resp.json().get("price", 0))

            # 从历史表获取 24h 前的数据
            rows = execute_read(
                "SELECT open_interest_usd, price FROM oi_history "
                "WHERE currency='BTC' ORDER BY timestamp DESC LIMIT 1"
            )

            oi_24h_ago = current_oi
            price_24h_ago = current_price
            if rows:
                oi_24h_ago = rows[0]["open_interest_usd"] or current_oi
                price_24h_ago = rows[0]["price"] or current_price

            oi_change_pct = ((current_oi - oi_24h_ago) / oi_24h_ago * 100) if oi_24h_ago > 0 else 0
            price_change_pct = ((current_price - price_24h_ago) / price_24h_ago * 100) if price_24h_ago > 0 else 0

            # 保存当前快照
            execute_write(
                "INSERT INTO oi_history (timestamp, currency, open_interest_usd, price, oi_change_24h_pct, price_change_24h_pct) "
                "VALUES (?, 'BTC', ?, ?, ?, ?)",
                (datetime.now().isoformat(), current_oi, current_price,
                 round(oi_change_pct, 2), round(price_change_pct, 2))
            )

            # 背离判断
            oi_direction = "flat"
            price_direction = "flat"
            if oi_change_pct > 1.5:
                oi_direction = "up"
            elif oi_change_pct < -1.5:
                oi_direction = "down"
            if price_change_pct > 0.5:
                price_direction = "up"
            elif price_change_pct < -0.5:
                price_direction = "down"

            divergence = "none"
            if oi_direction == "up" and price_direction == "down":
                divergence = "bearish"  # 空头加仓
            elif oi_direction == "up" and price_direction == "flat":
                divergence = "breakout_looming"  # 即将突破
            elif oi_direction == "down" and price_direction == "up":
                divergence = "short_squeeze"  # 逼空
            elif oi_direction == "down" and price_direction == "down":
                divergence = "long_capitulation"  # 多杀多
            elif oi_direction == "up" and price_direction == "up":
                divergence = "bullish"  # 多头加仓

            return {
                "current_oi": current_oi,
                "current_price": current_price,
                "oi_change_24h_pct": round(oi_change_pct, 2),
                "price_change_24h_pct": round(price_change_pct, 2),
                "oi_direction": oi_direction,
                "price_direction": price_direction,
                "divergence": divergence,
                "divergence_label": {
                    "bearish": "OI↑价格↓（空头加仓=看空）",
                    "short_squeeze": "OI↓价格↑（空头平仓=逼空风险）",
                    "bullish": "OI↑价格↑（多头加仓=看多）",
                    "long_capitulation": "OI↓价格↓（多头平仓=多杀多）",
                    "breakout_looming": "OI↑价格→（分歧加大=即将突破）",
                    "none": "无背离",
                }.get(divergence, "无背离"),
            }
        except Exception as e:
            logger.warning("OI-price divergence fetch failed: %s", e)
            return {"error": str(e), "divergence": "unknown"}

    # ============================================================
    # 指标 3: 资金费率波动率
    # ============================================================

    @classmethod
    def _get_funding_volatility(cls, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """资金费率波动率（从 perp_basis_history 计算 7 日 std）"""
        try:
            rows = execute_read(
                "SELECT funding_rate FROM perp_basis_history WHERE currency='BTC' "
                "ORDER BY timestamp DESC LIMIT 21"  # 21 个 8h 周期 ≈ 7 天
            )
            if not rows or len(rows) < 5:
                # 回退到实时获取
                resp = request_with_retry(
                    "https://fapi.binance.com/fapi/v1/premiumIndex",
                    params={"symbol": symbol},
                    timeout=10, verify=False, max_retries=2
                )
                current_rate = float(resp.json().get("lastFundingRate", 0))
                return {
                    "current_funding_rate_pct": round(current_rate * 100, 4),
                    "volatility_7d_pct": 0.0,
                    "signal": "insufficient_data",
                    "label": "数据不足（需>5个数据点）",
                }

            rates = [r["funding_rate"] for r in rows if r["funding_rate"] is not None]
            if len(rates) < 5:
                return {"error": "insufficient_data"}

            mean_rate = sum(rates) / len(rates)
            variance = sum((r - mean_rate) ** 2 for r in rates) / (len(rates) - 1)
            std_rate = math.sqrt(variance)
            volatility_pct = round(std_rate * 100, 4)

            fixed = CryptoThresholds.get_fixed_threshold("funding_volatility", volatility_pct)

            return {
                "current_funding_rate_pct": round(rates[0] * 100, 4),
                "volatility_7d_pct": volatility_pct,
                "mean_funding_rate_7d_pct": round(mean_rate * 100, 4),
                "data_points": len(rates),
                "signal": fixed.get("signal", "normal"),
                "label": fixed.get("label", ""),
            }
        except Exception as e:
            logger.warning("Funding volatility calc failed: %s", e)
            return {"error": str(e)}

    # ============================================================
    # 指标 4: 清算热力等级
    # ============================================================

    @classmethod
    def _get_liquidation_heat(cls, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """清算热力等级（最近1小时总清算额）"""
        try:
            liq_types = []
            total_long_usd = 0.0
            total_short_usd = 0.0

            for pos_type in ("LONG", "SHORT"):
                try:
                    resp = request_with_retry(
                        "https://fapi.binance.com/fapi/v1/forceOrders",
                        params={"symbol": symbol, "autoCloseType": "LIQUIDATION",
                                "startTime": int((datetime.now().timestamp() - 3600) * 1000),
                                "limit": 100},
                        timeout=10, verify=False, max_retries=1
                    )
                    orders = resp.json()
                    for order in orders:
                        vol = float(order.get("executedQty", 0))
                        if pos_type == "LONG":
                            total_long_usd += vol
                        else:
                            total_short_usd += vol
                except Exception:
                    pass

            total_usd = total_long_usd + total_short_usd
            fixed = CryptoThresholds.get_fixed_threshold("liquidation_heat", total_usd)
            direction_bias = ((total_long_usd - total_short_usd) / total_usd) if total_usd > 0 else 0

            return {
                "total_liquidation_1h_usd": round(total_usd),
                "long_liquidation_usd": round(total_long_usd),
                "short_liquidation_usd": round(total_short_usd),
                "direction_bias": round(direction_bias, 2),
                "heat_level": fixed.get("signal", "L0"),
                "label": fixed.get("label", "正常"),
            }
        except Exception as e:
            logger.warning("Liquidation heat fetch failed: %s", e)
            return {"error": str(e), "heat_level": "L0"}

    # ============================================================
    # 指标 5: 稳定币交易所储备
    # ============================================================

    @classmethod
    def _get_stablecoin_reserve(cls) -> Dict[str, Any]:
        """稳定币交易所余额变化（尝试 CryptoQuant 免费 API，失败返回固定值）"""
        try:
            # 尝试从 CoinGecko 获取 USDT 市值 + 交易所占比估算
            resp = request_with_retry(
                "https://api.coingecko.com/api/v3/coins/tether",
                params={"localization": "false", "tickers": "false", "community_data": "false",
                        "developer_data": "false"},
                timeout=15, verify=False, max_retries=1
            )
            if resp.status_code == 200:
                data = resp.json()
                market_data = data.get("market_data", {})
                market_cap = market_data.get("market_cap", {}).get("usd", 0)

                # 估算交易所余额（通常占总市值的 15-20%）
                if market_cap > 0:
                    estimated_reserve = market_cap * 0.175
                    rows = execute_read(
                        "SELECT balance FROM stablecoin_reserve_history ORDER BY timestamp DESC LIMIT 1"
                    )
                    prev_balance = estimated_reserve
                    if rows:
                        prev_balance = rows[0]["balance"] or estimated_reserve
                    change_7d = ((estimated_reserve - prev_balance) / prev_balance * 100) if prev_balance > 0 else 0

                    execute_write(
                        "INSERT INTO stablecoin_reserve_history (timestamp, exchange, asset, balance, change_7d_pct) "
                        "VALUES (?, 'global', 'USDT', ?, ?)",
                        (datetime.now().isoformat(), estimated_reserve, round(change_7d, 2))
                    )

                    fixed = CryptoThresholds.get_fixed_threshold("stablecoin_flow", change_7d)
                    return {
                        "estimated_reserve_usdt": round(estimated_reserve),
                        "change_7d_pct": round(change_7d, 2),
                        "signal": fixed.get("signal", "neutral"),
                        "label": fixed.get("label", "中性"),
                        "source": "coingecko_estimated",
                    }
        except Exception as e:
            logger.debug("Stablecoin reserve via CoinGecko failed: %s", e)

        return {"estimated_reserve_usdt": 0, "change_7d_pct": 0,
                "signal": "unknown", "label": "数据不可用", "source": "none"}

    # ============================================================
    # 指标 6: 期货/现货成交量比（重校准）
    # ============================================================

    @classmethod
    def _get_futures_spot_volume_ratio(cls, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """期货/现货成交量比（加密校准阈值）"""
        try:
            spot_resp = request_with_retry(
                "https://api.binance.com/api/v3/ticker/24hr",
                params={"symbol": symbol},
                timeout=10, verify=False, max_retries=2
            )
            spot_data = spot_resp.json()
            spot_volume = float(spot_data.get("volume", 0))

            futures_resp = request_with_retry(
                "https://fapi.binance.com/fapi/v1/ticker/24hr",
                params={"symbol": symbol},
                timeout=10, verify=False, max_retries=2
            )
            futures_data = futures_resp.json()
            futures_volume = float(futures_data.get("volume", 0))

            if spot_volume <= 0:
                return {"error": "spot_volume_zero", "ratio": 0}

            ratio = round(futures_volume / spot_volume, 2)
            fixed = CryptoThresholds.get_fixed_threshold("futures_spot_ratio", ratio)

            return {
                "futures_volume": futures_volume,
                "spot_volume": spot_volume,
                "ratio": ratio,
                "signal": fixed.get("signal", "normal"),
                "label": fixed.get("label", "正常加密市场"),
            }
        except Exception as e:
            logger.warning("Futures/spot ratio fetch failed: %s", e)
            return {"error": str(e), "ratio": 0}

    # ============================================================
    # 指标 7-8: 保留原有逻辑
    # ============================================================

    @classmethod
    def _get_sharpe_ratio(cls) -> Tuple[Optional[float], Optional[float]]:
        """Sharpe Ratio（保留原逻辑）"""
        try:
            resp = request_with_retry(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "1d", "limit": 90},
                timeout=10, verify=False, max_retries=2
            )
            klines = resp.json()
            if len(klines) < 30:
                return None, None

            closes = [float(k[4]) for k in klines]
            returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]

            returns_14d = returns[-14:]
            sharpe_14d = cls._calc_single_sharpe(returns_14d)

            returns_30d = returns[-30:]
            sharpe_30d = cls._calc_single_sharpe(returns_30d)

            return sharpe_14d, sharpe_30d
        except Exception as e:
            logger.warning("Sharpe Ratio calc failed: %s", e)
            return None, None

    @classmethod
    def _calc_single_sharpe(cls, returns) -> Optional[float]:
        if not returns or len(returns) < 2:
            return None
        avg_return = sum(returns) / len(returns)
        variance = sum((r - avg_return) ** 2 for r in returns) / (len(returns) - 1)
        std_dev = math.sqrt(variance)
        if std_dev == 0:
            return 0.0
        return round((avg_return / std_dev) * math.sqrt(365), 2)

    @classmethod
    def _interpret_sharpe(cls, sharpe: Optional[float]) -> str:
        if sharpe is None:
            return "--"
        if sharpe < -2:
            return "极端负值（历史底部）"
        elif sharpe < -1:
            return "显著负值（底部信号）"
        elif sharpe < 0:
            return "负回报（可能底部）"
        elif sharpe < 1:
            return "正回报（正常）"
        elif sharpe < 2:
            return "优异回报（警惕）"
        else:
            return "极度优异（可能过热）"

    # ============================================================
    # 综合评估
    # ============================================================

    @classmethod
    def get_all_metrics(cls, currency: str = "BTC") -> Dict[str, Any]:
        """获取所有衍生品指标"""
        perp_basis = cls._get_perp_basis(currency)
        oi_div = cls._get_oi_price_divergence()
        fund_vol = cls._get_funding_volatility()
        liq_heat = cls._get_liquidation_heat()
        stablecoin = cls._get_stablecoin_reserve()
        vol_ratio = cls._get_futures_spot_volume_ratio()
        sharpe_14d, sharpe_30d = cls._get_sharpe_ratio()

        # 加密原生过热综合评分
        assessment = cls._assess_crypto_overheating(
            perp_basis=perp_basis,
            oi_div=oi_div,
            fund_vol=fund_vol,
            liq_heat=liq_heat,
            stablecoin=stablecoin,
            vol_ratio=vol_ratio,
            sharpe_14d=sharpe_14d,
        )

        return {
            "perp_basis": perp_basis,
            "oi_price_divergence": oi_div,
            "funding_volatility": fund_vol,
            "liquidation_heat": liq_heat,
            "stablecoin_reserve": stablecoin,
            "futures_spot_ratio": vol_ratio,
            "sharpe_ratio_14d": sharpe_14d,
            "sharpe_ratio_30d": sharpe_30d,
            "sharpe_signal_14d": cls._interpret_sharpe(sharpe_14d),
            "sharpe_signal_30d": cls._interpret_sharpe(sharpe_30d),
            "overheating_assessment": assessment,
            "timestamp": datetime.now().isoformat(),
        }

    @classmethod
    def _assess_crypto_overheating(cls, perp_basis, oi_div, fund_vol,
                                    liq_heat, stablecoin, vol_ratio, sharpe_14d) -> Dict[str, Any]:
        """加密原生过热综合评估"""
        score = 0
        signals = []

        # 1. 永续基差（权重: 25）
        basis = perp_basis.get("basis_annualized", 0)
        if basis > config.PERP_BASIS_THRESHOLD_EXTREME:
            score -= 10
            signals.append({"emoji": "⚠️", "text": f"永续基差{basis}%极度投机", "type": "overheat"})
        elif basis > config.PERP_BASIS_THRESHOLD_HIGH:
            score -= 5
            signals.append({"emoji": "⚠️", "text": f"永续基差{basis}%偏高", "type": "overheat"})
        elif basis < -2:
            score += 5
            signals.append({"emoji": "🔴", "text": f"永续基差{basis}%（现货溢价=看空）", "type": "bearish"})
        else:
            signals.append({"emoji": "🟢", "text": f"永续基差{basis}%正常", "type": "neutral"})

        # 2. OI-价格背离（权重: 20）
        div = oi_div.get("divergence", "none")
        if div == "bearish":
            score -= 7
            signals.append({"emoji": "🔴", "text": "OI↑价格↓空头加仓", "type": "bearish"})
        elif div == "long_capitulation":
            score -= 5
            signals.append({"emoji": "⚠️", "text": "OI↓价格↓多杀多", "type": "bearish"})
        elif div == "short_squeeze":
            score += 5
            signals.append({"emoji": "🟢", "text": "OI↓价格↑逼空进行中", "type": "bullish"})
        elif div == "bullish":
            score += 3
            signals.append({"emoji": "🟢", "text": "OI↑价格↑多头加仓", "type": "bullish"})

        # 3. 资金费率波动率（权重: 15）
        fv_signal = fund_vol.get("signal", "normal")
        if fv_signal == "extreme":
            score -= 5
            signals.append({"emoji": "⚠️", "text": "费率波动剧烈（拐点预警）", "type": "overheat"})

        # 4. 清算热力（权重: 20）
        liq_level = liq_heat.get("heat_level", "L0")
        liq_bias = liq_heat.get("direction_bias", 0)
        if liq_level == "L3":
            score -= 8
            signals.append({"emoji": "🔴", "text": "L3清算高压", "type": "risk"})
        elif liq_level == "L2":
            score -= 4
            signals.append({"emoji": "⚠️", "text": "L2中度清算压力", "type": "risk"})
        if liq_bias > 0.3:
            score += 3
            signals.append({"emoji": "🟢", "text": "多头痛苦（潜在底部）", "type": "bottom"})

        # 5. 稳定币储备（权重: 10）
        sc_signal = stablecoin.get("signal", "neutral")
        if sc_signal == "strong_inflow":
            score += 4
            signals.append({"emoji": "🟢", "text": "稳定币大量流入", "type": "bullish"})
        elif sc_signal == "outflow":
            score -= 3
            signals.append({"emoji": "⚠️", "text": "稳定币流出", "type": "bearish"})

        # 6. 期货/现货比（权重: 10，降权）
        ratio = vol_ratio.get("ratio", 0)
        if ratio > config.FUTURES_SPOT_RATIO_EXTREME:
            score -= 3
            signals.append({"emoji": "⚠️", "text": f"比值{ratio}x偏高（加密市场中需结合基差看）", "type": "overheat"})

        # 综合判定
        if score >= 8:
            level, name, icon, color, advice = "STRONG_BOTTOM", "衍生品底部信号强", "🟢", "text-green-400", "衍生品信号偏多，关注做多机会"
        elif score >= 3:
            level, name, icon, color, advice = "BOTTOM", "潜在底部", "🟢", "text-green-400", "衍生品指标偏正面"
        elif score >= -3:
            level, name, icon, color, advice = "NEUTRAL", "中性", "⚪", "text-gray-400", "衍生品市场处于正常状态"
        elif score >= -8:
            level, name, icon, color, advice = "OVERHEATED", "过热警告", "⚠️", "text-orange-400", "衍生品过热，注意风险"
        else:
            level, name, icon, color, advice = "EXTREME_OVERHEAT", "极度过热", "🔴", "text-red-400", "衍生品极度过热，降低暴露"

        return {
            "score": score,
            "level": level,
            "name": name,
            "icon": icon,
            "color": color,
            "advice": advice,
            "signals": signals,
            "note": "基于加密原生指标体系（永续基差+OI背离+清算数据+稳定币流动+费率波动率）",
        }
```

- [ ] **Step 2: 验证重写后向后兼容**

Run: `python -c "from services.derivative_metrics import DerivativeMetrics; r = DerivativeMetrics.get_all_metrics(); print('keys:', list(r.keys())); print('has perp_basis:', 'perp_basis' in r); print('has sharpe_ratio_14d:', 'sharpe_ratio_14d' in r); print('overheating:', r['overheating_assessment']['level'])"`

Expected: 输出包含新旧所有指标的字典，向后兼容原调用方（`risk.py`、`dashboard.py`、`llm_analyst.py`）。

- [ ] **Step 3: Commit**

```bash
git add dashboard/services/derivative_metrics.py
git commit -m "feat: rewrite derivative metrics with 8 crypto-native indicators"
```

---

### Task 6: `crypto_market_context.py` 加密市场上下文构建器

**Files:**
- Create: `dashboard/services/crypto_market_context.py`

- [ ] **Step 1: 创建文件并实现上下文构建器**

```python
"""
加密市场上下文构建器
在每个 LLM 分析请求之前构建结构化市场快照，注入到 system prompt
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from config import config

logger = logging.getLogger(__name__)

# 缓存（5分钟 TTL）
_context_cache: Dict[str, Any] = {}
_cache_time: Optional[datetime] = None


class CryptoMarketContext:
    """加密市场结构上下文构建器"""

    STRUCTURAL_KNOWLEDGE = [
        "永续合约（Perpetual Swap）占币圈衍生品交易量的90%以上，期货/现货成交量比天然偏高（5-20x），这不等于「过度杠杆」。",
        "资金费率（Funding Rate）的正常波动范围是-0.03%到+0.10%（8小时费率），持续正值是牛市常态，不代表马上会均值回归。",
        "稳定币（USDT/USDC）的交易所余额变化是重要的「买盘火力」指标——流入=潜在购买需求，流出=资金撤退。",
        "清算瀑布（Liquidation Cascade）是币圈特有的风险事件——当价格触发大量强平订单时，会形成连锁反应，放大价格波动。",
        "BTC市值占比（BTC Dominance）是判断「山寨季」的核心指标——BTC.D下降+BTC价格稳定=资金轮动到山寨币的信号。",
        "永续基差（Perp Basis）比成交量比值更能反映杠杆程度——基差>15%年化才是真正的杠杆过热信号。",
        "OI（未平仓合约量）与价格的背离关系是市场方向的高质量先行指标——OI↑价格↓=空头加仓看空，OI↓价格↑=空头平仓（逼空风险）。",
    ]

    @classmethod
    def build(cls, data: Dict[str, Any], currency: str = "BTC") -> Dict[str, Any]:
        """构建完整市场上下文快照"""
        global _context_cache, _cache_time

        now = datetime.now()
        if _context_cache and _cache_time:
            age_seconds = (now - _cache_time).total_seconds()
            if age_seconds < config.MARKET_CONTEXT_CACHE_TTL:
                return _context_cache

        spot = data.get("spot", 0)
        dvol = data.get("dvol", 0)
        dvol_z = data.get("dvol_z", 0)
        dvol_signal = data.get("dvol_signal", "normal")

        # 周期判断（合并 onchain data）
        mvrv_z = data.get("mvrv_z", 0)
        nupl = data.get("nupl", 0)

        cycle_phase = cls._determine_cycle_phase(mvrv_z, nupl, spot)

        # 结构特征
        perp_basis = data.get("perp_basis", {})
        stablecoin = data.get("stablecoin_reserve", {})
        liq_heat = data.get("liquidation_heat", {})

        context = {
            "cycle": {
                "phase": cycle_phase,
                "btc_dominance": data.get("btc_dominance", 0),
                "dvol_regime": cls._dvol_regime_label(dvol, dvol_z),
            },
            "structure": {
                "perp_dominance": True,
                "contango_depth": perp_basis.get("basis_annualized", 0) if isinstance(perp_basis, dict) else 0,
                "stablecoin_flow": stablecoin.get("label", "未知") if isinstance(stablecoin, dict) else "未知",
                "liquidation_heat": liq_heat.get("heat_level", "L0") if isinstance(liq_heat, dict) else "L0",
            },
            "narrative": {
                "dominant_sectors": cls._infer_sectors(data),
                "macro_overlay": cls._infer_macro(data),
            },
            "warnings": cls._build_warnings(data),
            "structural_knowledge": cls.STRUCTURAL_KNOWLEDGE,
            "updated_at": now.isoformat(),
        }

        _context_cache = context
        _cache_time = now
        return context

    @classmethod
    def to_prompt_text(cls, context: Dict[str, Any]) -> str:
        """将上下文对象序列化为自然语言"""
        cycle = context.get("cycle", {})
        structure = context.get("structure", {})

        parts = [
            "## 当前加密市场结构背景",
            f"- 市场周期阶段: {cycle.get('phase', '未知')}",
            f"- BTC市占率: {cycle.get('btc_dominance', 'N/A')}%",
            f"- 波动率区间: {cycle.get('dvol_regime', '未知')}",
            f"- 永续基差: {structure.get('contango_depth', 'N/A')}% 年化",
            f"- 稳定币流向: {structure.get('stablecoin_flow', '未知')}",
            f"- 清算压力: {structure.get('liquidation_heat', '未知')}",
            "",
            "## 加密市场结构性常识（请在分析中应用）",
        ]

        for i, k in enumerate(context.get("structural_knowledge", []), 1):
            parts.append(f"{i}. {k}")

        warnings = context.get("warnings", [])
        if warnings:
            parts.append("")
            parts.append("## 当前特别关注")
            for w in warnings:
                parts.append(f"- ⚠️ {w}")

        return "\n".join(parts)

    @classmethod
    def _determine_cycle_phase(cls, mvrv_z: float, nupl: float, spot: float) -> str:
        """综合判断市场周期阶段"""
        if mvrv_z > 3.5 or nupl > 0.7:
            return "牛市顶部（高估值风险）"
        elif mvrv_z > 2.0 or nupl > 0.5:
            return "牛市中期"
        elif mvrv_z > 0.0 or nupl > 0.0:
            return "牛市早期/积累"
        elif mvrv_z > -1.0 or nupl > -0.5:
            return "熊市早期"
        elif mvrv_z < -2.0 or nupl < -0.5:
            return "熊市底部（历史低估）"
        return "横盘整理"

    @classmethod
    def _dvol_regime_label(cls, dvol: float, dvol_z: float) -> str:
        if dvol <= 0:
            return "未知"
        if dvol > config.DVOL_PANIC_THRESHOLD:
            return "恐慌波动"
        elif dvol > config.DVOL_HIGH_THRESHOLD:
            return "高波动"
        elif dvol > config.DVOL_LOW_THRESHOLD:
            return "中波动"
        return "低波动"

    @classmethod
    def _infer_sectors(cls, data: Dict[str, Any]) -> list:
        """推断当前主导叙事（简单规则）"""
        sectors = []
        # 如果有AI相关代币大幅上涨的迹象
        if data.get("btc_dominance", 0) > 55:
            sectors.append("BTC主导")
        else:
            sectors.append("山寨币轮动")
        return sectors

    @classmethod
    def _infer_macro(cls, data: Dict[str, Any]) -> str:
        """推断宏观叠加"""
        fear_greed = data.get("fear_greed", 50)
        if fear_greed <= 25:
            return "恐慌情绪（可能过度）"
        elif fear_greed >= 75:
            return "贪婪情绪（警惕回调）"
        return "中性情绪"

    @classmethod
    def _build_warnings(cls, data: Dict[str, Any]) -> list:
        """构建告警列表"""
        warnings = []
        perp_basis = data.get("perp_basis", {})
        if isinstance(perp_basis, dict):
            basis = perp_basis.get("basis_annualized", 0)
            if basis > config.PERP_BASIS_THRESHOLD_HIGH:
                pct = perp_basis.get("hybrid_assessment", {}).get("percentile", {}).get("pct", 50)
                warnings.append(f"永续基差 {basis}% 年化（{pct}%百分位），杠杆水平偏高")

        oi_div = data.get("oi_price_divergence", {})
        if isinstance(oi_div, dict) and oi_div.get("divergence") in ("bearish", "long_capitulation"):
            warnings.append(f"OI-价格背离: {oi_div.get('divergence_label', '')}")

        liq_heat = data.get("liquidation_heat", {})
        if isinstance(liq_heat, dict) and liq_heat.get("heat_level") in ("L2", "L3"):
            warnings.append(f"清算压力 L{liq_heat.get('heat_level')}，注意连锁清算风险")

        return warnings
```

- [ ] **Step 2: 验证上下文构建**

Run: `python -c "from services.crypto_market_context import CryptoMarketContext; ctx = CryptoMarketContext.build({'spot': 90000, 'dvol': 65, 'dvol_z': 1.2}); prompt = CryptoMarketContext.to_prompt_text(ctx); print(prompt[:500])"`

Expected: 输出包含结构化市场背景和 7 条加密市场常识的 prompt 文本。

- [ ] **Step 3: Commit**

```bash
git add dashboard/services/crypto_market_context.py
git commit -m "feat: add crypto market context builder for LLM prompt injection"
```

---

### Task 7: `ai_router.py` 新增 `crypto_analyst` preset

**Files:**
- Modify: `dashboard/services/ai_router.py:179-240`

- [ ] **Step 1: 在 `ai_chat_with_config` 中添加 `crypto_analyst` preset**

在 `ai_chat_with_config` 函数的 preset 判断逻辑中添加新 preset。在 `if preset == "fast":` 分支之前添加：

```python
    if preset == "crypto_analyst":
        thinking = True
        reasoning_effort = "high"
        # 注入加密结构性知识作为 system message
        from services.crypto_market_context import CryptoMarketContext
        ctx = CryptoMarketContext.build({}, "BTC")
        system_override = CryptoMarketContext.to_prompt_text(ctx)
        messages = [{"role": "system", "content": system_override[:3000]}] + messages
```

- [ ] **Step 2: 在 `ai_chat_with_config` 的 preset 条件链末尾添加**

找到 preset 条件判断的末尾（`if preset == "fast":` 等），在 `elif preset in ("analysis", "debate", "audit"):` 块之前添加上述代码。

- [ ] **Step 3: 验证新 preset 工作**

Run: `python -c "from services.ai_router import ai_chat_with_config; r = ai_chat_with_config([{'role': 'user', 'content': '1+1=?'}], preset='crypto_analyst', max_tokens=50); print('OK' if r else 'API key not set (expected)')"`

Expected: 如果未配置 API key 则输出 "API key not set (expected)"。如果已配置则返回 AI 回复。

- [ ] **Step 4: Commit**

```bash
git add dashboard/services/ai_router.py
git commit -m "feat(ai_router): add crypto_analyst preset with market context injection"
```

---

### Task 8: Panel — 新增衍生品指标面板 + 修改 6 个面板的规则函数和 LLM 模板

**Files:**
- Modify: `dashboard/services/panel_analyzers.py`

- [ ] **Step 1: 添加衍生品指标规则函数**

在 `calc_trend_strength` 函数之后（约第 490 行）添加新的衍生品指标规则函数：

```python
def calc_perp_basis_signal(data: dict, cache: dict):
    """永续基差信号（加密原生）"""
    perp_basis = data.get("perp_basis", {})
    if isinstance(perp_basis, dict):
        basis = _safe_float(perp_basis.get("basis_annualized", 0))
        pct = perp_basis.get("percentile", 50)
    else:
        basis = 0
        pct = 50

    if basis <= 0:
        return _make_result(name="永续基差", score=50, verdict="数据缺失")

    if basis > 30:
        return _make_result(name="永续基差", score=15,
                            verdict=f"极端投机(basis={basis}%, p{pct})",
                            reasoning=[f"年化基差={basis}%]>30%", f"百分位={pct}", "危险信号"])
    elif basis > 15:
        return _make_result(name="永续基差", score=30,
                            verdict=f"杠杆偏高(basis={basis}%, p{pct})",
                            reasoning=[f"年化基差={basis}%>15%", f"百分位={pct}"])
    elif basis > 8:
        return _make_result(name="永续基差", score=60,
                            verdict=f"温和看多(basis={basis}%)",
                            reasoning=[f"年化基差={basis}% 正常偏高"])
    elif basis < -2:
        return _make_result(name="永续基差", score=25,
                            verdict=f"现货溢价({basis}%)，看空信号",
                            reasoning=[f"年化基差={basis}%<0", "perp<spot，市场看空"])
    else:
        return _make_result(name="永续基差", score=75,
                            verdict=f"正常Contango(basis={basis}%)",
                            reasoning=[f"年化基差={basis}% 健康范围"])


def calc_oi_divergence_signal(data: dict, cache: dict):
    """OI-价格背离信号"""
    oi_div = data.get("oi_price_divergence", {})
    if not isinstance(oi_div, dict):
        return _make_result(name="OI背离", score=50, verdict="数据缺失")

    divergence = oi_div.get("divergence", "none")
    label = oi_div.get("divergence_label", "")

    if divergence == "bearish":
        return _make_result(name="OI背离", score=20,
                            verdict=label,
                            reasoning=["OI↑价格↓", "空头加仓=看空信号"])
    elif divergence == "long_capitulation":
        return _make_result(name="OI背离", score=30,
                            verdict=label,
                            reasoning=["OI↓价格↓", "多头平仓=短期看空"])
    elif divergence == "short_squeeze":
        return _make_result(name="OI背离", score=80,
                            verdict=label,
                            reasoning=["OI↓价格↑", "空头平仓=逼空"])
    elif divergence == "bullish":
        return _make_result(name="OI背离", score=70,
                            verdict=label,
                            reasoning=["OI↑价格↑", "多头加仓=看多"])
    elif divergence == "breakout_looming":
        return _make_result(name="OI背离", score=45,
                            verdict=label,
                            reasoning=["OI↑价格→", "分歧加大=即将突破"])
    else:
        return _make_result(name="OI背离", score=55,
                            verdict="OI与价格同向，无背离",
                            reasoning=["量价关系正常"])


def calc_liquidation_signal(data: dict, cache: dict):
    """清算热力信号"""
    liq = data.get("liquidation_heat", {})
    if not isinstance(liq, dict):
        return _make_result(name="清算热力", score=50, verdict="数据缺失")

    level = liq.get("heat_level", "L0")
    bias = _safe_float(liq.get("direction_bias", 0))
    total = _safe_float(liq.get("total_liquidation_1h_usd", 0))

    if level == "L3":
        base_score = 15
        verdict = f"L3高压({total/1e6:.0f}M/h清算)"
    elif level == "L2":
        base_score = 30
        verdict = f"L2中度压力({total/1e6:.0f}M/h清算)"
    elif level == "L1":
        base_score = 55
        verdict = f"L1轻度清算"
    else:
        base_score = 65
        verdict = "正常"

    # 方向偏向修正
    if bias > 0.3:
        base_score += 5
        verdict += "，多头痛苦（潜在底部）"
    elif bias < -0.3:
        base_score -= 5
        verdict += "，空头痛苦（潜在顶部）"

    return _make_result(name="清算热力", score=base_score, verdict=verdict,
                        reasoning=[f"1h清算={total/1e6:.1f}M", f"方向偏向={bias:.2f}"])


def calc_funding_vol_signal(data: dict, cache: dict):
    """资金费率波动率信号"""
    fv = data.get("funding_volatility", {})
    if isinstance(fv, dict):
        vol_pct = _safe_float(fv.get("volatility_7d_pct", 0))
        signal = fv.get("signal", "normal")
        label = fv.get("label", "")
    else:
        vol_pct = 0
        signal = "unknown"
        label = "数据缺失"

    if signal == "unknown" or vol_pct <= 0:
        return _make_result(name="费率波动", score=50, verdict="数据不足")

    if signal == "extreme":
        return _make_result(name="费率波动", score=15,
                            verdict=f"费率波动剧烈({vol_pct}%)，潜在拐点",
                            reasoning=[f"波动率={vol_pct}%>0.1%", "市场情绪极不稳定"])
    elif signal == "high":
        return _make_result(name="费率波动", score=35,
                            verdict=f"费率波动偏高({vol_pct}%)，情绪反复",
                            reasoning=[f"波动率={vol_pct}%"])
    elif signal == "stable":
        return _make_result(name="费率波动", score=75,
                            verdict=f"费率稳定({vol_pct}%)，市场共识强",
                            reasoning=[f"波动率={vol_pct}%<0.01%"])
    else:
        return _make_result(name="费率波动", score=60,
                            verdict=f"费率波动正常({vol_pct}%)",
                            reasoning=[f"波动率={vol_pct}%"])
```

- [ ] **Step 2: 在 PANEL_CONFIGS 中添加衍生品指标面板**

在 `# === 链上指标 ===` 面板定义之前添加：

```python
    # === 衍生品指标 ===
    "derivative_metrics": {
        "name": "衍生品指标",
        "rules": [
            {"id": "perp_basis", "name": "永续基差", "fn": calc_perp_basis_signal, "weight": 0.3},
            {"id": "oi_div", "name": "OI背离", "fn": calc_oi_divergence_signal, "weight": 0.25},
            {"id": "liq", "name": "清算热力", "fn": calc_liquidation_signal, "weight": 0.25},
            {"id": "fund_vol", "name": "费率波动", "fn": calc_funding_vol_signal, "weight": 0.2},
        ],
        "signal_formula": "weighted_score",
        "default_action": "",
    },
```

- [ ] **Step 3: 添加衍生品面板的 LLM prompt 模板**

在 `LLM_PROMPT_TEMPLATES` 中添加：

```python
    "derivative_metrics": {
        "synthesis": "基于以下加密原生衍生品数据，分析{currency}的衍生品市场状态:\n"
                     "- 永续基差: {perp_basis}% 年化\n"
                     "- OI-价格背离: {oi_divergence}\n"
                     "- 清算热力: 1h清算 ${liquidation_total_usd}\n"
                     "- 资金费率波动率: {funding_volatility}%\n"
                     "- 期货/现货比: {futures_spot_ratio}x\n"
                     "- 稳定币流向: {stablecoin_flow}\n"
                     "- 规则评分:\n{rule_scores}\n\n"
                     "【重要】请以加密原生视角分析。永续合约占90%+交易量，期货/现货比天然偏高(5-20x)。"
                     "请重点关注永续基差和OI-价格背离，而非传统杠杆比率。",
        "bull_context": "衍生品利多信号:\n"
                        "- 永续基差温和(0-8%)\n"
                        "- OI↑价格↑多头健康加仓\n"
                        "- 稳定币持续流入\n"
                        "- 清算压力低\n"
                        "- 费率波动稳定",
        "bear_context": "衍生品利空信号:\n"
                        "- 永续基差>15%年化（杠杆过热）\n"
                        "- 基差为负（perp<spot）\n"
                        "- OI↑价格↓空头加仓\n"
                        "- L2+清算压力\n"
                        "- 费率剧烈波动",
        "judge_criteria": "综合永续基差、OI背离、清算数据、稳定币流向、费率波动率五个维度，"
                         "以加密原生标准判定衍生品市场风险等级，给出具体操作建议。",
    },
```

- [ ] **Step 4: 修改 5 个现有面板的 LLM prompt 模板**

修改以下面板的 `synthesis` 模板，在数据段结尾注入市场上下文：

**market_metrics** (metric_cards, 约 line 762): 在 `synthesis` 末尾追加 `\n\n加密市场结构背景:\n{market_context}`

**risk_command_center** (约 line 767): 在 `synthesis` 末尾追加 `\n\n加密市场结构背景:\n{market_context}`

**strategy_center** (约 line 773): 在 `synthesis` 末尾追加 `\n\n注意：永续合约占币圈90%+交易量，基差{perp_basis}%年化是核心杠杆指标。`

**money_flow** (约 line 845): 在 `synthesis` 末尾追加 `\n\n注意：关注清算方向偏向—多头清算占优→潜在底部，空头清算占优→潜在顶部。`

**onchain_metrics** (约 line 851): 在 `synthesis` 末尾追加 `\n\n注意：稳定币交易所余额变化是「买盘火力」指标，流入=看多，流出=防御。`

- [ ] **Step 5: 在 `unified_recommendation_engine.py` 的 `LLMPromptBuilder.build` 中注入市场上下文**

在 `LLMPromptBuilder.build` 方法中，`format_args` 构建之前添加：

```python
        from services.crypto_market_context import CryptoMarketContext
        market_ctx = CryptoMarketContext.build(data_snapshot, currency)
        market_ctx_text = CryptoMarketContext.to_prompt_text(market_ctx)
```

并在 `format_args` 的 `_SafeDict` 中添加 `"market_context": market_ctx_text`。

修改 `services/unified_recommendation_engine.py` 的 `LLMPromptBuilder.build` 方法（约第 198-203 行）：

```python
        from services.crypto_market_context import CryptoMarketContext
        market_ctx = CryptoMarketContext.build(data_snapshot, currency)
        market_ctx_text = CryptoMarketContext.to_prompt_text(market_ctx)

        format_args = _SafeDict({
            "currency": currency, "spot": spot, "dvol": dvol, "dvol_z": dvol_z,
            "rule_scores": rule_scores_text, "data_snapshot": data_text,
            "panel_id": panel_id,
            "market_context": market_ctx_text,
            **data_snapshot,
        })
```

- [ ] **Step 6: 验证 LLM prompt 模板渲染**

Run: `python -c "from services.panel_analyzers import get_llm_prompt; t = get_llm_prompt('derivative_metrics'); print('synthesis' in t, 'bull_context' in t, 'bear_context' in t)"`

Expected: `True True True`

- [ ] **Step 7: Commit**

```bash
git add dashboard/services/panel_analyzers.py dashboard/services/unified_recommendation_engine.py
git commit -m "feat: add derivative metrics panel + inject crypto market context into LLM prompts"
```

---

### Task 9: 更新 `recommendations.py` 的数据采集 + 注册新面板

**Files:**
- Modify: `dashboard/api/recommendations.py:44-138`
- Modify: `dashboard/services/panel_analyzers.py:553`

- [ ] **Step 1: 在 `_collect_panel_data` 中添加新指标采集**

在 `_collect_panel_data` 函数末尾（约第 138 行，return 之前）添加：

```python
    # 衍生品指标（加密原生）
    try:
        from services.derivative_metrics import DerivativeMetrics
        deriv = DerivativeMetrics.get_all_metrics(currency)
        data["perp_basis"] = deriv.get("perp_basis", {})
        data["oi_price_divergence"] = deriv.get("oi_price_divergence", {})
        data["funding_volatility"] = deriv.get("funding_volatility", {})
        data["liquidation_heat"] = deriv.get("liquidation_heat", {})
        data["stablecoin_reserve"] = deriv.get("stablecoin_reserve", {})
        data["futures_spot_ratio"] = deriv.get("futures_spot_ratio", {})
    except Exception as e:
        logger.warning("Derivative metrics fetch failed: %s", e)
```

- [ ] **Step 2: 展开扁平的子字典，让模板变量可直接访问**

在 return data 之前添加：

```python
    # 展开嵌套字典，让 panel LLM 模板可以直接使用 {perp_basis} 等占位符
    if isinstance(data.get("perp_basis"), dict):
        data["basis_annualized"] = data["perp_basis"].get("basis_annualized", 0)
        data["perp_price"] = data["perp_basis"].get("perp_price", 0)
    if isinstance(data.get("oi_price_divergence"), dict):
        data["oi_divergence"] = data["oi_price_divergence"].get("divergence_label", "无数据")
    if isinstance(data.get("funding_volatility"), dict):
        data["funding_volatility"] = data["funding_volatility"].get("volatility_7d_pct", 0)
    if isinstance(data.get("liquidation_heat"), dict):
        data["liquidation_total_usd"] = data["liquidation_heat"].get("total_liquidation_1h_usd", 0)
    if isinstance(data.get("futures_spot_ratio"), dict):
        data["futures_spot_ratio"] = data["futures_spot_ratio"].get("ratio", 0)
    if isinstance(data.get("stablecoin_reserve"), dict):
        data["stablecoin_flow"] = data["stablecoin_reserve"].get("label", "未知")
```

- [ ] **Step 3: 验证数据采集**

Run: `python -c "from api.recommendations import _collect_panel_data; d = _collect_panel_data('BTC'); print('perp_basis' in d, 'oi_price_divergence' in d, 'liquidation_heat' in d)"`

Expected: `True True True`

- [ ] **Step 4: Commit**

```bash
git add dashboard/api/recommendations.py
git commit -m "feat(api): add crypto-native metrics to panel data collection"
```

---

### Task 10: 集成验证 + 全部测试

**Files:**
- Create: `dashboard/tests/test_crypto_thresholds.py`
- Create: `dashboard/tests/test_crypto_market_context.py`
- Modify: `dashboard/tests/test_derivative_metrics.py` (如果存在)

- [ ] **Step 1: 运行现有测试确保无回归**

Run: `cd dashboard && python -m pytest tests/ -v --tb=short 2>&1 | Select-String -Pattern "passed|failed|error"`

Expected: 所有现有测试通过。

- [ ] **Step 2: 编写 `test_crypto_thresholds.py`**

```python
"""测试加密阈值系统"""
import pytest
from services.crypto_thresholds import CryptoThresholds


class TestFixedThresholds:
    def test_perp_basis_normal(self):
        r = CryptoThresholds.get_fixed_threshold("perp_basis", 5.0)
        assert r["signal"] == "normal"

    def test_perp_basis_high(self):
        r = CryptoThresholds.get_fixed_threshold("perp_basis", 18.0)
        assert r["signal"] == "high"

    def test_perp_basis_extreme(self):
        r = CryptoThresholds.get_fixed_threshold("perp_basis", 35.0)
        assert r["signal"] == "extreme_high"

    def test_perp_basis_negative(self):
        r = CryptoThresholds.get_fixed_threshold("perp_basis", -5.0)
        assert r["signal"] == "negative"

    def test_futures_spot_ratio_crypto_normal(self):
        r = CryptoThresholds.get_fixed_threshold("futures_spot_ratio", 6.0)
        assert r["signal"] == "normal"  # 加密市场 6x 是正常的

    def test_futures_spot_ratio_high(self):
        r = CryptoThresholds.get_fixed_threshold("futures_spot_ratio", 20.0)
        assert r["signal"] == "very_high"

    def test_liquidation_l0(self):
        r = CryptoThresholds.get_fixed_threshold("liquidation_heat", 500_000)
        assert r["signal"] == "L0"

    def test_liquidation_l2(self):
        r = CryptoThresholds.get_fixed_threshold("liquidation_heat", 8_000_000)
        assert r["signal"] == "L2"

    def test_funding_volatility_stable(self):
        r = CryptoThresholds.get_fixed_threshold("funding_volatility", 0.005)
        assert r["signal"] == "stable"

    def test_funding_volatility_extreme(self):
        r = CryptoThresholds.get_fixed_threshold("funding_volatility", 0.15)
        assert r["signal"] == "extreme"

    def test_stablecoin_inflow(self):
        r = CryptoThresholds.get_fixed_threshold("stablecoin_flow", 7.0)
        assert r["signal"] == "strong_inflow"

    def test_unknown_metric(self):
        r = CryptoThresholds.get_fixed_threshold("nonexistent", 100)
        assert r["signal"] == "unknown"
```

- [ ] **Step 3: 运行阈值测试**

Run: `cd dashboard && python -m pytest tests/test_crypto_thresholds.py -v`

Expected: 12 passed

- [ ] **Step 4: 编写 `test_crypto_market_context.py`**

```python
"""测试加密市场上下文构建器"""
import pytest
from services.crypto_market_context import CryptoMarketContext


class TestMarketContext:
    def test_build_basic(self):
        ctx = CryptoMarketContext.build({"spot": 90000, "dvol": 65, "dvol_z": 1.2, "fear_greed": 45})
        assert ctx["cycle"]["phase"] in ["熊市早期", "横盘整理", "牛市早期/积累", "牛市中期", "牛市顶部"]
        assert ctx["cycle"]["dvol_regime"] in ["恐慌波动", "高波动", "中波动", "低波动", "未知"]
        assert ctx["structure"]["perp_dominance"] is True
        assert len(ctx["structural_knowledge"]) == 7

    def test_to_prompt_text(self):
        ctx = CryptoMarketContext.build({"spot": 90000, "dvol": 65, "dvol_z": 1.2})
        text = CryptoMarketContext.to_prompt_text(ctx)
        assert "加密市场结构背景" in text
        assert "永续合约" in text
        assert "BTC市占率" in text

    def test_cache_ttl(self):
        ctx1 = CryptoMarketContext.build({"spot": 90000, "dvol": 50, "dvol_z": 0})
        ctx2 = CryptoMarketContext.build({"spot": 91000, "dvol": 50, "dvol_z": 0})
        assert ctx1 is ctx2  # 缓存命中，返回同一对象

    def test_warnings_on_high_basis(self):
        ctx = CryptoMarketContext.build({
            "spot": 90000, "dvol": 65, "dvol_z": 1.2,
            "perp_basis": {"basis_annualized": 20, "hybrid_assessment": {"percentile": {"pct": 92}}},
        })
        warnings = ctx.get("warnings", [])
        assert any("永续基差" in w for w in warnings)

    def test_warnings_on_divergence(self):
        ctx = CryptoMarketContext.build({
            "spot": 90000, "dvol": 65, "dvol_z": 1.2,
            "oi_price_divergence": {"divergence": "bearish", "divergence_label": "OI↑价格↓（空头加仓=看空）"},
        })
        warnings = ctx.get("warnings", [])
        assert any("背离" in w for w in warnings)
```

- [ ] **Step 5: 运行上下文测试**

Run: `cd dashboard && python -m pytest tests/test_crypto_market_context.py -v`

Expected: 5 passed

- [ ] **Step 6: 运行全部测试**

Run: `cd dashboard && python -m pytest tests/ -v --tb=short 2>&1 | Select-String -Pattern "passed|failed|error"`

Expected: 所有测试通过。

- [ ] **Step 7: 启动服务验证衍生品面板可访问**

Run: `cd dashboard && python -c "from services.unified_recommendation_engine import UnifiedRecommendationEngine; e = UnifiedRecommendationEngine(); print('derivative_metrics' in e.panels)"`

Expected: `True`

- [ ] **Step 8: Commit**

```bash
git add dashboard/tests/
git commit -m "test: add crypto thresholds and market context tests"
```

---

### Task 11: 前端 — 注册衍生品面板到推荐系统

**Files:**
- Modify: `dashboard/static/recommendations.js:468` (PANEL_TARGETS)

- [ ] **Step 1: 在 `PANEL_TARGETS` 中添加衍生品面板映射**

在 `recommendations.js` 的 `PANEL_TARGETS` 对象中添加：

```javascript
derivative_metrics: '#derivativeSection',
```

（注：如果 HTML 中不存在 `#derivativeSection` 容器，则先跳过前端面板映射。衍生品数据已通过 `/api/recommendation/derivative_metrics` API 可用。）

- [ ] **Step 2: Commit**

```bash
git add dashboard/static/recommendations.js
git commit -m "feat(frontend): register derivative_metrics panel in recommendations"
```

---

### Task 12: 最终验证

- [ ] **Step 1: 启动服务端**

```bash
cd dashboard && python main.py
```

- [ ] **Step 2: 测试 API**

```bash
curl http://localhost:8000/api/recommendation/derivative_metrics?currency=BTC
```

Expected: 返回衍生品指标的信号灯和规则报告。

- [ ] **Step 3: 验证 LLM 分析**

```bash
curl -X POST http://localhost:8000/api/recommendation/derivative_metrics/llm \
  -H "Content-Type: application/json" \
  -d '{"currency":"BTC","force_refresh":false}'
```

Expected: SSE 流式返回，LLM prompt 中包含"加密市场结构背景"和"永续基差"等上下文。

- [ ] **Step 4: 确认期货/现货比不再总是告警**

观察 `/api/recommendation/derivative_metrics` 返回的 `report.factors` 中，永续基差信号是否基于加密校准阈值（而非传统金融阈值）。

- [ ] **Step 5: 运行全套测试最终确认**

Run: `cd dashboard && python -m pytest tests/ -v --tb=short 2>&1 | Select-String -Pattern "passed|failed|error"`

Expected: 所有测试通过，无回归。

---
