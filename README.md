<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Platform-Binance%20%2B%20Deribit-orange?logo=bitcoin" alt="Platform">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/v12.0-筑底信号v3.0-blueviolet" alt="Version">
</p>

<h1 align="center">Crypto Options Aggregator</h1>

<p align="center">
  <b>专业级双平台期权扫描器 + 链上数据引擎 + 智能策略系统</b><br>
  实时聚合 Binance + Deribit 深度期权数据，融合 fuckbtc.com 链上指标<br>
  专为 <b>Sell Put / Covered Call / Wheel</b> 策略交易者打造的一站式决策平台
</p>

---

## 🌟 核心亮点

### 🔗 链上数据引擎 v2.0 — 基于 BTC 筑底信号深度研究报告的多维指标体系

全面引入学术论文《BTC 筑底信号与宏观周期指标深度研究报告》的链上分析框架，从单一 MVRV 扩展到 7 维指标汇合系统。

| 指标 | 数据源 | 学术依据 | 底部信号 | 当前值示例 |
|------|--------|----------|----------|------------|
| **MVRV Ratio** | looknode-proxy API | Messari (2022) | < 1.0 历史底部 | 1.42 |
| **MVRV Z-Score** | MVRV 历史统计 | 统计学 Z 分布 | < -1 极度低估 | -0.38 |
| **NUPL** | MVRV 推导 | 净未实现损益模型 | < 0 恐惧区 | 0.295 |
| **Mayer Multiple** | Binance 200DMA | Mayer (2014) | < 1.0 低估 | 0.88 |
| **200WMA** | Binance 200周K线 | 长期趋势基准 | 价格接近 | $59,939 |
| **200DMA** | Binance 200日K线 | 中期成本基准 | 价格低于 | $86,805 |
| **Balanced Price** | 链上API直接获取 | 已实现-转移价格 | 价格低于 | $40,449 |

**多重指标汇合评分系统**:
- 每个指标贡献 -10 到 +10 分，总分范围 -70 ~ +70
- 6 级判定：STRONG_BOTTOM / BOTTOM / ACCUMULATION / NEUTRAL / DISTRIBUTION / TOP
- 底部概率估计：极高 (>80%) → 极低 (<5%)
- 信号明细输出：7 个独立指标信号逐一亮相

> 全面覆盖研究报告中的 5 大维度：链上成本基准、盈亏心理振荡器、长期技术指标、资本流转、宏观综合信号

### 💰 真实 Margin-APR — 反映资金真实效率

摒弃传统面值收益率，采用**真实保证金占用回报率**计算。

```
传统 APR = 权利金 / 合约面值  ❌ (低估资金占用)
真实 APR = 权利金 / 实际保证金  ✅ (反映真实效率)
```

### 🌊 DVOL 波动率引擎 — 策略自动适配

基于 Deribit 波动率指数，自动计算 Z-Score 和历史分位数：
- **高波动 (>70分位)**: 自动收紧参数，降低 APR 要求，增加合约数量
- **低波动 (<30分位)**: 自动放宽参数，提高 APR 要求，减少合约数量

### 🛡️ 动态风险框架 — 精确的市场阶段判断

| 风险层级 | 常规支撑位 | 极端支撑位 | 策略指令 |
|----------|------------|------------|----------|
| **安全区** | 价格 > 常规底 +10% | -- | 正常开仓，Wheel 策略 |
| **警戒区** | 常规底 ±10% | -- | 收紧 Delta，增加 DTE |
| **危险区** | 极端底 ~ 常规底 | -- | 暂停开仓，准备滚仓 |
| **恐慌区** | 价格 < 极端底 | -- | 正收益滚仓，确保 Net Credit > 0 |

结合 Max Pain、Gamma Flip、Put Wall 防线，输出实时操作指令。

### 🎯 智能策略引擎

**策略评分系统**: 0-100 分综合评分，4 维度评估
- 收益性 (30%)、风险性 (30%)、胜率 (25%)、流动性 (15%)

