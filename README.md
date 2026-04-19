<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Platform-Binance%20%2B%20Deribit-orange?logo=bitcoin" alt="Platform">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/v3.0-重构版-blueviolet" alt="Version">
</p>

<h1 align="center">Crypto Options Aggregator</h1>

<p align="center">
  <b>双平台期权监控面板 — Binance + Deribit 实时聚合</b><br>
  专为 <b>Sell Put / Covered Call / Wheel</b> 策略交易者打造的一站式决策平台
</p>

---

## 快速概览

| 模块 | 功能 |
|------|------|
| **期权扫描** | 实时扫描 Binance + Deribit 期权链，按 Margin-APR 排序 |
| **DVOL 引擎** | Z-Score + 7 日分位数，动态参数调整 |
| **大宗异动** | 实时追踪大额成交，机构行为分析 |
| **大单风向标** | 多空流向统计，情绪评分 |
| **IV 期限结构** | 波动率曲面 + Backwardation 检测 |
| **最大痛点** | 期权到期最大利润点预测 |
| **风险评估** | 流动性/Gamma/情绪多维风险面板 |
| **链上指标** | MVRV / NUPL / Mayer 等 7 维底部信号 |
| **策略引擎** | Roll / New / Grid 三种策略模式 |

---

## 快速开始

### 1. 安装

```bash
git clone https://github.com/roywongx/crypto-options-aggregator.git
cd crypto-options-aggregator

# 安装依赖
pip install -r requirements.txt
```

### 2. 启动

```bash
cd dashboard
python main.py
```

### 3. 访问

浏览器打开 → **http://localhost:8000**

---

## 界面功能

### 顶部指标卡片

- **BTC 实时价格** — Binance/Scan 双源
- **DVOL 值** — Deribit 波动率指数，含 Z-Score 信号
- **大宗交易** — 最近一小时内大额成交笔数
- **风险等级** — 基于流动性 + Gamma + 情绪综合评估
- **距支撑位** — 当前价到常规支撑位的距离

### 扫描控制台

- **手动扫描** — 点击触发全量期权链分析
- **自动刷新** — 可配置定时刷新（1/3/5 分钟）
- **扫描状态** — 实时显示扫描进度和结果

### 机会表格

- 按 **Margin-APR** 降序排列
- 分页显示（30 条/页），支持"加载更多"
- 显示合约详情、Delta、DTE、买卖价差

### DVOL 趋势图

- 24H / 7天 / 30天 三种时间维度
- 实时监控波动率变化趋势

### PCR 持仓量图

- Put/Call 持仓量比率
- 市场情绪风向标

### 底部面板

- **大单风向标** — 多空分布 + 买卖倾向
- **IV 期限结构** — 不同到期日的隐含波动率
- **最大痛点** — 期权卖方利润最大化价格
- **风险评估** — 5 维度风险评分

---

## 数据源

| 平台 | 数据类型 |
|------|----------|
| **Binance eAPI** | 期权行情、持仓量、成交记录 |
| **Deribit** | 期权摘要、大宗交易、DVOL 指数 |
| **Binance Spot API** | BTC/ETH 实时现货价格 |

---

## 架构亮点

- **全异步架构** — httpx.AsyncClient 消除 I/O 阻塞
- **聚合 API** — `/api/dashboard-init` 一次请求获取多模块数据
- **轻量刷新** — `/api/dvol/refresh` + `/api/trades/refresh` 独立实时刷新
- **SQLite 读写分离** — `execute_read`/`execute_write` 避免并发冲突
- **线程安全** — `threading.local()` 只读连接 + 写入锁序列化
- **前端并行加载** — 模块独立请求，无瀑布效应
- **智能分页** — Intersection Observer 懒加载 + 加载更多

---

## 更新日志

### v3.0 — 重构版（当前）

- 修复 binance_options 模块导入失败
- 修复数据库连接关闭错误（全面使用 `execute_read` 替代手动连接）
- 修复 charts.py / trades_api.py / spot_price.py / trades.py / grid.py / constants.py 数据库连接问题
- 新增 `/api/dvol/refresh` 轻量级 DVOL 实时刷新端点
- 新增 `/api/trades/refresh` 轻量级大宗异动实时刷新端点
- 前端集成 DVOL / 大宗异动自动刷新
- 删除最佳 APR 功能模块（无指导意义）

### v2.6

- 修复 grid-strategy.js null 引用错误
- 修复数据库连接关闭导致 500 错误
- 修复 health_check 时间戳解析错误
- 修复 export CSV 端点不可达
- 添加 null 保护到 initCharts 和 dvolValue
- 修复 AbortError 请求冲突
- 增加 API 超时时间至 30s
- loadPageDataAsync 并行加载所有模块

### v2.5

- 创建 `/api/dashboard-init` 聚合 API
- 消除前端瀑布加载逻辑
- 后端 asyncio.gather 并行获取 Wind/TermStructure/MaxPain

### v2.3

- 消除冗余 Binance 抓取逻辑
- 直接调用 `binance_options.fetch_binance_options()`

### v2.2

- SQLite 读写分离 + 写入锁序列化

### v2.1

- 后端扫描性能优化（O(N)→O(1) + 并行 OI + 缓存）
- 前端渐进式加载 + 表格分页 + GZIP 压缩

### v2.0

- 统一策略推荐引擎
- 链上数据引擎 v2.0

---

## License

[MIT License](LICENSE)
