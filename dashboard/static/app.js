
function $(id) { return document.getElementById(id); }

function safeHTML(str) {
    if (str == null) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

const STRATEGY_PRESETS = {
    "PUT": {
        "conservative": {"max_delta": 0.20, "min_dte": 30, "max_dte": 45, "margin_ratio": 0.18, "min_apr": 12.0, "label": "纯收租"},
        "standard":     {"max_delta": 0.30, "min_dte": 14, "max_dte": 35, "margin_ratio": 0.20, "min_apr": 15.0, "label": "标准平衡"},
        "aggressive":   {"max_delta": 0.40, "min_dte": 7,  "max_dte": 28, "margin_ratio": 0.22, "min_apr": 20.0, "label": "折价接货"}
    },
    "CALL": {
        "conservative": {"max_delta": 0.15, "min_dte": 30, "max_dte": 45, "margin_ratio": 0.18, "min_apr": 8.0, "label": "保留上涨"},
        "standard":     {"max_delta": 0.25, "min_dte": 14, "max_dte": 35, "margin_ratio": 0.20, "min_apr": 10.0, "label": "标准备兑"},
        "aggressive":   {"max_delta": 0.35, "min_dte": 7,  "max_dte": 28, "margin_ratio": 0.22, "min_apr": 15.0, "label": "强横盘"}
    }
};
let _currentPreset = 'standard';

/**
 * 期权监控面板 - 前端逻辑
 * 包含：实时扫描、倍投修复计算器、风险预警、滚仓建议
 */

let currentData = null;
let autoRefreshInterval = null;
let aprChart = null;
let dvolChart = null;
let chartPeriods = { apr: 168, dvol: 168, pcr: 168 };
let currentSpotPrice = null;
let scanStatusInterval = null;

const API_BASE = '';
const API_TIMEOUT_MS = 15000;
const FETCH_MAX_RETRIES = 1;

async function safeFetch(url, options = {}, retries = FETCH_MAX_RETRIES) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), API_TIMEOUT_MS);
    try {
        const opts = {...options, signal: controller.signal};
        const res = await fetch(url, opts);
        clearTimeout(timer);
        if (!res.ok) throw new Error(`HTTP ${res.status}: ${res.statusText}`);
        return res;
    } catch (e) {
        clearTimeout(timer);
        if (retries > 0 && e.name !== 'AbortError') {
            await new Promise(r => setTimeout(r, 1000));
            return safeFetch(url, options, retries - 1);
        }
        throw e;
    }
}



document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    loadLatestData();
    loadStats();
    setupEventListeners();
    updateParamDisplay();
    setAutoRefresh(5);
    requestNotificationPermission();
    loadPcrChart();
    
    // v8.0: 添加网络状态监听
    window.addEventListener('online', () => {
        showAlert('网络连接已恢复', 'success');
        loadLatestData();
    });

    window.addEventListener('offline', () => {
        showAlert('网络连接已断开', 'warning');
    });
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
    const recoveryLossEl = document.getElementById('recoveryLoss');
    if (recoveryLossEl) {
        recoveryLossEl.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') calculateRecovery();
        });
    }
}

function updateParamDisplay() {
    const currency = document.getElementById('currencySelect')?.value || 'BTC';
    const minDte = document.getElementById('minDte')?.value || '--';
    const maxDte = document.getElementById('maxDte')?.value || '--';
    const maxDelta = document.getElementById('maxDelta')?.value || '--';
    const optionType = document.getElementById('optionType')?.value || 'PUT';
    const strike = document.getElementById('strikeInput')?.value || '';
    const strikeRange = document.getElementById('strikeRangeInput')?.value || '';
    const presetLabel = {'conservative': '保守', 'standard': '标准', 'aggressive': '进取'}[_currentPreset] || '';

    let display = `${currency} | DTE ${minDte}-${maxDte} | Δ≤${maxDelta} | ${optionType === 'PUT' ? 'Sell Put' : 'Covered Call'}`;
    if (strike) display += ` | Strike=${strike}`;
    else if (strikeRange) display += ` | Range=${strikeRange}`;
    if (presetLabel) display += ` | ${presetLabel}`;

    const paramsEl = document.getElementById('currentParams');
    if (paramsEl) paramsEl.textContent = display;
    const labelEl = document.getElementById('currencyLabel');
    if (labelEl) labelEl.textContent = `${currency}/USDT`;
}

async function setAutoRefresh(minutes) {
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

function initCharts() {
    // APR图表
    const aprCtx = document.getElementById('aprChart').getContext('2d');
    aprChart = new Chart(aprCtx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: '最佳安全APR (Δ≤0.25)',
                data: [],
                borderColor: '#22c55e',
                backgroundColor: 'rgba(34, 197, 94, 0.1)',
                tension: 0.4,
                fill: true,
                borderWidth: 2,
                pointRadius: 2,
                pointHoverRadius: 4
            }, {
                label: 'P75安全APR',
                data: [],
                borderColor: '#3b82f6',
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
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
                legend: {
                    labels: {
                        color: '#9ca3af',
                        font: { size: 11 },
                        usePointStyle: true,
                        boxWidth: 6,
                        padding: 15
                    }
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    backgroundColor: 'rgba(30, 58, 95, 0.95)',
                    titleColor: '#fff',
                    bodyColor: '#fff',
                    borderColor: 'rgba(255, 255, 255, 0.1)',
                    borderWidth: 1,
                    callbacks: {
                        label: function(ctx) {
                            return ctx.dataset.label + ': ' + (ctx.parsed.y != null ? ctx.parsed.y.toFixed(1) + '%' : 'N/A');
                        }
                    },
                    padding: 10
                }
            },
            scales: {
                x: {
                    ticks: {
                        color: '#6b7280',
                        font: { size: 10 },
                        maxTicksLimit: 8
                    },
                    grid: { color: 'rgba(75, 85, 99, 0.2)' }
                },
                y: {
                    ticks: {
                        color: '#6b7280',
                        font: { size: 10 },
                        callback: function(value) { return value + '%'; }
                    },
                    grid: { color: 'rgba(75, 85, 99, 0.2)' }
                }
            },
            interaction: { intersect: false, mode: 'index' }
        }
    });

    // DVOL图表
    const dvolCtx = document.getElementById('dvolChart').getContext('2d');
    dvolChart = new Chart(dvolCtx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'DVOL',
                data: [],
                borderColor: '#3b82f6',
                backgroundColor: 'rgba(59, 130, 246, 0.1)',
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
                legend: { display: false },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    backgroundColor: 'rgba(30, 58, 95, 0.95)',
                    titleColor: '#fff',
                    bodyColor: '#fff',
                    borderColor: 'rgba(255, 255, 255, 0.1)',
                    borderWidth: 1,
                    padding: 10,
                    callbacks: {
                        label: (ctx) => `DVOL: ${ctx.parsed.y.toFixed(2)}`
                    }
                }
            },
            scales: {
                x: {
                    ticks: {
                        color: '#6b7280',
                        font: { size: 10 },
                        maxTicksLimit: 8
                    },
                    grid: { color: 'rgba(75, 85, 99, 0.2)' }
                },
                y: {
                    ticks: {
                        color: '#6b7280',
                        font: { size: 10 }
                    },
                    grid: { color: 'rgba(75, 85, 99, 0.2)' }
                }
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
            btn.classList.remove('bg-gray-700/50', 'hover:bg-gray-600');
            btn.classList.add('bg-orange-500', 'text-white');
        } else {
            btn.classList.add('bg-gray-700/50', 'hover:bg-gray-600');
            btn.classList.remove('bg-orange-500', 'text-white');
        }
    });
    if (chartType === 'apr') loadAprChartData();
    else if (chartType === 'dvol') loadDvolChartData();
    else if (chartType === 'pcr') loadPcrChart(document.getElementById('currencySelect').value, hours);
}

let _scanLock = false;

async function triggerScan() {
    if (_scanLock) return;
    _scanLock = true;
    const btn = document.getElementById('scanBtn');
    const icon = document.getElementById('scanIcon');
    const searchIcon = btn.querySelector('.fa-search');
    btn.disabled = true;
    if (searchIcon) searchIcon.style.display = 'none';
    if (icon) { icon.style.display = ''; icon.classList.add('fa-spin'); }

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

        const response = await safeFetch(`${API_BASE}/api/quick-scan`, {
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
        const searchIcon = btn.querySelector('.fa-search');
        if (searchIcon) searchIcon.style.display = '';
        if (icon) { icon.classList.remove('fa-spin'); icon.style.display = 'none'; }
        _scanLock = false;
    }
}

let currentCalcMode = 'roll';

function setCalcMode(mode) {
    currentCalcMode = mode;
    const rollBtn = document.getElementById('modeRollBtn');
    const newBtn = document.getElementById('modeNewBtn');
    const rollFields = document.getElementById('scRollFields');
    const newFields = document.getElementById('scNewFields');

    if (mode === 'roll') {
        rollBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-orange-500/20 border border-orange-500/50 text-orange-400';
        newBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-gray-700/50 border border-gray-600 text-gray-400 hover:bg-gray-600/50';
        rollFields.classList.remove('hidden');
        newFields.classList.add('hidden');
    } else {
        newBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-blue-500/20 border border-blue-500/50 text-blue-400';
        rollBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-gray-700/50 border border-gray-600 text-gray-400 hover:bg-gray-600/50';
        newFields.classList.remove('hidden');
        rollFields.classList.add('hidden');
    }
}

async function submitStrategyCalc() {
    const btn = document.getElementById('scSubmitBtn');
    const wrapper = document.getElementById('scResultsWrapper');

    const params = {
        currency: document.getElementById('scCurrency').value,
        mode: currentCalcMode,
        option_type: document.getElementById('scOptionType').value,
        reserve_capital: parseFloat(document.getElementById('scReserve').value) || 50000,
        target_max_delta: parseFloat(document.getElementById('scMaxDelta').value) || 0.35,
        min_dte: parseInt(document.getElementById('scMinDte').value) || 7,
        max_dte: parseInt(document.getElementById('scMaxDte').value) || 90,
        margin_ratio: 0.2
    };

    if (currentCalcMode === 'roll') {
        params.old_strike = parseFloat(document.getElementById('scOldStrike').value);
        params.old_qty = parseFloat(document.getElementById('scOldQty').value) || 1;
        params.close_cost_total = parseFloat(document.getElementById('scCloseCost').value) || 0;
        params.max_qty_multiplier = parseFloat(document.getElementById('scMaxMult').value) || 3;
        if (!params.old_strike) {
            showAlert('请输入旧行权价', 'error');
            return;
        }
    } else {
        params.target_apr = parseFloat(document.getElementById('scTargetApr').value) || 200;
    }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 计算中...';
    wrapper.innerHTML = '<div class="text-center py-12 text-cyan-400"><i class="fas fa-spinner fa-spin text-3xl mb-2"></i><p>计算中...</p></div>';

    try {
        const response = await safeFetch(`${API_BASE}/api/strategy-calc`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params)
        });
        const result = await response.json();

        if (!result.success) {
            wrapper.innerHTML = `<div class="text-center py-12 text-red-400"><i class="fas fa-times-circle text-3xl mb-2"></i><p>${safeHTML(result.error || '计算失败')}</p></div>`;
        } else {
            displayStrategyCalcResult(result, wrapper);
        }
    } catch (error) {
        wrapper.innerHTML = `<div class="text-center py-12 text-red-400"><i class="fas fa-times-circle text-3xl mb-2"></i><p>错误: ${safeHTML(error.message)}</p></div>`;
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-magic"></i> 计算方案';
    }
}

function displayStrategyCalcResult(result, wrapper) {
    const plans = result.plans || [];
    if (plans.length === 0) {
        const meta = result.meta || {};
        wrapper.innerHTML = `<div class="text-center py-12 text-yellow-400">
            <i class="fas fa-search text-3xl mb-2 opacity-50"></i>
            <p>未找到符合条件的方案</p>
            <p class="text-xs text-gray-500 mt-2">扫描了 ${meta.total_contracts_scanned || 0} 个合约</p>
        </div>`;
        return;
    }

    let html = `<div class="overflow-x-auto">
        <table class="w-full text-xs">
            <thead class="bg-gray-800/80">
                <tr class="text-gray-400 border-b border-gray-700/50">
                    <th class="text-left py-2 px-2 font-medium">排名</th>
                    <th class="text-left py-2 px-2 font-medium">合约</th>
                    <th class="text-right py-2 px-2 font-medium">Strike</th>
                    <th class="text-center py-2 px-2 font-medium">DTE</th>
                    <th class="text-right py-2 px-2 font-medium">Delta</th>
                    <th class="text-right py-2 px-2 font-medium">APR</th>
                    ${result.mode === 'roll' ? '<th class="text-right py-2 px-2 font-medium">数量</th><th class="text-right py-2 px-2 font-medium">净流入</th>' : '<th class="text-right py-2 px-2 font-medium">保证金</th><th class="text-right py-2 px-2 font-medium">权利金</th>'}
                    <th class="text-right py-2 px-2 font-medium">ROI%</th>
                    <th class="text-right py-2 px-2 font-medium">评分</th>
                </tr>
            </thead>
            <tbody class="divide-y divide-gray-800/30">`;

    plans.forEach((plan, idx) => {
        const isBest = idx === 0;
        html += `<tr class="hover:bg-white/5 transition ${isBest ? 'bg-green-500/10' : ''}">
            <td class="py-3 px-2">${isBest ? '<i class="fas fa-crown text-yellow-400"></i>' : idx + 1}</td>
            <td class="py-3 px-2"><span class="font-mono text-white">${safeHTML(plan.symbol)}</span><br><span class="text-[10px] text-gray-500">${safeHTML(plan.platform)}</span></td>
            <td class="py-3 px-2 text-right font-mono text-orange-400">${plan.strike?.toLocaleString()}</td>
            <td class="py-3 px-2 text-center">${plan.dte}</td>
            <td class="py-3 px-2 text-right">${plan.delta?.toFixed(3)}</td>
            <td class="py-3 px-2 text-right text-green-400">${plan.apr?.toFixed(1)}%</td>
            ${result.mode === 'roll' ? `<td class="py-3 px-2 text-right">${plan.new_qty}</td><td class="py-3 px-2 text-right text-cyan-400">$${plan.net_credit?.toFixed(2)}</td>` : `<td class="py-3 px-2 text-right">$${plan.margin_req?.toFixed(2)}</td><td class="py-3 px-2 text-right text-green-400">$${plan.gross_credit?.toFixed(2)}</td>`}
            <td class="py-3 px-2 text-right text-yellow-400 font-bold">${plan.roi_pct?.toFixed(1)}%</td>
            <td class="py-3 px-2 text-right">${plan.score?.toFixed(4)}</td>
        </tr>`;
    });

    html += '</tbody></table></div>';
    html += `<div class="mt-3 text-xs text-gray-500">扫描了 ${result.meta?.total_contracts_scanned || 0} 个合约，找到 ${result.meta?.plans_found || 0} 个方案</div>`;
    wrapper.innerHTML = html;
}

