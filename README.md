<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Platform-Binance%20%2B%20Deribit-orange?logo=bitcoin" alt="Platform">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/v5.5-Production%20Ready-blueviolet" alt="Version">
</p>

<h1 align="center">Crypto Options Aggregator Pro</h1>

<p align="center">
  <b>专业级加密货币期权交易终端 — Binance + Deribit 实时聚合</b><br>
  专为 <b>Sell Put / Covered Call / Wheel / 滚仓</b> 策略交易者打造的机构级决策平台
</p>

<p align="center">
  <a href="#-quick-start">快速开始</a> •
  <a href="#-architecture">核心架构</a> •
  <a href="#-features">功能模块</a> •
  <a href="#-api">API 文档</a> •
  <a href="#-changelog">更新日志</a>
</p>

---

## 🎯 项目解决的核心痛点

### 痛点 1：期权数据分散，跨平台比对效率低下
加密货币期权数据分散在 Binance、Deribit 等多个交易所，交易者需要在不同平台间切换比对，耗时且容易遗漏最优机会。

**解决方案：**
- **统一聚合层** — 同时连接 Binance eAPI 和 Deribit WebSocket，实时聚合双平台期权链
- **标准化数据模型** — `OptionContract` 统一封装双平台异构数据，策略计算无需关心数据来源
- **智能排序引擎** — 按 Margin-APR 自动排序，一眼识别最优 Sell Put/Covered Call 机会

### 痛点 2：市场瞬息万变，人工监控无法跟上节奏
期权价格、IV、Greeks 每秒都在变化，人工刷新页面无法捕捉转瞬即逝的交易机会。

**解决方案：**
- **DataHub 实时数据中心** — WebSocket 长连接替代 REST 轮询，数据延迟从秒级降至 <10ms
- **自动扫描引擎** — 后台持续扫描全市场期权链，自动识别高 APR、低 Delta、高流动性合约
- **多维度信号预警** — DVOL Z-Score、资金费率、大宗异动、PCR 比率实时监控

### 痛点 3：策略回测困难，风险难以量化
期权策略（尤其是 Wheel 和 Roll）涉及多腿组合，普通交易者难以快速计算盈亏、评估风险。

**解决方案：**
- **策略计算引擎** — 支持 Roll / New / Grid 三种模式，自动计算净信用、年化收益、保证金占用
- **压力测试系统** — 基于 Black-Scholes Vanna/Volga 敏感度分析，模拟极端行情下的组合表现
- **Paper Trading 模拟盘** — 5 万 U 虚拟本金，在真实市场数据上零风险测试策略

### 痛点 4：缺乏专业分析工具，决策依赖直觉
散户交易者缺乏机构级的量化分析工具，往往凭感觉下单。

**解决方案：**
- **AI Co-Pilot 智能投顾** — 内嵌 AI 对话系统，自动注入 DVOL、恐惧贪婪指数、资金费率等市场上下文，给出专业交易建议
- **MCP Server 外部 AI 直连** — Claude Desktop / Cursor 可直接调用本地 8 个交易工具，实现 AI 自主分析
- **多模型路由** — LiteLLM 支持 Claude/GPT/DeepSeek/Gemini 自动切换，复杂分析用 Claude，快速响应用 GPT-4o-mini

---

## 🏗️ 核心架构

### 数据流架构 — 从交易所到前端的毫秒级链路

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              数据采集层                                       │
├─────────────────────────────┬───────────────────────────────────────────────┤
│   Deribit WebSocket          │   Binance eAPI / Futures API                  │
│   - 期权摘要 (book.summary)  │   - 期权行情 (eapi/v1/options)                │
│   - 大宗交易 (trades)        │   - 资金费率 (fundingRate)                    │
│   - DVOL 指数                │   - 现货价格 (ticker/price)                   │
└──────────────┬──────────────┴───────────────────────┬───────────────────────┘
               │                                        │
               ▼                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            DataHub 实时数据中心                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Pub/Sub 主题分发                                                     │   │
