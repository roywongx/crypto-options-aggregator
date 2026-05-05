# 加密原生 AI 分析框架重设计

> 将统一推荐引擎的规则判断和 LLM 分析从传统金融思维切换到加密原生思维，消除假阳性信号，提升分析质量。

## 问题诊断

当前系统使用传统金融的标准判断加密市场，导致系统性误判：

1. **期货/现货比**：阈值 (1.5/3/5) 对加密市场过低，永续合约结构导致比特币比值天然在 5-20x，永远触发"极度过热"告警
2. **缺失指标**：永续基差、OI-价格背离、清算热力图、稳定币购买力——这些都是加密市场核心信号
3. **LLM Prompt**：缺乏加密市场结构性背景知识注入，AI 分析基于错误的假设前提
4. **阈值体系**：全部固定阈值，无法自适应市场阶段（牛市的"正常"和熊市的"正常"完全不同）

## 架构

```
                          ┌──────────────────────────────┐
                          │   Unified Recommendation      │
                          │   Engine (panel_analyzers)    │
                          │   → 16-panel rule functions   │
                          │   → LLM prompt templates      │
                          └──────────┬───────────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
              ▼                      ▼                      ▼
   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
   │ Crypto Market    │  │ Crypto Threshold │  │ Perp Basis       │
   │ Context Builder  │  │ Registry         │  │ Analyzer         │
   │ (新)             │  │ (新)             │  │ (新)             │
   └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
            │                     │                      │
            │    ┌────────────────┼──────────────────────┘
            │    │                │
            ▼    ▼                ▼
   ┌──────────────────────────────────────────┐
   │         Derivative Metrics (重写)         │
   │  → 永续基差 · 清算热力图 · OI-价格背离    │
   │  → 稳定币交易所储备 · 资金费率波动率       │
   │  → 混合阈值: 滚动百分位 + 加密校准固定值  │
   └──────────────────────────────────────────┘
            │
            ▼
   ┌──────────────────────────────────────────┐
   │         AI Router (ai_router.py)          │
   │  → Crypto market context injection        │
   │  → Panel-specific system prompts          │
   └──────────────────────────────────────────┘
```

## 文件变更清单

| 文件 | 类型 | 职责 |
|------|------|------|
| `services/crypto_market_context.py` | 新增 | 构建加密原生市场上下文：周期判断、结构性特征、叙事层 |
| `services/crypto_thresholds.py` | 新增 | 混合阈值注册表：百分位阈值 + 加密校准固定阈值 |
| `services/perp_basis_analyzer.py` | 新增 | 永续合约基差分析：年化基差、期限结构、Contango强度 |
| `services/derivative_metrics.py` | 重写 | 从4个传统指标扩展到8个加密原生指标 |
| `services/panel_analyzers.py` | 修改 | 更新6个面板的规则函数 + LLM prompt模板 |
| `services/unified_recommendation_engine.py` | 修改 | 注入加密市场上下文到LLM prompt |
| `services/ai_router.py` | 修改 | 新增 crypto_analyst preset + 系统提示词 |
| `config.py` | 修改 | 新增加密原生指标配置项 |
| `db/schema.py` | 修改 | 新增 3 张数据表 |
| `db/maintenance.py` | 修改 | 追加新表的数据清理逻辑 |

## 新指标设计

### 指标 1：永续基差 Perp Basis（替换期货/现货比）

永续合约占币圈衍生品 90%+ 交易量，基差直接反映杠杆成本和市场方向偏向。

```
年化基差 = (perp_price / spot_price - 1) × (365 × 24 / 8) × 100

阈值（加密校准固定值）：
- 0~8%: 正常 Contango
- 8~15%: 温和看多
- 15~30%: 强烈看多（投机过热预警）
- >30%: 极端投机
- 负基差: 看空信号

阈值策略：混合 — 核心用滚动 30 日百分位，辅助用上面固定阈值。
```

### 指标 2：OI-价格背离 OI-Price Divergence（新增）

检测"放量不涨"、"量价背离"。