async function calculateRecovery() {
    showAlert('请使用策略计算器', 'info');
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
        // 添加网络状态检测
        if (!navigator.onLine) {
            showAlert('网络连接已断开，刷新失败', 'error');
            return;
        }
        
        const currency = document.getElementById('currencySelect').value;
        const response = await safeFetch(`${API_BASE}/api/latest?currency=${currency}`);

        const data = await response.json();
        currentData = data;
        if (data.spot_price) currentSpotPrice = data.spot_price;

        updateMacroIndicators(data);
        if (data.dvol_interpretation || data.dvol_trend_label) {
            showDvolAdvice(data.currency || 'BTC');
        }
        updateOpportunitiesTable(data.contracts || []);
        updateLargeTrades(data.large_trades_details || [], data.large_trades_count || 0);
        updateLastUpdateTime(data.timestamp);
        loadAprChartData();
        loadDvolChartData();
        loadPcrChart(currency, chartPeriods.pcr || 168);
        
        // v8.0: Load risk dashboard (unified)
        loadRiskDashboard(currency);
        
        
        // v8.0: 非阻塞式加载网格策略数据
        loadGridStrategyData().catch(() => {});
        
        showAlert('数据刷新成功', 'success');
    } catch (error) {
        console.error('加载数据失败:', error);
        showAlert(`数据刷新失败: ${error.message}`, 'error');
        
        // 错误重试机制 - 只重试一次，避免无限循环
        if (!loadLatestData._retrying) {
            loadLatestData._retrying = true;
            setTimeout(() => {
                if (navigator.onLine) {
                    loadLatestData();
                }
                loadLatestData._retrying = false;
            }, 10000);
        }
    }
}

async function loadRiskDashboard(currency = 'BTC') {
    try {
        const res = await safeFetch(`${API_BASE}/api/risk/overview?currency=${currency}`);
        const data = await res.json();
        
        updateRiskDashboardUI(data);
    } catch (e) {
        console.error('Failed to load risk dashboard:', e);
    }
}

function updateRiskDashboardUI(data) {
    const scoreBadge = document.getElementById('riskScoreBadge');
    if (scoreBadge) {
        scoreBadge.textContent = `综合风险: ${data.composite_score}`;
        scoreBadge.className = 'px-3 py-1 rounded-full text-sm font-bold ';
        if (data.risk_level === 'LOW') {
            scoreBadge.classList.add('bg-green-500/20', 'text-green-400');
        } else if (data.risk_level === 'MEDIUM') {
            scoreBadge.classList.add('bg-yellow-500/20', 'text-yellow-400');
        } else if (data.risk_level === 'HIGH') {
            scoreBadge.classList.add('bg-orange-500/20', 'text-orange-400');
        } else {
            scoreBadge.classList.add('bg-red-500/20', 'text-red-400', 'animate-pulse');
        }
    }
    
    // Status Badge
    const badge = document.getElementById('rfStatusBadge');
    if (badge && data.status) {
        badge.innerText = data.status;
        badge.className = 'px-2 py-0.5 rounded text-xs font-bold uppercase tracking-wider ';
        if (data.status === 'NORMAL') {
            badge.classList.add('bg-green-500/20', 'text-green-400');
        } else if (data.status === 'NEAR_FLOOR') {
            badge.classList.add('bg-blue-500/20', 'text-blue-400');
        } else if (data.status === 'ADVERSE') {
            badge.classList.add('bg-orange-500/20', 'text-orange-400', 'animate-pulse');
        } else if (data.status === 'PANIC') {
            badge.classList.add('bg-red-500/20', 'text-red-400', 'animate-bounce');
        }
    }
    
    // 4维度风险分数
    const components = data.components;
    if (components) {
        const priceScore = document.getElementById('priceRiskScore');
        const priceLevel = document.getElementById('priceRiskLevel');
        if (priceScore && components.price_risk) {
            priceScore.textContent = components.price_risk.score;
            priceScore.className = 'text-2xl font-bold ' + getRiskColor(components.price_risk.score);
            priceLevel.textContent = components.price_risk.status;
        }
        
        const volScore = document.getElementById('volRiskScore');
        const volLevel = document.getElementById('volRiskLevel');
        if (volScore && components.volatility_risk) {
            volScore.textContent = components.volatility_risk.score;
            volScore.className = 'text-2xl font-bold ' + getRiskColor(components.volatility_risk.score);
            volLevel.textContent = components.volatility_risk.signal || '正常';
        }
        
        const sentScore = document.getElementById('sentimentRiskScore');
        const sentLevel = document.getElementById('sentimentRiskLevel');
        if (sentScore && components.sentiment_risk) {
            sentScore.textContent = components.sentiment_risk.score;
            sentScore.className = 'text-2xl font-bold ' + getRiskColor(components.sentiment_risk.score);
            sentLevel.textContent = '基于价格';
        }
        
        const liqScore = document.getElementById('liquidityRiskScore');
        const liqLevel = document.getElementById('liquidityRiskLevel');
        if (liqScore && components.liquidity_risk) {
            liqScore.textContent = components.liquidity_risk.score;
            liqScore.className = 'text-2xl font-bold ' + getRiskColor(components.liquidity_risk.score);
            liqLevel.textContent = '正常';
        }
    }
    
    // 策略建议
    const adviceList = document.getElementById('rfAdviceList');
    if (adviceList && data.advice) {
        adviceList.innerHTML = data.advice.map(a => `<li>${safeHTML(a)}</li>`).join('');
    }
    
    // 推荐操作
    const actionList = document.getElementById('rfActionList');
    if (actionList && data.recommended_actions) {
        actionList.innerHTML = data.recommended_actions.map(a => 
            `<span class="px-3 py-1 bg-green-500/20 border border-green-500/30 rounded-full text-xs text-green-300 font-medium">
                <i class="fas fa-check mr-1"></i> ${safeHTML(a)}
            </span>`
        ).join('');
    }
    
    // 市场痛点
    const mpEl = document.getElementById('rfMaxPain');
    if (mpEl) {
        mpEl.innerText = data.max_pain ? `$${data.max_pain.toLocaleString()}` : '--';
    }
    
    const distEl = document.getElementById('rfPainDist');
    if (distEl && data.max_pain && data.spot) {
        const diff = data.max_pain - data.spot;
        const pct = (diff / data.spot * 100).toFixed(1);
        const color = diff > 0 ? 'text-green-400' : 'text-red-400';
        const icon = diff > 0 ? '↑' : '↓';
        distEl.innerHTML = `<span class="${color}">${icon} ${Math.abs(diff).toLocaleString()} (${pct}%)</span>`;
    }
    
    // MM Signal
    const mmEl = document.getElementById('rfMmSignal');
    if (mmEl) {
        mmEl.innerHTML = data.mm_signal ? `<i class="fas fa-info-circle mr-2"></i> ${safeHTML(data.mm_signal)}` : '暂无做市商对冲信号';
    }
    
    // 支撑位
    if (data.floors) {
        const regularEl = document.getElementById('regularFloor');
        const extremeEl = document.getElementById('extremeFloor');
        const regularHeader = document.getElementById('floorRegularHeader');
        const extremeHeader = document.getElementById('floorExtremeHeader');
        
        if (regularEl) regularEl.textContent = `$${data.floors.regular.toLocaleString()}`;
        if (extremeEl) extremeEl.textContent = `$${data.floors.extreme.toLocaleString()}`;
        if (regularHeader) regularHeader.textContent = `$${data.floors.regular.toLocaleString()}`;
        if (extremeHeader) extremeHeader.textContent = `$${data.floors.extreme.toLocaleString()}`;
    }

    // 仓位建议
    const posGuide = data.position_guidance;
    if (posGuide) {
        const posEl = document.getElementById('positionGuidance');
        if (posEl) {
            const maxPct = posGuide.max_position_pct;
            const deltaRange = posGuide.suggested_delta_range;
            const dteRange = posGuide.suggested_dte;
            posEl.innerHTML = `
                <div class="flex items-center gap-4 text-sm">
                    <div class="flex items-center gap-2">
                        <i class="fas fa-chart-pie text-blue-400"></i>
                        <span class="text-gray-400">最大仓位:</span>
                        <span class="font-bold ${maxPct === 0 ? 'text-red-400' : maxPct <= 15 ? 'text-orange-400' : maxPct >= 40 ? 'text-emerald-400' : 'text-green-300'}">${maxPct}%</span>
                    </div>
                    <div class="flex items-center gap-2">
                        <i class="fas fa-crosshairs text-yellow-400"></i>
                        <span class="text-gray-400">Delta:</span>
                        <span class="font-mono font-bold text-yellow-300">${deltaRange}</span>
                    </div>
                    <div class="flex items-center gap-2">
                        <i class="fas fa-clock text-purple-400"></i>
                        <span class="text-gray-400">DTE:</span>
                        <span class="font-mono font-bold text-purple-300">${dteRange}</span>
                    </div>
                </div>
            `;
        }
    }

    // Put Wall / Gamma Flip
    const pwEl = document.getElementById('putWallInfo');
    const gfEl = document.getElementById('gammaFlipInfo');
    if (pwEl && data.put_wall) {
        const pw = data.put_wall;
        const distPct = ((data.spot - pw.strike) / pw.strike * 100).toFixed(1);
        pwEl.innerHTML = `<span class="text-emerald-400 font-bold">$${pw.strike.toLocaleString()}</span> <span class="text-gray-500 text-xs">(OI: ${pw.oi.toLocaleString()})</span> <span class="${distPct > 0 ? 'text-emerald-400' : 'text-red-400'} text-xs">${distPct > 0 ? '↑' + distPct + '%' : '↓' + Math.abs(distPct) + '%'}</span>`;
    } else if (pwEl) {
        pwEl.innerHTML = '<span class="text-gray-500">--</span>';
    }
    if (gfEl && data.gamma_flip) {
        const gf = data.gamma_flip;
        const isAbove = data.spot > gf.strike;
        gfEl.innerHTML = `<span class="${isAbove ? 'text-emerald-400' : 'text-red-400'} font-bold">$${gf.strike.toLocaleString()}</span> <span class="text-xs ${isAbove ? 'text-emerald-400' : 'text-red-400'}">${isAbove ? '✅ 多头Gamma区' : '⚠️ 空头Gamma区'}</span>`;
    } else if (gfEl) {
        gfEl.innerHTML = '<span class="text-gray-500">--</span>';
    }

    // 更新顶部指标卡
    const supportDistCard = document.getElementById('supportDistCard');
    if (supportDistCard && data.floors && data.spot) {
        const distPct = ((data.spot - data.floors.regular) / data.floors.regular * 100).toFixed(1);
        supportDistCard.textContent = distPct + '%';
        supportDistCard.className = 'text-2xl font-bold ' + (distPct >= 15 ? 'text-emerald-400' : distPct >= 5 ? 'text-yellow-400' : 'text-red-400');
    }
    const riskScoreCard = document.getElementById('riskScoreCard');
    const riskLevelCard = document.getElementById('riskLevelCard');
    if (riskScoreCard) {
        riskScoreCard.textContent = data.composite_score;
        riskScoreCard.className = 'text-2xl font-bold ' + getRiskColor(data.composite_score);
    }
    if (riskLevelCard) {
        riskLevelCard.textContent = data.risk_level || '综合风险';
    }
    
    // 更新链上核心指标（v8.1新增）
    updateOnchainMetrics(data.onchain_metrics);
}

