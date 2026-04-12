# 交接提示词（直接复制发给下一个AI）

## 复制以下内容 =====================================

你好！请帮我改造 crypto-options-aggregator 项目的架构，目标是从简单的期权扫描器变成一个 Sell Put / Sell Call 网格策略规划工具。

请先读 `/tmp/crypto-options-aggregator/docs/GRID_STRATEGY_IMPROVEMENT_PLAN.md`，这是完整实施方案（v2版，含竞品分析和bug清单）。

**核心规则**：
1. 必须严格按 Phase 顺序执行，Phase 1 没做完不许碰后面的
2. 每个 Phase 完成后必须跑验证清单，全部打勾才进下一 Phase
3. 标 ✅ 必须代码实际已改，不要虚报。Hermes 会逐行验证 diff
4. 每完成一个 Phase 就 git commit + push
5. Phase 1 结束后 main.py 必须 ≤ 300 行
6. Bug B1-B8 必须在 Phase 1-2 全部修复
7. 不要换前端框架、不要加 TypeScript、不要加 Docker、不要做登录系统

**第一步**：执行 Phase 1 — 拆分 dashboard/main.py（当前2636行），按方案中的步骤1-9逐个迁移模块到对应文件。同时修复 Bug B1（删除 _estimate_delta）和 B4（统一 _parse_inst_name）。

完成 Phase 1 后用 `wc -l dashboard/main.py` 确认 ≤ 300 行，然后跑验证清单，通过后 commit + push。

## 复制结束 =====================================