**Payoff 可视化**: 交互式盈亏曲线，支持 Sell/Buy Put/Call，实时显示最大盈亏、盈亏平衡点

**正收益滚仓计算器**: 持仓遇险时自动寻找更优合约，确保滚仓后净信用大于零

**网格策略引擎**: 4 种预设（保守/均衡/激进/智能推荐），根据 DVOL 自动调整参数

### ⚡ 压力测试系统 — 基于 Black-Scholes 的高阶 Greeks 敏感度分析

专业级期权风险度量工具，超越简单的 Delta-Gamma 模拟：

| 分析维度 | 说明 |
|----------|------|
| **Vanna 分析** | 价格-波动率交叉敏感度 (dDelta/dSigma) |
| **Volga 分析** | 波动率的波动率敏感度 (dVega/dSigma) |
| **价格敏感度矩阵** | 8 个价格变动场景 (-50% ~ +50%) |
| **波动率敏感度矩阵** | 5 个波动率变动场景 (-50% ~ +100%) |
| **联合压力场景** | 7 种标准场景（闪崩+波动率飙升等） |
| **风险评估引擎** | HIGH/MEDIUM/LOW 三级自动判定 |

```
典型场景: 闪崩 -50% + 波动率飙升 200%
→ Delta: -0.1523 | Gamma: 0.000012
→ Vanna: -0.0845 | Volga: 0.3214
→ 风险等级: HIGH ⚠️
```

### 🧠 AI 驱动的大宗交易情绪分析 v2.0 — 机构意图识别引擎

无需 LLM API 的本地规则引擎，6 维交叉分析识别大宗交易真实意图：

**6 维分析框架**:
1. **Moneyness 分析** — 实值/虚值程度判定
2. **Delta 行为分析** — 期权方向性暴露
3. **波动率分析** — IV 相对市场溢价/折价
4. **期限结构分析** — DTE 时间维度意图
5. **订单流分析** — 买/卖方向识别
6. **规模分析** — 名义价值 BTC 标准化

**机构行为模式识别**:
| 模式 | 描述 |
|------|------|
| Block Trade Split | 大宗分单（规避市场冲击） |
| Sweep Order | 扫单（跨行权价快速建仓） |
| Multi-leg Strategy | 多腿组合策略（价差/跨式） |
| Roll Operation | 滚仓操作（移仓换月） |
| Delta Hedge | 动态 Delta 对冲 |

**输出能力**: 主导意图、置信度、信号强度、风险预警、策略建议

### 📊 IV 期限结构 v2.0 — 基于学术研究的波动率分析框架

传统期限结构只显示原始 IV 数据，v2.0 引入完整的学术研究框架，将数据转化为可操作的策略建议：

**6 维分析框架**:
| 维度 | 学术依据 | 输出 |
|------|----------|------|
| 形态分类 | Hull & White (1987), Christoffersen et al. (2014) | Contango / Backwardation / Hump / Flat 等 6 种 |
| 斜率分析 | Derman & Kani (1994) | 百分比 + 分档 (VERY_STEEP ~ SEVERELY_INVERTED) |
| 曲率分析 | 二阶差分近似 | HUMP (事件驱动) / U-SHAPED / LINEAR |
| IV 水平 | DVOL 历史经验 | EXTREME / HIGH / NORMAL / LOW / VERY_LOW |
| VRP 估计 | Bakshi et al. (2003) | IV-HV 计算风险溢价，判定买卖方优势 |
| 市场状态 | 综合评分模型 | BULLISH_CALM ~ PANIC 五级评估 |

**策略建议引擎**:
- ⚠️ **警告类**：恐慌信号、低IV陷阱、市场恐惧
- 💡 **机会类**：卖方优势、Calendar Spread、高IV环境
- ℹ️ **信息类**：事件驱动关注、市场状态正常

---

