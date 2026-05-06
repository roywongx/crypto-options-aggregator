# Crypto Options Aggregator — 第二轮审计报告

**审计日期**: 2026-05-07
**审计范围**: 全部 Python 源码（112 文件，31,086 行）
**上轮状态**: 25/27 已修复，本轮验证修复质量 + 扫描新代码

---

## 一、上轮修复验证

| 文件 | 修复内容 | 验证结果 |
|------|----------|----------|
| `llm_analyst.py` | Fernet 加密 | ✅ 通过 |
| `datahub.py` | 深拷贝 + client 复用 + task 引用 | ✅ 通过 |
| `paper_trading.py` | 三账户模型 | ⚠️ 通过，但发现新问题 C2 |
| `scan_engine.py` | Pydantic 验证 | ✅ 通过 |
| `config.py` | to_dict 过滤 | ✅ 通过 |
| `exchange_abstraction.py` | K 线解析 | ✅ 通过 |

---

## 二、严重问题 (Critical) — 6 个

### C1. 每次调用创建新 ThreadPoolExecutor

- **文件**: `dashboard/services/options_debate_engine.py:98`
- **问题**: `_gather_market_data()` 每次调用都在 `with` 块内创建新线程池，开销大
- **修复**: 提升为模块级全局 executor

### C2. `_get_account()` 索引歧义导致数据错误

- **文件**: `dashboard/services/paper_trading.py:422-423`
- **问题**: 当 `len(r) == 4` 时，`locked_margin` 和 `currency` 都从 `r[3]` 取值，数据错误
- **修复**: 使用列名查询替代 `SELECT *`，通过字典键访问

### C3. 数据库连接泄漏

- **文件**: `dashboard/services/large_trades_fetcher.py:303-307, 355-359`
- **问题**: `cursor.close()` 被调用但 `conn.close()` 从未调用，长时间运行连接池耗尽
- **修复**: 使用 `with` 上下文管理器或 `execute_read()` 封装

### C4. 每次请求创建新 httpx.AsyncClient

- **文件**: `dashboard/services/exchange_abstraction.py:370, 385, 413, 565, 579, 607, 669, 747, 777, 793`
- **问题**: 10+ 处使用 `async with httpx.AsyncClient()` 模式，项目已有共享客户端未使用
- **修复**: 统一使用 `http_client.py` 的 `async_http_get`

### C5. SSL 验证禁用

- **文件**: `dashboard/services/perp_basis_analyzer.py:32, 39, 55`
- **问题**: `verify=False` 禁用 SSL 验证，易受中间人攻击
- **修复**: 移除 `verify=False`，使用默认 SSL 验证

### C6. verify 参数被接受但从未使用

- **文件**: `dashboard/services/api_retry.py:48, 69`
- **问题**: 函数签名接受 `verify` 参数但未传递给底层调用，`verify=False` 实际无效
- **修复**: 传递参数给底层函数或从签名移除

---

## 三、中等问题 (Medium) — 8 个

### M1. macro_data.py 模块级缓存非线程安全

- **文件**: `dashboard/services/macro_data.py:17-23`
- **修复**: 使用 `threading.Lock` 或 `cachetools.TTLCache`

### M2. onchain_metrics.py 类级缓存非线程安全

- **文件**: `dashboard/services/onchain_metrics.py:29-31`
- **修复**: 同 M1

### M3. grid_manager.py 初始化标志非线程安全

- **文件**: `dashboard/services/grid_manager.py:14`
- **修复**: 使用 `threading.Lock` 保护初始化

### M4. portfolio_service.py 每次调用创建新 ThreadPoolExecutor

- **文件**: `dashboard/services/portfolio_service.py:456`
- **修复**: 提升为模块级全局 executor

### M5. quick_scan() 函数过长 (245 行) — 上轮未修复

- **文件**: `dashboard/services/scan_engine.py:375-620`
- **修复**: 拆分为 `_fetch_scan_data()`, `_build_scan_result()`, `_save_scan_data()`

### M6. IV 期限结构逻辑重复 — 上轮未修复

- **文件**: `dashboard/services/scan_engine.py:723-808` vs `dashboard/services/llm_analyst.py:149-194`
- **修复**: 统一使用 `iv_term_structure.py` 的 `IVTermStructureAnalyzer`

### M7. 三个策略引擎共存 — 上轮未修复

- **文件**: `strategy_engine.py` / `unified_strategy_engine.py` / `grid_engine.py`
- **修复**: 以 `unified_strategy_engine.py` 为基础合并

### M8. options_debate_engine.py 模块级缓存非线程安全

- **文件**: `dashboard/services/options_debate_engine.py:29-31`
- **修复**: 使用 `threading.Lock` 保护缓存

---

## 四、轻微问题 (Minor) — 7 个

| # | 文件 | 行号 | 问题 | 修复 |
|---|------|------|------|------|
| L1 | `pressure_test.py` | 18-20 | 冗余 `_norm_cdf` 包装 | 直接调用 `shared_calculations.norm_cdf` |
| L2 | `flow_classifier.py` | 22 | 私有函数被外部导入 | 移除下划线前缀 |
| L3 | `volatility_predictor.py` | 236 | 3% 阈值硬编码 | 提取为类常量 |
| L4 | `martingale_sandbox.py` | 34 | 70% IV 变化率硬编码 | 提取为方法参数 |
| L5 | `perp_basis_analyzer.py` | 47 | 假设 8 小时资金费率周期 | 添加注释或参数化 |
| L6 | `backtest_engine.py` | 20-22 | 策略常量硬编码 | 移入 `BacktestParams` |
| L7 | `param_optimizer.py` | 44-49 | 搜索空间硬编码 | 允许动态生成 |

---

## 五、设计建议 — 8 个

| # | 状态 | 建议 |
|---|------|------|
| D1 | 未修复 | 统一 `ServiceResult` 基类 |
| D2 | 部分修复 | DataHub 使用 copy-on-write 或 readers-writer lock |
| D3 | 未修复 | 三个策略引擎统一（见 M7） |
| D4 | 未修复 | 引入 structlog 结构化日志 |
| D5 | 未修复 | 数据库添加索引 |
| D6 | 未修复 | 引入依赖注入框架 |
| D7 | 未修复 | 添加 API 速率限制和熔断器 |
| D8 | 新发现 | http_client.py 全局客户端未在 shutdown 时清理 |

---

## 六、汇总

| 级别 | 数量 | 状态 |
|------|------|------|
| 严重 (Critical) | 6 | 全部新发现 |
| 中等 (Medium) | 8 | 5 个新发现，3 个上轮遗留 |
| 轻微 (Minor) | 7 | 全部新发现 |
| 设计建议 | 8 | 7 个上轮遗留，1 个新发现 |

**上轮遗留未修复**: M5(quick_scan拆分)、M6(IV重复)、M7(策略引擎统一)、D1-D7