│  │  ├── topic_btc_options  →  期权链缓存 (symbol -> {mark, iv, delta})   │   │
│  │  ├── topic_eth_options  →  同上                                       │   │
│  │  ├── topic_dvol         →  DVOL + Z-Score + 信号                      │   │
│  │  ├── topic_spot         →  BTC/ETH 现货价格                           │   │
│  │  └── topic_funding      →  资金费率                                   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                              │                                              │
│                              ▼                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  自动回退机制 (Fallback)                                              │   │
│  │  DataHub 不可用时 → 自动切换 REST 请求，保证服务可用性                  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            计算引擎层                                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ 扫描引擎      │  │ 风险框架      │  │ 策略计算      │  │ 量化引擎      │   │
│  │ ScanEngine   │  │ RiskFramework│  │ StrategyCalc │  │ QuantEngine  │   │
│  ├──────────────┤  ├──────────────┤  ├──────────────┤  ├──────────────┤   │
│  │ • 全链扫描    │  │ • 价格风险评估│  │ • Roll 计算  │  │ • BS 定价    │   │
│  │ • 过滤排序    │  │ • 流动性评估  │  │ • New 计算   │  │ • Greeks 计算│   │
│  │ • APR 计算    │  │ • 情绪评估    │  │ • Grid 计算  │  │ • IV 曲面    │   │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            智能决策层 — AI 多 Agent 协作                      │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Agent 1: 市场数据分析师                                               │   │
│  │  职责: 聚合 DVOL + 恐惧贪婪指数 + 资金费率 + 大宗交易 → 生成市场摘要     │   │
│  │  触发: /api/copilot/chat, /api/mcp/chat                                │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Agent 2: 策略建议师                                                   │   │
│  │  职责: 基于市场摘要 + 用户持仓 → 给出 Roll/New 策略建议                 │   │
│  │  触发: /api/strategy-calc, /api/calculator/roll                        │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  Agent 3: 风险评估师                                                   │   │
│  │  职责: 并行计算价格/波动率/情绪/流动性风险 → 综合风险评分                │   │
│  │  触发: /api/risk/overview, /api/risk/assess                            │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  长链推理: AI Co-Pilot / MCP Server                                    │   │
│  │  输入: 用户自然语言问题 (如"现在适合 Sell Put 吗?")                     │   │
│  │  Step 1: 调用 MCP Tool get_market_overview → 获取市场数据              │   │
│  │  Step 2: 调用 MCP Tool get_risk_assessment → 获取风险评估              │   │
│  │  Step 3: 调用 MCP Tool analyze_large_trades → 分析机构动向             │   │
│  │  Step 4: AI 综合推理 → 生成交易建议                                    │   │
│  │  输出: 结构化建议 (策略 + 风险 + 仓位管理)                              │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            前端展示层                                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ 顶部指标卡片  │  │ 扫描控制台    │  │ 机会表格      │  │ 底部面板      │   │
│  │ • 现货价格   │  │ • 手动扫描    │  │ • APR 排序    │  │ • 大单风向标  │   │
│  │ • DVOL      │  │ • 自动刷新    │  │ • 分页加载    │  │ • IV 期限结构 │   │
│  │ • 恐惧贪婪   │  │ • 扫描状态    │  │ • 合约详情    │  │ • 最大痛点    │   │
│  │ • 资金费率   │  │              │  │              │  │ • 风险评估    │   │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ AI Co-Pilot 聊天框 (右下角浮动)                                        │   │
│  │ • 自然语言对话 → AI 自动拉取市场数据 → 实时推理 → 交易建议             │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 关键设计决策

| 设计点 | 决策 | 理由 |
|--------|------|------|
| **数据获取** | WebSocket + REST Fallback | 实时性优先，但保证可用性 |
| **缓存策略** | DataHub 内存缓存 + SQLite 持久化 | 热数据内存加速，历史数据本地存储 |
| **并发模型** | asyncio + ThreadPoolExecutor | IO 密集型用 async，CPU 密集型用线程池 |
| **AI 路由** | LiteLLM 多模型预设 | 不同任务匹配最优模型，成本与效果平衡 |
| **数据库** | SQLite (读写分离 + 异步线程池) | 单机部署零配置，异步查询避免阻塞事件循环 |
| **认证** | X-API-Key Header + 本地免验证 | 轻量级 API 保护，开发测试无感知 |
| **CORS** | FastAPI CORSMiddleware (环境感知) | 开发环境允许本地跨域，生产环境需显式配置 |
| **进程模型** | 单进程单例 (Uvicorn --workers 1) | 避免多进程导致 WebSocket 连接重复、内存翻倍 |