| 数据维度 | 来源 | 建议影响 |
|----------|------|----------|
| 风险层级 | 动态风险框架 | 基础仓位指导（NORMAL/PANIC） |
| 链上指标 | 8维筑底信号系统 | 周期位置判断 + 汇合评分 |
| 压力测试 | Vanna/Volga/Gamma | 高阶风险对冲建议 |
| AI情绪分析 | 大宗交易意图识别 | 机构行为预警 |
| 最大痛点 | 期权持仓分析 | 价格回归方向判断 |
| Put Wall/Gamma Flip | 做市商仓位 | 支撑/阻力动态调整 |

**示例输出**：
```
🛡️ Put Wall防线: $120,000 (OI=12,543) — 机构在此布防
⚡ 压力测试警告：Vanna/Volga 风险较高，建议严格对冲
🧠 AI信号: Put 占比 72%，恐慌情绪蔓延
🧠 AI识别：机构正在积极对冲风险，建议降低仓位暴露
✅ Gamma Flip $110,000 — 价格在多头Gamma区，波动受抑
当前最大痛点在 $108,000。价格高于痛点，存在向下回归压力。
```

---

## 📊 功能模块总览

| 模块 | 核心能力 |
|------|----------|
| 🔗 **双平台统一视图** | Binance (USDT本位) + Deribit (币本位) 同一面板对比真实收益 |
| 💡 **智能抄底助手** | 融合水位、Max Pain、GEX，输出建仓/滚仓/平仓指令 |
| 🌊 **大单风向标** | 监控百万级大单，基于 Delta 深度解析交易意图（备兑/保护/追涨） |
| 📈 **多维数据图表** | APR 分位图、DVOL 趋势图、波动率曲面、PCR 面板 |
| 🆚 **策略对比模式** | 同时对比最多 5 个参数组合，直观比较 ROI/胜率/风险回报 |
| 🎡 **Wheel ROI 计算器** | 完整 Wheel 策略收益分析，年化 ROI、胜率、Put/Call 收入分解 |
| 🐋 **大宗异动监控** | 5 级严重度分类，权利金/IV/Delta 显示，流向提示 |
| ⚡ **压力测试系统** | 基于 Black-Scholes 的 Vanna/Volga 高阶 Greeks 敏感度分析 |
| 🧠 **AI 情绪分析 v2.0** | 6 维交叉分析，机构意图识别，风险预警，策略建议 |
| 🏥 **API 健康检查** | `/api/health` 实时监控数据库、扫描状态、缓存有效性 |

---

## 🚀 快速开始

### 1. 环境准备

```bash
# 克隆仓库
git clone https://github.com/roywongx/crypto-options-aggregator.git
cd crypto-options-aggregator

# 安装依赖
pip install -r requirements.txt
pip install -r dashboard/requirements.txt
```

### 2. 启动服务

```bash
cd dashboard
python -m uvicorn main:app --reload --port 8000
```

访问 👉 `http://localhost:8000`

---

## 🏗️ API 端点

| 路由 | 方法 | 描述 |
|------|------|------|
| `/api/quick-scan` | POST | 核心扫描接口，并行获取盘口/现货价/期权链 |
| `/api/latest` | GET | 获取最后一次有效扫描结果 |
| `/api/risk/overview` | GET | 风险概览：价格/状态/支撑位/链上指标/Put Wall/建议 |
| `/api/health` | GET | 健康检查：数据库/扫描状态/缓存 |
| `/api/charts/apr` | GET | APR 历史数据 |
| `/api/charts/dvol` | GET | DVOL 历史数据 |
| `/api/charts/pcr` | GET | PCR 历史数据 |
| `/api/charts/vol-surface` | GET | 波动率曲面 |
| `/api/metrics/max-pain` | GET | 最大痛点 + Gamma Flip |
| `/api/grid/recommend` | GET | 智能网格推荐 |
| `/api/large-trades` | GET | 大额交易记录 |

---

## 📈 数据准确性报告

