# crypto-options-aggregator 问题分析与修复指南

**创建时间：** 2026年4月12日  
**分析人：** Hermes Agent  
**状态：** 待修复

---

## 📊 **项目现状分析**

### **已完成的工作**
1. ✅ 代码结构拆分：main.py 从 2636 行减少到 991 行
2. ✅ 创建了网格策略引擎 (`grid_engine.py`)
3. ✅ 创建了网格策略API端点 (`routers/grid.py`)
4. ✅ 前端有了网格策略Tab的基础框架
5. ✅ 创建了风险框架、DVOL分析器等服务

### **主要问题发现**

#### **1. 自动刷新失效问题**
**问题描述：** 用户反映自动刷新不生效，修了几次都失败。

**根本原因：**
- `setAutoRefresh(5)` 函数存在，但 `loadLatestData()` 函数可能没有正确更新UI
- 缺少网络状态检测，离线时刷新会静默失败
- 没有错误重试机制，API失败后不会自动恢复

**具体表现：**
```javascript
// 当前实现
function setAutoRefresh(minutes) {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
    }
    if (minutes > 0) {
        autoRefreshInterval = setInterval(() => { 
            loadLatestData(); 
            showAlert('数据已刷新（缓存）', 'info'); 
        }, minutes * 60 * 1000);
    }
}

// 问题：loadLatestData() 可能没有正确处理错误
```

#### **2. 预警日志毫无作用**
**问题描述：** 预警日志功能存在，但用户觉得毫无作用。

**根本原因：**
1. **没有持久化存储**：预警只是显示在页面上，刷新就丢失
2. **没有历史记录**：无法查看历史预警
3. **没有预警分类**：所有预警混在一起，无法筛选
4. **没有预警统计**：无法了解预警频率和类型分布
5. **没有预警行动建议**：只是显示信息，没有具体的应对建议

**当前实现分析：**
```javascript
function showAlert(message, type = 'info') {
    // 只是显示，没有持久化
    alertQueue.push({m:message, t:type, time:Date.now()});
    alertQueue = alertQueue.filter(a => Date.now() - a.time < 3000).slice(-3);
    // 3秒后自动消失，没有历史记录
}
```

#### **3. 网格策略前端实现不完整**
**问题描述：** 前端有网格策略Tab，但实际功能不完整。

**具体问题：**
1. **缺少关键JavaScript函数**：没有 `loadGridStrategy`、`updateGridDisplay`、`renderGridChart` 等函数
2. **没有调用网格API**：前端代码中找不到调用 `/api/grid/recommend` 的逻辑
3. **没有数据展示**：网格策略Tab只有HTML结构，没有数据填充逻辑
4. **没有交互功能**：没有事件监听器处理用户操作

#### **4. 对比主流竞品的功能差距**

**主流竞品功能对比：**

| 功能 | Deribit | Greeks.live | OptionStrat | 本项目 |
|------|---------|-------------|-------------|--------|
| 实时期权数据 | ✅ | ✅ | ✅ | ✅ |
| Payoff图可视化 | ✅ | ✅ | ✅ | ❌ 缺少 |
| 多档位网格推荐 | ❌ | ❌ | ❌ | ✅ 有优势 |
| 情景模拟分析 | ✅ | ✅ | ✅ | ⚠️ 部分实现 |
| 风险热力图 | ✅ | ✅ | ✅ | ⚠️ 部分实现 |
| 自动策略建议 | ❌ | ✅ | ✅ | ❌ 缺少 |
| 仓位管理 | ✅ | ✅ | ✅ | ❌ 缺少 |
| 历史回测 | ✅ | ❌ | ✅ | ❌ 缺少 |
| 实时预警 | ✅ | ✅ | ✅ | ⚠️ 基础实现 |

**最大差距：**
1. **Payoff可视化**：这是期权分析的核心功能，本项目完全缺失
2. **多档位网格推荐**：这是本项目的差异化优势，但前端没有展示
3. **Wheel ROI计算**：用户需要计算Sell Put被行权后转Sell Call的收益

---

