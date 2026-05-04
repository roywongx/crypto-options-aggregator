# 统一投资推荐系统 — 设计规格

> **日期**: 2026-05-05 | **状态**: 已确认
> **范围**: 仪表盘 17 个板块全覆盖 | 16 个有效面板（统计面板跳过）

## 1. 目标

为仪表盘所有板块增加两层分析能力：
1. **规则推荐（自动）**：基于金融知识的确定性规则引擎，输出信号灯 + 多因子评分报告
2. **LLM 深度分析（用户手动触发）**：复用 LLMAnalystEngine 的辩论流水线，提供完整分析叙事

## 2. 架构

```
数据层（各面板现有数据源）
        ↓
UnifiedRecommendationEngine（统一编排）
  ├─ PanelConfig 注册表 → 路由到对应规则集
  ├─ 规则函数（纯函数，score + verdict + reasoning）
  ├─ SignalCalculator（加权/最差/多数 三种聚合公式）
  └─ ReportBuilder → 标准化三层输出
        ↓
  ┌─────┴─────┐
  ↓           ↓
规则报告     LLM分析（可选）
（自动）      LLMAnalystEngine
               ├─ 合成
               ├─ 多头辩论
               ├─ 空头辩论
               └─ 审判+审计
```

**核心原则**：不改动现有 19 个规则引擎，新建 `UnifiedRecommendationEngine` 作为统一包装层。

## 3. 新增文件

| 文件 | 行数 | 职责 |
|---|---|---|
| `services/unified_recommendation_engine.py` | ~400 | 编排核心 + SignalCalculator + ReportBuilder + LLMPromptBuilder |
| `services/panel_analyzers.py` | ~600 | 16个面板的规则配置 + 规则函数 + LLM prompt 模板 |
| `api/recommendations.py` | ~200 | 4个API端点 |
| `static/recommendations.js` | ~300 | 前端渲染器 + LLM抽屉组件 + 顶部汇总条 |

## 4. 修改文件

| 文件 | 改动 | 行数 |
|---|---|---|
| `main.py` | 注册 recommendations 路由 | +2 |
| `static/app.js` | 16个面板注入信号灯渲染调用 | +~100 |
| `static/index.html` | LLM抽屉HTML + 顶部汇总条 | +~50 |
| `db/schema.py` | llm_analysis_cache + llm_usage_log 表 | +20 |
| `config.py` | LLM 开关、缓存 TTL | +10 |

## 5. 统一输出格式

所有面板输出完全一致的三层结构：

### 层级1：信号灯（自动，页面加载即显示）

```json
{
  "signal": "bullish",
  "signal_emoji": "🟢",
  "signal_text": "看多波动率",
  "confidence": 78
}
```

四种信号：🟢 bullish | 🔴 bearish | 🟡 neutral | ⚠️ caution

### 层级2：规则分析报告（点击展开）

```json
{
  "report": {
    "summary": "...综合判断...",
    "factors": [
      {"name": "期限溢价", "score": 85, "max": 100, "verdict": "陡峭Contango，有利于卖方"}
    ],
    "logic_chain": ["1. ...", "2. ...", "3. ...", "4. 综合 → 推荐..."],
    "suggested_action": "卖出2W DTE 5% OTM PUT",
    "risk_flags": ["FOMC下周召开，建议周三前减仓"],
    "refs": ["参照3月类似结构，该策略胜率82%"]
  }
}
```

### 层级3：LLM 深度分析（用户触发，抽屉弹窗）

```json
{
  "llm_analysis": {
    "synthesis": "...",
    "bull_debate": {"argument": "...", "score": 7.5},
    "bear_debate": {"argument": "...", "score": 6.2},
    "judge_verdict": "...",
    "audit": {"hallucination_score": 0.02, "data_citations": [...]},
    "model_used": "claude-sonnet-4-6",
    "tokens": {"input": 2847, "output": 1523}
  }
}
```

### 完整响应

```json
{
  "panel_id": "iv_term_structure",
  "timestamp": "2026-05-05T12:00:00Z",
  "data_snapshot": {...},
  "signal": {...},
  "report": {...},
  "llm_analysis": null,
  "meta": {"rules_version": "1.0", "computation_ms": 12}
}
```

## 6. 面板配置

每个面板定义：`data_sources → rules → signal_formula → llm_prompt_template`

### 规则函数签名

```python
def rule_fn(data: dict, cache: dict) -> RuleResult:
    return RuleResult(
        name="factor_name",
        score=85,       # 0-100
        max=100,
        verdict="正面解读",
        reasoning=["推理链1", "推理链2"]
    )
```

### 信号聚合公式

- `weighted_score` — 加权平均评分 → 映射到信号灯
- `worst_case` — 取最差评分（风险类面板用）
- `majority` — 多数规则倾向（行情判断类用）