function updateOnchainMetrics(onchain) {
    console.log('[OnChain] 更新链上指标:', onchain);
    
    if (!onchain) {
        console.log('[OnChain] 警告: onchain 数据为空');
        return;
    }
    
    // MVRV Ratio
    const mvrvEl = document.getElementById('onchainMVRV');
    const mvrvSignalEl = document.getElementById('onchainMVRVSignal');
    console.log('[OnChain] MVRV元素:', mvrvEl ? '找到' : '未找到');
    
    if (mvrvEl && mvrvSignalEl) {
        if (onchain.mvrv_ratio !== null && onchain.mvrv_ratio !== undefined) {
            mvrvEl.textContent = onchain.mvrv_ratio.toFixed(2);
            mvrvSignalEl.textContent = onchain.mvrv_signal || '--';
            console.log('[OnChain] MVRV更新:', onchain.mvrv_ratio);
            
            // 根据 MVRV 值设置颜色
            if (onchain.mvrv_ratio < 1) {
                mvrvEl.className = 'text-xl font-bold text-green-400';
                mvrvSignalEl.className = 'text-xs mt-1 text-green-500';
            } else if (onchain.mvrv_ratio < 3.5) {
                mvrvEl.className = 'text-xl font-bold text-yellow-400';
                mvrvSignalEl.className = 'text-xs mt-1 text-yellow-500';
            } else {
                mvrvEl.className = 'text-xl font-bold text-red-400';
                mvrvSignalEl.className = 'text-xs mt-1 text-red-500';
            }
        } else {
            mvrvEl.textContent = '--';
            mvrvSignalEl.textContent = onchain.mvrv_signal || '数据加载中...';
        }
    }
    
    // 200周均线
    const wmaEl = document.getElementById('onchain200WMA');
    const wmaRatioEl = document.getElementById('onchain200WMARatio');
    if (wmaEl && wmaRatioEl) {
        if (onchain.price_200wma !== null && onchain.price_200wma !== undefined) {
            wmaEl.textContent = `$${onchain.price_200wma.toLocaleString()}`;
            if (onchain.price_to_200wma_ratio) {
                const ratio = onchain.price_to_200wma_ratio;
                wmaRatioEl.textContent = `当前价格 / 200WMA = ${ratio.toFixed(2)}x`;
                if (ratio > 1.5) {
                    wmaRatioEl.className = 'text-xs mt-1 text-red-400';
                } else if (ratio > 1.0) {
                    wmaRatioEl.className = 'text-xs mt-1 text-yellow-400';
                } else {
                    wmaRatioEl.className = 'text-xs mt-1 text-green-400';
                }
            }
        } else {
            wmaEl.textContent = '--';
            wmaRatioEl.textContent = '数据加载中...';
        }
    }
    
    // Balanced Price
    const bpEl = document.getElementById('onchainBalancedPrice');
    const bpRatioEl = document.getElementById('onchainBalancedPriceRatio');
    if (bpEl && bpRatioEl) {
        if (onchain.balanced_price !== null && onchain.balanced_price !== undefined) {
            bpEl.textContent = `$${onchain.balanced_price.toLocaleString()}`;
            if (onchain.current_price && onchain.balanced_price > 0) {
                const ratio = (onchain.current_price / onchain.balanced_price).toFixed(2);
                bpRatioEl.textContent = `当前价格 / BP = ${ratio}x`;
            }
        } else {
            bpEl.textContent = '--';
            bpRatioEl.textContent = '数据加载中...';
        }
    }
    
    // 减半倒计时
    const halvingEl = document.getElementById('onchainHalvingDays');
    const halvingDateEl = document.getElementById('onchainHalvingDate');
    if (halvingEl) {
        if (onchain.halving_days_remaining !== null && onchain.halving_days_remaining !== undefined) {
            halvingEl.textContent = `${onchain.halving_days_remaining} 天`;
            
            // 估算减半日期
            const halvingDate = new Date();
            halvingDate.setDate(halvingDate.getDate() + onchain.halving_days_remaining);
            halvingDateEl.textContent = `预计 ${halvingDate.getFullYear()}年${halvingDate.getMonth() + 1}月`;
        } else {
            halvingEl.textContent = '--';
            halvingDateEl.textContent = '--';
        }
    }
    
    // MVRV 周期模式分析（参考 Murphy 的推文）
    const analysisEl = document.getElementById('onchainMVRVAnalysis');
    if (analysisEl && onchain.mvrv_ratio !== null && onchain.mvrv_ratio !== undefined) {
        analysisEl.classList.remove('hidden');
        
        let analysisText = '<i class="fas fa-chart-line mr-2"></i> ';
        analysisText += '<b>MVRV 周期模式分析（参考 Messari & Murphy）</b><br>';
        analysisText += '• BTC 前3轮减半后 MVRV 走势高度同频<br>';
        analysisText += '• 历史顶部信号：MVRV > 3.5（Messari 2022年报告）<br>';
        analysisText += '• 历史底部信号：MVRV < 1.0<br>';
        
        if (onchain.mvrv_ratio < 1) {
            analysisText += `<br><span class="text-green-400">📊 当前 MVRV ${onchain.mvrv_ratio.toFixed(2)} 处于历史底部区域，可能是积累机会</span>`;
        } else if (onchain.mvrv_ratio < 3.5) {
            analysisText += `<br><span class="text-yellow-400">📊 当前 MVRV ${onchain.mvrv_ratio.toFixed(2)} 处于正常区间</span>`;
        } else {
            analysisText += `<br><span class="text-red-400">📊 当前 MVRV ${onchain.mvrv_ratio.toFixed(2)} 处于过热区域，注意风险</span>`;
        }
        
        analysisEl.innerHTML = analysisText;
    }
}