## 🔧 **具体修复方案**

### **修复1：自动刷新问题**

**文件：** `dashboard/static/app.js`

**修复步骤：**

1. **增强 `loadLatestData` 函数：**
```javascript
async function loadLatestData() {
    try {
        const currency = document.getElementById('currencySelect').value;
        
        // 添加网络状态检测
        if (!navigator.onLine) {
            showAlert('网络连接已断开，刷新失败', 'error');
            return;
        }
        
        const response = await safeFetch(`${API_BASE}/api/latest?currency=${currency}`);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const data = await response.json();
        currentData = data;
        if (data.spot_price) currentSpotPrice = data.spot_price;
        
        updateMacroIndicators(data);
        if (data.dvol_interpretation || data.dvol_trend_label) {
            showDvolAdvice(data.currency || 'BTC');
        }
        
        // 更新网格策略数据
        await loadGridStrategyData();
        
        showAlert('数据刷新成功', 'success');
        
    } catch (error) {
        console.error('数据刷新失败:', error);
        showAlert(`数据刷新失败: ${error.message}`, 'error');
        
        // 错误重试机制
        setTimeout(() => {
            if (navigator.onLine) {
                loadLatestData();
            }
        }, 5000);
    }
}
```

2. **添加网络状态监听：**
```javascript
// 在页面加载时添加
window.addEventListener('online', () => {
    showAlert('网络连接已恢复', 'success');
    loadLatestData();
});

window.addEventListener('offline', () => {
    showAlert('网络连接已断开', 'warning');
});
```

3. **改进自动刷新函数：**
```javascript
function setAutoRefresh(minutes) {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
    }
    
    if (minutes > 0) {
        autoRefreshInterval = setInterval(async () => {
            if (navigator.onLine) {
                await loadLatestData();
            } else {
                showAlert('网络断开，跳过自动刷新', 'warning');
            }
        }, minutes * 60 * 1000);
        
        showAlert(`已设置 ${minutes} 分钟自动刷新`, 'info');
    }
}
```

### **修复2：预警日志系统**

**新建文件：** `dashboard/services/alert_manager.py`

```python
"""
预警管理器
提供持久化、分类、统计的预警系统
"""
import json
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from enum import Enum

class AlertLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

class AlertType(Enum):
    PRICE = "price"
    VOLATILITY = "volatility"
    POSITION = "position"
    RISK = "risk"
    SYSTEM = "system"

class AlertManager:
    def __init__(self, db_path: str = "alerts.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                level TEXT NOT NULL,
                type TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT,
                acknowledged BOOLEAN DEFAULT FALSE,
                action_taken TEXT,
                resolved BOOLEAN DEFAULT FALSE
            )
        """)
        
        conn.commit()
        conn.close()
    
    def create_alert(self, level: AlertLevel, alert_type: AlertType, 
                    message: str, details: Dict = None) -> int:
        """创建新预警"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO alerts (level, type, message, details)
            VALUES (?, ?, ?, ?)
        """, (level.value, alert_type.value, message, 
              json.dumps(details) if details else None))
        
        alert_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return alert_id
    
    def get_alerts(self, level: AlertLevel = None, alert_type: AlertType = None,
                  hours: int = 24, limit: int = 100) -> List[Dict]:
        """获取预警列表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        query = """
            SELECT id, timestamp, level, type, message, details, 
                   acknowledged, action_taken, resolved
            FROM alerts
            WHERE timestamp > datetime('now', ? || ' hours')
        """
        params = [str(-hours)]
        
        if level:
            query += " AND level = ?"
            params.append(level.value)
        
        if alert_type:
            query += " AND type = ?"
            params.append(alert_type.value)
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(str(limit))
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        alerts = []
        for row in rows:
            alerts.append({
                "id": row[0],
                "timestamp": row[1],
                "level": row[2],
                "type": row[3],
                "message": row[4],
                "details": json.loads(row[5]) if row[5] else None,
                "acknowledged": bool(row[6]),
                "action_taken": row[7],
                "resolved": bool(row[8])
            })
        
        conn.close()
        return alerts
    
    def acknowledge_alert(self, alert_id: int, action_taken: str = None):
        """确认预警"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE alerts 
            SET acknowledged = TRUE, action_taken = ?
            WHERE id = ?
        """, (action_taken, alert_id))
        
        conn.commit()
        conn.close()
    
    def get_alert_stats(self, hours: int = 24) -> Dict[str, Any]:
        """获取预警统计"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 按级别统计
        cursor.execute("""
            SELECT level, COUNT(*) as count
            FROM alerts
            WHERE timestamp > datetime('now', ? || ' hours')
            GROUP BY level
        """, (str(-hours),))
        
        level_stats = {row[0]: row[1] for row in cursor.fetchall()}
        
        # 按类型统计
        cursor.execute("""
            SELECT type, COUNT(*) as count
            FROM alerts
            WHERE timestamp > datetime('now', ? || ' hours')
            GROUP BY type
        """, (str(-hours),))
        
        type_stats = {row[0]: row[1] for row in cursor.fetchall()}
        
        # 总数
        cursor.execute("""
            SELECT COUNT(*) as total
            FROM alerts
            WHERE timestamp > datetime('now', ? || ' hours')
        """, (str(-hours),))
        
        total = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "total": total,
            "by_level": level_stats,
            "by_type": type_stats,
            "hours": hours
        }
```