---

## 🚀 快速开始

### 环境要求

- Python 3.13+
- 支持 asyncio 的运行时环境
- 网络连接（用于访问 Binance / Deribit API）

### 安装步骤

```bash
# 1. 克隆仓库
git clone https://github.com/roywongx/crypto-options-aggregator.git
cd crypto-options-aggregator/dashboard

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量（可选）
cp .env.example .env
# 编辑 .env 文件，设置 API 密钥等

# 4. 启动服务（开发模式 - 单进程）
python main.py

# 或使用 Uvicorn（推荐）
uvicorn main:app --host 0.0.0.0 --port 8000 --reload --workers 1
```

> ⚠️ **重要**: 必须使用 `--workers 1` 单进程模式启动。系统使用内存单例管理 WebSocket 连接和缓存，多进程会导致连接重复、内存翻倍，甚至触发交易所 API 频率限制。

浏览器打开 → **http://localhost:8000**

### 生产环境部署

```bash
# 设置生产环境变量
export DASHBOARD_ENV=production
export DASHBOARD_API_KEY=your-secure-api-key
export CORS_ALLOWED_ORIGINS=https://your-domain.com

# 启动（单进程）
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

### AI Co-Pilot 配置（可选）

系统内置 AI 交易助手，支持任意 OpenAI 兼容 API：

1. 点击右下角 **🤖 AI 交易助手** → **⚙️ 设置按钮**
2. 填写您的 API 信息：
   - **API Key**: 您的 OpenAI / DeepSeek / Claude / 小米米莫 等 API Key
   - **Base URL** (可选): 自定义 API 地址，如 `https://token-plan-sgp.xiaomimimo.com/v1`
   - **模型**: 模型名称，如 `gpt-4o-mini`、`deepseek-chat`、`mimo-v2.5-pro`
3. 点击保存，立即开始对话

> 💡 **提示**: 配置会自动保存到浏览器本地存储，刷新页面后无需重新输入。

### Docker 部署 (待实现)

> ⚠️ Dockerfile 尚未创建，以下命令待后续补充。

```bash
# docker build -t crypto-options-aggregator .
# docker run -p 8000:8000 --env-file .env crypto-options-aggregator
```

---

## ✨ 功能模块

| 模块 | 功能 | 技术亮点 |
|------|------|----------|
| **期权扫描** | 实时扫描双平台期权链，按 Margin-APR 排序 | DataHub 缓存，<10ms 响应 |
| **DVOL 引擎** | Z-Score + 7 日分位数，动态参数调整 | 自适应阈值，减少假信号 |
| **大宗异动** | 实时追踪大额成交，机构行为分析 | 流分类器，自动识别多空意图 |
| **策略引擎** | Roll / New / Grid 三种模式 | 异步计算，支持复杂滚仓逻辑 |
| **Paper Trading** | 连续模拟盘，5 万 U 虚拟本金 | SQLite 持久化，实时 UPnL，保证金冻结 |
| **AI Co-Pilot** | 内嵌智能投顾，实时对话 | 自动注入市场上下文，多模型路由，支持自定义 API Key |
| **MCP Server** | 8 个工具，外部 AI 直连 | 自主决策，AI 可直接调用本地数据 |
| **风险框架** | 价格/波动率/情绪/流动性四维评估 | 并行计算，综合风险评分 |

---

## 📡 API 文档

启动服务后访问：**http://localhost:8000/docs**

