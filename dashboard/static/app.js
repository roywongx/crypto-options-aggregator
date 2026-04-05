/**
 * 期权监控面板 - 前端逻辑
 * 包含：实时扫描、倍投修复计算器、风险预警、滚仓建议
 */

let currentData = null;
let autoRefreshInterval = null;
let aprChart = null;
let dvolChart = null;
let chartPeriods = { apr: 168, dvol: 168 };
let currentSpotPrice = null;
let scanStatusInterval = null;

const API_BASE = '';

document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    loadLatestData();
    loadStats();
    setupEventListeners();
    updateParamDisplay();
    setAutoRefresh(5);
    requestNotificationPermission();
});

function requestNotificationPermission() {
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }
}

function setupEventListeners() {
    document.getElementById('autoRefresh').addEventListener('change', (e) => {
        const minutes = parseInt(e.target.value);
        setAutoRefresh(minutes);
    });
    
    ['currencySelect', 'minDte', 'maxDte', 'maxDelta', 'optionType', 'strikeInput', 'strikeRangeInput'].forEach(id => {
        document.getElementById(id).addEventListener('change', updateParamDisplay);
    });
    
    document.getElementById('strikeInput').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') triggerScan();
    });
    document.getElementById('strikeRangeInput').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') triggerScan();
    });
    document.getElementById('recoveryLoss').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') calculateRecovery();
    });
}

function updateParamDisplay() {
    const currency = document.getElementById('currencySelect').value;
    const minDte = document.getElementById('minDte').value;
    const maxDte = document.getElementById('maxDte').value;
    const maxDelta = document.getElementById('maxDelta').value;
    const optionType = document.getElementById('optionType').value;
    const strike = document.getElementById('strikeInput').value;
    const strikeRange = document.getElementById('strikeRangeInput').value;
    
    let display = `${currency} | DTE ${minDte}-${maxDte} | Δ≤${maxDelta} | ${optionType === 'PUT' ? 'Sell Put' : 'Covered Call'}`;
    if (strike) display += ` | Strike=${strike}`;
    else if (strikeRange) display += ` | Range=${strikeRange}`;
    
    document.getElementById('currentParams').textContent = display;
    document.getElementById('currencyLabel').textContent = `${currency}/USDT`;
}

function setAutoRefresh(minutes) {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
    }
    if (minutes > 0) {
        autoRefreshInterval = setInterval(triggerScan, minutes * 60 * 1000);
    }
}

function initCharts() {
    const aprCtx = document.getElementById('aprChart').getContext('2d');
    aprChart = new Chart(aprCtx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: '最高 APR',
                data: [],
                borderColor: '#22c55e',
                backgroundColor: 'rgba(34, 197, 94, 0.1)',
                tension: 0.4,
                fill: true,
                borderWidth: 2,
                pointRadius: 2,
                pointHoverRadius: 4
            }, {
                label: '平均 APR',
                data: [],
                borderColor: '#f97316',
                backgroundColor: 'rgba(249, 115, 22, 0.1)',
                tension: 0.4,
                fill: true,
                borderWidth: 2,
                pointRadius: 2,
                pointHoverRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { labels: { color: '#9ca3af', font: { size: 10 }, usePointStyle: true, boxWidth: 6 } },
                tooltip: { mode: 'index', intersect: false, backgroundColor: 'rgba(30, 58, 95, 0.9)', titleColor: '#fff', bodyColor: '#fff', borderColor: 'rgba(255, 255, 255, 0.1)', borderWidth: 1 }
            },
            scales: {
                x: { ticks: { color: '#6b7280', font: { size: 9 }, maxTicksLimit: 6 }, grid: { color: 'rgba(75, 85, 99, 0.2)' } },
                y: { ticks: { color: '#6b7280', font: { size: 9 } }, grid: { color: 'rgba(75, 85, 99, 0.2)' } }
            },
            interaction: { intersect: false, mode: 'index' }
        }
    });

    const dvolCtx = document.getElementById('dvolChart').getContext('2d');
    dvolChart = new Chart(dvolCtx, {
        type: 'line',
        data: { labels: [], datasets: [{ label: 'DVOL', data: [], borderColor: '#3b82f6', backgroundColor: 'rgba(59, 130, 246, 0.1)', tension: 0.4, fill: true, borderWidth: 2, pointRadius: 2, pointHoverRadius: 4 }] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false, backgroundColor: 'rgba(30, 58, 95, 0.9)', titleColor: '#fff', bodyColor: '#fff', borderColor: 'rgba(255, 255, 255, 0.1)', borderWidth: 1, callbacks: { label: (ctx) => `DVOL: ${ctx.parsed.y.toFixed(2)}` } } },
            scales: {
                x: { ticks: { color: '#6b7280', font: { size: 9 }, maxTicksLimit: 6 }, grid: { color: 'rgba(75, 85, 99, 0.2)' } },
                y: { ticks: { color: '#6b7280', font: { size: 9 } }, grid: { color: 'rgba(75, 85, 99, 0.2)' } }
            },
            interaction: { intersect: false, mode: 'index' }
        }
    });
}