**前端修改：**

1. **添加预警历史界面：**
```html
<!-- 在 index.html 中添加 -->
<section id="alertHistory" class="card-glass rounded-xl p-5 mb-6">
    <div class="flex items-center justify-between mb-4">
        <h3 class="font-semibold text-lg">预警历史</h3>
        <div class="flex gap-2">
            <select id="alertLevelFilter" class="input-dark rounded px-2 py-1 text-sm">
                <option value="">所有级别</option>
                <option value="info">信息</option>
                <option value="warning">警告</option>
                <option value="error">错误</option>
                <option value="critical">严重</option>
            </select>
            <select id="alertTypeFilter" class="input-dark rounded px-2 py-1 text-sm">
                <option value="">所有类型</option>
                <option value="price">价格</option>
                <option value="volatility">波动率</option>
                <option value="position">仓位</option>
                <option value="risk">风险</option>
            </select>
            <button onclick="loadAlertHistory()" class="bg-blue-500 hover:bg-blue-600 text-white px-3 py-1 rounded text-sm">
                刷新
            </button>
        </div>
    </div>
    
    <div id="alertStats" class="grid grid-cols-4 gap-4 mb-4">
        <!-- 统计数据 -->
    </div>
    
    <div id="alertHistoryList" class="space-y-2 max-h-96 overflow-y-auto">
        <!-- 预警列表 -->
    </div>
</section>
```

