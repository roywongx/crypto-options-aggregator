/**
 * 网格策略前端实现
 * v8.1: 合并版 - 完整参数 + 情景模拟
 */

let gridData = null;
let gridChart = null;

async function initGridStrategy() {
    const gridCurrency = document.getElementById('gridCurrency');
    const gridPutCount = document.getElementById('gridPutCount');
    const gridCallCount = document.getElementById('gridCallCount');
    const gridMinDte = document.getElementById('gridMinDte');
    const gridMaxDte = document.getElementById('gridMaxDte');
    const gridMinApr = document.getElementById('gridMinApr');
    
    if (gridCurrency) gridCurrency.addEventListener('change', loadGridStrategy);
    if (gridPutCount) gridPutCount.addEventListener('change', loadGridStrategy);
    if (gridCallCount) gridCallCount.addEventListener('change', loadGridStrategy);
    if (gridMinDte) gridMinDte.addEventListener('change', loadGridStrategy);
    if (gridMaxDte) gridMaxDte.addEventListener('change', loadGridStrategy);
    if (gridMinApr) gridMinApr.addEventListener('change', loadGridStrategy);
    
    await loadGridStrategy();
}

async function loadGridStrategy() {
    try {
        const currencyEl = document.getElementById('gridCurrency');
        if (!currencyEl) return;
        
        const currency = currencyEl.value;
        const putCount = document.getElementById('gridPutCount')?.value || 5;
        const callCount = document.getElementById('gridCallCount')?.value || 3;
        const minDte = document.getElementById('gridMinDte')?.value || 7;
        const maxDte = document.getElementById('gridMaxDte')?.value || 45;
        const minApr = document.getElementById('gridMinApr')?.value || 15;
        
        const loadingEl = document.getElementById('gridLoading');
        if (loadingEl) loadingEl.classList.remove('hidden');
        
        const response = await safeFetch(
            `${API_BASE}/api/grid/recommend?currency=${currency}&put_count=${putCount}&call_count=${callCount}&min_dte=${minDte}&max_dte=${maxDte}&min_apr=${minApr}`
        );
        
        gridData = await response.json();
        
        updateGridDisplay(gridData);
        renderGridChart(gridData);
        renderGridScenarios(gridData);
        
        if (loadingEl) loadingEl.classList.add('hidden');
        
    } catch (error) {
        console.error('加载网格策略失败:', error);
        if (typeof showAlert === 'function') {
            showAlert(`网格策略加载失败: ${error.message}`, 'error');
        }
        const loadingEl = document.getElementById('gridLoading');
        if (loadingEl) loadingEl.classList.add('hidden');
    }
}

function updateGridDisplay(data) {
    const putLevelsDiv = document.getElementById('putLevels');
    if (putLevelsDiv && data.put_levels) {
        if (data.put_levels.length === 0) {
            putLevelsDiv.innerHTML = '<div class="text-center text-gray-500 py-4">无符合条件的Put档位</div>';
        } else {
            putLevelsDiv.innerHTML = data.put_levels.map(level => `
                <div class="bg-gray-800/50 p-3 rounded-lg border-l-4 border-green-500">
                    <div class="flex justify-between items-center">
                        <div>
                            <div class="text-sm font-medium">Sell Put</div>
                            <div class="text-lg font-bold">$${level.strike.toLocaleString()}</div>
                            <div class="text-xs text-gray-400">${level.dte}天 · Delta ${level.delta} · 距离${level.distance_pct}%</div>
                        </div>
                        <div class="text-right">
                            <div class="text-green-400 font-bold">${level.apr}% APR</div>
                            <div class="text-xs text-gray-400">$${level.premium_usd}</div>
                            <div class="text-xs ${getRecommendationColor(level.recommendation)}">${level.recommendation}</div>
                        </div>
                    </div>
                    <div class="mt-1 text-xs text-gray-500">${level.reason || ''}</div>
                </div>
            `).join('');
        }
    }
    
    const callLevelsDiv = document.getElementById('callLevels');
    if (callLevelsDiv && data.call_levels) {
        if (data.call_levels.length === 0) {
            callLevelsDiv.innerHTML = '<div class="text-center text-gray-500 py-4">无符合条件的Call档位</div>';
        } else {
            callLevelsDiv.innerHTML = data.call_levels.map(level => `
                <div class="bg-gray-800/50 p-3 rounded-lg border-l-4 border-blue-500">
                    <div class="flex justify-between items-center">
                        <div>
                            <div class="text-sm font-medium">Sell Call</div>
                            <div class="text-lg font-bold">$${level.strike.toLocaleString()}</div>
                            <div class="text-xs text-gray-400">${level.dte}天 · Delta ${level.delta} · 距离+${Math.abs(level.distance_pct)}%</div>
                        </div>
                        <div class="text-right">
                            <div class="text-blue-400 font-bold">${level.apr}% APR</div>
                            <div class="text-xs text-gray-400">$${level.premium_usd}</div>
                            <div class="text-xs ${getRecommendationColor(level.recommendation)}">${level.recommendation}</div>
                        </div>
                    </div>
                    <div class="mt-1 text-xs text-gray-500">${level.reason || ''}</div>
                </div>
            `).join('');
        }
    }
    
    const signalDiv = document.getElementById('gridSignal');
    if (signalDiv) {
        const signalLabels = {
            'FAVOR_PUT': '偏向Put（恐慌时卖Put收高价）',
            'FAVOR_CALL': '偏向Call（平静时卖Call收溢价）',
            'NEUTRAL': '中性均衡'
        };
        
        signalDiv.innerHTML = `
            <div class="bg-gray-800/50 p-4 rounded-lg space-y-2">
                <div class="text-lg font-bold ${getSignalColor(data.dvol_signal)}">${signalLabels[data.dvol_signal] || data.dvol_signal}</div>
                <div class="text-sm text-gray-300">推荐比例: <b>${data.recommended_ratio || '5:5'}</b></div>
                <div class="text-sm text-gray-300">潜在总权利金: <b class="text-green-400">$${(data.total_potential_premium || 0).toLocaleString()}</b></div>
                <div class="text-sm text-gray-300">现货: <b>$${(data.spot_price || 0).toLocaleString()}</b></div>
            </div>
        `;
    }
}