| 指标 | 本系统 | fuckbtc.com | 偏差 |
|------|--------|-------------|------|
| MVRV Ratio | 1.38 | 1.38 | **0%** |
| 200周均线 | $59,947 | $59,948 | **<0.002%** |
| Balanced Price | $40,437 | $40,437 | **0%** |
| 常规支撑位 | $69,798 | -- | 真实 Binance 数据 |
| Put Wall | $70,000 | -- | 真实 Deribit OI 数据 |

**代码质量评分**: 9.2/10 (A+) — [详细报告](dashboard/TEST_REPORT.md)

**压力测试验证**:
- Black-Scholes 模型通过 Greeks 解析解验证
- 联合压力场景覆盖极端市场情况（闪崩、波动率飙升、双重打击等）
- 风险等级判定基于 Vanna/Volga/Gamma 多维阈值

**AI情绪分析验证**:
- 6 维分析框架基于机构交易行为学研究
- 机构行为模式识别匹配主流大宗交易特征
- 规则引擎可解释性强，每笔交易输出完整推理链

---

## 💡 更新日志

### v12.0 (2026-04) — 筑底信号 v3.0（Bitcoin Magazine Pro 标准）

**🔬 MVRV Z-Score 修正**
- 修正计算逻辑：使用标准 MVRV 历史统计 Z-Score 方法
- 引入 Bitcoin Magazine Pro 颜色带区标准：绿色带（Z < 0）= 低估区，粉色带（Z > 1）= 过热区
- 新增历史极值记录：min/max Z-Score + 当前百分位
- 前端新增颜色渐变指示条（绿→黄→粉）

**📊 8 维筑底信号汇合系统**
- 新增 Puell Multiple（矿工收入倍数）：基于 Binance 365 天价格估算矿工收入
- 汇合评分扩展为 8 个指标，总分范围 -80 ~ +80
- 阈值调整：STRONG_BOTTOM ≤ -40, BOTTOM ≤ -20, ACCUMULATION ≤ -5, NEUTRAL ≤ 15, DISTRIBUTION ≤ 30, TOP > 30
- 8 个激活指标实时显示信号明细

### v11.0 (2026-04) — 链上数据引擎 v2.0 + 筑底信号系统

**🔗 链上数据引擎 v2.0（基于 BTC 筑底信号深度研究报告）**
- 新增 MVRV Z-Score：基于 MVRV 365天历史统计，识别极端低估区域
- 新增 NUPL（Net Unrealized Profit/Loss）：净未实现损益情绪振荡器
- 新增 Mayer Multiple：价格/200DMA，经典超买超卖识别
- 新增 200日均线（200DMA）：Binance 200日收盘均价
- 全面升级数据准确性：修复 current_price 判定逻辑，确保 0 值正确处理

**📊 多重指标汇合评分系统**
- 7 维度综合评分：每个指标贡献 -10 ~ +10 分
- 6 级自动判定：STRONG_BOTTOM / BOTTOM / ACCUMULATION / NEUTRAL / DISTRIBUTION / TOP
- 底部概率估计：结合历史周期经验给出概率区间
- 信号明细输出：7 个独立指标逐一亮相（底部/中性/顶部信号）

**🎨 前端增强**
- 新增「筑底信号汇合仪表盘」：综合评分 + 底部概率 + 激活指标 + 信号明细
- 新增 4 个指标卡片：MVRV Z-Score / NUPL / Mayer Multiple / 200DMA
- 全新色彩系统：不同指标使用不同主题色（紫/靛蓝/玫瑰红/青绿/天蓝）
- 智能颜色编码：根据信号强度自动切换绿/黄/红三色

### v10.0 (2026-04) — 压力测试系统 + AI情绪分析v2.0 + 马丁格尔沙盘v2.0

**⚡ 压力测试系统（基于 Black-Scholes）**
- 新增 Vanna/Volga 高阶 Greeks 敏感度分析
- 7 种联合压力场景（闪崩-50%+波动率飙升200%等）
- 风险评估引擎（HIGH/MEDIUM/LOW 三级自动判定）
- 价格敏感度矩阵（8个场景）+ 波动率敏感度矩阵（5个场景）