2. **添加JavaScript函数：**
```javascript
// 预警历史管理
async function loadAlertHistory() {
    try {
        const level = document.getElementById('alertLevelFilter').value;
        const type = document.getElementById('alertTypeFilter').value;
        
        let url = `${API_BASE}/api/alerts?hours=24`;
        if (level) url += `&level=${level}`;
        if (type) url += `&type=${type}`;
        
        const response = await safeFetch(url);
        const data = await response.json();
        
        updateAlertStats(data.stats);
        updateAlertHistoryList(data.alerts);
    } catch (error) {
        console.error('加载预警历史失败:', error);
    }
}

function updateAlertStats(stats) {
    const statsDiv = document.getElementById('alertStats');
    statsDiv.innerHTML = `
        <div class="bg-gray-800/50 p-3 rounded-lg text-center">
            <div class="text-2xl font-bold">${stats.total}</div>
            <div class="text-xs text-gray-400">总预警数</div>
        </div>
        <div class="bg-red-500/10 p-3 rounded-lg text-center">
            <div class="text-2xl font-bold text-red-400">${stats.by_level.error || 0}</div>
            <div class="text-xs text-gray-400">错误</div>
        </div>
        <div class="bg-yellow-500/10 p-3 rounded-lg text-center">
            <div class="text-2xl font-bold text-yellow-400">${stats.by_level.warning || 0}</div>
            <div class="text-xs text-gray-400">警告</div>
        </div>
        <div class="bg-blue-500/10 p-3 rounded-lg text-center">
            <div class="text-2xl font-bold text-blue-400">${stats.by_level.info || 0}</div>
            <div class="text-xs text-gray-400">信息</div>
        </div>
    `;
}

function updateAlertHistoryList(alerts) {
    const listDiv = document.getElementById('alertHistoryList');
    
    if (alerts.length === 0) {
        listDiv.innerHTML = '<div class="text-center text-gray-500 py-8">暂无预警记录</div>';
        return;
    }
    
    listDiv.innerHTML = alerts.map(alert => {
        const time = new Date(alert.timestamp).toLocaleTimeString('zh-CN', {
            hour: '2-digit',
            minute: '2-digit'
        });
        
        const levelColors = {
            info: 'border-blue-500 bg-blue-500/10',
            warning: 'border-yellow-500 bg-yellow-500/10',
            error: 'border-red-500 bg-red-500/10',
            critical: 'border-red-700 bg-red-700/10'
        };
        
        return `
            <div class="border-l-4 ${levelColors[alert.level]} p-3 rounded-lg">
                <div class="flex justify-between items-start">
                    <div>
                        <div class="text-sm font-medium">${alert.message}</div>
                        <div class="text-xs text-gray-400">${time} · ${alert.type}</div>
                    </div>
                    <button onclick="acknowledgeAlert(${alert.id})" 
                            class="text-xs bg-gray-700 hover:bg-gray-600 px-2 py-1 rounded">
                        ${alert.acknowledged ? '已处理' : '确认'}
                    </button>
                </div>
                ${alert.details ? `<div class="text-xs text-gray-500 mt-1">${JSON.stringify(alert.details)}</div>` : ''}
            </div>
        `;
    }).join('');
}
```

### **修复3：网格策略前端实现**

**新建文件：** `dashboard/static/grid-strategy.js`