### 核心 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 健康检查 |
| `/api/latest` | GET | 获取最新扫描结果 |
| `/api/quick-scan` | POST | 快速扫描期权链 |
| `/api/dvol` | GET | DVOL 分析数据 |
| `/api/charts/vol-surface` | GET | IV 期限结构（支持 Backwardation/Contango 判断） |
| `/api/charts/dvol` | GET | DVOL 历史图表数据 |
| `/api/charts/pcr` | GET | Put/Call Ratio 图表 |
| `/api/large-trades` | GET | 大宗交易记录 |
| `/api/risk/overview` | GET | 风险概览 |
| `/api/copilot/chat` | POST | AI Co-Pilot 对话 |
| `/api/mcp/chat` | POST | MCP Server 对话 |
| `/api/paper/account` | GET | 模拟盘账户信息 |
| `/api/paper/positions` | GET | 模拟盘持仓 |
| `/api/paper/trade` | POST | 模拟盘交易 |

### 认证

所有 API 端点（除 `/api/health` 外）需要 `X-API-Key` Header：

```bash
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/latest
```

前端自动从配置注入，无需手动处理。

---

## 📁 项目结构

```
crypto-options-aggregator/
├── dashboard/                    # 主应用目录
│   ├── main.py                   # FastAPI 应用入口
│   ├── config.py                 # 配置文件
│   ├── api/                      # API 端点
│   │   ├── health.py             # 健康检查
│   │   ├── scan.py               # 扫描 API
│   │   ├── dvol.py               # DVOL 分析
│   │   ├── trades_api.py         # 交易 API
│   │   ├── paper_trading.py      # 模拟盘 API
│   │   ├── copilot.py            # AI Co-Pilot
│   │   └── mcp.py                # MCP Server
│   ├── services/                 # 业务逻辑层
│   │   ├── datahub.py            # DataHub 实时数据中心
│   │   ├── scan_engine.py        # 扫描引擎
│   │   ├── large_trades_fetcher.py # 大宗交易获取器（从 scan_engine 提取）
│   │   ├── http_client.py        # 统一 HTTP 客户端（httpx 封装）
│   │   ├── dvol_analyzer.py      # DVOL 分析器
│   │   ├── iv_term_structure.py  # IV 期限结构分析器
│   │   ├── paper_trading.py      # 模拟盘引擎
│   │   ├── async_http.py         # 异步 HTTP 客户端
│   │   └── ...
│   ├── db/                       # 数据库层
│   │   ├── schema.py             # 数据库 Schema
│   │   └── connection.py         # 连接管理
│   ├── static/                   # 前端静态文件
│   │   ├── index.html            # 主页面
│   │   ├── app.js                # 主应用逻辑
│   │   └── grid-strategy.js      # 网格策略
│   ├── templates/                # HTML 模板
│   └── test_e2e.py               # E2E 测试 (待实现)
├── README.md                     # 项目文档
├── requirements.txt              # Python 依赖
└── LICENSE                       # MIT 许可证
```

---

## 🧪 测试

### 运行单元测试

```bash
python -m pytest tests/test_core.py -v
```

### 测试覆盖

- **Margin Calculator**: 验证 PUT/CALL 保证金计算、最小值保护、非负约束
- **Black-Scholes**: 验证期权定价、Greeks 计算、边界条件
- **Win Rate**: 验证 OTM/ITM 胜率计算
- **Grid Score**: 验证评分归一化和推荐等级映射
- **Norm CDF**: 验证正态分布累积函数精度

### E2E 测试 (待实现)

> ⚠️ `test_e2e.py` 尚未创建，以下命令待后续补充。

```bash
# cd dashboard
# python test_e2e.py
```

---

## 📝 更新日志

### v5.5 — Greeks 计算修复与异步数据库优化（当前）

#### 核心修复
- **Greeks 计算修复**: 使用 Black-Scholes 模型实时计算 Greeks（Deribit API 不返回 Greeks 数据）
- **Greeks OI 加权**: 风险矩阵使用 Open Interest 加权计算，更准确反映市场真实风险敞口
- **异步数据库调用修复**: 修复 5 个 API 端点中的同步 DB 调用阻塞事件循环问题
  - `api/scan.py` - `get_latest` / `export_csv`
  - `api/refresh.py` - `refresh_dvol`
  - `api/debate.py` - `get_debate_history`
  - `routers/charts.py` - `get_vol_surface`

