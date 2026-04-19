<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Platform-Binance%20%2B%20Deribit-orange?logo=bitcoin" alt="Platform">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/v4.0-Pro%20Terminal-blueviolet" alt="Version">
</p>

<h1 align="center">Crypto Options Aggregator Pro</h1>

<p align="center">
  <b>专业级加密货币期权交易终端 — Binance + Deribit 实时聚合</b><br>
  专为 <b>Sell Put / Covered Call / Wheel / 滚仓</b> 策略交易者打造的机构级决策平台
</p>

<p align="center">
  <b>✨ v4.0 全新架构</b> — 基于 FinceptTerminal 源码级深度分析，引入 5 大专业架构模块
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
| **DataHub** | WebSocket 实时推送，<10ms 毫秒级扫描 |
| **Paper Trading** | 连续模拟盘，5 万 U 虚拟本金测试策略 |
| **AI Co-Pilot** | 内嵌智能投顾，实时对话获取交易建议 |
| **MCP Server** | 外部 AI 可直接调用本地交易工具 |

---

## v4.0 核心架构升级

### 1. DataHub — 高性能 Pub/Sub 数据中心

```
┌─────────────┐     WebSocket      ┌──────────────┐
│  Deribit WS │──── real-time ────▶│              │
│  Binance WS │──── real-time ────▶│   DataHub    │──▶ quick_scan (<10ms)
│  DVOL Calc  │──── real-time ────▶│  Pub/Sub     │──▶ 前端 WebSocket 推送
└─────────────┘                     └──────────────┘
```

- **替代 REST 轮询** — WebSocket 持久连接实时接收 ticker/trade/orderbook
- **毫秒级响应** — quick_scan 直接从 DataHub 缓存读取，速度从秒级降至 <10ms
- **主题订阅** — `topic_btc_options`, `topic_eth_options`, `topic_dvol`, `topic_spot`
- **自动回退** — DataHub 不可用时自动回退到 REST 请求，保证服务可用性

### 2. Exchange Abstraction Layer — 多交易所抽象层

```python
BaseExchange (ABC)
├── BinanceExchange
├── DeribitExchange
├── BybitExchange (未来扩展)
└── OKXExchange   (未来扩展)
```

- **统一接口** — `get_options_chain()`, `get_dvol()`, `get_spot()` 标准化方法
- **策略无关** — CalculationEngine 和 RiskFramework 处理数据无需关心交易所来源
- **即插即用** — 添加新交易所只需实现 BaseExchange，主逻辑零修改

### 3. Paper Trading Engine — 连续模拟盘

- **虚拟本金 $50,000 USDT** — SQLite 持久化存储
- **实时 UPnL 计算** — 基于市场价格的浮动盈亏追踪
- **保证金监控** — 实时计算保证金占用率，超限报警
- **滚仓策略支持** — 一键试算 Rolling Down & Out 成本和收益
- **安全测试** — 在真实市场数据上测试策略，零风险

### 4. MCP Server — 模型上下文协议

```
Claude Desktop / Cursor
         │
         ▼
┌─────────────────┐
│  MCP Server     │
├─────────────────┤
│ • get_market_overview()
│ • calculate_greeks()
│ • analyze_large_trades()
│ • suggest_roll_strategy()
│ • get_risk_assessment()
│ • get_highest_apr_put()
│ • calculate_roll_cost()
│ • get_paper_portfolio()
└─────────────────┘
         │
         ▼
   本地 FastAPI 服务
```

- **8 个 MCP Tools** — 覆盖市场数据、希腊字母、大宗分析、滚仓建议
- **外部 AI 直连** — Claude Desktop / Cursor / Gemini CLI 可直接调用
- **自主决策** — AI 可自主拉取数据并给出交易建议

### 5. AI Co-Pilot — 内嵌智能投顾

- **右下角聊天框** — 与 AI 直接对话
- **自动注入上下文** — DVOL、恐惧贪婪指数、资金费率自动附加
- **实时推理** — 基于当前盘面给出专业交易建议

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

- **BTC/ETH 实时价格** — Binance/Scan 双源，WebSocket 推送
- **DVOL 值** — Deribit 波动率指数，含 Z-Score 信号
- **恐惧贪婪指数** — alternative.me 实时情绪指标
- **资金费率** — Binance Futures 实时资金费率
- **大宗交易** — 最近一小时内大额成交笔数
- **风险等级** — 基于流动性 + Gamma + 情绪综合评估
- **距支撑位** — 当前价到常规支撑位的距离

### 扫描控制台

- **手动扫描** — 点击触发全量期权链分析（<10ms DataHub 模式）
- **自动刷新** — 可配置定时刷新（1/3/5 分钟）
- **扫描状态** — 实时显示扫描进度和结果

### 机会表格

- 按 **Margin-APR** 降序排列
- 分页显示（30 条/页），支持"加载更多"
- 显示合约详情、Delta、DTE、买卖价差、流动性评分

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

### AI Co-Pilot 聊天框

- 右下角浮动按钮
- 点击展开聊天窗口
- 支持回车发送消息
- 自动附加市场上下文

---

## API 端点

