import { $, safeHTML, STRATEGY_PRESETS, API_BASE, API_TIMEOUT_MS, FETCH_MAX_RETRIES, TABLE_PAGE_SIZE, getApiKey, safeFetch, getFieldName } from './utils.js';
import { loadTermStructure, showTermStructureError } from './term-structure.js';
import { loadMaxPain, showMaxPainError } from './maxpain.js';
import { runSandbox, exportCSV } from './sandbox.js';

// 向后兼容：将模块函数挂载到 window，供内联 onclick 使用
window.loadTermStructure = () => loadTermStructure({ safeFetch, safeHTML, API_BASE, showAlert: window.showAlert || (()=>{}) });
window.loadMaxPain = () => loadMaxPain({ safeFetch, safeHTML, API_BASE });
window.runSandbox = () => runSandbox({ safeFetch, safeHTML, API_BASE });
window.exportCSV = () => exportCSV(API_BASE);
window.showTermStructureError = showTermStructureError;
window.showMaxPainError = showMaxPainError;
window.safeHTML = safeHTML;
window.safeFetch = safeFetch;
window.API_BASE = API_BASE;
window.API_TIMEOUT_MS = API_TIMEOUT_MS;
window.FETCH_MAX_RETRIES = FETCH_MAX_RETRIES;
window.TABLE_PAGE_SIZE = TABLE_PAGE_SIZE;
window.getApiKey = getApiKey;
window.getFieldName = getFieldName;

// STRATEGY_PRESETS 已从 utils.js 导入
let _currentPreset = 'standard';

/**
 * 期权监控面板 - 前端逻辑
 * 包含：实时扫描、倍投修复计算器、风险预警、滚仓建议
 */

let currentData = null;
let autoRefreshInterval = null;
let dvolChart = null;
let chartPeriods = { dvol: 168, pcr: 168 };
let currentSpotPrice = null;
let scanStatusInterval = null;

let _allContracts = [];
let _displayedCount = 0;
let _currentSortField = null;
let _currentSortDir = 'desc';