**🧠 AI 情绪分析 v2.0（机构意图识别引擎）**
- 6 维交叉分析框架（Moneyness/Delta/波动率/期限结构/订单流/规模）
- 新增「波动率博弈」意图分类（押注波动率方向而非价格方向）
- 5 种机构行为模式识别（Block/Sweep/Multi-leg/Roll/Delta Hedge）
- Gamma 敞口影响估计（评估大额交易对短期价格的潜在影响）
- 信号强度分级（STRONG/MEDIUM/WEAK）
- 风险预警系统（HIGH/MEDIUM/LOW 三级）
- 市场流摘要（交易数/名义价值/机构占比/平均单量）
- 动态策略建议生成（意图+信号驱动组合推荐）

**🎨 前端增强**
- 压力测试显示区块（风险等级/Vanna/Volga风险/联合压力场景表格）
- AI情绪分析 v2.0 UI（主导意图/置信度/风险等级/信号强度/意图分布/关键信号/风险预警）
- 新增「波动率博弈📊」意图类别显示

**🧪 马丁格尔沙盘推演 v2.0（全面重构）**
- 重写输入界面：持仓信息（行权价/类型/数量/权利金/DTE）+ 崩盘情景
- 精确损失分解：本金损失 + Vega 冲击（崩盘 IV 飙升影响）
- 安全评估引擎：4级评估（SAFE/WARNING/DANGER/CRITICAL）+ 资金覆盖率
- 恢复方案搜索：基于 Binance/Deribit 双平台真实扫描数据
- 恢复方案列表：Top 8 候选，按净恢复值排序
- 状态指示灯：✅成功/⚠️部分/🔴危险
- 新增参数：保证金比率、最小 APR 过滤

### v9.0 (2026-04) — 链上数据引擎 + 支撑位重构 + API重试机制

**🔗 链上数据引擎**
- 新增 MVRV Ratio、200周均线、Balanced Price、减半倒计时
- 数据精度与 fuckbtc.com 一致，全面弃用硬编码/估算

**🛠️ 支撑位系统重构**
- 常规支撑位: $56,543 → $69,798 (真实 Binance 数据)
- 极端支撑位: $48,062 → $59,328 (合理推算)
- Put Wall 逻辑修正: 单个行权价最大 Put OI

**🔄 API重试机制**
- 新增 `services/api_retry.py` 指数退避重试工具
- 所有外部 API 自动重试 3 次 (1s→2s→4s)

**🔒 代码质量**
- 修复 4 处裸 except，8 处 print()→logging
- 新增 `/api/health` 健康检查端点
- 评分: 8.45 → 8.925 (A- → A)

### v8.0 (2025-04) — 网格策略引擎修复 + 全面Bug修复
- 修复网格去重逻辑导致空结果的核心 Bug
- 修复 SQL 列索引错位、场景模拟盈亏计算错误
- 修复前端 XSS 漏洞，移除后端 Tailwind 类名
- 新增 `constants.py` 集中管理默认值

### v7.0 (2024-04) — 智能策略引擎重大升级
- Payoff 可视化 + Wheel ROI 专业增强
- 策略评分系统 (0-100分，4维度)
- 网格策略引擎 4 种预设 + DVOL 智能推荐
- 最大痛点/Gamma Flip 重新设计
- 大宗异动 5 级严重度分类

---

## 🙏 致谢

- **[deribit-options-monitor](https://github.com/lianyanshe-ai/deribit-options-monitor)** — 核心根基：Deribit API 封装、Greeks 推算、DVOL 框架
- **[ccxt](https://github.com/ccxt/ccxt)** — 极端行情下现货价格的 Fallback 方案

---

## ⚠️ 风险免责声明

期权交易具有极高风险，可能导致本金完全损失。
本工具所有数据、建议、压力测试及滚仓计算**仅供学习与量化分析参考，绝不构成任何投资建议**。实盘交易前请充分理解期权规则并严格做好资金管理。
