<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Platform-Binance%20%2B%20Deribit-orange?logo=bitcoin" alt="Platform">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/v2.1-性能优化-blueviolet" alt="Version">
</p>

<h1 align="center">Crypto Options Aggregator</h1>

<p align="center">
  <b>双平台期权扫描器 + 链上数据引擎 + 智能策略系统</b><br>
  实时聚合 Binance + Deribit 期权数据，融合 fuckbtc.com 链上指标<br>
  为 <b>Sell Put / Covered Call / Wheel</b> 策略交易者打造的决策平台
</p>

---

## 🌟 核心功能

### 🔗 链上数据引擎

基于 BTC 筑底信号研究的 7 维指标汇合系统：

| 指标 | 底部信号 | 用途 |
|------|----------|------|
| **MVRV Ratio** | < 1.0 | 估值参考 |
| **MVRV Z-Score** | < -1 | 极端低估 |
| **NUPL** | < 0 | 恐惧区判断 |
| **Mayer Multiple** | < 1.0 | 中期底部 |
| **200WMA** | 价格接近 | 长期趋势 |
| **200DMA** | 价格低于 | 成本基准 |
| **Balanced Price** | 价格低于 | 链上成本 |

综合评分：-70 ~ +70 分，6 级信号判定（STRONG_BOTTOM → TOP）

### 💰 真实 Margin-APR

```
传统 APR = 权利金 / 合约面值  ❌
真实 APR = 权利金 / 实际保证金  ✅
```

### 🌊 DVOL 波动率引擎

自动计算 Z-Score 和历史分位数，动态调整扫描参数。

### 🛡️ 动态风险框架

结合 Max Pain、Gamma Flip、Put Wall，输出实时操作指令。

### 🎯 智能策略引擎

三种模式共享统一筛选器和评分框架：
- **Roll 模式**：滚仓优化，确保 Net Credit > 0
- **New 模式**：新建开仓，综合 ROI/Delta/DTE/流动性评分
- **Grid 模式**：多档位网格，Put/Call 双卖

---

## 📦 快速开始

### 前置要求

- Python 3.13+
- 网络可访问 Binance 和 Deribit API

### 安装

```bash
git clone https://github.com/roywongx/crypto-options-aggregator.git
cd crypto-options-aggregator

# 安装依赖
cd deribit-options-monitor && pip install -r requirements.txt
cd ../dashboard && pip install -r requirements.txt

# 启动服务
python main.py
```

### 访问

打开浏览器访问：http://localhost:8000

### 环境变量

```bash
# 可选：设置 API Key 保护管理面板
export DASHBOARD_API_KEY=your_secret_key
```

---

## 🏗️ 项目结构

```
crypto-options-aggregator/
├── dashboard/                  # Web 控制面板
│   ├── main.py                 # FastAPI 主服务
│   ├── db/                     # SQLite 数据库管理
│   ├── static/                 # 前端静态文件
│   │   ├── index.html          # 主页面
│   │   ├── app.js              # 前端逻辑
│   │   └── style.css           # 样式
│   ├── services/               # 业务逻辑
│   │   ├── spot_price.py       # 现货价格服务
│   │   ├── dvol_analyzer.py    # DVOL 分析
│   │   └── ...
│   ├── routers/                # API 路由
│   ├── models/                 # 数据模型
│   ├── config/                 # 配置模块
│   └── utils/                  # 工具函数
├── deribit-options-monitor/    # Deribit 期权监控
│   └── deribit_options_monitor.py
├── binance_options.py          # Binance 期权数据
├── options_aggregator.py       # 数据聚合器
└── requirements.txt            # 依赖列表
```

---

## 📊 数据源

| 平台 | 数据类型 | API |
|------|----------|-----|
| **Binance** | 期权 Mark/Ticker/OI | eapi.binance.com |
| **Deribit** | 期权摘要/大单/DVOL | www.deribit.com |
| **fuckbtc.com** | 链上指标 (MVRV/NUPL) | api.fuckbtc.com |

---

## ⚡ 性能优化

- O(1) 字典查找替代线性搜索
- ThreadPoolExecutor 并行 OI 请求
- ExchangeInfo 1 小时缓存
- GZIP 响应压缩
- 前端分页渲染（30条/页）
- SQLite WAL 模式 + 60s busy_timeout

---

## 📝 更新日志

### v2.1

- 后端扫描性能优化（O(N)→O(1) + 并行 OI + 缓存）
- Delta 估算精度提升（误差 < 7.5e-8）
- 前端渐进式加载 + 表格分页 + GZIP 压缩
- 代码质量修复（统一日志 + 数据库连接优化）

### v2.0

- 统一策略推荐引擎（Roll/New/Grid）
- 链上数据引擎 v2.0（7 维指标汇合）
- 共享计算模块
- 前端面板整合

---

## 📄 License

MIT License
