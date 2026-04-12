# Crypto Options Aggregator — 网格策略优化实施方案

> **文档性质**: 实施规范（给执行AI用）
> **策略核心**: 长期持有BTC + Sell Put/Call 网格做多 = 备兑轮 + 网格收租
> **编写者**: Hermes（总指挥）
> **日期**: 2026-04-12
> **当前版本**: v6.3.0

---

## 🎉 全部完成！

---

## 第零章：现状确认（v6.0.8 已验证完成项）

以下修改已验证真实存在，实施时**不要重复做**：

| 已完成 | 验证结果 |
|--------|---------|
| DVOL 字段映射 (current_dvol, z_score_7d) | ✅ |
| _parse_inst_name 返回 C/P | ✅ |
| _calc_pop 改用 Black-Scholes + _norm_cdf | ✅ |
| DeribitOptionsMonitor 单例 (_deribit_monitor_cache) | ✅ |
| CORS 中间件 | ✅ |
| 风险阈值常量配置化 (config.py 6个) | ✅ |
| requirements.txt | ✅ |
| threading.local() 连接管理 | ✅ |
| 分页加载 (PAGE_SIZE + contractPage) | ✅ |
| 排序后展开状态恢复 | ✅ |
| auto-refresh 改 loadDashboardData | ✅ |
| showAlert localStorage 检查 | ✅ |
| Date.UTC 时间解析 | ✅ |
| config.py L46 语法正常 | ✅ 确认无问题 |

---

## 第一章：核心架构改造（先做这个，后面所有新功能都依赖它）

### 1.1 拆分 main.py（2636行 → 多模块）

当前所有逻辑堆在 `dashboard/main.py` 一个文件里。不拆开，后面加新功能只会更乱。

**目标结构**：
```
dashboard/
├── main.py                  # 入口 + FastAPI app 创建 + 路由注册 (~200行)
├── routers/
│   ├── __init__.py
│   ├── scan.py              # POST /api/scan, POST /api/quick-scan
│   ├── data.py              # GET /api/latest, GET /api/stats, GET /api/export/csv
│   ├── charts.py            # GET /api/charts/* (apr, dvol, pcr, vol-surface)
│   ├── trades.py            # GET /api/trades/* (history, strike-distribution, wind-analysis)
│   ├── calculator.py        # POST /api/recovery-calculate, POST /api/calculator/roll
│   ├── risk.py              # GET /api/metrics/max-pain, GET /api/dvol-advice, GET /api/bottom-fishing/advice
│   ├── sandbox.py           # POST /api/sandbox/simulate
│   └── grid.py              # 【新增】所有网格策略相关端点
├── services/
│   ├── __init__.py
│   ├── scanner.py           # 核心扫描逻辑 (quick_scan, run_scan)
│   ├── spot_price.py        # get_spot_price() 统一入口 + 所有 fallback
│   ├── dvol_analyzer.py     # DVOL 分析、信号判定、建议生成
│   ├── risk_framework.py    # 风险等级判定 (NORMAL/NEAR_FLOOR/ADVERSE/PANIC)
│   ├── instrument.py        # _parse_inst_name() 唯一实现 + InstrumentInfo dataclass
│   ├── flow_classifier.py   # 大单流向分类 (flow_label)
│   ├── margin_calc.py       # 保证金计算 (PUT/CALL 两种模式)
│   └── grid_engine.py       # 【新增】网格策略引擎核心逻辑
├── models/
│   ├── __init__.py
│   ├── contracts.py         # Contract, ScanResult, LargeTrade Pydantic models
│   └── grid.py              # 【新增】GridLevel, GridRecommendation, GridScenario models
├── db/
│   ├── __init__.py
│   ├── connection.py        # get_db_connection() + 上下文管理器
│   ├── schema.py            # 表定义、CREATE TABLE 语句
│   └── maintenance.py       # 数据清理、VACUUM
└── config.py                # 保持不变（已经是独立的）
```

