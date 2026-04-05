# Crypto Options Aggregator & Stress Tester

A professional-grade, dual-platform (Binance + Deribit) crypto options scanner designed for aggressive Sell Put and Covered Call strategies.

This project was built upon the excellent foundation provided by [lianyanshe-ai/deribit-options-monitor](https://github.com/lianyanshe-ai/deribit-options-monitor). We extended the core logic to support Binance, advanced risk modeling, and a unified cross-platform view.

---

## 🌐 Web Dashboard 网页监控面板

除了命令行工具，本项目还提供了一个功能强大的 **Web 监控面板**，基于 FastAPI + 原生 JavaScript 构建，支持实时监控、风险预警和智能修复建议。

### 网页版核心功能

#### 1. 📊 实时监控大屏
- **双平台数据聚合**：Binance + Deribit 实时合约数据
- **宏观指标展示**：现货价格、DVOL 波动率指数、大宗交易监控
- **APR 趋势图表**：支持 24H/7天/30天 历史趋势查看
- **DVOL 趋势图表**：波动率变化趋势分析

#### 2. 🔥 倍投修复计算器 (Recovery Calculator)
当你遇到浮亏时，输入亏损金额，系统自动：
- 扫描当前 IV 最高的远期合约
- 基于目标年化收益率（默认 200%）计算修复方案
- 推荐最优合约组合，显示所需保证金和预期净利润

**使用方法**：在面板顶部输入浮亏金额 → 点击"计算修复方案"

#### 3. ⚠️ 智能风险预警系统
- **自动风险检测**：Delta > 0.45 或 价格接近行权价 2% 时自动标记
- **闪烁红框提醒**：高风险合约行显示红色闪烁边框
- **浏览器通知**：发现高风险时自动推送系统通知
- **风险预警面板**：右侧集中展示所有高风险合约

#### 4. 🔄 滚仓修复建议 (Rolling Alert)
点击任意高风险合约行，弹出智能建议弹窗：
- 显示当前持仓风险详情
- 推荐更低行权价的远期合约作为替代
- 计算预计亏损和替代方案收益
- 一键获取"平仓+新开仓"操作建议

#### 5. 📈 完整 Greeks 展示
合约表格包含完整的 Greeks 数据：
- **Delta**：方向风险
- **Gamma**：加速度风险
- **Vega**：波动率敏感度（高Vega用橙色高亮）
- **APR**：年化收益率
- **-10%亏损**：压力测试结果

#### 6. ⚡ 异步后台扫描
- **非阻塞扫描**：点击扫描后页面不卡顿
- **自动刷新**：支持 5/10/30 分钟自动扫描
- **静默守望模式**：后台自动监控，发现风险立即通知

---

## 🚀 快速开始

### 安装

```bash
git clone https://github.com/roywongx/crypto-options-aggregator.git
cd crypto-options-aggregator
pip install -r requirements.txt
```

### 启动 Web 面板

```bash
cd dashboard
python -c "import main; import uvicorn; uvicorn.run(main.app, host='0.0.0.0', port=8080)"
```

然后访问 http://localhost:8080

---

## 🛠️ 命令行工具使用

### 1. 统一扫描（默认）
查找双平台最佳 Sell Put 机会，DTE 3-30天，Max Delta 0.5：
```bash
python options_aggregator.py
```

### 2. JSON 模式（供 API 调用）
输出结构化 JSON 数据：
```bash
python options_aggregator.py --json
```

### 3. 特定行权价压力测试
卖出指定行权价的 Put 并查看 10% 跌幅压力测试：
```bash
python options_aggregator.py --strike 64000
```

### 4. 备兑看涨（现货生息）
持有 BTC 时卖出看涨期权：
```bash
python options_aggregator.py --option-type CALL --strike 75000
```

### 高级参数
- `--currency`: BTC, ETH, SOL, XRP, BNB, DOGE
- `--min-dte` / `--max-dte`: 到期天数范围
- `--max-delta`: 最大 Delta 过滤（默认 0.5）
- `--strike-range`: 行权价范围（如 `60000-65000`）
- `--margin-ratio`: 保证金比率（默认 0.2 = 20%）
- `--json`: 输出 JSON 格式

---

## ✨ 核心特性

### 双平台整合
- 合并 Deribit（币本位）和 Binance（U本位）数据
- 统一排行榜，便于比较

### 真实资本效率（Margin APR）
使用保证金计算 APR：`Premium / (Strike * Margin_Ratio)`
避免深度实值期权的高估问题

### 风险压力测试
实时计算 Gamma 和 Vega，评估现货价格突然下跌 10% 时的近似浮亏：
```
dPrice = Δ·dSpot + 0.5·Γ·dSpot²
```

### 宏观环境监控
- Deribit 隐含波动率指数 (DVOL)
- 近期机构大宗交易数据

---

## 🏗️ 项目结构

```
crypto-options-aggregator/
├── dashboard/                 # Web 监控面板
│   ├── main.py               # FastAPI 后端
│   ├── static/
│   │   ├── index.html        # 前端页面
│   │   └── app.js            # 前端逻辑
│   └── data/
│       └── monitor.db        # SQLite 数据库
├── deribit-options-monitor/   # Deribit API 模块
├── options_aggregator.py      # 主聚合脚本
├── binance_options.py         # Binance API 模块
└── requirements.txt
```

---

## 🙏 致谢

感谢原作者 **[lianyanshe-ai](https://github.com/lianyanshe-ai/deribit-options-monitor)** 提供的优秀 Deribit API 封装、Greeks 计算和 DVOL/大宗交易逻辑。坚实的架构基础使这些交易增强功能成为可能。

---

## ⚠️ 免责声明

期权交易风险极高。本工具仅供信息参考，不构成投资建议。压力测试计算为近似值，实际盈亏可能有所不同。请根据自身风险承受能力谨慎交易。

---

## 📄 许可证

MIT License
