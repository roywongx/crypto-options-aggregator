# 风险指挥中心重设计

## 概述

将现有"BTC 风险中枢"全面重设计为"风险指挥中心"。修复审计发现的数学错误，升级前端可视化，整合 LLM 智能研判。

**目标：** 保持全量数据获取能力，修复数学 bug，升级为仪表盘+雷达图布局，底部增加 LLM 研判报告。

**架构：** 后端服务修复数学错误 → API 聚合层优化 → 前端 Gauge+Radar+Tab 布局 → LLM 研判端点

---

## 1. 后端数学修复

### 1.1 HIGH 级修复

**Volga 公式 (`services/pressure_test.py:81`):**

```python
# 当前（错误）
volga = vega * math.sqrt(T) * d1 * d2 / sigma / 100
# 修复
volga = vega * d1 * d2 / sigma
```

推导: Vega = S * N'(d1) * sqrt(T)。Volga = dVega/dsigma = Vega * d1 * d2 / sigma。

**POP 计算 (`services/dvol_analyzer.py:191`):**

```python
# 当前（错误）— 用 N(d1)
pop = 1 - nd1 if delta_val > 0 else nd1
# 修复 — 用 N(d2)
d2 = d1 - iv_decimal * math.sqrt(dte_years)
nd2 = norm_cdf(d2)
if option_type.upper() == "CALL":
    pop = 1 - nd2  # P(S_T > K) = 1 - N(d2)
else:
    pop = nd2       # P(S_T < K) = N(d2)
```

**压力测试参数 (`api/risk.py:138`):**

```python
# 当前（硬编码）
PressureTestEngine.stress_test(S=spot, K=spot, T=30/365, r=0.05, sigma=0.5, option_type="C")
# 修复 — 使用实际 DVOL，改为期权类型 P
sigma = dvol_data.get("current", 50) / 100
PressureTestEngine.stress_test(S=spot, K=spot, T=30/365, r=0.05, sigma=sigma, option_type="P")
```

**支撑位权重 (`services/support_calculator.py:142`):**

```python
# 当前（等权，与注释不符）
supports = [ma200, fib_levels.get("0.382", 50000), on_chain]
return sum(supports) / len(supports)
# 修复 — 链上数据权重 50%
weights = [0.25, 0.25, 0.50]
return sum(s * w for s, w in zip(supports, weights))
```

### 1.2 MEDIUM 级修复

| 文件 | 问题 | 修复 |
|------|------|------|
| `onchain_metrics.py:173,197,221` | f-string 缺失 | 改为 `f"...{e}"` |
| `onchain_metrics.py:274` | Puell 硬编码区块奖励 3.125 | 从 blockchain.info 获取区块高度，动态计算当前 epoch 奖励 |
| `derivative_metrics.py:94` | 7 天 Sharpe 仅 7 个数据点 | 改为 14 天窗口（14 个数据点） |
| `dvol_analyzer.py:106` | Z-Score 用总体标准差 (N) | 改为样本标准差 (N-1, Bessel 校正) |
| `unified_risk_assessor.py:131` | 情绪分数可能为 0 | 加 `max(0, score)` floor 保护 |
| `ai_sentiment.py:448` | Gamma 影响估算系数 0.1 无依据 | 用 BS Gamma 公式替代 |
| `support_calculator.py:142` | 注释与代码不一致 | 更新注释匹配实际权重 |
| `ai_sentiment.py:361` | `"B" in side` 可能误判 "BLOCK" | 改为 `side.upper() in ("BUY", "B")` |

### 1.3 LOW 级清理

- `risk.py:48` — 删除 `mm_signal` 死代码字段
- `risk_framework.py:66` — 边界条件 `>` 改为 `>=`
- 多处 `verify=False` — 保留但添加注释说明原因

---

## 2. LLM 研判端点

### 2.1 新增端点

`GET /api/risk/llm-insight?currency=BTC`