document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    loadPageDataAsync();
    loadStats();
    setupEventListeners();
    updateParamDisplay();
    setAutoRefresh(5);
    requestNotificationPermission();
    
    window.addEventListener('online', () => {
        showAlert('网络连接已恢复', 'success');
        loadLatestData(false);
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

    const scanBtn = document.getElementById('scanBtn');
    if (scanBtn) scanBtn.addEventListener('click', triggerScan);

    const exportCsvBtn = document.getElementById('exportCsvBtn');
    if (exportCsvBtn) exportCsvBtn.addEventListener('click', exportCSV);

    const payoffModeSingle = document.getElementById('payoffModeSingle');
    if (payoffModeSingle) payoffModeSingle.addEventListener('click', () => setPayoffMode('single'));
    const payoffModeWheel = document.getElementById('payoffModeWheel');
    if (payoffModeWheel) payoffModeWheel.addEventListener('click', () => setPayoffMode('wheel'));
    const payoffModeCompare = document.getElementById('payoffModeCompare');
    if (payoffModeCompare) payoffModeCompare.addEventListener('click', () => setPayoffMode('compare'));

    const estimatePremiumBtn = document.getElementById('estimatePremiumBtn');
    if (estimatePremiumBtn) estimatePremiumBtn.addEventListener('click', estimatePremium);

    const calcPayoffBtn = document.getElementById('calcPayoffBtn');
    if (calcPayoffBtn) calcPayoffBtn.addEventListener('click', calcPayoff);

    const toggleAdvancedBtn = document.getElementById('toggleAdvancedBtn');
    if (toggleAdvancedBtn) toggleAdvancedBtn.addEventListener('click', toggleAdvancedParams);

    const calcWheelBtn = document.getElementById('calcWheelBtn');
    if (calcWheelBtn) calcWheelBtn.addEventListener('click', calcWheelROI);

    const modeRollBtn = document.getElementById('modeRollBtn');
    if (modeRollBtn) modeRollBtn.addEventListener('click', () => setCalcMode('roll'));
    const modeNewBtn = document.getElementById('modeNewBtn');
    if (modeNewBtn) modeNewBtn.addEventListener('click', () => setCalcMode('new'));
    const modeGridBtn = document.getElementById('modeGridBtn');
    if (modeGridBtn) modeGridBtn.addEventListener('click', () => setCalcMode('grid'));

    const scSubmitBtn = document.getElementById('scSubmitBtn');
    if (scSubmitBtn) scSubmitBtn.addEventListener('click', submitStrategyCalc);

    const presetCon = document.getElementById('presetCon');
    if (presetCon) presetCon.addEventListener('click', () => applyPreset('conservative'));
    const presetStd = document.getElementById('presetStd');
    if (presetStd) presetStd.addEventListener('click', () => applyPreset('standard'));
    const presetAgg = document.getElementById('presetAgg');
    if (presetAgg) presetAgg.addEventListener('click', () => applyPreset('aggressive'));

    const viewSellPut = document.getElementById('viewSellPut');
    if (viewSellPut) viewSellPut.addEventListener('click', () => switchView('sellput'));
    const viewCoveredCall = document.getElementById('viewCoveredCall');
    if (viewCoveredCall) viewCoveredCall.addEventListener('click', () => switchView('coveredcall'));
    const viewWheel = document.getElementById('viewWheel');
    if (viewWheel) viewWheel.addEventListener('click', () => switchView('wheel'));
    const viewAll = document.getElementById('viewAll');
    if (viewAll) viewAll.addEventListener('click', () => switchView('all'));

    const tableHeaders = document.getElementById('tableHeaders');
    if (tableHeaders) {
        tableHeaders.querySelectorAll('th[data-sort]').forEach(th => {
            th.addEventListener('click', () => sortContracts(th.dataset.sort));
        });
    }

    const loadMoreBtn = document.getElementById('loadMoreBtn');
    if (loadMoreBtn) loadMoreBtn.addEventListener('click', loadMoreContracts);

    document.querySelectorAll('.dvol-period-btn').forEach(btn => {
        btn.addEventListener('click', () => setChartPeriod('dvol', parseInt(btn.dataset.period)));
    });
    document.querySelectorAll('.pcr-period-btn').forEach(btn => {
        btn.addEventListener('click', () => setChartPeriod('pcr', parseInt(btn.dataset.period)));
    });

    const runSandboxBtn = document.getElementById('runSandboxBtn');
    if (runSandboxBtn) runSandboxBtn.addEventListener('click', runSandbox);

    const closeRollModalBtn = document.getElementById('closeRollModalBtn');
    if (closeRollModalBtn) closeRollModalBtn.addEventListener('click', closeRollModal);

    const copilotToggle = document.getElementById('copilotToggle');
    if (copilotToggle) copilotToggle.addEventListener('click', toggleCopilotChat);
    const closeCopilotBtn = document.getElementById('closeCopilotBtn');
    if (closeCopilotBtn) closeCopilotBtn.addEventListener('click', toggleCopilotChat);

    const copilotForm = document.getElementById('copilotForm');
    if (copilotForm) {
        copilotForm.addEventListener('submit', (e) => {
            e.preventDefault();
            sendCopilotMessage(e);
        });
    }

    const tradesCurrency = document.getElementById('tradesCurrency');
    if (tradesCurrency) tradesCurrency.addEventListener('change', loadWindAnalysis);
    const tradesDays = document.getElementById('tradesDays');
    if (tradesDays) tradesDays.addEventListener('change', loadWindAnalysis);

    // AI 配置事件监听
    const aiSettingsBtn = document.getElementById('aiSettingsBtn');
    if (aiSettingsBtn) aiSettingsBtn.addEventListener('click', openAiSettings);
    const closeAiSettings = document.getElementById('closeAiSettings');
    if (closeAiSettings) closeAiSettings.addEventListener('click', closeAiSettingsModal);
    const saveAiSettings = document.getElementById('saveAiSettings');
    if (saveAiSettings) saveAiSettings.addEventListener('click', saveAiConfig);
    const clearAiSettings = document.getElementById('clearAiSettings');
    if (clearAiSettings) clearAiSettings.addEventListener('click', clearAiConfig);
    const aiSettingsModal = document.getElementById('aiSettingsModal');
    if (aiSettingsModal) {
        aiSettingsModal.addEventListener('click', (e) => {
            if (e.target === aiSettingsModal) closeAiSettingsModal();
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

let _refreshInFlight = false;

async function setAutoRefresh(minutes) {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
    }

    if (minutes > 0) {
        autoRefreshInterval = setInterval(async () => {
            if (_refreshInFlight) return;
            if (navigator.onLine) {
                _refreshInFlight = true;
                try {
                    const currency = document.getElementById('currencySelect')?.value || 'BTC';
                    await loadLatestData();
                    await refreshAndLoadDvol(currency);
                    await refreshAndLoadTrades(currency);
                } catch (e) {
                    console.warn('Auto refresh failed:', e);
                } finally {
                    _refreshInFlight = false;
                }
            } else {
                showAlert('网络断开，跳过自动刷新', 'warning');
            }
        }, minutes * 60 * 1000);

        showAlert(`已设置 ${minutes} 分钟自动刷新`, 'info');
    }
}

function initCharts() {
    // DVOL图表
    const dvolEl = document.getElementById('dvolChart');
    if (!dvolEl) return;
    const dvolCtx = dvolEl.getContext('2d');
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
    if (chartType === 'dvol') loadDvolChartData();
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
            await loadLatestData(true, true);
            // 扫描完成后刷新图表
            const currency = document.getElementById('currencySelect')?.value || 'BTC';
            loadWindAnalysis().catch(() => {});
            window.loadTermStructure().catch(() => {});
            window.loadMaxPain().catch(() => {});
            loadPcrChart(currency, chartPeriods.pcr || 168).catch(() => {});
            loadDvolChartData().catch(() => {});
            loadRiskDashboard(currency).catch(() => {});
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
    const gridBtn = document.getElementById('modeGridBtn');
    const rollFields = document.getElementById('scRollFields');
    const newFields = document.getElementById('scNewFields');
    const gridFields = document.getElementById('scGridFields');
    const optionTypeSelect = document.getElementById('scOptionType');

    rollBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-gray-700/50 border border-gray-600 text-gray-400 hover:bg-gray-600/50';
    newBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-gray-700/50 border border-gray-600 text-gray-400 hover:bg-gray-600/50';
    gridBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-gray-700/50 border border-gray-600 text-gray-400 hover:bg-gray-600/50';

    rollFields.classList.add('hidden');
    newFields.classList.add('hidden');
    gridFields.classList.add('hidden');
    optionTypeSelect.disabled = false;

    if (mode === 'roll') {
        rollBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-orange-500/20 border border-orange-500/50 text-orange-400';
        rollFields.classList.remove('hidden');
    } else if (mode === 'new') {
        newBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-blue-500/20 border border-blue-500/50 text-blue-400';
        newFields.classList.remove('hidden');
    } else if (mode === 'grid') {
        gridBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-purple-500/20 border border-purple-500/50 text-purple-400';
        gridFields.classList.remove('hidden');
        optionTypeSelect.disabled = true;
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
    } else if (currentCalcMode === 'new') {
        params.target_apr = parseFloat(document.getElementById('scTargetApr').value) || 200;
    } else if (currentCalcMode === 'grid') {
        params.put_count = parseInt(document.getElementById('scGridPutCount').value) || 5;
        params.call_count = parseInt(document.getElementById('scGridCallCount').value) || 0;
        params.min_apr = parseFloat(document.getElementById('scGridMinApr').value) || 8;
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
    if (result.mode === 'grid') {
        displayGridResult(result, wrapper);
        return;
    }

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
                    <th class="text-right py-2 px-2 font-medium">胜率</th>
                    ${result.mode === 'roll' ? '<th class="text-right py-2 px-2 font-medium">数量</th><th class="text-right py-2 px-2 font-medium">净流入</th>' : '<th class="text-right py-2 px-2 font-medium">保证金</th><th class="text-right py-2 px-2 font-medium">权利金</th>'}
                    <th class="text-right py-2 px-2 font-medium">ROI%</th>
                    <th class="text-right py-2 px-2 font-medium">评分</th>
                </tr>
            </thead>
            <tbody class="divide-y divide-gray-800/30">`;

    plans.forEach((plan, idx) => {
        const isBest = idx === 0;
        const metrics = plan.metrics || {};
        html += `<tr class="hover:bg-white/5 transition ${isBest ? 'bg-green-500/10' : ''}">
            <td class="py-3 px-2">${isBest ? '<i class="fas fa-crown text-yellow-400"></i>' : idx + 1}</td>
            <td class="py-3 px-2"><span class="font-mono text-white">${safeHTML(plan.symbol)}</span><br><span class="text-[10px] text-gray-500">${safeHTML(plan.platform)}</span></td>
            <td class="py-3 px-2 text-right font-mono text-orange-400">${plan.strike?.toLocaleString()}</td>
            <td class="py-3 px-2 text-center">${plan.dte}</td>
            <td class="py-3 px-2 text-right">${metrics.delta?.toFixed(3)}</td>
            <td class="py-3 px-2 text-right text-cyan-400">${metrics.win_rate?.toFixed(1)}%</td>
            ${result.mode === 'roll' ? `<td class="py-3 px-2 text-right">${metrics.new_qty}</td><td class="py-3 px-2 text-right text-cyan-400">$${metrics.net_credit?.toFixed(2)}</td>` : `<td class="py-3 px-2 text-right">$${metrics.margin_required?.toFixed(2)}</td><td class="py-3 px-2 text-right text-green-400">$${metrics.gross_credit?.toFixed(2)}</td>`}
            <td class="py-3 px-2 text-right text-yellow-400 font-bold">${metrics.roi?.toFixed(1)}%</td>
            <td class="py-3 px-2 text-right">${plan.score?.toFixed(4)}</td>
        </tr>`;
    });

    html += '</tbody></table></div>';
    html += `<div class="mt-3 text-xs text-gray-500">扫描了 ${result.meta?.total_contracts_scanned || 0} 个合约，找到 ${result.meta?.plans_found || 0} 个方案</div>`;
    wrapper.innerHTML = html;
}

function displayGridResult(result, wrapper) {
    const putLevels = result.put_levels || [];
    const callLevels = result.call_levels || [];

    let html = '<div class="space-y-6">';
    
    // DVOL 信号
    html += `<div class="bg-gray-800/50 p-3 rounded-xl border border-gray-700">
        <h4 class="text-xs font-semibold text-purple-400 mb-2"><i class="fas fa-chart-area mr-1"></i>波动率信号</h4>
        <div class="flex gap-4 text-xs">
            <span class="text-gray-400">信号: <span class="text-white font-bold">${safeHTML(result.dvol_signal || 'N/A')}</span></span>
            <span class="text-gray-400">推荐比例: <span class="text-white">${safeHTML(result.recommended_ratio || 'N/A')}</span></span>
            <span class="text-gray-400">总潜在权利金: <span class="text-green-400 font-bold">$${result.total_potential_premium?.toFixed(2) || '0'}</span></span>
        </div>
    </div>`;

    if (putLevels.length > 0) {
        html += `<h4 class="text-sm font-semibold text-green-400"><i class="fas fa-arrow-down mr-1"></i>Put 网格档位 (${putLevels.length} 个)</h4>`;
        html += '<div class="overflow-x-auto"><table class="w-full text-xs">';
        html += '<thead class="bg-gray-800/80"><tr class="text-gray-400 border-b border-gray-700/50">';
        html += '<th class="text-left py-2 px-2 font-medium">等级</th><th class="text-right py-2 px-2 font-medium">Strike</th>';
        html += '<th class="text-center py-2 px-2 font-medium">DTE</th><th class="text-right py-2 px-2 font-medium">权利金</th>';
        html += '<th class="text-right py-2 px-2 font-medium">胜率</th><th class="text-right py-2 px-2 font-medium">APR</th>';
        html += '<th class="text-right py-2 px-2 font-medium">Theta</th><th class="text-right py-2 px-2 font-medium">评分</th>';
        html += '<th class="text-left py-2 px-2 font-medium">原因</th></tr></thead><tbody class="divide-y divide-gray-800/30">';

        putLevels.forEach((level) => {
            const metrics = level.metrics || {};
            const extra = level.extra || {};
            const score = level.score || 0;
            const recLevel = metrics.recommendation_level || 'OK';
            const colorMap = { 'BEST': 'text-green-400', 'GOOD': 'text-blue-400', 'OK': 'text-yellow-400', 'CAUTION': 'text-orange-400', 'SKIP': 'text-red-400' };
            const color = colorMap[recLevel] || 'text-gray-400';
            
            html += `<tr class="hover:bg-white/5 transition">
                <td class="py-2 px-2"><span class="${color} font-bold">${safeHTML(recLevel)}</span></td>
                <td class="py-2 px-2 text-right font-mono text-orange-400">${level.strike?.toLocaleString()}</td>
                <td class="py-2 px-2 text-center">${level.dte}</td>
                <td class="py-2 px-2 text-right text-green-400">$${level.premium_usd?.toFixed(2)}</td>
                <td class="py-2 px-2 text-right text-cyan-400">${metrics.win_rate?.toFixed(1)}%</td>
                <td class="py-2 px-2 text-right text-yellow-400">${metrics.apr?.toFixed(1)}%</td>
                <td class="py-2 px-2 text-right text-pink-400">${metrics.theta_decay?.toFixed(2) || 'N/A'}</td>
                <td class="py-2 px-2 text-right">${score?.toFixed(4)}</td>
                <td class="py-2 px-2 text-gray-400">${safeHTML(metrics.reason || '')}</td>
            </tr>`;
        });

        html += '</tbody></table></div>';
    }

    if (callLevels.length > 0) {
        html += `<h4 class="text-sm font-semibold text-red-400 mt-4"><i class="fas fa-arrow-up mr-1"></i>Call 网格档位 (${callLevels.length} 个)</h4>`;
        html += '<div class="overflow-x-auto"><table class="w-full text-xs">';
        html += '<thead class="bg-gray-800/80"><tr class="text-gray-400 border-b border-gray-700/50">';
        html += '<th class="text-left py-2 px-2 font-medium">等级</th><th class="text-right py-2 px-2 font-medium">Strike</th>';
        html += '<th class="text-center py-2 px-2 font-medium">DTE</th><th class="text-right py-2 px-2 font-medium">权利金</th>';
        html += '<th class="text-right py-2 px-2 font-medium">胜率</th><th class="text-right py-2 px-2 font-medium">APR</th>';
        html += '<th class="text-right py-2 px-2 font-medium">Theta</th><th class="text-right py-2 px-2 font-medium">评分</th>';
        html += '<th class="text-left py-2 px-2 font-medium">原因</th></tr></thead><tbody class="divide-y divide-gray-800/30">';

        callLevels.forEach((level) => {
            const metrics = level.metrics || {};
            const score = level.score || 0;
            const recLevel = metrics.recommendation_level || 'OK';
            const colorMap = { 'BEST': 'text-green-400', 'GOOD': 'text-blue-400', 'OK': 'text-yellow-400', 'CAUTION': 'text-orange-400', 'SKIP': 'text-red-400' };
            const color = colorMap[recLevel] || 'text-gray-400';
            
            html += `<tr class="hover:bg-white/5 transition">
                <td class="py-2 px-2"><span class="${color} font-bold">${safeHTML(recLevel)}</span></td>
                <td class="py-2 px-2 text-right font-mono text-orange-400">${level.strike?.toLocaleString()}</td>
                <td class="py-2 px-2 text-center">${level.dte}</td>
                <td class="py-2 px-2 text-right text-green-400">$${level.premium_usd?.toFixed(2)}</td>
                <td class="py-2 px-2 text-right text-cyan-400">${metrics.win_rate?.toFixed(1)}%</td>
                <td class="py-2 px-2 text-right text-yellow-400">${metrics.apr?.toFixed(1)}%</td>
                <td class="py-2 px-2 text-right text-pink-400">${metrics.theta_decay?.toFixed(2) || 'N/A'}</td>
                <td class="py-2 px-2 text-right">${score?.toFixed(4)}</td>
                <td class="py-2 px-2 text-gray-400">${safeHTML(metrics.reason || '')}</td>
            </tr>`;
        });

        html += '</tbody></table></div>';
    }

    if (putLevels.length === 0 && callLevels.length === 0) {
        html += '<div class="text-center py-12 text-yellow-400"><i class="fas fa-search text-3xl mb-2 opacity-50"></i><p>未找到符合条件的网格配置</p></div>';
    }

    html += '</div>';
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
                <div class="text-center"><div class="text-xs text-gray-400 mb-1">推荐合约</div><div class="font-mono font-semibold text-white">${safeHTML(recommended.symbol)}</div><div class="text-xs text-gray-500">${safeHTML(recommended.platform)}</div></div>
                <div class="text-center"><div class="text-xs text-gray-400 mb-1">卖出张数</div><div class="text-2xl font-bold text-orange-400">${recommended.num_contracts} 张</div></div>
                <div class="text-center"><div class="text-xs text-gray-400 mb-1">所需保证金</div><div class="text-xl font-semibold text-white">$${recommended.total_margin.toLocaleString()}</div></div>
                <div class="text-center"><div class="text-xs text-gray-400 mb-1">预期净利润</div><div class="text-xl font-bold text-green-400">+$${recommended.net_profit.toLocaleString()}</div><div class="text-xs ${riskColor}">${safeHTML(recommended.risk_level)}</div></div>
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
                <td class="py-2 px-2 font-mono text-xs">${safeHTML(plan.symbol)}</td>
                <td class="py-2 px-2 text-center">${plan.dte.toFixed(0)}</td>
                <td class="py-2 px-2 text-right font-mono">${Math.round(plan.strike).toLocaleString()}</td>
                <td class="py-2 px-2 text-right font-mono text-green-400">${plan.apr.toFixed(1)}%</td>
                <td class="py-2 px-2 text-right font-mono font-semibold">${plan.num_contracts}</td>
                <td class="py-2 px-2 text-right font-mono">$${plan.total_margin.toLocaleString()}</td>
                <td class="py-2 px-2 text-right font-mono text-blue-400">$${plan.expected_premium.toLocaleString()}</td>
                <td class="py-2 px-2 text-right font-mono ${profitColor} font-semibold">${plan.net_profit >= 0 ? '+' : ''}$${plan.net_profit.toLocaleString()}</td>
                <td class="py-2 px-2 text-center ${riskColor} text-xs">${safeHTML(plan.risk_level)}</td>
            </tr>`;
        }).join('');
    } else {
        tableBody.innerHTML = '<tr><td colspan="10" class="text-center py-4 text-gray-500">无可用方案</td></tr>';
    }
}

async function loadLatestData(showSuccess = true, skipCharts = false) {
    try {
        if (!navigator.onLine) {
            showAlert('网络连接已断开，刷新失败', 'error');
            return;
        }
        
        const currency = document.getElementById('currencySelect').value;
        
        // 渐进式加载：先获取宏观数据（快速），再获取合约数据（慢速）
        const [macroRes, fullRes] = await Promise.all([
            safeFetch(`${API_BASE}/api/macro?currency=${currency}`),
            safeFetch(`${API_BASE}/api/latest?currency=${currency}`)
        ]);
        
        // 1. 先更新宏观指标（秒开）
        const macroData = await macroRes.json();
        if (macroData.success && macroData.spot_price) {
            currentSpotPrice = macroData.spot_price;
            updateMacroIndicators(macroData);
            updateLastUpdateTime(macroData.timestamp);
            
            // 合约计数先显示
            const countEl = document.getElementById('contractCount');
            if (countEl && macroData.contracts_count !== undefined) {
                countEl.textContent = `${macroData.contracts_count} 个合约`;
            }
        }
        
        // 2. 合约数据加载完成后再更新表格
        const data = await fullRes.json();
        currentData = data;
        if (data.spot_price) currentSpotPrice = data.spot_price;

        // 仅在合约数据真正变更时才重新渲染表格（避免排序状态丢失）
        if (!data._contracts_same) {
            updateOpportunitiesTable(data.contracts || []);
        }
        
        updateLargeTrades(data.large_trades_details || [], data.large_trades_count || 0);
        if (!macroData.timestamp) updateLastUpdateTime(data.timestamp);

        if (showSuccess) showAlert('数据刷新成功', 'success');

        if (data.dvol_interpretation || data.dvol_trend_label) {
            showDvolAdvice(data.currency || 'BTC');
        }

        // 仅在非初始化加载时才更新图表（避免重复请求）
        if (!skipCharts) {
            loadDvolChartData();
            loadPcrChart(currency, chartPeriods.pcr || 168);
            loadRiskDashboard(currency);
        }
    } catch (error) {
        console.error('加载数据失败:', error);
        showAlert(`数据刷新失败: ${error.message}`, 'error');
        
        if (!loadLatestData._retrying) {
            loadLatestData._retrying = true;
            setTimeout(() => {
                if (navigator.onLine) {
                    loadLatestData(false);
                }
                loadLatestData._retrying = false;
            }, 5000);
        }
    }
}

async function loadDashboardInit() {
    // 使用聚合 API 一次性加载 Wind/TermStructure/MaxPain
    try {
        const currency = document.getElementById('currencySelect')?.value || 'BTC';
        const res = await safeFetch(`${API_BASE}/api/dashboard-init?currency=${currency}`);
        const data = await res.json();
        
        if (!data.success) {
            console.warn('Dashboard init failed:', data);
            return;
        }
        
        // 更新 Wind Analysis
        if (data.wind && !data.wind.error) {
            updateWindUI(data.wind);
        } else {
            console.warn('[DashboardInit] Wind error:', data.wind?.error);
        }
        
        // 更新 Term Structure
        if (data.term_structure && !data.term_structure.error) {
            updateTermStructureUI(data.term_structure);
        } else {
            console.warn('[DashboardInit] Term structure error:', data.term_structure?.error);
        }
        
        // 更新 Max Pain
        if (data.max_pain && !data.max_pain.error) {
            updateMaxPainUI(data.max_pain);
        } else {
            console.warn('[DashboardInit] Max pain error:', data.max_pain?.error);
        }
    } catch (e) {
        console.error('Failed to load dashboard init:', e);
    }
}

function updateWindUI(data) {
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
        if (totalNotionalEl) totalNotionalEl.textContent = data.spot ? `$${(data.spot / 1000).toFixed(0)}K` : '-';
        
        const dominantFlowEl = document.getElementById('windDominantFlow');
        if (dominantFlowEl) dominantFlowEl.textContent = data.dominant_flow || '-';
        
        const spotMarkerEl = document.getElementById('windSpotMarker');
        if (spotMarkerEl && data.spot > 0) {
            spotMarkerEl.textContent = `● 现价 $${data.spot.toLocaleString()}`;
            spotMarkerEl.classList.remove('hidden');
        }
    } else {
        summaryCard?.classList.add('hidden');
    }
}

function updateTermStructureUI(data) {
    const tsData = data.term_structure || [];
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
    
    // 更新期限结构标签
    const structLabel = document.getElementById('tsStructureLabel');
    const slopeLabel = document.getElementById('tsSlopeLabel');
    if (structLabel && slopeLabel && tsData.length >= 2) {
        const sortedTs = [...tsData].filter(t => t.avg_iv !== null && t.avg_iv > 0).sort((a, b) => a.dte - b.dte);
        if (sortedTs.length < 2) return;
        const frontIv = sortedTs[0].avg_iv;
        const backIv = sortedTs[sortedTs.length - 1].avg_iv;
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
    
    // 更新 backwardation 警报
    const bwEl = document.getElementById('backwardationAlert');
    const bwTxt = document.getElementById('bwText');
    if (bwEl && bwTxt && data.backwardation) {
        bwEl.classList.remove('hidden');
        bwTxt.textContent = '近高远低结构';
    } else if (bwEl) {
        bwEl.classList.add('hidden');
    }
}

function updateMaxPainUI(data) {
    const nearestMp = data.nearest_mp;
    const spot = data.spot;
    const mmOverview = data.mm_overview || '';
    const signal = data.signal || '';
    const firstExpiry = (data.expiries && data.expiries.length > 0) ? data.expiries[0] : null;
    
    // 更新关键数据卡片
    const mpSpot = document.getElementById('mpSpot');
    if (mpSpot && spot) {
        mpSpot.textContent = `$${spot.toLocaleString()}`;
    }
    
    // 更新 Max Pain 价格
    const mpPrice = document.getElementById('mpPrice');
    if (mpPrice && nearestMp) {
        mpPrice.textContent = `$${nearestMp.toLocaleString()}`;
    }
    
    // 更新 Gamma Flip 信息
    const mpFlip = document.getElementById('mpFlip');
    if (mpFlip && firstExpiry && firstExpiry.gamma_status && firstExpiry.gamma_status.flip_strike) {
        mpFlip.textContent = `$${firstExpiry.gamma_status.flip_strike.toLocaleString()}`;
    }
    
    // 更新距离信息
    const mpDist = document.getElementById('mpDist');
    if (mpDist && data.nearest_dist !== undefined) {
        mpDist.textContent = `${data.nearest_dist}%`;
    }
    
    // 更新 PCR 信息
    const mpPCR = document.getElementById('mpPCR');
    if (mpPCR && firstExpiry && firstExpiry.pcr !== undefined) {
        mpPCR.textContent = firstExpiry.pcr.toFixed(2);
    }
    
    // 更新信号文本
    const mpSignal = document.getElementById('mpSignal');
    if (mpSignal && signal) {
        mpSignal.textContent = signal;
    }
    
    // 更新 Gamma 状态卡片（从第一个到期日提取）
    const gammaStatus = firstExpiry ? firstExpiry.gamma_status : null;
    if (gammaStatus) {
        const statusCard = document.getElementById('gammaStatusCard');
        if (statusCard) {
            statusCard.classList.remove('hidden');
            statusCard.className = 'mb-3 p-3 rounded-lg border ' + 
                (gammaStatus.region === 'long' ? 'border-emerald-500/30 bg-emerald-500/5' :
                 gammaStatus.region === 'short' ? 'border-red-500/30 bg-red-500/5' :
                 'border-gray-500/30 bg-gray-500/5');
        }
        
        const iconEl = document.getElementById('gammaStatusIcon');
        if (iconEl && gammaStatus.icon) {
            iconEl.textContent = gammaStatus.icon;
        }
        
        const textEl = document.getElementById('gammaStatusText');
        if (textEl && gammaStatus.region_cn) {
            textEl.textContent = gammaStatus.region_cn;
            textEl.className = 'text-sm font-bold ' + 
                (gammaStatus.region === 'long' ? 'text-emerald-400' :
                 gammaStatus.region === 'short' ? 'text-red-400' : 'text-gray-400');
        }
        
        const distEl = document.getElementById('gammaDistance');
        if (distEl && gammaStatus.distance_pct !== undefined) {
            const distText = (gammaStatus.region === 'long' ? '现货高于 Flip 点 ' : '现货低于 Flip 点 ') + gammaStatus.distance_pct.toFixed(1) + '%';
            distEl.textContent = distText;
        }
        
        const volEl = document.getElementById('gammaVolatility');
        if (volEl && gammaStatus.volatility) {
            volEl.textContent = gammaStatus.volatility;
        }
        
        const instEl = document.getElementById('gammaInstitutional');
        if (instEl && gammaStatus.institutional) {
            instEl.textContent = gammaStatus.institutional;
        }
        
        // 更新区域距离卡片
        const regionDistEl = document.getElementById('mpRegionDist');
        if (regionDistEl && gammaStatus.distance_pct !== undefined) {
            regionDistEl.textContent = (gammaStatus.distance_pct > 0 ? '+' : '') + gammaStatus.distance_pct.toFixed(1) + '%';
            regionDistEl.className = 'font-mono text-xs ' + 
                (gammaStatus.distance_pct > 5 ? 'text-emerald-400' :
                 gammaStatus.distance_pct < -5 ? 'text-red-400' : 'text-gray-400');
        }
    }
    
    // 更新策略建议（从第一个到期日提取）
    const gammaAdvice = firstExpiry ? firstExpiry.gamma_advice : null;
    if (gammaAdvice) {
        const adviceCard = document.getElementById('gammaAdviceCard');
        if (adviceCard) {
            adviceCard.classList.remove('hidden');
            adviceCard.className = 'mb-3 p-2.5 rounded-lg border ' + 
                (gammaStatus && gammaStatus.region === 'long' ? 'border-emerald-500/20 bg-emerald-500/5' :
                 gammaStatus && gammaStatus.region === 'short' ? 'border-red-500/20 bg-red-500/5' :
                 'border-gray-500/20 bg-gray-500/5');
        }
        
        const adviceText = document.getElementById('gammaAdviceText');
        if (adviceText && gammaAdvice.text) {
            adviceText.textContent = gammaAdvice.text;
        }
        
        const advicePosition = document.getElementById('advicePosition');
        if (advicePosition && gammaAdvice.position_pct) {
            advicePosition.textContent = `${gammaAdvice.position_pct}%`;
        }
        
        const adviceStrategy = document.getElementById('adviceStrategy');
        if (adviceStrategy && gammaAdvice.strategy) {
            adviceStrategy.textContent = gammaAdvice.strategy;
        }
        
        const adviceDelta = document.getElementById('adviceDelta');
        if (adviceDelta && gammaAdvice.delta_range) {
            adviceDelta.textContent = gammaAdvice.delta_range;
        }
    }
    
    // 风险预警
    const mmEl = document.getElementById('mmAlert');
    if (firstExpiry && firstExpiry.mm_signal && mmEl) {
        mmEl.classList.remove('hidden');
        mmEl.className = firstExpiry.mm_signal.includes('DANGER') || firstExpiry.mm_signal.includes('危险') ? 
            'mb-3 p-2 rounded text-xs bg-red-900/40 border border-red-500/50 text-red-300' : 
            'mb-3 p-2 rounded text-xs bg-green-900/30 border border-green-500/30 text-green-300';
        mmEl.textContent = firstExpiry.mm_signal;
    } else if (mmEl) {
        mmEl.classList.add('hidden');
    }
}

function loadPageDataAsync() {
    const currency = document.getElementById('currencySelect')?.value || 'BTC';
    loadLatestData(false, true).catch(e => console.error('[loadPageDataAsync] loadLatestData failed:', e));
    loadMacroData().catch(e => console.error('[loadPageDataAsync] loadMacroData failed:', e));
    loadWindAnalysis().catch(e => console.error('[loadPageDataAsync] loadWindAnalysis failed:', e));
    window.loadTermStructure().catch(e => console.error('[loadPageDataAsync] loadTermStructure failed:', e));
    window.loadMaxPain().catch(e => console.error('[loadPageDataAsync] loadMaxPain failed:', e));
    loadPcrChart(currency, chartPeriods.pcr || 168).catch(e => console.error('[loadPageDataAsync] loadPcrChart failed:', e));
    refreshAndLoadDvol(currency).catch(e => console.error('[loadPageDataAsync] refreshAndLoadDvol failed:', e));
    refreshAndLoadTrades(currency).catch(e => console.error('[loadPageDataAsync] refreshAndLoadTrades failed:', e));
    loadRiskDashboard(currency).catch(e => console.error('[loadPageDataAsync] loadRiskDashboard failed:', e));
}

async function refreshAndLoadDvol(currency) {
    try {
        const res = await safeFetch(`${API_BASE}/api/dvol/refresh?currency=${currency}`);
        const data = await res.json();
        if (data.success && data.dvol_current) {
            updateDvolDisplay(data);
        }
    } catch (e) {
        console.warn('DVOL refresh failed, loading chart from cache:', e);
    }
    await loadDvolChartData();
}

async function refreshAndLoadTrades(currency) {
    try {
        const res = await safeFetch(`${API_BASE}/api/trades/refresh?currency=${currency}`);
        const data = await res.json();
        if (data.success) {
            updateLargeTrades(data.large_trades_details || [], data.large_trades_count || 0);
        }
    } catch (e) {
        console.warn('Trades refresh failed:', e);
    }
}

function updateDvolDisplay(data) {
    const dvolEl = document.getElementById('dvolValue');
    if (dvolEl && data.dvol_current) dvolEl.textContent = data.dvol_current.toFixed(2);

    const dvolSignal = document.getElementById('dvolSignal');
    if (dvolSignal) {
        const interp = data.dvol_interpretation || '';
        const trend = data.dvol_trend_label || data.dvol_trend || '';
        const signal = data.dvol_signal || '';
        const zScore = data.dvol_z_score;

        if (interp) {
            dvolSignal.textContent = interp;
            dvolSignal.className = trend.includes('上涨') ? 'text-xs mt-1 text-red-400 font-medium' : trend.includes('下跌') ? 'text-xs mt-1 text-green-400 font-medium' : 'text-xs mt-1 text-gray-400';
        } else if (signal) {
            dvolSignal.textContent = signal;
            dvolSignal.className = signal.includes('偏高') ? 'text-xs mt-1 text-red-400 font-medium' : signal.includes('偏低') ? 'text-xs mt-1 text-green-400 font-medium' : 'text-xs mt-1 text-gray-400';
        } else if (zScore !== null && zScore !== undefined) {
            if (zScore > 2) { dvolSignal.textContent = '异常偏高 ⚠️'; dvolSignal.className = 'text-xs mt-1 text-red-400 font-medium'; }
            else if (zScore > 1) { dvolSignal.textContent = '偏高'; dvolSignal.className = 'text-xs mt-1 text-yellow-400 font-medium'; }
            else if (zScore < -2) { dvolSignal.textContent = '异常偏低'; dvolSignal.className = 'text-xs mt-1 text-green-400 font-medium'; }
            else if (zScore < -1) { dvolSignal.textContent = '偏低'; dvolSignal.className = 'text-xs mt-1 text-blue-400 font-medium'; }
            else { dvolSignal.textContent = '正常区间'; dvolSignal.className = 'text-xs mt-1 text-gray-400'; }
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
                        <span class="font-mono font-bold text-yellow-300">${safeHTML(deltaRange)}</span>
                    </div>
                    <div class="flex items-center gap-2">
                        <i class="fas fa-clock text-purple-400"></i>
                        <span class="text-gray-400">DTE:</span>
                        <span class="font-mono font-bold text-purple-300">${safeHTML(dteRange)}</span>
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
    
    // 更新宏观数据
    loadMacroData().catch(() => {});
    
    // 更新链上核心指标（v8.1新增）
    updateOnchainMetrics(data.onchain_metrics);
    
    // 衍生品市场过热检测（v12.0新增）
    updateDerivativeMetrics(data.derivative_metrics);
    
    // 压力测试系统（v9.0新增）
    updatePressureTest(data.pressure_test);
    
    // AI 驱动的情绪分析（v9.0新增）
    updateSentimentAnalysis(data.ai_sentiment);
}

function updateOnchainMetrics(onchain) {
    console.log('[OnChain] 更新链上指标:', onchain);
    
    if (!onchain) {
        console.log('[OnChain] 警告: onchain 数据为空');
        return;
    }
    
    // ===== 汇合仪表盘 =====
    updateConvergenceDashboard(onchain.convergence_score);
    
    // ===== MVRV Ratio =====
    setMetricValue('onchainMVRV', onchain.mvrv_ratio, 2);
    setMetricText('onchainMVRVSignal', onchain.mvrv_signal || '--');
    if (onchain.mvrv_ratio !== null && onchain.mvrv_ratio !== undefined) {
        const mvrvEl = document.getElementById('onchainMVRV');
        const sigEl = document.getElementById('onchainMVRVSignal');
        if (mvrvEl) mvrvEl.className = 'text-xl font-bold ' + (onchain.mvrv_ratio < 1 ? 'text-green-400' : onchain.mvrv_ratio < 3.5 ? 'text-yellow-400' : 'text-red-400');
        if (sigEl) sigEl.className = 'text-xs mt-1 ' + (onchain.mvrv_ratio < 1 ? 'text-green-500' : onchain.mvrv_ratio < 3.5 ? 'text-yellow-500' : 'text-red-500');
    }
    
    // ===== MVRV Z-Score =====
    const zscore = onchain.mvrv_z_score;
    const zzone = onchain.mvrv_z_zone;
    const zzoneName = onchain.mvrv_z_zone_name;
    const zExtremes = onchain.mvrv_z_extremes || {};
    
    setMetricValue('onchainMVRVZScore', zscore, 2);
    setMetricText('onchainMVRVZSignal', zzoneName || onchain.mvrv_z_signal || '--');
    
    if (zscore !== null && zscore !== undefined) {
        const zEl = document.getElementById('onchainMVRVZScore');
        const sigEl = document.getElementById('onchainMVRVZSignal');
        
        // Bitcoin Magazine Pro 颜色带区标准
        let zColor, sigColor;
        if (zzone === 'green') {
            zColor = 'text-green-400';
            sigColor = 'text-green-500';
        } else if (zzone === 'pink') {
            zColor = 'text-pink-400';
            sigColor = 'text-pink-500';
        } else {
            zColor = 'text-yellow-400';
            sigColor = 'text-yellow-500';
        }
        
        if (zEl) zEl.className = 'text-xl font-bold ' + zColor;
        if (sigEl) sigEl.className = 'text-xs mt-1 ' + sigColor;
    }
    
    // Z-Score 历史极值提示
    if (zExtremes && zExtremes.min_z !== undefined) {
        const histEl = document.getElementById('onchainMVRVZHistory');
        if (histEl) {
            const histText = `历史极值: ${zExtremes.min_z} ~ ${zExtremes.max_z} | 百分位: ${zExtremes.current_percentile}%`;
            histEl.textContent = histText;
            histEl.className = 'text-[10px] text-gray-600 mt-1';
        }
    }
    
    // ===== NUPL =====
    if (onchain.nupl !== null && onchain.nupl !== undefined) {
        setMetricValue('onchainNUPL', onchain.nupl, 3);
        setMetricText('onchainNUPLSignal', onchain.nupl_signal || '--');
        const nuplEl = document.getElementById('onchainNUPL');
        const sigEl = document.getElementById('onchainNUPLSignal');
        if (nuplEl) nuplEl.className = 'text-xl font-bold ' + (onchain.nupl < 0 ? 'text-red-400' : onchain.nupl < 0.25 ? 'text-yellow-400' : onchain.nupl < 0.75 ? 'text-yellow-300' : 'text-green-400');
        if (sigEl) sigEl.className = 'text-xs mt-1 ' + (onchain.nupl < 0 ? 'text-red-500' : onchain.nupl < 0.25 ? 'text-yellow-500' : onchain.nupl < 0.75 ? 'text-yellow-300' : 'text-green-500');
    }
    
    // ===== Mayer Multiple =====
    setMetricValue('onchainMayer', onchain.mayer_multiple, 2);
    setMetricText('onchainMayerSignal', onchain.mayer_signal || '--');
    if (onchain.mayer_multiple !== null && onchain.mayer_multiple !== undefined) {
        const mEl = document.getElementById('onchainMayer');
        const sigEl = document.getElementById('onchainMayerSignal');
        if (mEl) mEl.className = 'text-xl font-bold ' + (onchain.mayer_multiple < 1 ? 'text-green-400' : onchain.mayer_multiple < 2.4 ? 'text-yellow-400' : 'text-red-400');
        if (sigEl) sigEl.className = 'text-xs mt-1 ' + (onchain.mayer_multiple < 1 ? 'text-green-500' : onchain.mayer_multiple < 2.4 ? 'text-yellow-500' : 'text-red-500');
    }
    
    // ===== 200WMA =====
    setMetricValue('onchain200WMA', onchain.price_200wma, 0, '$');
    setMetricText('onchain200WMARatio', onchain.price_to_200wma_ratio ? `当前价格 / 200WMA = ${onchain.price_to_200wma_ratio.toFixed(2)}x` : '--');
    
    // ===== Balanced Price =====
    setMetricValue('onchainBalancedPrice', onchain.balanced_price, 0, '$');
    setMetricText('onchainBalancedPriceRatio', onchain.balanced_price_ratio ? `当前价格 / BP = ${onchain.balanced_price_ratio.toFixed(2)}x` : '--');
    
    // ===== 200DMA =====
    setMetricValue('onchain200DMA', onchain.price_200dma, 0, '$');
    
    // ===== 减半倒计时 =====
    if (onchain.halving_days_remaining !== null && onchain.halving_days_remaining !== undefined) {
        setMetricText('onchainHalvingDays', `${onchain.halving_days_remaining} 天`);
    }
    
    // ===== Puell Multiple =====
    setMetricValue('onchainPuell', onchain.puell_multiple, 2);
    setMetricText('onchainPuellSignal', onchain.puell_signal || '--');
    if (onchain.puell_multiple !== null && onchain.puell_multiple !== undefined) {
        const pEl = document.getElementById('onchainPuell');
        const sigEl = document.getElementById('onchainPuellSignal');
        if (pEl) pEl.className = 'text-xl font-bold ' + (onchain.puell_multiple < 0.4 ? 'text-green-400' : onchain.puell_multiple < 2.0 ? 'text-yellow-400' : 'text-red-400');
        if (sigEl) sigEl.className = 'text-xs mt-1 ' + (onchain.puell_multiple < 0.4 ? 'text-green-500' : onchain.puell_multiple < 2.0 ? 'text-yellow-500' : 'text-red-500');
    }
}

function updateConvergenceDashboard(convergence) {
    const badgeEl = document.getElementById('convergenceScoreBadge');
    const scoreEl = document.getElementById('convergenceScoreValue');
    const probEl = document.getElementById('convergenceBottomProb');
    const activeEl = document.getElementById('convergenceActiveCount');
    const signalsEl = document.getElementById('convergenceSignals');
    
    if (!convergence || !badgeEl || !scoreEl || !probEl || !activeEl || !signalsEl) {
        return;
    }
    
    // 设置徽章
    badgeEl.textContent = convergence.icon + ' ' + convergence.name;
    badgeEl.className = `px-3 py-1 rounded-full text-xs font-bold ${convergence.color || 'text-gray-400'} ${getConvergenceBg(convergence.level)}`;
    
    // 设置分数
    scoreEl.textContent = convergence.score > 0 ? `+${convergence.score}` : `${convergence.score}`;
    scoreEl.className = 'text-2xl font-bold ' + (convergence.color || 'text-gray-400');
    
    // 设置底部概率
    probEl.textContent = convergence.bottom_probability || '--';
    probEl.className = 'text-sm font-bold ' + (convergence.color || 'text-gray-400');
    
    // 设置激活指标
    activeEl.textContent = `${convergence.active_indicators || 0} / 7`;
    
    // 设置信号列表
    if (convergence.signals && convergence.signals.length > 0) {
        signalsEl.innerHTML = convergence.signals.map(([icon, name, type]) => {
            const colors = {
                'bottom': 'text-green-400 bg-green-500/10 border-green-500/30',
                'top': 'text-red-400 bg-red-500/10 border-red-500/30',
                'neutral': 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30'
            };
            const c = colors[type] || colors.neutral;
            return `<div class="p-2 rounded border ${c}">${safeHTML(icon)} ${safeHTML(name)}</div>`;
        }).join('');
    } else {
        signalsEl.innerHTML = '<div class="text-gray-600 text-center py-2 col-span-4">无信号</div>';
    }
}

function getConvergenceBg(level) {
    const bgMap = {
        'STRONG_BOTTOM': 'bg-red-500/20',
        'BOTTOM': 'bg-yellow-500/20',
        'ACCUMULATION': 'bg-green-500/20',
        'NEUTRAL': 'bg-gray-500/20',
        'DISTRIBUTION': 'bg-orange-500/20',
        'TOP': 'bg-red-500/20'
    };
    return bgMap[level] || 'bg-gray-500/20';
}

function setMetricValue(elementId, value, decimals = null, prefix = '') {
    const el = document.getElementById(elementId);
    if (!el) return;
    if (value === null || value === undefined) {
        el.textContent = '--';
    } else if (decimals !== null) {
        el.textContent = prefix + Number(value).toFixed(decimals);
    } else {
        el.textContent = prefix + value;
    }
}

function setMetricText(elementId, text) {
    const el = document.getElementById(elementId);
    if (!el) return;
    el.textContent = text || '--';
}

function updateDerivativeMetrics(dm) {
    if (!dm || dm.error) {
        console.log('[Derivative] 警告: 数据为空');
        return;
    }
    
    // Sharpe 7d
    setMetricValue('derivSharpe7d', dm.sharpe_ratio_7d, 2);
    setMetricText('derivSharpe7dSignal', dm.sharpe_signal_7d || '--');
    if (dm.sharpe_ratio_7d !== null && dm.sharpe_ratio_7d !== undefined) {
        const el = document.getElementById('derivSharpe7d');
        if (el) el.className = 'text-xl font-bold ' + (dm.sharpe_ratio_7d < -1 ? 'text-green-400' : dm.sharpe_ratio_7d < 0 ? 'text-yellow-400' : dm.sharpe_ratio_7d < 2 ? 'text-cyan-400' : 'text-red-400');
    }
    
    // Sharpe 30d
    setMetricValue('derivSharpe30d', dm.sharpe_ratio_30d, 2);
    setMetricText('derivSharpe30dSignal', dm.sharpe_signal_30d || '--');
    if (dm.sharpe_ratio_30d !== null && dm.sharpe_ratio_30d !== undefined) {
        const el = document.getElementById('derivSharpe30d');
        if (el) el.className = 'text-xl font-bold ' + (dm.sharpe_ratio_30d < -1 ? 'text-green-400' : dm.sharpe_ratio_30d < 0 ? 'text-yellow-400' : dm.sharpe_ratio_30d < 2 ? 'text-teal-400' : 'text-red-400');
    }
    
    // 资金费率
    if (dm.funding_rate !== null && dm.funding_rate !== undefined) {
        const pct = dm.funding_rate * 100;
        setMetricText('derivFundingRate', pct.toFixed(3) + '%');
        setMetricText('derivFundingSignal', dm.funding_signal || '--');
        const el = document.getElementById('derivFundingRate');
        if (el) el.className = 'text-xl font-bold ' + (pct > 0.1 ? 'text-red-400' : pct > 0.05 ? 'text-orange-400' : pct < -0.05 ? 'text-green-400' : 'text-rose-400');
    }
    
    // 期货/现货比率
    setMetricValue('derivVolRatio', dm.futures_spot_ratio, 2);
    setMetricText('derivVolRatioSignal', dm.futures_spot_signal || '--');
    if (dm.futures_spot_ratio !== null && dm.futures_spot_ratio !== undefined) {
        const el = document.getElementById('derivVolRatio');
        if (el) el.className = 'text-xl font-bold ' + (dm.futures_spot_ratio > 3 ? 'text-red-400' : dm.futures_spot_ratio > 1.5 ? 'text-yellow-400' : 'text-violet-400');
    }
    
    // 综合评估
    const oa = dm.overheating_assessment;
    if (oa) {
        const badge = document.getElementById('derivOverheatBadge');
        const advice = document.getElementById('derivOverheatAdvice');
        const signals = document.getElementById('derivOverheatSignals');
        
        if (badge) {
            badge.textContent = oa.icon + ' ' + oa.name;
            badge.className = 'px-3 py-1 rounded-full text-xs font-bold ' + (oa.color || 'text-gray-400') + ' ' + getDerivBg(oa.level);
        }
        if (advice) advice.textContent = oa.advice || '--';
        
        if (signals && oa.signals && oa.signals.length > 0) {
            const colors = {
                'bottom': 'text-green-400 bg-green-500/10 border-green-500/30',
                'top': 'text-red-400 bg-red-500/10 border-red-500/30',
                'neutral': 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30'
            };
            signals.innerHTML = oa.signals.map(([icon, name, type]) => {
                const c = colors[type] || colors.neutral;
                return '<div class="p-2 rounded border ' + c + '">' + safeHTML(icon) + ' ' + safeHTML(name) + '</div>';
            }).join('');
        }
    }
}

function getDerivBg(level) {
    const bgMap = {
        'STRONG_BOTTOM': 'bg-red-500/20',
        'BOTTOM': 'bg-yellow-500/20',
        'NEUTRAL': 'bg-gray-500/20',
        'OVERHEATED': 'bg-orange-500/20',
        'EXTREME_OVERHEAT': 'bg-red-500/20'
    };
    return bgMap[level] || 'bg-gray-500/20';
}

function updatePressureTest(pt) {
    if (!pt || pt.error) {
        return;
    }
    
    const levelEl = document.getElementById('ptRiskLevel');
    const descEl = document.getElementById('ptRiskDesc');
    if (levelEl && pt.risk_assessment) {
        const ra = pt.risk_assessment;
        levelEl.textContent = ra.level === 'HIGH' ? '⚠️ 高风险' : ra.level === 'MEDIUM' ? '⚡ 中风险' : '✅ 低风险';
        levelEl.className = 'text-lg font-bold ' + (ra.level === 'HIGH' ? 'text-red-400' : ra.level === 'MEDIUM' ? 'text-yellow-400' : 'text-green-400');
        if (descEl) descEl.textContent = ra.description;
    }
    
    const vannaEl = document.getElementById('ptVannaRisk');
    const volgaEl = document.getElementById('ptVolgaRisk');
    const maxDeltaEl = document.getElementById('ptMaxDelta');
    if (vannaEl && pt.risk_assessment) {
        vannaEl.textContent = pt.risk_assessment.vanna_risk ? '⚠️ 较高' : '✅ 可控';
        vannaEl.className = 'text-lg font-bold ' + (pt.risk_assessment.vanna_risk ? 'text-red-400' : 'text-green-400');
    }
    if (volgaEl && pt.risk_assessment) {
        volgaEl.textContent = pt.risk_assessment.volga_risk ? '⚠️ 较高' : '✅ 可控';
        volgaEl.className = 'text-lg font-bold ' + (pt.risk_assessment.volga_risk ? 'text-orange-400' : 'text-green-400');
    }
    if (maxDeltaEl && pt.risk_assessment) {
        maxDeltaEl.textContent = pt.risk_assessment.max_delta_exposure.toFixed(4);
    }
    
    const tbody = document.getElementById('ptJointScenarios');
    if (tbody && pt.joint_stress_tests) {
        tbody.innerHTML = pt.joint_stress_tests.map(s => {
            const deltaColor = Math.abs(s.delta) > 0.5 ? 'text-red-400' : Math.abs(s.delta) > 0.3 ? 'text-yellow-400' : 'text-green-400';
            return `<tr class="hover:bg-gray-800/30">
                <td class="py-1 px-2 text-left text-gray-300">${safeHTML(s.scenario)}</td>
                <td class="py-1 px-2 font-mono">$${s.price.toLocaleString()}</td>
                <td class="py-1 px-2 font-mono">${s.volatility.toFixed(1)}%</td>
                <td class="py-1 px-2 font-mono ${deltaColor}">${s.delta.toFixed(4)}</td>
                <td class="py-1 px-2 font-mono text-gray-400">${s.gamma.toFixed(6)}</td>
                <td class="py-1 px-2 font-mono text-gray-400">${s.vanna.toFixed(6)}</td>
                <td class="py-1 px-2 font-mono text-gray-400">${s.volga.toFixed(4)}</td>
            </tr>`;
        }).join('');
    }
}

function updateSentimentAnalysis(sa) {
    if (!sa || sa.error) {
        return;
    }
    
    const iconEl = document.getElementById('aiSentimentIcon');
    const intentEl = document.getElementById('aiDominantIntent');
    const confEl = document.getElementById('aiConfidence');
    const recEl = document.getElementById('aiRecommendation');
    const riskLevelEl = document.getElementById('aiRiskLevel');
    const signalStrengthEl = document.getElementById('aiSignalStrength');
    
    if (iconEl) iconEl.textContent = sa.sentiment_icon || '➖';
    if (intentEl) {
        intentEl.textContent = sa.overall_sentiment || '--';
        intentEl.className = 'text-xl font-bold ' + (sa.dominant_intent?.intent_color || 'text-gray-400');
    }
    if (confEl) confEl.textContent = sa.confidence ? `${sa.confidence}%` : '--';
    if (recEl) recEl.innerHTML = sa.ai_recommendation ? safeHTML(sa.ai_recommendation) : '等待分析...';
    
    // v2.0: 风险等级
    if (riskLevelEl && sa.dominant_intent?.risk_level) {
        const rl = sa.dominant_intent.risk_level;
        riskLevelEl.textContent = `风险等级: ${rl === 'HIGH' ? '🔴 高风险' : rl === 'MEDIUM' ? '🟡 中风险' : '🟢 低风险'}`;
        riskLevelEl.className = 'text-xs mt-1 ' + (rl === 'HIGH' ? 'text-red-400' : rl === 'MEDIUM' ? 'text-yellow-400' : 'text-green-400');
    }
    
    // v2.0: 信号强度
    if (signalStrengthEl) {
        // 信号强度从 confidence 推断
        const conf = sa.confidence || 0;
        if (conf > 75) {
            signalStrengthEl.textContent = '🔥 STRONG';
            signalStrengthEl.className = 'text-xs font-bold text-red-400';
        } else if (conf > 50) {
            signalStrengthEl.textContent = '⚡ MEDIUM';
            signalStrengthEl.className = 'text-xs font-bold text-yellow-400';
        } else {
            signalStrengthEl.textContent = '💤 WEAK';
            signalStrengthEl.className = 'text-xs font-bold text-gray-500';
        }
    }
    
    if (sa.put_call_ratio) {
        const putPctEl = document.getElementById('aiPutPct');
        const callPctEl = document.getElementById('aiCallPct');
        if (putPctEl) putPctEl.textContent = `${sa.put_call_ratio.put_pct.toFixed(1)}%`;
        if (callPctEl) callPctEl.textContent = `${sa.put_call_ratio.call_pct.toFixed(1)}%`;
    }
    
    // v2.0: 市场流摘要
    const summaryEl = document.getElementById('aiMarketFlowSummary');
    if (summaryEl && sa.market_flow_summary) {
        const mfs = sa.market_flow_summary;
        if (mfs.total_trades > 0) {
            summaryEl.classList.remove('hidden');
            const totalPremiumM = (mfs.total_premium / 1e6).toFixed(1);
            summaryEl.innerHTML = `📊 分析 <b>${mfs.total_trades}</b> 笔交易 | 总名义价值 <b>$${totalPremiumM}M</b> | 机构占比 <b>${mfs.institutional_ratio || 0}%</b> | 平均单量 <b>$${(mfs.avg_trade_size / 1e3).toFixed(0)}K</b>`;
        }
    }
    
    const distEl = document.getElementById('aiIntentDist');
    if (distEl && sa.intent_distribution) {
        const intentNames = {
            'directional_speculation': {name: '方向性投机', icon: '🎯', color: 'bg-red-500/20'},
            'institutional_hedging': {name: '机构对冲', icon: '🛡️', color: 'bg-blue-500/20'},
            'arbitrage': {name: '套利交易', icon: '⚖️', color: 'bg-green-500/20'},
            'market_maker_adjust': {name: '做市商调仓', icon: '🔄', color: 'bg-yellow-500/20'},
            'income_generation': {name: '收益增强', icon: '💰', color: 'bg-purple-500/20'},
            'volatility_play': {name: '波动率博弈', icon: '📊', color: 'bg-cyan-500/20'}
        };
        const total = Object.values(sa.intent_distribution).reduce((sum, v) => sum + v.count, 0);
        distEl.innerHTML = Object.entries(sa.intent_distribution).map(([key, val]) => {
            const info = intentNames[key] || {name: key, icon: '❓', color: 'bg-gray-500/20'};
            const pct = total > 0 ? (val.count / total * 100).toFixed(0) : 0;
            return `<div class="flex items-center gap-2">
                <span class="text-xs">${info.icon}</span>
                <span class="text-xs text-gray-300 flex-1">${info.name}</span>
                <div class="w-16 bg-gray-700 rounded-full h-1.5">
                    <div class="${info.color} h-1.5 rounded-full" style="width: ${pct}%"></div>
                </div>
                <span class="text-xs font-mono text-gray-400 w-8 text-right">${val.count}</span>
            </div>`;
        }).join('');
    }
    
    const signalsEl = document.getElementById('aiKeySignals');
    if (signalsEl && sa.key_signals) {
        if (sa.key_signals.length === 0) {
            signalsEl.innerHTML = '<div class="text-gray-600 text-xs">暂无显著信号</div>';
        } else {
            const typeClasses = {
                'warning': 'bg-yellow-500/10 border-yellow-500/30 text-yellow-300',
                'success': 'bg-green-500/10 border-green-500/30 text-green-300',
                'info': 'bg-blue-500/10 border-blue-500/30 text-blue-300',
                'danger': 'bg-red-500/10 border-red-500/30 text-red-300'
            };
            signalsEl.innerHTML = sa.key_signals.map(s => 
                `<div class="p-2 rounded border text-xs ${typeClasses[s.type] || 'bg-gray-500/10 border-gray-500/30 text-gray-300'}">
                    ${safeHTML(s.text)}
                </div>`
            ).join('');
        }
    }
    
    // v2.0: 风险预警
    const riskWarningsSection = document.getElementById('aiRiskWarningsSection');
    const riskWarningsEl = document.getElementById('aiRiskWarnings');
    if (riskWarningsSection && riskWarningsEl && sa.risk_warnings && sa.risk_warnings.length > 0) {
        riskWarningsSection.classList.remove('hidden');
        const levelClasses = {
            'HIGH': 'bg-red-500/10 border-red-500/30 text-red-300',
            'MEDIUM': 'bg-yellow-500/10 border-yellow-500/30 text-yellow-300',
            'LOW': 'bg-blue-500/10 border-blue-500/30 text-blue-300'
        };
        riskWarningsEl.innerHTML = sa.risk_warnings.map(w => 
            `<div class="p-2 rounded border text-xs ${levelClasses[w.level] || 'bg-gray-500/10 border-gray-500/30 text-gray-300'}">
                <span class="font-bold mr-1">${w.level === 'HIGH' ? '🔴' : w.level === 'MEDIUM' ? '🟡' : '🔵'}</span>${safeHTML(w.text)}
            </div>`
        ).join('');
    } else if (riskWarningsSection) {
        riskWarningsSection.classList.add('hidden');
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
    const dvolEl = document.getElementById('dvolValue');
    if (dvolEl) dvolEl.textContent = dvol ? dvol.toFixed(2) : '--';

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
}

let _expandedRow = null;

// 更新后的表格渲染函数 - 分页加载（前30条+加载更多）
function updateOpportunitiesTable(contracts) {
    _allContracts = contracts || [];
    _displayedCount = 0;
    window.contractPage = 1;

    const countEl = document.getElementById('contractCount');
    const loadMoreContainer = document.getElementById('loadMoreContainer');

    if (_allContracts.length === 0) {
        countEl.textContent = '0 个合约';
        loadMoreContainer.classList.add('hidden');
        const tbody = document.getElementById('opportunitiesTable');
        tbody.innerHTML = `<tr><td colspan="25" class="text-center py-12 text-gray-500"><div class="flex flex-col items-center gap-3"><i class="fas fa-inbox text-3xl text-gray-600"></i><p>暂无符合条件的合约</p><p class="text-xs text-gray-600">尝试调整扫描参数</p></div></td></tr>`;
        return;
    }

    countEl.textContent = `${_allContracts.length} 个合约`;
    renderTablePage();
}

function renderTablePage() {
    const tbody = document.getElementById('opportunitiesTable');
    const loadMoreContainer = document.getElementById('loadMoreContainer');
    const loadedCountEl = document.getElementById('loadedCount');
    const totalCountEl = document.getElementById('totalCount');

    const prevCount = _displayedCount || 0;
    _displayedCount = Math.min(window.contractPage * TABLE_PAGE_SIZE, _allContracts.length);
    const isAppend = prevCount > 0 && _displayedCount > prevCount && tbody.children.length > 0;
    const displayContracts = _allContracts.slice(isAppend ? prevCount : 0, _displayedCount);

    if (loadedCountEl) loadedCountEl.textContent = _displayedCount;
    if (totalCountEl) totalCountEl.textContent = _allContracts.length;

    const hasMore = _displayedCount < _allContracts.length;
    if (hasMore) {
        loadMoreContainer.classList.remove('hidden');
    } else {
        loadMoreContainer.classList.add('hidden');
    }

    let highRiskContracts = [];
    const rowsHtml = displayContracts.map((contract, idx) => {
        const platformColor = contract.platform === 'Deribit' ? 'text-blue-400' : 'text-yellow-400';
        const liqColor = contract.liquidity_score >= 70 ? 'text-green-400' : contract.liquidity_score >= 40 ? 'text-yellow-400' : 'text-red-400';
        const deltaAbs = Math.abs(Number(contract.delta) || 0);

        const symbol = safeHTML(contract.symbol || contract.instrument_name || 'N/A');
        contract.symbol = symbol;

        let riskBadge = '';
        let riskClass = '';

        let distancePct = null;
        const strikeVal = Number(contract.strike);
        if (currentSpotPrice && strikeVal > 0 && Number.isFinite(strikeVal)) {
            distancePct = Math.abs(strikeVal - currentSpotPrice) / currentSpotPrice * 100;
        }

        const isHighDelta = deltaAbs > 0.45;
        const isNearStrike = distancePct !== null && distancePct < 2;

        if (isHighDelta && isNearStrike) {
            riskClass = 'risk-alert-high';
            riskBadge = '<span class="risk-badge bg-red-500 text-[10px] text-white px-1.5 py-0.5 rounded font-bold"><i class="fas fa-exclamation-triangle"></i> 极高</span>';
            highRiskContracts.push({ contract, reason: `Delta(${deltaAbs.toFixed(3)})>0.45 且 价格接近Strike(${distancePct.toFixed(1)}%)` });
        } else if (isHighDelta) {
            riskClass = 'risk-alert-high';
            riskBadge = '<span class="risk-badge bg-red-500 text-[10px] text-white px-1.5 py-0.5 rounded font-bold"><i class="fas fa-exclamation"></i> 高</span>';
            highRiskContracts.push({ contract, reason: `Delta(${deltaAbs.toFixed(3)})>0.45` });
        } else if (isNearStrike) {
            riskClass = 'risk-alert-medium';
            riskBadge = '<span class="bg-orange-500 text-[10px] text-white px-1.5 py-0.5 rounded"><i class="fas fa-exclamation-circle"></i> 接近</span>';
        } else if (deltaAbs > 0.35) {
            riskBadge = '<span class="bg-yellow-500/80 text-[10px] text-white px-1.5 py-0.5 rounded">警告</span>';
        } else {
            riskBadge = '<span class="bg-green-500/50 text-[10px] text-white px-1.5 py-0.5 rounded">正常</span>';
        }

        const spreadColor = (contract.spread_pct || 0) > 5 ? 'text-orange-400' : 'text-gray-400';
        const lossVal = Math.abs(Number(contract.loss_at_10pct) || 0);
        const breakeven = Number(contract.breakeven) || 0;
        const oi = Number(contract.open_interest) || 0;
        const spreadPct = Number(contract.spread_pct) || 0;
        const gamma = Number(contract.gamma) || 0;
        const vega = Number(contract.vega) || 0;
        const theta = Number(contract.theta) || 0;
        const iv = Number(contract.mark_iv) || Number(contract.iv) || 0;
        const pop = Number(contract.pop) || null;
        const bePct = Number(contract.breakeven_pct) || null;
        const ivRank = Number(contract.iv_rank) || null;
        const marginReq = Number(contract.margin_required) || 0;
        const capEff = Number(contract.capital_efficiency) || 0;
        const supportDist = contract.support_distance_pct !== undefined ? Number(contract.support_distance_pct) : null;
        const isPut = contract.option_type === 'P' || contract.option_type === 'PUT';
        const dte = Number(contract.dte) || 0;
        const apr = Number(contract.apr) || 0;
        const premium = Number(contract.premium) || Number(contract.premium_usd) || 0;
        const liquidityScore = Number(contract.liquidity_score) || 0;
        const score = contract._score;

        return `<tr class="hover:bg-white/[0.02] transition ${riskClass}">
            <td class="py-2 px-3 text-center"><span class="${platformColor} text-xs font-semibold">${safeHTML(contract.platform)}</span></td>
            <td class="py-2 px-2 text-center"><span class="${isPut ? 'text-green-400' : 'text-blue-400'} text-xs font-bold">${safeHTML(contract.option_type || 'PUT')}</span></td>
            <td class="py-2 px-2 text-center font-mono text-xs tabular-nums">${safeHTML(symbol.split('-')[1] || '')}</td>
            <td class="py-2 px-2 text-center text-xs tabular-nums">${dte.toFixed(0)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums">$${Math.round(strikeVal).toLocaleString()}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums font-semibold ${deltaAbs > 0.35 ? 'text-red-400' : deltaAbs > 0.25 ? 'text-yellow-400' : 'text-green-400'}">${deltaAbs.toFixed(4)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums font-semibold ${theta > 5 ? 'text-emerald-400' : theta > 0 ? 'text-green-300' : 'text-gray-500'}" title="每日时间价值衰减">${theta > 0 ? '+' : ''}${theta.toFixed(2)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${gamma > 0.15 ? 'text-orange-400' : 'text-gray-300'}">${gamma.toFixed(4)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${vega > 50 ? 'text-yellow-400' : 'text-gray-300'}">${vega.toFixed(1)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${iv ? (iv >= 80 ? 'text-red-400' : iv >= 50 ? 'text-yellow-400' : 'text-emerald-400') : 'text-gray-300'}">${iv ? iv.toFixed(1) + '%' : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs font-bold text-green-400 tabular-nums">${apr.toFixed(1)}%</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${pop ? (isPut ? (pop >= 70 ? 'text-emerald-400' : pop >= 50 ? 'text-yellow-300' : 'text-orange-400') : (pop <= 30 ? 'text-emerald-400' : pop <= 50 ? 'text-yellow-300' : 'text-red-400')) : 'text-gray-500'}" title="${isPut ? '到期不被行权概率' : '被行权概率(卖飞风险)'}">${pop ? (isPut ? pop.toFixed(0) + '%' : (100 - pop).toFixed(0) + '%飞') : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-yellow-300/90">$${premium.toLocaleString(undefined, {maximumFractionDigits: 2})}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-gray-400" title="开仓保证金需求">$${marginReq.toLocaleString(undefined, {maximumFractionDigits: 0})}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums font-semibold ${capEff >= 15 ? 'text-emerald-400' : capEff >= 8 ? 'text-green-300' : 'text-gray-400'}" title="权利金/保证金">${capEff.toFixed(1)}%</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${supportDist !== null && supportDist !== undefined ? (supportDist >= 10 ? 'text-emerald-400' : supportDist >= 5 ? 'text-yellow-300' : 'text-red-400') : 'text-gray-600'}" title="PUT行权价到支撑位距离">${supportDist !== null && supportDist !== undefined ? supportDist.toFixed(1) + '%' : (isPut ? '-' : 'N/A')}</td>
            <td class="py-2 px-2 text-center"><span class="${liqColor} text-xs font-medium">${liquidityScore}</span></td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-red-400/80">$${lossVal.toLocaleString(undefined, {maximumFractionDigits: 0})}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-blue-300/80">$${breakeven.toLocaleString(undefined, {maximumFractionDigits: 0})}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${bePct ? (bePct >= 10 ? 'text-emerald-400' : bePct >= 5 ? 'text-yellow-300' : 'text-orange-400') : 'text-gray-500'}">${bePct ? bePct.toFixed(1) + '%' : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-gray-400">${oi.toLocaleString()}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${spreadColor}">${spreadPct.toFixed(2)}%</td>
            <td class="py-2 px-2 text-center font-mono text-xs tabular-nums ${ivRank ? (ivRank >= 70 ? 'text-red-400' : ivRank <= 30 ? 'text-emerald-400' : 'text-gray-400') : 'text-gray-500'}">${ivRank ? String(ivRank).split('.')[0] : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${score !== undefined ? (score >= 0.7 ? "text-emerald-400 font-bold" : score >= 0.5 ? "text-green-300" : score >= 0.3 ? "text-yellow-300" : "text-gray-500") : "text-gray-500"}" title="加权评分: APR(25%)+POP(25%)+安全垫(20%)+流动性(15%)+IV中性(15%)">${score !== undefined ? score.toFixed(3) : "-"}</td>
            <td class="py-2 px-3 text-center">${riskBadge}</td>
        </tr>`;
    }).join('');

    if (isAppend) {
        tbody.insertAdjacentHTML('beforeend', rowsHtml);
    } else {
        tbody.innerHTML = rowsHtml;
    }

    if (highRiskContracts.length > 0 && 'Notification' in window && Notification.permission === 'granted') {
        new Notification('期权风险预警', {
            body: `检测到 ${highRiskContracts.length} 个高风险合约，建议执行滚仓操作`,
            icon: '/static/favicon.ico'
        });
    }
}

function loadMoreContracts() {
    window.contractPage = (window.contractPage || 1) + 1;
    renderTablePage();
}

function resetTableSort() {
    _currentSortField = null;
    _currentSortDir = 'desc';
    document.querySelectorAll('#tableHeaders th').forEach(th => th.classList.remove('sort-asc', 'sort-desc'));
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
                <div class="flex justify-between"><span class="font-mono">${safeHTML(alt.symbol)}</span><span class="text-green-400">${alt.apr.toFixed(1)}% APR</span></div>
                <div class="text-xs text-gray-400 mt-1">Strike: ${Math.round(alt.strike).toLocaleString()} | DTE: ${alt.dte.toFixed(0)} | Delta: ${Math.abs(alt.delta).toFixed(3)}</div>
            </div>
        `).join('')}</div>`;
    }

    content.innerHTML = `
        <div class="space-y-4">
            <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-4">
                <h4 class="font-semibold text-red-400 mb-2">当前持仓风险</h4>
                <div class="grid grid-cols-2 gap-4 text-sm">
                    <div><span class="text-gray-400">合约:</span> <span class="font-mono">${safeHTML(contract.symbol)}</span></div>
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

    if (!container) { console.warn('大单风向标: container not found'); return; }

    console.log('大单风向标更新:', count, '条记录,', trades?.length || 0, '条详情');

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
        const inst = safeHTML(trade.instrument_name || trade.symbol || '');
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
        const dteMatch = String(trade.instrument_name || trade.symbol || '').match(/(\d{1,2}[A-Z]{3}\d{2})/);
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

        const flowCN = safeHTML(flowNames[flow] || flow || '');
        const flowHint = safeHTML(flowHints[flow] || '');

        const blockTag = isBlock
            ? '<span class="bg-amber-500/30 text-amber-300 text-[9px] px-1 py-0.5 rounded font-bold">大宗</span>'
            : '';

        const deltaStr = delta ? 'Δ' + Math.abs(delta).toFixed(2) : '';
        const ivStr = iv ? 'IV' + iv.toFixed(0) + '%' : '';

        const volStr = volume > 0 ? volume.toFixed(0) + '张' : '';

        return `<div class="${sevStyle.bg} border-l-3 ${sevStyle.border} rounded-lg px-3 py-2 text-xs hover:bg-white/5 transition cursor-default">
            <div class="flex items-center gap-1.5">
                <span class="${dirColor} font-bold text-sm">${dirIcon}</span>
                <span class="font-mono text-white font-medium truncate" style="max-width:130px" title="${inst}">${inst || '--'}</span>
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

async function loadMacroData() {
    try {
        const res = await safeFetch(`${API_BASE}/api/macro-data`);
        const data = await res.json();
        
        const fgEl = document.getElementById('fearGreedValue');
        const fgLabel = document.getElementById('fearGreedLabel');
        if (fgEl && data.fear_greed) {
            const val = data.fear_greed.value;
            const label = data.fear_greed.classification;
            if (val !== null) {
                fgEl.textContent = val;
                fgEl.className = 'text-2xl font-bold ' + (
                    val <= 20 ? 'text-red-400' : val <= 40 ? 'text-orange-400' : 
                    val <= 60 ? 'text-yellow-400' : val <= 80 ? 'text-green-400' : 'text-emerald-400'
                );
                fgLabel.textContent = label;
            }
        }
        
        const frEl = document.getElementById('fundingRateValue');
        const frLabel = document.getElementById('fundingRateLabel');
        if (frEl && data.funding_rate) {
            const rate = data.funding_rate.current_rate;
            const sentiment = data.funding_rate.sentiment;
            if (rate !== null) {
                frEl.textContent = rate.toFixed(4) + '%';
                frEl.className = 'text-2xl font-bold ' + (
                    rate < -0.1 ? 'text-red-400' : rate > 0.1 ? 'text-emerald-400' : 'text-gray-300'
                );
                frLabel.textContent = sentiment;
            }
        }
    } catch (e) {
        console.error('加载宏观数据失败:', e);
    }
}

function parseUTC(ts) {
    if (!ts) return new Date(NaN);
    const s = String(ts).trim();
    if (s.includes('T') || s.includes('Z')) {
        const iso = s.endsWith('Z') ? s : s + 'Z';
        return new Date(iso);
    }
    const parts = s.split(/[- :]/);
    if (parts.length >= 5) {
        const [year, month, day, hour, minute, second = 0] = parts.map(Number);
        return new Date(Date.UTC(year, month - 1, day, hour, minute, second));
    }
    return new Date(s);
}

function updateLastUpdateTime(timestamp) {
    const date = timestamp ? parseUTC(timestamp) : new Date();
    if (isNaN(date.getTime())) { document.getElementById('lastUpdate').textContent = '更新于 --:--:--'; return; }
    const timeStr = date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    document.getElementById('lastUpdate').textContent = `更新于 ${timeStr}`;
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

        // Filter out zero/invalid dvol values and outliers (e.g. 50)
        const filtered = data.filter(d => d.dvol && d.dvol > 0 && d.dvol < 49);

        dvolChart.data.labels = filtered.map(d => {
            const date = parseUTC(d.time || d.timestamp);
            return hours <= 24 ? `${date.getUTCHours()}:${String(date.getUTCMinutes()).padStart(2,'0')}` : hours <= 168 ? `${date.getUTCMonth()+1}/${date.getUTCDate()} ${date.getUTCHours()}:00` : `${date.getUTCMonth()+1}/${date.getUTCDate()}`;
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

// ============================================================
// AI Copilot Chat Widget
// ============================================================
let copilotOpen = false;

function toggleCopilotChat() {
    const chat = document.getElementById('copilotChat');
    copilotOpen = !copilotOpen;
    if (chat) {
        chat.classList.toggle('hidden', !copilotOpen);
    }
    if (copilotOpen) {
        const input = document.getElementById('copilotInput');
        if (input) input.focus();
    }
}

function addCopilotMessage(content, isUser = false) {
    const container = document.getElementById('copilotMessages');
    if (!container) return;

    const div = document.createElement('div');
    div.className = 'flex items-start gap-2 ' + (isUser ? 'flex-row-reverse' : '');

    const icon = document.createElement('i');
    icon.className = isUser
        ? 'fas fa-user text-green-400 mt-1 text-xs'
        : 'fas fa-robot text-blue-400 mt-1 text-xs';

    const bubble = document.createElement('div');
    bubble.className = isUser
        ? 'bg-blue-600/30 rounded-lg p-3 text-sm text-gray-200 max-w-[90%]'
        : 'bg-gray-800/50 rounded-lg p-3 text-sm text-gray-300 max-w-[90%]';

    // 安全处理：将内容按换行拆分，使用 textContent 避免 XSS
    const lines = String(content).split('\n');
    lines.forEach((line, index) => {
        if (index > 0) {
            bubble.appendChild(document.createElement('br'));
        }
        bubble.appendChild(document.createTextNode(line));
    });

    div.appendChild(icon);
    div.appendChild(bubble);
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

async function sendCopilotMessage(event) {
    event.preventDefault();
    const input = document.getElementById('copilotInput');
    if (!input) return;
    
    const message = input.value.trim();
    if (!message) return;
    
    input.value = '';
    addCopilotMessage(message, true);
    
    // 显示加载中
    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'flex items-start gap-2';
    loadingDiv.innerHTML = '<i class="fas fa-robot text-blue-400 mt-1 text-sm"></i><div class="bg-gray-800/50 rounded-lg p-3 text-sm text-gray-500 max-w-[90%]">思考中...</div>';
    document.getElementById('copilotMessages').appendChild(loadingDiv);
    
    try {
        const currency = document.getElementById('currencySelect')?.value || 'BTC';
        const aiCfg = getAiConfig();
        const headers = {};
        if (aiCfg.api_key) headers['X-AI-API-Key'] = aiCfg.api_key;
        if (aiCfg.base_url) headers['X-AI-Base-URL'] = aiCfg.base_url;
        if (aiCfg.model) headers['X-AI-Model'] = aiCfg.model;

        // 调试日志
        console.log('[AI Debug] 配置:', { hasKey: !!aiCfg.api_key, model: aiCfg.model, baseUrl: aiCfg.base_url });
        console.log('[AI Debug] 请求 Headers:', Object.keys(headers));

        const response = await fetch(`${API_BASE}/api/copilot/chat?message=${encodeURIComponent(message)}&currency=${currency}`, {
            method: 'POST',
            headers: headers
        });
        const data = await response.json();

        // 调试日志
        console.log('[AI Debug] 响应:', data);

        // 移除加载中
        loadingDiv.remove();

        if (data.response) {
            addCopilotMessage(data.response);
        } else {
            addCopilotMessage('抱歉，AI 服务暂时不可用。请确保已配置 API Key。');
        }
    } catch (e) {
        loadingDiv.remove();
        console.error('[AI Debug] 请求异常:', e);
        addCopilotMessage('请求失败，请检查网络连接。');
    }
}

// AI 配置函数
function openAiSettings() {
    const modal = document.getElementById('aiSettingsModal');
    if (modal) modal.classList.add('active');
    // 加载已保存的配置
    try {
        const cfg = JSON.parse(localStorage.getItem('ai_config') || '{}');
        const keyInput = document.getElementById('aiApiKey');
        const urlInput = document.getElementById('aiBaseUrl');
        const modelInput = document.getElementById('aiModel');
        if (keyInput) keyInput.value = cfg.api_key || '';
        if (urlInput) urlInput.value = cfg.base_url || '';
        if (modelInput) modelInput.value = cfg.model || '';
    } catch (_) {}
}

function closeAiSettingsModal() {
    const modal = document.getElementById('aiSettingsModal');
    if (modal) modal.classList.remove('active');
}

function saveAiConfig() {
    const apiKey = document.getElementById('aiApiKey')?.value.trim() || '';
    const baseUrl = document.getElementById('aiBaseUrl')?.value.trim() || '';
    const model = document.getElementById('aiModel')?.value.trim() || '';
    const cfg = { api_key: apiKey, base_url: baseUrl, model: model };
    try {
        localStorage.setItem('ai_config', JSON.stringify(cfg));
        showAlert('AI 配置已保存', 'success');
    } catch (e) {
        showAlert('保存失败: ' + e.message, 'error');
    }
    closeAiSettingsModal();
}

function clearAiConfig() {
    try {
        localStorage.removeItem('ai_config');
        const keyInput = document.getElementById('aiApiKey');
        const urlInput = document.getElementById('aiBaseUrl');
        const modelInput = document.getElementById('aiModel');
        if (keyInput) keyInput.value = '';
        if (urlInput) urlInput.value = '';
        if (modelInput) modelInput.value = '';
        showAlert('AI 配置已清除', 'success');
    } catch (e) {
        showAlert('清除失败: ' + e.message, 'error');
    }
}

function getAiConfig() {
    try {
        return JSON.parse(localStorage.getItem('ai_config') || '{}');
    } catch (_) {
        return {};
    }
}

// 回车键发送
document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && copilotOpen) {
        const input = document.getElementById('copilotInput');
        if (document.activeElement === input) {
            e.preventDefault();
            sendCopilotMessage(new Event('submit'));
        }
    }
});

setTimeout(addDemoAlerts, 1000);

// 点击模态框外部关闭
document.getElementById('rollModal').addEventListener('click', (e) => {
    if (e.target.id === 'rollModal') closeRollModal();
});


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

    const expandedSymbols = new Set();
    document.querySelectorAll('tr[data-expanded="true"]').forEach(r => expandedSymbols.add(r.dataset.symbol));

    const fieldMap = {
        'mark_iv': 'iv',
        'premium': 'premium_usd',
        'spread_pct': 'spread_pct',
        'distance_spot_pct': 'distance_spot_pct'
    };
    const actualField = fieldMap[field] || field;

    if (currentSort.field === actualField) {
        currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
    } else {
        currentSort.field = actualField;
        currentSort.direction = 'desc';
    }

    updateSortIcons(actualField, currentSort.direction);

    // 排序全局合约数据（_allContracts 是 currentData.contracts 的引用或副本）
    if (_allContracts.length > 0) {
        _allContracts.sort((a, b) => {
            let valA = a[actualField];
            let valB = b[actualField];
            if (field === 'delta') {
                valA = Math.abs(valA);
                valB = Math.abs(valB);
            }
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
        // 同时更新 currentData.contracts 以保持同步
        currentData.contracts = _allContracts;
    }

    window.contractPage = 1;
    renderTablePage();

    showAlert(`已按 ${getFieldName(field)} ${currentSort.direction === 'asc' ? '升序' : '降序'} 排序`, 'info');

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

// getFieldName 已从 utils.js 导入

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
                        <span class="${colorClass}">${safeHTML(f.label)}</span>
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
            // 销毁旧 Chart 实例防止内存泄漏
            if (window._strikeChart) {
                window._strikeChart.destroy();
                window._strikeChart = null;
            }
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
// Module 1: IV Term Structure (已迁移到 term-structure.js)
// ============================================================
async function _loadTermStructure(retryCount = 0) {
    const maxRetries = 2;
    const statusEl = document.getElementById('ts7');
    if (!statusEl) { console.warn('TS: container not found'); return; }
    try {
        const currency = document.getElementById('currencySelect')?.value || 'BTC';
        const resp = await safeFetch(API_BASE + '/api/charts/vol-surface?currency=' + currency);
        const d = await resp.json();
        if (d.error) {
            if (retryCount < maxRetries) {
                setTimeout(() => _loadTermStructure(retryCount + 1), 2000);
            return;
        }
        _showTermStructureError(d.error);
            return;
        }

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
        // 恢复 canvas 显示并隐藏错误 overlay
        ctx.style.display = '';
        const tsErr = document.getElementById('termStructureError');
        if (tsErr) tsErr.style.display = 'none';

        const validTs = tsData.filter(t => t.avg_iv !== null && t.avg_iv > 0);
        if (validTs.length < 2) {
            ctx.style.display = 'none';
            let errDiv = document.getElementById('termStructureError');
            if (!errDiv) {
                errDiv = document.createElement('div');
                errDiv.id = 'termStructureError';
                errDiv.className = 'text-gray-500 text-center py-8 text-sm absolute inset-0 bg-gray-900/90 z-10';
                ctx.parentElement.appendChild(errDiv);
            }
            errDiv.textContent = '数据不足 (' + validTs.length + ' 个到期月份)';
            errDiv.style.display = '';
            return;
        }

        if (typeof Chart === 'undefined') {
            ctx.style.display = 'none';
            let errDiv = document.getElementById('termStructureError');
            if (!errDiv) {
                errDiv = document.createElement('div');
                errDiv.id = 'termStructureError';
                errDiv.className = 'text-yellow-500 text-center py-8 text-sm absolute inset-0 bg-gray-900/90 z-10';
                ctx.parentElement.appendChild(errDiv);
            }
            errDiv.textContent = '⚠️ Chart.js 未加载';
            errDiv.style.display = '';
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

        // ===== v2.0 学术分析展示 =====
        const analysis = d.analysis;
        if (!analysis || analysis.error) {
            console.warn('IV分析不可用:', analysis?.error || '无数据');
        } else {
            // 结构标签
            const structLabel = document.getElementById('tsStructureLabel');
            if (structLabel && analysis.structure_type) {
                const st = analysis.structure_type;
                structLabel.textContent = st.icon + ' ' + st.name;
                structLabel.className = 'text-xs px-2 py-0.5 rounded-full font-medium ' + (st.color.includes('text-') ? 'bg-' + st.color.replace('text-','').replace('-400','-500/20').replace('-300','-500/20') + ' ' + st.color : 'bg-gray-700 text-gray-400');
            }
            // 斜率标签
            const slopeLabel = document.getElementById('tsSlopeLabel');
            if (slopeLabel && analysis.slope) {
                const s = analysis.slope;
                slopeLabel.textContent = (s.percent > 0 ? '+' : '') + s.percent + '%';
                slopeLabel.className = 'text-xs px-2 py-0.5 rounded-full ' + (s.percent >= 0 ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400');
            }
            // 市场状态
            const msEl = document.getElementById('ivMarketState');
            const msAdvice = document.getElementById('ivMarketAdvice');
            if (msEl && analysis.market_state) {
                const ms = analysis.market_state;
                msEl.textContent = ms.icon + ' ' + ms.name;
                msEl.className = 'text-lg font-bold ' + ms.color;
                if (msAdvice) msAdvice.textContent = ms.advice;
            }
            // VRP
            const vrpEl = document.getElementById('ivVRPValue');
            const vrpDesc = document.getElementById('ivVRPDesc');
            if (vrpEl && analysis.vrp) {
                const v = analysis.vrp;
                const vrpColor = v.signal && v.signal.includes('SELL') ? 'text-green-400' : v.signal === 'BUY_EDGE' ? 'text-blue-400' : 'text-gray-400';
                vrpEl.textContent = (v.value > 0 ? '+' : '') + v.value + '%';
                vrpEl.className = 'text-lg font-bold ' + vrpColor;
                if (vrpDesc) vrpDesc.textContent = v.description;
            }
            // 形态指标
            const slopeGrade = document.getElementById('ivSlopeGrade');
            if (slopeGrade && analysis.slope) {
                const s = analysis.slope;
                slopeGrade.textContent = s.grade || '--';
                slopeGrade.className = 'text-xs font-bold ' + (s.grade === 'SEVERELY_INVERTED' || s.grade === 'INVERTED' ? 'text-red-400' : s.grade === 'STEEP' || s.grade === 'VERY_STEEP' ? 'text-green-400' : 'text-gray-400');
            }
            const curvEl = document.getElementById('ivCurvatureType');
            if (curvEl && analysis.curvature) {
                curvEl.textContent = analysis.curvature.type || '--';
                curvEl.className = 'text-xs font-bold ' + (analysis.curvature.type === 'HUMP' ? 'text-yellow-400' : 'text-gray-400');
            }
            const regEl = document.getElementById('ivRegime');
            if (regEl && analysis.iv_levels) {
                const il = analysis.iv_levels;
                regEl.textContent = il.avg_iv + '%';
                regEl.className = 'text-xs font-bold ' + (il.regime === 'EXTREME' ? 'text-red-400' : il.regime === 'HIGH' ? 'text-orange-400' : il.regime === 'LOW' || il.regime === 'VERY_LOW' ? 'text-blue-400' : 'text-green-400');
            }
            // 策略建议
            const recsEl = document.getElementById('ivRecommendations');
            if (recsEl && analysis.recommendations && analysis.recommendations.length > 0) {
                const typeColors = {'warning': 'border-red-500/30 bg-red-500/5', 'opportunity': 'border-green-500/30 bg-green-500/5', 'info': 'border-blue-500/30 bg-blue-500/5'};
                const fragment = document.createDocumentFragment();
                analysis.recommendations.forEach(r => {
                    const div = document.createElement('div');
                    div.className = 'p-2.5 rounded border ' + (typeColors[r.type] || 'border-gray-700/30 bg-gray-800/30');

                    const title = document.createElement('div');
                    title.className = 'text-xs font-bold mb-1';
                    title.textContent = r.title || '';
                    div.appendChild(title);

                    const body = document.createElement('div');
                    body.className = 'text-[11px] text-gray-400 leading-relaxed';
                    body.textContent = r.body || '';
                    div.appendChild(body);

                    const action = document.createElement('div');
                    action.className = 'text-[11px] text-cyan-300 mt-1 font-medium';
                    action.textContent = '→ ' + (r.action || '');
                    div.appendChild(action);

                    fragment.appendChild(div);
                });
                recsEl.innerHTML = '';
                recsEl.appendChild(fragment);
            }
        }
    } catch(e) {
        console.error('TS error:', e);
        if (retryCount < maxRetries) {
            setTimeout(() => _loadTermStructure(retryCount + 1), 2000);
        } else {
            _showTermStructureError(e.message);
        }
    }
}

function _showTermStructureError(message) {
    const el = document.getElementById('termStructureChart');
    if (el && el.parentElement) {
        // 保留 canvas，使用 overlay 显示错误
        el.style.display = 'none';
        let errDiv = document.getElementById('termStructureError');
        if (!errDiv) {
            errDiv = document.createElement('div');
            errDiv.id = 'termStructureError';
            errDiv.className = 'text-gray-500 text-center py-8 text-xs absolute inset-0 bg-gray-900/90 z-10';
            el.parentElement.appendChild(errDiv);
        }
        errDiv.innerHTML = '<i class="fas fa-exclamation-triangle text-yellow-400 text-2xl mb-2"></i><br>' +
            '数据加载失败: ' + safeHTML(message) + '<br>' +
            '<button onclick="window.loadTermStructure()" class="mt-2 px-3 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded">' +
            '<i class="fas fa-redo mr-1"></i>重试</button>';
        errDiv.style.display = '';
    }
}

// ============================================================
// Module 2: Max Pain & GEX
// ============================================================
let mpChart = null;
async function _loadMaxPain(retryCount = 0) {
    const maxRetries = 2;
    const spotEl = document.getElementById('mpSpot');
    if (!spotEl) { console.warn('MP: container not found'); return; }
    try {
        const currency = document.getElementById('currencySelect')?.value || 'BTC';
        const resp = await safeFetch(API_BASE + '/api/metrics/max-pain?currency=' + currency);
        const d = await resp.json();
        if (d.error || !d.expiries) {
            if (retryCount < maxRetries) {
                setTimeout(() => _loadMaxPain(retryCount + 1), 2000);
                return;
            }
            _showMaxPainError(d.error || '无数据');
            return;
        }

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
        const hasPainData = (exp.pain_curve && exp.pain_curve.length) || (exp.pain_chart && exp.pain_chart.length);
        if (!ctx || !hasPainData) return;

        // 恢复 canvas 显示并隐藏错误 overlay
        ctx.style.display = '';
        const mpErr = document.getElementById('maxPainError');
        if (mpErr) mpErr.style.display = 'none';

        if (typeof Chart === 'undefined') {
            ctx.style.display = 'none';
            let errDiv = document.getElementById('maxPainError');
            if (!errDiv) {
                errDiv = document.createElement('div');
                errDiv.id = 'maxPainError';
                errDiv.className = 'text-yellow-500 text-center py-8 text-sm absolute inset-0 bg-gray-900/90 z-10';
                ctx.parentElement.appendChild(errDiv);
            }
            errDiv.textContent = '⚠️ Chart.js 未加载';
            errDiv.style.display = '';
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
        if (retryCount < maxRetries) {
            setTimeout(() => _loadMaxPain(retryCount + 1), 2000);
        } else {
            _showMaxPainError(e.message);
        }
    }
}

function _showMaxPainError(message) {
    const container = document.getElementById('painGexChart');
    if (container && container.parentElement) {
        // 保留 canvas，使用 overlay 显示错误
        container.style.display = 'none';
        let errDiv = document.getElementById('maxPainError');
        if (!errDiv) {
            errDiv = document.createElement('div');
            errDiv.id = 'maxPainError';
            errDiv.className = 'text-gray-500 text-center py-8 absolute inset-0 bg-gray-900/90 z-10';
            container.parentElement.appendChild(errDiv);
        }
        errDiv.innerHTML = '<i class="fas fa-exclamation-triangle text-yellow-400 text-2xl mb-2"></i><br>' +
            '最大痛点数据加载失败: ' + safeHTML(message) + '<br>' +
            '<button onclick="window.loadMaxPain()" class="mt-2 px-3 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded">' +
            '<i class="fas fa-redo mr-1"></i>重试</button>';
        errDiv.style.display = '';
    }
}

// ============================================================
// Module 3: Martingale Sandbox v2.0
// ============================================================
async function _runSandbox() {
    var strike = parseFloat(document.getElementById('sbStrike').value) || 65000;
    var optionType = document.getElementById('sbOptionType').value || 'P';
    var qty = parseFloat(document.getElementById('sbQty').value) || 1;
    var premium = parseFloat(document.getElementById('sbPremium').value) || 2000;
    var dte = parseInt(document.getElementById('sbDTE').value) || 30;
    var crash = parseFloat(document.getElementById('sbCrash').value) || 45000;
    var reserve = parseFloat(document.getElementById('sbReserve').value) || 50000;
    var margin = parseFloat(document.getElementById('sbMargin').value) || 0.20;
    var minAPR = parseFloat(document.getElementById('sbMinAPR').value) || 5;

    var resultDiv = document.getElementById('sandboxResult');
    if (!resultDiv) { alert('沙盘容器未找到'); return; }
    resultDiv.innerHTML = '<div class="text-center py-4 text-cyan-400"><i class="fas fa-spinner fa-spin mr-2"></i>🔄 推演计算中...</div>';

    try {
        var resp = await safeFetch(API_BASE + '/api/sandbox/simulate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                current_strike: strike, option_type: optionType,
                current_qty: qty, avg_premium: premium, avg_dte: dte,
                crash_price: crash, reserve_capital: reserve,
                margin_ratio: margin, min_apr: minAPR
            })
        });
        var d = await resp.json();

        var html = '';

        // ===== 安全评估卡片 =====
        var safety = d.safety_assessment || {};
        var safetyColors = {
            'SAFE': 'bg-green-900/30 border-green-500/40 text-green-300',
            'WARNING': 'bg-yellow-900/30 border-yellow-500/40 text-yellow-300',
            'DANGER': 'bg-red-900/30 border-red-500/40 text-red-300',
            'CRITICAL': 'bg-red-900/50 border-red-500/60 text-red-200'
        };
        var sc = safetyColors[safety.level] || 'bg-gray-800 border-gray-600 text-gray-300';
        html += '<div class="p-4 rounded-lg border ' + sc + '">';
        html += '<div class="flex items-center justify-between mb-2">';
        html += '<span class="text-sm font-bold">🛡️ 安全评估</span>';
        html += '<span class="text-xs font-mono bg-black/20 px-2 py-1 rounded">资金覆盖率 ' + (safety.reserve_sufficiency || 0) + '%</span>';
        html += '</div>';
        html += '<div class="text-sm">' + safeHTML(safety.message) + '</div>';
        html += '</div>';

        // ===== 崩盘情景 + 损失分析 =====
        var crash = d.crash_scenario || {};
        var loss = d.loss_analysis || {};
        html += '<div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">';
        
        // 左：崩盘情景
        html += '<div class="p-3 rounded-lg bg-gray-800 border border-gray-700/30">';
        html += '<div class="text-xs font-semibold text-gray-400 mb-2">📉 崩盘情景</div>';
        html += '<div class="space-y-1 text-xs">';
        html += '<div class="flex justify-between"><span class="text-gray-500">当前价格</span><span class="font-mono">$' + (crash.from_price || 0).toLocaleString() + '</span></div>';
        html += '<div class="flex justify-between"><span class="text-gray-500">崩盘目标</span><span class="font-mono text-red-400">$' + (crash.to_price || 0).toLocaleString() + '</span></div>';
        html += '<div class="flex justify-between"><span class="text-gray-500">跌幅</span><span class="font-mono text-red-400">' + (crash.drop_pct || 0) + '%</span></div>';
        html += '</div></div>';

        // 右：损失分析
        html += '<div class="p-3 rounded-lg bg-gray-800 border border-gray-700/30">';
        html += '<div class="text-xs font-semibold text-gray-400 mb-2">💥 损失分解</div>';
        html += '<div class="space-y-1 text-xs">';
        html += '<div class="flex justify-between"><span class="text-gray-500">本金损失</span><span class="font-mono text-red-400">$' + (loss.intrinsic_loss || 0).toLocaleString() + '</span></div>';
        html += '<div class="flex justify-between"><span class="text-gray-500">Vega 冲击</span><span class="font-mono text-orange-400">$' + (loss.vega_impact || 0).toLocaleString() + '</span></div>';
        html += '<div class="flex justify-between"><span class="text-gray-500">总损失</span><span class="font-mono text-red-300 font-bold">$' + (loss.total_loss || 0).toLocaleString() + '</span></div>';
        html += '<div class="flex justify-between"><span class="text-gray-500">损失比例</span><span class="font-mono text-red-400">' + (loss.loss_pct || 0) + '%</span></div>';
        html += '</div></div>';
        html += '</div>';

        // ===== 最佳恢复方案 =====
        var best = d.best_plan;
        if (best) {
            var planBorder = best.status === 'success' ? 'border-green-500/40' : best.status === 'partial' ? 'border-yellow-500/40' : 'border-red-500/40';
            var planBg = best.status === 'success' ? 'bg-green-900/10' : best.status === 'partial' ? 'bg-yellow-900/10' : 'bg-red-900/10';
            html += '<div class="p-3 rounded-lg border ' + planBorder + ' ' + planBg + ' mb-3">';
            html += '<div class="text-sm font-bold mb-2">🎯 最佳恢复方案</div>';
            html += '<div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">';
            html += '<div><span class="text-gray-500 block">恢复合约</span><span class="font-mono">' + safeHTML(best.symbol || '-') + '</span></div>';
            html += '<div><span class="text-gray-500 block">行权价 / DTE</span><span class="font-mono">$' + best.strike + ' / ' + best.dte + 'D</span></div>';
            html += '<div><span class="text-gray-500 block">APR / Delta</span><span class="font-mono">' + best.apr + '% / ' + best.delta + '</span></div>';
            html += '<div><span class="text-gray-500 block">OI</span><span class="font-mono">' + best.oi + '</span></div>';
            html += '</div>';
            html += '<div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs mt-2">';
            html += '<div><span class="text-gray-500 block">每张权利金</span><span class="font-mono">$' + best.premium_per_contract + '</span></div>';
            html += '<div><span class="text-gray-500 block">加仓数量</span><span class="font-mono">' + best.contracts + 'x</span></div>';
            html += '<div><span class="text-gray-500 block">所需保证金</span><span class="font-mono text-yellow-400">$' + best.margin_required.toLocaleString() + '</span></div>';
            var nc = best.net_recovery >= 0 ? 'text-green-400' : 'text-red-400';
            html += '<div><span class="text-gray-500 block">净恢复</span><span class="font-mono ' + nc + '">$' + best.net_recovery.toLocaleString() + '</span></div>';
            html += '</div>';
            html += '<div class="grid grid-cols-2 gap-2 text-xs mt-2">';
            var rc = best.remaining_reserve >= 0 ? 'text-green-400' : 'text-red-400';
            html += '<div><span class="text-gray-500 block">剩余后备金</span><span class="font-mono ' + rc + '">$' + best.remaining_reserve.toLocaleString() + '</span></div>';
            html += '<div><span class="text-gray-500 block">安全距离</span><span class="font-mono">' + best.distance_from_crash + '%</span></div>';
            html += '</div>';
            html += '</div>';
        }

        // ===== 候选恢复合约列表 =====
        if (d.recovery_plans && d.recovery_plans.length > 0) {
            html += '<div class="mb-3">';
            html += '<div class="text-xs font-semibold text-gray-400 mb-2">📋 恢复合约列表（Top ' + d.recovery_plans.length + '）</div>';
            html += '<div class="overflow-x-auto">';
            html += '<table class="w-full text-xs text-center">';
            html += '<thead><tr class="text-gray-500">';
            html += '<th class="py-1 px-2 text-left">合约</th>';
            html += '<th class="py-1 px-2">行权价</th>';
            html += '<th class="py-1 px-2">DTE</th>';
            html += '<th class="py-1 px-2">APR</th>';
            html += '<th class="py-1 px-2">权利金</th>';
            html += '<th class="py-1 px-2">数量</th>';
            html += '<th class="py-1 px-2">保证金</th>';
            html += '<th class="py-1 px-2">净恢复</th>';
            html += '<th class="py-1 px-2">状态</th>';
            html += '</tr></thead>';
            html += '<tbody class="divide-y divide-gray-800/30">';
            d.recovery_plans.forEach(function(p) {
                var stClass = p.status === 'success' ? 'text-green-400' : p.status === 'partial' ? 'text-yellow-400' : 'text-red-400';
                var stText = p.status === 'success' ? '✅' : p.status === 'partial' ? '⚠️' : '🔴';
                var nc2 = p.net_recovery >= 0 ? 'text-green-400' : 'text-red-400';
                html += '<tr class="hover:bg-gray-800/30">';
                html += '<td class="py-1 px-2 text-left font-mono text-gray-300">' + safeHTML(p.symbol || '-') + '</td>';
                html += '<td class="py-1 px-2 font-mono">$' + p.strike.toLocaleString() + '</td>';
                html += '<td class="py-1 px-2 font-mono">' + p.dte + 'D</td>';
                html += '<td class="py-1 px-2 font-mono">' + p.apr + '%</td>';
                html += '<td class="py-1 px-2 font-mono">$' + p.premium_per_contract + '</td>';
                html += '<td class="py-1 px-2 font-mono">' + p.contracts + 'x</td>';
                html += '<td class="py-1 px-2 font-mono text-yellow-400">$' + p.margin_required.toLocaleString() + '</td>';
                html += '<td class="py-1 px-2 font-mono ' + nc2 + '">$' + p.net_recovery.toLocaleString() + '</td>';
                html += '<td class="py-1 px-2 ' + stClass + '">' + stText + '</td>';
                html += '</tr>';
            });
            html += '</tbody></table></div></div>';
        }

        if (d.total_candidates === 0) {
            html += '<div class="text-yellow-400 text-xs mt-2 p-2 bg-yellow-900/20 rounded">⚠️ 该价格水平下无可用恢复合约（链上无深度或IV过高）</div>';
        }

        resultDiv.innerHTML = html;
    } catch(e) {
        resultDiv.innerHTML = '<div class="text-red-400 text-sm p-3">❌ 错误: ' + safeHTML(e.message) + '</div>';
    }
}


function _exportCSV() {
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
        if (!ctx) return;
        if (window._pcrChart) {
            window._pcrChart.destroy();
            window._pcrChart = null;
        }
        if (!data || data.length === 0) {
            const container = ctx.parentElement;
            if (container) {
                let errDiv = document.getElementById('pcrChartError');
                if (!errDiv) {
                    errDiv = document.createElement('div');
                    errDiv.id = 'pcrChartError';
                    errDiv.className = 'text-gray-500 text-center py-8 text-xs';
                    container.appendChild(errDiv);
                }
                errDiv.textContent = '暂无 PCR 数据';
                errDiv.style.display = '';
            }
            ctx.style.display = 'none';
            return;
        }
        ctx.style.display = '';
        const pcrErr = document.getElementById('pcrChartError');
        if (pcrErr) pcrErr.style.display = 'none';
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


// =========================================================================
// AI 辩论分析 (Multi-Agent Debate Engine)
// =========================================================================

function initDebateSection() {
    const runBtn = document.getElementById('debateRunBtn');
    if (!runBtn) return;
    runBtn.addEventListener('click', () => runDebate());
}

async function runDebate() {
    const currency = document.getElementById('debateCurrency').value;
    const runBtn = document.getElementById('debateRunBtn');
    const loading = document.getElementById('debateLoading');
    const empty = document.getElementById('debateEmpty');
    const results = document.getElementById('debateResults');

    // 显示加载状态
    runBtn.disabled = true;
    runBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> <span>分析中...</span>';
    loading.classList.remove('hidden');
    empty.classList.add('hidden');
    results.classList.add('hidden');

    try {
        const resp = await safeFetch(`${API_BASE}/api/debate/analyze?currency=${currency}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });

        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}`);
        }

        const data = await resp.json();
        renderDebateResults(data);
    } catch (e) {
        console.error('Debate failed:', e);
        showAlert('辩论分析失败: ' + e.message, 'error');
        empty.classList.remove('hidden');
    } finally {
        runBtn.disabled = false;
        runBtn.innerHTML = '<i class="fas fa-play"></i> <span>开始分析</span>';
        loading.classList.add('hidden');
    }
}

function renderDebateResults(data) {
    const results = document.getElementById('debateResults');
    results.classList.remove('hidden');

    const synthesis = data.synthesis || {};
    const reports = data.reports || [];
    const summary = data.market_data_summary || {};

    // 1. 最终建议
    renderDebateVerdict(synthesis);

    // 2. 各智能体卡片
    renderDebateAgents(reports);

    // 3. 市场数据摘要
    renderDebateMarketSummary(summary, data);

    // 4. 错误信息
    renderDebateErrors(data.errors || []);
}

function renderDebateVerdict(synthesis) {
    const score = synthesis.overall_score || 0;
    const rec = synthesis.recommendation || 'hold';
    const recLabel = synthesis.recommendation_label || '观望';
    const consensus = synthesis.consensus || '';

    // 评分颜色
    const scoreColor = score > 30 ? 'text-green-400' : score > 0 ? 'text-emerald-300' : score > -30 ? 'text-yellow-400' : 'text-red-400';

    // 推荐图标
    const recIcons = {
        'strong_buy': '🚀', 'buy': '📈', 'hold': '⚖️', 'sell': '📉', 'strong_sell': '🚨'
    };

    document.getElementById('debateRecIcon').textContent = recIcons[rec] || '⚖️';
    document.getElementById('debateRecLabel').textContent = recLabel;
    document.getElementById('debateRecLabel').className = 'text-xl font-bold ' + scoreColor;
    document.getElementById('debateConsensus').textContent = '共识度: ' + consensus;
    document.getElementById('debateOverallScore').textContent = score > 0 ? '+' + score : score;
    document.getElementById('debateOverallScore').className = 'text-3xl font-bold ' + scoreColor;

    // 入场建议
    const sugDiv = document.getElementById('debateSuggestions');
    const suggestions = synthesis.entry_suggestions || [];
    if (suggestions.length === 0) {
        sugDiv.innerHTML = '<div class="text-gray-400 text-sm">暂无具体建议</div>';
        return;
    }

    let html = '';
    for (const sug of suggestions) {
        const actionColor = sug.action === 'Sell Put' ? 'green' : sug.action === '减仓/对冲' ? 'red' : 'yellow';
        html += `<div class="bg-gray-800/50 rounded-lg p-3 border border-${actionColor}-500/20">`;
        html += `<div class="flex items-center justify-between mb-1">`;
        html += `<span class="text-${actionColor}-400 font-semibold text-sm">${safeHTML(sug.action)}</span>`;
        if (sug.apr) html += `<span class="text-xs text-gray-400">APR ${sug.apr}% | 胜率 ${sug.win_rate}%</span>`;
        html += `</div>`;
        if (sug.strike) {
            html += `<div class="grid grid-cols-2 md:grid-cols-4 gap-2 mt-2 text-xs">`;
            html += `<div class="bg-gray-900/40 rounded px-2 py-1"><span class="text-gray-500">行权价</span><div class="font-bold text-white">$${sug.strike.toLocaleString()}</div></div>`;
            html += `<div class="bg-gray-900/40 rounded px-2 py-1"><span class="text-gray-500">权利金</span><div class="font-bold text-white">$${sug.premium}</div></div>`;
            html += `<div class="bg-gray-900/40 rounded px-2 py-1"><span class="text-gray-500">DTE</span><div class="font-bold text-white">${sug.dte}天</div></div>`;
            html += `<div class="bg-gray-900/40 rounded px-2 py-1"><span class="text-gray-500">单次ROI</span><div class="font-bold text-${actionColor}-400">${sug.roi_per_trade}%</div></div>`;
            html += `</div>`;
        }
        if (sug.reason) html += `<div class="text-xs text-gray-400 mt-2">${safeHTML(sug.reason)}</div>`;
        if (sug.具体操作 && Array.isArray(sug.具体操作)) {
            html += '<ul class="text-xs text-gray-300 mt-2 list-disc list-inside space-y-1">';
            for (const op of sug.具体操作) html += `<li>${safeHTML(op)}</li>`;
            html += '</ul>';
        }
        html += `</div>`;
    }
    sugDiv.innerHTML = html;
}

function renderDebateAgents(reports) {
    const container = document.getElementById('debateAgentCards');
    let html = '';

    const agentColors = {
        '🐂 多头分析师': { bg: 'green', border: 'green', icon: '🐂' },
        '🐻 空头分析师': { bg: 'red', border: 'red', icon: '🐻' },
        '📊 波动率分析师': { bg: 'blue', border: 'blue', icon: '📊' },
        '🐋 资金流向分析师': { bg: 'purple', border: 'purple', icon: '🐋' },
        '🛡️ 风险官': { bg: 'yellow', border: 'yellow', icon: '🛡️' },
    };

    for (const r of reports) {
        const colors = agentColors[r.name] || { bg: 'gray', border: 'gray', icon: '🤖' };
        const score = r.score || 0;
        const scoreColor = score > 20 ? 'text-green-400' : score > 0 ? 'text-emerald-300' : score > -20 ? 'text-yellow-400' : 'text-red-400';
        const barWidth = Math.min(100, Math.abs(score));
        const barColor = score > 0 ? 'bg-green-500' : score < 0 ? 'bg-red-500' : 'bg-gray-500';

        html += `<div class="card-glass rounded-xl p-4 border-l-4 border-${colors.border}-500/60 metric-card">`;
        // Header
        html += `<div class="flex items-center justify-between mb-3">`;
        html += `<div class="flex items-center gap-2"><span class="text-xl">${colors.icon}</span><span class="font-semibold text-sm">${safeHTML(r.name)}</span></div>`;
        html += `<span class="text-lg font-bold ${scoreColor}">${score > 0 ? '+' : ''}${score}</span>`;
        html += `</div>`;
        // Verdict
        html += `<div class="mb-3"><span class="text-xs px-2 py-0.5 rounded-full bg-${colors.bg}-500/20 text-${colors.bg}-300 font-medium">${safeHTML(r.verdict)}</span>`;
        html += `<span class="text-xs text-gray-500 ml-2">置信度 ${r.confidence}%</span></div>`;
        // Score bar
        html += `<div class="w-full h-2 bg-gray-700 rounded-full mb-3 overflow-hidden"><div class="h-full ${barColor} rounded-full transition-all" style="width: ${barWidth}%"></div></div>`;
        // Key points
        html += `<ul class="text-xs text-gray-300 space-y-1.5">`;
        for (const pt of (r.key_points || [])) {
            html += `<li class="flex items-start gap-1.5"><span class="text-gray-500 mt-0.5">•</span><span>${safeHTML(pt)}</span></li>`;
        }
        html += `</ul>`;
        // Extra data
        const extra = r.data || {};
        const showKeys = Object.entries(extra).filter(([k, v]) => typeof v !== 'object' && v !== '' && v !== 0);
        if (showKeys.length > 0) {
            html += `<div class="mt-3 pt-2 border-t border-gray-700/50 grid grid-cols-2 gap-1.5 text-[10px]">`;
            for (const [k, v] of showKeys.slice(0, 6)) {
                const label = {avg_apr:'平均APR', avg_win_rate:'平均胜率', best_apr:'最佳APR', contract_count:'合约数',
                    dvol:'DVOL', z_score:'Z-Score', percentile_7d:'IV百分位', regime:'波动率体制', skew:'IV偏度',
                    pcr:'PCR', whale_trades:'超大单', buy_pct:'买入占比', daily_var_pct:'日VaR%',
                    recommended_position_pct:'建议仓位%', vol_trend:'波动率趋势'}[k] || k;
                html += `<div><span class="text-gray-500">${label}</span>: <span class="text-gray-300 font-medium">${typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(1)) : v}</span></div>`;
            }
            html += `</div>`;
        }
        html += `</div>`;
    }
    container.innerHTML = html;
}

function renderDebateMarketSummary(summary, data) {
    const grid = document.getElementById('debateMarketGrid');
    const spot = summary.spot || 0;
    const dvol = summary.dvol || 0;
    const riskLabel = summary.risk_label || '';
    const tradesCount = summary.large_trades_count || 0;
    const contractsCount = summary.contracts_count || 0;
    const dvolSignal = summary.dvol_signal || '';

    let html = '';
    html += _summaryCard('现货价格', spot > 0 ? '$' + spot.toLocaleString() : '--', 'text-orange-400');
    html += _summaryCard('DVOL', dvol > 0 ? dvol.toFixed(1) + '%' : '--', 'text-blue-400', dvolSignal);
    html += _summaryCard('风险状态', riskLabel || '--', '');
    html += _summaryCard('数据', `${tradesCount} 大单 | ${contractsCount} 合约`, 'text-gray-300');

    if (data.errors && data.errors.length > 0) {
        html += _summaryCard('数据警告', `${data.errors.length} 项`, 'text-yellow-400');
    }

    grid.innerHTML = html;
}

function _summaryCard(label, value, color, sub) {
    return `<div class="p-2 bg-gray-900/40 rounded-lg">
        <div class="text-[10px] text-gray-500 mb-1">${label}</div>
        <div class="text-sm font-bold ${color || 'text-white'}">${safeHTML(value)}</div>
        ${sub ? `<div class="text-[10px] text-gray-500 mt-0.5">${safeHTML(sub)}</div>` : ''}
    </div>`;
}

function renderDebateErrors(errors) {
    const el = document.getElementById('debateErrors');
    if (!errors || errors.length === 0) {
        el.classList.add('hidden');
        return;
    }
    el.classList.remove('hidden');
    el.innerHTML = '<div class="font-semibold mb-1">⚠️ 数据获取警告</div>' +
        errors.map(e => `<div>• ${safeHTML(e)}</div>`).join('');
}

// 初始化辩论模块
initDebateSection();


// ============================================================
// IV 波动率微笑图 + Greeks 风险矩阵
// ============================================================

async function loadIVSmile() {
    const container = document.getElementById('ivSmileChart');
    if (!container) return;
    container.innerHTML = '<div class="text-gray-400 text-sm py-8 text-center">加载中...</div>';

    try {
        const currency = document.getElementById('ivSmileCurrency')?.value || 'BTC';
        const resp = await safeFetch(`${API_BASE}/api/charts/iv-smile?currency=${currency}`);
        const data = await resp.json();
        if (data.error) {
            container.innerHTML = `<div class="text-yellow-400 text-sm py-4">${safeHTML(data.error)}</div>`;
            return;
        }

        const smiles = data.smiles || {};
        const spot = data.spot || 0;
        let html = '';

        for (const [key, smile] of Object.entries(smiles)) {
            const all = smile.all || [];
            if (all.length === 0) continue;

            // 找到 IV 范围
            const minIv = Math.min(...all.map(p => p.iv));
            const maxIv = Math.max(...all.map(p => p.iv));
            const ivRange = maxIv - minIv || 1;

            html += `<div class="mb-6">
                <div class="text-sm text-gray-400 mb-2">到期 ${smile.dte} 天 (Spot: $${spot.toLocaleString()})</div>
                <div class="relative h-40 bg-gray-800/20 rounded-lg p-2">
                    <div class="flex items-end justify-center gap-1 h-full">`;

            // 按 strike 排序，画柱状图
            const sorted = [...all].sort((a, b) => a.strike - b.strike);
            const barWidth = Math.max(8, Math.min(24, 300 / sorted.length));

            for (const p of sorted) {
                const heightPct = Math.max(10, ((p.iv - minIv) / ivRange) * 80 + 10);
                const isPut = p.type === 'P';
                const isATM = Math.abs(p.strike - spot) / spot < 0.02;
                const color = isATM ? 'bg-yellow-400' : isPut ? 'bg-green-500' : 'bg-blue-500';
                const opacity = p.oi > 100 ? 'opacity-100' : 'opacity-60';

                html += `<div class="group relative flex flex-col items-center justify-end" style="width:${barWidth}px; height:100%;">
                    <div class="${color} ${opacity} rounded-t w-full transition-all hover:opacity-100 hover:brightness-110"
                         style="height:${heightPct}%; min-height: 4px;"></div>
                    <div class="hidden group-hover:block absolute bottom-full mb-1 bg-gray-900 border border-gray-600 rounded p-1.5 text-xs whitespace-nowrap z-10 shadow-lg">
                        <div class="font-medium">K=$${p.strike.toLocaleString()} | IV=${p.iv}%</div>
                        <div class="text-gray-400">${p.type} | OI=${Math.round(p.oi)} | 距现货${p.moneyness > 0 ? '+' : ''}${p.moneyness}%</div>
                    </div>
                </div>`;
            }

            html += `</div>
                </div>
                <div class="flex justify-between text-xs text-gray-500 mt-1 px-2">
                    <span>$${sorted[0]?.strike?.toLocaleString() || ''}</span>
                    <span class="text-yellow-400 font-medium">ATM $${spot.toLocaleString()}</span>
                    <span>$${sorted[sorted.length-1]?.strike?.toLocaleString() || ''}</span>
                </div>
                <div class="flex justify-center gap-4 mt-2 text-xs">
                    <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-green-500"></span> Put</span>
                    <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-blue-500"></span> Call</span>
                    <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-yellow-400"></span> ATM</span>
                </div>
            </div>`;
        }

        container.innerHTML = html || '<div class="text-gray-400 text-sm py-4">暂无微笑数据</div>';
    } catch (e) {
        container.innerHTML = `<div class="text-red-400 text-sm py-4">加载失败: ${e.message}</div>`;
    }
}

async function loadGreeksSummary() {
    const container = document.getElementById('greeksGrid');
    if (!container) return;
    container.innerHTML = '<div class="text-gray-400 text-sm py-4 text-center">加载中...</div>';

    try {
        const currency = document.getElementById('greeksCurrency')?.value || 'BTC';
        const resp = await safeFetch(`${API_BASE}/api/charts/greeks-summary?currency=${currency}`);
        const data = await resp.json();
        if (data.error) {
            container.innerHTML = `<div class="text-yellow-400 text-sm">${safeHTML(data.error)}</div>`;
            return;
        }

        // 兼容新旧 API 格式
        const g = data.greeks_per_contract || data.greeks || {};
        const total = data.total_greeks_exposure || {};
        const risk = data.risk_assessment || {};
        const spot = data.spot || 0;
        const totalOi = data.total_oi || 0;

        // 基于单合约 Greeks 计算颜色阈值
        const deltaColor = Math.abs(g.delta) > 0.5 ? 'text-red-400' : Math.abs(g.delta) > 0.2 ? 'text-yellow-400' : 'text-green-400';
        const thetaColor = g.theta < -0.5 ? 'text-red-400' : g.theta < -0.1 ? 'text-yellow-400' : 'text-green-400';

        container.innerHTML = `
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                <div class="bg-gray-800/50 rounded-lg p-3 text-center">
                    <div class="text-xs text-gray-400">Delta (Δ)</div>
                    <div class="text-xl font-bold ${deltaColor}">${g.delta?.toFixed(4) || 0}</div>
                    <div class="text-xs text-gray-500">每合约价格敏感度</div>
                </div>
                <div class="bg-gray-800/50 rounded-lg p-3 text-center">
                    <div class="text-xs text-gray-400">Gamma (Γ)</div>
                    <div class="text-xl font-bold text-blue-400">${g.gamma?.toFixed(6) || 0}</div>
                    <div class="text-xs text-gray-500">Delta 变化率</div>
                </div>
                <div class="bg-gray-800/50 rounded-lg p-3 text-center">
                    <div class="text-xs text-gray-400">Theta (Θ)</div>
                    <div class="text-xl font-bold ${thetaColor}">$${g.theta?.toFixed(4) || 0}</div>
                    <div class="text-xs text-gray-500">每日时间损耗</div>
                </div>
                <div class="bg-gray-800/50 rounded-lg p-3 text-center">
                    <div class="text-xs text-gray-400">Vega (V)</div>
                    <div class="text-xl font-bold text-purple-400">$${g.vega?.toFixed(4) || 0}</div>
                    <div class="text-xs text-gray-500">IV 敏感度</div>
                </div>
            </div>
            <div class="bg-gray-800/30 rounded-lg p-3 mb-3">
                <div class="text-sm font-medium text-gray-300 mb-2">总风险敞口 (OI 加权)</div>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                    <div class="text-center">
                        <div class="text-gray-400">总 Delta</div>
                        <div class="text-lg font-bold ${total.delta > 0 ? 'text-green-400' : 'text-red-400'}">${total.delta?.toLocaleString() || 0}</div>
                    </div>
                    <div class="text-center">
                        <div class="text-gray-400">总 Gamma</div>
                        <div class="text-lg font-bold text-blue-400">${total.gamma?.toFixed(4) || 0}</div>
                    </div>
                    <div class="text-center">
                        <div class="text-gray-400">总 Theta</div>
                        <div class="text-lg font-bold ${total.theta > 0 ? 'text-green-400' : 'text-red-400'}">$${total.theta?.toLocaleString() || 0}</div>
                    </div>
                    <div class="text-center">
                        <div class="text-gray-400">总 Vega</div>
                        <div class="text-lg font-bold text-purple-400">$${total.vega?.toLocaleString() || 0}</div>
                    </div>
                </div>
            </div>
            <div class="bg-gray-800/30 rounded-lg p-3">
                <div class="text-sm font-medium text-gray-300 mb-2">情景分析</div>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-2 text-xs">
                    <div class="flex justify-between">
                        <span class="text-gray-400">若 ${currency} 下跌 10%</span>
                        <span class="${risk.delta_pnl_if_down_10pct < 0 ? 'text-red-400' : 'text-green-400'}">${risk.delta_pnl_if_down_10pct < 0 ? '' : '+'}$${risk.delta_pnl_if_down_10pct?.toLocaleString() || 0}</span>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-gray-400">若 ${currency} 上涨 10%</span>
                        <span class="${risk.delta_pnl_if_up_10pct > 0 ? 'text-green-400' : 'text-red-400'}">+${risk.delta_pnl_if_up_10pct?.toLocaleString() || 0}</span>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-gray-400">若 IV 上升 5%</span>
                        <span class="text-green-400">+$${risk.vega_pnl_if_iv_up_5pct?.toLocaleString() || 0}</span>
                    </div>
                </div>
                <div class="mt-2 text-xs text-gray-500">
                    合约: ${data.contract_count}个 (${data.put_count} Put / ${data.call_count} Call) | 
                    总 OI: ${totalOi.toLocaleString() || 0} | 
                    Delta 风险: ${risk.delta_risk || '未知'}
                </div>
            </div>`;
    } catch (e) {
        container.innerHTML = `<div class="text-red-400 text-sm">加载失败: ${e.message}</div>`;
    }
}

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    loadIVSmile();
    loadGreeksSummary();
});