#### 前端优化
- **IV Smile 渲染修复**: 修复柱状图高度计算导致空白画布的问题
- **Greeks 使用说明**: 添加 Delta/Gamma/Theta/Vega 指标说明文本
- **IV Smile 使用说明**: 添加波动率微笑图表说明和图例

### v5.4 — AI 配置增强与交互优化

#### AI Co-Pilot 增强
- **自定义 API Key 支持**: 前端新增 AI 配置面板，支持输入任意 OpenAI 兼容 API Key（OpenAI / DeepSeek / Claude / Gemini / 小米米莫等）
- **自定义 Base URL**: 支持自建 API 代理或第三方兼容服务
- **自定义模型**: 从下拉选择改为自由输入 + 智能提示，支持任意模型名称
- **配置持久化**: API 配置自动保存到 localStorage，刷新页面不丢失
- **CORS 修复**: 添加 `X-AI-API-Key`、`X-AI-Base-URL`、`X-AI-Model` 到 CORS 允许列表，跨域预检请求不再 405
- **异常处理完善**: 捕获 `AuthenticationError`、`BadRequestError`、`RateLimitError` 等 LiteLLM 异常，避免 500 错误
- **模型前缀自动补全**: 自定义 API 自动添加 `openai/` 前缀，兼容更多服务商

#### 前端优化
- **AI 聊天窗口放大**: 宽度从 320px 增至 480px，高度从 320px 增至 500px
- **字体放大**: 消息字体从 12px 增至 14px，阅读更舒适
- **调试日志**: 前端添加 `[AI Debug]` 日志，方便排查连接问题

### v5.3 — 架构债务清理与 HTTP 库统一

#### 架构优化
- **HTTP 库统一**: 全代码库从 `requests` 迁移到 `httpx`，新增 `services/http_client.py` 统一封装同步/异步客户端，复用 TCP 连接
- **代码去重**: 从 `scan_engine.py` 提取 `large_trades_fetcher.py`，消除 async/sync 双版本函数的 140 行重复代码
- **异常处理规范化**: 替换 186 个裸 `except Exception` 为具体异常类型（`httpx.HTTPError`、`ValueError`、`KeyError` 等），添加结构化日志记录
- **数据库配置统一**: 通过 `DASHBOARD_DB_PATH` 环境变量统一 dashboard 和 Deribit monitor 的数据库路径
- **JSON1 索引优化**: 添加 `top_apr` 和 `contracts_count` 虚拟列及索引，加速 JSON blob 查询

#### 功能修复
- **IV 期限结构 v2.0**: 新增 `/api/charts/vol-surface` 端点，从数据库合约数据计算 ATM IV 期限结构，支持 Backwardation/Contango 判断
- **IV 单位标准化**: `vol-surface` API 自动检测并统一 IV 单位（小数 0.42 → 百分比 42%）
- **SQLite 兼容性**: 修复 `ALTER TABLE ADD COLUMN IF NOT EXISTS` 语法错误（SQLite 不支持），改用 try/except 处理重复列
- **生命周期修复**: 修复 `lifespan` 中 `UnboundLocalError: logger` 错误，确保服务正常启动
- **DVOL 异常值过滤**: 修复 DVOL 趋势图表中异常值 50 的显示问题，增加 `d.dvol < 49` 过滤条件

#### 依赖更新
- `requirements.txt`: 移除 `requests`，统一使用 `httpx>=0.27.0`

### v5.2 — 稳定性与安全性全面提升