```
OI 变化率 = (current_OI - OI_24h_ago) / OI_24h_ago × 100
价格变化率 = (current_price - price_24h_ago) / price_24h_ago × 100

背离类型：
- OI↑ 价格↓：空头加仓 = 看空
- OI↓ 价格↑：空头平仓 = 短期看多（逼空风险）
- OI↑ 价格→：分歧加大 = 即将突破

阈值：滚动百分位，OI 与价格方向性背离超过 1.5 个标准差时报警
```

### 指标 3：资金费率波动率 Funding Rate Volatility（新增）

费率绝对值高不一定是危险信号，但费率剧烈波动 = 市场情绪不稳定。

```
资金费率波动率 = std(8h_funding_rate, 7_periods)

阈值：
- <0.01%: 稳定
- 0.01~0.05%: 正常波动
- 0.05~0.10%: 情绪反复
- >0.10%: 极端波动（潜在拐点）
```

### 指标 4：清算热力等级 Liquidation Heat（新增）

清算数据是币圈特有的"痛苦指数"。

```
等级判定：
- L0: 1h 清算 <100万美元 → 正常
- L1: 1h 清算 100万-500万 → 轻度压力
- L2: 1h 清算 500万-2000万 → 中度压力
- L3: 1h 清算 >2000万 → 高压

方向偏向 = (多头清算 - 空头清算) / 总清算
- >0.3: 多头痛苦（潜在底部）
- <-0.3: 空头痛苦（潜在顶部）
```

### 指标 5：稳定币交易所储备 Stablecoin Reserve（新增）

稳定币流入 = 潜在买盘火力。

```
储备变化率 = (current_reserve - reserve_7d_ago) / reserve_7d_ago × 100

阈值：
- 流入 >5%: 强买盘预期
- 流入 2~5%: 温和看多
- 平稳 ±2%: 中性
- 流出 >5%: 资金撤退
```

### 指标 6-8：保留但重校准

| # | 指标 | 改动 |
|---|------|------|
| 6 | 期货/现货成交量比 | 阈值 1.5/3/5 → 3/8/15/25，加密校准 |
| 7 | OI 结构 (Call/Put OI Ratio) | 保留，无改动 |
| 8 | 期货期限结构 | 保留，无改动 |

## 加密市场上下文构建器

在每个 LLM 分析请求之前，构建结构化市场快照注入 system prompt：

```python
CryptoMarketContext = {
    "cycle": {
        "phase": "牛市中期",          # MVRV-Z + NUPL + 200WMA 综合判断
        "btc_dominance": 58.3,        # BTC.D
        "dvol_regime": "高波动",
    },
    "structure": {
        "perp_dominance": True,       # 永续主导
        "contango_depth": 12.5,       # 年化基差 %
        "stablecoin_flow": "+3.2%",
        "liquidation_heat": "L1",
    },
    "narrative": {
        "dominant_sectors": ["AI", "RWA"],
        "macro_overlay": "降息预期",
    },
    "warnings": [
        "永续基差 12.5% 处于 90% 百分位",
        "OI 与价格背离：OI 上升但价格横盘",
    ]
}
```

## AI Router 新增 Preset

`crypto_analyst` preset：
- thinking: enabled
- reasoning_effort: high
- system_prompt: 注入 7 条加密市场结构性常识（永续主导、资金费率常态、稳定币火力、清算瀑布、BTC 市占率、基差判断、OI 背离）

## LLM Prompt 模板重设计

### 衍生品面板（当前最严重的假阳性问题）

**重设计前**（传统金融思维）：
"分析期货/现货成交量比 10.66，判断杠杆风险"

**重设计后**（加密原生思维）：
- 先展示 8 个加密原生指标数据
- 注入市场结构背景
- 要求 AI 对比"加密市场常态"而非传统金融标准
- 重点关注 OI-价格背离方向
- 强调资金费率波动率比绝对值更重要

### 6 个面板的修改清单

| 面板 | Prompt 主要改动 |
|------|----------------|
| 衍生品指标 | 从单一比值 → 8 个指标综合分析；注入"永续主导"结构性认知 |
| 风险指挥中心 | 加入清算热力图 + OI-价格背离作为新的风险维度 |
| 市场指标 | 加入稳定币流动 + BTC 市占率趋势 |
| 资金流向 | 加入清算方向偏向分析（多头/空头痛苦指数） |
| 策略中心 | 加入永续基差作为 APR 质量的辅助判断 |
| On-Chain 指标 | 加入稳定币交易所余额变化 |

