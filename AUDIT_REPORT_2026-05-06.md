# Crypto Options Aggregator — 全面审计报告

**审计日期**: 2026-05-06
**审计范围**: 全部 Python 源码、架构设计、安全性、性能
**项目状态**: 178 tests passing · 53 services · 16 API modules

---

## 一、严重问题 (必须修复)

### S1. API 密钥明文存储在数据库中

- **文件**: `dashboard/services/llm_analyst.py:264-283`
- **问题**: `_get_custom_config()` 从 `llm_config` 表直接读取明文 `api_key`。数据库文件 `monitor.db` 被拷贝即泄露所有 LLM API Key。`save_config()` (第302行) 也将 key 明文写入。
- **修复**: 使用 Fernet 对称加密存储 API Key，读取时解密。

### S2. Config.to_dict() 可能导出交易所密钥

- **文件**: `dashboard/config.py:262-287`
- **问题**: `BINANCE_API_KEY`, `BINANCE_SECRET_KEY`, `DERIBIT_CLIENT_ID`, `DERIBIT_CLIENT_SECRET` 是公开属性。`to_dict()` 未过滤敏感字段，若 `/api/status` 调用此方法将通过 HTTP 泄露密钥。
- **修复**: `to_dict()` 明确过滤包含 `KEY`, `SECRET`, `PASSWORD` 的字段。

### S3. DataHub 读取共享数据无锁 — 竞态条件

- **文件**: `dashboard/services/datahub.py:125-131`
- **问题**: `publish()` 在 `self._lock` 下修改数据，但 `get_snapshot()` 和 `get_options_chain_snapshot()` 读取时无锁。高并发下可能读到不一致的中间状态。
- **修复**: 读操作加锁，或使用 copy-on-write 模式（发布时替换引用而非原地 mutate）。

### S4. BinanceWSConnector 每次轮询新建 httpx.AsyncClient

- **文件**: `dashboard/services/datahub.py:295-313`
- **问题**: 每30秒创建销毁一个连接池，造成 TCP/TLS 开销和潜在 socket 泄漏。
- **修复**: 将 `httpx.AsyncClient` 作为实例属性，connector 停止时关闭。

### S5. create_task 未保存引用，任务可能被 GC 回收

- **文件**: `dashboard/services/datahub.py:148`
- **问题**: `asyncio.create_task(self._cleanup_task())` 返回值未保存，清理任务可能静默停止。
- **修复**: 保存 task 引用到 `self._cleanup_task_handle`，stop() 时 cancel 并 await。

### S6. scan_engine 写入数据库缺少 schema 验证

- **文件**: `dashboard/services/scan_engine.py:80-136`
- **问题**: `parse_trade_alert()` 返回值未经类型验证直接写入数据库。
- **修复**: 增加 Pydantic model 验证写入字段。

### S7. 模拟盘保证金计算逻辑错误

- **文件**: `dashboard/services/paper_trading.py:93-175`
- **问题**: 资金流逻辑不一致 — 权利金加到 cash，保证金隐含在 cash 中而非单独跟踪。开平仓计算方向矛盾。
- **修复**: 重构为 `cash + locked_margin + unrealized_pnl = total_equity` 标准三账户模型。paper_account 表增加 `locked_margin` 字段。

---

## 二、中等问题 (建议修复)

### M1. sys.path.insert 污染导入路径

- **文件**: `dashboard/main.py:38`, `dashboard/services/scan_engine.py:15`
- **问题**: `sys.path.insert(0, ...)` 在多 worker 或测试环境中导致不可预测的导入行为。
- **修复**: 使用标准 Python 包结构 (pyproject.toml)，消除 sys.path.insert。

### M2. quick_scan 函数 246 行 — God Function

- **文件**: `dashboard/services/scan_engine.py:343-588`
- **问题**: 承担 7+ 个职责，难以测试和维护。
- **修复**: 拆分为 `_fetch_spot_from_datahub()`, `_fetch_options_data()`, `_persist_scan_results()` 等。

### M3. IV 期限结构逻辑重复实现

- **文件**: `dashboard/services/scan_engine.py:691-793` vs `dashboard/services/llm_analyst.py:109-154`
- **问题**: 两处几乎相同的 IV 期限结构计算，修改一处易遗漏另一处。
- **修复**: 提取为 `services/iv_term_structure.py` 公共函数。

### M4. RiskFramework 缓存失败时不更新时间戳

- **文件**: `dashboard/services/risk_framework.py:31-56`
- **问题**: 动态支撑位计算失败时回退到静态值但不更新缓存，导致每次调用都重试。且使用 `threading.Lock()` 在异步上下文中可能阻塞事件循环。
- **修复**: 失败时也更新 `_cache_timestamp`；异步上下文使用 `asyncio.Lock()`。

### M5. DeribitWSConnector 重连缺少 jitter

- **文件**: `dashboard/services/datahub.py:178-185`
- **问题**: 指数退避无随机 jitter，多实例同时断开会形成惊群效应。
- **修复**: 添加 `random.uniform(0, delay)` 的 jitter。

### M6. DvolCalculator._iv_samples 死代码

- **文件**: `dashboard/services/datahub.py:343-385`
- **问题**: `_iv_samples` 列表从未被使用或清理，是不完整重构的残留。
- **修复**: 删除该字段。

### M7. _is_local_request 的 testclient 特殊处理可被利用

- **文件**: `dashboard/main.py:158-166`
- **问题**: 空 client_host 或 "testclient" 绕过鉴权。反向代理不设 X-Forwarded-For 时远程请求也能绕过。
- **修复**: 移除 testclient 特殊处理，空 host 在生产模式视为非本地。