### 2.2 实现

在 `api/risk.py` 中新增函数：

```python
async def get_llm_risk_insight(currency: str) -> Dict:
    from services.llm_analyst import LLMAnalystEngine
    from services.ai_router import ai_chat_with_config

    # 1. 获取全量风险数据
    risk_data = await run_in_threadpool(get_risk_overview_sync, currency)

    # 2. 组装 prompt
    prompt = f"""你是加密货币风险分析师。基于以下风险数据，给出分析。

数据：
{json.dumps(risk_data, ensure_ascii=False, indent=2)}

输出 JSON：
{{
  "narrative": "200字以内的风险总评",
  "anomalies": ["异常1", "异常2"],
  "recommendations": ["建议1", "建议2"],
  "confidence": 0-100
}}"""

    # 3. 调用 LLM
    custom_config = LLMAnalystEngine()._get_custom_config()
    response = ai_chat_with_config(
        [{"role": "user", "content": prompt}],
        preset="analysis", temperature=0.3, max_tokens=1500,
        custom_config=custom_config
    )

    # 4. 解析返回
    parsed = LLMAnalystEngine()._parse_json_response(response)
    return parsed or {"narrative": response, "anomalies": [], "recommendations": [], "confidence": 50}
```

### 2.3 错误处理

- LLM 不可用 → 返回 `{"narrative": "LLM 服务未配置", "anomalies": [], "recommendations": [], "confidence": 0}`
- LLM 超时 → 返回已获取的风险数据摘要 + 警告
- JSON 解析失败 → 返回原始文本作为 narrative

---

## 3. 前端视觉重设计

### 3.1 布局结构

替换现有 `riskDashboard` section（index.html 151-554 行），新布局：

```
┌─────────────────────────────────────────────────┐
│  Header: 标题 + 状态 Badge + 支撑位              │
├──────────────────────┬──────────────────────────┤
│  Gauge 仪表盘        │  6 个关键指标卡片          │
│  (综合评分 0-100)     │  2x3 网格                 │
├──────────────────────┴──────────────────────────┤
│  雷达图 (4 维风险)                                │
├─────────────────────────────────────────────────┤
│  Tab: [链上指标] [衍生品] [压力测试] [AI情绪]      │
├─────────────────────────────────────────────────┤
│  Tab 内容区                                       │
├─────────────────────────────────────────────────┤
│  LLM 智能研判面板                                 │
└─────────────────────────────────────────────────┘
```

### 3.2 Gauge 仪表盘

Chart.js doughnut chart 实现半圆仪表盘：
- 数据集: `[score, 100-score]`，背景色根据分数区间变化
- 0-30 绿色 `#10b981`，30-60 黄色 `#eab308`，60-80 橙色 `#f97316`，80-100 红色 `#ef4444`
- 中间用 Chart.js plugin 显示数字 + 状态文字
- 旋转: `rotation: -90, circumference: 180`（半圆）

### 3.3 雷达图

Chart.js radar chart：
- 4 个轴: Price / Volatility / Sentiment / Liquidity
- 每个轴 0-100
- 填充区域: 半透明红色 `rgba(239, 68, 68, 0.2)`
- 边框: `rgba(239, 68, 68, 0.8)`
- 网格线: 暗灰色

### 3.4 关键指标卡片

6 个卡片，2 行 x 3 列网格：

| 卡片 | 数据源 | 显示 |
|------|--------|------|
| Max Pain | `risk_data.max_pain.price` | 价格 + 距现货 % |
| Put Wall | `risk_data.put_wall` | 行权价 + OI |
| Gamma Flip | `risk_data.gamma_flip` | 价格 + 信号 |
| 常规支撑 | `risk_data.floors.regular` | 价格 + 距现货 % |
| 极端支撑 | `risk_data.floors.extreme` | 价格 + 距现货 % |
| 做市商信号 | `risk_data.mm_signal` | 信号文字（或"暂无"） |

