# AI 研判中心设计

## 概述

将现有 debate 区域 + copilot 浮窗替换为统一的"AI 研判中心"。保留现有 5 个规则 agent，在其上叠加 LLM 综合分析、多空辩论、数据异常检测三层能力。

**目标：** LLM 能看到全量原始数据，既做综合研判，又做数据质量审计。

**架构：** 规则引擎(5 agent) → LLM 综合分析师 → LLM 多空辩论 → LLM 异常检测 → 前端展示

---

## 1. 后端架构

### 1.1 新文件：`services/llm_analyst.py`

**LLMAnalystEngine 类：**

```python
class LLMAnalystEngine:
    async def run_full_analysis(self, currency: str, mode: str = "full") -> LLMAnalysisResult:
        """一键全流程分析"""
        context = await self._prepare_context(currency)
        rule_reports = self._run_rule_engine(currency)
        synthesis = await self._llm_synthesize(context, rule_reports)
        debate = await self._llm_debate(context, synthesis) if mode == "full" else None
        audit = await self._llm_audit(context, rule_reports, synthesis)
        return LLMAnalysisResult(...)
```

**数据准备 `_prepare_context(currency)`：**

收集全量数据并组装为结构化 JSON，供所有 LLM 调用使用：

| 数据源 | 来源 | 字段 |
|--------|------|------|
| 现货价格 | `services.spot_price` | spot |
| DVOL | `services.dvol_analyzer` | current, z_score, percentile, signal, trend |
| 期权合约 | `scan_records` DB | 全量合约数据（strike, premium, IV, delta, OI, spread, APR） |
| 大单 | `services.large_trades_fetcher` | 最近 30 笔大单明细 |
| 链上指标 | `services.onchain_metrics` | MVRV, NUPL, Mayer, Puell, convergence_score |
| 宏观数据 | `services.macro_data` | fear_greed, funding_rate, qqq_spy, risk_off |
| 衍生品 | `services.derivative_metrics` | sharpe_7d, sharpe_30d, vol_ratio, overheating |
| 风险框架 | `services.risk_framework` | status, floors, VaR |
| IV 期限结构 | `services.iv_term_structure` | state, slope, curvature, vrp |
| 最大痛点 | `max_pain_history` DB | max_pain_price |
| 策略引擎 | `services.strategy_engine` | filter_summary, recommendations |

**4 次 LLM 调用：**

| 调用 | 角色 | 输入 | 输出 |
|------|------|------|------|
| 综合分析师 | 资深期权策略师 | 全量数据 + 5 份规则报告 | market_assessment, strategy_recommendation, risk_warning, confidence |
| Bull Agent | 看多分析师 | 全量数据 + 综合报告 | bullish_case, key_drivers, target_scenarios, confidence |
| Bear Agent | 看空分析师 | 全量数据 + 综合报告 | bearish_case, key_risks, downside_scenarios, confidence |
| 审计 Agent | 数据质量审计师 | 全量原始数据 + 规则中间结果 + 策略推荐 | anomalies[], logic_issues[], data_quality_score |

**LLM 调用方式：**

复用 `services/ai_router.py` 的 `ai_chat_with_config()` 函数，使用用户配置的 LLM provider。preset 使用 "analysis"（claude-sonnet → gemini → deepseek fallback）。

**异常检测 Prompt 设计：**

```
你是数据质量审计师。审查以下加密货币期权分析数据，找出异常。

检查维度：
1. 数据源间一致性：DVOL vs IV、链上信号 vs 衍生品信号、价格 vs 成交量
2. 计算逻辑合理性：APR 是否异常（>200%?）、胜率是否合理（>95%?）、spread 是否正常
3. 数据完整性：是否有缺失字段、数据是否过期（timestamp > 1小时?）
4. 前端展示一致性：策略引擎输出 vs 原始数据是否匹配

输出 JSON：
{
  "anomalies": [
    {"severity": "critical|warning|info", "source": "数据源名", "description": "描述", "suggestion": "建议"}
  ],
  "logic_issues": [
    {"severity": "...", "component": "模块名", "description": "...", "suggestion": "..."}
  ],
  "data_quality_score": 0-100
}
```

### 1.2 数据结构

```python
@dataclass
class LLMAnalysisResult:
    success: bool
    currency: str
    timestamp: str
    rule_reports: Dict[str, Any]       # 5 个规则 agent 报告
    synthesis: Dict[str, Any]          # LLM 综合分析
    debate: Optional[Dict[str, Any]]   # LLM 多空辩论（quick 模式为空）
    audit: Dict[str, Any]              # LLM 异常检测
    llm_config: Dict[str, Any]         # LLM 调用元信息
```

