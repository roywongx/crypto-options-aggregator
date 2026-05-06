# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 常用命令

```bash
# 启动开发服务器（单 worker）
cd dashboard && python main.py

# 运行全部测试
cd dashboard && python -m pytest tests/ -v

# 运行单个测试文件
cd dashboard && python -m pytest tests/test_risk_math.py -v

# 运行单个测试函数
cd dashboard && python -m pytest tests/test_risk_math.py::test_var_calculation -v

# 编译 Tailwind CSS（修改 tailwind-input.css 或 safelist 后）
cd dashboard && npx @tailwindcss/cli -i tailwind-input.css -o static/tailwind-output.css --watch

# 安装依赖
cd dashboard && pip install -r requirements.txt
cd dashboard && npm install    # 仅 tailwindcss 编译用
```

## 架构概览

### 三层数据流

```
Binance REST + Deribit WebSocket → DataHub (Pub/Sub + 内存缓存)
    → 计算层 (Scan Engine · Risk Framework · Greeks · IV)
    → 决策层 (统一推荐引擎 → 17 面板信号灯 + LLM 分析)
    → FastAPI + 静态 SPA (Chart.js + Tailwind CSS v4)
```

### 关键约束

- **必须单 worker 运行**：系统依赖内存单例管理 WebSocket 连接、DataHub 缓存和后台任务，`--workers N > 1` 会导致数据重复和连接异常
- **SQLite WAL 模式**：读写分离 — 读操作复用线程本地只读连接，写操作通过 `_write_lock` 全局序列化
- **API 鉴权**：开发环境 `127.0.0.1`/`localhost` 免验证；生产环境强制 `X-API-Key` header，HMAC 恒定时间比较

### 后端分层

| 层 | 目录 | 职责 |
|---|---|---|
| 路由 | `api/` (14 模块) + `routers/` (5 模块) | HTTP 端点，参数校验，调用 services |
| 服务 | `services/` (47 模块) | 业务逻辑、计算、外部 API 调用 |
| 数据 | `db/` (3 模块) | SQLite 连接池、schema、维护 |
| 配置 | `config.py` | 支持 `.env.enc` (DPAPI) → `.env` → 默认值 三级回退，可动态 `reload()` |

模块延迟导入：大量 `from services.xxx import ...` 写在函数体内而非文件顶部，避免循环导入。

### 前端架构

- **Vanilla JS ES modules**，通过 `window.xxx = xxx` 挂载供 HTML `onclick` 调用
- **`utils.js`** 是核心共享模块：`safeFetch()`（超时重试）、`safeHTML()`、`API_BASE`、`getApiKey()`
- **`safeFetch()` 返回 `Response` 对象**（未解析），调用方需要 `.json()` — `freqtrade.js` 历史上曾缺失此步骤
- **Tailwind v4** 从 `tailwind-input.css` 编译 → `static/tailwind-output.css`。JS 模板字面量中的动态类名必须在 `static/safelist-classes.html` 中声明，否则 JIT 不生成
- **Chart.js** 暗色主题：所有图表重绘前必须 `destroy()` 旧实例
- **推荐系统**：`recommendations.js` 用 `PANEL_TARGETS` 映射 17 个面板到 DOM 选择器，三级递进（信号灯 → 规则报告 → LLM 抽屉 SSE 流式）

### 面板 ID 约定

前端 HTML 中的 section ID 和后端的 `panel_id` 必须一致。当前注册的面板：`metric_cards`, `risk_command_center`, `strategy_center`, `greeks_matrix`, `ai_analyst_center`, `iv_term_structure`, `iv_smile`, `dvol_trend`, `pcr_chart`, `max_pain`, `large_trades`, `martingale_sandbox`, `opportunities_table`, `gex_chart`, `money_flow`, `onchain_metrics`, `derivative_metrics`

### v3.0 Freqtrade 模块（`services/`）

- `portfolio_risk.py` — Delta-Normal VaR (95%), CVaR (×1.25 肥尾), Kelly 仓位, 行权价集中度
- `protections.py` — 6 守卫模式：止损守卫、回撤熔断、连亏冷却、过度交易、VaR、集中度
- `param_optimizer.py` — 网格搜索 + Bayesian 优化 (scikit-optimize GP)，Sortino/Calmar/Sharpe 目标函数
- `backtest_engine.py` — 事件驱动回测，50% 止盈规则
- `volatility_predictor.py` — DVOL 7天/30天预测
- `exchange_abstraction.py` — 多交易所统一接口 (ABC)，当前支持 Binance + Deribit

### Commit 风格

使用 conventional commits，scope 指向变更模块（英文小写）：`fix(wind):`, `refactor(llm):`, `feat(frontend):`, `fix(ui):`, `test:`, `chore:`。消息用英文简短描述。

### 配置安全

`.env` 和 `.env.enc` 已在 `.gitignore`。`.env.example` 是模板文件可以提交。API key 相关的环境变量：`BINANCE_API_KEY`, `BINANCE_SECRET_KEY`, `DERIBIT_CLIENT_ID`, `DERIBIT_CLIENT_SECRET`。