## 数据库 Schema 新增

```sql
-- 永续基差历史（用于百分位计算）
CREATE TABLE IF NOT EXISTS perp_basis_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    currency TEXT NOT NULL DEFAULT 'BTC',
    perp_price REAL NOT NULL,
    spot_price REAL NOT NULL,
    basis_annualized REAL NOT NULL,
    funding_rate REAL
);

-- 未平仓合约量历史（OI-价格背离检测）
CREATE TABLE IF NOT EXISTS oi_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    currency TEXT NOT NULL DEFAULT 'BTC',
    open_interest_usd REAL NOT NULL,
    price REAL NOT NULL,
    oi_change_24h_pct REAL,
    price_change_24h_pct REAL
);

-- 稳定币交易所余额快照
CREATE TABLE IF NOT EXISTS stablecoin_reserve_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    exchange TEXT NOT NULL DEFAULT 'binance',
    asset TEXT NOT NULL DEFAULT 'USDT',
    balance REAL NOT NULL,
    change_7d_pct REAL
);
```

保留天数：基差/OI 30 天，稳定币 90 天。

## Config 新增

```python
# 加密原生指标阈值
PERP_BASIS_THRESHOLD_HIGH = 15.0          # 年化基差 >15% = 过热
PERP_BASIS_THRESHOLD_EXTREME = 30.0
PERP_BASIS_PERCENTILE_WINDOW = 30         # 百分位窗口（天）
FUTURES_SPOT_RATIO_HIGH = 8.0             # 加密校准
FUTURES_SPOT_RATIO_EXTREME = 15.0
LIQUIDATION_HEAT_L2_THRESHOLD = 5_000_000 # 500万美元
LIQUIDATION_HEAT_L3_THRESHOLD = 20_000_000
STABLECOIN_INFLOW_HIGH = 5.0              # 流入 >5%
STABLECOIN_OUTFLOW_HIGH = -5.0
OI_DIVERGENCE_STD_THRESHOLD = 1.5
MARKET_CONTEXT_CACHE_TTL = 300            # 5分钟
LLM_REASONING_EFFORT = "high"             # 修复原 "max"
```

## 实施优先级

| 阶段 | 文件 | 行数 | 依赖 |
|------|------|------|------|
| **Phase 1: 指标层** | `derivative_metrics.py` 重写 | ~300 | `crypto_thresholds.py` |
| | `crypto_thresholds.py` 新增 | ~120 | 无 |
| | `perp_basis_analyzer.py` 新增 | ~180 | `crypto_thresholds.py` |
| | `db/schema.py` 新增 3 表 | ~60 | 无 |
| | `config.py` 新增配置 | ~30 | 无 |
| **Phase 2: 上下文层** | `crypto_market_context.py` 新增 | ~200 | Phase 1 |
| | `ai_router.py` 修改 preset | ~40 | Phase 1 |
| **Phase 3: 集成层** | `panel_analyzers.py` 修改 6 面板 | ~120 | Phase 1+2 |
| | `unified_recommendation_engine.py` 修改 | ~40 | Phase 2 |
| | `db/maintenance.py` 追加清理 | ~20 | Phase 1 |
| **Phase 4: 测试** | `tests/test_derivative_metrics.py` 重写 | ~150 | Phase 1 |
| | `tests/test_crypto_market_context.py` 新增 | ~80 | Phase 2 |

总预估：~1,340 行代码。

## 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 阈值策略 | 混合（百分位 + 固定值） | 核心指标用百分位自适应，辅助用校准固定值 |
| 永续基差 vs 期货/现货比 | 基差为主，比值保留为辅 | 基差直接反映资金成本，比值反映市场结构 |
| 数据源 | 优先 Binance REST API | 免费、无需 API Key、数据质量好 |
| 百分位窗口 | 30 天（可配置） | 覆盖完整情绪周期 |
| 清算数据 | Binance Futures API | 覆盖最大流动性池 |
| 稳定币储备 | CryptoQuant 免费层 + fallback | 避免单点依赖 |
| DB 表设计 | 独立新表 | 职责分离，方便百分位查询 |
| 向后兼容 | 完全兼容 | 所有旧 API 返回格式不变，只改内部计算 |
