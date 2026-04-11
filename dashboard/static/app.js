
function $(id) { return document.getElementById(id); }

function safeHTML(str) {
    if (str == null) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

const STRATEGY_PRESETS = {
    "PUT": {
        "conservative": {"max_delta": 0.20, "min_dte": 30, "max_dte": 45, "label": "纯收租"},
        "standard":     {"max_delta": 0.30, "min_dte": 14, "max_dte": 35, "label": "标准平衡"},
        "aggressive":   {"max_delta": 0.40, "min_dte": 7,  "max_dte": 28, "label": "折价接货"}
    },
    "CALL": {
        "conservative": {"max_delta": 0.30, "min_dte": 30, "max_dte": 45, "label": "保留上涨"},
        "standard":     {"max_delta": 0.45, "min_dte": 14, "max_dte": 35, "label": "标准备兑"},
        "aggressive":   {"max_delta": 0.55, "min_dte": 7,  "max_dte": 28, "label": "强横盘"}
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
let chartPeriods = { apr: 168, dvol: 168 };
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

        const response = await safeFetch(`${API_BASE}/api/recovery-calculate`, {
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
        const response = await safeFetch(`${API_BASE}/api/latest?currency=${currency}`);
        // safeFetch already throws on non-2xx, so no need to check response.status

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
        tbody.innerHTML = `<tr><td colspan="21" class="text-center py-12 text-gray-500"><div class="flex flex-col items-center gap-3"><i class="fas fa-inbox text-3xl text-gray-600"></i><p>暂无符合条件的合约</p><p class="text-xs text-gray-600">尝试调整扫描参数</p></div></td></tr>`;
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

        // 统一合约名称字段
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

        if (riskLevel === '极高' || riskLevel === '高') {
            riskAlerts.push({ symbol: symbol, strike: contract.strike, delta: deltaAbs, distancePct, level: riskLevel, reason: riskLevel === '极高' ? `Delta(${deltaAbs.toFixed(3)})>0.45 且 价格接近Strike(${distancePct.toFixed(1)}%)` : `Delta(${deltaAbs.toFixed(3)})>0.45` });
        }

        // 精简版12列表格数据
        const spreadColor = (contract.spread_pct || 0) > 5 ? 'text-orange-400' : 'text-gray-400';
        const lossVal = Math.abs(contract.loss_at_10pct || 0);
        const breakeven = contract.breakeven || 0;
        const oi = contract.open_interest || 0;
        const spreadPct = contract.spread_pct || 0;

        const gamma = contract.gamma || 0;
        const vega = contract.vega || 0;
        const iv = contract.mark_iv || contract.iv || 0;
        const pop = contract.pop || null;
        const bePct = contract.breakeven_pct || null;
        const ivRank = contract.iv_rank || null;

        return `<tr class="hover:bg-white/[0.02] transition ${riskClass}">
            <td class="py-2 px-3 text-center"><span class="${platformColor} text-xs font-semibold">${contract.platform}</span></td>
            <td class="py-2 px-2 text-center"><span class="${contract.option_type === 'PUT' ? 'text-green-400' : 'text-blue-400'} text-xs font-bold">${contract.option_type || 'PUT'}</span></td>
            <td class="py-2 px-2 text-center font-mono text-xs tabular-nums">${symbol.split('-')[1] || ''}</td>
            <td class="py-2 px-2 text-center text-xs tabular-nums">${(contract.dte || 0).toFixed(0)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums">$${Math.round(contract.strike).toLocaleString()}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums font-semibold ${deltaAbs > 0.35 ? 'text-red-400' : deltaAbs > 0.25 ? 'text-yellow-400' : 'text-green-400'}">${deltaAbs.toFixed(4)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${gamma > 0.15 ? 'text-orange-400' : 'text-gray-300'}">${gamma.toFixed(4)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${vega > 50 ? 'text-yellow-400' : 'text-gray-300'}">${vega.toFixed(1)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${iv ? (iv >= 80 ? 'text-red-400' : iv >= 50 ? 'text-yellow-400' : 'text-emerald-400') : 'text-gray-300'}">${iv ? iv.toFixed(1) + '%' : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs font-bold text-green-400 tabular-nums">${(contract.apr || 0).toFixed(1)}%</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${pop ? (pop >= 70 ? 'text-emerald-400' : pop >= 50 ? 'text-yellow-300' : 'text-orange-400') : 'text-gray-500'}">${pop ? pop.toFixed(0) + '%' : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-yellow-300/90">$${(contract.premium || contract.premium_usd || 0).toLocaleString(undefined, {maximumFractionDigits: 2})}</td>
            <td class="py-2 px-2 text-center"><span class="${liqColor} text-xs font-medium">${contract.liquidity_score}</span></td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-red-400/80">$${lossVal.toLocaleString(undefined, {maximumFractionDigits: 0})}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-blue-300/80">$${breakeven.toLocaleString(undefined, {maximumFractionDigits: 0})}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${bePct ? (bePct >= 10 ? 'text-emerald-400' : bePct >= 5 ? 'text-yellow-300' : 'text-orange-400') : 'text-gray-500'}">${bePct ? bePct.toFixed(1) + '%' : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-gray-400">${oi.toLocaleString()}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${spreadColor}">${spreadPct.toFixed(2)}%</td>
            <td class="py-2 px-2 text-center font-mono text-xs tabular-nums ${ivRank ? (ivRank >= 70 ? 'text-red-400' : ivRank <= 30 ? 'text-emerald-400' : 'text-gray-400') : 'text-gray-500'}">${ivRank ? String(ivRank).split('.')[0] : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${contract._score !== undefined ? (contract._score >= 0.7 ? "text-emerald-400 font-bold" : contract._score >= 0.5 ? "text-green-300" : contract._score >= 0.3 ? "text-yellow-300" : "text-gray-500") : "text-gray-500"}" title="\u52a0\u6743\u8bc4\u5206: APR(25%)+POP(25%)+\u5b89\u5168\u57ab(20%)+\u6d41\u52a8\u6027(15%)+IV\u4e2d\u6027(15%)">${contract._score !== undefined ? contract._score.toFixed(3) : "-"}</td>
            <td class="py-2 px-3 text-center">${riskBadge}</td>
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
                <p class="sm:text-gray-300">建议平仓当前合约，卖出更低行权价的远期Put，获取更高权利金的同时下移防线。</p>
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

const flowSugg = {
    protective_hedge: '\u673a\u6784\u62a4\u51b2 \u2193 \u77edf\u671f\u8c28\u614e',
    premium_collect: '\u6536\u53d6\u6743\u5229 \u2191 \u503c\u597d\u73af\u5883',
    speculative_put: '\u770b\u8dcc\u6295\u673a \u2193 \u98ce\u9669\u5347',
    call_momentum: '\u8ffd\u6da8\u5efa\u4ed3 \u2191 \u770b\u597d\u884c\u60c5',
    call_speculative: '\u770b\u6da8\u6295\u673a \u2191 \u5c0f\u5355\u4f4e\u4f4d\u5165\u573a',
    covered_call: '\u5907\u5156\u5f00\u4ed3 \u2191 \u9501\u5b9a\u6536\u76ca',
    call_overwrite: '\u6539\u4ed3\u64cd\u4f5c \u2191 \u8c03\u6574\u4ef7\u683c',
};

function updateLargeTrades(trades, count) {
    const container = document.getElementById('largeTradesList');
    const titleCount = document.getElementById('largeTradesTitleCount');

    if (count > 0) { titleCount.textContent = count; titleCount.classList.remove('hidden'); }
    else titleCount.classList.add('hidden');

    if (!trades || trades.length === 0) {
        container.innerHTML = '<div class="text-gray-500 text-center py-4 text-sm">近1小时无大单成交</div>';
        return;
    }

    const flowNames = {
        // === Sell PUT = 永远看涨 ===
        sell_put_deep_itm: '保护性对冲',
        sell_put_atm_itm: '收权利金',
        sell_put_otm: '备兑开仓',
        // === Buy PUT = 看跌/对冲 ===
        buy_put_deep_itm: '保护性买入',
        buy_put_atm: '看跌投机',
        buy_put_otm: '看跌投机',
        // === Sell CALL = 中性/看不涨 ===
        sell_call_otm: '备兑开仓',
        sell_call_itm: '改仓操作',
        // === Buy CALL = 看涨 ===
        buy_call_atm_itm: '追涨建仓',
        buy_call_otm: '看涨投机',
        // === Legacy / Fallback ===
        protective_hedge: '保护性对冲',
        premium_collect: '收权利金',
        speculative_put: '看跌投机',
        call_speculative: '看涨投机',
        call_momentum: '追涨建仓',
        covered_call: '备兑开仓',
        call_overwrite: '改仓操作',
        put_buy_hedge: '保护性买入',
        unclassified: '未分类',
        unknown: '未知流向'
    };

    container.innerHTML = trades.map(trade => {
        const inst = trade.instrument_name || trade.symbol || '';
        const dir = (trade.direction || '').toLowerCase();
        const flow = trade.flow_label || '';
        const notional = trade.notional_usd || 0;
        const strike = trade.strike || 0;
        const optType = trade.option_type || '';

        let directionIcon, directionClass, dirLabel;
        if (dir === 'buy') {
            directionIcon = '<i class="fas fa-arrow-up text-red-400"></i>'; directionClass = 'border-l-red-500'; dirLabel = '买入';
        } else if (dir === 'sell') {
            directionIcon = '<i class="fas fa-arrow-down text-green-400"></i>'; directionClass = 'border-l-green-500'; dirLabel = '卖出';
        } else {
            directionIcon = '<i class="fas fa-minus text-gray-400"></i>'; directionClass = 'border-l-gray-500'; dirLabel = '';
        }

        const severity = trade.severity || (notional >= 2000000 ? 'high' : notional >= 500000 ? 'medium' : 'info');
        const sevConfig = {
            high: { bg: 'bg-red-500/20', badge: 'bg-red-500', label: '\u5927\u5355', emoji: '\u26a0\ufe0f' },
            medium: { bg: 'bg-orange-500/20', badge: 'bg-orange-500', label: '\u4e2d\u5355', emoji: '\U0001f7e1' },
            info: { bg: 'bg-blue-500/10', badge: 'bg-blue-500', label: '\u666e\u901a', emoji: '\u2705' }
        };
        const sev = sevConfig[severity] || sevConfig.info;

        const flowCN = flowNames[flow] || flow || '';
        const notionalStr = notional >= 1000000 ? '$' + (notional / 1000000).toFixed(2) + 'M' : '$' + Math.round(notional).toLocaleString();
        const strikeStr = strike ? '@ $' + strike.toLocaleString() : '';
        const optIsPut = optType && optType.toUpperCase().startsWith('P');
        const optTypeTag = optType ? '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold ' + (optIsPut ? 'bg-purple-500/30 text-purple-300' : 'bg-green-500/30 text-green-300') + '">' + (optIsPut ? 'PUT' : 'CALL') + '</span>' : '';

        return `<div class="${sev.bg} border-l-4 ${directionClass} rounded-lg p-3 text-xs hover:bg-white/5 transition cursor-default">
            <div class="flex items-start gap-2">
                <div class="flex-shrink-0 mt-0.5">${directionIcon}</div>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-1.5 mb-1 flex-wrap">
                        <span class="font-medium text-white truncate">${inst || '大宗成交'}</span>
                        ${optTypeTag}
                        ${dirLabel ? '<span class="text-gray-400">·</span><span class="' + (dir === 'buy' ? 'text-red-400' : 'text-green-400') + '">' + dirLabel + '</span>' : ''}
                        ${strikeStr ? '<span class="text-gray-500">' + strikeStr + '</span>' : ''}
                        <span class="${sev.badge} text-white text-[10px] px-1.5 py-0.5 rounded font-bold flex-shrink-0 ml-auto">${sev.label} ${sev.emoji}</span>
                    </div>
                    <div class="flex items-center gap-2 flex-wrap">
                        ${flowCN ? '<span class="text-cyan-300">' + flowCN + '</span>' : ''}
                        ${flowCN ? '<span class="text-gray-500 text-[10px] ml-1">' + (() => {
                            const suggestions = {
                                'protective_hedge': '\u673a\u6784\u62a4\u51b2\u2193 \u77ed0\u671f\u8c28\u614e',
                                'premium_collect': '\u6536\u53d6\u6743\u5229\u9650 \u2191 \u503c\u597d\u73af\u5883',
                                'speculative_put': '\u770b\u8dcc\u6295\u673a \u2193 \u98ce\u9669\ 吻\u5347',
                                'call_momentum': '\u8ffd\u6da8\u5efa\u4ed3 \u2191 \u770b\u597d\u884c\u60c5',
                                'call_speculative': '\u770b\u6da8\u6295\u673a \u2191 \u5c0f\u5355\u4f4e\u4f4d\u5165\u573a',
                                'covered_call': '\u5907\u5156\u5f00\u4ed3 \u2191 \u9501\u5b9a\u6536\u76ca',
                                'call_overwrite': '\u6539\u4ed3\u64cd\u4f5c \u2191 \u8c03\u6574\u4ef7\u683c',
                                'unclassified': '',
                                'unknown': ''
                            };
                            return suggestions[flow] || '';
                        })() + '</span>' : ''}
                        <span class="text-yellow-300 font-medium">${notionalStr}</span>
                    </div>
                </div>
            </div>
        </div>`;
    }).join('');
}

function updateLastUpdateTime(timestamp) {
    let date;
    if (timestamp && timestamp.includes('T')) {
        date = new Date(timestamp);
    } else if (timestamp) {
        const parts=timestamp.split(/[- :]/);
        date=new Date(parts[0],parts[1]-1,parts[2],parts[3],parts[4],parts[5]);
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

        aprChart.data.labels = data.map(d => {
            const date = new Date(d.time || d.timestamp);
            return hours <= 24 ? `${date.getHours()}:${String(date.getMinutes()).padStart(2,'0')}` : hours <= 168 ? `${date.getMonth()+1}/${date.getDate()} ${date.getHours()}:00` : `${date.getMonth()+1}/${date.getDate()}`;
        });
        const cleanBest = data.map(d => d.best_safe_apr != null ? d.best_safe_apr : null);
        const cleanP75 = data.map(d => d.p75_safe_apr != null ? d.p75_safe_apr : null);
        aprChart.data.datasets[0].data = cleanBest;
        aprChart.data.datasets[1].data = cleanP75;
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

        dvolChart.data.labels = data.map(d => {
            const date = new Date(d.time || d.timestamp);
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
    alertQueue.push({m:message, t:type, time:Date.now()});
    alertQueue = alertQueue.filter(a => Date.now() - a.time < 3000).slice(-3);
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
        { msg: '布局优化：精简表格至12列核心指标，提升可读性', type: 'success' }
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
        const currency = document.getElementById('tradesCurrency').value;
        const days = document.getElementById('tradesDays').value;

        const response = await safeFetch(`${API_BASE}/api/trades/wind-analysis?currency=${currency}&days=${days}`);
        const data = await response.json();
        const summary = data.summary || {};
        const strikes = data.strike_flows || [];
        const flows = data.flow_breakdown || [];

        const countEl = document.getElementById('tradesStatsCount');
        countEl.textContent = `${summary.total_trades || 0} 笔`;
        countEl.classList.remove('hidden');

        const summaryCard = document.getElementById('windSummaryCard');
        if (summary.total_trades > 0) {
            summaryCard.classList.remove('hidden');
            const score = summary.sentiment_score || 0;
            let icon, scoreLabel, scoreClass;
            if (score >= 2) { icon = '🐂'; scoreLabel = '偏多'; scoreClass = 'bg-green-500/20 text-green-300'; }
            else if (score >= 1) { icon = '📈'; scoreLabel = '温和看多'; scoreClass = 'bg-green-900/30 text-green-400'; }
            else if (score > -1) { icon = '➡️'; scoreLabel = '中性'; scoreClass = 'bg-gray-700 text-gray-300'; }
            else if (score > -2) { icon = '📉'; scoreLabel = '温和看空'; scoreClass = 'bg-red-900/30 text-red-400'; }
            else { icon = '🐻'; scoreLabel = '偏空'; scoreClass = 'bg-red-500/20 text-red-300'; }
            document.getElementById('windSentimentIcon').textContent = icon;
            const scEl = document.getElementById('windSentimentScore');
            scEl.textContent = scoreLabel;
            scEl.className = `text-xs font-mono px-2 py-0.5 rounded ${scoreClass}`;
            document.getElementById('windSentimentText').textContent = data.sentiment_text || '';
            document.getElementById('windBuySellRatio').textContent =
                `${(summary.buy_ratio * 100).toFixed(0)}% / ${(summary.sell_ratio * 100).toFixed(0)}%`;
            document.getElementById('windTotalNotional').textContent =
                `$${(summary.total_notional / 1000000).toFixed(1)}M`;
            const flowNames = {
                // === Sell PUT = 永远看涨 ===
                'sell_put_deep_itm': '保护性对冲',
                'sell_put_atm_itm': '收权利金',
                'sell_put_otm': '备兑开仓',
                // === Buy PUT = 看跌/对冲 ===
                'buy_put_deep_itm': '保护性买入',
                'buy_put_atm': '看跌投机',
                'buy_put_otm': '看跌投机',
                // === Sell CALL = 中性/看不涨 ===
                'sell_call_otm': '备兑开仓',
                'sell_call_itm': '改仓操作',
                // === Buy CALL = 看涨 ===
                'buy_call_atm_itm': '追涨建仓',
                'buy_call_otm': '看涨投机',
                // === Legacy / Fallback ===
                'protective_hedge': '保护性对冲',
                'premium_collect': '收权利金',
                'speculative_put': '看跌投机',
                'speculative_call': '看涨投机',
                'call_momentum': '追涨建仓',
                'covered_call': '备兑开仓',
                'call_overwrite': '改仓操作',
                'put_buy_hedge': '保护性买入',
                'unclassified': '未分类',
                'unknown': '未知流向'
            };
            document.getElementById('windDominantFlow').textContent =
                flowNames[summary.dominant_flow] || summary.dominant_flow || '-';
        } else {
            summaryCard.classList.add('hidden');
        }

        const spotEl = document.getElementById('windSpotMarker');
        if (summary.spot_price > 0) {
            spotEl.textContent = `● 现价 $${summary.spot_price.toLocaleString()}`;
            spotEl.classList.remove('hidden');
        } else {
            spotEl.classList.add('hidden');
        }

        const chartEl = document.getElementById('strikeFlowsChart');
        if (strikes.length === 0) {
            chartEl.innerHTML = '<div class="text-gray-500 text-center py-4">暂无大宗交易数据</div>';
        } else {
            const maxAbsNet = Math.max(...strikes.map(s => Math.abs(s.net)), 1);
            chartEl.innerHTML = strikes.map(s => {
                const netPct = s.net / maxAbsNet * 100;
                const isBuy = s.net > 0;
                const barW = Math.min(95, Math.abs(netPct));
                const distPct = s.dist_from_spot_pct || 0;
                const distLabel = distPct > 0 ? `+${distPct}%` : `${distPct}%`;
                const optType = (s.option_type || '').toUpperCase();
                const isPut = optType === 'PUT' || optType[0] === 'P';
                const optTag = optType ? `<span class="px-1 py-0.5 rounded text-[9px] font-bold ${isPut ? 'bg-purple-500/30 text-purple-300' : 'bg-green-500/30 text-green-300'}">${isPut ? 'PUT' : 'CALL'}</span>` : '';
                let colorClass, bgColor;
                if (s.net > 3) { colorClass = 'text-green-400'; bgColor = 'from-green-700 to-green-500'; }
                else if (s.net > 0) { colorClass = 'text-green-300'; bgColor = 'from-green-900 to-green-700'; }
                else if (s.net > -3) { colorClass = 'text-red-300'; bgColor = 'from-red-900 to-red-700'; }
                else { colorClass = 'text-red-400'; bgColor = 'from-red-700 to-red-500'; }
                return `<div class="flex items-center gap-2 py-1 hover:bg-white/5 rounded px-1">
                    <span class="font-mono text-xs w-20 text-right ${s.strike == (summary.key_levels?.heaviest_strike) ? 'text-yellow-300 font-bold' : ''}">$${Math.round(s.strike).toLocaleString()}</span>
                    ${optTag}
                    <div class="flex-1 bg-gray-800 rounded-full h-3 overflow-hidden relative">
                        <div class="h-full rounded-full bg-gradient-to-r ${bgColor}" style="width: ${barW}%; ${isBuy ? '' : 'margin-left:' + (95-barW) + '%'}"></div>
                        ${s.strike == (summary.key_levels?.net_support) ? '<span class="absolute left-0 top-0 bottom-0 w-0.5 bg-yellow-400"></span>' : ''}
                        ${s.strike == (summary.key_levels?.net_resistance) ? '<span class="absolute right-0 top-0 bottom-0 w-0.5 bg-orange-400"></span>' : ''}
                    </div>
                    <span class="${colorClass} text-xs w-10 text-right tabular-nums">${s.net > 0 ? '+' : ''}${s.net}</span>
                    <span class="text-gray-500 text-xs w-12 text-right">(${distLabel})</span>
                    <span class="text-gray-500 text-xs w-6 text-center tabular-nums">${s.buys}/${s.sells}</span></div>`;
            }).join('');
        }

        const fbEl = document.getElementById('flowBreakdown');
        if (flows.length === 0) {
            fbEl.innerHTML = '<div class="col-span-2 text-gray-500 text-center py-2 text-xs">无流向数据</div>';
        } else {
            const maxCnt = Math.max(...flows.map(f => f.count), 1);
            fbEl.innerHTML = flows.map(f => {
                const pctBar = Math.min(100, f.count / maxCnt * 100);
                return `<div class="rounded-lg bg-gray-800/50 p-2 flex items-center gap-2"><div class="w-1.5 h-1.5 rounded-full flex-shrink-0" style="background:${f.pct > 30 ? '#f97316' : '#6b7280'}"></div><div class="flex-1 min-w-0"><div class="text-xs text-gray-200 truncate">${f.label_cn || f.label}</div><div class="mt-0.5 bg-gray-700 rounded-full h-1.5 overflow-hidden"><div class="h-full rounded-full bg-orange-500/70" style="width:${f.pct}%"></div></div></div><div class="text-right flex-shrink-0"><div class="text-xs font-mono text-white">${f.count}</div><div class="text-[10px] text-gray-500">${f.pct}%</div></div></div>`;
            }).join('');
        }
    } catch (error) {
        console.error('加载风向分析失败:', error);
    }
}
// ============================================================
// Module 1: IV Term Structure
// ============================================================
let tsChart = null;
async function loadTermStructure() {
    const statusEl = document.getElementById('ts7');
    if (!statusEl) { console.warn('TS: container not found'); return; }
    try {
        const resp = await safeFetch(API_BASE + '/api/charts/vol-surface?currency=BTC');
        const d = await resp.json();
        if (d.error) { console.warn('TS:', d.error); return; }

        [7,14,30,60,90].forEach(dte => {
            const el = document.getElementById('ts' + dte);
            if (el) {
                const ts = (d.term_structure || []).find(t => t.dte === dte);
                el.textContent = ts && ts.avg_iv ? ts.avg_iv + '%' : '--';
            }
        });

        const bwEl = document.getElementById('backwardationAlert');
        const bwTxt = document.getElementById('bwText');
        if (bwEl && bwTxt && d.backwardation) {
            bwEl.classList.remove('hidden');
            bwTxt.textContent = d.alert || '⚠️ 远期IV < 近期IV（倒挂/Backwardation）！';
        }

        const ctx = document.getElementById('termStructureChart');
        if (!ctx) return;
        const validTs = (d.term_structure || []).filter(t => t.avg_iv !== null && t.avg_iv > 0);
        if (validTs.length < 2) {
            ctx.parentElement.innerHTML = '<div class="text-gray-500 text-center py-8 text-sm">数据不足 (' + validTs.length + ' 个到期月份)</div>';
            return;
        }

        if (typeof Chart === 'undefined') {
            ctx.parentElement.innerHTML = '<div class="text-yellow-500 text-center py-8 text-sm">⚠️ Chart.js 未加载</div>';
            return;
        }

        if (tsChart) try { tsChart.destroy(); } catch(e) {}
        tsChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: validTs.map(t => t.dte + 'D'),
                datasets: [{
                    label: '平均隐含波动率 (%)',
                    data: validTs.map(t => t.avg_iv),
                    borderColor: '#22d3ee',
                    backgroundColor: 'rgba(34,211,238,0.1)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 5,
                    pointBackgroundColor: validTs.map(t => t.dte <= 14 ? '#ef4444' : '#22d3ee')
                }]
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: {
                    y: { title: { display: true, text: '隐含波动率 (%)', color: '#9ca3af' }, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9ca3af' } },
                    x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9ca3af' } }
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
        const resp = await safeFetch(API_BASE + '/api/metrics/max-pain?currency=BTC');
        const d = await resp.json();
        if (d.error || !d.expiries) { console.warn('MP:', d.error || 'no expiries'); return; }

        const exp = d.expiries[0];
        document.getElementById('mpSpot').textContent = '$' + (d.spot || 0).toLocaleString();
        document.getElementById('mpPrice').textContent = '$' + (exp.max_pain || 0).toLocaleString();
        document.getElementById('mpDist').textContent = (exp.dist_pct || 0).toFixed(1) + '%';
        document.getElementById('mpPCR').textContent = (exp.pcr || 0).toFixed(2);
        document.getElementById('mpSignal').textContent = exp.signal || '';

        const mmEl = document.getElementById('mmAlert');
        if (exp.mm_signal && mmEl) {
            mmEl.classList.remove('hidden');
            mmEl.className = exp.mm_signal.includes('DANGER') ? 'mb-3 p-2 rounded text-xs bg-red-900/40 border border-red-500/50 text-red-300' : 'mb-3 p-2 rounded text-xs bg-green-900/30 border border-green-500/30 text-green-300';
            mmEl.textContent = exp.mm_signal;
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
        if (!ctx || !data.data || data.data.length === 0) return;
        if (window._pcrChart) window._pcrChart.destroy();
        window._pcrChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.data.map(d => d.timestamp?.slice(5, 16)),
                datasets: [{
                    label: 'Put/Call Ratio',
                    data: data.data.map(d => d.pcr),
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

function closeRollCalcModal() {}

async function submitRollCalc() {
    const btn = document.getElementById('rcSubmitBtn');
    const wrapper = document.getElementById('rcResultsWrapper');
    const table = document.getElementById('rcResultsTable');
    const tbody = document.getElementById('rcResultsBody');
    
    table.classList.remove('hidden');
    
    const currency = document.getElementById('rcCurrency').value;
    const oldStrike = parseFloat(document.getElementById('rcOldStrike').value);
    const oldQty = parseFloat(document.getElementById('rcOldQty').value);
    const closeCost = parseFloat(document.getElementById('rcCloseCost').value);
    const reserve = parseFloat(document.getElementById('rcReserve').value);
    
    if (!oldStrike || !oldQty || !closeCost) {
        showAlert('请填写完整的实盘持仓与平仓成本信息', 'error');
        return;
    }
    
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 计算中...';
    tbody.innerHTML = '<tr><td colspan="6" class="text-center py-10 text-cyan-400"><i class="fas fa-spinner fa-spin mr-2"></i>扫描匹配最优滚仓路径...</td></tr>';
    
    try {
        const payload = {
            currency: currency,
            old_strike: oldStrike,
            old_qty: oldQty,
            close_cost_total: closeCost,
            reserve_capital: reserve,
            target_max_delta: parseFloat(document.getElementById('rcMaxDelta').value) || 0.35,
            min_dte: parseInt(document.getElementById('rcMinDte').value) || 7,
            max_dte: parseInt(document.getElementById('rcMaxDte').value) || 90,
            max_qty_multiplier: parseFloat(document.getElementById('rcMaxMult').value) || 3.0
        };
        
        const response = await safeFetch(API_BASE + '/api/calculator/roll', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        const result = await response.json();
        
        if (!result.success || !result.plans || result.plans.length === 0) {
            const meta = (result.meta || {}).filtered || {};
            let reasonHtml = '';
            if (Object.keys(meta).length > 0) {
                reasonHtml = `<br><span class="text-xs text-gray-400">
                    过滤统计: BE超限${meta.break_even_exceeded_cap||0} | 负收益${meta.negative_net_credit||0} | 保证金不足${meta.insufficient_margin||0}<br>
                    建议: 增大倍数上限 / 提高后备金 / 放宽Delta
                </span>`;
            }
            wrapper.innerHTML = `<div class="text-center py-6 ${!result.success ? 'text-red-400' : 'text-yellow-500'}">
                <i class="fas ${!result.success ? 'fa-times-circle' : 'fa-search'} text-2xl mb-2 opacity-50 block"></i>
                <div class="font-medium">${!result.success ? ('计算失败: ' + (result.error || '未知错误')) : '未找到满足条件的正收益滚仓方案'}</div>
                ${reasonHtml ? `<div class="text-xs text-gray-400 mt-2">${reasonHtml}</div>` : '<div class="text-xs text-gray-500 mt-2">建议: 放宽Delta/增大倍数/提高后备金</div>'}
            </div>`;
            table.classList.add('hidden');
        } else {
            wrapper.innerHTML = '';
            table.classList.remove('hidden');
            tbody.innerHTML = result.plans.map((plan, idx) => {
                const isBest = idx === 0;
                return `
                <tr class="hover:bg-white/5 transition ${isBest ? 'bg-green-500/10' : ''}">
                    <td class="py-3 px-3">
                        <div class="flex flex-col">
                            <span class="font-mono text-white flex items-center gap-2">
                                ${isBest ? '<i class="fas fa-crown text-yellow-400"></i>' : ''}
                                ${plan.symbol}
                            </span>
                            <span class="text-[10px] text-gray-500">DTE: ${plan.dte} | APR: ${plan.apr.toFixed(1)}% | 平台: ${plan.platform}</span>
                        </div>
                    </td>
                    <td class="py-3 px-3 text-right">
                        <span class="font-mono text-orange-400 font-bold">${plan.new_qty}</span>
                        <span class="text-xs text-gray-500 ml-1">(BE:${plan.break_even_qty || '?'})</span>
                    </td>
                    <td class="py-3 px-3 text-right font-mono ${plan.delta > 0.3 ? 'text-red-400' : 'text-green-400'}">${plan.delta.toFixed(3)}</td>
                    <td class="py-3 px-3 text-right">
                        <span class="font-mono">$${Math.round(plan.margin_req).toLocaleString()}</span>
                    </td>
                    <td class="py-3 px-3 text-right">
                        <span class="font-mono font-bold text-green-400">+$${Math.round(plan.net_credit).toLocaleString()}</span>
                        <div class="text-[10px] text-gray-500">已扣除平仓成本</div>
                    </td>
                    <td class="py-3 px-3 text-right">
                        <span class="font-mono ${((plan.roi_pct || 0) > 50) ? 'text-green-400 font-bold' : 'text-yellow-300'}">${(plan.roi_pct || 0).toFixed(1)}%</span>
                        <div class="text-[10px] text-gray-500">${(plan.capital_efficiency || 0).toFixed(2)}x</div>
                    </td>
                </tr>
                `;
            }).join('');
        }
        
    } catch (e) {
        wrapper.innerHTML = `<div class="text-center py-6 text-red-500"><i class="fas fa-exclamation-triangle text-2xl mb-2"></i><div>计算失败: ${e.message}</div></div>`;
        table.classList.add('hidden');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-magic"></i> 计算正收益滚仓方案';
    }
}