function getRiskColor(score) {
    if (score < 30) return 'text-green-400';
    if (score < 60) return 'text-yellow-400';
    if (score < 80) return 'text-orange-400';
    return 'text-red-400';
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
    const dvolInterp = data.dvol_interpretation || '';
    const dvolTrend = data.dvol_trend_label || data.dvol_trend || '';

    if (dvolInterp) {
        dvolSignal.textContent = dvolInterp;
        dvolSignal.className = dvolTrend.includes('上涨') ? 'text-xs mt-1 text-red-400 font-medium' : dvolTrend.includes('下跌') ? 'text-xs mt-1 text-green-400 font-medium' : 'text-xs mt-1 text-gray-400';
    } else if (signal) {
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

let _expandedRow = null;

// 更新后的表格渲染函数 - 精简为12列核心数据
function updateOpportunitiesTable(contracts) {
    const tbody = document.getElementById('opportunitiesTable');
    const countEl = document.getElementById('contractCount');
    countEl.textContent = `${contracts.length} 个合约`;

    if (contracts.length === 0) {
        tbody.innerHTML = `<tr><td colspan="25" class="text-center py-12 text-gray-500"><div class="flex flex-col items-center gap-3"><i class="fas fa-inbox text-3xl text-gray-600"></i><p>暂无符合条件的合约</p><p class="text-xs text-gray-600">尝试调整扫描参数</p></div></td></tr>`;
        return;
    }

    let highRiskContracts = [];

    // 分页加载：初始显示20个，支持加载更多
    let PAGE_SIZE = 20;
    if (!window.displayedContracts) window.displayedContracts = [];
    if (!window.contractPage) window.contractPage = 1;
    // 始终用传入的 contracts 更新显示数据（排序时需要更新）
    window.displayedContracts = contracts.slice(0, window.contractPage * PAGE_SIZE);
    const displayContracts = window.displayedContracts;
    const hasMore = displayContracts.length < contracts.length;

    tbody.innerHTML = displayContracts.map((contract, idx) => {
        const platformColor = contract.platform === 'Deribit' ? 'text-blue-400' : 'text-yellow-400';
        const platformBg = contract.platform === 'Deribit' ? 'bg-blue-500/10' : 'bg-yellow-500/10';
        const liqColor = contract.liquidity_score >= 70 ? 'text-green-400' : contract.liquidity_score >= 40 ? 'text-yellow-400' : 'text-red-400';
        const liqBg = contract.liquidity_score >= 70 ? 'bg-green-500/10' : contract.liquidity_score >= 40 ? 'bg-yellow-500/10' : 'bg-red-500/10';
        const deltaAbs = Math.abs(contract.delta);

        const symbol = contract.symbol || contract.instrument_name || 'N/A';
        contract.symbol = symbol;

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
            riskBadge = '<span class="risk-badge bg-red-500 text-[10px] text-white px-1.5 py-0.5 rounded font-bold"><i class="fas fa-exclamation-triangle"></i> 极高</span>';
            riskLevel = '极高';
            highRiskContracts.push({ contract, reason: `Delta(${deltaAbs.toFixed(3)})>0.45 且 价格接近Strike(${distancePct.toFixed(1)}%)` });
        } else if (isHighDelta) {
            riskClass = 'risk-alert-high';
            riskBadge = '<span class="risk-badge bg-red-500 text-[10px] text-white px-1.5 py-0.5 rounded font-bold"><i class="fas fa-exclamation"></i> 高</span>';
            riskLevel = '高';
            highRiskContracts.push({ contract, reason: `Delta(${deltaAbs.toFixed(3)})>0.45` });
        } else if (isNearStrike) {
            riskClass = 'risk-alert-medium';
            riskBadge = '<span class="bg-orange-500 text-[10px] text-white px-1.5 py-0.5 rounded"><i class="fas fa-exclamation-circle"></i> 接近</span>';
            riskLevel = '中';
        } else if (deltaAbs > 0.35) {
            riskBadge = '<span class="bg-yellow-500/80 text-[10px] text-white px-1.5 py-0.5 rounded">警告</span>';
            riskLevel = '警告';
        } else {
            riskBadge = '<span class="bg-green-500/50 text-[10px] text-white px-1.5 py-0.5 rounded">正常</span>';
            riskLevel = '正常';
        }

        // 精简版12列表格数据
        const spreadColor = (contract.spread_pct || 0) > 5 ? 'text-orange-400' : 'text-gray-400';
        const lossVal = Math.abs(contract.loss_at_10pct || 0);
        const breakeven = contract.breakeven || 0;
        const oi = contract.open_interest || 0;
        const spreadPct = contract.spread_pct || 0;

        const gamma = contract.gamma || 0;
        const vega = contract.vega || 0;
        const theta = contract.theta || 0;
        const iv = contract.mark_iv || contract.iv || 0;
        const pop = contract.pop || null;
        const bePct = contract.breakeven_pct || null;
        const ivRank = contract.iv_rank || null;
        const marginReq = contract.margin_required || 0;
        const capEff = contract.capital_efficiency || 0;
        const supportDist = contract.support_distance_pct;
        const isPut = contract.option_type === 'P' || contract.option_type === 'PUT';

        return `<tr class="hover:bg-white/[0.02] transition ${riskClass}">
            <td class="py-2 px-3 text-center"><span class="${platformColor} text-xs font-semibold">${contract.platform}</span></td>
            <td class="py-2 px-2 text-center"><span class="${isPut ? 'text-green-400' : 'text-blue-400'} text-xs font-bold">${contract.option_type || 'PUT'}</span></td>
            <td class="py-2 px-2 text-center font-mono text-xs tabular-nums">${symbol.split('-')[1] || ''}</td>
            <td class="py-2 px-2 text-center text-xs tabular-nums">${(contract.dte || 0).toFixed(0)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums">$${Math.round(contract.strike).toLocaleString()}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums font-semibold ${deltaAbs > 0.35 ? 'text-red-400' : deltaAbs > 0.25 ? 'text-yellow-400' : 'text-green-400'}">${deltaAbs.toFixed(4)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums font-semibold ${theta > 5 ? 'text-emerald-400' : theta > 0 ? 'text-green-300' : 'text-gray-500'}" title="每日时间价值衰减">${theta > 0 ? '+' : ''}${theta.toFixed(2)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${gamma > 0.15 ? 'text-orange-400' : 'text-gray-300'}">${gamma.toFixed(4)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${vega > 50 ? 'text-yellow-400' : 'text-gray-300'}">${vega.toFixed(1)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${iv ? (iv >= 80 ? 'text-red-400' : iv >= 50 ? 'text-yellow-400' : 'text-emerald-400') : 'text-gray-300'}">${iv ? iv.toFixed(1) + '%' : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs font-bold text-green-400 tabular-nums">${(contract.apr || 0).toFixed(1)}%</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${pop ? (isPut ? (pop >= 70 ? 'text-emerald-400' : pop >= 50 ? 'text-yellow-300' : 'text-orange-400') : (pop <= 30 ? 'text-emerald-400' : pop <= 50 ? 'text-yellow-300' : 'text-red-400')) : 'text-gray-500'}" title="${isPut ? '到期不被行权概率' : '被行权概率(卖飞风险)'}">${pop ? (isPut ? pop.toFixed(0) + '%' : (100 - pop).toFixed(0) + '%飞') : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-yellow-300/90">$${(contract.premium || contract.premium_usd || 0).toLocaleString(undefined, {maximumFractionDigits: 2})}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-gray-400" title="开仓保证金需求">$${marginReq.toLocaleString(undefined, {maximumFractionDigits: 0})}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums font-semibold ${capEff >= 15 ? 'text-emerald-400' : capEff >= 8 ? 'text-green-300' : 'text-gray-400'}" title="权利金/保证金">${capEff.toFixed(1)}%</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${supportDist !== null && supportDist !== undefined ? (supportDist >= 10 ? 'text-emerald-400' : supportDist >= 5 ? 'text-yellow-300' : 'text-red-400') : 'text-gray-600'}" title="PUT行权价到支撑位距离">${supportDist !== null && supportDist !== undefined ? supportDist.toFixed(1) + '%' : (isPut ? '-' : 'N/A')}</td>
            <td class="py-2 px-2 text-center"><span class="${liqColor} text-xs font-medium">${contract.liquidity_score}</span></td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-red-400/80">$${lossVal.toLocaleString(undefined, {maximumFractionDigits: 0})}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-blue-300/80">$${breakeven.toLocaleString(undefined, {maximumFractionDigits: 0})}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${bePct ? (bePct >= 10 ? 'text-emerald-400' : bePct >= 5 ? 'text-yellow-300' : 'text-orange-400') : 'text-gray-500'}">${bePct ? bePct.toFixed(1) + '%' : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-gray-400">${oi.toLocaleString()}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${spreadColor}">${spreadPct.toFixed(2)}%</td>
            <td class="py-2 px-2 text-center font-mono text-xs tabular-nums ${ivRank ? (ivRank >= 70 ? 'text-red-400' : ivRank <= 30 ? 'text-emerald-400' : 'text-gray-400') : 'text-gray-500'}">${ivRank ? String(ivRank).split('.')[0] : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${contract._score !== undefined ? (contract._score >= 0.7 ? "text-emerald-400 font-bold" : contract._score >= 0.5 ? "text-green-300" : contract._score >= 0.3 ? "text-yellow-300" : "text-gray-500") : "text-gray-500"}" title="加权评分: APR(25%)+POP(25%)+安全垫(20%)+流动性(15%)+IV中性(15%)">${contract._score !== undefined ? contract._score.toFixed(3) : "-"}</td>
            <td class="py-2 px-3 text-center">${riskBadge}</td>
        </tr>`;
    }).join('');

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
                <p class="sm:text-gray-300">建议平仓当前合约，卖出更低行权价的远期Put，获取更高权利金的同时下移防线。</p>
            </div>
        </div>
    `;

    modal.classList.add('active');
}

function closeRollModal() {
    document.getElementById('rollModal').classList.remove('active');
}

const flowSugg = {
    protective_hedge: '机构护冲 ↓ 短期谨慎',
    premium_collect: '收取权利金 ↑ 值好环境',
    speculative_put: '看跌投机 ↓ 风险升',
    call_momentum: '追涨建仓 ↑ 看好行情',
    call_speculative: '看涨投机 ↑ 小单低位入场',
    covered_call: '备兑开仓 ↑ 锁定收益',
    call_overwrite: '改仓操作 ↑ 调整价格',
};

function updateLargeTrades(trades, count) {
    const container = document.getElementById('largeTradesList');
    const titleCount = document.getElementById('largeTradesTitleCount');

    if (count > 0) { titleCount.textContent = count; titleCount.classList.remove('hidden'); }
    else titleCount.classList.add('hidden');

    if (!trades || trades.length === 0) {
        container.innerHTML = '<div class="text-gray-500 text-center py-4 text-sm">近1小时无大单成交</div>';
        ['ltMegaCount','ltHighCount','ltMediumCount','ltLowCount'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.textContent = '0';
        });
        const tnEl = document.getElementById('tradesTotalNotional');
        if (tnEl) tnEl.textContent = '--';
        return;
    }

    const flowNames = {
        sell_put_deep_itm: '保护性对冲', sell_put_atm_itm: '收权利金', sell_put_otm: '备兑开仓',
        buy_put_deep_itm: '保护性买入', buy_put_atm: '看跌投机', buy_put_otm: '看跌投机',
        sell_call_otm: '备兑开仓', sell_call_itm: '改仓操作',
        buy_call_atm_itm: '追涨建仓', buy_call_otm: '看涨投机',
        protective_hedge: '保护性对冲', premium_collect: '收权利金', speculative_put: '看跌投机',
        call_speculative: '看涨投机', call_momentum: '追涨建仓', covered_call: '备兑开仓',
        call_overwrite: '改仓操作', put_buy_hedge: '保护性买入',
        unclassified: '未分类', unknown: '未知流向'
    };

    const flowHints = {
        sell_put_deep_itm: '强烈看涨愿接货', sell_put_atm_itm: '温和看涨收权', sell_put_otm: '纯收权利金',
        buy_put_deep_itm: '机构对冲防跌', buy_put_atm: '短线看跌', buy_put_otm: '投机看跌',
        sell_call_otm: '备兑锁定收益', sell_call_itm: '调整仓位',
        buy_call_atm_itm: '顺势追涨', buy_call_otm: '低成本博反弹',
        protective_hedge: '机构护冲↓', premium_collect: '收权↑', speculative_put: '看跌↓',
        call_speculative: '看涨↑', call_momentum: '追涨↑', covered_call: '备兑↑',
        call_overwrite: '改仓↑', put_buy_hedge: '护冲↓'
    };

    let megaCount = 0, highCount = 0, mediumCount = 0, lowCount = 0, totalNotional = 0;
    trades.forEach(t => {
        const n = t.notional_usd || 0;
        totalNotional += n;
        const sev = t.severity || _classifySeverity(n);
        if (sev === 'mega') megaCount++;
        else if (sev === 'high') highCount++;
        else if (sev === 'medium') mediumCount++;
        else if (sev === 'low') lowCount++;
    });

    const megaEl = document.getElementById('ltMegaCount');
    const highEl = document.getElementById('ltHighCount');
    const medEl = document.getElementById('ltMediumCount');
    const lowEl = document.getElementById('ltLowCount');
    if (megaEl) megaEl.textContent = megaCount;
    if (highEl) highEl.textContent = highCount;
    if (medEl) medEl.textContent = mediumCount;
    if (lowEl) lowEl.textContent = lowCount;

    const tnEl = document.getElementById('tradesTotalNotional');
    if (tnEl) {
        tnEl.textContent = totalNotional >= 1000000
            ? '总名义 $' + (totalNotional / 1000000).toFixed(1) + 'M'
            : '总名义 $' + Math.round(totalNotional).toLocaleString();
    }

    const sevStyles = {
        mega:   { border: 'border-l-red-500',    bg: 'bg-red-500/15',    badge: 'bg-red-600 text-white',       label: '巨鲸', icon: '🐋' },
        high:   { border: 'border-l-orange-500',  bg: 'bg-orange-500/10', badge: 'bg-orange-500 text-white',    label: '大单', icon: '🔥' },
        medium: { border: 'border-l-yellow-500',  bg: 'bg-yellow-500/8',  badge: 'bg-yellow-500 text-gray-900', label: '中单', icon: '⚡' },
        low:    { border: 'border-l-blue-400',    bg: 'bg-blue-500/5',    badge: 'bg-blue-500 text-white',      label: '小单', icon: '📊' },
        info:   { border: 'border-l-gray-600',    bg: 'bg-gray-800/30',   badge: 'bg-gray-600 text-white',      label: '',     icon: '' }
    };

    container.innerHTML = trades.map(trade => {
        const inst = trade.instrument_name || trade.symbol || '';
        const dir = (trade.direction || '').toLowerCase();
        const flow = trade.flow_label || '';
        const volume = trade.volume || 0;
        const strike = trade.strike || 0;
        const optType = trade.option_type || '';
        const notional = trade.notional_usd || 0;
        const premium = trade.premium_usd || 0;
        const delta = trade.delta || 0;
        const iv = trade.iv || 0;
        const isBlock = trade.is_block || false;
        const tradePrice = trade.trade_price || 0;

        const sev = trade.severity || _classifySeverity(notional);
        const sevStyle = sevStyles[sev] || sevStyles.info;

        let dirIcon, dirColor, dirLabel;
        if (dir === 'buy') {
            dirIcon = '▲'; dirColor = 'text-red-400'; dirLabel = '买';
        } else if (dir === 'sell') {
            dirIcon = '▼'; dirColor = 'text-green-400'; dirLabel = '卖';
        } else {
            dirIcon = '—'; dirColor = 'text-gray-400'; dirLabel = '';
        }

        const optIsPut = optType && optType.toUpperCase().startsWith('P');
        const optTag = optType
            ? '<span class="px-1 py-0.5 rounded text-[10px] font-bold ' +
              (optIsPut ? 'bg-purple-500/30 text-purple-300' : 'bg-emerald-500/30 text-emerald-300') +
              '">' + (optIsPut ? 'P' : 'C') + '</span>'
            : '';

        const strikeStr = strike ? '$' + strike.toLocaleString() : '';
        const dteMatch = inst.match(/(\d{1,2}[A-Z]{3}\d{2})/);
        const dteStr = dteMatch ? dteMatch[1] : '';

        const notionalStr = notional >= 1000000
            ? '$' + (notional / 1000000).toFixed(2) + 'M'
            : notional >= 1000
            ? '$' + (notional / 1000).toFixed(0) + 'K'
            : '$' + Math.round(notional).toLocaleString();

        const premiumStr = premium >= 1000000
            ? '$' + (premium / 1000000).toFixed(2) + 'M'
            : premium >= 1000
            ? '$' + (premium / 1000).toFixed(0) + 'K'
            : premium > 0
            ? '$' + Math.round(premium).toLocaleString()
            : '';

        const flowCN = flowNames[flow] || flow || '';
        const flowHint = flowHints[flow] || '';

        const blockTag = isBlock
            ? '<span class="bg-amber-500/30 text-amber-300 text-[9px] px-1 py-0.5 rounded font-bold">大宗</span>'
            : '';

        const deltaStr = delta ? 'Δ' + Math.abs(delta).toFixed(2) : '';
        const ivStr = iv ? 'IV' + iv.toFixed(0) + '%' : '';

        const volStr = volume > 0 ? volume.toFixed(0) + '张' : '';

        return `<div class="${sevStyle.bg} border-l-3 ${sevStyle.border} rounded-lg px-3 py-2 text-xs hover:bg-white/5 transition cursor-default">
            <div class="flex items-center gap-1.5">
                <span class="${dirColor} font-bold text-sm">${dirIcon}</span>
                <span class="font-mono text-white font-medium truncate" style="max-width:130px" title="${safeHTML(inst)}">${inst || '--'}</span>
                ${optTag}${blockTag}
                <span class="text-gray-500">${strikeStr}</span>
                <span class="text-yellow-300 font-bold ml-auto">${notionalStr}</span>
                ${sevStyle.label ? '<span class="' + sevStyle.badge + ' text-[10px] px-1.5 py-0.5 rounded font-bold ml-1">' + sevStyle.icon + ' ' + sevStyle.label + '</span>' : ''}
            </div>
            <div class="flex items-center gap-1.5 mt-0.5 text-[11px]">
                <span class="${dirColor}">${dirLabel}</span>
                ${flowCN ? '<span class="text-cyan-300">' + flowCN + '</span>' : ''}
                ${flowHint ? '<span class="text-gray-500">· ' + flowHint + '</span>' : ''}
                ${premiumStr ? '<span class="text-gray-400 ml-1">权利金' + premiumStr + '</span>' : ''}
                ${volStr ? '<span class="text-gray-500">' + volStr + '</span>' : ''}
                <span class="text-gray-600 ml-auto flex items-center gap-1.5">
                    ${deltaStr ? '<span>' + deltaStr + '</span>' : ''}
                    ${ivStr ? '<span>' + ivStr + '</span>' : ''}
                </span>
            </div>
        </div>`;
    }).join('');
}

function _classifySeverity(notional) {
    if (notional >= 5000000) return 'mega';
    if (notional >= 2000000) return 'high';
    if (notional >= 500000) return 'medium';
    if (notional >= 100000) return 'low';
    return 'info';
}

function updateLastUpdateTime(timestamp) {
    let date;
    if (timestamp && timestamp.includes('T')) {
        date = new Date(timestamp);
    } else if (timestamp) {
        const parts=timestamp.split(/[- :]/);
        const [year, month, day, hour, minute, second] = parts.map(Number);
        date = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
    } else {
        date = new Date();
    }
    if (isNaN(date.getTime())) { document.getElementById('lastUpdate').textContent = '更新于 --:--:--'; return; }
    const timeStr = date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    document.getElementById('lastUpdate').textContent = `更新于 ${timeStr}`;
}

async function loadAprChartData() {
    try {
        const currency = document.getElementById('currencySelect').value;
        const hours = chartPeriods.apr;
        const response = await safeFetch(`${API_BASE}/api/charts/apr?currency=${currency}&hours=${hours}`);
        const data = await response.json();

        if (!data || data.length === 0) {
            aprChart.data.labels = [];
            aprChart.data.datasets[0].data = [];
            aprChart.data.datasets[1].data = [];
            aprChart.update();
            return;
        }

        // Filter outliers: APR should be between 1 and 500%
        const filtered = data.filter(d => {
            const apr = d.best_safe_apr || d.avg_apr || 0;
            return apr >= 1 && apr < 500;
        });

        aprChart.data.labels = filtered.map(d => {
            const date = new Date(d.time || d.timestamp);
            return hours <= 24 ? `${date.getHours()}:${String(date.getMinutes()).padStart(2,'0')}` : hours <= 168 ? `${date.getMonth()+1}/${date.getDate()} ${date.getHours()}:00` : `${date.getMonth()+1}/${date.getDate()}`;
        });
        aprChart.data.datasets[0].data = filtered.map(d => d.best_safe_apr || d.avg_apr || null);
        aprChart.data.datasets[1].data = filtered.map(d => d.p75_safe_apr || (d.avg_apr ? d.avg_apr * 0.85 : null));
        aprChart.update();
    } catch (error) {
        console.error('加载APR图表失败:', error);
    }
}

async function loadDvolChartData() {
    try {
        const currency = document.getElementById('currencySelect').value;
        const hours = chartPeriods.dvol;
        const response = await safeFetch(`${API_BASE}/api/charts/dvol?currency=${currency}&hours=${hours}`);
        const data = await response.json();

        if (!data || data.length === 0) {
            dvolChart.data.labels = [];
            dvolChart.data.datasets[0].data = [];
            dvolChart.update();
            return;
        }

        // Filter out zero/invalid dvol values
        const filtered = data.filter(d => d.dvol && d.dvol > 0);

        dvolChart.data.labels = filtered.map(d => {
            const date = new Date(d.time || d.timestamp);
            return hours <= 24 ? `${date.getHours()}:${String(date.getMinutes()).padStart(2,'0')}` : hours <= 168 ? `${date.getMonth()+1}/${date.getDate()} ${date.getHours()}:00` : `${date.getMonth()+1}/${date.getDate()}`;
        });
        dvolChart.data.datasets[0].data = filtered.map(d => d.dvol);
        dvolChart.update();
    } catch (error) {
        console.error('加载DVOL图表失败:', error);
    }
}

async function loadStats() {
    try {
        const response = await safeFetch(`${API_BASE}/api/stats`);
        const data = await response.json();
        document.getElementById('totalScans').textContent = data.total_scans;
        document.getElementById('todayScans').textContent = data.today_scans;
        document.getElementById('dbSize').textContent = data.db_size_mb + ' MB';
    } catch (error) {
        console.error('加载统计失败:', error);
    }
}

let alertQueue = [];
function showAlert(message, type = 'info') {
    const colors = { success: 'border-green-500 bg-green-500/10 text-green-400', error: 'border-red-500 bg-red-500/10 text-red-400', warning: 'border-yellow-500 bg-yellow-500/10 text-yellow-400', info: 'border-blue-500 bg-blue-500/10 text-blue-400' };
    const icons = { success: 'fa-check-circle', error: 'fa-exclamation-circle', warning: 'fa-exclamation-triangle', info: 'fa-info-circle' };
    const time = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });

    const alertsList = document.getElementById('alertsList');
    if (alertsList) {
        if (alertsList.children.length === 1 && alertsList.children[0].textContent === '暂无预警') alertsList.innerHTML = '';
        const alert = document.createElement('div');
        alert.className = `border-l-4 p-3 rounded-lg text-sm ${colors[type]} flex items-start gap-2 animate-fade-in`;
        alert.innerHTML = `<i class="fas ${icons[type]} mt-0.5 flex-shrink-0"></i><div class="flex-1 min-w-0"><div class="text-xs text-gray-500 mb-0.5">${safeHTML(time)}</div></div>`;
        const msgDiv = alert.querySelector('.flex-1');
        const msgContent = document.createElement('div');
        msgContent.textContent = message;
        msgDiv.appendChild(msgContent);
        alertsList.insertBefore(alert, alertsList.firstChild);
        while (alertsList.children.length > 20) alertsList.removeChild(alertsList.lastChild);
        return;
    }

    let container = document.getElementById('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        container.style.cssText = 'position:fixed;top:20px;right:20px;z-index:99999;display:flex;flex-direction:column;gap:8px;max-width:380px;';
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = `border-l-4 p-3 rounded-lg text-sm ${colors[type]} flex items-start gap-2 animate-fade-in`;
    toast.style.cssText = 'backdrop-filter:blur(10px);box-shadow:0 4px 12px rgba(0,0,0,0.3);';
    toast.innerHTML = `<i class="fas ${icons[type]} mt-0.5 flex-shrink-0"></i><div class="flex-1 min-w-0"><div class="text-xs text-gray-500 mb-0.5">${safeHTML(time)}</div></div>`;
    const toastMsg = toast.querySelector('.flex-1');
    const toastContent = document.createElement('div');
    toastContent.textContent = message;
    toastMsg.appendChild(toastContent);
    container.appendChild(toast);
    setTimeout(() => { toast.style.transition = 'opacity 0.3s'; toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 4000);
    while (container.children.length > 5) container.removeChild(container.firstChild);
}

function addDemoAlerts() {
    showAlert('系统就绪，点击"立即扫描"开始监控', 'info');
}

setTimeout(addDemoAlerts, 1000);

// 点击模态框外部关闭
document.getElementById('rollModal').addEventListener('click', (e) => {
    if (e.target.id === 'rollModal') closeRollModal();
});


// v8.0: 网格策略数据加载 - 委托给 grid-strategy.js
async function loadGridStrategyData() {
    if (typeof loadGridStrategy === 'function') {
        await loadGridStrategy();
    }
}

// v8.0: Payoff可视化
let payoffChart = null;

function setPayoffMode(mode) {
    const singleMode = document.getElementById('payoffSingleMode');
    const wheelMode = document.getElementById('payoffWheelMode');
    const singleBtn = document.getElementById('payoffModeSingle');
    const wheelBtn = document.getElementById('payoffModeWheel');
    const compareBtn = document.getElementById('payoffModeCompare');
    
    if (mode === 'single') {
        singleMode.classList.remove('hidden');
        wheelMode.classList.add('hidden');
        singleBtn.className = 'px-3 py-1 rounded text-sm font-medium bg-cyan-500/20 border border-cyan-500/50 text-cyan-400';
        wheelBtn.className = 'px-3 py-1 rounded text-sm font-medium bg-gray-700/50 border border-gray-600 text-gray-400 hover:bg-gray-600/50';
        compareBtn.className = 'px-3 py-1 rounded text-sm font-medium bg-gray-700/50 border border-gray-600 text-gray-400 hover:bg-gray-600/50';
    } else if (mode === 'wheel') {
        singleMode.classList.add('hidden');
        wheelMode.classList.remove('hidden');
        wheelBtn.className = 'px-3 py-1 rounded text-sm font-medium bg-cyan-500/20 border border-cyan-500/50 text-cyan-400';
        singleBtn.className = 'px-3 py-1 rounded text-sm font-medium bg-gray-700/50 border border-gray-600 text-gray-400 hover:bg-gray-600/50';
        compareBtn.className = 'px-3 py-1 rounded text-sm font-medium bg-gray-700/50 border border-gray-600 text-gray-400 hover:bg-gray-600/50';
    } else if (mode === 'compare') {
        singleMode.classList.add('hidden');
        wheelMode.classList.add('hidden');
        showAlert('对比模式开发中，敬请期待', 'info');
        compareBtn.className = 'px-3 py-1 rounded text-sm font-medium bg-cyan-500/20 border border-cyan-500/50 text-cyan-400';
        singleBtn.className = 'px-3 py-1 rounded text-sm font-medium bg-gray-700/50 border border-gray-600 text-gray-400 hover:bg-gray-600/50';
        wheelBtn.className = 'px-3 py-1 rounded text-sm font-medium bg-gray-700/50 border border-gray-600 text-gray-400 hover:bg-gray-600/50';
    }
}

async function calcPayoff() {
    try {
        const direction = document.getElementById('payoffDirection').value;
        const optionType = document.getElementById('payoffOptionType').value;
        const quantity = parseFloat(document.getElementById('payoffQuantity').value) || 1;
        const strike = parseFloat(document.getElementById('payoffStrike').value);
        const premium = parseFloat(document.getElementById('payoffPremium').value) || 0;
        const dte = parseFloat(document.getElementById('payoffDTE').value) || 30;
        const iv = parseFloat(document.getElementById('payoffIV').value) || 50;
        const spot = parseFloat(document.getElementById('payoffSpot').value) || currentSpotPrice || 73000;
        
        const response = await safeFetch(`${API_BASE}/api/payoff/calc`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                legs: [{ direction, option_type: optionType, strike, premium, quantity }],
                spot,
                pct_range: 0.3,
                steps: 100
            })
        });
        
        const data = await response.json();
        renderPayoffChart(data);
        
        // 计算策略评分和实操建议（同时更新胜率）
        const scoreData = await calcStrategyScore({ direction, option_type: optionType, strike, premium, quantity }, spot, dte, iv);
        updatePayoffResult(data, scoreData);
    } catch (error) {
        console.error('Payoff 计算失败:', error);
        showAlert('Payoff 计算失败', 'error');
    }
}

async function calcStrategyScore(leg, spot, dte, iv) {
    try {
        const response = await safeFetch(`${API_BASE}/api/payoff/score`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                legs: [leg],
                spot, dte, iv
            })
        });
        
        const data = await response.json();
        renderStrategyAdvice(data);
        return data.score;
    } catch (error) {
        console.error('策略评分计算失败:', error);
        return null;
    }
}

function renderStrategyAdvice(data) {
    const scoreData = data.score;
    const adviceData = data.advice;
    
    if (!scoreData || !adviceData) return;
    
    const levelColors = {
        'green': { text: 'text-green-400', bg: 'bg-green-500/10 border-green-500/30' },
        'emerald': { text: 'text-emerald-400', bg: 'bg-emerald-500/10 border-emerald-500/30' },
        'yellow': { text: 'text-yellow-400', bg: 'bg-yellow-500/10 border-yellow-500/30' },
        'orange': { text: 'text-orange-400', bg: 'bg-orange-500/10 border-orange-500/30' },
        'red': { text: 'text-red-400', bg: 'bg-red-500/10 border-red-500/30' }
    };
    const color = levelColors[adviceData.rating_level] || levelColors['yellow'];
    
    const adviceCard = document.getElementById('strategyAdviceCard');
    adviceCard.classList.remove('hidden');
    adviceCard.className = `mb-4 p-3 rounded-lg border ${color.bg}`;
    
    document.getElementById('strategyRating').textContent = adviceData.rating;
    document.getElementById('strategyRating').className = `text-lg font-bold ${color.text}`;
    document.getElementById('strategyScenario').textContent = adviceData.scenario;
    
    document.getElementById('strategyScore').textContent = `${scoreData.total_score}/100`;
    document.getElementById('scoreRoi').textContent = `${scoreData.components.roi_score}分`;
    document.getElementById('scoreRisk').textContent = `${scoreData.components.risk_score}分`;
    document.getElementById('scoreWinRate').textContent = `${scoreData.components.win_rate_score}分`;
    document.getElementById('scoreLiquidity').textContent = `${scoreData.components.liquidity_score}分`;
    
    document.getElementById('strategyAdviceText').textContent = adviceData.advice_text;
    
    const risksList = document.getElementById('strategyRisks');
    risksList.innerHTML = '';
    adviceData.risks.forEach(risk => {
        const li = document.createElement('li');
        li.textContent = risk;
        risksList.appendChild(li);
    });
    
    const optList = document.getElementById('strategyOptimizations');
    optList.innerHTML = '';
    adviceData.optimizations.forEach(opt => {
        const li = document.createElement('li');
        li.textContent = opt;
        optList.appendChild(li);
    });
}

async function estimatePremium() {
    try {
        const optionType = document.getElementById('payoffOptionType').value;
        const strike = parseFloat(document.getElementById('payoffStrike').value);
        const dte = parseFloat(document.getElementById('payoffDTE').value) || 30;
        const iv = parseFloat(document.getElementById('payoffIV').value) || 50;
        const spot = parseFloat(document.getElementById('payoffSpot').value) || currentSpotPrice || 73000;
        
        const response = await safeFetch(`${API_BASE}/api/payoff/estimate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ option_type: optionType, strike, spot, dte, iv })
        });
        
        const data = await response.json();
        if (data.estimated_premium) {
            document.getElementById('payoffPremium').value = data.estimated_premium;
            showAlert(`估算完成：权利金 ≈ $${data.estimated_premium.toLocaleString()} (Delta: ${data.delta})`, 'success');
        } else if (data.error) {
            showAlert(data.error, 'error');
        }
    } catch (error) {
        console.error('权利金估算失败:', error);
        showAlert('权利金估算失败', 'error');
    }
}

