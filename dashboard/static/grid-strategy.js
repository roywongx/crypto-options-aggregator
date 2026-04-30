/**
 * 网格策略前端实现
 * v8.1: 合并版 - 完整参数 + 情景模拟 + 预设功能
 */

function gridSafeHTML(str) {
    if (str == null) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

let gridData = null;
let gridChart = null;
let gridPresets = {};
let currentGridPreset = 'sell_put_grid';
let _gridStrategyInitialized = false;

async function initGridStrategy() {
    if (_gridStrategyInitialized) return;
    _gridStrategyInitialized = true;

    await loadGridPresets();

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

    applyGridPreset('sell_put_grid');
    await loadGridStrategy();
}

async function loadGridPresets() {
    try {
        const response = await safeFetch(`${API_BASE}/api/grid/presets`);
        const data = await response.json();
        if (data.presets) {
            gridPresets = {};
            data.presets.forEach(p => {
                gridPresets[p.id] = p;
            });
        }
    } catch (error) {
        console.error('加载网格预设失败:', error);
    }
}

function applyGridPreset(presetId) {
    const preset = gridPresets[presetId];
    if (!preset) return;
    
    currentGridPreset = presetId;
    
    if (preset.put_count !== undefined) { const el = document.getElementById('gridPutCount'); if (el) el.value = preset.put_count; }
    if (preset.call_count !== undefined) { const el = document.getElementById('gridCallCount'); if (el) el.value = preset.call_count; }
    if (preset.min_dte !== undefined) { const el = document.getElementById('gridMinDte'); if (el) el.value = preset.min_dte; }
    if (preset.max_dte !== undefined) { const el = document.getElementById('gridMaxDte'); if (el) el.value = preset.max_dte; }
    if (preset.min_apr !== undefined) { const el = document.getElementById('gridMinApr'); if (el) el.value = preset.min_apr; }
    
    document.querySelectorAll('#gridPresetButtons button').forEach(btn => {
        btn.classList.remove('ring-1', 'bg-purple-500/15', 'text-purple-300', 'ring-purple-500/30',
            'bg-green-500/15', 'text-green-300', 'ring-green-500/30',
            'bg-blue-500/15', 'text-blue-300', 'ring-blue-500/30',
            'bg-orange-500/15', 'text-orange-300', 'ring-orange-500/30');
    });
    
    const activeBtn = document.querySelector(`#gridPresetButtons button[data-preset="${CSS.escape(presetId)}"]`);
    if (activeBtn) {
        const colorMap = {
            'sell_put_grid': 'purple',
            'conservative': 'green',
            'balanced': 'blue',
            'aggressive': 'orange'
        };
        const c = colorMap[presetId] || 'purple';
        activeBtn.classList.add(`bg-${c}-500/15`, `text-${c}-300`, `ring-1`, `ring-${c}-500/30`);
    }
}

async function loadGridStrategy() {
    try {
        const currencyEl = document.getElementById('gridCurrency');
        if (!currencyEl) return;
        
        const currency = currencyEl.value;
        const putCount = document.getElementById('gridPutCount')?.value || 7;
        const callCount = document.getElementById('gridCallCount')?.value || 0;
        const minDte = document.getElementById('gridMinDte')?.value || 14;
        const maxDte = document.getElementById('gridMaxDte')?.value || 90;
        const minApr = document.getElementById('gridMinApr')?.value || 8;
        
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
            putLevelsDiv.innerHTML = '';
            data.put_levels.forEach(level => {
                const card = document.createElement('div');
                card.className = 'bg-gray-800/50 p-3 rounded-lg border-l-4 border-green-500';
                card.innerHTML = `
                    <div class="flex justify-between items-center">
                        <div>
                            <div class="text-sm font-medium">Sell Put</div>
                            <div class="text-lg font-bold">$${gridSafeHTML(level.strike.toLocaleString())}</div>
                            <div class="text-xs text-gray-400">${gridSafeHTML(level.dte)}天 · Delta ${gridSafeHTML(level.delta)} · 距离${gridSafeHTML(level.distance_pct)}%</div>
                        </div>
                        <div class="text-right">
                            <div class="text-green-400 font-bold">${gridSafeHTML(level.apr)}% APR</div>
                            <div class="text-xs text-gray-400">$${gridSafeHTML(level.premium_usd)}</div>
                            <div class="text-xs ${getRecommendationColor(level.recommendation)}">${gridSafeHTML(level.recommendation)}</div>
                        </div>
                    </div>
                `;
                const reasonDiv = document.createElement('div');
                reasonDiv.className = 'mt-1 text-xs text-gray-500';
                reasonDiv.textContent = level.reason || '';
                card.appendChild(reasonDiv);
                putLevelsDiv.appendChild(card);
            });
        }
    }
    
    const callLevelsDiv = document.getElementById('callLevels');
    if (callLevelsDiv && data.call_levels) {
        if (data.call_levels.length === 0) {
            callLevelsDiv.innerHTML = '<div class="text-center text-gray-500 py-4">无符合条件的Call档位</div>';
        } else {
            callLevelsDiv.innerHTML = '';
            data.call_levels.forEach(level => {
                const card = document.createElement('div');
                card.className = 'bg-gray-800/50 p-3 rounded-lg border-l-4 border-blue-500';
                card.innerHTML = `
                    <div class="flex justify-between items-center">
                        <div>
                            <div class="text-sm font-medium">Sell Call</div>
                            <div class="text-lg font-bold">$${gridSafeHTML(level.strike.toLocaleString())}</div>
                            <div class="text-xs text-gray-400">${gridSafeHTML(level.dte)}天 · Delta ${gridSafeHTML(level.delta)} · 距离+${gridSafeHTML(Math.abs(level.distance_pct))}%</div>
                        </div>
                        <div class="text-right">
                            <div class="text-blue-400 font-bold">${gridSafeHTML(level.apr)}% APR</div>
                            <div class="text-xs text-gray-400">$${gridSafeHTML(level.premium_usd)}</div>
                            <div class="text-xs ${getRecommendationColor(level.recommendation)}">${gridSafeHTML(level.recommendation)}</div>
                        </div>
                    </div>
                `;
                const reasonDiv = document.createElement('div');
                reasonDiv.className = 'mt-1 text-xs text-gray-500';
                reasonDiv.textContent = level.reason || '';
                card.appendChild(reasonDiv);
                callLevelsDiv.appendChild(card);
            });
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
                <div class="text-lg font-bold ${getSignalColor(data.dvol_signal)}">${gridSafeHTML(signalLabels[data.dvol_signal] || data.dvol_signal)}</div>
                <div class="text-sm text-gray-300">推荐比例: <b>${gridSafeHTML(data.recommended_ratio || '5:5')}</b></div>
                <div class="text-sm text-gray-300">潜在总权利金: <b class="text-green-400">$${gridSafeHTML((data.total_potential_premium || 0).toLocaleString())}</b></div>
                <div class="text-sm text-gray-300">现货: <b>$${gridSafeHTML((data.spot_price || 0).toLocaleString())}</b></div>
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

    const putMap = new Map();
    putStrikes.forEach((s, i) => putMap.set(s, putAprs[i]));
    const callMap = new Map();
    callStrikes.forEach((s, i) => callMap.set(s, callAprs[i]));

    const putData = allStrikes.map(strike => putMap.get(strike) || 0);
    const callData = allStrikes.map(strike => callMap.get(strike) || 0);
    
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
        const isCurrent = Math.abs(s.price - spot) < 0.01;
        const pnlColor = totalPnl >= 0 ? 'text-green-400' : 'text-red-400';
        const bgColor = isCurrent ? 'bg-purple-500/10 border-purple-500/30' : 'bg-gray-800/40';
        
        return `
            <div class="p-3 ${bgColor} rounded-lg border ${isCurrent ? 'border-purple-500/30' : 'border-gray-700/30'}">
                <div class="text-xs text-gray-400 mb-1">${gridSafeHTML(s.label)}</div>
                <div class="text-sm font-medium">$${gridSafeHTML(s.price.toLocaleString(undefined, {maximumFractionDigits: 0}))}</div>
                <div class="text-sm font-bold ${pnlColor}">$${gridSafeHTML(totalPnl.toLocaleString(undefined, {maximumFractionDigits: 0}))}</div>
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