function setChartPeriod(chartType, hours) {
    chartPeriods[chartType] = hours;
    document.querySelectorAll(`.${chartType}-period-btn`).forEach(btn => {
        const btnHours = parseInt(btn.dataset.period);
        if (btnHours === hours) {
            btn.classList.remove('bg-gray-700', 'hover:bg-gray-600');
            btn.classList.add('bg-orange-500', 'text-white');
        } else {
            btn.classList.add('bg-gray-700', 'hover:bg-gray-600');
            btn.classList.remove('bg-orange-500', 'text-white');
        }
    });
    if (chartType === 'apr') loadAprChartData();
    else if (chartType === 'dvol') loadDvolChartData();
}

async function triggerScan() {
    const btn = document.getElementById('scanBtn');
    const icon = document.getElementById('scanIcon');
    btn.disabled = true;
    icon.classList.add('fa-spin');
    
    try {
        const strikeInput = document.getElementById('strikeInput').value;
        const strikeRangeInput = document.getElementById('strikeRangeInput').value;
        
        const params = {
            currency: document.getElementById('currencySelect').value,
            min_dte: parseInt(document.getElementById('minDte').value) || 14,
            max_dte: parseInt(document.getElementById('maxDte').value) || 25,
            max_delta: parseFloat(document.getElementById('maxDelta').value) || 0.4,
            margin_ratio: 0.2,
            option_type: document.getElementById('optionType').value
        };
        
        if (strikeInput && !isNaN(strikeInput)) params.strike = parseFloat(strikeInput);
        if (strikeRangeInput && strikeRangeInput.includes('-')) params.strike_range = strikeRangeInput;
        
        showAlert('正在扫描期权数据...', 'info');
        
        const response = await fetch(`${API_BASE}/api/scan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params)
        });
        
        const result = await response.json();
        
        if (result.success) {
            showAlert(`扫描完成！发现 ${result.contracts_count} 个合约`, 'success');
            await loadLatestData();
            await loadStats();
        } else {
            showAlert('扫描失败: ' + (result.detail || result.error), 'error');
        }
    } catch (error) {
        showAlert('请求错误: ' + error.message, 'error');
    } finally {
        btn.disabled = false;
        icon.classList.remove('fa-spin');
    }
}

async function calculateRecovery() {
    const btn = document.getElementById('recoveryBtn');
    const lossInput = document.getElementById('recoveryLoss');
    const resultDiv = document.getElementById('recoveryResult');
    
    const currentLoss = parseFloat(lossInput.value);
    if (!currentLoss || currentLoss <= 0) {
        showAlert('请输入有效的浮亏金额', 'error');
        lossInput.focus();
        return;
    }
    
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> <span>计算中...</span>';
    
    try {
        const params = {
            currency: document.getElementById('recoveryCurrency').value,
            current_loss: currentLoss,
            target_apr: parseFloat(document.getElementById('recoveryApr').value) || 200,
            max_delta: parseFloat(document.getElementById('recoveryMaxDelta').value) || 0.45
        };
        
        const response = await fetch(`${API_BASE}/api/recovery-calculate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params)
        });
        
        const result = await response.json();
        
        if (result.error) {
            showAlert('计算失败: ' + result.error, 'error');
            resultDiv.classList.add('hidden');
        } else {
            displayRecoveryResult(result);
            resultDiv.classList.remove('hidden');
            showAlert('修复方案计算完成！', 'success');
        }
    } catch (error) {
        showAlert('计算错误: ' + error.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-magic"></i> <span>计算修复方案</span>';
    }
}