**拆分规则**：
- `main.py` 只做 app 创建、中间件注册、路由挂载，不写任何业务逻辑
- 每个 router 文件只做参数解析 + 调用 service + 返回响应
- service 文件做所有计算和外部 API 调用
- model 文件定义 Pydantic 模型，被 router 和 service 共用
- 不要一次全拆，按下面的顺序逐步迁移

**拆分顺序**（必须按此顺序，每步验证能跑再下一步）：

| 步骤 | 移动内容 | 从 → 到 | 状态 |
|------|---------|---------|------|
| 1 | `_parse_inst_name` + InstrumentInfo | main.py → services/instrument.py | ✅ 已完成 |
| 2 | `get_spot_price*` 系列函数 | main.py → services/spot_price.py | ✅ 已完成 |
| 3 | `get_db_connection` + 建表语句 | main.py → db/connection.py + db/schema.py | ✅ 已完成 |
| 4 | Pydantic 模型定义 | main.py → models/contracts.py | ✅ 已完成 |
| 5 | DVOL 相关函数 | main.py → services/dvol_analyzer.py | ✅ 已完成 |
| 6 | 风险框架函数 | main.py → services/risk_framework.py | ✅ 已完成 |
| 7 | 大单流向分类 | main.py → services/flow_classifier.py | ✅ 已完成 |
| 8 | 扫描核心逻辑 | main.py → services/scanner.py | ✅ 已完成 |
| 9 | 路由拆分 | main.py → routers/*.py | ✅ 已完成 |
| 10 | 剩余杂项整理 | main.py → 各自归属 | ✅ 已完成 |

### 1.6 数据库维护
- `db/maintenance.py`: ✅ 已完成

### 1.2 统一 instrument 解析（消除重复）

当前状态：
- `main.py: _parse_inst_name()` — 解析 instrument 名称
- `main.py: _estimate_delta()` — 标记为 DEPRECATED 但仍被调用
- `options_aggregator.py` 可能有自己的一套

**要求**：
- 全项目只保留 `services/instrument.py` 中的 `_parse_inst_name()`
- `_estimate_delta()` 删除（已被 API delta 替代）
- 所有文件 import from `services.instrument`

### 1.3 统一 spot price 获取

当前 4 个函数做同一件事：
- `get_spot_price_binance()`
- `get_spot_price_deribit()`
- `get_spot_price()` — 调用上面两个
- `_get_spot_from_scan()` — 从 DB 读

**要求**：
- 合并到 `services/spot_price.py`
- 统一入口：`get_spot_price(currency, source="auto")`
- source="auto" 时按优先级：DB缓存(5分钟内) → Deribit API → Binance API
- 添加 5 分钟本地缓存（避免同一请求内重复调 API）

### 1.4 统一 contract 字段名

当前 premium 字段有 `premium_usd`, `premium`, `mark_price` 多种写法。

**要求**：
- 内部统一使用一个字段名：`premium_usd`
- 从 API 获取时统一转换：`premium_usd = mark.get('mark_price') or mark.get('premium') or mark.get('premium_usd')`
- 前端适配保持向后兼容 `contract.premium || contract.premium_usd || 0`

### 1.5 清理静默异常

当前 11 处 `except: pass`，其中 3 处是 ValueError（可接受），8 处是 broad Exception。

**要求**：
- ValueError 的 3 处保持不变（合理的类型转换 fallback）
- 其余 8 处改为：
```python
except Exception as e:
    logger.warning(f"[函数名] 操作失败: {e}")
```
- 不要用 `print()`，用统一的 logging

### 1.6 数据库维护

**要求**：
- 启用 WAL 模式：`PRAGMA journal_mode=WAL` 在每次连接时执行
- 实现 `db/maintenance.py`：
  - `cleanup_old_data(days=90)` — 删除超过保留期的记录
  - `vacuum_db()` — SQLite VACUUM
- 在 FastAPI lifespan 启动时调用 cleanup（每天最多执行一次）

---

## 第二章：网格策略引擎（核心新功能）

### 2.1 数据模型 (`models/grid.py`)

```python
from dataclasses import dataclass
from typing import List, Optional
from enum import Enum

class GridDirection(Enum):
    PUT = "put"      # Sell Put 接货网格
    CALL = "call"    # Sell Call 备兑网格

class RecommendationLevel(Enum):
    BEST = 5     # ★★★★★ 首选
    GOOD = 4     # ★★★★ 优质
    OK = 3       # ★★★ 可选
    CAUTION = 2  # ★★ 需谨慎
    SKIP = 1     # ★ 不推荐

@dataclass
class GridLevel:
    """单个网格档位"""
    direction: GridDirection
    strike: float           # 行权价
    expiry: str             # 到期日 (如 "25APR25")
    dte: int                # 到期天数
    premium_usd: float      # 单张权利金
    apr: float              # 年化收益率 (%)
    distance_pct: float     # 距现价百分比 (%)
    iv: float               # 隐含波动率
    delta: float            # Delta 值
    oi: int                 # 持仓量
    volume: int             # 成交量
    liquidity_score: float  # 流动性评分
    recommendation: RecommendationLevel
    reason: str             # 推荐/不推荐的理由

@dataclass
class GridRecommendation:
    """完整网格推荐"""
    currency: str
    spot_price: float
    timestamp: str
    put_levels: List[GridLevel]     # Sell Put 档位
    call_levels: List[GridLevel]    # Sell Call 档位
    dvol_signal: str                # 波动率方向指导
    recommended_ratio: str          # 如 "6:4 PUT:CALL"
    total_potential_premium: float  # 全部档位的潜在权利金

@dataclass
class GridScenario:
    """情景模拟结果"""
    target_price: float
    put_results: list       # 每个 put 档位在该价格的盈亏
    call_results: list      # 每个 call 档位在该价格的盈亏
    spot_pnl: float         # BTC 现货浮盈/亏
    total_pnl: float        # 组合总盈亏
    vs_hold_pnl: float      # vs 纯持有的差异
```

### 2.2 网格推荐引擎 (`services/grid_engine.py`)

这是整个项目的核心新增功能。

#### 2.2.1 推荐算法

```python
def recommend_grid(
    currency: str = "BTC",
    spot_price: float = None,
    put_count: int = 5,        # 推荐几个 Put 档位
    call_count: int = 3,       # 推荐几个 Call 档位
    min_dte: int = 7,          # 最短到期天数
    max_dte: int = 45,         # 最长到期天数
    min_apr: float = 15.0,     # 最低 APR 门槛
    prefer_short_dte: bool = True  # 偏好短期（Theta 更优）
) -> GridRecommendation:
```

**评分公式**（核心，必须严格按此实现）：

```
score = apr_score * 0.35 + safety_score * 0.30 + liquidity_score * 0.20 + theta_score * 0.15

其中：
- apr_score = min(apr / 100, 1.0)  # 归一化到 0-1
- safety_score = 1.0 - min(abs(distance_pct) / 15.0, 1.0)  # 距离越远越安全，15%为满分
- liquidity_score = min(oi / 500 + volume / 100, 1.0) / 2  # OI和Volume各占一半
- theta_score = theta_decay_factor(dte)  # 14-21天最优，太短权利金少，太长Theta慢
```

**档位间距规则**：
- Put 网格：从 ATM 往下，按 score 排序选取 top N，确保相邻档位 strike 间距 > 2%
- Call 网格：从 ATM 往上，同理
- 如果候选不足，降低 min_apr 重试一次

**推荐等级映射**：
```
score >= 0.75 → BEST (★★★★★)
score >= 0.60 → GOOD (★★★★)
score >= 0.45 → OK (★★★)
score >= 0.30 → CAUTION (★★)
score <  0.30 → SKIP (★)
```

**reason 字段生成**（给用户看的中文解释）：
- APR 高于 50%: "高收益"
- 距离 > 10%: "安全距离充足"
- OI < 100: "流动性不足"
- DTE < 10: "短期，Theta 加速"
- DTE > 30: "长期，权利金较高"
- IV > 70%: "高波动率环境，权利金丰厚"

#### 2.2.2 波动率方向指导

```python
def get_vol_direction_signal(currency: str = "BTC") -> dict:
    返回：
    {
        "dvol_current": 58.2,
        "dvol_30d_avg": 52.1,
        "dvol_percentile": 72,      # 当前DVOL在30日中的分位数
        "skew": {
            "put_iv_avg": 64.5,     # Put端平均IV
            "call_iv_avg": 61.3,    # Call端平均IV
            "skew_pct": 5.2,        # 正值=Put端更贵
            "interpretation": "市场偏恐惧，Put端溢价"
        },
        "signal": "FAVOR_PUT",      # FAVOR_PUT / FAVOR_CALL / NEUTRAL
        "reason": "DVOL高于30日均值11.7%，Put端IV溢价5.2%，建议偏重Sell Put收租",
        "suggested_ratio": "6:4"    # PUT:CALL 建议比例
    }
```

**信号判定逻辑**：
- `dvol_percentile > 70 AND skew_pct > 3` → FAVOR_PUT（恐慌时卖Put收高价）
- `dvol_percentile < 30 AND skew_pct < -3` → FAVOR_CALL（平静时卖Call收溢价）
- 其他情况 → NEUTRAL

#### 2.2.3 新增 API 端点 (`routers/grid.py`)

```
GET  /api/grid/recommend?currency=BTC&put_count=5&call_count=3
     → 返回 GridRecommendation

GET  /api/grid/vol-direction?currency=BTC
     → 返回波动率方向信号

POST /api/grid/scenario
     → body: {"currency": "BTC", "grid_levels": [...], "target_prices": [70000, 75000, 80000, 85000, 90000]}
     → 返回每个目标价格的盈亏模拟

GET  /api/grid/revenue-summary?currency=BTC&days=30
     → 返回收益汇总（累计权利金、年化率、vs纯持有对比）

GET  /api/grid/risk-heatmap?currency=BTC
     → 返回所有档位的实时风险状态
```

---

## 第三章：前端改造（服务于网格策略）

### 3.1 新增 "网格策略" Tab

在现有导航中新增一个 Tab，专门展示网格策略功能。

**页面布局**：

```
┌─────────────────────────────────────────────────────────┐
│  [扫描] [监控] [图表] [计算] [网格策略★] [设置]         │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─ 波动率方向指示 ─────────────────────────────────┐   │
│  │ DVOL: 58.2 ▲ | 分位: 72% | 信号: FAVOR_PUT     │   │
│  │ 建议 PUT:CALL = 6:4                             │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─ Sell Put 网格 ───────────┐ ┌─ Sell Call 罀格 ─────┐ │
│  │ 档位 行权价 DTE APR 推荐  │ │ 档位 行权价 DTE APR  │ │
│  │  1   $80K  13d 58% ★★★★★ │ │  1   $87K  13d 48% ★★★★★│ │
│  │  2   $76K  13d 36% ★★★★  │ │  2   $90K  20d 28% ★★★★ │ │
│  │  3   $72K  20d 32% ★★★   │ │  3   $95K  27d 15% ★★★  │ │
│  └───────────────────────────┘ └───────────────────────┘ │
│                                                         │
│  ┌─ 风险热力图 ─────────────────────────────────────┐   │
│  │ $70K ■■■  $72K ■■■  $76K ■■  $80K ■             │   │
│  │                    $83,500 ←                      │   │
│  │         ■ $87K    ■■ $90K   ■■■ $95K             │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─ 情景模拟 ───────────────────────────────────────┐   │
│  │ BTC → $75K: 组合盈亏 -$7,620 (vs持有 -$8,500)    │   │
│  │ BTC → $90K: 组合盈亏 +$12,800 (vs持有 +$6,500)   │   │
│  │ [滑块: $60K ─────●───── $100K]                   │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─ 收益仪表盘 ─────────────────────────────────────┐   │
│  │ 本月权利金: +$1,680 | 年化: 32% | vs持有: +60%   │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 3.2 改造主合约表格

**当前问题**：21列太多，移动端不可用。

**要求**：
- 默认显示 8 列核心数据：Symbol | 方向 | 行权价 | DTE | 权利金 | APR | Delta | 距离%
- 右上角添加"列显示/隐藏"按钮，用户自定义
- 移动端自动折叠为卡片布局（每个合约一张卡片）
- 添加 `max-height` + 内部滚动

### 3.3 新增图表

1. **收益累计图** — 折线图，显示累计权利金收入随时间增长
2. **网格档位分布图** — 在价格轴上标注所有档位的行权价
3. **IV 偏度图** — 显示 Put/Call 两端 IV 的差异

### 3.4 通知增强

- 大单成交时播放声音（可开关）
- 价格接近网格档位 3% 时推送通知
- 添加通知历史面板（可回看）

---

## 第四章：代码质量修复（收尾）

### 4.1 STRATEGY_PRESETS 统一

当前状态：`options_aggregator.py` 4次 + `main.py` 1次 + `app.js` 2次 = 3套定义。

**要求**：
- 后端只在 `config.py` 定义一套
- `options_aggregator.py` 和 `main.py` 都 import from config
- `app.js` 通过 API `/api/config/presets` 获取（不在前端硬编码）

### 4.2 静默异常处理（剩余8处）

上文已述，改为 logger.warning。

### 4.3 输入验证增强

```python
# 所有 GET 端点的参数都要验证
@app.get("/api/charts/apr")
async def get_apr_chart(
    currency: str = Query("BTC", regex="^(BTC|ETH|SOL)$"),
    hours: int = Query(168, ge=1, le=720),
):
```

### 4.4 安全加固

- 添加简单的 API Key 认证（环境变量 `API_KEY`，header 中传 `X-API-Key`）
- 如果环境变量未设置则跳过认证（方便本地开发）
- 不需要做复杂 OAuth，一个 header 检查就够

---

## 第五章：实施顺序（必须严格按此执行）

| 阶段 | 内容 | 预估时间 | 前置条件 |
|------|------|---------|---------|
| **Phase 1** | 1.1 拆分 main.py（按步骤1-9） | 2天 | 无 |
| **Phase 2** | 1.2-1.6 架构清理 | 1天 | Phase 1 完成 |
| **Phase 3** | 2.2 网格引擎 + API 端点 | 2天 | Phase 2 完成 |
| **Phase 4** | 3.1-3.3 前端网格策略 Tab | 2天 | Phase 3 完成 |
| **Phase 5** | 3.4 通知 + 4.x 代码质量 | 1天 | Phase 4 完成 |
| **Phase 6** | 全面测试 + bug 修复 | 1天 | Phase 5 完成 |

**总计预估：9天**

---

## 第六章：验证清单（每个 Phase 完成后必须通过）

### Phase 1 验证
- [ ] `main.py` ≤ 300 行
- [ ] 所有原有 API 端点正常响应（用 curl 逐个测试）
- [ ] `python -c "from dashboard.main import app"` 无报错
- [ ] 没有循环 import

### Phase 2 验证
- [x] 全项目 `_parse_inst_name` 只有一处定义 ✅
- [x] `get_spot_price()` 统一入口工作正常 ✅
- [x] SQLite WAL 模式启用 ✅
- [ ] 全项目 0 处 broad `except: pass`（ValueError 除外）

### Phase 3 验证
- [x] `GET /api/grid/recommend` 返回正确格式的网格推荐 ✅
- [ ] 评分公式各权重正确实现
- [x] 波动率方向信号逻辑正确 ✅
- [ ] 情景模拟计算准确（与手动计算对比）
- [x] 空数据时优雅降级（返回空列表而非 500）✅

### Phase 4 验证
- [x] 网格策略 Tab 正确显示 ✅
- [x] Put/Call 表格数据与 API 一致 ✅
- [x] 风险热力图颜色编码正确 ✅
- [ ] 情景模拟滑块交互流畅
- [ ] 移动端基本可用（不求完美）

### Phase 5 验证
- [x] 列显示/隐藏功能正常 ✅
- [ ] 声音通知可开关
- [x] 输入验证拒绝非法参数（返回 422）✅
- [x] API Key 认证工作正常 ✅

### Phase 6 验证
- [ ] 全流程手动测试（打开浏览器完整操作一遍）
- [ ] 无 JavaScript console 报错
- [ ] 无 Python traceback
- [ ] 性能可接受（页面加载 < 3秒，API 响应 < 2秒）

---

## 附录 A：关键代码片段（供参考，非直接复制）

### A.1 评分公式实现

```python
def _calc_grid_score(
    apr: float,
    distance_pct: float,
    oi: int,
    volume: int,
    dte: int
) -> float:
    apr_score = min(apr / 100.0, 1.0)
    safety_score = 1.0 - min(abs(distance_pct) / 15.0, 1.0)
    liquidity_score = min((oi / 500.0 + volume / 100.0), 1.0) / 2.0
    
    # Theta: 14-21天最优区间
    if 14 <= dte <= 21:
        theta_score = 1.0
    elif dte < 14:
        theta_score = 0.5 + (dte / 14.0) * 0.5
    else:
        theta_score = max(0.3, 1.0 - (dte - 21) / 60.0)
    
    return apr_score * 0.35 + safety_score * 0.30 + liquidity_score * 0.20 + theta_score * 0.15
```

### A.2 波动率方向判定

```python
def _determine_vol_signal(dvol_percentile: float, skew_pct: float) -> str:
    if dvol_percentile > 70 and skew_pct > 3:
        return "FAVOR_PUT"
    elif dvol_percentile < 30 and skew_pct < -3:
        return "FAVOR_CALL"
    else:
        return "NEUTRAL"
```

### A.3 情景模拟核心

```python
def simulate_scenario(
    grid_levels: List[GridLevel],
    spot_price: float,
    btc_quantity: float,
    target_price: float
) -> GridScenario:
    spot_pnl = (target_price - spot_price) * btc_quantity
    
    put_results = []
    for level in grid_levels:
        if level.direction == GridDirection.PUT:
            if target_price <= level.strike:
                # 被行权：以 strike 买入，浮亏 = (target - strike) * qty
                pnl = (target_price - level.strike) + level.premium_usd
            else:
                pnl = level.premium_usd  # 安全到期，纯收权利金
            put_results.append({"strike": level.strike, "pnl": pnl})
    
    call_results = []
    for level in grid_levels:
        if level.direction == GridDirection.CALL:
            if target_price >= level.strike:
                # 被行权：以 strike 卖出
                pnl = (level.strike - target_price) + level.premium_usd
            else:
                pnl = level.premium_usd
            call_results.append({"strike": level.strike, "pnl": pnl})
    
    total_pnl = spot_pnl + sum(r["pnl"] for r in put_results + call_results)
    vs_hold = total_pnl - spot_pnl
    
    return GridScenario(target_price, put_results, call_results, spot_pnl, total_pnl, vs_hold)
```

---

## 附录 B：不要做的事（防止过度工程）

| ❌ 不要做 | 理由 |
|----------|------|
| 换前端框架（React/Vue） | 丸总没要求，原生JS够用 |
| 加 TypeScript | 过度工程 |
| 做用户登录系统 | 单用户工具不需要 |
| 接 WebSocket 实时推送 | 当前 polling 够用 |
| 支持 10+ 币种 | 先做好 BTC，ETH 复制即可 |
| 做回测系统 | 复杂度太高，后期再说 |
| 加 Docker 部署 | 丸总本地跑就行 |

---

*文档结束。执行 AI 请严格按 Phase 顺序实施，每 Phase 完成后跑验证清单。*
