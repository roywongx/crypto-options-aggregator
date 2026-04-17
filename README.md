<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Platform-Binance%20%2B%20Deribit-orange?logo=bitcoin" alt="Platform">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/v9.0-链上数据引擎+智能策略引擎-blueviolet" alt="Version">
</p>

<h1 align="center">Crypto Options Aggregator</h1>

<p align="center">
  <b>专业级双平台期权扫描器 + 链上数据引擎 + 智能策略系统</b><br>
  实时聚合 Binance + Deribit 深度期权数据，融合 fuckbtc.com 链上指标<br>
  专为 <b>Sell Put / Covered Call / Wheel</b> 策略交易者打造的一站式决策平台
</p>

---

## 🌟 核心亮点

### 🔗 链上数据引擎 — 数据精度与 fuckbtc.com 一致

| 指标 | 数据源 | 精度验证 |
|------|--------|----------|
| **MVRV Ratio** | looknode-proxy API | 与 fuckbtc.com 精确匹配 (1.38) |
| **200周均线** | Binance 200周K线 | 误差 <0.002% ($59,947 vs $59,948) |
| **Balanced Price** | 链上API直接获取 | 零偏差 ($40,437) |
| **减半倒计时** | blockchain.info 实时区块 | 实时更新 (725天) |

> 全面弃用硬编码/估算，所有链上指标均来自真实API，配备指数退避重试机制确保稳定性。

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

**代码质量评分**: 8.925/10 (A) — [详细报告](dashboard/TEST_REPORT.md)

---

## 💡 更新日志

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