---

## 2. API 设计

### 2.1 新文件：`api/llm_analyst.py`

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/llm-analyst/analyze` | POST | 全流程分析（规则→综合→辩论→审计） |
| `/api/llm-analyst/quick` | POST | 快速模式（跳过辩论） |
| `/api/llm-analyst/history` | GET | 历史分析结果 |

**POST /api/llm-analyst/analyze 请求：**
```json
{"currency": "BTC", "mode": "full"}
```

**响应包含：**
- `rule_reports`：5 个规则 agent 的结构化报告
- `synthesis`：LLM 综合分析（market_assessment, strategy_recommendation, risk_warning, confidence）
- `debate`：LLM 多空辩论（bull_case, bear_case, judge_verdict, winner, 各方 confidence）
- `audit`：数据审计（anomalies[], logic_issues[], data_quality_score）
- `llm_config`：模型信息、调用次数、token 消耗、延迟

### 2.2 LLM 配置端点

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/llm-analyst/config` | GET | 获取当前 LLM 配置 |
| `/api/llm-analyst/config` | POST | 保存 LLM 配置（API key, base URL, model） |
| `/api/llm-analyst/test` | POST | 测试 LLM 连接 |

配置存储在后端（SQLite `llm_config` 表），不再使用 localStorage。

---

## 3. 前端设计

### 3.1 布局

替换 debate section + copilot 浮窗，新建"AI 研判中心"section：

- **Header**：标题 + 币种选择 + 开始分析/快速分析按钮 + 模型状态指示
- **综合研判卡片**：市场评估 + 策略建议 + 风险提示 + 信心条
- **多空辩论卡片**：Bull Case | Bear Case | 裁决（三列布局）
- **数据审计卡片**：质量分数条 + 异常/问题列表（黄色 warning / 红色 critical）
- **规则 Agent 详情**：可折叠，5 个 agent 卡片（复用现有 debate agent 卡片样式）
- **LLM 配置面板**：内嵌在 section 底部（API Key, Base URL, Model, 测试连接）

### 3.2 交互流程

1. 用户选择币种 → 点击"开始分析"
2. 显示进度指示：4 个阶段（规则分析 → 综合研判 → 多空辩论 → 数据审计）
3. 每个阶段完成后实时渲染（SSE 或轮询）
4. 异常检测结果用警告条展示（critical 红色 / warning 黄色 / info 蓝色）
5. Agent 卡片可展开查看详细报告

### 3.3 移除旧代码

- 删除 `#copilotToggle`, `#copilotChat` 浮窗 HTML + JS
- 删除 AI Settings 模态框 HTML + JS
- 删除旧 debate section HTML + JS
- 删除 `api/copilot.py`（功能迁移到 `api/llm_analyst.py`）

### 3.4 LLM 配置

- 配置存储在后端 SQLite，不再用 localStorage
- 前端提供配置面板（内嵌在研判中心底部）
- 支持"测试连接"功能（发送简单 prompt 验证 API key 有效）
- 模型列表复用现有 18 个预设模型

---

## 4. 数据流

```
用户点击"开始分析"
    ↓
POST /api/llm-analyst/analyze {currency: "BTC"}
    ↓
_prepare_context() → 收集全量数据
    ↓
_run_rule_engine() → 5 份规则报告（已有 options_debate_engine）
    ↓
_llm_synthesize(context, reports) → 综合分析报告
    ↓
_llm_debate(context, synthesis) → Bull/Bear 辩论 + 裁决
    ↓
_llm_audit(context, reports, synthesis) → 异常检测报告
    ↓
返回完整结果 → 前端渲染
```

---

## 5. 错误处理

- LLM API 不可用 → 跳过 LLM 层，只返回规则引擎结果 + 警告
- LLM 超时（30 秒/次） → 返回已完成的部分结果
- LLM 返回格式错误 → 解析失败时返回原始文本 + 警告
- 数据源缺失 → 标记为 audit anomaly，不阻断流程

---

## 6. 测试策略

### 后端测试
- `test_llm_analyst.py`：context 准备、prompt 组装、响应解析、错误处理
- Mock LLM 调用，验证 prompt 内容和响应处理逻辑

### 前端测试
- 进度条渲染
- 各阶段结果展示
- 异常警告样式
- LLM 配置保存/加载