```javascript
/**
 * 网格策略前端实现
 */

// 全局变量
let gridData = null;
let gridChart = null;

// 初始化网格策略
async function initGridStrategy() {
    console.log('初始化网格策略...');
    
    // 绑定事件
    document.getElementById('gridCurrency').addEventListener('change', loadGridStrategy);
    document.getElementById('gridPutCount').addEventListener('change', loadGridStrategy);
    document.getElementById('gridCallCount').addEventListener('change', loadGridStrategy);
    
    // 初始加载
    await loadGridStrategy();
}

// 加载网格策略数据
async function loadGridStrategy() {
    try {
        const currency = document.getElementById('gridCurrency').value;
        const putCount = document.getElementById('gridPutCount').value;
        const callCount = document.getElementById('gridCallCount').value;
        
        showLoading('加载网格策略...');
        
        // 调用网格推荐API
        const response = await safeFetch(
            `${API_BASE}/api/grid/recommend?currency=${currency}&put_count=${putCount}&call_count=${callCount}`
        );
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        gridData = await response.json();
        
        // 更新显示
        updateGridDisplay(gridData);
        renderGridChart(gridData);
        
        hideLoading();
        
    } catch (error) {
        console.error('加载网格策略失败:', error);
        showAlert(`网格策略加载失败: ${error.message}`, 'error');
        hideLoading();
    }
}

// 更新网格显示
function updateGridDisplay(data) {
    // 更新Put档位
    const putLevelsDiv = document.getElementById('putLevels');
    if (putLevelsDiv && data.put_levels) {
        putLevelsDiv.innerHTML = data.put_levels.map(level => `
            <div class="bg-gray-800/50 p-3 rounded-lg border-l-4 border-green-500">
                <div class="flex justify-between items-center">
                    <div>
                        <div class="text-sm font-medium">Sell Put</div>
                        <div class="text-lg font-bold">${level.strike}</div>
                        <div class="text-xs text-gray-400">${level.dte}天 · Delta ${level.delta}</div>
                    </div>
                    <div class="text-right">
                        <div class="text-green-400 font-bold">${level.apr}% APR</div>
                        <div class="text-xs text-gray-400">$${level.premium_usd}</div>
                    </div>
                </div>
                <div class="mt-2 text-xs ${getRecommendationColor(level.recommendation)}">
                    ${level.reason}
                </div>
            </div>
        `).join('');
    }
    
    // 更新Call档位
    const callLevelsDiv = document.getElementById('callLevels');
    if (callLevelsDiv && data.call_levels) {
        callLevelsDiv.innerHTML = data.call_levels.map(level => `
            <div class="bg-gray-800/50 p-3 rounded-lg border-l-4 border-blue-500">
                <div class="flex justify-between items-center">
                    <div>
                        <div class="text-sm font-medium">Sell Call</div>
                        <div class="text-lg font-bold">${level.strike}</div>
                        <div class="text-xs text-gray-400">${level.dte}天 · Delta ${level.delta}</div>
                    </div>
                    <div class="text-right">
                        <div class="text-blue-400 font-bold">${level.apr}% APR</div>
                        <div class="text-xs text-gray-400">$${level.premium_usd}</div>
                    </div>
                </div>
                <div class="mt-2 text-xs ${getRecommendationColor(level.recommendation)}">
                    ${level.reason}
                </div>
            </div>
        `).join('');
    }
    
    // 更新信号信息
    const signalDiv = document.getElementById('gridSignal');
    if (signalDiv) {
        signalDiv.innerHTML = `
            <div class="bg-gray-800/50 p-4 rounded-lg">
                <div class="text-sm text-gray-400 mb-2">波动率方向信号</div>
                <div class="text-lg font-bold ${getSignalColor(data.dvol_signal)}">${data.dvol_signal}</div>
                <div class="text-xs text-gray-500">推荐比例: ${data.recommended_ratio}</div>
                <div class="text-xs text-gray-500">潜在总权利金: $${data.total_potential_premium}</div>
            </div>
        `;
    }
}

// 渲染网格图表
function renderGridChart(data) {
    const ctx = document.getElementById('gridChart').getContext('2d');
    
    if (gridChart) {
        gridChart.destroy();
    }
    
    // 准备图表数据
    const strikes = [];
    const putAprs = [];
    const callAprs = [];
    const putDistances = [];
    const callDistances = [];
    
    if (data.put_levels) {
        data.put_levels.forEach(level => {
            strikes.push(level.strike);
            putAprs.push(level.apr);
            putDistances.push(level.distance_pct);
        });
    }
    
    if (data.call_levels) {
        data.call_levels.forEach(level => {
            if (!strikes.includes(level.strike)) {
                strikes.push(level.strike);
            }
            callAprs.push(level.apr);
            callDistances.push(Math.abs(level.distance_pct));
        });
    }
    
    // 排序
    strikes.sort((a, b) => a - b);
    
    gridChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: strikes.map(s => s.toLocaleString()),
            datasets: [
                {
                    label: 'Put APR %',
                    data: strikes.map(strike => {
                        const level = data.put_levels?.find(l => l.strike === strike);
                        return level ? level.apr : 0;
                    }),
                    backgroundColor: 'rgba(34, 197, 94, 0.5)',
                    borderColor: 'rgba(34, 197, 94, 1)',
                    borderWidth: 1
                },
                {
                    label: 'Call APR %',
                    data: strikes.map(strike => {
                        const level = data.call_levels?.find(l => l.strike === strike);
                        return level ? level.apr : 0;
                    }),
                    backgroundColor: 'rgba(59, 130, 246, 0.5)',
                    borderColor: 'rgba(59, 130, 246, 1)',
                    borderWidth: 1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                title: {
                    display: true,
                    text: '网格策略 APR 分布'
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            return `${context.dataset.label}: ${context.parsed.y}%`;
                        }
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'APR %'
                    }
                },
                x: {
                    title: {
                        display: true,
                        text: '行权价'
                    }
                }
            }
        }
    });
}

// 辅助函数
function getRecommendationColor(recommendation) {
    const colors = {
        'BEST': 'text-green-400',
        'GOOD': 'text-blue-400',
        'OK': 'text-yellow-400',
        'CAUTION': 'text-orange-400',
        'SKIP': 'text-red-400'
    };
    return colors[recommendation] || 'text-gray-400';
}

function getSignalColor(signal) {
    const colors = {
        'FAVOR_PUT': 'text-green-400',
        'FAVOR_CALL': 'text-blue-400',
        'NEUTRAL': 'text-yellow-400'
    };
    return colors[signal] || 'text-gray-400';
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    // 延迟初始化，确保其他组件加载完成
    setTimeout(initGridStrategy, 1000);
});
```

