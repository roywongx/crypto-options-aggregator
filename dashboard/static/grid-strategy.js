/**
 * 网格策略前端实现
 * v8.0: 完整的网格策略展示和交互
 */

// 全局变量
let gridData = null;
let gridChart = null;

// 初始化网格策略
async function initGridStrategy() {
    console.log('初始化网格策略...');
    
    // 绑定事件
    const gridCurrency = document.getElementById('gridCurrency');
    const gridPutCount = document.getElementById('gridPutCount');
    const gridCallCount = document.getElementById('gridCallCount');
    
    if (gridCurrency) gridCurrency.addEventListener('change', loadGridStrategy);
    if (gridPutCount) gridPutCount.addEventListener('change', loadGridStrategy);
    if (gridCallCount) gridCallCount.addEventListener('change', loadGridStrategy);
    
    // 初始加载
    await loadGridStrategy();
}

// 加载网格策略数据
async function loadGridStrategy() {
    try {
        const currencyEl = document.getElementById('gridCurrency');
        const putCountEl = document.getElementById('gridPutCount');
        const callCountEl = document.getElementById('gridCallCount');
        
        if (!currencyEl) return;
        
        const currency = currencyEl.value;
        const putCount = putCountEl ? putCountEl.value : 5;
        const callCount = callCountEl ? callCountEl.value : 3;
        
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
                        <div class="text-lg font-bold">$${level.strike.toLocaleString()}</div>
                        <div class="text-xs text-gray-400">${level.dte}天 · Delta ${level.delta}</div>
                    </div>
                    <div class="text-right">
                        <div class="text-green-400 font-bold">${level.apr}% APR</div>
                        <div class="text-xs text-gray-400">$${level.premium_usd}</div>
                    </div>
                </div>
                <div class="mt-2 text-xs ${getRecommendationColor(level.recommendation)}">
                    ${level.reason || '推荐'}
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
                        <div class="text-lg font-bold">$${level.strike.toLocaleString()}</div>
                        <div class="text-xs text-gray-400">${level.dte}天 · Delta ${level.delta}</div>
                    </div>
                    <div class="text-right">
                        <div class="text-blue-400 font-bold">${level.apr}% APR</div>
                        <div class="text-xs text-gray-400">$${level.premium_usd}</div>
                    </div>
                </div>
                <div class="mt-2 text-xs ${getRecommendationColor(level.recommendation)}">
                    ${level.reason || '推荐'}
                </div>
            </div>
        `).join('');
    }
    
    // 更新信号信息
    const signalDiv = document.getElementById('gridSignal');
    if (signalDiv) {
        const signalLabels = {
            'FAVOR_PUT': '偏向Put',
            'FAVOR_CALL': '偏向Call',
            'NEUTRAL': '中性'
        };
        
        signalDiv.innerHTML = `
            <div class="bg-gray-800/50 p-4 rounded-lg">
                <div class="text-sm text-gray-400 mb-2">波动率方向信号</div>
                <div class="text-lg font-bold ${getSignalColor(data.dvol_signal)}">${signalLabels[data.dvol_signal] || data.dvol_signal}</div>
                <div class="text-xs text-gray-500 mt-1">推荐比例: ${data.recommended_ratio || '1:1'}</div>
                <div class="text-xs text-gray-500">潜在总权利金: $${data.total_potential_premium || 0}</div>
            </div>
        `;
    }
}

// 渲染网格图表
function renderGridChart(data) {
    const canvas = document.getElementById('gridChart');
    if (!canvas) return;
    
    const ctx = canvas.getContext('2d');
    
    if (gridChart) {
        gridChart.destroy();
    }
    
    // 准备图表数据
    const putStrikes = [];
    const putAprs = [];
    const callStrikes = [];
    const callAprs = [];
    
    if (data.put_levels) {
        data.put_levels.forEach(level => {
            putStrikes.push(level.strike);
            putAprs.push(level.apr);
        });
    }
    
    if (data.call_levels) {
        data.call_levels.forEach(level => {
            callStrikes.push(level.strike);
            callAprs.push(level.apr);
        });
    }
    
    // 合并所有行权价并排序
    const allStrikes = [...new Set([...putStrikes, ...callStrikes])].sort((a, b) => a - b);
    
    // 为每个行权价匹配Put和Call的APR
    const putData = allStrikes.map(strike => {
        const idx = putStrikes.indexOf(strike);
        return idx >= 0 ? putAprs[idx] : 0;
    });
    
    const callData = allStrikes.map(strike => {
        const idx = callStrikes.indexOf(strike);
        return idx >= 0 ? callAprs[idx] : 0;
    });
    
    gridChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: allStrikes.map(s => s.toLocaleString()),
            datasets: [
                {
                    label: 'Put APR %',
                    data: putData,
                    backgroundColor: 'rgba(34, 197, 94, 0.5)',
                    borderColor: 'rgba(34, 197, 94, 1)',
                    borderWidth: 1
                },
                {
                    label: 'Call APR %',
                    data: callData,
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
    setTimeout(initGridStrategy, 1500);
});