function renderGridChart(data) {
    const canvas = document.getElementById('gridChart');
    if (!canvas) return;
    
    const ctx = canvas.getContext('2d');
    if (gridChart) gridChart.destroy();
    
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
    
    const allStrikes = [...new Set([...putStrikes, ...callStrikes])].sort((a, b) => a - b);
    
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
            labels: allStrikes.map(s => '$' + (s / 1000) + 'K'),
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
                title: { display: true, text: 'APR 分布' },
                tooltip: {
                    callbacks: {
                        label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y}%`
                    }
                }
            },
            scales: {
                y: { beginAtZero: true, title: { display: true, text: 'APR %' } },
                x: { title: { display: true, text: '行权价' } }
            }
        }
    });
}

function renderGridScenarios(data) {
    const scenariosDiv = document.getElementById('gridScenarios');
    if (!scenariosDiv || !data.spot_price) return;
    
    const spot = data.spot_price;
    const scenarios = [
        { label: '暴跌 -20%', price: spot * 0.80 },
        { label: '下跌 -10%', price: spot * 0.90 },
        { label: '当前价格', price: spot },
        { label: '上涨 +10%', price: spot * 1.10 },
        { label: '大涨 +20%', price: spot * 1.20 }
    ];
    
    scenariosDiv.innerHTML = scenarios.map(s => {
        const putPnl = (data.put_levels || []).reduce((sum, l) => {
            if (s.price < l.strike) {
                return sum + l.premium_usd - (l.strike - s.price);
            }
            return sum + l.premium_usd;
        }, 0);
        
        const callPnl = (data.call_levels || []).reduce((sum, l) => {
            if (s.price > l.strike) {
                return sum + l.premium_usd - (s.price - l.strike);
            }
            return sum + l.premium_usd;
        }, 0);
        
        const totalPnl = putPnl + callPnl;
        const isCurrent = s.price === spot;
        const pnlColor = totalPnl >= 0 ? 'text-green-400' : 'text-red-400';
        const bgColor = isCurrent ? 'bg-purple-500/10 border-purple-500/30' : 'bg-gray-800/40';
        
        return `
            <div class="p-3 ${bgColor} rounded-lg border ${isCurrent ? 'border-purple-500/30' : 'border-gray-700/30'}">
                <div class="text-xs text-gray-400 mb-1">${s.label}</div>
                <div class="text-sm font-medium">$${s.price.toLocaleString(undefined, {maximumFractionDigits: 0})}</div>
                <div class="text-sm font-bold ${pnlColor}">$${totalPnl.toLocaleString(undefined, {maximumFractionDigits: 0})}</div>
            </div>
        `;
    }).join('');
}

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

document.addEventListener('DOMContentLoaded', function() {
    setTimeout(initGridStrategy, 1500);
});