**在 index.html 中添加：**
```html
<!-- 网格策略部分 -->
<section id="gridStrategySection" class="card-glass rounded-xl p-5 mb-6 border-l-4 border-purple-500">
    <div class="flex items-center gap-2 mb-4">
        <i class="fas fa-th text-purple-500"></i>
        <h3 class="font-semibold text-lg">网格策略引擎</h3>
        <span class="text-xs text-gray-400 ml-2">智能推荐最优Put/Call网格配置</span>
    </div>
    
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
        <div>
            <label class="block text-gray-400 text-xs mb-1.5">币种</label>
            <select id="gridCurrency" class="input-dark w-full rounded-lg px-3 py-2 text-sm">
                <option value="BTC">BTC</option>
                <option value="ETH">ETH</option>
                <option value="SOL">SOL</option>
            </select>
        </div>
        <div>
            <label class="block text-gray-400 text-xs mb-1.5">Put 数量</label>
            <input type="number" id="gridPutCount" value="5" min="1" max="10" 
                   class="input-dark w-full rounded-lg px-3 py-2 text-sm">
        </div>
        <div>
            <label class="block text-gray-400 text-xs mb-1.5">Call 数量</label>
            <input type="number" id="gridCallCount" value="3" min="1" max="10" 
                   class="input-dark w-full rounded-lg px-3 py-2 text-sm">
        </div>
    </div>
    
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <div>
            <h4 class="text-sm font-semibold text-green-400 mb-2">Put 档位</h4>
            <div id="putLevels" class="space-y-2 max-h-64 overflow-y-auto">
                <!-- Put档位将在这里显示 -->
            </div>
        </div>
        <div>
            <h4 class="text-sm font-semibold text-blue-400 mb-2">Call 档位</h4>
            <div id="callLevels" class="space-y-2 max-h-64 overflow-y-auto">
                <!-- Call档位将在这里显示 -->
            </div>
        </div>
    </div>
    
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div>
            <h4 class="text-sm font-semibold text-gray-300 mb-2">APR 分布图</h4>
            <div class="h-64">
                <canvas id="gridChart"></canvas>
            </div>
        </div>
        <div>
            <h4 class="text-sm font-semibold text-gray-300 mb-2">信号与建议</h4>
            <div id="gridSignal">
                <!-- 信号信息将在这里显示 -->
            </div>
        </div>
    </div>
</section>
```

### **修复4：添加缺失的API端点**

**在 `routers/grid.py` 中添加：**

```python
@router.get("/presets")
async def get_grid_presets():
    """获取网格策略预设配置"""
    return {
        "presets": [
            {
                "name": "保守型",
                "description": "低风险，稳定收益",
                "put_count": 3,
                "call_count": 2,
                "min_dte": 14,
                "max_dte": 30,
                "min_apr": 20.0
            },
            {
                "name": "均衡型",
                "description": "平衡风险与收益",
                "put_count": 5,
                "call_count": 3,
                "min_dte": 7,
                "max_dte": 45,
                "min_apr": 15.0
            },
            {
                "name": "激进型",
                "description": "高风险，高收益",
                "put_count": 7,
                "call_count": 5,
                "min_dte": 7,
                "max_dte": 60,
                "min_apr": 10.0
            }
        ]
    }
```

---

## 📋 **修复优先级与验证**

### **第一优先级（1-2天）**
1. **修复自动刷新问题**
   - 验证：设置5分钟自动刷新，观察是否正常工作
   - 测试：断网后重连，验证恢复机制