function toggleAdvancedParams() {
    const panel = document.getElementById('advancedParams');
    panel.classList.toggle('hidden');
}

async function calcWheelROI() {
    try {
        const putStrike = parseFloat(document.getElementById('wheelPutStrike').value);
        const putPremium = parseFloat(document.getElementById('wheelPutPremium').value);
        const putDTE = parseFloat(document.getElementById('wheelPutDTE').value) || 30;
        const callStrike = parseFloat(document.getElementById('wheelCallStrike').value);
        const callPremium = parseFloat(document.getElementById('wheelCallPremium').value);
        const callDTE = parseFloat(document.getElementById('wheelCallDTE').value) || 30;
        const spot = parseFloat(document.getElementById('payoffSpot').value) || currentSpotPrice || 73000;
        
        const response = await safeFetch(`${API_BASE}/api/payoff/wheel`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                put_strike: putStrike,
                put_premium: putPremium,
                put_dte: putDTE,
                call_strike: callStrike,
                call_premium: callPremium,
                call_dte: callDTE,
                spot,
                quantity: 1
            })
        });
        
        const data = await response.json();
        renderWheelChart(data);
        updateWheelResult(data);
    } catch (error) {
        console.error('Wheel ROI 计算失败:', error);
        showAlert('Wheel ROI 计算失败', 'error');
    }
}

function renderPayoffChart(data) {
    const ctx = document.getElementById('payoffChart').getContext('2d');
    if (payoffChart) payoffChart.destroy();
    
    const zeroLine = new Array(data.prices.length).fill(0);
    
    payoffChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.prices.map(p => p.toLocaleString()),
            datasets: [
                {
                    label: '总盈亏',
                    data: data.total_pnl,
                    borderColor: 'rgba(6, 182, 212, 1)',
                    backgroundColor: 'rgba(6, 182, 212, 0.1)',
                    fill: true,
                    borderWidth: 2,
                    pointRadius: 0
                },
                ...data.legs.map((leg, i) => ({
                    label: `${leg.direction === 'sell' ? 'Sell' : 'Buy'} ${leg.option_type === 'P' ? 'Put' : 'Call'} $${leg.strike.toLocaleString()}`,
                    data: leg.pnl,
                    borderColor: leg.option_type === 'P' ? 'rgba(34, 197, 94, 0.6)' : 'rgba(59, 130, 246, 0.6)',
                    borderWidth: 1,
                    borderDash: [5, 5],
                    pointRadius: 0,
                    fill: false
                })),
                {
                    label: '盈亏平衡',
                    data: zeroLine,
                    borderColor: 'rgba(234, 179, 8, 0.5)',
                    borderWidth: 1,
                    borderDash: [3, 3],
                    pointRadius: 0,
                    fill: false
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                title: { display: true, text: '策略 Payoff 图' },
                tooltip: {
                    callbacks: {
                        label: ctx => `${ctx.dataset.label}: $${ctx.parsed.y.toLocaleString()}`
                    }
                }
            },
            scales: {
                y: {
                    title: { display: true, text: '盈亏 ($)' },
                    ticks: { callback: v => '$' + v.toLocaleString() }
                },
                x: {
                    title: { display: true, text: 'BTC 价格 ($)' },
                    ticks: { maxTicksLimit: 10, callback: v => '$' + v.toLocaleString() }
                }
            }
        }
    });
}