### 16 面板清单

| 面板ID | 规则来源 | 新建/包装 | 复杂度 |
|---|---|---|---|
| metric_cards | DVOL + 恐贪 + 趋势 | 新建 | ⭐ |
| risk_command_center | RiskFramework + UnifiedRisk | 包装 | ⭐⭐ |
| strategy_center | UnifiedStrategyEngine | 包装 | ⭐⭐ |
| greeks_matrix | GreeksAnalyzer + MarketState | 包装 | ⭐⭐ |
| ai_analyst_center | LLMAnalystEngine | 包装+接入路由 | ⭐⭐⭐ |
| iv_term_structure | 期限溢价 + 日历价差 | 新建 | ⭐⭐ |
| iv_smile | 偏度 + 峰度 + 微笑形态 | 新建 | ⭐⭐ |
| dvol_trend | DVOL区间 + 均值回归 | 新建 | ⭐ |
| pcr_chart | PCR方向 + 极端值检测 | 新建 | ⭐ |
| max_pain | MaxPain磁吸 + GammaFlip | 包装 | ⭐⭐ |
| large_trades | 大单方向 + 聪明钱 | 新建 | ⭐ |
| martingale_sandbox | 补仓风险 + 盈亏概率 | 包装 | ⭐⭐ |
| opportunities_table | 质量评分 + 风险过滤 | 包装 | ⭐ |
| gex_chart | GEX + 伽马暴露解读 | 包装 | ⭐⭐ |
| money_flow | 资金流向 + 主动买卖比 | 包装 | ⭐⭐ |
| onchain_metrics | MVRV + 均衡价格 | 包装 | ⭐ |

统计面板（stats_panel）跳过 — 纯运维数据无投资含义。

## 7. 前端交互

### 混合展示方案（三层信息递进）

1. **信号灯**：卡片右上角色标，页面加载后自动渲染
2. **规则报告**：点击信号灯或卡片展开，内嵌显示，不打断浏览
3. **LLM 抽屉**：规则报告底部"LLM深度分析"按钮 → 右侧全屏抽屉打开 → SSE 流式展示辩论过程

### 新增 UI 组件

- `RecommendationRenderer` — 信号灯 + 展开规则报告
- `LLMDrawer` — 全屏抽屉（Tab切换：合成/多头/空头/最终判决）
- `SummaryBar` — 顶部全板块信号汇总条（16个信号灯横向排列）

## 8. API 端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/recommendation/{panel_id}` | GET | 单个面板规则推荐，`?currency=BTC` |
| `/api/recommendation/{panel_id}/llm` | POST | 触发LLM分析，body:`{currency, force_refresh}` → SSE流 |
| `/api/recommendations/summary` | GET | 全板块信号汇总（顶部条用） |
| `/api/recommendations/batch` | POST | 批量获取，body:`{panels:[], currency}` |

## 9. 数据库

### llm_analysis_cache

```sql
CREATE TABLE IF NOT EXISTS llm_analysis_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    panel_id TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'BTC',
    input_hash TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    model_used TEXT,
    tokens_input INTEGER,
    tokens_output INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(panel_id, currency, input_hash)
);
```

input_hash = MD5(规则报告 + 数据快照)，相同输入不重复调 LLM。

### llm_usage_log

```sql
CREATE TABLE IF NOT EXISTS llm_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    panel_id TEXT NOT NULL,
    model TEXT,
    tokens_input INTEGER,
    tokens_output INTEGER,
    latency_ms INTEGER,
    cost_estimate REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## 10. LLM 配置 (config.py)

```python
LLM_ANALYSIS_ENABLED = True      # 全局开关
LLM_CACHE_TTL_SECONDS = 3600     # 缓存1小时
LLM_MAX_TOKENS_PER_PANEL = 4000  # 每面板token上限
LLM_DEFAULT_MODEL = "claude-sonnet-4-6"
LLM_FALLBACK_CHAIN = ["claude-haiku-4-5", "gpt-4o-mini"]
LLM_STREAMING_ENABLED = True     # SSE流式输出
```

## 11. 实现顺序

### Phase 1：基础设施
1. `UnifiedRecommendationEngine` 核心编排
2. `api/recommendations.py` 路由 + `main.py` 注册
3. `db/schema.py` 新表
4. `static/recommendations.js` 渲染器
5. AI 分析中心路由接入（已有 LLM 引擎）

### Phase 2：面板覆盖
6. 6 个缺规则面板：IV期限结构、IV Smile、DVOL趋势、PCR、大单追踪、指标卡
7. 8 个包装引擎面板：风险中心、策略中心、Greeks、MaxPain 等
8. 每个面板配 LLM prompt 模板
9. 前端 16 个面板注入信号灯组件