2. **修复预警日志系统**
   - 验证：创建预警，刷新页面后仍存在
   - 测试：筛选、确认功能正常

### **第二优先级（3-5天）**
3. **完善网格策略前端**
   - 验证：网格策略Tab正常显示数据
   - 测试：图表、交互功能正常

4. **添加缺失的API端点**
   - 验证：所有API端点正常响应
   - 测试：参数验证、错误处理

### **第三优先级（1-2周）**
5. **添加 Payoff 可视化**
   - 验证：正确显示盈亏图
   - 测试：不同策略的对比

6. **添加 Wheel ROI 计算**
   - 验证：计算 Sell Put 被行权后转 Sell Call 的收益
   - 测试：不同场景的模拟

---

## 🎯 **验证标准**

### **自动刷新验证**
- [ ] 设置自动刷新后，定时更新数据
- [ ] 网络断开时显示警告，不静默失败
- [ ] 网络恢复后自动重试
- [ ] 错误信息清晰明确

### **预警日志验证**
- [ ] 预警持久化存储，刷新不丢失
- [ ] 可以按级别、类型筛选
- [ ] 可以确认和处理预警
- [ ] 统计信息正确显示

### **网格策略验证**
- [ ] 前端正确显示网格档位
- [ ] 图表正确渲染 APR 分布
- [ ] 信号和建议清晰易懂
- [ ] 用户交互响应正常

### **API端点验证**
- [ ] 所有端点正常响应
- [ ] 参数验证正确
- [ ] 错误处理完善
- [ ] 响应格式一致

---

## 💬 **给AI的指导**

### **当前状态**
你已经完成了代码结构拆分和部分后端实现，但前端实现不完整，特别是网格策略、自动刷新、预警日志等功能。

### **具体问题**
1. **自动刷新失效**：`loadLatestData()` 函数可能没有正确处理错误和更新UI
2. **预警日志无用**：只是显示，没有持久化、历史记录、分类统计
3. **网格策略前端缺失**：有HTML结构，但缺少JavaScript实现
4. **API端点不完整**：前端调用的某些端点后端不存在

### **修复步骤**

#### **第一步：修复自动刷新（1天）**
1. 修改 `app.js` 中的 `loadLatestData()` 函数，添加错误处理和网络检测
2. 添加网络状态监听器
3. 改进 `setAutoRefresh()` 函数，添加错误重试机制
4. 测试：设置5分钟自动刷新，观察是否正常工作

#### **第二步：修复预警日志（1天）**
1. 创建 `alert_manager.py` 服务
2. 添加API端点 `/api/alerts`
3. 在前端添加预警历史界面
4. 修改 `showAlert()` 函数，调用API持久化预警
5. 测试：创建预警，刷新页面后验证是否仍然存在

#### **第三步：完善网格策略前端（2天）**
1. 创建 `grid-strategy.js` 文件
2. 实现 `loadGridStrategy()`、`updateGridDisplay()`、`renderGridChart()` 函数
3. 在 `index.html` 中添加网格策略部分
4. 在 `app.js` 中初始化网格策略
5. 测试：网格策略Tab正常显示数据和图表

#### **第四步：添加缺失的API端点（1天）**
1. 在 `routers/grid.py` 中添加 `/presets` 端点
2. 检查所有前端调用的API，确保后端都存在
3. 测试：所有API端点正常响应

### **验证方法**
每个修复完成后，按照上述验证标准进行测试。确保：
1. 功能正常工作
2. 错误处理完善
3. 用户体验良好
4. 代码质量良好

### **注意事项**
1. **不要破坏现有功能**：修改前先备份
2. **逐步验证**：每完成一个修复就测试
3. **错误处理**：所有API调用都要有错误处理
4. **用户体验**：提供清晰的反馈和提示

### **完成标准**
所有修复完成，验证标准全部通过，用户不再抱怨自动刷新和预警日志问题。

---

**最后更新：** 2026年4月12日  
**文档版本：** v1.0  
**分析人：** Hermes Agent