function renderWheelChart(data) {
    const ctx = document.getElementById('payoffChart').getContext('2d');
    if (payoffChart) payoffChart.destroy();
    
    const zeroLine = new Array(data.prices.length).fill(0);
    
    payoffChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.prices.map(p => p.toLocaleString()),
            datasets: [
                {
                    label: 'Wheel 总盈亏',
                    data: data.wheel_pnl,
                    borderColor: 'rgba(6, 182, 212, 1)',
                    backgroundColor: 'rgba(6, 182, 212, 0.1)',
                    fill: true,
                    borderWidth: 2,
                    pointRadius: 0
                },
                {
                    label: 'Sell Put 盈亏',
                    data: data.put_pnl,
                    borderColor: 'rgba(34, 197, 94, 0.6)',
                    borderWidth: 1,
                    borderDash: [5, 5],
                    pointRadius: 0,
                    fill: false
                },
                {
                    label: 'Sell Call 盈亏',
                    data: data.call_pnl,
                    borderColor: 'rgba(59, 130, 246, 0.6)',
                    borderWidth: 1,
                    borderDash: [5, 5],
                    pointRadius: 0,
                    fill: false
                },
                {
                    label: '持股盈亏',
                    data: data.stock_pnl,
                    borderColor: 'rgba(168, 85, 247, 0.6)',
                    borderWidth: 1,
                    borderDash: [3, 3],
                    pointRadius: 0,
                    fill: false
                },
                {
                    label: '盈亏平衡',
                    data: zeroLine,
                    borderColor: 'rgba(234, 179, 8, 0.5)',
                    borderWidth: 1,
                    borderDash: [3, 3],
                    pointRadius: 0,
                    fill: false
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                title: { display: true, text: 'Wheel 策略 Payoff 图' },
                tooltip: {
                    callbacks: {
                        label: ctx => `${ctx.dataset.label}: $${ctx.parsed.y.toLocaleString()}`
                    }
                }
            },
            scales: {
                y: {
                    title: { display: true, text: '盈亏 ($)' },
                    ticks: { callback: v => '$' + v.toLocaleString() }
                },
                x: {
                    title: { display: true, text: 'BTC 价格 ($)' },
                    ticks: { maxTicksLimit: 10, callback: v => '$' + v.toLocaleString() }
                }
            }
        }
    });
}

function updatePayoffResult(data, scoreData = null) {
    document.getElementById('payoffMaxProfit').textContent = `$${data.max_profit.toLocaleString()}`;
    document.getElementById('payoffMaxLoss').textContent = `$${Math.abs(data.max_loss).toLocaleString()}`;
    document.getElementById('payoffBreakeven').textContent = data.breakevens.length > 0 ? `$${data.breakevens.map(b => b.toLocaleString()).join(', ')}` : '无';
    
    const capitalAtRisk = Math.abs(data.max_loss) || data.max_profit;
    const roi = capitalAtRisk > 0 ? ((data.max_profit / capitalAtRisk) * 100).toFixed(1) : 0;
    const riskReward = data.max_loss !== 0 ? (data.max_profit / Math.abs(data.max_loss)).toFixed(2) : 0;
    
    document.getElementById('payoffROI').textContent = `${roi}%`;
    
    if (scoreData && scoreData.metrics && scoreData.metrics.win_rate_pct) {
        document.getElementById('payoffWinRate').textContent = `${scoreData.metrics.win_rate_pct.toFixed(0)}%`;
    } else {
        document.getElementById('payoffWinRate').textContent = '--';
    }
    
    document.getElementById('payoffRiskReward').textContent = `1:${riskReward}`;
    
    document.getElementById('wheelResult').classList.add('hidden');
}

function updateWheelResult(data) {
    const s = data.summary;
    
    document.getElementById('payoffMaxProfit').textContent = `$${s.total_income.toLocaleString()}`;
    document.getElementById('payoffMaxLoss').textContent = `$${s.capital_at_risk.toLocaleString()}`;
    document.getElementById('payoffBreakeven').textContent = `$${s.breakeven_stock.toLocaleString()}`;
    
    const roi = s.wheel_roi_pct ? `${s.wheel_roi_pct.toFixed(1)}%` : '--';
    const riskReward = s.capital_at_risk > 0 ? (s.total_income / s.capital_at_risk).toFixed(2) : 0;
    
    document.getElementById('payoffROI').textContent = roi;
    document.getElementById('payoffWinRate').textContent = s.win_rate_pct ? `${s.win_rate_pct.toFixed(0)}%` : '--';
    document.getElementById('payoffRiskReward').textContent = `1:${riskReward}`;
    
    document.getElementById('wheelResult').classList.remove('hidden');
    document.getElementById('wheelPutIncome').textContent = `$${s.put_income.toLocaleString()}`;
    document.getElementById('wheelCallIncome').textContent = `$${s.call_income.toLocaleString()}`;
    document.getElementById('wheelROI').textContent = `${s.wheel_roi_pct.toFixed(1)}%`;
    document.getElementById('wheelAnnualizedROI').textContent = `${s.annualized_roi_pct.toFixed(1)}%`;
}

// 视图切换功能（仅过滤行数据，不隐藏列）
const VIEW_PRESETS = {
    sellput: {
        filter: (c) => c.option_type === 'P' || c.option_type === 'PUT'
    },
    coveredcall: {
        filter: (c) => c.option_type === 'C' || c.option_type === 'CALL'
    },
    wheel: {
        filter: null
    },
    all: {
        filter: null
    }
};
let _currentView = 'sellput';

function switchView(viewName) {
    _currentView = viewName;
    const preset = VIEW_PRESETS[viewName];
    if (!preset) return;

    document.querySelectorAll('[id^="view"]').forEach(btn => {
        if (btn.id.startsWith('view')) {
            btn.className = 'px-2.5 py-1 rounded text-xs font-medium bg-gray-700/50 text-gray-400 border border-gray-600/30 transition';
        }
    });
    const activeBtn = document.getElementById('view' + viewName.charAt(0).toUpperCase() + viewName.slice(1));
    if (activeBtn) {
        const colors = { sellput: 'bg-green-500/20 text-green-400 border-green-500/30', coveredcall: 'bg-blue-500/20 text-blue-400 border-blue-500/30', wheel: 'bg-purple-500/20 text-purple-400 border-purple-500/30', all: 'bg-orange-500/20 text-orange-400 border-orange-500/30' };
        activeBtn.className = `px-2.5 py-1 rounded text-xs font-medium ${colors[viewName] || ''} border transition`;
    }

    if (currentData && currentData.contracts && preset.filter) {
        const filtered = currentData.contracts.filter(preset.filter);
        updateOpportunitiesTable(filtered);
    } else if (currentData && currentData.contracts) {
        updateOpportunitiesTable(currentData.contracts);
    }
}

// 排序功能
let currentSort = { field: null, direction: 'desc' };

