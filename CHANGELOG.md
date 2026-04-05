# Changelog

All notable changes to this project will be documented in this file.

## [2026-04-06] — 数据字段修复 + 策略层优化

### 🔧 Fixed: Binance 数据字段错误 (Critical)

**问题**: Binance IV 始终显示 0，OI 显示离谱数值（8626 vs 实际 147.44）

| 字段 | 修复前 | 修复后 | 根因 |
|------|--------|--------|------|
| IV | `mark.get('markIv', ...)` → 返回 **0** | `mark.get('markIV', ...)` → **0.49** ✅ | Binance API 字段名是 **大写 IV**，不是小写 v |
| OI | `ticker['amount']` → **8626** ❌ | `/eapi/v1/openInterest` API → **147.44** ✅ | ticker 没有 openInterest 字段，amount 不是持仓量 |
| OI (备选) | depth bid+ask 合计 → **~137** ❌ | 同上（只覆盖挂单量，不包含未挂单仓位） | depth 只反映盘口可见量 |

**关键发现**: `/eapi/v1/openInterest` 端点的 `expiration` 参数必须用 **YYMMDD 格式**（如 `'260424'`），不是毫秒时间戳。用毫秒时间戳会返回 `-6010` 错误。此格式通过阅读 **ccxt 库源码** 才确认，Binance 官方文档未明确说明。

### 🔧 Fixed: Deribit IV 偏大 100x (Critical)

**问题**: Deribit 合约隐含波动率前端显示 **4750%**（实际应为 ~47.5%）

| 修复前 | 修复后 |
|--------|--------|
| `mark_iv = float(book.get("mark_iv") or 0.0)` → **47.5** | `... / 100.0` → **0.475** ✅ |

**根因**: Binance 返回 decimal 格式 IV（0.47），Deribit 返回 percentage 整数格式 IV（47.5）。前端统一 `(iv * 100) + '%'` 显示，Deribit 需先除以 100。

**影响位置**:
- `deribit_options_monitor.py:787` — 大单扫描流程
- `deribit_options_monitor.py:959` — 推荐合约/book summary 流程

### 🔄 Refactored: options_aggregator.py v4.0 → v4.1

- **移除硬编码** `spot_price = 67200` → 改为从 Deribit/Binance 数据动态提取（当前 ~67670）
- **新增数据校验层** `validate_contract()` — 校验 IV/OI/Delta/Premium 合理性，异常数据输出到 `validation_warnings`
- **新增** `extract_spot_price()` — 多级回退策略（Deribit → Binance 推算 → 0）
- **补充字段映射** — Deribit 合约也映射 `premium_usd` 和 `open_interest`

### 📝 New: crypto-options-api Skill

创建项目专属 skill [`.trae/skills/crypto-options-api/SKILL.md`](.trae/skills/crypto-options-api/SKILL.md)，记录：

- Binance/Deribit 全部端点和字段字典
- 4 个致命陷阱及解决方案
- 调试方法论和常见异常对照表
- 文件索引和前端显示约定

---

## 更早的变更

### UI / Frontend

- 表格布局优化、中文表头
- 字体大小调整、列顺序重排
- 新增 Premium 列

### 后端

- FastAPI dashboard 后端搭建
- 双平台（Binance + Deribit）数据聚合管道
- DVOL 自适应参数调整策略
- 策略预设系统（conservative/standard/aggressive）