### 3.5 Tab 内容区

**链上指标 Tab:**
- 顶部: 收敛评分小型 gauge + 底部概率 + 活跃指标数
- 9 个指标卡片（MVRV, Z-Score, NUPL, Mayer, 200WMA, Balanced Price, 200DMA, Halving, Puell）
- 每个卡片新增迷你 sparkline（最近 7 个历史值的折线）
- MVRV Z-Score 保留渐变条

**衍生品 Tab:**
- 过热评估仪表盘 + 信号网格
- 4 个指标卡片（Sharpe 7d/30d, 资金费率, 期货/现货比）
- 资金费率趋势图（如有历史数据）

**压力测试 Tab:**
- 风险等级卡片 + Vanna/Volga/Gamma 卡片
- 7 场景表格，增加颜色渐变（绿→黄→红）

**AI 情绪 Tab:**
- 主导意图 + 风险等级 + 信心度
- Put/Call 比例条形图
- 意图分布水平条形图（6 类）
- 风险警告列表

### 3.6 LLM 研判面板

- 独立卡片，红色左边框
- Header: "🤖 LLM 智能研判" + [开始研判] 按钮 + 模型状态
- 加载态: spinner + "LLM 分析中..."
- 结果: 3 个可折叠区块
  - 风险叙事（蓝色边框）
  - 异常告警（黄色边框，⚠️ 图标）
  - 操作建议（绿色边框，✅ 图标）
- 底部: 模型名 + 信心度进度条

### 3.7 JS 函数

新增函数：
- `renderRiskGauge(canvasId, score, status)` — Gauge 仪表盘
- `renderRiskRadar(canvasId, dimensions)` — 雷达图
- `renderSparkline(elementId, values)` — 迷你折线图
- `setRiskTab(tab)` — Tab 切换
- `loadLLMRiskInsight(currency)` — 调用 LLM 端点
- `renderLLMRiskInsight(data)` — 渲染 LLM 结果

修改函数：
- `loadRiskDashboard(currency)` — 增加 gauge/radar 渲染
- `updateOnchainMetrics()` — 增加 sparkline
- `updateDerivativeMetrics()` — 增加趋势图
- `updatePressureTest()` — 增加颜色渐变
- `updateSentimentAnalysis()` — 增加条形图

删除：
- 旧的 4 维分数条 HTML + JS（被雷达图替代）
- 旧的策略建议列表（被 LLM 研判替代）

---

## 4. 数据流

```
用户打开页面
    ↓
loadRiskDashboard(currency)
    ↓
GET /api/risk/overview → get_risk_overview_sync()
    ↓
返回全量风险数据（含修复后的数学计算）
    ↓
前端渲染:
    ├── renderRiskGauge(综合评分)
    ├── renderRiskRadar(4维)
    ├── 6 个指标卡片
    ├── Tab 内容（链上/衍生品/压力测试/AI情绪）
    └── [用户点击] loadLLMRiskInsight()
            ↓
        GET /api/risk/llm-insight
            ↓
        LLM 读取全量风险数据 → 生成研判报告
            ↓
        renderLLMRiskInsight()
```

---

## 5. 错误处理

- 风险数据获取失败 → Gauge 显示 "--"，卡片显示"数据不可用"
- LLM 不可用 → 面板显示"LLM 服务未配置，请在 AI 研判中心配置"
- LLM 超时 → 显示已获取的部分数据 + 超时警告
- 单个数据源失败 → 该卡片显示降级数据，不影响其他模块

---

## 6. 测试策略

### 后端测试

- `test_risk_math.py`: Volga 公式、POP 计算、支撑位权重、Z-Score Bessel 校正
- `test_risk_api.py`: LLM 研判端点 mock 测试

### 前端测试

- Gauge 渲染（不同分数区间颜色）
- Radar 渲染（4 维数据）
- Tab 切换
- LLM 面板加载/错误/结果渲染