function sortContracts(field) {
    if (!currentData || !currentData.contracts || currentData.contracts.length === 0) return;

    // 保存当前展开行的 symbol
    const expandedSymbols = new Set();
    document.querySelectorAll('tr[data-expanded="true"]').forEach(r => expandedSymbols.add(r.dataset.symbol));

    // 字段名映射：HTML使用的名称 -> API返回的名称
    const fieldMap = {
        'mark_iv': 'iv',
        'premium': 'premium_usd',
        'spread_pct': 'spread_pct',
        'distance_spot_pct': 'distance_spot_pct'
    };
    const actualField = fieldMap[field] || field;

    // 切换排序方向
    if (currentSort.field === actualField) {
        currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
    } else {
        currentSort.field = actualField;
        currentSort.direction = 'desc';
    }

    // 更新表头图标
    updateSortIcons(actualField, currentSort.direction);

    // 排序数据
    const sortedContracts = [...currentData.contracts].sort((a, b) => {
        let valA = a[actualField];
        let valB = b[actualField];

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
        if (field === 'option_type') {
            const order = { 'PUT': 0, 'CALL': 1 };
            valA = order[valA] ?? 9;
            valB = order[valB] ?? 9;
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

    // 恢复之前展开的行
    setTimeout(() => {
        expandedSymbols.forEach(symbol => {
            const row = document.querySelector(`tr[data-symbol="${symbol}"]`);
            if (row && !row.dataset.expanded) {
                row.dataset.expanded = 'true';
                const btn = row.querySelector('.expand-btn');
                if (btn) btn.textContent = '▼';
                const detail = row.nextElementSibling;
                if (detail && detail.classList.contains('contract-detail')) {
                    detail.classList.remove('hidden');
                }
            }
        });
    }, 50);
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
        'option_type': '类型',
        'expiry': '到期日',
        'dte': '到期天数',
        'strike': '行权价',
        'delta': 'Delta',
        'gamma': 'Gamma',
        'vega': 'Vega',
        'mark_iv': '隐含波动率',
        'apr': '年化收益',
        'premium': '权利金',
        'liquidity_score': '流动性',
        'loss_at_10pct': '-10%亏损',
        'breakeven': '盈亏平衡',
        'open_interest': '持仓量',
        'spread_pct': '买卖价差',
        'risk': '风险等级'
    };
    return names[field] || field;
}

// 大单风向标功能

function applyPreset(presetName) {
    _currentPreset = presetName;
    const optType = document.getElementById('optionType').value;
    const preset = STRATEGY_PRESETS[optType]?.[presetName];
    if (!preset) return;

    document.getElementById('minDte').value = preset.min_dte;
    document.getElementById('maxDte').value = preset.max_dte;
    document.getElementById('maxDelta').value = preset.max_delta;

    // 更新按钮激活状态
    ['Con', 'Std', 'Agg'].forEach(id => {
        const btn = document.getElementById('preset' + id);
        btn.classList.remove('ring-1', 'bg-blue-500/15', 'text-blue-300', 'ring-blue-500/30',
            'bg-green-500/15', 'text-green-300', 'ring-green-500/30',
            'bg-orange-500/15', 'text-orange-300', 'ring-orange-500/30');
    });

    const activeBtn = document.getElementById('preset' +
        (presetName === 'conservative' ? 'Con' : presetName === 'standard' ? 'Std' : 'Agg'));
    const colorMap = {conservative: 'green', standard: 'blue', aggressive: 'orange'};
    const c = colorMap[presetName];
    activeBtn.classList.add(`bg-${c}-500/15`, `text-${c}-300`, `ring-1`, `ring-${c}-500/30`);

    updateParamDisplay();
}

// 策略类型切换时自动应用当前预设的对应版本
document.getElementById('optionType')?.addEventListener('change', function() {
    applyPreset(_currentPreset);
});



['minDte', 'maxDte', 'maxDelta', 'currencySelect', 'optionType'].forEach(id => {
    document.getElementById(id)?.addEventListener('input', updateParamDisplay);
});

// DVOL自适应建议展示

function toggleDetail(idx) {
    const detailId = 'detail_' + idx;
    const detail = document.getElementById(detailId);
    const icon = document.getElementById('icon_' + idx);

    if (!detail) return;

    if (_expandedRow && _expandedRow !== detailId) {
        const prevDetail = document.getElementById(_expandedRow);
        if (prevDetail) prevDetail.classList.add('hidden');
        const prevIdx = _expandedRow.replace('detail_', '');
        const prevIcon = document.getElementById('icon_' + prevIdx);
        if (prevIcon) { prevIcon.style.transform = ''; prevIcon.parentElement?.classList.remove('bg-gray-600'); }
    }

    const isHidden = detail.classList.toggle('hidden');
    if (icon) {
        icon.style.transform = isHidden ? '' : 'rotate(180deg)';
        if (!isHidden) icon.parentElement?.classList.add('bg-gray-600');
        else icon.parentElement?.classList.remove('bg-gray-600');
    }
    _expandedRow = isHidden ? null : detailId;
}

async function showDvolAdvice(currency) {
    try {
        const r = await safeFetch(`${API_BASE}/api/dvol-advice?currency=${currency}`);
        const data = await r.json();
        if (data.error) return;

        const bar = document.getElementById('dvolAdviceBar');
        const text = document.getElementById('dvolAdviceText');
        const badge = document.getElementById('dvolAdjustBadge');

        if (!bar || !data.dvol_snapshot?.trend) return;

        const snap = data.dvol_snapshot;
        const putAdvice = data.adapted_presets?.PUT_standard?.advice || [];
        const level = data.adapted_presets?.PUT_standard?.adjustment_level || 'none';

        if (putAdvice.length > 0 || level !== 'none') {
            bar.classList.remove('hidden');
            text.textContent = putAdvice.join(' | ') || `${snap.trend || ''} DVOL ${snap.signal || ''}`;

            if (level === 'conservative') {
                badge.textContent = '已收紧参数';
                badge.className = 'ml-auto text-[10px] px-1.5 py-0.5 rounded bg-red-500/20 text-red-300';
            } else if (level === 'aggressive') {
                badge.textContent = '已放宽参数';
                badge.className = 'ml-auto text-[10px] px-1.5 py-0.5 rounded bg-green-500/20 text-green-300';
            } else {
                badge.textContent = '';
            }
        } else {
            bar.classList.add('hidden');
        }
    } catch(e) {}
}

async function loadWindAnalysis() {
    try {
        const currency = document.getElementById('tradesCurrency')?.value || 'BTC';
        const days = document.getElementById('tradesDays')?.value || 7;

        const response = await safeFetch(`${API_BASE}/api/trades/wind-analysis?currency=${currency}&days=${days}`);
        const data = await response.json();
        const summary = data.summary || {};

        const countEl = document.getElementById('tradesStatsCount');
        if (countEl) {
            countEl.textContent = `${summary.total_trades || 0} 笔`;
            countEl.classList.remove('hidden');
        }

        const summaryCard = document.getElementById('windSummaryCard');
        if (summary.total_trades > 0) {
            summaryCard?.classList.remove('hidden');
            const score = data.sentiment_score || 0;
            let icon, scoreLabel, scoreClass;
            if (score >= 2) { icon = '🐂'; scoreLabel = '偏多'; scoreClass = 'bg-green-500/20 text-green-300'; }
            else if (score >= 1) { icon = '📈'; scoreLabel = '温和看多'; scoreClass = 'bg-green-900/30 text-green-400'; }
            else if (score > -1) { icon = '➡️'; scoreLabel = '中性'; scoreClass = 'bg-gray-700 text-gray-300'; }
            else if (score > -2) { icon = '📉'; scoreLabel = '温和看空'; scoreClass = 'bg-red-900/30 text-red-400'; }
            else { icon = '🐻'; scoreLabel = '偏空'; scoreClass = 'bg-red-500/20 text-red-300'; }

            const iconEl = document.getElementById('windSentimentIcon');
            if (iconEl) iconEl.textContent = icon;

            const scEl = document.getElementById('windSentimentScore');
            if (scEl) { scEl.textContent = scoreLabel; scEl.className = `text-xs font-mono px-2 py-0.5 rounded ${scoreClass}`; }

            const sentimentTextEl = document.getElementById('windSentimentText');
            if (sentimentTextEl) sentimentTextEl.textContent = data.sentiment_text || data.dominant_flow || '';

            const buySellRatioEl = document.getElementById('windBuySellRatio');
            if (buySellRatioEl) buySellRatioEl.textContent = `${(data.buy_ratio * 100 || 0).toFixed(0)}% / ${((1 - data.buy_ratio) * 100 || 0).toFixed(0)}%`;

            const totalNotionalEl = document.getElementById('windTotalNotional');
            if (totalNotionalEl) {
                const dist = data.distribution || [];
                const totalNotional = dist.reduce((sum, d) => sum + (d.total || 0), 0);
                totalNotionalEl.textContent = totalNotional > 0 ? `$${(totalNotional / 1000000).toFixed(1)}M` : '-';
            }

            const dominantFlowEl = document.getElementById('windDominantFlow');
            if (dominantFlowEl) dominantFlowEl.textContent = data.dominant_flow || '-';

            // Update flow breakdown display
            const flowBreakdownEl = document.getElementById('flowBreakdown');
            if (flowBreakdownEl && data.flow_breakdown) {
                flowBreakdownEl.innerHTML = data.flow_breakdown.map(f => {
                    const pct = f.count > 0 ? Math.round(f.count / (summary.total_trades || 1) * 100) : 0;
                    const colorClass = f.type.includes('protective') || f.type.includes('put_buy') ? 'text-green-400' :
                                      f.type.includes('speculative') || f.type.includes('call_momentum') ? 'text-blue-400' :
                                      f.type.includes('covered') || f.type.includes('overwrite') ? 'text-yellow-400' :
                                      f.type.includes('premium') ? 'text-purple-400' : 'text-gray-400';
                    return `<div class="flex justify-between items-center text-xs">
                        <span class="${colorClass}">${f.label}</span>
                        <span class="text-gray-300 font-mono">${f.count} <span class="text-gray-500">(${pct}%)</span></span>
                    </div>`;
                }).join('');
            }
        } else {
            summaryCard?.classList.add('hidden');
        }

        const spotEl = document.getElementById('windSpotMarker');
        if (spotEl && data.spot > 0) {
            spotEl.textContent = `● 现价 $${data.spot.toLocaleString()}`;
            spotEl.classList.remove('hidden');
        } else if (spotEl) {
            spotEl.classList.add('hidden');
        }

        const chartEl = document.getElementById('strikeFlowsChart');
        const dist = data.distribution || [];
        if (chartEl) {
            chartEl.innerHTML = '';
            const canvas = document.createElement('canvas');
            canvas.id = 'strikeChartCanvas';
            chartEl.appendChild(canvas);
            const filteredDist = dist.filter(d => d.strike > 0 && (d.put > 0 || d.call > 0)).slice(0, 20);
            if (filteredDist.length === 0) {
                chartEl.innerHTML = '<div class="text-gray-500 text-center py-4">暂无OI数据</div>';
            } else {
                window._strikeChart = new Chart(canvas, {
                    type: 'bar',
                    data: {
                        labels: filteredDist.map(d => d.strike?.toString() || ''),
                        datasets: [
                            { label: 'Put OI', data: filteredDist.map(d => d.put || 0), backgroundColor: 'rgba(239, 68, 68, 0.6)' },
                            { label: 'Call OI', data: filteredDist.map(d => d.call || 0), backgroundColor: 'rgba(34, 197, 94, 0.6)' }
                        ]
                    },
                    options: {
                        responsive: true,
                        scales: { y: { beginAtZero: true } },
                        plugins: { legend: { display: true, position: 'top' } }
                    }
                });
            }
        }
    } catch (error) {
        console.error('加载风向分析失败:', error);
    }
}
// Module 1: IV Term Structure
// ============================================================
let tsChart = null;
async function loadTermStructure() {
    const statusEl = document.getElementById('ts7');
    if (!statusEl) { console.warn('TS: container not found'); return; }
    try {
        const currency = document.getElementById('currencySelect')?.value || 'BTC';
        const resp = await safeFetch(API_BASE + '/api/charts/vol-surface?currency=' + currency);
        const d = await resp.json();
        if (d.error) { console.warn('TS:', d.error); return; }

        const tsData = d.term_structure || [];

        const targetDtes = [
            {key: '7', target: 7},
            {key: '14', target: 14},
            {key: '30', target: 30},
            {key: '60', target: 60},
            {key: '90', target: 90},
            {key: '180', target: 180}
        ];

        targetDtes.forEach(({key, target}) => {
            const el = document.getElementById('ts' + key);
            const dteEl = document.getElementById('ts' + key + 'dte');
            if (!el) return;

            let best = null;
            let bestDiff = Infinity;
            for (const t of tsData) {
                const diff = Math.abs(t.dte - target);
                if (diff < bestDiff && t.avg_iv !== null && t.avg_iv > 0) {
                    bestDiff = diff;
                    best = t;
                }
            }

            const maxAllowedDiff = target * 0.5 + 5;
            if (best && bestDiff <= maxAllowedDiff) {
                const iv = best.avg_iv;
                el.textContent = iv.toFixed(1) + '%';
                if (iv > 70) el.className = 'font-mono text-sm font-bold text-red-400';
                else if (iv > 55) el.className = 'font-mono text-sm font-bold text-yellow-400';
                else el.className = 'font-mono text-sm font-bold text-cyan-400';
                if (dteEl) dteEl.textContent = best.dte !== target ? `DTE ${best.dte}` : '';
            } else {
                el.textContent = '--';
                el.className = 'font-mono text-sm font-bold text-gray-600';
                if (dteEl) dteEl.textContent = '';
            }
        });

        const structLabel = document.getElementById('tsStructureLabel');
        const slopeLabel = document.getElementById('tsSlopeLabel');
        if (structLabel && slopeLabel && tsData.length >= 2) {
            const frontIv = tsData[0].avg_iv;
            const backIv = tsData[tsData.length - 1].avg_iv;
            if (frontIv && backIv) {
                if (frontIv > backIv) {
                    structLabel.textContent = 'Backwardation';
                    structLabel.className = 'text-xs px-2 py-0.5 rounded-full bg-red-500/20 text-red-400 font-medium';
                } else {
                    structLabel.textContent = 'Contango';
                    structLabel.className = 'text-xs px-2 py-0.5 rounded-full bg-green-500/20 text-green-400 font-medium';
                }
                const slope = ((backIv - frontIv) / frontIv * 100).toFixed(1);
                slopeLabel.textContent = (slope > 0 ? '+' : '') + slope + '%';
                slopeLabel.className = 'text-xs px-2 py-0.5 rounded-full ' + (slope >= 0 ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400');
            }
        }

        const bwEl = document.getElementById('backwardationAlert');
        const bwTxt = document.getElementById('bwText');
        if (bwEl && bwTxt && d.backwardation) {
            bwEl.classList.remove('hidden');
            const frontIv = tsData[0]?.avg_iv?.toFixed(1) || '?';
            const backIv = tsData[tsData.length-1]?.avg_iv?.toFixed(1) || '?';
            bwTxt.textContent = `⚠️ IV倒挂: 近期${frontIv}% > 远期${backIv}%，市场恐慌信号`;
        } else if (bwEl) {
            bwEl.classList.add('hidden');
        }

        const ctx = document.getElementById('termStructureChart');
        if (!ctx) return;
        const validTs = tsData.filter(t => t.avg_iv !== null && t.avg_iv > 0);
        if (validTs.length < 2) {
            ctx.parentElement.innerHTML = '<div class="text-gray-500 text-center py-8 text-sm">数据不足 (' + validTs.length + ' 个到期月份)</div>';
            return;
        }

        if (typeof Chart === 'undefined') {
            ctx.parentElement.innerHTML = '<div class="text-yellow-500 text-center py-8 text-sm">⚠️ Chart.js 未加载</div>';
            return;
        }

        const isBackwardation = d.backwardation;
        const lineColor = isBackwardation ? '#ef4444' : '#22d3ee';
        const fillColor = isBackwardation ? 'rgba(239,68,68,0.08)' : 'rgba(34,211,238,0.08)';

        if (tsChart) try { tsChart.destroy(); } catch(e) {}
        tsChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: validTs.map(t => t.dte + 'D'),
                datasets: [{
                    label: 'ATM IV (%)',
                    data: validTs.map(t => t.avg_iv),
                    borderColor: lineColor,
                    backgroundColor: fillColor,
                    fill: true,
                    tension: 0.35,
                    pointRadius: 4,
                    pointHoverRadius: 7,
                    pointBackgroundColor: validTs.map(t => t.avg_iv > 80 ? '#ef4444' : t.dte <= 7 ? '#f59e0b' : lineColor),
                    pointBorderColor: validTs.map(t => t.avg_iv > 80 ? '#ef4444' : t.dte <= 7 ? '#f59e0b' : lineColor),
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(17,24,39,0.95)',
                        borderColor: 'rgba(75,85,99,0.3)',
                        borderWidth: 1,
                        titleFont: { size: 11 },
                        bodyFont: { size: 12, weight: 'bold' },
                        padding: 10,
                        callbacks: {
                            title: (items) => {
                                const t = validTs[items[0].dataIndex];
                                return t.expiry ? `到期: ${t.expiry} (DTE ${t.dte})` : `DTE ${t.dte}`;
                            },
                            label: (item) => `ATM IV: ${item.raw.toFixed(1)}%`
                        }
                    }
                },
                scales: {
                    y: {
                        title: { display: true, text: 'ATM IV (%)', color: '#6b7280', font: { size: 10 } },
                        grid: { color: 'rgba(255,255,255,0.04)' },
                        ticks: { color: '#6b7280', font: { size: 10 } },
                        suggestedMin: 30,
                        suggestedMax: 80
                    },
                    x: {
                        grid: { color: 'rgba(255,255,255,0.04)' },
                        ticks: { color: '#6b7280', font: { size: 10 }, maxRotation: 0 }
                    }
                },
                interaction: {
                    intersect: false,
                    mode: 'index'
                }
            }
        });
        console.log('TS chart rendered:', validTs.length, 'points');
    } catch(e) {
        console.error('TS error:', e);
        const ctx = document.getElementById('termStructureChart');
        if (ctx) ctx.parentElement.innerHTML = '<div class="text-red-400 text-center py-4 text-xs">❌ 错误: ' + e.message + '</div>';
    }
}