#### 前端修复
- **自动刷新保护**: 添加 `_refreshInFlight` 锁，防止接口慢于刷新间隔时并发请求造成 UI 竞态
- **错误处理增强**: `safeFetch()` 现在读取后端 JSON detail，HTTP 错误提示更精准
- **API 失败反馈**: DVOL、Stats、Wind、PCR 等 API 失败时显示"加载失败/数据已过期"，不再保留旧图表误导用户
- **时间解析统一**: 新增 `parseUTC()` 函数，统一处理带 T 和空格格式的 ISO 时间，消除 8 小时时区偏移
- **PCR 图表修复**: 数据为空时先销毁旧 `_pcrChart`，再显示空状态占位，避免过期图表残留
- **期限结构排序**: 按 dte 升序排序后再计算 front/back，防止后端顺序变化导致升贴水判断错误
- **Max Pain 守卫**: 同时检查 `pain_curve` 和 `pain_chart`，避免只返回一种格式时误判无数据
- **CSS 选择器安全**: `presetId` 进入 CSS selector 前使用 `CSS.escape()` 转义，防止异常 ID 导致选择器错误
- **CSP 合规**: 迁移所有 inline onclick 到 `addEventListener`，支持更严格的 Content Security Policy

#### 后端修复
- **IV 单位统一**: 移除 `quick_scan` 和 `calc_delta_bs` 中的双重 `/100`，统一使用百分比单位（IV=50 表示 50%）
- **API Key 注入**: 前端 `safeFetch()` 自动注入 `X-API-Key`，生产环境鉴权无缝衔接
- **CORS 修复**: 替换自定义 middleware 为 FastAPI `CORSMiddleware`，原生支持 OPTIONS preflight，跨域调用不再 405
- **健康检查修复**: 修复 `config` 导入路径，解决 `SCAN_INTERVAL_SECONDS` 属性缺失导致的 503 错误
- **DataHub 启动**: 在 `lifespan` 中启动 DataHub 服务，并在 shutdown 时优雅停止，WebSocket 缓存优化正式生效
- **循环导入解决**: 将 `quick_scan` / `run_options_scan` 等函数迁移到 `services/scan_engine.py`，API 层不再反向 import main
- **数据库索引**: 添加复合索引 `(currency, timestamp DESC)` 和 `(currency, timestamp DESC, notional_usd DESC)`，大单查询性能提升
- **事务原子性**: 模拟盘开仓、交易记录、账户更新改为同一事务提交，避免持仓/交易/现金不一致
- **保证金冻结**: 模拟盘增加 `locked_margin` 计算，按未平仓头寸冻结保证金，防止连续超额开仓
- **事件循环保护**: `mcp_chat()` 使用 `run_in_threadpool` 包装同步 `ai_chat()`，避免阻塞事件循环
- **连接池清理**: `lifespan` shutdown 阶段调用 `close_async_client()`，防止连接池泄漏
- **DB 初始化优化**: 模拟盘数据库初始化移到应用启动阶段，每个请求不再重复执行 DDL
- **参数校验**: `trades_api` 添加 `days` (le=90) 和 `limit` (le=500) 上限，防止大范围扫描导致响应膨胀

#### E2E 测试
- 新增 `test_e2e.py`（待实现），计划覆盖 CORS、认证、健康检查、DataHub、交易 API、模拟盘、IV 计算、扫描引擎、事务管理等 9 个测试用例

### v5.1 — Bug 修复与稳定性提升

- 修复策略计算返回空计划的问题（asyncio 事件循环冲突）
- 添加现货价格异常保护，防止服务端崩溃
- 修复除以零风险，增强参数校验
- Risk Overview 完善 Put Wall / Gamma Flip / Max Pain 数据

### v5.0 — 渐进式重构

- API 模块化：2600+ 行 main.py 拆分为 15 个 api/ 目录模块
- DataHub 实时数据中心：WebSocket 替代轮询
- Exchange Abstraction Layer：统一交易所接口
- Paper Trading Engine：连续模拟盘
- MCP Server + AI Co-Pilot：智能投顾系统

---

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 打开 Pull Request

---

## 📄 许可证

本项目采用 [MIT License](LICENSE) 开源许可证。

---

<p align="center">
  <b>Built with ❤️ for crypto options traders</b><br>
  <a href="https://github.com/roywongx/crypto-options-aggregator">GitHub</a> •
  <a href="https://github.com/roywongx/crypto-options-aggregator/issues">Issues</a> •
  <a href="https://github.com/roywongx/crypto-options-aggregator/discussions">Discussions</a>
</p>