### 核心数据

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/health` | GET | 健康检查（DB + Deribit + Binance） |
| `/api/dashboard-init` | GET | 聚合初始化（Wind/TermStructure/MaxPain） |
| `/api/quick-scan` | POST | 快速扫描（DataHub 优化，<10ms） |
| `/api/dvol/refresh` | GET | DVOL 实时刷新 |
| `/api/trades/refresh` | GET | 大宗异动实时刷新 |

### DataHub WebSocket

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/datahub/status` | GET | WebSocket 连接状态 |
| `/api/datahub/options-chain` | GET | 毫秒级期权链查询 |
| `/api/eventbus/snapshot` | GET | 事件总线当前快照 |
| `/api/eventbus/history` | GET | 事件历史查询 |
| `/api/eventbus/ws` | WS | WebSocket 实时推送 |

### Exchange Abstraction

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/exchanges/list` | GET | 已注册的交易所列表 |
| `/api/exchanges/chain` | GET | 通过统一接口获取期权链 |
| `/api/exchanges/multi-chain` | GET | 同时获取多个交易所的期权链 |
| `/api/exchanges/dvol` | GET | 获取指定交易所的 DVOL |

### Paper Trading

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/paper/portfolio` | GET | 模拟盘组合概览 |
| `/api/paper/trades` | GET | 历史交易记录 |
| `/api/paper/open` | POST | 模拟开仓 |
| `/api/paper/close` | POST | 模拟平仓 |
| `/api/paper/roll-suggestion` | GET | 滚仓建议 |

### MCP & AI

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/mcp/tools` | GET | 列出所有可用的 MCP 工具 |
| `/api/mcp/execute` | POST | 执行 MCP 工具 |
| `/api/mcp/chat` | POST | MCP 对话接口 |
| `/api/copilot/chat` | POST | AI Co-Pilot 对话 |

---

## 数据源

| 平台 | 数据类型 |
|------|----------|
| **Binance eAPI** | 期权行情、持仓量、成交记录 |
| **Deribit** | 期权摘要、大宗交易、DVOL 指数 |
| **Binance Spot API** | BTC/ETH 实时现货价格 |
| **Binance Futures API** | 实时资金费率 |
| **alternative.me** | 恐惧贪婪指数 |
| **yfinance** | QQQ/SPY 宏观数据 |
| **FRED API** | 无风险利率 |

---

## 架构亮点

- **全异步架构** — httpx.AsyncClient 消除 I/O 阻塞
- **WebSocket 实时推送** — DataHub 持久连接替代 REST 轮询
- **聚合 API** — `/api/dashboard-init` 一次请求获取多模块数据
- **轻量刷新** — `/api/dvol/refresh` + `/api/trades/refresh` 独立实时刷新
- **SQLite 读写分离** — `execute_read`/`execute_write` 避免并发冲突
- **线程安全** — `threading.local()` 只读连接 + 写入锁序列化
- **前端并行加载** — 模块独立请求，无瀑布效应
- **智能分页** — Intersection Observer 懒加载 + 加载更多
- **Exchange Abstraction** — 多交易所统一接口，未来可扩展
- **Paper Trading** — 连续模拟盘，实时 UPnL 计算
- **MCP Server** — 8 个工具，外部 AI 直连
- **AI Co-Pilot** — 内嵌聊天框，自动注入市场上下文

---

## 更新日志

### v4.0 — Pro Terminal（当前）

**FinceptTerminal 源码级架构升级：**

- ✅ **DataHub** — WebSocket 实时推送，<10ms 毫秒级扫描
- ✅ **Exchange Abstraction Layer** — BaseExchange(ABC) 统一接口
- ✅ **Paper Trading Engine** — 连续模拟盘 + SQLite 持久化
- ✅ **MCP Server** — 8 个 MCP Tools，外部 AI 直连
- ✅ **AI Co-Pilot** — 内嵌聊天框 + 自动上下文注入
- ✅ **Fear & Greed Index** — 实时情绪指标
- ✅ **Funding Rate** — Binance Futures 资金费率
- ✅ **yfinance 宏观数据** — QQQ/SPY 市场数据
- ✅ **Quant Engine** — 机构级希腊字母计算（scipy）
- ✅ **LiteLLM AI 路由** — 多模型切换支持

**黑白盒测试修复：**
- 修复 Deribit/Binance WebSocket 连接格式
- 修复 quick_scan DataHub 数据转换逻辑
- 修复 MCP Server 函数参数顺序
- 修复重复 @app.on_event 冲突
- 修复 DeribitExchange 方法调用错误

### v3.0 — 重构版

- 修复 binance_options 模块导入失败
- 修复数据库连接关闭错误（全面使用 `execute_read` 替代手动连接）
- 新增 `/api/dvol/refresh` 轻量级 DVOL 实时刷新端点
- 新增 `/api/trades/refresh` 轻量级大宗异动实时刷新端点
- 前端集成 DVOL / 大宗异动自动刷新
- 删除最佳 APR 功能模块（无指导意义）

### v2.6

- 修复 grid-strategy.js null 引用错误
- 修复数据库连接关闭导致 500 错误
- 修复 health_check 时间戳解析错误
- 修复 export CSV 端点不可达

### v2.5

- 创建 `/api/dashboard-init` 聚合 API
- 消除前端瀑布加载逻辑
- 后端 asyncio.gather 并行获取 Wind/TermStructure/MaxPain

### v2.1

- 后端扫描性能优化（O(N)→O(1) + 并行 OI + 缓存）
- 前端渐进式加载 + 表格分页 + GZIP 压缩

### v2.0

- 统一策略推荐引擎
- 链上数据引擎 v2.0

---

## License

[MIT License](LICENSE)