// ============================================================
// Module 2: Max Pain & GEX
// ============================================================
let mpChart = null;
async function loadMaxPain() {
    const spotEl = document.getElementById('mpSpot');
    if (!spotEl) { console.warn('MP: container not found'); return; }
    try {
        const currency = document.getElementById('currencySelect')?.value || 'BTC';
        const resp = await safeFetch(API_BASE + '/api/metrics/max-pain?currency=' + currency);
        const d = await resp.json();
        if (d.error || !d.expiries) { console.warn('MP:', d.error || 'no expiries'); return; }

        const exp = d.expiries[0];
        
        // 更新关键数据卡片
        document.getElementById('mpSpot').textContent = '$' + (d.spot || 0).toLocaleString();
        document.getElementById('mpFlip').textContent = exp.gamma_status && exp.gamma_status.flip_strike ? '$' + exp.gamma_status.flip_strike.toLocaleString() : '--';
        document.getElementById('mpPrice').textContent = '$' + (exp.max_pain || 0).toLocaleString();
        document.getElementById('mpDist').textContent = (exp.dist_pct || 0).toFixed(1) + '%';
        document.getElementById('mpPCR').textContent = (exp.pcr || 0).toFixed(2);
        document.getElementById('mpSignal').textContent = exp.signal || '';
        
        // 更新 Gamma 状态指示器
        const statusCard = document.getElementById('gammaStatusCard');
        const adviceCard = document.getElementById('gammaAdviceCard');
        if (exp.gamma_status && statusCard) {
            statusCard.classList.remove('hidden');
            statusCard.className = 'mb-3 p-3 rounded-lg border ' + 
                (exp.gamma_status.region === 'long' ? 'border-emerald-500/30 bg-emerald-500/5' :
                 exp.gamma_status.region === 'short' ? 'border-red-500/30 bg-red-500/5' :
                 'border-gray-500/30 bg-gray-500/5');
            
            document.getElementById('gammaStatusIcon').textContent = exp.gamma_status.icon || '⚖️';
            document.getElementById('gammaStatusText').textContent = exp.gamma_status.region_cn || '中性区域';
            document.getElementById('gammaStatusText').className = 'text-sm font-bold ' + 
                (exp.gamma_status.region === 'long' ? 'text-emerald-400' :
                 exp.gamma_status.region === 'short' ? 'text-red-400' : 'text-gray-400');
            
            const distText = exp.gamma_status.distance_pct ? 
                (exp.gamma_status.region === 'long' ? '现货高于 Flip 点 ' : '现货低于 Flip 点 ') + exp.gamma_status.distance_pct.toFixed(1) + '%' : '';
            document.getElementById('gammaDistance').textContent = distText;
            document.getElementById('gammaVolatility').textContent = exp.gamma_status.volatility || '';
            document.getElementById('gammaInstitutional').textContent = exp.gamma_status.institutional || '';
            
            // 更新区域距离卡片
            const regionDistEl = document.getElementById('mpRegionDist');
            if (regionDistEl && exp.gamma_status.distance_pct !== undefined) {
                regionDistEl.textContent = (exp.gamma_status.distance_pct > 0 ? '+' : '') + exp.gamma_status.distance_pct.toFixed(1) + '%';
                regionDistEl.className = 'font-mono text-xs ' + 
                    (exp.gamma_status.distance_pct > 5 ? 'text-emerald-400' :
                     exp.gamma_status.distance_pct < -5 ? 'text-red-400' : 'text-gray-400');
            }
        }
        
        // 更新方向性建议
        if (exp.gamma_advice && adviceCard) {
            adviceCard.classList.remove('hidden');
            adviceCard.className = 'mb-3 p-2.5 rounded-lg border ' + 
                (exp.gamma_status && exp.gamma_status.region === 'long' ? 'border-emerald-500/20 bg-emerald-500/5' :
                 exp.gamma_status && exp.gamma_status.region === 'short' ? 'border-red-500/20 bg-red-500/5' :
                 'border-gray-500/20 bg-gray-500/5');
            
            document.getElementById('gammaAdviceText').textContent = exp.gamma_advice.text || '';
            document.getElementById('advicePosition').textContent = exp.gamma_advice.position_pct ? exp.gamma_advice.position_pct + '%' : '--';
            document.getElementById('adviceStrategy').textContent = exp.gamma_advice.strategy || '--';
            document.getElementById('adviceDelta').textContent = exp.gamma_advice.delta_range || '--';
        }

        // 风险预警
        const mmEl = document.getElementById('mmAlert');
        if (exp.mm_signal && mmEl) {
            mmEl.classList.remove('hidden');
            mmEl.className = exp.mm_signal.includes('DANGER') || exp.mm_signal.includes('危险') ? 'mb-3 p-2 rounded text-xs bg-red-900/40 border border-red-500/50 text-red-300' : 'mb-3 p-2 rounded text-xs bg-green-900/30 border border-green-500/30 text-green-300';
            mmEl.textContent = exp.mm_signal;
        } else if (mmEl) {
            mmEl.classList.add('hidden');
        }

        const ctx = document.getElementById('painGexChart');
        if (!ctx || !exp.pain_curve || !exp.pain_curve.length) return;

        if (typeof Chart === 'undefined') {
            ctx.parentElement.innerHTML = '<div class="text-yellow-500 text-center py-8 text-sm">⚠️ Chart.js 未加载</div>';
            return;
        }

        const strikes = exp.pain_chart || exp.pain_curve;
        if (mpChart) try { mpChart.destroy(); } catch(e) {}
        var painData = exp.pain_curve || [];
        var gexData = exp.gex_curve || exp.gex_chart || [];
        var strikeLabels = strikes.map(function(s) { return '$' + (s.strike / 1000).toFixed(0) + 'K'; });
        var painValues = painData.map(function(p) { return p.pain || p.total_pain || 0; });
        var gexValues = gexData.map(function(g) { return g.gex || 0; });
        var mpStrike = exp.max_pain || 0;
        var spotPrice = d.spot || 0;
        
        var painMin = Math.min.apply(null, painValues.filter(function(v){return v>0;}));
        var painMax = Math.max.apply(null, painValues);
        var normPain = painValues.map(function(v) {
            return painMax > painMin ? ((v - painMin) / (painMax - painMin) * 100) : 50;
        });
        
        var gexAbsMax = Math.max.apply(null, gexValues.map(Math.abs));
        var normGex = gexValues.map(function(v) {
            return gexAbsMax > 0 ? (v / gexAbsMax * 100) : 0;
        });

        mpChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: strikeLabels,
                datasets: [
                    {
                        label: 'OI净敞口分布 (归一化)',
                        data: normGex,
                        backgroundColor: gexValues.map(function(v) {
                            return v >= 0 ? 'rgba(34,197,94,0.6)' : 'rgba(239,68,68,0.6)';
                        }),
                        borderColor: gexValues.map(function(v) {
                            return v >= 0 ? 'rgba(34,197,94,1)' : 'rgba(239,68,68,1)';
                        }),
                        borderWidth: 1,
                        yAxisID: 'y1',
                        order: 2
                    },
                    {
                        label: '痛点曲线 (归一化)',
                        data: normPain,
                        type: 'line',
                        borderColor: '#f97316',
                        backgroundColor: 'rgba(249,115,22,0.1)',
                        fill: true,
                        tension: 0.3,
                        pointRadius: 0,
                        pointHoverRadius: 5,
                        pointHoverBackgroundColor: '#f97316',
                        yAxisID: 'y',
                        order: 1
                    }
                ]
            },
            options: {
                responsive: true,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { 
                        labels: { color: '#9ca3af', boxWidth: 12, padding: 15, font: { size: 11 } },
                        title: { display: true, text: '最大痛点 $' + mpStrike.toLocaleString() + ' | 现货 $' + spotPrice.toLocaleString(), color: '#eab308', font: { size: 13, weight: 'bold' } }
                    },
                    tooltip: {
                        callbacks: {
                            title: function(items) {
                                var idx = items[0].dataIndex;
                                var s = strikes[idx];
                                return '行权价: $' + (s.strike || 0).toLocaleString();
                            },
                            afterBody: function(items) {
                                var idx = items[0].dataIndex;
                                var lines = [];
                                if (painData[idx]) {
                                    var pd = painData[idx];
                                    lines.push('实际痛点: $' + pd.pain.toLocaleString());
                                    if (pd.call_pain !== undefined) lines.push('  Call损耗: $' + pd.call_pain.toLocaleString() + ' | Put损耗: $' + pd.put_pain.toLocaleString());
                                }
                                if (gexData[idx]) {
                                    var gd = gexData[idx];
                                    lines.push('OI净敞口: ' + gd.gex.toLocaleString());
                                    if (gd.oi_call !== undefined) lines.push('  Call OI: ' + gd.oi_call.toLocaleString() + ' | Put OI: ' + gd.oi_put.toLocaleString());
                                }
                                var stk = strikes[idx] ? strikes[idx].strike : 0;
                                if (Math.abs(stk - mpStrike) < 500) lines.push('⭐ 最大痛点');
                                if (Math.abs(stk - spotPrice) < 500) lines.push('📍 当前现货');
                                return lines;
                            }
                        }
                    }
                },
                scales: {
                    y: { 
                        type: 'linear', position: 'left', 
                        min: 0, max: 110,
                        title: { display: true, text: '痛点曲线 (%)', color: '#f97316' }, 
                        grid: { color: 'rgba(255,255,255,0.06)' }, 
                        ticks: { color: '#f97316', callback: function(v) { return v + '%'; } } 
                    },
                    y1: { 
                        type: 'linear', position: 'right',
                        title: { display: true, text: 'OI净敞口 (%)', color: '#22c55e' }, 
                        grid: { drawOnChartArea: false }, 
                        ticks: { 
                            color: '#22c55e', 
                            callback: function(v) { 
                                if (Math.abs(v) >= 1000) return (v/1000).toFixed(0) + 'K';
                                return v; 
                            } 
                        }
                    },
                    x: { 
                        grid: { color: 'rgba(255,255,255,0.06)' }, 
                        ticks: { 
                            color: '#9ca3af', maxTicksLimit: 20,
                            callback: function(val, idx) {
                                var s = this.getLabelForValue(val);
                                var stk = parseFloat(s.replace(/[$K]/g, '')) * 1000;
                                if (Math.abs(stk - mpStrike) < 1000) return '🎯 ' + s + ' MP';
                                if (Math.abs(stk - spotPrice) < 1000) return '📍 ' + s + ' SPOT';
                                return s;
                            }
                        } 
                    }
                }
            }
        });
        console.log('MP chart rendered:', strikes.length, 'strikes');
    } catch(e) {
        console.error('MP error:', e);
        const ctx = document.getElementById('painGexChart');
        if (ctx) ctx.parentElement.innerHTML = '<div class="text-red-400 text-center py-4 text-xs">❌ 错误: ' + e.message + '</div>';
    }
}

// ============================================================
// Module 3: Martingale Sandbox
// ============================================================
async function runSandbox() {
    var symbol = (document.getElementById('sbSymbol').value || '').trim() || 'BTC-26APR26-65000-P';
    var crash = parseFloat(document.getElementById('sbCrash').value) || 45000;
    var reserve = parseFloat(document.getElementById('sbReserve').value) || 50000;
    var nContracts = parseInt(document.getElementById('sbContracts').value) || 1;

    var resultDiv = document.getElementById('sandboxResult');
    if (!resultDiv) { alert('沙盘容器未找到'); return; }
    resultDiv.innerHTML = '<div class="text-center py-4 text-cyan-400"><i class="fas fa-spinner fa-spin mr-2"></i>🔄 推演计算中...</div>';

    try {
        var resp = await safeFetch(API_BASE + '/api/sandbox/simulate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ current_symbol: symbol, crash_price: crash, reserve_capital: reserve, num_contracts: nContracts })
        });
        var d = await resp.json();

        var html = '';
        html += '<div class="p-3 rounded-lg ' + (d.crash && d.crash.drop_pct < -20 ? 'bg-red-900/30 border border-red-500/30' : 'bg-gray-800') + ' mb-3">';
        html += '<div class="flex justify-between items-center mb-2">';
        html += '<span class="text-sm font-medium">📉 崩盘情景模拟</span>';
        html += '<span class="text-xs font-mono">$' + (d.crash ? d.crash.from.toLocaleString() : '?') + ' -> $' + (d.crash ? d.crash.to.toLocaleString() : '?') + (' (' + (d.crash ? d.crash.drop_pct : '?') + '%)</span>');
        html += '</div>';
        html += '<div class="grid grid-cols-3 gap-2 text-xs">';
        html += '<div>当前持仓: <span class="text-white">' + (d.position ? d.position.symbol : '?') + '</span></div>';
        html += '<div>预估亏损: <span class="' + (d.loss > 0 ? 'text-red-400' : '') + '">$' + (d.loss || 0).toLocaleString() + '</span></div>';
        html += '<div>后备资金: <span class="text-cyan-400">$' + (d.reserve || 0).toLocaleString() + '</span></div>';
        html += '</div></div>';

        (d.steps || []).forEach(function(st) {
            var sc = st.status === 'danger' ? 'border-red-500/50 bg-red-900/20' : st.status === 'warning' ? 'border-yellow-500/50 bg-yellow-900/20' : 'border-green-500/50 bg-green-900/20';
            html += '<div class="p-3 rounded-lg border ' + sc + ' mb-2">';
            html += '<div class="text-sm font-medium mb-1">第 ' + st.step + ': ' + st.title + '</div>';
            (st.details || []).forEach(function(det) { html += '<div class="text-xs text-gray-300 ml-2 py-0.5">' + det + '</div>'; });
            if (st.alert) {
                var ac = st.status === 'danger' ? 'text-red-400' : st.status === 'warning' ? 'text-yellow-400' : 'text-green-400';
                html += '<div class="mt-2 text-xs font-medium ' + ac + '">' + st.alert + '</div>';
            }
            html += '</div>';
        });

        if (d.best) {
            html += '<div class="p-3 rounded-lg bg-purple-900/20 border border-purple-500/30 mt-2">';
            html += '<div class="text-sm font-medium mb-2">🎯 推荐恢复方案</div>';
            html += '<div class="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">';
            html += '<div>恢复合约: <span class="text-white">' + (d.best.symbol || '?') + '</span></div>';
            html += '<div>加仓数量: <span class="text-white">' + (d.best.contracts || 0) + 'x</span></div>';
            html += '<div>所需保证金: <span class="text-yellow-400">$' + ((d.best.margin || 0)).toLocaleString() + '</span></div>';
            var nc = d.best.net >= 0 ? 'text-green-400' : 'text-red-400';
            html += '<div>净盈亏: <span class="' + nc + '">$' + ((d.best.net || 0)).toLocaleString() + '</span></div>';
            var rc = d.best.reserve >= 0 ? 'text-green-400' : 'text-red-400';
            html += '<div>剩余后备金: <span class="' + rc + '">$' + ((d.best.reserve || 0)).toLocaleString() + '</span></div>';
            html += '</div></div>';
        }

        if (d.n_cands === 0) {
            html += '<div class="text-yellow-400 text-xs mt-2 p-2 bg-yellow-900/20 rounded">⚠️ 该价格水平下无可用恢复合约（链上无深度或IV过高）</div>';
        }

        resultDiv.innerHTML = html;
    } catch(e) {
        resultDiv.innerHTML = '<div class="text-red-400 text-sm p-3">❌ 错误: ' + e.message + '</div>';
    }
}


function exportCSV() {
    const currency = document.getElementById('currencySelect')?.value || 'BTC';
    const url = `${API_BASE}/api/export/csv?currency=${currency}&hours=168`;
    const a = document.createElement('a');
    a.href = url;
    a.download = `options_${currency}_168h.csv`;
    a.click();
}


async function loadPcrChart(currency = 'BTC', hours = 168) {
    try {
        const res = await safeFetch(`${API_BASE}/api/charts/pcr?currency=${currency}&hours=${hours}`);
        const data = await res.json();
        const ctx = document.getElementById('pcrChart');
        if (!ctx || !data || data.length === 0) return;
        if (window._pcrChart) window._pcrChart.destroy();
        window._pcrChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.map(d => d.time?.slice(5, 16)),
                datasets: [{
                    label: 'Put/Call Ratio',
                    data: data.map(d => d.pcr),
                    borderColor: 'rgb(168, 85, 247)',
                    backgroundColor: 'rgba(168, 85, 247, 0.1)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 2
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: { title: { display: true, text: 'PCR', color: '#9ca3af' }, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9ca3af' } },
                    x: { grid: { display: false }, ticks: { color: '#9ca3af', maxTicksLimit: 8 } }
                }
            }
        });
    } catch(e) { console.warn('PCR chart failed:', e); }
}

document.addEventListener('DOMContentLoaded', function() {
    setTimeout(function() { loadWindAnalysis(); }, 2000);
    setTimeout(function() { loadTermStructure(); }, 2500);
    setTimeout(function() { loadMaxPain(); }, 3000);
});


// \u{1F504} 正收益滚仓计算器器逻辑
function openRollCalcModal() {
    const el = document.getElementById('rollCalcInline');
    if (el) {
        el.scrollIntoView({behavior: 'smooth', block: 'start'});
        const curSpot = currentSpotPrice;
        if (curSpot && !document.getElementById('rcOldStrike').value) {
            document.getElementById('rcOldStrike').value = Math.round(curSpot * 0.95);
        }
        document.getElementById('rcOldStrike').focus();
    }
}