function displayRecoveryResult(result) {
    const recommended = result.recommended;
    const plans = result.plans || [];
    
    const recommendedDiv = document.getElementById('recommendedPlan');
    if (recommended) {
        const riskColor = recommended.risk_level === '低风险' ? 'text-green-400' : recommended.risk_level === '中风险' ? 'text-yellow-400' : recommended.risk_level === '高风险' ? 'text-orange-400' : 'text-red-400';
        
        recommendedDiv.innerHTML = `
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div class="text-center"><div class="text-xs text-gray-400 mb-1">推荐合约</div><div class="font-mono font-semibold text-white">${recommended.symbol}</div><div class="text-xs text-gray-500">${recommended.platform}</div></div>
                <div class="text-center"><div class="text-xs text-gray-400 mb-1">卖出张数</div><div class="text-2xl font-bold text-orange-400">${recommended.num_contracts} 张</div></div>
                <div class="text-center"><div class="text-xs text-gray-400 mb-1">所需保证金</div><div class="text-xl font-semibold text-white">$${recommended.total_margin.toLocaleString()}</div></div>
                <div class="text-center"><div class="text-xs text-gray-400 mb-1">预期净利润</div><div class="text-xl font-bold text-green-400">+$${recommended.net_profit.toLocaleString()}</div><div class="text-xs ${riskColor}">${recommended.risk_level}</div></div>
            </div>
            <div class="mt-3 pt-3 border-t border-green-500/20 text-xs text-gray-400"><i class="fas fa-info-circle mr-1"></i>基于 ${recommended.apr.toFixed(1)}% APR，在 ${recommended.dte.toFixed(0)} 天内通过卖出 Put 期权获取权利金覆盖浮亏</div>
        `;
    }
    
    const tableBody = document.getElementById('recoveryPlansTable');
    if (plans.length > 0) {
        tableBody.innerHTML = plans.map((plan, index) => {
            const riskColor = plan.risk_level === '低风险' ? 'text-green-400' : plan.risk_level === '中风险' ? 'text-yellow-400' : plan.risk_level === '高风险' ? 'text-orange-400' : 'text-red-400';
            const profitColor = plan.net_profit >= 0 ? 'text-green-400' : 'text-red-400';
            
            return `<tr class="border-b border-gray-800/50 hover:bg-gray-800/30 transition ${index === 0 ? 'bg-green-500/5' : ''}">
                <td class="py-2 px-2">${index === 0 ? '<span class="text-green-400 font-bold"><i class="fas fa-crown"></i> 推荐</span>' : `<span class="text-gray-500">#${index + 1}</span>`}</td>
                <td class="py-2 px-2 font-mono text-xs">${plan.symbol}</td>
                <td class="py-2 px-2 text-center">${plan.dte.toFixed(0)}</td>
                <td class="py-2 px-2 text-right font-mono">${Math.round(plan.strike).toLocaleString()}</td>
                <td class="py-2 px-2 text-right font-mono text-green-400">${plan.apr.toFixed(1)}%</td>
                <td class="py-2 px-2 text-right font-mono font-semibold">${plan.num_contracts}</td>
                <td class="py-2 px-2 text-right font-mono">$${plan.total_margin.toLocaleString()}</td>
                <td class="py-2 px-2 text-right font-mono text-blue-400">$${plan.expected_premium.toLocaleString()}</td>
                <td class="py-2 px-2 text-right font-mono ${profitColor} font-semibold">${plan.net_profit >= 0 ? '+' : ''}$${plan.net_profit.toLocaleString()}</td>
                <td class="py-2 px-2 text-center ${riskColor} text-xs">${plan.risk_level}</td>
            </tr>`;
        }).join('');
    } else {
        tableBody.innerHTML = '<tr><td colspan="10" class="text-center py-4 text-gray-500">无可用方案</td></tr>';
    }
}

async function loadLatestData() {
    try {
        const currency = document.getElementById('currencySelect').value;
        const response = await fetch(`${API_BASE}/api/latest?currency=${currency}`);
        if (response.status === 404) return;
        
        const data = await response.json();
        currentData = data;
        if (data.spot_price) currentSpotPrice = data.spot_price;
        
        updateMacroIndicators(data);
        updateOpportunitiesTable(data.contracts || []);
        updateLargeTrades(data.large_trades_details || [], data.large_trades_count || 0);
        updateLastUpdateTime(data.timestamp);
        loadAprChartData();
        loadDvolChartData();
    } catch (error) {
        console.error('加载数据失败:', error);
    }
}

function updateMacroIndicators(data) {
    const spotPrice = data.spot_price;
    const spotEl = document.getElementById('spotPrice');
    if (spotPrice) {
        spotEl.textContent = `$${Math.round(spotPrice).toLocaleString()}`;
        spotEl.classList.remove('text-gray-500');
        currentSpotPrice = spotPrice;
    } else {
        spotEl.textContent = '--';
    }
    
    const dvol = data.dvol_current;
    document.getElementById('dvolValue').textContent = dvol ? dvol.toFixed(2) : '--';
    
    const dvolSignal = document.getElementById('dvolSignal');
    const zScore = data.dvol_z_score;
    const signal = data.dvol_signal;
    
    if (signal) {
        dvolSignal.textContent = signal;
        dvolSignal.className = signal.includes('偏高') ? 'text-xs mt-1 text-red-400 font-medium' : signal.includes('偏低') ? 'text-xs mt-1 text-green-400 font-medium' : 'text-xs mt-1 text-gray-400';
    } else if (zScore !== null && zScore !== undefined) {
        if (zScore > 2) { dvolSignal.textContent = '异常偏高 ⚠️'; dvolSignal.className = 'text-xs mt-1 text-red-400 font-medium'; }
        else if (zScore > 1) { dvolSignal.textContent = '偏高'; dvolSignal.className = 'text-xs mt-1 text-yellow-400 font-medium'; }
        else if (zScore < -2) { dvolSignal.textContent = '异常偏低'; dvolSignal.className = 'text-xs mt-1 text-green-400 font-medium'; }
        else if (zScore < -1) { dvolSignal.textContent = '偏低'; dvolSignal.className = 'text-xs mt-1 text-blue-400 font-medium'; }
        else { dvolSignal.textContent = '正常区间'; dvolSignal.className = 'text-xs mt-1 text-gray-400'; }
    } else {
        dvolSignal.textContent = '--';
        dvolSignal.className = 'text-xs mt-1 text-gray-400';
    }
    
    document.getElementById('largeTradesCount').textContent = data.large_trades_count || 0;
    
    const contracts = data.contracts || [];
    const bestAprEl = document.getElementById('bestApr');
    if (contracts.length > 0) {
        const bestApr = Math.max(...contracts.map(c => c.apr));
        bestAprEl.textContent = bestApr.toFixed(1) + '%';
    } else {
        bestAprEl.textContent = '--';
    }
}

function updateOpportunitiesTable(contracts) {
    const tbody = document.getElementById('opportunitiesTable');
    const countEl = document.getElementById('contractCount');
    countEl.textContent = `${contracts.length} 个合约`;
    
    if (contracts.length === 0) {
        tbody.innerHTML = `<tr><td colspan="11" class="text-center py-12 text-gray-500"><div class="flex flex-col items-center gap-3"><i class="fas fa-inbox text-3xl text-gray-600"></i><p>暂无符合条件的合约</p><p class="text-xs text-gray-600">尝试调整扫描参数</p></div></td></tr>`;
        updateRiskAlerts([]);
        return;
    }
    
    const riskAlerts = [];
    let highRiskContracts = [];
    
    tbody.innerHTML = contracts.slice(0, 20).map((contract, idx) => {
        const platformColor = contract.platform === 'Deribit' ? 'text-blue-400' : 'text-yellow-400';
        const platformBg = contract.platform === 'Deribit' ? 'bg-blue-500/10' : 'bg-yellow-500/10';
        const liqColor = contract.liquidity_score >= 70 ? 'text-green-400' : contract.liquidity_score >= 40 ? 'text-yellow-400' : 'text-red-400';
        const liqBg = contract.liquidity_score >= 70 ? 'bg-green-500/10' : contract.liquidity_score >= 40 ? 'bg-yellow-500/10' : 'bg-red-500/10';
        const deltaAbs = Math.abs(contract.delta);
        const vega = contract.vega || 0;
        
        // 统一合约名称字段：Deribit使用instrument_name，Binance使用symbol
        const symbol = contract.symbol || contract.instrument_name || 'N/A';
        contract.symbol = symbol; // 确保后续使用统一
        
        let riskClass = '';
        let riskBadge = '';
        let riskLevel = '';
        
        let distancePct = null;
        if (currentSpotPrice && contract.strike) {
            distancePct = Math.abs(contract.strike - currentSpotPrice) / currentSpotPrice * 100;
        }
        
        const isHighDelta = deltaAbs > 0.45;
        const isNearStrike = distancePct !== null && distancePct < 2;
        
        if (isHighDelta && isNearStrike) {
            riskClass = 'risk-alert-high';
            riskBadge = '<span class="risk-badge bg-red-500 text-white text-xs px-2 py-0.5 rounded font-bold"><i class="fas fa-exclamation-triangle"></i> 极高风险</span>';
            riskLevel = '极高';
            highRiskContracts.push({ contract, reason: `Delta(${deltaAbs.toFixed(3)})>0.45 且 价格接近Strike(${distancePct.toFixed(1)}%)` });
        } else if (isHighDelta) {
            riskClass = 'risk-alert-high';
            riskBadge = '<span class="risk-badge bg-red-500 text-white text-xs px-2 py-0.5 rounded font-bold"><i class="fas fa-exclamation"></i> 高风险</span>';
            riskLevel = '高';
            highRiskContracts.push({ contract, reason: `Delta(${deltaAbs.toFixed(3)})>0.45` });
        } else if (isNearStrike) {
            riskClass = 'risk-alert-medium';
            riskBadge = '<span class="bg-orange-500 text-white text-xs px-2 py-0.5 rounded"><i class="fas fa-exclamation-circle"></i> 接近行权</span>';
            riskLevel = '中';
        } else if (deltaAbs > 0.35) {
            riskBadge = '<span class="bg-yellow-500/80 text-white text-xs px-2 py-0.5 rounded">警告</span>';
            riskLevel = '警告';
        } else {
            riskBadge = '<span class="bg-green-500/50 text-white text-xs px-2 py-0.5 rounded">正常</span>';
            riskLevel = '正常';
        }
        
        if (riskLevel === '极高' || riskLevel === '高') {
            riskAlerts.push({ symbol: symbol, strike: contract.strike, delta: deltaAbs, distancePct, level: riskLevel, reason: riskLevel === '极高' ? `Delta(${deltaAbs.toFixed(3)})>0.45 且 价格接近Strike(${distancePct.toFixed(1)}%)` : `Delta(${deltaAbs.toFixed(3)})>0.45` });
        }
        
        return `<tr class="border-b border-gray-800/50 hover:bg-gray-800/30 transition ${riskClass}" onclick="showRollSuggestion(${idx})" style="cursor: pointer;">
            <td class="py-3 px-2"><span class="${platformColor} ${platformBg} px-2 py-1 rounded text-xs font-medium">${contract.platform}</span></td>
            <td class="py-3 px-2 font-mono text-xs">${contract.symbol}</td>
            <td class="py-3 px-2 text-center">${contract.dte.toFixed(1)}</td>
            <td class="py-3 px-2 text-right font-mono">${Math.round(contract.strike).toLocaleString()}</td>
            <td class="py-3 px-2 text-right font-mono ${deltaAbs > 0.35 ? 'text-red-400 font-semibold' : ''}">${contract.delta.toFixed(3)}</td>
            <td class="py-3 px-2 text-right font-mono">${contract.gamma ? contract.gamma.toFixed(6) : '-'}</td>
            <td class="py-3 px-2 text-right font-mono ${vega > 50 ? 'vega-high' : ''}">${vega > 0 ? vega.toFixed(2) : '-'}</td>
            <td class="py-3 px-2 text-right font-mono"><span class="text-green-400 font-semibold">${contract.apr.toFixed(1)}%</span></td>
            <td class="py-3 px-2 text-center"><span class="${liqColor} ${liqBg} px-2 py-1 rounded text-xs font-medium">${contract.liquidity_score}</span></td>
            <td class="py-3 px-2 text-right font-mono text-red-400/80">-$${Math.abs(contract.loss_at_10pct || 0).toLocaleString()}</td>
            <td class="py-3 px-2 text-center">${riskBadge}</td>
        </tr>`;
    }).join('');
    
    updateRiskAlerts(riskAlerts);
    
    // 浏览器通知
    if (highRiskContracts.length > 0 && 'Notification' in window && Notification.permission === 'granted') {
        new Notification('期权风险预警', {
            body: `检测到 ${highRiskContracts.length} 个高风险合约，建议执行滚仓操作`,
            icon: '/static/favicon.ico'
        });
    }
}

function showRollSuggestion(idx) {
    if (!currentData || !currentData.contracts || !currentData.contracts[idx]) return;
    
    const contract = currentData.contracts[idx];
    const deltaAbs = Math.abs(contract.delta);
    const distancePct = currentSpotPrice ? Math.abs(contract.strike - currentSpotPrice) / currentSpotPrice * 100 : 0;
    
    if (deltaAbs <= 0.45 && distancePct >= 2) return;
    
    const modal = document.getElementById('rollModal');
    const content = document.getElementById('rollModalContent');
    
    // 计算建议
    const estimatedLoss = Math.abs(contract.loss_at_10pct || 0);
    const alternatives = currentData.contracts.filter(c => 
        c.strike < contract.strike && 
        c.dte > contract.dte && 
        Math.abs(c.delta) < 0.3 &&
        c.apr > 50
    ).slice(0, 3);
    
    let alternativesHtml = '';
    if (alternatives.length > 0) {
        alternativesHtml = `<div class="mt-4"><h4 class="font-semibold text-green-400 mb-2">建议滚仓至：</h4>${alternatives.map(alt => `
            <div class="bg-gray-800/50 rounded-lg p-3 mb-2">
                <div class="flex justify-between"><span class="font-mono">${alt.symbol}</span><span class="text-green-400">${alt.apr.toFixed(1)}% APR</span></div>
                <div class="text-xs text-gray-400 mt-1">Strike: ${Math.round(alt.strike).toLocaleString()} | DTE: ${alt.dte.toFixed(0)} | Delta: ${Math.abs(alt.delta).toFixed(3)}</div>
            </div>
        `).join('')}</div>`;
    }
    
    content.innerHTML = `
        <div class="space-y-4">
            <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-4">
                <h4 class="font-semibold text-red-400 mb-2">当前持仓风险</h4>
                <div class="grid grid-cols-2 gap-4 text-sm">
                    <div><span class="text-gray-400">合约:</span> <span class="font-mono">${contract.symbol}</span></div>
                    <div><span class="text-gray-400">Delta:</span> <span class="text-red-400 font-bold">${contract.delta.toFixed(3)}</span></div>
                    <div><span class="text-gray-400">行权价:</span> $${Math.round(contract.strike).toLocaleString()}</div>
                    <div><span class="text-gray-400">距离现货:</span> <span class="${distancePct < 2 ? 'text-red-400' : ''}">${distancePct.toFixed(1)}%</span></div>
                </div>
                <div class="mt-2 text-sm"><span class="text-gray-400">-10%亏损预估:</span> <span class="text-red-400 font-bold">-$${estimatedLoss.toLocaleString()}</span></div>
            </div>
            ${alternativesHtml}
            <div class="bg-blue-500/10 border border-blue-500/30 rounded-lg p-4">
                <h4 class="font-semibold text-blue-400 mb-2">操作建议</h4>
                <p class="text-sm text-gray-300">建议平仓当前合约，卖出更低行权价的远期Put，获取更高权利金的同时下移防线。</p>
            </div>
        </div>
    `;
    
    modal.classList.add('active');
}

function closeRollModal() {
    document.getElementById('rollModal').classList.remove('active');
}

function updateRiskAlerts(alerts) {
    const panel = document.getElementById('riskAlertsPanel');
    const list = document.getElementById('riskAlertsList');
    
    if (alerts.length === 0) {
        panel.style.display = 'none';
        return;
    }
    
    panel.style.display = 'block';
    const levelOrder = { '极高': 0, '高': 1, '中': 2, '警告': 3 };
    alerts.sort((a, b) => levelOrder[a.level] - levelOrder[b.level]);
    
    list.innerHTML = alerts.map(alert => {
        const bgColor = alert.level === '极高' ? 'bg-red-500/20 border-red-500' : alert.level === '高' ? 'bg-red-500/10 border-red-400' : 'bg-orange-500/10 border-orange-400';
        const icon = alert.level === '极高' || alert.level === '高' ? 'fa-exclamation-triangle' : 'fa-exclamation-circle';
        
        return `<div class="${bgColor} border-l-4 rounded-lg p-3 text-xs">
            <div class="flex items-start gap-2">
                <i class="fas ${icon} text-red-400 mt-0.5 flex-shrink-0"></i>
                <div class="flex-1">
                    <div class="font-semibold text-white mb-1">${alert.symbol} - Strike ${Math.round(alert.strike).toLocaleString()}</div>
                    <div class="text-gray-400">${alert.reason}</div>
                    ${alert.distancePct !== null ? `<div class="text-orange-400 mt-1">距离: ${alert.distancePct.toFixed(1)}%</div>` : ''}
                </div>
            </div>
        </div>`;
    }).join('');
    
    const highRiskCount = alerts.filter(a => a.level === '极高' || a.level === '高').length;
    if (highRiskCount > 0) showAlert(`检测到 ${highRiskCount} 个高风险合约，建议执行滚仓操作！`, 'warning');
}

function updateLargeTrades(trades, count) {
    const container = document.getElementById('largeTradesList');
    const titleCount = document.getElementById('largeTradesTitleCount');
    
    if (count > 0) { titleCount.textContent = count; titleCount.classList.remove('hidden'); }
    else titleCount.classList.add('hidden');
    
    if (!trades || trades.length === 0) {
        container.innerHTML = '<div class="text-gray-500 text-center py-4 text-sm">近1小时无大单成交</div>';
        return;
    }
    
    container.innerHTML = trades.map(trade => {
        const isBuy = trade.includes('buy') || trade.includes('买入');
        const isSell = trade.includes('sell') || trade.includes('卖出');
        const directionIcon = isBuy ? '<i class="fas fa-arrow-up text-red-400"></i>' : isSell ? '<i class="fas fa-arrow-down text-green-400"></i>' : '<i class="fas fa-minus text-gray-400"></i>';
        const directionClass = isBuy ? 'border-l-red-500' : isSell ? 'border-l-green-500' : 'border-l-gray-500';
        
        return `<div class="bg-gray-800/30 border-l-4 ${directionClass} rounded-lg p-3 text-xs hover:bg-gray-800/50 transition cursor-default"><div class="flex items-start gap-2"><div class="flex-shrink-0 mt-0.5">${directionIcon}</div><div class="flex-1 text-gray-300 leading-relaxed break-words">${trade}</div></div></div>`;
    }).join('');
}

function updateLastUpdateTime(timestamp) {
    const date = new Date(timestamp);
    const timeStr = date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    document.getElementById('lastUpdate').textContent = `更新于 ${timeStr}`;
}

async function loadAprChartData() {
    try {
        const currency = document.getElementById('currencySelect').value;
        const hours = chartPeriods.apr;
        const response = await fetch(`${API_BASE}/api/charts/apr?currency=${currency}&hours=${hours}`);
        const data = await response.json();
        
        if (!data || data.length === 0) {
            aprChart.data.labels = [];
            aprChart.data.datasets[0].data = [];
            aprChart.data.datasets[1].data = [];
            aprChart.update();
            return;
        }
        
        aprChart.data.labels = data.map(d => {
            const date = new Date(d.timestamp);
            return hours <= 24 ? `${date.getHours()}:${String(date.getMinutes()).padStart(2,'0')}` : hours <= 168 ? `${date.getMonth()+1}/${date.getDate()} ${date.getHours()}:00` : `${date.getMonth()+1}/${date.getDate()}`;
        });
        aprChart.data.datasets[0].data = data.map(d => d.max_apr);
        aprChart.data.datasets[1].data = data.map(d => d.avg_apr);
        aprChart.update();
    } catch (error) {
        console.error('加载APR图表失败:', error);
    }
}

async function loadDvolChartData() {
    try {
        const currency = document.getElementById('currencySelect').value;
        const hours = chartPeriods.dvol;
        const response = await fetch(`${API_BASE}/api/charts/dvol?currency=${currency}&hours=${hours}`);
        const data = await response.json();
        
        if (!data || data.length === 0) {
            dvolChart.data.labels = [];
            dvolChart.data.datasets[0].data = [];
            dvolChart.update();
            return;
        }
        
        dvolChart.data.labels = data.map(d => {
            const date = new Date(d.timestamp);
            return hours <= 24 ? `${date.getHours()}:${String(date.getMinutes()).padStart(2,'0')}` : hours <= 168 ? `${date.getMonth()+1}/${date.getDate()} ${date.getHours()}:00` : `${date.getMonth()+1}/${date.getDate()}`;
        });
        dvolChart.data.datasets[0].data = data.map(d => d.dvol);
        dvolChart.update();
    } catch (error) {
        console.error('加载DVOL图表失败:', error);
    }
}

async function loadStats() {
    try {
        const response = await fetch(`${API_BASE}/api/stats`);
        const data = await response.json();
        document.getElementById('totalScans').textContent = data.total_scans;
        document.getElementById('todayScans').textContent = data.today_scans;
        document.getElementById('dbSize').textContent = data.db_size_mb + ' MB';
    } catch (error) {
        console.error('加载统计失败:', error);
    }
}

function showAlert(message, type = 'info') {
    const alertsList = document.getElementById('alertsList');
    if (alertsList.children.length === 1 && alertsList.children[0].textContent === '暂无预警') alertsList.innerHTML = '';
    
    const colors = { success: 'border-green-500 bg-green-500/10 text-green-400', error: 'border-red-500 bg-red-500/10 text-red-400', warning: 'border-yellow-500 bg-yellow-500/10 text-yellow-400', info: 'border-blue-500 bg-blue-500/10 text-blue-400' };
    const icons = { success: 'fa-check-circle', error: 'fa-exclamation-circle', warning: 'fa-exclamation-triangle', info: 'fa-info-circle' };
    const time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    
    const alert = document.createElement('div');
    alert.className = `border-l-4 p-3 rounded-lg text-sm ${colors[type]} flex items-start gap-2 animate-fade-in`;
    alert.innerHTML = `<i class="fas ${icons[type]} mt-0.5 flex-shrink-0"></i><div class="flex-1 min-w-0"><div class="text-xs text-gray-500 mb-0.5">${time}</div><div>${message}</div></div>`;
    alertsList.insertBefore(alert, alertsList.firstChild);
    while (alertsList.children.length > 20) alertsList.removeChild(alertsList.lastChild);
}

function addDemoAlerts() {
    const demoAlerts = [
        { msg: '系统就绪，点击"立即扫描"开始监控', type: 'info' },
        { msg: '新增功能：倍投修复计算器 - 输入浮亏金额自动计算修复方案', type: 'success' },
        { msg: '新增功能：风险水位预警 - Delta>0.45或价格接近Strike 2%时自动提醒', type: 'success' },
        { msg: '新增功能：点击高风险合约查看滚仓建议', type: 'success' },
        { msg: '新增功能：Vega列展示 - 判断权利金贵贱', type: 'success' }
    ];
    demoAlerts.forEach((alert, i) => setTimeout(() => showAlert(alert.msg, alert.type), i * 500));
}

setTimeout(addDemoAlerts, 1000);

// 点击模态框外部关闭
document.getElementById('rollModal').addEventListener('click', (e) => {
    if (e.target.id === 'rollModal') closeRollModal();
});


// 排序功能
let currentSort = { field: null, direction: 'desc' };

function sortContracts(field) {
    if (!currentData || !currentData.contracts || currentData.contracts.length === 0) return;
    
    // 切换排序方向
    if (currentSort.field === field) {
        currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
    } else {
        currentSort.field = field;
        currentSort.direction = 'desc';
    }
    
    // 更新表头图标
    updateSortIcons(field, currentSort.direction);
    
    // 排序数据
    const sortedContracts = [...currentData.contracts].sort((a, b) => {
        let valA = a[field];
        let valB = b[field];
        
        // 处理特殊字段
        if (field === 'delta') {
            valA = Math.abs(valA);
            valB = Math.abs(valB);
        }
        
        // 处理字符串排序
        if (typeof valA === 'string') {
            valA = valA.toLowerCase();
            valB = valB.toLowerCase();
        }
        
        if (valA === undefined || valA === null) valA = 0;
        if (valB === undefined || valB === null) valB = 0;
        
        if (currentSort.direction === 'asc') {
            return valA > valB ? 1 : valA < valB ? -1 : 0;
        } else {
            return valA < valB ? 1 : valA > valB ? -1 : 0;
        }
    });
    
    // 更新表格
    updateOpportunitiesTable(sortedContracts);
    
    showAlert(`已按 ${getFieldName(field)} ${currentSort.direction === 'asc' ? '升序' : '降序'} 排序`, 'info');
}

function updateSortIcons(activeField, direction) {
    document.querySelectorAll('#tableHeaders th[data-sort]').forEach(th => {
        const icon = th.querySelector('.sort-icon');
        if (!icon) return;
        
        const field = th.dataset.sort;
        if (field === activeField) {
            icon.className = `fas fa-sort-${direction === 'asc' ? 'up' : 'down'} text-xs text-orange-400`;
        } else {
            icon.className = 'fas fa-sort text-xs opacity-50';
        }
    });
}

function getFieldName(field) {
    const names = {
        'platform': '平台',
        'symbol': '合约',
        'dte': 'DTE',
        'strike': 'Strike',
        'delta': 'Delta',
        'gamma': 'Gamma',
        'vega': 'Vega',
        'apr': 'APR',
        'liquidity_score': '流动性',
        'loss_at_10pct': '-10%亏损'
    };
    return names[field] || field;
}

// 修复时区问题 - 覆盖原函数
function updateLastUpdateTime(timestamp) {
    // 将 '2026-04-05 16:40:18' 转换为 '2026-04-05T16:40:18'
    const date = new Date(timestamp.replace(' ', 'T'));
    const timeStr = date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    document.getElementById('lastUpdate').textContent = `更新于 ${timeStr}`;
}

// 修复图表时区问题
function parseLocalDate(timestamp) {
    return new Date(timestamp.replace(' ', 'T'));
}