### M8. paper_trading 无并发控制

- **文件**: `dashboard/services/paper_trading.py` 全文
- **问题**: 读-改-写非原子，并发请求可能同时通过余额检查导致超卖。
- **修复**: 外层使用 `_write_lock` 或 SQLite `BEGIN IMMEDIATE` 事务。

### M9. CachedStaticFiles 动态导入 re 模块

- **文件**: `dashboard/main.py:238`
- **问题**: `__import__('re')` 在每次请求时动态导入，写法不规范。
- **修复**: 文件顶部 `import re`，方法中直接使用。

### M10. ThreadPoolExecutor 未在关闭时清理

- **文件**: `dashboard/services/scan_engine.py:146`
- **问题**: 模块级线程池在 lifespan 关闭时未 shutdown，可能阻止进程退出。
- **修复**: 在 lifespan finally 块添加 `_scan_executor.shutdown(wait=False)`。

---

## 三、轻微问题 (可选优化)

### L1. DPAPI 解密仅支持 Windows
- **文件**: `dashboard/config.py:23-53`
- **修复**: 添加平台检查，非 Windows 跳过并记录日志。

### L2. 异常捕获模式过于宽泛
- **文件**: `dashboard/services/llm_analyst.py` 多处
- **问题**: `except (RuntimeError, ConnectionError, TimeoutError, Exception)` 中 Exception 已覆盖前面所有。
- **修复**: 只用 `except Exception` 或明确列出具体异常。

### L3. 魔法数字散布各处
- **文件**: `dashboard/services/scan_engine.py:271-291`, `dashboard/services/paper_trading.py:462`
- **修复**: 提取为 `config.py` 常量。

### L4. 模拟盘用线性衰减而非 BS 模型
- **文件**: `dashboard/services/paper_trading.py:441-464`
- **问题**: 每天2%线性衰减，但项目已有完整 Black-Scholes 实现。
- **修复**: 调用 `bs_put_price`/`bs_call_price` 重新计算。

### L5. get_trade_history 硬编码索引访问
- **文件**: `dashboard/services/paper_trading.py:322-339`
- **问题**: `r[0]`, `r[1]` 等硬编码索引，表结构变化时易出错。
- **修复**: 使用 `sqlite3.Row` 字典访问。

### L6. GreeksAnalyzer 权重逻辑冗余
- **文件**: `dashboard/services/greeks_analyzer.py:76-107`
- **问题**: `max(1.0, c["oi"])` 永远不会触发 1.0 回退（上游已过滤 oi<1）。
- **修复**: 简化为 `weight = c["oi"]`。

### L7. ExchangeRegistry 模块级实例化
- **文件**: `dashboard/services/exchange_abstraction.py:1118`
- **问题**: 导入时立即实例化，某交易所依赖不可用会导致整个模块导入失败。
- **修复**: 惰性初始化或工厂模式。

### L8. Bybit/OKX 的 get_dvol 实现相同
- **文件**: `dashboard/services/exchange_abstraction.py:766-771, 960-965`
- **问题**: 都调用 `get_dvol_from_deribit()`，不符合抽象层设计初衷。
- **修复**: 在 BaseExchange 提供默认实现或文档说明。

### L9. _apply_quality_filter 的 margin_ratio 硬编码
- **文件**: `dashboard/services/scan_engine.py:291`
- **问题**: 硬编码 0.2，与 config.py 中的配置不一致。
- **修复**: 作为参数传入。

### L10. GreeksAnalyzer emoji 图标混用
- **文件**: `dashboard/services/greeks_analyzer.py:272`
- **问题**: NEUTRAL 状态用 `"⚖️"` 而其他用英文标识。
- **修复**: 统一风格。

---

## 四、设计改进建议

### D1. 缺乏统一的 Result 类型
函数返回类型混乱：有的返回 `Dict[str, Any]` 带 `success`/`error`，有的抛异常，有的返回 None。建议定义统一 `Result[T]` 泛型。

### D2. DataHub 应使用更高效的并发原语
`publish()` 高频操作时锁成为瓶颈。建议 copy-on-write + `asyncio.Event` 通知。

### D3. 三套策略引擎并存
- `strategy_engine.py` — StrategyEngine
- `unified_strategy_engine.py` — UnifiedStrategyEngine
- `strategy_calc.py` — 旧版计算函数

评分权重、过滤逻辑各不相同，同一数据在不同面板产生不同推荐。建议收敛到一套。

### D4. 缺少结构化日志
建议引入 `structlog`，添加 request_id, currency, latency_ms 等字段。

### D5. 数据库缺少索引
`scan_records` 和 `large_trades_history` 频繁按 `(currency, timestamp)` 查询删除，需创建复合索引。

### D6. K线数据解析 bug
- **文件**: `dashboard/services/exchange_abstraction.py:596-633`
- **问题**: Deribit 返回格式解析完全错误，会产生错误的 K 线数据。
- **修复**: 正确解析 `data["open"][i]`, `data["high"][i]`, `data["low"][i]`, `data["close"][i]`。

### D7. 建议引入依赖注入框架
模块级单例 + 延迟导入导致测试困难、初始化顺序隐晦。

### D8. 缺少 API 限流/熔断保护
- 无请求级限流
- 无外部 API 熔断器
- LLM 调用无并发控制

建议添加 `slowapi` 限流中间件 + `circuitbreaker` 模式。
