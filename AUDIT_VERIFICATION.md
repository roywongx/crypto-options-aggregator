# Crypto Options Aggregator - 深度检查报告

本报告旨在识别潜在 Bug、设计缺陷，并为未来发展提供建议。报告采用了结构化的技术语言，便于其他 AI 代理理解和处理。

## 1. 核心 Bug 报告 (高优先级)

| 组件 | 问题描述 | 影响 | 修复建议 |
| :--- | :--- | :--- | :--- |
| `grid_engine.py` | 错误调用 `math.random()` (该方法不存在) | 导致 Monte Carlo 模拟功能在运行时直接崩溃。 | 替换为 `random.random()`，并添加 `import random`。 |
| `main.py` | 重复定义的函数 (`get_spot_price` 等) | 增加了维护难度，可能导致不同模块间逻辑不一致。 | 移除冗余定义，统一调用 `services/spot_price.py`。 |
| `binance_options.py` | 循环中同步获取 OI (未平仓量) | 执行效率低，合约多时容易触发 API 频率限制。 | 使用 `ThreadPoolExecutor` 并发获取多期限的 OI 数据。 |
| `options_aggregator.py` | 依赖 `sys.executable` 执行子进程 | 环境隔离不彻底，且子进程错误信息难以透传至 Web 端。 | 将子脚本重构为模块化类，通过函数直接调用。 |

## 2. 设计不合理/优化点 (中优先级)

*   **架构冗余 (Bloated Backend)**: `main.py` 文件超过 2500 行，集成了 HTML 处理、逻辑计算和路由。建议将其拆分为 FastAPI 的多个 `APIRouter`（如 `scan.py`, `analysis.py`, `grid.py`）。
*   **多源价格不一致**: 不同的脚本 (`binance_options.py`, `deribit_monitor.py`) 拥有各自的价格获取逻辑。应标准化为一个单例 `OracleService`，并引入 1-2 秒的缓存。
*   **轮询效率低**: 目前 Dashboard 采用 `fetch` 轮询方式获取数据，在高频交易场景下延迟较高。建议引入 **WebSocket** 订阅行情和成交大单。
*   **数据库碎片化**: 存在 `monitor.db`, `deribit_monitor.sqlite3`, `options.db` 等多个数据库文件，数据持久化层缺乏统一规划。

## 3. 未来发展与建议

1.  **统一交易所抽象层**: 构建 `AbstractExchange` 基类，标准化不同交易所（Binance, Deribit, OKX）的 Greeks、Ticker 和 OI 获取接口。
2.  **个人仓位风险管理**: 增加 API Key 安全接入功能，实时计算账户整体的 Delta、Gamma、Theta 风险暴露，而不仅仅是扫描市场机会。
3.  **压力测试系统**: 除了简单的 Delta-Gamma 模拟，引入基于 Black-Scholes 的高级压力测试（Vanna, Volga 敏感度分析）。
4.  **AI 驱动的情绪分析**: 利用 LLM 结合历史大宗成交数据，对“异常流向”进行更深层的意图判定（例如：判定是机构对冲还是方向性投机）。
5.  **容器化部署**: 提供 `Dockerfile` 和 `docker-compose.yml`，简化部署流程。

## 4. AI 可读的结构化摘要 (Manifest)

```json
{
  "project": "crypto-options-aggregator",
  "audit_version": "v1.0",
  "detected_vulnerabilities": [
    {"location": "dashboard/services/grid_engine.py", "error": "AttributeError: math.random"}
  ],
  "technical_debt": {
    "monolith_risk": "High (main.py is 2600+ lines)",
    "concurrency_bottleneck": "Binance OI fetching is sequential",
    "consistency_risk": "Multiple redundant spot price providers"
  },
  "roadmap_priorities": [
    "Modularize FastAPI backend",
    "Implement WebSocket for real-time alerts",
    "Standardize exchange interface"
  ]
}
```
