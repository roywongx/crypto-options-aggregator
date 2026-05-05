import { $, safeHTML, STRATEGY_PRESETS, API_BASE, API_TIMEOUT_MS, FETCH_MAX_RETRIES, TABLE_PAGE_SIZE, getApiKey, safeFetch, getFieldName, getRecommendationLabel, getRecommendationColor } from './utils.js';
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

// Kraken Chart.js theme
const KRAKEN_CHART_THEME = {
    purple: '#7132f5',
    purpleLight: 'rgba(113,50,245,0.2)',
    positive: '#149e61',
    positiveLight: 'rgba(20,158,97,0.2)',
    negative: '#ef4444',
    negativeLight: 'rgba(239,68,68,0.2)',
    grid: 'rgba(71,73,85,0.15)',
    text: '#9497a9',
    textMuted: '#686b82',
    surface: '#22232e',
};
window.KRAKEN_CHART_THEME = KRAKEN_CHART_THEME;

// Toast notification system
function showToast(message, type = 'info', duration = 4000) {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const toast = document.createElement('div');
    const icons = { success: 'fa-check-circle', warning: 'fa-exclamation-triangle', error: 'fa-times-circle', info: 'fa-info-circle' };
    const colors = { success: 'border-l-4 border-[#149e61] bg-[#149e61]/10', warning: 'border-l-4 border-[#f59e0b] bg-[#f59e0b]/10', error: 'border-l-4 border-[#ef4444] bg-[#ef4444]/10', info: 'border-l-4 border-[#7132f5] bg-[#7132f5]/10' };
    const textColors = { success: 'text-[#149e61]', warning: 'text-[#f59e0b]', error: 'text-[#ef4444]', info: 'text-[#7132f5]' };
    toast.className = `flex items-center gap-3 px-4 py-3 rounded-lg shadow-lg ${colors[type]} backdrop-blur-sm text-sm text-[#e4e4e7] transform transition-all duration-300 translate-x-full opacity-0 pointer-events-auto`;
    toast.innerHTML = `<i class="fas ${icons[type]} ${textColors[type]}"></i><span>${safeHTML(message)}</span>`;
    container.appendChild(toast);
    requestAnimationFrame(() => { toast.classList.remove('translate-x-full', 'opacity-0'); toast.classList.add('translate-x-0', 'opacity-100'); });
    setTimeout(() => {
        toast.classList.remove('translate-x-0', 'opacity-100');
        toast.classList.add('translate-x-full', 'opacity-0');
        setTimeout(() => toast.remove(), 300);
    }, duration);
}
window.showToast = showToast;

// Density mode
let _densityMode = 'standard';
function setDensityMode(mode) {
    _densityMode = mode;
    const body = document.body;
    body.classList.remove('density-compact', 'density-pro');
    if (mode === 'compact') body.classList.add('density-compact');
    else if (mode === 'pro') body.classList.add('density-pro');
    document.querySelectorAll('.density-btn').forEach(btn => {
        if (btn.dataset.density === mode) {
            btn.classList.add('bg-[#7132f5]', 'text-white');
            btn.classList.remove('bg-[#22232e]', 'text-[#9497a9]');
        } else {
            btn.classList.remove('bg-[#7132f5]', 'text-white');
            btn.classList.add('bg-[#22232e]', 'text-[#9497a9]');
        }
    });
    localStorage.setItem('densityMode', mode);
}
window.setDensityMode = setDensityMode;

// Risk change detection
let _lastRiskLevel = null;
function checkRiskChange(riskLevel) {
    if (!riskLevel || riskLevel === _lastRiskLevel) return;
    if (_lastRiskLevel !== null) {
        const levelMap = { 'LOW': '低', 'MEDIUM': '中', 'HIGH': '高', 'CRITICAL': '极高', 'NORMAL': '正常', 'NEAR_FLOOR': '临近底', 'ADVERSE': '逆境', 'PANIC': '恐慌' };
        const from = levelMap[_lastRiskLevel] || _lastRiskLevel;
        const to = levelMap[riskLevel] || riskLevel;
        const type = ['HIGH', 'CRITICAL', 'PANIC', 'ADVERSE'].includes(riskLevel) ? 'warning' : 'info';
        showToast(`风险等级变化: ${from} → ${to}`, type, 6000);
    }
    _lastRiskLevel = riskLevel;
}
window.checkRiskChange = checkRiskChange;

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

    // Restore density mode
    const savedDensity = localStorage.getItem('densityMode') || 'standard';
    setDensityMode(savedDensity);

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

    // Old payoff event listeners removed — replaced by analysis center (setAnalysisMode, anaCalcPayoff, anaCalcWheel)
    // const modeRollBtn = document.getElementById('modeRollBtn');
    // if (modeRollBtn) modeRollBtn.addEventListener('click', () => setCalcMode('roll'));
    // const modeNewBtn = document.getElementById('modeNewBtn');
    // if (modeNewBtn) modeNewBtn.addEventListener('click', () => setCalcMode('new'));
    // const modeGridBtn = document.getElementById('modeGridBtn');
    // if (modeGridBtn) modeGridBtn.addEventListener('click', () => setCalcMode('grid'));

    // const scSubmitBtn = document.getElementById('scSubmitBtn');
    // if (scSubmitBtn) scSubmitBtn.addEventListener('click', submitStrategyCalc);

    // Strategy Recommendation Center v2 event listeners
    const strModeNewBtn = document.getElementById('modeNewBtn');
    if (strModeNewBtn) strModeNewBtn.addEventListener('click', () => setStrategyMode('new'));
    const strModeRollBtn = document.getElementById('modeRollBtn');
    if (strModeRollBtn) strModeRollBtn.addEventListener('click', () => setStrategyMode('roll'));
    const strModeWheelBtn = document.getElementById('modeWheelBtn');
    if (strModeWheelBtn) strModeWheelBtn.addEventListener('click', () => setStrategyMode('wheel'));
    const strModeGridBtn = document.getElementById('modeGridBtn');
    if (strModeGridBtn) strModeGridBtn.addEventListener('click', () => setStrategyMode('grid'));
    const strDirPutBtn = document.getElementById('strDirPut');
    if (strDirPutBtn) strDirPutBtn.addEventListener('click', () => setStrategyDirection('PUT'));
    const strDirCallBtn = document.getElementById('strDirCall');
    if (strDirCallBtn) strDirCallBtn.addEventListener('click', () => setStrategyDirection('CALL'));
    const strSubmitBtn = document.getElementById('strSubmitBtn');
    if (strSubmitBtn) strSubmitBtn.addEventListener('click', fetchStrategyRecommend);

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

    const tradesCurrency = document.getElementById('tradesCurrency');
    if (tradesCurrency) tradesCurrency.addEventListener('change', loadWindAnalysis);
    const tradesDays = document.getElementById('tradesDays');
    if (tradesDays) tradesDays.addEventListener('change', loadWindAnalysis);

    // Restore strategy preferences
    try {
        const savedMode = localStorage.getItem('strategy_mode');
        if (savedMode && ['new', 'roll', 'wheel', 'grid'].includes(savedMode)) {
            setStrategyMode(savedMode);
        }
        const savedDir = localStorage.getItem('strategy_direction');
        if (savedDir && ['PUT', 'CALL'].includes(savedDir)) {
            setStrategyDirection(savedDir);
        }
    } catch(_) {}
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
            btn.classList.add('bg-[#7132f5]', 'text-white');
        } else {
            btn.classList.add('bg-[#22232e]/50', 'hover:bg-[#2a2b38]');
            btn.classList.remove('bg-[#7132f5]', 'text-white');
        }
    });
    if (chartType === 'dvol') loadDvolChartData();
    else if (chartType === 'pcr') loadPcrChart(document.getElementById('currencySelect')?.value || 'BTC', hours);
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
            showToast(`扫描完成, 发现 ${result.contracts_count} 个机会`, 'success');
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

    if (!rollBtn || !newBtn || !gridBtn || !rollFields || !newFields || !gridFields) return;

    rollBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-gray-700/50 border border-gray-600 text-gray-400 hover:bg-gray-600/50';
    newBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-gray-700/50 border border-gray-600 text-gray-400 hover:bg-gray-600/50';
    gridBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-gray-700/50 border border-gray-600 text-gray-400 hover:bg-gray-600/50';

    rollFields.classList.add('hidden');
    newFields.classList.add('hidden');
    gridFields.classList.add('hidden');
    optionTypeSelect.disabled = false;

    if (mode === 'roll') {
        rollBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-[#7132f5]/20 border border-[#7132f5]/50 text-[#7132f5]';
        rollFields.classList.remove('hidden');
    } else if (mode === 'new') {
        newBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-[#7132f5]/20 border border-[#7132f5]/50 text-[#7132f5]';
        newFields.classList.remove('hidden');
    } else if (mode === 'grid') {
        gridBtn.className = 'px-4 py-2 rounded-lg font-medium text-sm transition-all bg-[#7132f5]/20 border border-[#7132f5]/50 text-[#7132f5]';
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
    wrapper.innerHTML = '<div class="text-center py-12 text-[#7132f5]"><i class="fas fa-spinner fa-spin text-3xl mb-2"></i><p>计算中...</p></div>';

    try {
        const response = await safeFetch(`${API_BASE}/api/strategy-calc`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params)
        });
        const result = await response.json();

        if (!result.success) {
            wrapper.innerHTML = `<div class="text-center py-12 text-[#ef4444]"><i class="fas fa-times-circle text-3xl mb-2"></i><p>${safeHTML(result.error || '计算失败')}</p></div>`;
        } else {
            displayStrategyCalcResult(result, wrapper);
        }
    } catch (error) {
        wrapper.innerHTML = `<div class="text-center py-12 text-[#ef4444]"><i class="fas fa-times-circle text-3xl mb-2"></i><p>错误: ${safeHTML(error.message)}</p></div>`;
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
        wrapper.innerHTML = `<div class="text-center py-12 text-[#f59e0b]">
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
        html += `<tr class="hover:bg-white/5 transition ${isBest ? 'bg-[#149e61]/10' : ''}">
            <td class="py-3 px-2">${isBest ? '<i class="fas fa-crown text-[#f59e0b]"></i>' : idx + 1}</td>
            <td class="py-3 px-2"><span class="font-mono text-white">${safeHTML(plan.symbol)}</span><br><span class="text-[10px] text-gray-500">${safeHTML(plan.platform)}</span></td>
            <td class="py-3 px-2 text-right font-mono text-[#7132f5]">${plan.strike?.toLocaleString()}</td>
            <td class="py-3 px-2 text-center">${plan.dte}</td>
            <td class="py-3 px-2 text-right">${metrics.delta?.toFixed(3)}</td>
            <td class="py-3 px-2 text-right text-[#7132f5]">${metrics.win_rate?.toFixed(1)}%</td>
            ${result.mode === 'roll' ? `<td class="py-3 px-2 text-right">${metrics.new_qty}</td><td class="py-3 px-2 text-right text-[#7132f5]">$${metrics.net_credit?.toFixed(2)}</td>` : `<td class="py-3 px-2 text-right">$${metrics.margin_required?.toFixed(2)}</td><td class="py-3 px-2 text-right text-[#149e61]">$${metrics.gross_credit?.toFixed(2)}</td>`}
            <td class="py-3 px-2 text-right text-[#f59e0b] font-bold">${metrics.roi?.toFixed(1)}%</td>
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
        <h4 class="text-xs font-semibold text-[#7132f5] mb-2"><i class="fas fa-chart-area mr-1"></i>波动率信号</h4>
        <div class="flex gap-4 text-xs">
            <span class="text-gray-400">信号: <span class="text-white font-bold">${safeHTML(result.dvol_signal || 'N/A')}</span></span>
            <span class="text-gray-400">推荐比例: <span class="text-white">${safeHTML(result.recommended_ratio || 'N/A')}</span></span>
            <span class="text-gray-400">总潜在权利金: <span class="text-[#149e61] font-bold">$${result.total_potential_premium?.toFixed(2) || '0'}</span></span>
        </div>
    </div>`;

    if (putLevels.length > 0) {
        html += `<h4 class="text-sm font-semibold text-[#149e61]"><i class="fas fa-arrow-down mr-1"></i>Put 网格档位 (${putLevels.length} 个)</h4>`;
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
            const colorMap = { 'BEST': 'text-[#149e61]', 'GOOD': 'text-[#7132f5]', 'OK': 'text-[#f59e0b]', 'CAUTION': 'text-[#7132f5]', 'SKIP': 'text-[#ef4444]' };
            const color = colorMap[recLevel] || 'text-gray-400';
            
            html += `<tr class="hover:bg-white/5 transition">
                <td class="py-2 px-2"><span class="${color} font-bold">${safeHTML(recLevel)}</span></td>
                <td class="py-2 px-2 text-right font-mono text-[#7132f5]">${level.strike?.toLocaleString()}</td>
                <td class="py-2 px-2 text-center">${level.dte}</td>
                <td class="py-2 px-2 text-right text-[#149e61]">$${level.premium_usd?.toFixed(2)}</td>
                <td class="py-2 px-2 text-right text-[#7132f5]">${metrics.win_rate?.toFixed(1)}%</td>
                <td class="py-2 px-2 text-right text-[#f59e0b]">${metrics.apr?.toFixed(1)}%</td>
                <td class="py-2 px-2 text-right text-pink-400">${metrics.theta_decay?.toFixed(2) || 'N/A'}</td>
                <td class="py-2 px-2 text-right">${score?.toFixed(4)}</td>
                <td class="py-2 px-2 text-gray-400">${safeHTML(metrics.reason || '')}</td>
            </tr>`;
        });

        html += '</tbody></table></div>';
    }

    if (callLevels.length > 0) {
        html += `<h4 class="text-sm font-semibold text-[#ef4444] mt-4"><i class="fas fa-arrow-up mr-1"></i>Call 网格档位 (${callLevels.length} 个)</h4>`;
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
            const colorMap = { 'BEST': 'text-[#149e61]', 'GOOD': 'text-[#7132f5]', 'OK': 'text-[#f59e0b]', 'CAUTION': 'text-[#7132f5]', 'SKIP': 'text-[#ef4444]' };
            const color = colorMap[recLevel] || 'text-gray-400';
            
            html += `<tr class="hover:bg-white/5 transition">
                <td class="py-2 px-2"><span class="${color} font-bold">${safeHTML(recLevel)}</span></td>
                <td class="py-2 px-2 text-right font-mono text-[#7132f5]">${level.strike?.toLocaleString()}</td>
                <td class="py-2 px-2 text-center">${level.dte}</td>
                <td class="py-2 px-2 text-right text-[#149e61]">$${level.premium_usd?.toFixed(2)}</td>
                <td class="py-2 px-2 text-right text-[#7132f5]">${metrics.win_rate?.toFixed(1)}%</td>
                <td class="py-2 px-2 text-right text-[#f59e0b]">${metrics.apr?.toFixed(1)}%</td>
                <td class="py-2 px-2 text-right text-pink-400">${metrics.theta_decay?.toFixed(2) || 'N/A'}</td>
                <td class="py-2 px-2 text-right">${score?.toFixed(4)}</td>
                <td class="py-2 px-2 text-gray-400">${safeHTML(metrics.reason || '')}</td>
            </tr>`;
        });

        html += '</tbody></table></div>';
    }

    if (putLevels.length === 0 && callLevels.length === 0) {
        html += '<div class="text-center py-12 text-[#f59e0b]"><i class="fas fa-search text-3xl mb-2 opacity-50"></i><p>未找到符合条件的网格配置</p></div>';
    }

    html += '</div>';
    wrapper.innerHTML = html;
}

// ========== 策略推荐中心 v2 ==========
let _strMode = 'new';
let _strDirection = 'PUT';

window.setStrategyMode = function(mode) {
    _strMode = mode;
    const btns = {new: 'modeNewBtn', roll: 'modeRollBtn', wheel: 'modeWheelBtn', grid: 'modeGridBtn'};
    const colors = {new: 'bg-[#7132f5]', roll: 'bg-[#7132f5]', wheel: 'bg-[#149e61]', grid: 'bg-[#7132f5]'};
    Object.entries(btns).forEach(([k, id]) => {
        const el = document.getElementById(id);
        if (el) el.className = `px-3 py-1.5 rounded-lg text-sm font-medium ${k === mode ? colors[k] + ' text-white' : 'bg-gray-700 text-gray-300'}`;
    });
    const rollFields = document.getElementById('strRollFields');
    const gridFields = document.getElementById('strGridFields');
    if (rollFields) rollFields.classList.toggle('hidden', mode !== 'roll');
    if (gridFields) gridFields.classList.toggle('hidden', mode !== 'grid');
    try { localStorage.setItem('strategy_mode', mode); } catch(_) {}
};

window.setStrategyDirection = function(dir) {
    _strDirection = dir;
    const putBtn = document.getElementById('strDirPut');
    const callBtn = document.getElementById('strDirCall');
    if (putBtn) putBtn.className = `flex-1 px-2 py-1.5 rounded-lg text-sm font-medium ${dir === 'PUT' ? 'bg-[#149e61] text-white' : 'bg-gray-700 text-gray-300'}`;
    if (callBtn) callBtn.className = `flex-1 px-2 py-1.5 rounded-lg text-sm font-medium ${dir === 'CALL' ? 'bg-[#ef4444] text-white' : 'bg-gray-700 text-gray-300'}`;
    try { localStorage.setItem('strategy_direction', dir); } catch(_) {}
};

window.fetchStrategyRecommend = async function() {
    const loading = document.getElementById('strLoading');
    const empty = document.getElementById('strEmpty');
    const wrapper = document.getElementById('strResultsWrapper');
    const dvolWarn = document.getElementById('strDvolWarning');

    if (loading) loading.classList.remove('hidden');
    if (empty) empty.classList.add('hidden');
    if (wrapper) wrapper.innerHTML = '';
    if (dvolWarn) dvolWarn.classList.add('hidden');

    const body = {
        currency: document.getElementById('strCurrency')?.value || 'BTC',
        mode: _strMode,
        option_type: _strDirection,
        capital: parseFloat(document.getElementById('strCapital')?.value) || 50000,
        max_results: 10,
        grid_levels: parseInt(document.getElementById('strGridLevels')?.value) || 5,
        grid_interval_pct: parseFloat(document.getElementById('strGridInterval')?.value) || 3.0,
        overrides: {},
    };

    const maxDelta = parseFloat(document.getElementById('strMaxDelta')?.value);
    const minDte = parseInt(document.getElementById('strMinDte')?.value);
    const maxDte = parseInt(document.getElementById('strMaxDte')?.value);
    const minApr = parseFloat(document.getElementById('strMinApr')?.value);
    if (!isNaN(maxDelta)) body.overrides.max_delta = maxDelta;
    if (!isNaN(minDte)) body.overrides.min_dte = minDte;
    if (!isNaN(maxDte)) body.overrides.max_dte = maxDte;
    if (!isNaN(minApr)) body.overrides.min_apr = minApr;
    if (Object.keys(body.overrides).length === 0) body.overrides = null;

    if (_strMode === 'roll') {
        body.old_strike = parseFloat(document.getElementById('strOldStrike')?.value) || null;
        body.old_expiry = document.getElementById('strOldExpiry')?.value || null;
        if (!body.old_strike) {
            if (loading) loading.classList.add('hidden');
            showAlert('滚仓模式必须填写当前行权价', 'error');
            return;
        }
    }

    try {
        const res = await safeFetch('/api/strategy/recommend', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (loading) loading.classList.add('hidden');

        if (!data.success || !data.recommendations?.length) {
            if (empty) empty.classList.remove('hidden');
            const msg = document.getElementById('strEmptyMessage');
            if (msg) msg.textContent = data.filter_summary?.message || '当前条件下无可用合约';
            if (wrapper) wrapper.innerHTML = renderStrFilterSummary(data.filter_summary);
            return;
        }

        const z = data.dvol_snapshot?.z_score || 0;
        if (Math.abs(z) > 2 && dvolWarn) {
            dvolWarn.classList.remove('hidden');
            dvolWarn.innerHTML = '<i class="fas fa-exclamation-triangle mr-1"></i> DVOL z-score ' + z.toFixed(1) + ' — 当前处于极端波动区间，建议谨慎操作';
        }

        renderStrategyResults(data);

        if (window._strAutoRefreshTimer) clearInterval(window._strAutoRefreshTimer);
        window._strAutoRefreshTimer = setInterval(() => {
            if (!document.getElementById('strResultsWrapper')?.querySelector('table')) {
                clearInterval(window._strAutoRefreshTimer);
                return;
            }
            fetchStrategyRecommend();
        }, 60000);
    } catch (e) {
        console.error('[Strategy] Error:', e);
        if (loading) loading.classList.add('hidden');
        showAlert('策略推荐请求失败: ' + e.message, 'error');
    }
};

function renderStrFilterSummary(summary) {
    if (!summary) return '';
    let html = '<div class="mb-3 p-3 bg-gray-800/50 rounded-lg text-xs text-gray-400">';
    html += '<div class="flex flex-wrap gap-3 mb-2">';
    html += '<span>总合约: <b class="text-white">' + (summary.total_contracts || 0) + '</b></span>';
    html += '<span>→ 硬性过滤: <b class="text-white">' + (summary.after_hard_filter || 0) + '</b></span>';
    html += '<span>→ DVOL过滤: <b class="text-white">' + (summary.after_dvol_filter || 0) + '</b></span>';
    html += '<span>→ 策略过滤: <b class="text-[#7132f5]">' + (summary.after_strategy_filter || 0) + '</b></span>';
    html += '</div>';
    const adj = summary.dvol_adjustments || {};
    const adjEntries = Object.entries(adj).filter(([k]) => !k.startsWith('_'));
    if (adjEntries.length) {
        html += '<div class="text-gray-500">DVOL调整: ' + adjEntries.map(([k, v]) => k + ': ' + v).join(' | ') + '</div>';
    }
    if (adj._fallback) {
        html += '<div class="text-[#f59e0b] mt-1"><i class="fas fa-exclamation-triangle mr-1"></i>' + adj._fallback + '</div>';
    }
    if (summary.reason === 'no_contracts') {
        html += '<div class="text-[#ef4444] mt-1">' + safeHTML(summary.message || '') + '</div>';
    }
    html += '</div>';
    return html;
}

function renderStrategyResults(data) {
    const wrapper = document.getElementById('strResultsWrapper');
    if (!wrapper) return;

    let html = renderStrFilterSummary(data.filter_summary);

    html += '<div class="overflow-x-auto"><table class="w-full text-sm">';
    html += '<thead><tr class="text-gray-400 border-b border-gray-700">';
    html += '<th class="py-2 px-2 text-left">#</th>';
    html += '<th class="py-2 px-2 text-left">平台</th>';
    html += '<th class="py-2 px-2 text-left">方向</th>';
    html += '<th class="py-2 px-2 text-right">行权价</th>';
    html += '<th class="py-2 px-2 text-left">到期日</th>';
    html += '<th class="py-2 px-2 text-right">DTE</th>';
    html += '<th class="py-2 px-2 text-right">Delta</th>';
    html += '<th class="py-2 px-2 text-right">权利金</th>';
    html += '<th class="py-2 px-2 text-right">APR</th>';
    html += '<th class="py-2 px-2 text-right">持仓量</th>';
    html += '<th class="py-2 px-2 text-right">价差</th>';
    html += '<th class="py-2 px-2 text-right">评分</th>';
    html += '<th class="py-2 px-2 text-center">推荐</th>';
    html += '</tr></thead><tbody>';

    const recColors = {BEST:'text-[#149e61] bg-[#149e61]/10', GOOD:'text-[#7132f5] bg-[#7132f5]/10', OK:'text-[#9497a9] bg-[#22232e]/30', CAUTION:'text-[#f59e0b] bg-[#f59e0b]/10', SKIP:'text-[#ef4444] bg-[#ef4444]/10'};
    const recLabels = {BEST:'强烈推荐', GOOD:'推荐', OK:'可考虑', CAUTION:'谨慎', SKIP:'不推荐'};

    data.recommendations.forEach((r, i) => {
        const sc = r.scores || {};
        const color = recColors[sc.recommendation] || recColors.SKIP;
        const label = recLabels[sc.recommendation] || sc.recommendation;
        const rowBg = i === 0 ? 'bg-[#149e61]/10' : 'hover:bg-gray-800/50';

        html += '<tr class="border-b border-gray-800/50 ' + rowBg + ' cursor-pointer strategy-row">';
        html += '<td class="py-2 px-2">' + (i === 0 ? '1' : i + 1) + '</td>';
        html += '<td class="py-2 px-2">' + safeHTML(r.platform) + '</td>';
        const _isPut = r.option_type === 'P' || r.option_type === 'PUT' || r.option_type === 'put';
        html += '<td class="py-2 px-2">' + (_isPut ? '<span class="text-[#149e61]">PUT</span>' : '<span class="text-[#ef4444]">CALL</span>') + '</td>';
        html += '<td class="py-2 px-2 text-right font-mono">' + (r.strike || 0).toLocaleString() + '</td>';
        html += '<td class="py-2 px-2 text-xs">' + safeHTML(r.expiry || '') + '</td>';
        html += '<td class="py-2 px-2 text-right">' + r.dte + '</td>';
        html += '<td class="py-2 px-2 text-right">' + (r.delta || 0).toFixed(3) + '</td>';
        html += '<td class="py-2 px-2 text-right">$' + (r.premium_usd || 0).toLocaleString() + '</td>';
        html += '<td class="py-2 px-2 text-right">' + (r.apr || 0).toFixed(1) + '%</td>';
        html += '<td class="py-2 px-2 text-right">' + (r.open_interest || 0).toLocaleString() + '</td>';
        html += '<td class="py-2 px-2 text-right">' + (r.spread_pct || 0).toFixed(1) + '%</td>';
        html += '<td class="py-2 px-2 text-right font-mono">' + (sc.total || 0).toFixed(3) + '</td>';
        html += '<td class="py-2 px-2 text-center"><span class="px-2 py-0.5 rounded text-xs ' + color + '">' + label + '</span></td>';
        html += '</tr>';
        html += '<tr class="hidden detail-row"><td colspan="13" class="px-4 py-3 bg-gray-900/50">';
        html += '<div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">';
        html += '<div><span class="text-gray-500">EV评分:</span> <span class="text-white">' + (sc.ev || 0).toFixed(3) + '</span></div>';
        html += '<div><span class="text-gray-500">APR评分:</span> <span class="text-white">' + (sc.apr || 0).toFixed(3) + '</span></div>';
        html += '<div><span class="text-gray-500">流动性:</span> <span class="text-white">' + (sc.liquidity || 0).toFixed(3) + '</span></div>';
        html += '<div><span class="text-gray-500">Theta:</span> <span class="text-white">' + (sc.theta || 0).toFixed(3) + '</span></div>';
        html += '<div><span class="text-gray-500">保证金:</span> <span class="text-white">$' + (r.margin_required || 0).toLocaleString() + '</span></div>';
        html += '<div><span class="text-gray-500">资本效率:</span> <span class="text-white">' + (r.capital_efficiency || 0) + '%</span></div>';
        html += '<div><span class="text-gray-500">最大亏损:</span> <span class="text-[#ef4444]">$' + (r.risk?.max_loss || 0).toLocaleString() + '</span></div>';
        html += '<div><span class="text-gray-500">盈亏平衡:</span> <span class="text-white">' + (r.risk?.breakeven || 0).toLocaleString() + '</span></div>';
        html += '</div></td></tr>';
    });

    html += '</tbody></table></div>';
    wrapper.innerHTML = html;

    // Event delegation for row click (CSP-safe, no inline onclick)
    wrapper.querySelectorAll('.strategy-row').forEach(row => {
        row.addEventListener('click', () => {
            const detail = row.nextElementSibling;
            if (detail && detail.classList.contains('detail-row')) {
                detail.classList.toggle('hidden');
            }
        });
    });
}

window.toggleStrategyDetail = function(row) {
    const detail = row.nextElementSibling;
    if (detail && detail.classList.contains('detail-row')) {
        detail.classList.toggle('hidden');
    }
};

async function calculateRecovery() {
    showAlert('请使用策略计算器', 'info');
}

function displayRecoveryResult(result) {
    const recommended = result.recommended;
    const plans = result.plans || [];

    const recommendedDiv = document.getElementById('recommendedPlan');
    if (recommended) {
        const riskColor = recommended.risk_level === '低风险' ? 'text-[#149e61]' : recommended.risk_level === '中风险' ? 'text-[#f59e0b]' : recommended.risk_level === '高风险' ? 'text-[#7132f5]' : 'text-[#ef4444]';

        recommendedDiv.innerHTML = `
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div class="text-center"><div class="text-xs text-gray-400 mb-1">推荐合约</div><div class="font-mono font-semibold text-white">${safeHTML(recommended.symbol)}</div><div class="text-xs text-gray-500">${safeHTML(recommended.platform)}</div></div>
                <div class="text-center"><div class="text-xs text-gray-400 mb-1">卖出张数</div><div class="text-2xl font-bold text-[#7132f5]">${recommended.num_contracts} 张</div></div>
                <div class="text-center"><div class="text-xs text-gray-400 mb-1">所需保证金</div><div class="text-xl font-semibold text-white">$${recommended.total_margin.toLocaleString()}</div></div>
                <div class="text-center"><div class="text-xs text-gray-400 mb-1">预期净利润</div><div class="text-xl font-bold text-[#149e61]">+$${recommended.net_profit.toLocaleString()}</div><div class="text-xs ${riskColor}">${safeHTML(recommended.risk_level)}</div></div>
            </div>
            <div class="mt-3 pt-3 border-t border-[#149e61]/20 text-xs text-gray-400"><i class="fas fa-info-circle mr-1"></i>基于 ${recommended.apr.toFixed(1)}% APR，在 ${recommended.dte.toFixed(0)} 天内通过卖出 Put 期权获取权利金覆盖浮亏</div>
        `;
    }

    const tableBody = document.getElementById('recoveryPlansTable');
    if (plans.length > 0) {
        tableBody.innerHTML = plans.map((plan, index) => {
            const riskColor = plan.risk_level === '低风险' ? 'text-[#149e61]' : plan.risk_level === '中风险' ? 'text-[#f59e0b]' : plan.risk_level === '高风险' ? 'text-[#7132f5]' : 'text-[#ef4444]';
            const profitColor = plan.net_profit >= 0 ? 'text-[#149e61]' : 'text-[#ef4444]';

            return `<tr class="border-b border-gray-800/50 hover:bg-gray-800/30 transition ${index === 0 ? 'bg-[#149e61]/5' : ''}">
                <td class="py-2 px-2">${index === 0 ? '<span class="text-[#149e61] font-bold"><i class="fas fa-crown"></i> 推荐</span>' : `<span class="text-gray-500">#${index + 1}</span>`}</td>
                <td class="py-2 px-2 font-mono text-xs">${safeHTML(plan.symbol)}</td>
                <td class="py-2 px-2 text-center">${plan.dte.toFixed(0)}</td>
                <td class="py-2 px-2 text-right font-mono">${Math.round(plan.strike).toLocaleString()}</td>
                <td class="py-2 px-2 text-right font-mono text-[#149e61]">${plan.apr.toFixed(1)}%</td>
                <td class="py-2 px-2 text-right font-mono font-semibold">${plan.num_contracts}</td>
                <td class="py-2 px-2 text-right font-mono">$${plan.total_margin.toLocaleString()}</td>
                <td class="py-2 px-2 text-right font-mono text-[#7132f5]">$${plan.expected_premium.toLocaleString()}</td>
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

        const currency = document.getElementById('currencySelect')?.value || 'BTC';

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
        if (score >= 2) { icon = '🐂'; scoreLabel = '偏多'; scoreClass = 'bg-[#149e61]/20 text-[#149e61]'; }
        else if (score >= 1) { icon = '📈'; scoreLabel = '温和看多'; scoreClass = 'bg-[#149e61]/10 text-[#149e61]'; }
        else if (score > -1) { icon = '➡️'; scoreLabel = '中性'; scoreClass = 'bg-gray-700 text-gray-300'; }
        else if (score > -2) { icon = '📉'; scoreLabel = '温和看空'; scoreClass = 'bg-[#ef4444]/10 text-[#ef4444]'; }
        else { icon = '🐻'; scoreLabel = '偏空'; scoreClass = 'bg-[#ef4444]/20 text-[#ef4444]'; }
        
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
            if (iv > 70) el.className = 'font-mono text-sm font-bold text-[#ef4444]';
            else if (iv > 55) el.className = 'font-mono text-sm font-bold text-[#f59e0b]';
            else el.className = 'font-mono text-sm font-bold text-[#7132f5]';
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
                structLabel.className = 'text-xs px-2 py-0.5 rounded-full bg-[#ef4444]/20 text-[#ef4444] font-medium';
            } else {
                structLabel.textContent = 'Contango';
                structLabel.className = 'text-xs px-2 py-0.5 rounded-full bg-[#149e61]/20 text-[#149e61] font-medium';
            }
            const slope = ((backIv - frontIv) / frontIv * 100).toFixed(1);
            slopeLabel.textContent = (slope > 0 ? '+' : '') + slope + '%';
            slopeLabel.className = 'text-xs px-2 py-0.5 rounded-full ' + (slope >= 0 ? 'bg-[#149e61]/10 text-[#149e61]' : 'bg-[#ef4444]/10 text-[#ef4444]');
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
                (gammaStatus.region === 'long' ? 'border-[#149e61]/30 bg-[#149e61]/5' :
                 gammaStatus.region === 'short' ? 'border-[#ef4444]/30 bg-[#ef4444]/5' :
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
                (gammaStatus.region === 'long' ? 'text-[#149e61]' :
                 gammaStatus.region === 'short' ? 'text-[#ef4444]' : 'text-gray-400');
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
                (gammaStatus.distance_pct > 5 ? 'text-[#149e61]' :
                 gammaStatus.distance_pct < -5 ? 'text-[#ef4444]' : 'text-gray-400');
        }
    }
    
    // 更新策略建议（从第一个到期日提取）
    const gammaAdvice = firstExpiry ? firstExpiry.gamma_advice : null;
    if (gammaAdvice) {
        const adviceCard = document.getElementById('gammaAdviceCard');
        if (adviceCard) {
            adviceCard.classList.remove('hidden');
            adviceCard.className = 'mb-3 p-2.5 rounded-lg border ' + 
                (gammaStatus && gammaStatus.region === 'long' ? 'border-[#149e61]/20 bg-[#149e61]/5' :
                 gammaStatus && gammaStatus.region === 'short' ? 'border-[#ef4444]/20 bg-[#ef4444]/5' :
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
            'mb-3 p-2 rounded text-xs bg-[#ef4444]/10 border border-[#ef4444]/50 text-[#ef4444]' : 
            'mb-3 p-2 rounded text-xs bg-[#149e61]/10 border border-[#149e61]/30 text-[#149e61]';
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
            dvolSignal.className = trend.includes('上涨') ? 'text-xs mt-1 text-[#ef4444] font-medium' : trend.includes('下跌') ? 'text-xs mt-1 text-[#149e61] font-medium' : 'text-xs mt-1 text-gray-400';
        } else if (signal) {
            dvolSignal.textContent = signal;
            dvolSignal.className = signal.includes('偏高') ? 'text-xs mt-1 text-[#ef4444] font-medium' : signal.includes('偏低') ? 'text-xs mt-1 text-[#149e61] font-medium' : 'text-xs mt-1 text-gray-400';
        } else if (zScore !== null && zScore !== undefined) {
            if (zScore > 2) { dvolSignal.textContent = '异常偏高 ⚠️'; dvolSignal.className = 'text-xs mt-1 text-[#ef4444] font-medium'; }
            else if (zScore > 1) { dvolSignal.textContent = '偏高'; dvolSignal.className = 'text-xs mt-1 text-[#f59e0b] font-medium'; }
            else if (zScore < -2) { dvolSignal.textContent = '异常偏低'; dvolSignal.className = 'text-xs mt-1 text-[#149e61] font-medium'; }
            else if (zScore < -1) { dvolSignal.textContent = '偏低'; dvolSignal.className = 'text-xs mt-1 text-[#7132f5] font-medium'; }
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
    if (!data) return;

    // Status badge
    const statusMap = { 'NORMAL': ['正常', 'bg-[#149e61]/10 text-[#149e61]'], 'NEAR_FLOOR': ['接近支撑', 'bg-[#f59e0b]/10 text-[#f59e0b]'], 'ADVERSE': ['逆境', 'bg-[#f59e0b]/10 text-[#7132f5]'], 'PANIC': ['恐慌', 'bg-[#ef4444]/10 text-[#ef4444]'] };
    const status = data.status || 'NORMAL';
    const [statusText, statusClass] = statusMap[status] || ['--', 'bg-gray-700 text-gray-300'];
    const badge = document.getElementById('rfStatusBadge');
    if (badge) { badge.textContent = statusText; badge.className = 'px-2 py-0.5 rounded text-xs font-medium ' + statusClass; }

    // Risk change detection
    checkRiskChange(data.risk_level || status);

    // Risk score
    const score = data.composite_score || 0;
    const scoreBadge = document.getElementById('riskScoreBadge');
    if (scoreBadge) { scoreBadge.textContent = score; scoreBadge.style.color = getRiskColor(score); }

    // Pulse effect on risk score + update card
    const riskCardEl = document.getElementById('riskScoreCard');
    if (riskCardEl) {
        riskCardEl.textContent = score;
        riskCardEl.style.color = getRiskColor(score);
        riskCardEl.classList.add('pulse-update');
        setTimeout(() => riskCardEl.classList.remove('pulse-update'), 1000);
    }
    const riskLevelEl = document.getElementById('riskLevelCard');
    if (riskLevelEl) riskLevelEl.textContent = data.risk_level || '综合风险';

    // Support distance card
    const supportDistEl = document.getElementById('supportDistCard');
    if (supportDistEl && data.floors && data.spot) {
        const regularFloor = data.floors.regular || 0;
        if (regularFloor > 0) {
            const distPct = ((data.spot - regularFloor) / regularFloor * 100).toFixed(1);
            supportDistEl.textContent = distPct + '%';
            supportDistEl.style.color = parseFloat(distPct) < 5 ? '#ef4444' : parseFloat(distPct) < 15 ? '#f59e0b' : '#149e61';
        }
    }

    // Gauge
    renderRiskGauge('riskGaugeCanvas', score);

    // Radar
    const comp = data.components || {};
    renderRiskRadar('riskRadarCanvas', { 'Price': (comp.price_risk || {}).score || 0, 'Volatility': (comp.volatility_risk || {}).score || 0, 'Sentiment': (comp.sentiment_risk || {}).score || 0, 'Liquidity': (comp.liquidity_risk || {}).score || 0 });

    // 5 Indicator Cards
    const spot = data.spot || 0;
    if (data.max_pain) {
        const mp = typeof data.max_pain === 'number' ? data.max_pain : data.max_pain.price || 0;
        const elMaxPain = document.getElementById('cardMaxPain');
        if (elMaxPain) elMaxPain.textContent = '$' + mp.toLocaleString();
        const elMaxPainDist = document.getElementById('cardMaxPainDist');
        if (elMaxPainDist) elMaxPainDist.textContent = spot ? ((mp - spot) / spot * 100).toFixed(1) + '% 距现货' : '--';
    }
    if (data.put_wall) {
        const elPutWall = document.getElementById('cardPutWall');
        if (elPutWall) elPutWall.textContent = '$' + (data.put_wall.strike || 0).toLocaleString();
        const elPutWallOI = document.getElementById('cardPutWallOI');
        if (elPutWallOI) elPutWallOI.textContent = 'OI: ' + (data.put_wall.oi || 0).toLocaleString();
    }
    if (data.gamma_flip) {
        const elGammaFlip = document.getElementById('cardGammaFlip');
        if (elGammaFlip) elGammaFlip.textContent = '$' + (data.gamma_flip.strike || 0).toLocaleString();
        const elGammaFlipSignal = document.getElementById('cardGammaFlipSignal');
        if (elGammaFlipSignal) elGammaFlipSignal.textContent = spot > data.gamma_flip.strike ? '多头Gamma区' : '空头Gamma区';
    }
    if (data.floors) {
        const elFloorRegular = document.getElementById('cardFloorRegular');
        if (elFloorRegular) elFloorRegular.textContent = '$' + (data.floors.regular || 0).toLocaleString();
        const elFloorRegularDist = document.getElementById('cardFloorRegularDist');
        if (elFloorRegularDist) elFloorRegularDist.textContent = spot ? ((data.floors.regular - spot) / spot * 100).toFixed(1) + '% 距现货' : '--';
        const elFloorExtreme = document.getElementById('cardFloorExtreme');
        if (elFloorExtreme) elFloorExtreme.textContent = '$' + (data.floors.extreme || 0).toLocaleString();
        const elFloorExtremeDist = document.getElementById('cardFloorExtremeDist');
        if (elFloorExtremeDist) elFloorExtremeDist.textContent = spot ? ((data.floors.extreme - spot) / spot * 100).toFixed(1) + '% 距现货' : '--';
    }

    // Floors in header
    if (data.floors) {
        const elFloorRegularHeader = document.getElementById('floorRegularHeader');
        if (elFloorRegularHeader) elFloorRegularHeader.textContent = '$' + (data.floors.regular || 0).toLocaleString();
        const elFloorExtremeHeader = document.getElementById('floorExtremeHeader');
        if (elFloorExtremeHeader) elFloorExtremeHeader.textContent = '$' + (data.floors.extreme || 0).toLocaleString();
    }

    // Sub-functions
    if (data.onchain_metrics) updateOnchainMetrics(data.onchain_metrics);
    if (data.derivative_metrics) updateDerivativeMetrics(data.derivative_metrics);
    if (data.pressure_test) updatePressureTest(data.pressure_test);
    if (data.ai_sentiment) updateSentimentAnalysis(data.ai_sentiment);

    // Default to onchain tab
    setRiskTab('onchain');
}

function updateOnchainMetrics(onchain) {
    if (!onchain || onchain.error) return;

    // Convergence dashboard (keep existing logic)
    if (onchain.convergence_score) updateConvergenceDashboard(onchain.convergence_score);

    // 9 indicator cards into grid
    const grid = document.getElementById('onchainGrid');
    if (!grid) return;

    const indicators = [
        { id: 'MVRV', value: onchain.mvrv_ratio, fmt: v => v != null ? v.toFixed(2) : '--', color: v => v < 1 ? '#149e61' : v < 3.5 ? '#7132f5' : '#ef4444' },
        { id: 'MVRV Z-Score', value: onchain.mvrv_z_score, fmt: v => v != null ? v.toFixed(2) : '--', color: v => v < 0 ? '#149e61' : v < 7 ? '#7132f5' : '#ef4444' },
        { id: 'NUPL', value: onchain.nupl, fmt: v => v != null ? (v * 100).toFixed(1) + '%' : '--', color: v => v < 0 ? '#ef4444' : v < 0.25 ? '#7132f5' : v < 0.75 ? '#7132f5' : '#149e61' },
        { id: 'Mayer', value: onchain.mayer_multiple, fmt: v => v != null ? v.toFixed(2) : '--', color: v => v < 1 ? '#149e61' : v < 2.4 ? '#7132f5' : '#ef4444' },
        { id: '200WMA', value: onchain.price_200wma, fmt: v => v != null ? '$' + v.toLocaleString() : '--', color: () => '#7132f5' },
        { id: 'Balanced Price', value: onchain.balanced_price, fmt: v => v != null ? '$' + v.toLocaleString() : '--', color: () => '#7132f5' },
        { id: '200DMA', value: onchain.price_200dma, fmt: v => v != null ? '$' + v.toLocaleString() : '--', color: () => '#7132f5' },
        { id: 'Halving', value: onchain.halving_days_remaining, fmt: v => v != null ? v + ' 天' : '--', color: () => '#8b5cf6' },
        { id: 'Puell', value: onchain.puell_multiple, fmt: v => v != null ? v.toFixed(2) : '--', color: v => v < 0.4 ? '#149e61' : v < 2.0 ? '#7132f5' : '#ef4444' }
    ];

    grid.innerHTML = indicators.map(ind => {
        const val = ind.fmt(ind.value);
        const clr = ind.color(ind.value);
        return `<div class="bg-gray-800/50 rounded-lg p-3">
            <div class="text-xs text-gray-400 mb-1">${ind.id}</div>
            <div class="text-lg font-bold font-mono" style="color:${clr}">${val}</div>
        </div>`;
    }).join('');
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
                'bottom': 'text-[#149e61] bg-[#149e61]/10 border-[#149e61]/30',
                'top': 'text-[#ef4444] bg-[#ef4444]/10 border-[#ef4444]/30',
                'neutral': 'text-[#f59e0b] bg-[#f59e0b]/10 border-[#f59e0b]/30'
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
        'STRONG_BOTTOM': 'bg-[#ef4444]/20',
        'BOTTOM': 'bg-[#f59e0b]/20',
        'ACCUMULATION': 'bg-[#149e61]/20',
        'NEUTRAL': 'bg-gray-500/20',
        'DISTRIBUTION': 'bg-[#7132f5]/20',
        'TOP': 'bg-[#ef4444]/20'
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

function updateDerivativeMetrics(deriv) {
    if (!deriv || deriv.error) return;

    const section = document.getElementById('derivOverheatSection');
    const grid = document.getElementById('derivGrid');
    if (!section || !grid) return;

    // Overheating assessment
    const oh = deriv.overheating_assessment || {};
    const level = oh.level || 'NORMAL';
    const levelColors = { NORMAL: 'bg-[#149e61]/10 text-[#149e61]', WARM: 'bg-[#f59e0b]/10 text-[#f59e0b]', HOT: 'bg-[#f59e0b]/10 text-[#7132f5]', OVERHEATED: 'bg-[#ef4444]/10 text-[#ef4444]' };
    section.innerHTML = `<div class="flex items-center gap-3 mb-2">
        <span class="px-2 py-0.5 rounded text-xs font-medium ${levelColors[level] || levelColors.NORMAL}">${level}</span>
        <span class="text-sm text-gray-300">${oh.advice || ''}</span>
    </div>`;

    // 4 metric cards
    const metrics = [
        { id: 'Sharpe 14d', value: deriv.sharpe_ratio_14d, fmt: v => v != null ? v.toFixed(2) : '--' },
        { id: 'Sharpe 30d', value: deriv.sharpe_30d, fmt: v => v != null ? v.toFixed(2) : '--' },
        { id: '资金费率', value: deriv.funding_rate, fmt: v => v != null ? (v * 100).toFixed(4) + '%' : '--' },
        { id: '期货/现货比', value: deriv.futures_spot_ratio, fmt: v => v != null ? v.toFixed(2) : '--' }
    ];

    grid.innerHTML = metrics.map(m => `<div class="bg-gray-800/50 rounded-lg p-3">
        <div class="text-xs text-gray-400 mb-1">${m.id}</div>
        <div class="text-lg font-bold font-mono text-white">${m.fmt(m.value)}</div>
    </div>`).join('');
}

function getDerivBg(level) {
    const bgMap = {
        'STRONG_BOTTOM': 'bg-[#ef4444]/20',
        'BOTTOM': 'bg-[#f59e0b]/20',
        'NEUTRAL': 'bg-gray-500/20',
        'OVERHEATED': 'bg-[#7132f5]/20',
        'EXTREME_OVERHEAT': 'bg-[#ef4444]/20'
    };
    return bgMap[level] || 'bg-gray-500/20';
}

function updatePressureTest(pt) {
    if (!pt || pt.error) return;

    const section = document.getElementById('pressureTestSection');
    if (!section) return;

    const ra = pt.risk_assessment || {};
    const levelColors = { HIGH: 'text-[#ef4444]', MEDIUM: 'text-[#f59e0b]', LOW: 'text-[#149e61]' };
    const level = ra.risk_level || 'LOW';

    let html = `<div class="flex items-center gap-3 mb-3">
        <span class="text-lg font-bold ${levelColors[level] || 'text-gray-400'}">${level} 风险</span>
        <span class="text-sm text-gray-400">${ra.description || ''}</span>
    </div>`;

    // Greeks cards
    const bg = pt.base_greeks || {};
    html += `<div class="grid grid-cols-4 gap-3 mb-3">
        <div class="bg-gray-800/50 rounded-lg p-3"><div class="text-xs text-gray-400">Delta</div><div class="font-mono text-white">${(bg.delta || 0).toFixed(4)}</div></div>
        <div class="bg-gray-800/50 rounded-lg p-3"><div class="text-xs text-gray-400">Gamma</div><div class="font-mono text-white">${(bg.gamma || 0).toFixed(6)}</div></div>
        <div class="bg-gray-800/50 rounded-lg p-3"><div class="text-xs text-gray-400">Vanna</div><div class="font-mono text-white">${(bg.vanna || 0).toFixed(6)}</div></div>
        <div class="bg-gray-800/50 rounded-lg p-3"><div class="text-xs text-gray-400">Volga</div><div class="font-mono text-white">${(bg.volga || 0).toFixed(4)}</div></div>
    </div>`;

    // Joint stress scenarios table
    const scenarios = pt.joint_stress_tests || [];
    if (scenarios.length) {
        html += `<div class="overflow-x-auto"><table class="w-full text-sm"><thead><tr class="text-gray-400 border-b border-gray-700">
            <th class="text-left py-2 px-2">场景</th><th class="text-right py-2 px-2">Delta</th><th class="text-right py-2 px-2">Gamma</th><th class="text-right py-2 px-2">Vanna</th><th class="text-right py-2 px-2">Volga</th>
        </tr></thead><tbody>`;
        scenarios.forEach(s => {
            const risk = s.risk_assessment || {};
            const rowColor = risk.risk_level === 'HIGH' ? 'text-[#ef4444]' : risk.risk_level === 'MEDIUM' ? 'text-[#f59e0b]' : 'text-[#149e61]';
            html += `<tr class="border-b border-gray-800 ${rowColor}">
                <td class="py-1.5 px-2">${safeHTML(s.scenario || '')}</td>
                <td class="text-right py-1.5 px-2 font-mono">${(s.delta || 0).toFixed(4)}</td>
                <td class="text-right py-1.5 px-2 font-mono">${(s.gamma || 0).toFixed(6)}</td>
                <td class="text-right py-1.5 px-2 font-mono">${(s.vanna || 0).toFixed(6)}</td>
                <td class="text-right py-1.5 px-2 font-mono">${(s.volga || 0).toFixed(4)}</td>
            </tr>`;
        });
        html += '</tbody></table></div>';
    }

    section.innerHTML = html;
}

function updateSentimentAnalysis(sentiment) {
    if (!sentiment || sentiment.error) return;

    const section = document.getElementById('sentimentSection');
    if (!section) return;

    const da = sentiment.dominant_intent || {};
    const intentColors = {
        directional_speculation: 'text-[#ef4444]', institutional_hedging: 'text-[#7132f5]',
        arbitrage: 'text-[#7132f5]', market_maker_adjust: 'text-[#f59e0b]',
        income_generation: 'text-[#149e61]', volatility_play: 'text-[#7132f5]'
    };
    const intentLabels = {
        directional_speculation: '方向投机', institutional_hedging: '机构对冲',
        arbitrage: '套利', market_maker_adjust: '做市商调整',
        income_generation: '收租', volatility_play: '波动率交易'
    };

    const intentKey = da.name || '';
    let html = `<div class="flex items-center gap-4 mb-3">
        <div><span class="text-xs text-gray-400">主导意图</span><div class="text-lg font-bold ${intentColors[intentKey] || 'text-white'}">${intentLabels[intentKey] || da.name || '--'}</div></div>
        <div><span class="text-xs text-gray-400">风险等级</span><div class="text-lg font-bold ${da.risk_level === 'HIGH' ? 'text-[#ef4444]' : da.risk_level === 'MEDIUM' ? 'text-[#f59e0b]' : 'text-[#149e61]'}">${da.risk_level || '--'}</div></div>
        <div><span class="text-xs text-gray-400">信心度</span><div class="text-lg font-bold text-white">${sentiment.confidence || 0}%</div></div>
        <div><span class="text-xs text-gray-400">AI 建议</span><div class="text-sm text-gray-300">${safeHTML(sentiment.ai_recommendation || '--')}</div></div>
    </div>`;

    // Put/Call ratio
    const pc = sentiment.put_call_ratio || {};
    html += `<div class="flex gap-4 mb-3 text-sm">
        <span class="text-[#ef4444]">Put: ${(pc.put_pct || 0).toFixed(1)}%</span>
        <span class="text-[#149e61]">Call: ${(pc.call_pct || 0).toFixed(1)}%</span>
    </div>`;

    // Intent distribution
    const dist = sentiment.intent_distribution || {};
    if (Object.keys(dist).length) {
        html += '<div class="space-y-1 mb-3">';
        Object.entries(dist).forEach(([key, val]) => {
            const pct = (typeof val === 'object' && val != null) ? (val.pct || 0) : (typeof val === 'number' ? val : 0);
            const label = intentLabels[key] || key;
            html += `<div class="flex items-center gap-2 text-xs">
                <span class="w-24 text-gray-400">${label}</span>
                <div class="flex-1 bg-[#22232e] rounded-full h-2"><div class="bg-[#7132f5] h-2 rounded-full" style="width:${Math.min(pct, 100)}%"></div></div>
                <span class="text-gray-400 w-10 text-right">${pct.toFixed(0)}</span>
            </div>`;
        });
        html += '</div>';
    }

    // Risk warnings
    const warnings = sentiment.risk_warnings || [];
    if (warnings.length) {
        html += '<div class="space-y-1">';
        warnings.forEach(w => {
            const level = (w.level || '').toUpperCase();
            const icon = level === 'HIGH' ? '🔴' : level === 'MEDIUM' ? '🟡' : '🟢';
            html += `<div class="text-sm text-gray-300">${icon} ${safeHTML(w.text || w.message || w)}</div>`;
        });
        html += '</div>';
    }

    section.innerHTML = html;
}

function getRiskColor(score) {
    if (score < 30) return '#149e61';
    if (score < 60) return '#7132f5';
    if (score < 80) return '#f59e0b';
    return '#ef4444';
}

function renderRiskGauge(canvasId, score) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (window._riskGaugeChart) { window._riskGaugeChart.destroy(); }
    let color;
    if (score <= 30) color = '#149e61';
    else if (score <= 60) color = '#7132f5';
    else if (score <= 80) color = '#f59e0b';
    else color = '#ef4444';
    window._riskGaugeChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            datasets: [{
                data: [score, 100 - score],
                backgroundColor: [color, '#22232e'],
                borderWidth: 0
            }]
        },
        options: {
            rotation: -90,
            circumference: 180,
            cutout: '75%',
            responsive: false,
            plugins: { legend: { display: false }, tooltip: { enabled: false } }
        },
        plugins: [{
            id: 'gaugeCenter',
            afterDraw(chart) {
                const { ctx, chartArea } = chart;
                const cx = (chartArea.left + chartArea.right) / 2;
                const cy = chartArea.bottom - 10;
                ctx.save();
                ctx.textAlign = 'center';
                ctx.fillStyle = color;
                ctx.font = 'bold 32px sans-serif';
                ctx.fillText(score, cx, cy - 8);
                ctx.font = '12px sans-serif';
                ctx.fillStyle = '#9497a9';
                const status = score <= 30 ? '低风险' : score <= 60 ? '中等' : score <= 80 ? '偏高' : '高风险';
                ctx.fillText(status, cx, cy + 12);
                ctx.restore();
            }
        }]
    });
}

function renderRiskRadar(canvasId, dimensions) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (window._riskRadarChart) { window._riskRadarChart.destroy(); }
    const labels = Object.keys(dimensions);
    const values = Object.values(dimensions);
    window._riskRadarChart = new Chart(ctx, {
        type: 'radar',
        data: {
            labels: labels,
            datasets: [{
                label: '风险维度',
                data: values,
                backgroundColor: 'rgba(239, 68, 68, 0.2)',
                borderColor: 'rgba(239, 68, 68, 0.8)',
                borderWidth: 2,
                pointBackgroundColor: 'rgba(239, 68, 68, 1)',
                pointRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            aspectRatio: 1.2,
            scales: {
                r: {
                    min: 0, max: 100,
                    ticks: { stepSize: 20, color: '#6b7280', backdropColor: 'transparent' },
                    grid: { color: 'rgba(75, 85, 99, 0.3)' },
                    angleLines: { color: 'rgba(75, 85, 99, 0.3)' },
                    pointLabels: { color: '#d1d5db', font: { size: 11 } }
                }
            },
            plugins: { legend: { display: false } }
        }
    });
}

function renderSparkline(elementId, values) {
    const el = document.getElementById(elementId);
    if (!el || !values || values.length < 2) return;
    const w = 80, h = 24;
    const min = Math.min(...values), max = Math.max(...values);
    const range = max - min || 1;
    const points = values.map((v, i) => {
        const x = (i / (values.length - 1)) * w;
        const y = h - ((v - min) / range) * h;
        return `${x},${y}`;
    }).join(' ');
    el.innerHTML = `<svg width="${w}" height="${h}" class="inline-block"><polyline points="${points}" fill="none" stroke="#7132f5" stroke-width="1.5"/></svg>`;
}

function setRiskTab(tab) {
    document.querySelectorAll('.risk-tab').forEach(btn => {
        btn.classList.toggle('border-[#7132f5]', btn.dataset.tab === tab);
        btn.classList.toggle('text-white', btn.dataset.tab === tab);
        btn.classList.toggle('border-transparent', btn.dataset.tab !== tab);
        btn.classList.toggle('text-[#9497a9]', btn.dataset.tab !== tab);
    });
    document.querySelectorAll('.risk-tab-content').forEach(el => el.classList.add('hidden'));
    const map = { onchain: 'riskTabOnchain', deriv: 'riskTabDeriv', pressure: 'riskTabPressure', sentiment: 'riskTabSentiment' };
    const target = document.getElementById(map[tab]);
    if (target) {
        target.classList.remove('hidden');
        // Fade-in animation
        target.style.opacity = '0';
        target.style.transform = 'translateY(4px)';
        requestAnimationFrame(() => {
            target.style.transition = 'opacity 0.2s ease, transform 0.2s ease';
            target.style.opacity = '1';
            target.style.transform = 'translateY(0)';
        });
    }
}

async function loadLLMRiskInsight(currency) {
    const btn = document.getElementById('llmInsightBtn');
    const loading = document.getElementById('llmInsightLoading');
    const result = document.getElementById('llmInsightResult');
    if (btn) btn.disabled = true;
    if (loading) loading.classList.remove('hidden');
    if (result) result.classList.add('hidden');
    try {
        const resp = await safeFetch(`${API_BASE}/api/risk/llm-insight?currency=${currency}`, { timeout: 300000 });
        const data = await resp.json();
        renderLLMRiskInsight(data);
    } catch (e) {
        console.error('LLM insight failed:', e);
        if (result) {
            result.classList.remove('hidden');
            document.getElementById('llmNarrative').textContent = 'LLM 分析失败: ' + e.message;
        }
    } finally {
        if (btn) btn.disabled = false;
        if (loading) loading.classList.add('hidden');
    }
}

function renderLLMRiskInsight(data) {
    const result = document.getElementById('llmInsightResult');
    if (!result) return;
    result.classList.remove('hidden');
    document.getElementById('llmNarrative').textContent = data.narrative || '';
    const anomEl = document.getElementById('llmAnomalies');
    if (data.anomalies && data.anomalies.length) {
        anomEl.innerHTML = '<div class="text-xs text-[#f59e0b] font-medium mb-1">⚠️ 异常告警</div>' +
            data.anomalies.map(a => `<div class="text-sm text-gray-300 mb-1">• ${safeHTML(a)}</div>`).join('');
        anomEl.classList.remove('hidden');
    } else {
        anomEl.classList.add('hidden');
    }
    const recEl = document.getElementById('llmRecommendations');
    if (data.recommendations && data.recommendations.length) {
        recEl.innerHTML = '<div class="text-xs text-[#149e61] font-medium mb-1">✅ 操作建议</div>' +
            data.recommendations.map(r => `<div class="text-sm text-gray-300 mb-1">• ${safeHTML(r)}</div>`).join('');
        recEl.classList.remove('hidden');
    } else {
        recEl.classList.add('hidden');
    }
    const conf = data.confidence || 0;
    document.getElementById('llmConfidenceBar').style.width = conf + '%';
    document.getElementById('llmConfidenceText').textContent = conf + '%';
}

// Expose to global scope for onclick handlers (module scripts don't leak to window)
window.setRiskTab = setRiskTab;
window.loadLLMRiskInsight = loadLLMRiskInsight;
window.renderLLMRiskInsight = renderLLMRiskInsight;
window.loadIVSmile = loadIVSmile;
window.loadGreeksSummary = loadGreeksSummary;

function updateMacroIndicators(data) {
    const spotPrice = data.spot_price;
    const spotEl = document.getElementById('spotPrice');
    if (!spotEl) return;
    if (spotPrice) {
        spotEl.textContent = `$${Math.round(spotPrice).toLocaleString()}`;
        spotEl.classList.remove('text-gray-500');
        currentSpotPrice = spotPrice;
        // Pulse effect on price update
        spotEl.classList.add('pulse-update');
        setTimeout(() => spotEl.classList.remove('pulse-update'), 1000);
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

    if (dvolSignal) {
        if (dvolInterp) {
            dvolSignal.textContent = dvolInterp;
            dvolSignal.className = dvolTrend.includes('上涨') ? 'text-xs mt-1 text-[#ef4444] font-medium' : dvolTrend.includes('下跌') ? 'text-xs mt-1 text-[#149e61] font-medium' : 'text-xs mt-1 text-gray-400';
        } else if (signal) {
            dvolSignal.textContent = signal;
            dvolSignal.className = signal.includes('偏高') ? 'text-xs mt-1 text-[#ef4444] font-medium' : signal.includes('偏低') ? 'text-xs mt-1 text-[#149e61] font-medium' : 'text-xs mt-1 text-gray-400';
        } else if (zScore !== null && zScore !== undefined) {
            if (zScore > 2) { dvolSignal.textContent = '异常偏高 ⚠️'; dvolSignal.className = 'text-xs mt-1 text-[#ef4444] font-medium'; }
            else if (zScore > 1) { dvolSignal.textContent = '偏高'; dvolSignal.className = 'text-xs mt-1 text-[#f59e0b] font-medium'; }
            else if (zScore < -2) { dvolSignal.textContent = '异常偏低'; dvolSignal.className = 'text-xs mt-1 text-[#149e61] font-medium'; }
            else if (zScore < -1) { dvolSignal.textContent = '偏低'; dvolSignal.className = 'text-xs mt-1 text-[#7132f5] font-medium'; }
            else { dvolSignal.textContent = '正常区间'; dvolSignal.className = 'text-xs mt-1 text-gray-400'; }
        } else {
            dvolSignal.textContent = '--';
            dvolSignal.className = 'text-xs mt-1 text-gray-400';
        }
    }

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
        if (countEl) countEl.textContent = '0 个合约';
        if (loadMoreContainer) loadMoreContainer.classList.add('hidden');
        const tbody = document.getElementById('opportunitiesTable');
        tbody.innerHTML = `<tr><td colspan="25" class="text-center py-12 text-gray-500"><div class="flex flex-col items-center gap-3"><i class="fas fa-inbox text-3xl text-gray-600"></i><p>暂无符合条件的合约</p><p class="text-xs text-gray-600">尝试调整扫描参数</p></div></td></tr>`;
        return;
    }

    if (countEl) countEl.textContent = `${_allContracts.length} 个合约`;
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
        const platformColor = contract.platform === 'Deribit' ? 'text-[#7132f5]' : 'text-[#f59e0b]';
        const liqColor = contract.liquidity_score >= 70 ? 'text-[#149e61]' : contract.liquidity_score >= 40 ? 'text-[#f59e0b]' : 'text-[#ef4444]';
        const deltaAbs = Math.abs(Number(contract.delta) || 0);

        const symbol = safeHTML(contract.symbol || contract.instrument_name || 'N/A');
        contract.symbol = symbol;

        let riskBadge = '';
        let riskClass = '';

        const riskLevel = contract.risk_level || 'normal';
        const strikeVal = Number(contract.strike);
        if (riskLevel === 'extreme') {
            riskClass = 'risk-alert-high';
            riskBadge = '<span class="risk-badge bg-[#ef4444] text-[10px] text-white px-1.5 py-0.5 rounded font-bold"><i class="fas fa-exclamation-triangle"></i> 极高</span>';
            highRiskContracts.push({ contract, reason: `后端判定 extreme: delta=${deltaAbs.toFixed(3)}` });
        } else if (riskLevel === 'high') {
            riskClass = 'risk-alert-high';
            riskBadge = '<span class="risk-badge bg-[#ef4444] text-[10px] text-white px-1.5 py-0.5 rounded font-bold"><i class="fas fa-exclamation"></i> 高</span>';
            highRiskContracts.push({ contract, reason: `后端判定 high: delta=${deltaAbs.toFixed(3)}` });
        } else if (riskLevel === 'warning') {
            riskBadge = '<span class="bg-[#f59e0b] text-[10px] text-white px-1.5 py-0.5 rounded">警告</span>';
        } else {
            riskBadge = '<span class="bg-[#149e61]/50 text-[10px] text-white px-1.5 py-0.5 rounded">正常</span>';
        }

        const spreadColor = (contract.spread_pct || 0) > 5 ? 'text-[#7132f5]' : 'text-gray-400';
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
            <td class="py-2 px-2 text-center"><span class="${isPut ? 'text-[#149e61]' : 'text-[#7132f5]'} text-xs font-bold">${safeHTML(contract.option_type || 'PUT')}</span></td>
            <td class="py-2 px-2 text-center font-mono text-xs tabular-nums">${safeHTML(symbol.split('-')[1] || '')}</td>
            <td class="py-2 px-2 text-center text-xs tabular-nums">${dte.toFixed(0)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums">$${Math.round(strikeVal).toLocaleString()}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums font-semibold ${deltaAbs > 0.35 ? 'text-[#ef4444]' : deltaAbs > 0.25 ? 'text-[#f59e0b]' : 'text-[#149e61]'}">${deltaAbs.toFixed(4)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums font-semibold ${theta > 5 ? 'text-[#149e61]' : theta > 0 ? 'text-[#149e61]' : 'text-gray-500'}" title="每日时间价值衰减">${theta > 0 ? '+' : ''}${theta.toFixed(2)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${gamma > 0.15 ? 'text-[#7132f5]' : 'text-gray-300'}">${gamma.toFixed(4)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${vega > 50 ? 'text-[#f59e0b]' : 'text-gray-300'}">${vega.toFixed(1)}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${iv ? (iv >= 80 ? 'text-[#ef4444]' : iv >= 50 ? 'text-[#f59e0b]' : 'text-[#149e61]') : 'text-gray-300'}">${iv ? iv.toFixed(1) + '%' : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs font-bold text-[#149e61] tabular-nums">${apr.toFixed(1)}%</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${pop ? (isPut ? (pop >= 70 ? 'text-[#149e61]' : pop >= 50 ? 'text-[#f59e0b]' : 'text-[#7132f5]') : (pop <= 30 ? 'text-[#149e61]' : pop <= 50 ? 'text-[#f59e0b]' : 'text-[#ef4444]')) : 'text-gray-500'}" title="${isPut ? '到期不被行权概率' : '被行权概率(卖飞风险)'}">${pop ? (isPut ? pop.toFixed(0) + '%' : (100 - pop).toFixed(0) + '%飞') : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-[#f59e0b]/90">$${premium.toLocaleString(undefined, {maximumFractionDigits: 2})}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-gray-400" title="开仓保证金需求">$${marginReq.toLocaleString(undefined, {maximumFractionDigits: 0})}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums font-semibold ${capEff >= 15 ? 'text-[#149e61]' : capEff >= 8 ? 'text-[#149e61]' : 'text-gray-400'}" title="权利金/保证金">${capEff.toFixed(1)}%</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${supportDist !== null && supportDist !== undefined ? (supportDist >= 10 ? 'text-[#149e61]' : supportDist >= 5 ? 'text-[#f59e0b]' : 'text-[#ef4444]') : 'text-gray-600'}" title="PUT行权价到支撑位距离">${supportDist !== null && supportDist !== undefined ? supportDist.toFixed(1) + '%' : (isPut ? '-' : 'N/A')}</td>
            <td class="py-2 px-2 text-center"><span class="${liqColor} text-xs font-medium">${liquidityScore}</span></td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-[#ef4444]/80">$${lossVal.toLocaleString(undefined, {maximumFractionDigits: 0})}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-[#8b5cf6]/80">$${breakeven.toLocaleString(undefined, {maximumFractionDigits: 0})}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${bePct ? (bePct >= 10 ? 'text-[#149e61]' : bePct >= 5 ? 'text-[#f59e0b]' : 'text-[#7132f5]') : 'text-gray-500'}">${bePct ? bePct.toFixed(1) + '%' : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums text-gray-400">${oi.toLocaleString()}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${spreadColor}">${spreadPct.toFixed(2)}%</td>
            <td class="py-2 px-2 text-center font-mono text-xs tabular-nums ${ivRank ? (ivRank >= 70 ? 'text-[#ef4444]' : ivRank <= 30 ? 'text-[#149e61]' : 'text-gray-400') : 'text-gray-500'}">${ivRank ? String(ivRank).split('.')[0] : '-'}</td>
            <td class="py-2 px-2 text-right font-mono text-xs tabular-nums ${score !== undefined ? (score >= 0.7 ? "text-[#149e61] font-bold" : score >= 0.5 ? "text-[#149e61]" : score >= 0.3 ? "text-[#f59e0b]" : "text-gray-500") : "text-gray-500"}" title="加权评分: APR(25%)+POP(25%)+安全垫(20%)+流动性(15%)+IV中性(15%)">${score !== undefined ? score.toFixed(3) : "-"}</td>
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
            icon: '/static/favicon.svg'
        });
    }
    if (highRiskContracts.length > 0) {
        showToast(`高风险预警! ${highRiskContracts.length} 个合约 Delta 过高`, 'error', 8000);
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
    const contract = (_allContracts && _allContracts[idx]) || (currentData && currentData.contracts && currentData.contracts[idx]);
    if (!contract) return;
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
        alternativesHtml = `<div class="mt-4"><h4 class="font-semibold text-[#149e61] mb-2">建议滚仓至：</h4>${alternatives.map(alt => `
            <div class="bg-gray-800/50 rounded-lg p-3 mb-2">
                <div class="flex justify-between"><span class="font-mono">${safeHTML(alt.symbol)}</span><span class="text-[#149e61]">${alt.apr.toFixed(1)}% APR</span></div>
                <div class="text-xs text-gray-400 mt-1">Strike: ${Math.round(alt.strike).toLocaleString()} | DTE: ${alt.dte.toFixed(0)} | Delta: ${Math.abs(alt.delta).toFixed(3)}</div>
            </div>
        `).join('')}</div>`;
    }

    content.innerHTML = `
        <div class="space-y-4">
            <div class="bg-[#ef4444]/10 border border-[#ef4444]/30 rounded-lg p-4">
                <h4 class="font-semibold text-[#ef4444] mb-2">当前持仓风险</h4>
                <div class="grid grid-cols-2 gap-4 text-sm">
                    <div><span class="text-gray-400">合约:</span> <span class="font-mono">${safeHTML(contract.symbol)}</span></div>
                    <div><span class="text-gray-400">Delta:</span> <span class="text-[#ef4444] font-bold">${contract.delta.toFixed(3)}</span></div>
                    <div><span class="text-gray-400">行权价:</span> $${Math.round(contract.strike).toLocaleString()}</div>
                    <div><span class="text-gray-400">距离现货:</span> <span class="${distancePct < 2 ? 'text-[#ef4444]' : ''}">${distancePct.toFixed(1)}%</span></div>
                </div>
                <div class="mt-2 text-sm"><span class="text-gray-400">-10%亏损预估:</span> <span class="text-[#ef4444] font-bold">-$${estimatedLoss.toLocaleString()}</span></div>
            </div>
            ${alternativesHtml}
            <div class="bg-[#7132f5]/10 border border-[#7132f5]/30 rounded-lg p-4">
                <h4 class="font-semibold text-[#7132f5] mb-2">操作建议</h4>
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

    // Update top-level card
    const largeTradesEl = document.getElementById('largeTradesCount');
    if (largeTradesEl) largeTradesEl.textContent = count || 0;

    if (!container) { console.warn('大单风向标: container not found'); return; }

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
        mega:   { border: 'border-l-[#ef4444]',    bg: 'bg-[#ef4444]/15',    badge: 'bg-[#ef4444] text-white',       label: '巨鲸', icon: '🐋' },
        high:   { border: 'border-l-[#f59e0b]',  bg: 'bg-[#7132f5]/10', badge: 'bg-[#f59e0b] text-white',    label: '大单', icon: '🔥' },
        medium: { border: 'border-l-[#f59e0b]',  bg: 'bg-[#f59e0b]/10',  badge: 'bg-[#f59e0b] text-gray-900', label: '中单', icon: '⚡' },
        low:    { border: 'border-l-[#7132f5]',    bg: 'bg-[#7132f5]/5',    badge: 'bg-[#7132f5] text-white',      label: '小单', icon: '📊' },
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
            dirIcon = '▲'; dirColor = 'text-[#ef4444]'; dirLabel = '买';
        } else if (dir === 'sell') {
            dirIcon = '▼'; dirColor = 'text-[#149e61]'; dirLabel = '卖';
        } else {
            dirIcon = '—'; dirColor = 'text-gray-400'; dirLabel = '';
        }

        const optIsPut = optType && optType.toUpperCase().startsWith('P');
        const optTag = optType
            ? '<span class="px-1 py-0.5 rounded text-[10px] font-bold ' +
              (optIsPut ? 'bg-[#7132f5]/30 text-[#8b5cf6]' : 'bg-[#149e61]/30 text-[#149e61]') +
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
                <span class="text-[#f59e0b] font-bold ml-auto">${notionalStr}</span>
                ${sevStyle.label ? '<span class="' + sevStyle.badge + ' text-[10px] px-1.5 py-0.5 rounded font-bold ml-1">' + sevStyle.icon + ' ' + sevStyle.label + '</span>' : ''}
            </div>
            <div class="flex items-center gap-1.5 mt-0.5 text-[11px]">
                <span class="${dirColor}">${dirLabel}</span>
                ${flowCN ? '<span class="text-[#8b5cf6]">' + flowCN + '</span>' : ''}
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
                    val <= 20 ? 'text-[#ef4444]' : val <= 40 ? 'text-[#7132f5]' : 
                    val <= 60 ? 'text-[#f59e0b]' : val <= 80 ? 'text-[#149e61]' : 'text-[#149e61]'
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
                    rate < -0.1 ? 'text-[#ef4444]' : rate > 0.1 ? 'text-[#149e61]' : 'text-gray-300'
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
        if (!dvolChart) return;
        const currency = document.getElementById('currencySelect')?.value || 'BTC';
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
    const colors = { success: 'border-[#149e61] bg-[#149e61]/10 text-[#149e61]', error: 'border-[#ef4444] bg-[#ef4444]/10 text-[#ef4444]', warning: 'border-[#f59e0b] bg-[#f59e0b]/10 text-[#f59e0b]', info: 'border-[#7132f5] bg-[#7132f5]/10 text-[#7132f5]' };
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
        const colors = { sellput: 'bg-[#149e61]/20 text-[#149e61] border-[#149e61]/30', coveredcall: 'bg-[#7132f5]/20 text-[#7132f5] border-[#7132f5]/30', wheel: 'bg-[#7132f5]/20 text-[#7132f5] border-[#7132f5]/30', all: 'bg-[#7132f5]/20 text-[#7132f5] border-[#7132f5]/30' };
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
            icon.className = `fas fa-sort-${direction === 'asc' ? 'up' : 'down'} text-xs text-[#7132f5]`;
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
        btn.classList.remove('ring-1', 'bg-[#7132f5]/15', 'text-[#8b5cf6]', 'ring-[#7132f5]/30',
            'bg-[#149e61]/15', 'text-[#149e61]', 'ring-[#149e61]/30');
    });

    const activeBtn = document.getElementById('preset' +
        (presetName === 'conservative' ? 'Con' : presetName === 'standard' ? 'Std' : 'Agg'));
    const colorMap = {conservative: '#149e61', standard: '#7132f5', aggressive: '#7132f5'};
    const c = colorMap[presetName];
    activeBtn.classList.add(`bg-[${c}]/15`, `text-[${c}]`, `ring-1`, `ring-[${c}]/30`);

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
                badge.className = 'ml-auto text-[10px] px-1.5 py-0.5 rounded bg-[#ef4444]/20 text-[#ef4444]';
            } else if (level === 'aggressive') {
                badge.textContent = '已放宽参数';
                badge.className = 'ml-auto text-[10px] px-1.5 py-0.5 rounded bg-[#149e61]/20 text-[#149e61]';
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
            if (score >= 2) { icon = '🐂'; scoreLabel = '偏多'; scoreClass = 'bg-[#149e61]/20 text-[#149e61]'; }
            else if (score >= 1) { icon = '📈'; scoreLabel = '温和看多'; scoreClass = 'bg-[#149e61]/10 text-[#149e61]'; }
            else if (score > -1) { icon = '➡️'; scoreLabel = '中性'; scoreClass = 'bg-gray-700 text-gray-300'; }
            else if (score > -2) { icon = '📉'; scoreLabel = '温和看空'; scoreClass = 'bg-[#ef4444]/10 text-[#ef4444]'; }
            else { icon = '🐻'; scoreLabel = '偏空'; scoreClass = 'bg-[#ef4444]/20 text-[#ef4444]'; }

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
                const totalNotional = data.total_notional || 0;
                totalNotionalEl.textContent = totalNotional > 0 ? `$${(totalNotional / 1000000).toFixed(1)}M` : '-';
            }

            const dominantFlowEl = document.getElementById('windDominantFlow');
            if (dominantFlowEl) dominantFlowEl.textContent = data.dominant_flow || '-';

            // Update flow breakdown display
            const flowBreakdownEl = document.getElementById('flowBreakdown');
            if (flowBreakdownEl && data.flow_breakdown) {
                flowBreakdownEl.innerHTML = data.flow_breakdown.map(f => {
                    const pct = f.count > 0 ? Math.round(f.count / (summary.total_trades || 1) * 100) : 0;
                    const colorClass = f.type === 'sell_put' ? 'text-[#149e61]' :
                                      f.type === 'buy_call' ? 'text-[#7132f5]' :
                                      f.type === 'buy_put' ? 'text-[#ef4444]' :
                                      f.type === 'sell_call' ? 'text-[#f59e0b]' : 'text-gray-400';
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
            } else {
                _showTermStructureError(d.error);
            }
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
                if (iv > 70) el.className = 'font-mono text-sm font-bold text-[#ef4444]';
                else if (iv > 55) el.className = 'font-mono text-sm font-bold text-[#f59e0b]';
                else el.className = 'font-mono text-sm font-bold text-[#7132f5]';
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
                    structLabel.className = 'text-xs px-2 py-0.5 rounded-full bg-[#ef4444]/20 text-[#ef4444] font-medium';
                } else {
                    structLabel.textContent = 'Contango';
                    structLabel.className = 'text-xs px-2 py-0.5 rounded-full bg-[#149e61]/20 text-[#149e61] font-medium';
                }
                const slope = ((backIv - frontIv) / frontIv * 100).toFixed(1);
                slopeLabel.textContent = (slope > 0 ? '+' : '') + slope + '%';
                slopeLabel.className = 'text-xs px-2 py-0.5 rounded-full ' + (slope >= 0 ? 'bg-[#149e61]/10 text-[#149e61]' : 'bg-[#ef4444]/10 text-[#ef4444]');
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
                errDiv.className = 'text-[#f59e0b] text-center py-8 text-sm absolute inset-0 bg-gray-900/90 z-10';
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
                slopeLabel.className = 'text-xs px-2 py-0.5 rounded-full ' + (s.percent >= 0 ? 'bg-[#149e61]/10 text-[#149e61]' : 'bg-[#ef4444]/10 text-[#ef4444]');
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
                const vrpColor = v.signal && v.signal.includes('SELL') ? 'text-[#149e61]' : v.signal === 'BUY_EDGE' ? 'text-[#7132f5]' : 'text-gray-400';
                vrpEl.textContent = (v.value > 0 ? '+' : '') + v.value + '%';
                vrpEl.className = 'text-lg font-bold ' + vrpColor;
                if (vrpDesc) vrpDesc.textContent = v.description;
            }
            // 形态指标
            const slopeGrade = document.getElementById('ivSlopeGrade');
            if (slopeGrade && analysis.slope) {
                const s = analysis.slope;
                slopeGrade.textContent = s.grade || '--';
                slopeGrade.className = 'text-xs font-bold ' + (s.grade === 'SEVERELY_INVERTED' || s.grade === 'INVERTED' ? 'text-[#ef4444]' : s.grade === 'STEEP' || s.grade === 'VERY_STEEP' ? 'text-[#149e61]' : 'text-gray-400');
            }
            const curvEl = document.getElementById('ivCurvatureType');
            if (curvEl && analysis.curvature) {
                curvEl.textContent = analysis.curvature.type || '--';
                curvEl.className = 'text-xs font-bold ' + (analysis.curvature.type === 'HUMP' ? 'text-[#f59e0b]' : 'text-gray-400');
            }
            const regEl = document.getElementById('ivRegime');
            if (regEl && analysis.iv_levels) {
                const il = analysis.iv_levels;
                regEl.textContent = il.avg_iv + '%';
                regEl.className = 'text-xs font-bold ' + (il.regime === 'EXTREME' ? 'text-[#ef4444]' : il.regime === 'HIGH' ? 'text-[#7132f5]' : il.regime === 'LOW' || il.regime === 'VERY_LOW' ? 'text-[#7132f5]' : 'text-[#149e61]');
            }
            // 策略建议
            const recsEl = document.getElementById('ivRecommendations');
            if (recsEl && analysis.recommendations && analysis.recommendations.length > 0) {
                const typeColors = {'warning': 'border-[#ef4444]/30 bg-[#ef4444]/5', 'opportunity': 'border-[#149e61]/30 bg-[#149e61]/5', 'info': 'border-[#7132f5]/30 bg-[#7132f5]/5'};
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
                    action.className = 'text-[11px] text-[#8b5cf6] mt-1 font-medium';
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
        errDiv.innerHTML = '<i class="fas fa-exclamation-triangle text-[#f59e0b] text-2xl mb-2"></i><br>' +
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
                (exp.gamma_status.region === 'long' ? 'border-[#149e61]/30 bg-[#149e61]/5' :
                 exp.gamma_status.region === 'short' ? 'border-[#ef4444]/30 bg-[#ef4444]/5' :
                 'border-gray-500/30 bg-gray-500/5');
            
            document.getElementById('gammaStatusIcon').textContent = exp.gamma_status.icon || '⚖️';
            document.getElementById('gammaStatusText').textContent = exp.gamma_status.region_cn || '中性区域';
            document.getElementById('gammaStatusText').className = 'text-sm font-bold ' + 
                (exp.gamma_status.region === 'long' ? 'text-[#149e61]' :
                 exp.gamma_status.region === 'short' ? 'text-[#ef4444]' : 'text-gray-400');
            
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
                    (exp.gamma_status.distance_pct > 5 ? 'text-[#149e61]' :
                     exp.gamma_status.distance_pct < -5 ? 'text-[#ef4444]' : 'text-gray-400');
            }
        }
        
        // 更新方向性建议
        if (exp.gamma_advice && adviceCard) {
            adviceCard.classList.remove('hidden');
            adviceCard.className = 'mb-3 p-2.5 rounded-lg border ' + 
                (exp.gamma_status && exp.gamma_status.region === 'long' ? 'border-[#149e61]/20 bg-[#149e61]/5' :
                 exp.gamma_status && exp.gamma_status.region === 'short' ? 'border-[#ef4444]/20 bg-[#ef4444]/5' :
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
            mmEl.className = exp.mm_signal.includes('DANGER') || exp.mm_signal.includes('危险') ? 'mb-3 p-2 rounded text-xs bg-[#ef4444]/10 border border-[#ef4444]/50 text-[#ef4444]' : 'mb-3 p-2 rounded text-xs bg-[#149e61]/10 border border-[#149e61]/30 text-[#149e61]';
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
                errDiv.className = 'text-[#f59e0b] text-center py-8 text-sm absolute inset-0 bg-gray-900/90 z-10';
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
                        borderColor: '#7132f5',
                        backgroundColor: 'rgba(249,115,22,0.1)',
                        fill: true,
                        tension: 0.3,
                        pointRadius: 0,
                        pointHoverRadius: 5,
                        pointHoverBackgroundColor: '#7132f5',
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
                        labels: { color: '#9497a9', boxWidth: 12, padding: 15, font: { size: 11 } },
                        title: { display: true, text: '最大痛点 $' + mpStrike.toLocaleString() + ' | 现货 $' + spotPrice.toLocaleString(), color: '#7132f5', font: { size: 13, weight: 'bold' } }
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
                        title: { display: true, text: '痛点曲线 (%)', color: '#7132f5' }, 
                        grid: { color: 'rgba(255,255,255,0.06)' }, 
                        ticks: { color: '#7132f5', callback: function(v) { return v + '%'; } } 
                    },
                    y1: { 
                        type: 'linear', position: 'right',
                        title: { display: true, text: 'OI净敞口 (%)', color: '#149e61' }, 
                        grid: { drawOnChartArea: false }, 
                        ticks: { 
                            color: '#149e61', 
                            callback: function(v) { 
                                if (Math.abs(v) >= 1000) return (v/1000).toFixed(0) + 'K';
                                return v; 
                            } 
                        }
                    },
                    x: { 
                        grid: { color: 'rgba(255,255,255,0.06)' }, 
                        ticks: { 
                            color: '#9497a9', maxTicksLimit: 20,
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
        errDiv.innerHTML = '<i class="fas fa-exclamation-triangle text-[#f59e0b] text-2xl mb-2"></i><br>' +
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
    resultDiv.innerHTML = '<div class="text-center py-4 text-[#7132f5]"><i class="fas fa-spinner fa-spin mr-2"></i>🔄 推演计算中...</div>';

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
            'SAFE': 'bg-[#149e61]/10 border-[#149e61]/40 text-[#149e61]',
            'WARNING': 'bg-[#f59e0b]/10 border-[#f59e0b]/40 text-[#f59e0b]',
            'DANGER': 'bg-[#ef4444]/10 border-[#ef4444]/40 text-[#ef4444]',
            'CRITICAL': 'bg-[#ef4444]/10 border-[#ef4444]/60 text-[#ef4444]'
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
        html += '<div class="flex justify-between"><span class="text-gray-500">崩盘目标</span><span class="font-mono text-[#ef4444]">$' + (crash.to_price || 0).toLocaleString() + '</span></div>';
        html += '<div class="flex justify-between"><span class="text-gray-500">跌幅</span><span class="font-mono text-[#ef4444]">' + (crash.drop_pct || 0) + '%</span></div>';
        html += '</div></div>';

        // 右：损失分析
        html += '<div class="p-3 rounded-lg bg-gray-800 border border-gray-700/30">';
        html += '<div class="text-xs font-semibold text-gray-400 mb-2">💥 损失分解</div>';
        html += '<div class="space-y-1 text-xs">';
        html += '<div class="flex justify-between"><span class="text-gray-500">本金损失</span><span class="font-mono text-[#ef4444]">$' + (loss.intrinsic_loss || 0).toLocaleString() + '</span></div>';
        html += '<div class="flex justify-between"><span class="text-gray-500">Vega 冲击</span><span class="font-mono text-[#7132f5]">$' + (loss.vega_impact || 0).toLocaleString() + '</span></div>';
        html += '<div class="flex justify-between"><span class="text-gray-500">总损失</span><span class="font-mono text-[#ef4444] font-bold">$' + (loss.total_loss || 0).toLocaleString() + '</span></div>';
        html += '<div class="flex justify-between"><span class="text-gray-500">损失比例</span><span class="font-mono text-[#ef4444]">' + (loss.loss_pct || 0) + '%</span></div>';
        html += '</div></div>';
        html += '</div>';

        // ===== 最佳恢复方案 =====
        var best = d.best_plan;
        if (best) {
            var planBorder = best.status === 'success' ? 'border-[#149e61]/40' : best.status === 'partial' ? 'border-[#f59e0b]/40' : 'border-[#ef4444]/40';
            var planBg = best.status === 'success' ? 'bg-[#149e61]/10' : best.status === 'partial' ? 'bg-[#f59e0b]/10' : 'bg-[#ef4444]/10';
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
            html += '<div><span class="text-gray-500 block">所需保证金</span><span class="font-mono text-[#f59e0b]">$' + best.margin_required.toLocaleString() + '</span></div>';
            var nc = best.net_recovery >= 0 ? 'text-[#149e61]' : 'text-[#ef4444]';
            html += '<div><span class="text-gray-500 block">净恢复</span><span class="font-mono ' + nc + '">$' + best.net_recovery.toLocaleString() + '</span></div>';
            html += '</div>';
            html += '<div class="grid grid-cols-2 gap-2 text-xs mt-2">';
            var rc = best.remaining_reserve >= 0 ? 'text-[#149e61]' : 'text-[#ef4444]';
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
                var stClass = p.status === 'success' ? 'text-[#149e61]' : p.status === 'partial' ? 'text-[#f59e0b]' : 'text-[#ef4444]';
                var stText = p.status === 'success' ? '✅' : p.status === 'partial' ? '⚠️' : '🔴';
                var nc2 = p.net_recovery >= 0 ? 'text-[#149e61]' : 'text-[#ef4444]';
                html += '<tr class="hover:bg-gray-800/30">';
                html += '<td class="py-1 px-2 text-left font-mono text-gray-300">' + safeHTML(p.symbol || '-') + '</td>';
                html += '<td class="py-1 px-2 font-mono">$' + p.strike.toLocaleString() + '</td>';
                html += '<td class="py-1 px-2 font-mono">' + p.dte + 'D</td>';
                html += '<td class="py-1 px-2 font-mono">' + p.apr + '%</td>';
                html += '<td class="py-1 px-2 font-mono">$' + p.premium_per_contract + '</td>';
                html += '<td class="py-1 px-2 font-mono">' + p.contracts + 'x</td>';
                html += '<td class="py-1 px-2 font-mono text-[#f59e0b]">$' + p.margin_required.toLocaleString() + '</td>';
                html += '<td class="py-1 px-2 font-mono ' + nc2 + '">$' + p.net_recovery.toLocaleString() + '</td>';
                html += '<td class="py-1 px-2 ' + stClass + '">' + stText + '</td>';
                html += '</tr>';
            });
            html += '</tbody></table></div></div>';
        }

        if (d.total_candidates === 0) {
            html += '<div class="text-[#f59e0b] text-xs mt-2 p-2 bg-[#f59e0b]/10 rounded">⚠️ 该价格水平下无可用恢复合约（链上无深度或IV过高）</div>';
        }

        resultDiv.innerHTML = html;
    } catch(e) {
        resultDiv.innerHTML = '<div class="text-[#ef4444] text-sm p-3">❌ 错误: ' + safeHTML(e.message) + '</div>';
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
                    y: { title: { display: true, text: 'PCR', color: '#9497a9' }, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9497a9' } },
                    x: { grid: { display: false }, ticks: { color: '#9497a9', maxTicksLimit: 8 } }
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
// AI 研判中心 (LLM Analyst)
// =========================================================================

function initLLMAnalystSection() {
    const analyzeBtn = document.getElementById('llmAnalyzeBtn');
    const quickBtn = document.getElementById('llmQuickBtn');
    const configToggle = document.getElementById('llmConfigToggle');
    const saveConfigBtn = document.getElementById('llmSaveConfig');
    const testConnBtn = document.getElementById('llmTestConn');
    const toggleRuleBtn = document.getElementById('llmToggleRuleAgents');

    if (analyzeBtn) analyzeBtn.addEventListener('click', () => runLLMAnalysis('full'));
    if (quickBtn) quickBtn.addEventListener('click', () => runLLMAnalysis('quick'));
    if (configToggle) configToggle.addEventListener('click', toggleLLMConfig);
    if (saveConfigBtn) saveConfigBtn.addEventListener('click', saveLLMConfig);
    if (testConnBtn) testConnBtn.addEventListener('click', testLLMConnection);
    if (toggleRuleBtn) toggleRuleBtn.addEventListener('click', toggleRuleAgents);

    loadLLMConfigStatus();
}

function toggleLLMConfig() {
    const panel = document.getElementById('llmConfigPanel');
    panel.classList.toggle('hidden');
}

function toggleRuleAgents() {
    const content = document.getElementById('llmRuleAgentsContent');
    const icon = document.getElementById('llmRuleAgentsIcon');
    content.classList.toggle('hidden');
    icon.style.transform = content.classList.contains('hidden') ? '' : 'rotate(90deg)';
}

async function loadLLMConfigStatus() {
    try {
        const resp = await safeFetch(`${API_BASE}/api/llm-analyst/config`);
        if (resp.ok) {
            const config = await resp.json();
            const status = document.getElementById('llmConfigStatus');
            if (config.api_key && config.api_key !== '****') {
                status.textContent = config.model ? `已配置 (${config.model})` : '已配置';
                status.className = 'text-xs ml-2 text-[#149e61]';
            } else if (config.api_key === '****') {
                status.textContent = config.model ? `已配置 (${config.model})` : '已配置';
                status.className = 'text-xs ml-2 text-[#149e61]';
                if (config.base_url) document.getElementById('llmBaseUrl').value = config.base_url;
                if (config.model) document.getElementById('llmModel').value = config.model;
            } else {
                status.textContent = '未配置';
                status.className = 'text-xs ml-2 text-[#f59e0b]';
            }
        }
    } catch (e) {
        // silent
    }
}

async function saveLLMConfig() {
    const apiKey = document.getElementById('llmApiKey').value.trim();
    const baseUrl = document.getElementById('llmBaseUrl').value.trim();
    const model = document.getElementById('llmModel').value.trim();

    if (!apiKey) {
        showAlert('请输入 API Key', 'warning');
        return;
    }

    try {
        const resp = await safeFetch(`${API_BASE}/api/llm-analyst/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: apiKey, base_url: baseUrl, model: model }),
        });

        if (resp.ok) {
            showAlert('配置已保存', 'success');
            document.getElementById('llmApiKey').value = '';
            loadLLMConfigStatus();
        } else {
            const err = await resp.json();
            showAlert('保存失败: ' + (err.detail || '未知错误'), 'error');
        }
    } catch (e) {
        showAlert('保存失败: ' + e.message, 'error');
    }
}

async function testLLMConnection() {
    const apiKey = document.getElementById('llmApiKey').value.trim();
    const baseUrl = document.getElementById('llmBaseUrl').value.trim();
    const model = document.getElementById('llmModel').value.trim();

    const resultSpan = document.getElementById('llmTestResult');
    resultSpan.textContent = '测试中...';
    resultSpan.className = 'text-xs ml-2 text-gray-400';

    try {
        const resp = await safeFetch(`${API_BASE}/api/llm-analyst/test`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: apiKey, base_url: baseUrl, model: model }),
        });

        const data = await resp.json();
        if (data.success) {
            resultSpan.textContent = `连接成功 (${data.latency_ms}ms)`;
            resultSpan.className = 'text-xs ml-2 text-[#149e61]';
        } else {
            resultSpan.textContent = `失败: ${data.error || '未知错误'}`;
            resultSpan.className = 'text-xs ml-2 text-[#ef4444]';
        }
    } catch (e) {
        resultSpan.textContent = '连接失败: ' + e.message;
        resultSpan.className = 'text-xs ml-2 text-[#ef4444]';
    }
}

async function runLLMAnalysis(mode) {
    const currency = document.getElementById('llmCurrency').value;
    const analyzeBtn = document.getElementById('llmAnalyzeBtn');
    const quickBtn = document.getElementById('llmQuickBtn');
    const progress = document.getElementById('llmProgress');
    const empty = document.getElementById('llmEmpty');
    const results = document.getElementById('llmResults');

    analyzeBtn.disabled = true;
    quickBtn.disabled = true;
    analyzeBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> <span>分析中...</span>';
    progress.classList.remove('hidden');
    empty.classList.add('hidden');
    results.classList.add('hidden');

    resetLLMProgress();

    try {
        const resp = await safeFetch(`${API_BASE}/api/llm-analyst/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ currency: currency, mode: mode }),
            timeout: 300000, // 5 分钟，LLM 分析需要较长时间
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || `HTTP ${resp.status}`);
        }

        const data = await resp.json();

        setLLMStepComplete(1);
        setLLMStepComplete(2);
        if (mode === 'full') setLLMStepComplete(3);
        setLLMStepComplete(4);
        document.getElementById('llmProgressBar').style.width = '100%';

        results.classList.remove('hidden');
        renderLLMSynthesis(data.synthesis);
        if (data.debate) renderLLMDebate(data.debate);
        renderLLMAudit(data.audit);
        renderLLMRuleAgents(data.rule_reports);

    } catch (e) {
        console.error('LLM analysis failed:', e);
        showAlert('分析失败: ' + e.message, 'error');
        empty.classList.remove('hidden');
    } finally {
        analyzeBtn.disabled = false;
        quickBtn.disabled = false;
        analyzeBtn.innerHTML = '<i class="fas fa-play"></i> <span>开始分析</span>';
        setTimeout(() => progress.classList.add('hidden'), 2000);
    }
}

function resetLLMProgress() {
    const labels = ['规则分析', '综合研判', '多空辩论', '数据审计'];
    for (let i = 1; i <= 4; i++) {
        const step = document.getElementById(`llmStep${i}`);
        step.className = 'flex items-center gap-1.5 text-xs text-gray-500';
        step.innerHTML = '<i class="far fa-circle"></i> ' + labels[i-1];
    }
    document.getElementById('llmProgressBar').style.width = '0%';
}

function setLLMStepComplete(stepNum) {
    const labels = ['规则分析', '综合研判', '多空辩论', '数据审计'];
    const step = document.getElementById(`llmStep${stepNum}`);
    step.className = 'flex items-center gap-1.5 text-xs text-[#149e61]';
    step.innerHTML = '<i class="fas fa-check-circle"></i> ' + labels[stepNum-1];
    const progress = (stepNum / 4) * 100;
    document.getElementById('llmProgressBar').style.width = progress + '%';
}

function renderLLMSynthesis(synthesis) {
    const card = document.getElementById('llmSynthesisCard');
    const content = document.getElementById('llmSynthesisContent');
    const confSpan = document.getElementById('llmSynthesisConfidence');

    if (!synthesis || !synthesis.success) {
        card.classList.add('hidden');
        return;
    }

    card.classList.remove('hidden');

    const conf = synthesis.confidence || 0;
    confSpan.textContent = `信心度 ${conf}%`;
    confSpan.className = `text-xs px-2 py-0.5 rounded-full ${conf >= 70 ? 'bg-[#149e61]/20 text-[#149e61]' : conf >= 40 ? 'bg-[#f59e0b]/20 text-[#f59e0b]' : 'bg-[#ef4444]/20 text-[#ef4444]'} ml-auto`;

    let html = '';
    if (synthesis.market_assessment) {
        html += `<div><span class="text-[#7132f5] font-medium">市场评估：</span><span>${safeHTML(synthesis.market_assessment)}</span></div>`;
    }
    if (synthesis.strategy_recommendation) {
        html += `<div><span class="text-[#7132f5] font-medium">策略建议：</span><span>${safeHTML(synthesis.strategy_recommendation)}</span></div>`;
    }
    if (synthesis.risk_warning) {
        html += `<div><span class="text-[#ef4444] font-medium">风险提示：</span><span>${safeHTML(synthesis.risk_warning)}</span></div>`;
    }
    content.innerHTML = html;
}

function renderLLMDebate(debate) {
    const card = document.getElementById('llmDebateCard');
    if (!debate || !debate.success) {
        card.classList.remove('hidden');
        const bull = document.getElementById('llmBullContent');
        const bear = document.getElementById('llmBearContent');
        const judge = document.getElementById('llmJudgeContent');
        if (bull) bull.innerHTML = '<p class="text-gray-500">LLM 未配置或分析失败</p>';
        if (bear) bear.innerHTML = '<p class="text-gray-500">LLM 未配置或分析失败</p>';
        if (judge) judge.innerHTML = '<p class="text-gray-500">无法完成辩论裁决</p>';
        return;
    }
    card.classList.remove('hidden');

    // Bull
    const bull = debate.bull || {};
    document.getElementById('llmBullConf').textContent = bull.success ? `${bull.confidence || 0}%` : '失败';
    let bullHtml = '';
    if (bull.bullish_case) bullHtml += `<p>${safeHTML(bull.bullish_case)}</p>`;
    if (bull.key_drivers && bull.key_drivers.length) {
        bullHtml += '<ul class="list-disc list-inside mt-1">';
        for (const d of bull.key_drivers) bullHtml += `<li>${safeHTML(d)}</li>`;
        bullHtml += '</ul>';
    }
    document.getElementById('llmBullContent').innerHTML = bullHtml || '<p class="text-gray-500">分析失败</p>';

    // Bear
    const bear = debate.bear || {};
    document.getElementById('llmBearConf').textContent = bear.success ? `${bear.confidence || 0}%` : '失败';
    let bearHtml = '';
    if (bear.bearish_case) bearHtml += `<p>${safeHTML(bear.bearish_case)}</p>`;
    if (bear.key_risks && bear.key_risks.length) {
        bearHtml += '<ul class="list-disc list-inside mt-1">';
        for (const r of bear.key_risks) bearHtml += `<li>${safeHTML(r)}</li>`;
        bearHtml += '</ul>';
    }
    document.getElementById('llmBearContent').innerHTML = bearHtml || '<p class="text-gray-500">分析失败</p>';

    // Judge
    const judge = debate.judge || {};
    let judgeHtml = '';
    if (judge.judge_verdict) judgeHtml += `<p class="font-medium">${safeHTML(judge.judge_verdict)}</p>`;
    if (judge.winner) {
        const winnerColors = { bull: 'text-[#149e61]', bear: 'text-[#ef4444]', draw: 'text-[#f59e0b]' };
        const winnerLabels = { bull: '多头胜', bear: '空头胜', draw: '平局' };
        judgeHtml += `<p class="${winnerColors[judge.winner] || 'text-gray-300'} font-bold mt-2">${winnerLabels[judge.winner] || judge.winner}</p>`;
    }
    if (judge.reasoning) judgeHtml += `<p class="mt-1 text-gray-400">${safeHTML(judge.reasoning)}</p>`;
    document.getElementById('llmJudgeContent').innerHTML = judgeHtml || '<p class="text-gray-500">裁决生成中</p>';
}

function renderLLMAudit(audit) {
    const card = document.getElementById('llmAuditCard');
    if (!audit) {
        card.classList.add('hidden');
        return;
    }
    card.classList.remove('hidden');

    const score = audit.data_quality_score || 0;
    const scoreEl = document.getElementById('llmAuditScore');
    const fillEl = document.getElementById('llmAuditScoreFill');

    scoreEl.textContent = score;
    scoreEl.className = `text-lg font-bold ${score >= 80 ? 'text-[#149e61]' : score >= 50 ? 'text-[#f59e0b]' : 'text-[#ef4444]'}`;
    fillEl.style.width = score + '%';
    fillEl.className = `h-full rounded-full transition-all ${score >= 80 ? 'bg-[#149e61]' : score >= 50 ? 'bg-[#f59e0b]' : 'bg-[#ef4444]'}`;

    const content = document.getElementById('llmAuditContent');
    let html = '';

    // Show error if audit failed
    if (audit.error) {
        html = `<div class="text-[#f59e0b] text-sm"><i class="fas fa-exclamation-circle mr-1"></i>审计未完成: ${safeHTML(audit.error)}</div>`;
        content.innerHTML = html;
        return;
    }

    const anomalies = audit.anomalies || [];
    const issues = audit.logic_issues || [];

    if (anomalies.length === 0 && issues.length === 0) {
        html = '<div class="text-[#149e61] text-sm"><i class="fas fa-check-circle mr-1"></i>未发现数据异常</div>';
    } else {
        for (const a of anomalies) {
            const sevColors = { critical: 'red', warning: 'yellow', info: 'blue' };
            const sevIcons = { critical: 'exclamation-triangle', warning: 'exclamation-circle', info: 'info-circle' };
            const color = sevColors[a.severity] || 'gray';
            const icon = sevIcons[a.severity] || 'info-circle';
            html += `<div class="flex items-start gap-2 p-2 rounded bg-${color}-900/20 border border-${color}-500/20 text-sm">`;
            html += `<i class="fas fa-${icon} text-${color}-400 mt-0.5"></i>`;
            html += `<div><span class="text-${color}-300 font-medium">[${safeHTML(a.source || '')}]</span> ${safeHTML(a.description || '')}`;
            if (a.suggestion) html += `<div class="text-xs text-gray-400 mt-1">建议: ${safeHTML(a.suggestion)}</div>`;
            html += `</div></div>`;
        }
        for (const i of issues) {
            const sevColors = { critical: 'red', warning: 'yellow', info: 'blue' };
            const color = sevColors[i.severity] || 'gray';
            html += `<div class="flex items-start gap-2 p-2 rounded bg-${color}-900/20 border border-${color}-500/20 text-sm">`;
            html += `<i class="fas fa-cog text-${color}-400 mt-0.5"></i>`;
            html += `<div><span class="text-${color}-300 font-medium">[${safeHTML(i.component || '')}]</span> ${safeHTML(i.description || '')}`;
            if (i.suggestion) html += `<div class="text-xs text-gray-400 mt-1">建议: ${safeHTML(i.suggestion)}</div>`;
            html += `</div></div>`;
        }
    }

    content.innerHTML = html;
}

function renderLLMRuleAgents(ruleReports) {
    const countSpan = document.getElementById('llmRuleAgentsCount');
    const content = document.getElementById('llmRuleAgentsContent');

    const reports = ruleReports?.reports || [];
    countSpan.textContent = `(${reports.length} 个 Agent)`;

    let html = '';
    const agentColors = {
        '\u{1f402} 多头分析师': { bg: 'green', icon: '\u{1f402}' },
        '\u{1f43b} 空头分析师': { bg: 'red', icon: '\u{1f43b}' },
        '\u{1f4ca} 波动率分析师': { bg: 'blue', icon: '\u{1f4ca}' },
        '\u{1f40b} 资金流向分析师': { bg: 'purple', icon: '\u{1f40b}' },
        '\u{1f6e1}️ 风险官': { bg: 'yellow', icon: '\u{1f6e1}️' },
    };

    for (const r of reports) {
        const colors = agentColors[r.name] || { bg: 'gray', icon: '\u{1f916}' };
        const score = r.score || 0;
        const scoreColor = score > 20 ? 'text-[#149e61]' : score > 0 ? 'text-[#149e61]' : score > -20 ? 'text-[#f59e0b]' : 'text-[#ef4444]';

        html += `<div class="card-glass rounded-lg p-3 border-l-4 border-${colors.bg}-500/60">`;
        html += `<div class="flex items-center justify-between mb-2">`;
        html += `<div class="flex items-center gap-1.5"><span>${colors.icon}</span><span class="text-xs font-semibold">${safeHTML(r.name)}</span></div>`;
        html += `<span class="text-sm font-bold ${scoreColor}">${score > 0 ? '+' : ''}${score}</span>`;
        html += `</div>`;
        html += `<div class="text-[10px] text-gray-400 mb-1">${safeHTML(r.verdict || '')} · 置信度 ${r.confidence || 0}%</div>`;
        html += `<ul class="text-[10px] text-gray-300 space-y-0.5">`;
        for (const pt of (r.key_points || []).slice(0, 3)) {
            html += `<li>• ${safeHTML(pt)}</li>`;
        }
        html += `</ul></div>`;
    }

    content.innerHTML = html;
}

// 初始化 AI 研判中心
initLLMAnalystSection();


// ============================================================
// IV 波动率微笑图 + Greeks 风险矩阵
// ============================================================

let _ivSmileChart = null;

async function loadIVSmile() {
    const canvas = document.getElementById('ivSmileCanvas');
    const analysisDiv = document.getElementById('ivSmileAnalysis');
    if (!canvas) return;

    if (analysisDiv) analysisDiv.innerHTML = '';

    try {
        const currency = document.getElementById('ivSmileCurrency')?.value || 'BTC';
        const resp = await safeFetch(`${API_BASE}/api/charts/iv-smile?currency=${currency}`);
        const data = await resp.json();

        if (data.error) {
            if (_ivSmileChart) { _ivSmileChart.destroy(); _ivSmileChart = null; }
            const ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            if (analysisDiv) analysisDiv.innerHTML = `<div class="text-[#f59e0b] text-sm py-4 text-center">${safeHTML(data.error)}</div>`;
            return;
        }

        const smiles = data.smiles || {};
        const spot = data.spot || 0;
        const expiryKeys = Object.keys(smiles).sort((a, b) => smiles[a].dte - smiles[b].dte);

        if (expiryKeys.length === 0) return;

        const colors = ['#ef4444', '#f59e0b', '#3b82f6'];
        const dashPatterns = [[], [5, 5], [10, 5]];
        const datasets = [];

        expiryKeys.forEach((key, i) => {
            const smile = smiles[key];
            const all = (smile.all || []).sort((a, b) => a.strike - b.strike);
            if (all.length === 0) return;

            datasets.push({
                label: `${smile.dte}D`,
                data: all.map(p => ({ x: p.strike, y: p.iv })),
                borderColor: colors[i % colors.length],
                backgroundColor: colors[i % colors.length] + '20',
                borderDash: dashPatterns[i % dashPatterns.length],
                tension: 0.3,
                borderWidth: 2,
                pointRadius: 3,
                pointHoverRadius: 6,
                fill: false,
            });
        });

        // ATM vertical line (no annotation plugin — use dataset)
        if (spot > 0 && datasets.length > 0) {
            const allIvs = datasets.flatMap(d => d.data.map(p => p.y));
            const minY = Math.min(...allIvs);
            const maxY = Math.max(...allIvs);
            datasets.push({
                label: 'ATM',
                data: [{ x: spot, y: minY }, { x: spot, y: maxY }],
                borderColor: '#f59e0b',
                borderWidth: 1,
                borderDash: [4, 4],
                pointRadius: 0,
                fill: false,
            });
        }

        if (_ivSmileChart) _ivSmileChart.destroy();

        _ivSmileChart = new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: { datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'nearest', intersect: false },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        labels: { color: '#9497a9', usePointStyle: true, pointStyle: 'line', padding: 15, font: { size: 11 } }
                    },
                    tooltip: {
                        backgroundColor: '#1a1b23',
                        titleColor: '#e4e4e7',
                        bodyColor: '#9497a9',
                        borderColor: '#333',
                        borderWidth: 1,
                        callbacks: {
                            title: (items) => `$${items[0].parsed.x.toLocaleString()}`,
                            label: (item) => `${item.dataset.label}: IV ${item.parsed.y.toFixed(2)}%`,
                        }
                    },
                },
                scales: {
                    x: {
                        type: 'linear',
                        title: { display: true, text: 'Strike', color: '#686b82', font: { size: 11 } },
                        ticks: {
                            color: '#686b82',
                            callback: (v) => '$' + v.toLocaleString(),
                            maxTicksLimit: 10,
                        },
                        grid: { color: 'rgba(255,255,255,0.05)' },
                    },
                    y: {
                        title: { display: true, text: 'IV %', color: '#686b82', font: { size: 11 } },
                        ticks: {
                            color: '#686b82',
                            callback: (v) => v.toFixed(0) + '%',
                        },
                        grid: { color: 'rgba(255,255,255,0.05)' },
                    },
                },
            },
        });

        // Render analysis panel
        if (analysisDiv && data.analysis) {
            renderIVSmileAnalysis(analysisDiv, data.analysis, spot);
        }
    } catch (e) {
        if (_ivSmileChart) { _ivSmileChart.destroy(); _ivSmileChart = null; }
        if (analysisDiv) analysisDiv.innerHTML = `<div class="text-[#ef4444] text-sm py-4 text-center">加载失败: ${safeHTML(e.message)}</div>`;
    }
}

function renderIVSmileAnalysis(container, analysis, spot) {
    const sent = analysis.sentiment || {};
    const met = analysis.metrics || {};
    const formIcon = analysis.form_icon || '';
    const formLabel = analysis.form_label || '';

    let html = `<div class="bg-[#22232e]/50 rounded-xl border border-[rgba(71,73,85,0.3)] p-4 space-y-4">`;

    // Row 1: Form + Sentiment + ATM IV
    html += `<div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3">
            <span class="text-lg">${formIcon}</span>
            <span class="text-sm font-medium text-[#e4e4e7]">${safeHTML(formLabel)}</span>
        </div>
        <div class="flex items-center gap-2">
            <span class="text-sm" style="color:${sent.color || '#9497a9'}">${sent.icon || ''} ${safeHTML(sent.label || '')}</span>
        </div>
        <div class="text-sm text-[#9497a9]">ATM IV: <span class="text-[#e4e4e7] font-bold">${met.atm_iv?.toFixed(1) || '--'}%</span></div>
    </div>`;

    // Row 2: Key metrics
    html += `<div class="grid grid-cols-3 gap-3 text-center">
        <div class="bg-[#1a1b23]/50 rounded-lg p-2">
            <div class="text-[10px] text-[#686b82]">25Δ Skew</div>
            <div class="text-sm font-bold ${(met.skew_25d || 0) > 0 ? 'text-[#ef4444]' : 'text-[#149e61]'}">${met.skew_25d > 0 ? '+' : ''}${met.skew_25d?.toFixed(1) || '--'}</div>
        </div>
        <div class="bg-[#1a1b23]/50 rounded-lg p-2">
            <div class="text-[10px] text-[#686b82]">Put 偏度</div>
            <div class="text-sm font-bold ${(met.put_skew_pct || 0) > 0 ? 'text-[#ef4444]' : 'text-[#149e61]'}">${met.put_skew_pct > 0 ? '+' : ''}${met.put_skew_pct?.toFixed(1) || '--'}%</div>
        </div>
        <div class="bg-[#1a1b23]/50 rounded-lg p-2">
            <div class="text-[10px] text-[#686b82]">曲度</div>
            <div class="text-sm font-bold text-[#e4e4e7]">${met.curvature?.toFixed(1) || '--'}%</div>
        </div>
    </div>`;

    // Row 3: By-expiry table
    const byExpiry = analysis.by_expiry || [];
    if (byExpiry.length > 0) {
        html += `<div class="overflow-x-auto"><table class="w-full text-xs">
            <thead><tr class="text-[#686b82] border-b border-gray-700/50">
                <th class="text-left py-1.5 px-2">到期</th>
                <th class="text-right py-1.5 px-2">ATM IV</th>
                <th class="text-right py-1.5 px-2">25Δ Skew</th>
                <th class="text-center py-1.5 px-2">形态</th>
                <th class="text-right py-1.5 px-2">点数</th>
            </tr></thead><tbody>`;
        for (const e of byExpiry) {
            html += `<tr class="border-b border-gray-800/30">
                <td class="py-1.5 px-2 text-[#e4e4e7]">${e.dte}D</td>
                <td class="py-1.5 px-2 text-right text-[#e4e4e7]">${e.atm_iv?.toFixed(1)}%</td>
                <td class="py-1.5 px-2 text-right ${(e.skew_25d || 0) > 0 ? 'text-[#ef4444]' : 'text-[#149e61]'}">${e.skew_25d > 0 ? '+' : ''}${e.skew_25d?.toFixed(1)}</td>
                <td class="py-1.5 px-2 text-center text-[#9497a9]">${safeHTML(e.form_label || e.form)}</td>
                <td class="py-1.5 px-2 text-right text-[#9497a9]">${e.point_count}</td>
            </tr>`;
        }
        html += `</tbody></table></div>`;
    }

    // Row 4: Recommendations
    const recs = analysis.recommendations || [];
    if (recs.length > 0) {
        html += `<div class="space-y-2">
            <div class="text-xs font-semibold text-[#7132f5]"><i class="fas fa-lightbulb mr-1"></i>策略建议</div>`;
        for (const r of recs) {
            const confColor = r.confidence === 'HIGH' ? '#149e61' : '#f59e0b';
            html += `<div class="flex items-start gap-2 bg-[#1a1b23]/40 rounded-lg p-2.5 border-l-2" style="border-color:${confColor}">
                <span class="text-xs font-bold px-1.5 py-0.5 rounded" style="color:${confColor}; background:${confColor}15">${r.confidence}</span>
                <div class="flex-1 min-w-0">
                    <div class="text-xs font-medium text-[#e4e4e7]">${safeHTML(r.title)}</div>
                    <div class="text-[11px] text-[#9497a9] mt-0.5">${safeHTML(r.body)}</div>
                    <div class="text-[11px] text-[#686b82] mt-0.5"><i class="fas fa-crosshairs mr-1"></i>${safeHTML(r.action)}</div>
                </div>
            </div>`;
        }
        html += `</div>`;
    }

    html += `</div>`;
    container.innerHTML = html;
}

async function loadGreeksSummary() {
    const grid = document.getElementById('greeksGrid');
    const statusBar = document.getElementById('greeksStatusBar');
    const analysisDiv = document.getElementById('greeksAnalysis');
    if (!grid) return;

    grid.innerHTML = '<div class="text-gray-400 text-sm py-4 text-center">加载中...</div>';

    try {
        const currency = document.getElementById('greeksCurrency')?.value || 'BTC';
        const resp = await safeFetch(`${API_BASE}/api/charts/greeks-summary?currency=${currency}`);
        const data = await resp.json();
        if (data.error) {
            grid.innerHTML = `<div class="text-[#f59e0b] text-sm">${safeHTML(data.error)}</div>`;
            if (statusBar) statusBar.innerHTML = '';
            if (analysisDiv) analysisDiv.innerHTML = '';
            return;
        }

        const gs = data.greeks_summary || {};
        const per = gs.per_contract || {};
        const total = gs.total_exposure || {};
        const gex = data.gex || {};
        const scenarios = data.scenarios || {};
        const analysis = data.analysis;

        // Status bar
        if (analysis && statusBar) {
            const gexR = analysis.gex_regime || {};
            const pinR = analysis.pin_risk || {};
            const ms = analysis.market_state || {};
            const thetaPerDay = per.theta || 0;
            const thetaColor = thetaPerDay < -100 ? '#ef4444' : thetaPerDay < -50 ? '#f59e0b' : '#149e61';
            statusBar.innerHTML = `
                <div class="bg-gray-800/50 rounded-lg p-3 text-center">
                    <div class="text-lg">${gexR.icon || ''} ${gexR.label || '--'}</div>
                    <div class="text-xs text-gray-400">GEX Regime</div>
                </div>
                <div class="bg-gray-800/50 rounded-lg p-3 text-center">
                    <div class="text-lg">${pinR.icon || ''} ${pinR.label || '--'}</div>
                    <div class="text-xs text-gray-400">Pin Risk</div>
                </div>
                <div class="bg-gray-800/50 rounded-lg p-3 text-center">
                    <div class="text-lg" style="color:${ms.color || '#9497a9'}">${ms.icon || ''} ${ms.label || '--'}</div>
                    <div class="text-xs text-gray-400">Market State</div>
                </div>
                <div class="bg-gray-800/50 rounded-lg p-3 text-center">
                    <div class="text-lg font-bold" style="color:${thetaColor}">$${thetaPerDay.toFixed(2)}/天</div>
                    <div class="text-xs text-gray-400">Theta/Day</div>
                </div>`;
        }

        // GEX Chart
        renderGEXChart(gex, data.spot);

        // Greeks Curves Chart
        renderGreeksCurvesChart(data.by_expiry || []);

        // Greeks Overview Grid
        const riskRatings = analysis?.risk_ratings || {};
        grid.innerHTML = `
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                ${['delta', 'gamma', 'theta', 'vega'].map(g => {
                    const rr = riskRatings[g] || {};
                    const val = per[g] || 0;
                    const color = rr.level === 'HIGH' ? '#ef4444' : rr.level === 'MEDIUM' ? '#f59e0b' : '#149e61';
                    const labels = {delta: 'Delta (Δ)', gamma: 'Gamma (Γ)', theta: 'Theta (Θ)', vega: 'Vega (V)'};
                    const fmt = g === 'gamma' ? val.toFixed(6) : g === 'delta' ? val.toFixed(4) : '$' + val.toFixed(2);
                    return `<div class="bg-gray-800/50 rounded-lg p-3 text-center">
                        <div class="text-xs text-gray-400">${labels[g]}</div>
                        <div class="text-xl font-bold" style="color:${color}">${fmt}</div>
                        <div class="text-xs" style="color:${color}">${rr.label || '--'}</div>
                    </div>`;
                }).join('')}
            </div>
            <div class="bg-gray-800/30 rounded-lg p-3 mb-3">
                <div class="text-sm font-medium text-gray-300 mb-2">总风险敞口 (OI 加权)</div>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                    <div class="text-center"><div class="text-gray-400">总 Delta</div>
                        <div class="text-lg font-bold ${total.delta > 0 ? 'text-[#149e61]' : 'text-[#ef4444]'}">${total.delta?.toLocaleString() || 0}</div></div>
                    <div class="text-center"><div class="text-gray-400">总 Gamma</div>
                        <div class="text-lg font-bold text-[#7132f5]">${total.gamma?.toFixed(4) || 0}</div></div>
                    <div class="text-center"><div class="text-gray-400">总 Theta</div>
                        <div class="text-lg font-bold ${total.theta > 0 ? 'text-[#149e61]' : 'text-[#ef4444]'}">$${total.theta?.toLocaleString() || 0}</div></div>
                    <div class="text-center"><div class="text-gray-400">总 Vega</div>
                        <div class="text-lg font-bold text-[#7132f5]">$${total.vega?.toLocaleString() || 0}</div></div>
                </div>
            </div>
            <div class="bg-gray-800/30 rounded-lg p-3">
                <div class="text-sm font-medium text-gray-300 mb-2">情景分析</div>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
                    <div class="flex justify-between"><span class="text-gray-400">若 ${currency} 下跌 10%</span>
                        <span class="${scenarios.down_10pct < 0 ? 'text-[#ef4444]' : 'text-[#149e61]'}">${scenarios.down_10pct < 0 ? '' : '+'}$${scenarios.down_10pct?.toLocaleString() || 0}</span></div>
                    <div class="flex justify-between"><span class="text-gray-400">若 ${currency} 上涨 10%</span>
                        <span class="${scenarios.up_10pct > 0 ? 'text-[#149e61]' : 'text-[#ef4444]'}">+$${scenarios.up_10pct?.toLocaleString() || 0}</span></div>
                    <div class="flex justify-between"><span class="text-gray-400">若 IV 上升 5%</span>
                        <span class="text-[#149e61]">+$${scenarios.iv_up_5pct?.toLocaleString() || 0}</span></div>
                    <div class="flex justify-between"><span class="text-gray-400">若 IV 下降 5%</span>
                        <span class="text-[#ef4444]">$${scenarios.iv_down_5pct?.toLocaleString() || 0}</span></div>
                </div>
                <div class="mt-2 text-xs text-gray-500">
                    合约: ${data.contract_count}个 (${data.put_count} Put / ${data.call_count} Call) | 总 OI: ${(data.total_oi || 0).toLocaleString()}
                </div>
            </div>`;

        // Analysis Panel
        if (analysis && analysisDiv) {
            renderGreeksAnalysis(analysis);
        } else if (analysisDiv) {
            analysisDiv.innerHTML = '';
        }
    } catch (e) {
        grid.innerHTML = `<div class="text-[#ef4444] text-sm">加载失败: ${e.message}</div>`;
    }
}

function renderGEXChart(gex, spot) {
    const canvas = document.getElementById('gexCanvas');
    if (!canvas || !gex.by_strike || gex.by_strike.length === 0) return;

    if (canvas._chart) canvas._chart.destroy();

    const labels = gex.by_strike.map(e => e.strike.toLocaleString());
    const callGex = gex.by_strike.map(e => e.call_gex);
    const putGex = gex.by_strike.map(e => e.put_gex);
    const netGex = gex.by_strike.map(e => e.net_gex);

    canvas._chart = new Chart(canvas, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                { label: 'Call GEX', data: callGex, backgroundColor: 'rgba(20,158,97,0.7)', stack: 'gex' },
                { label: 'Put GEX', data: putGex, backgroundColor: 'rgba(239,68,68,0.7)', stack: 'gex' },
                { label: 'Net GEX', data: netGex, type: 'line', borderColor: '#3b82f6', borderWidth: 2, pointRadius: 3, fill: false, yAxisID: 'y' },
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                title: { display: true, text: 'GEX by Strike', color: '#9497a9', font: { size: 13 } },
                legend: { labels: { color: '#9497a9', boxWidth: 12 } },
            },
            scales: {
                x: { ticks: { color: '#686b82', maxRotation: 45 }, grid: { color: 'rgba(255,255,255,0.05)' } },
                y: { ticks: { color: '#686b82' }, grid: { color: 'rgba(255,255,255,0.05)' } },
            }
        }
    });
}

function renderGreeksCurvesChart(byExpiry) {
    const canvas = document.getElementById('greeksCurvesCanvas');
    if (!canvas || byExpiry.length === 0) return;

    if (canvas._chart) canvas._chart.destroy();

    const labels = byExpiry.map(e => e.dte + 'D');
    const deltaData = byExpiry.map(e => e.delta);
    const gammaData = byExpiry.map(e => e.gamma);
    const thetaData = byExpiry.map(e => e.theta);
    const vegaData = byExpiry.map(e => e.vega);

    canvas._chart = new Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [
                { label: 'Delta', data: deltaData, borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.1)', fill: false, tension: 0.3 },
                { label: 'Gamma', data: gammaData, borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,0.1)', fill: false, tension: 0.3, yAxisID: 'y' },
                { label: 'Theta', data: thetaData, borderColor: '#ef4444', backgroundColor: 'rgba(239,68,68,0.1)', fill: false, tension: 0.3, yAxisID: 'y1' },
                { label: 'Vega', data: vegaData, borderColor: '#149e61', backgroundColor: 'rgba(20,158,97,0.1)', fill: false, tension: 0.3, yAxisID: 'y1' },
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                title: { display: true, text: 'Greeks by Expiry', color: '#9497a9', font: { size: 13 } },
                legend: { labels: { color: '#9497a9', boxWidth: 12 } },
            },
            scales: {
                x: { ticks: { color: '#686b82' }, grid: { color: 'rgba(255,255,255,0.05)' } },
                y: { position: 'left', title: { display: true, text: 'Delta / Gamma', color: '#686b82' }, ticks: { color: '#686b82' }, grid: { color: 'rgba(255,255,255,0.05)' } },
                y1: { position: 'right', title: { display: true, text: 'Theta / Vega ($)', color: '#686b82' }, ticks: { color: '#686b82' }, grid: { drawOnChartArea: false } },
            }
        }
    });
}

function renderGreeksAnalysis(analysis) {
    const div = document.getElementById('greeksAnalysis');
    if (!div || !analysis) return;

    let html = '<div class="card-glass rounded-xl p-4">';

    // Interpretation
    if (analysis.interpretation && analysis.interpretation.length > 0) {
        html += '<div class="mb-3"><div class="text-sm font-medium text-gray-300 mb-2">📊 市场解读</div>';
        for (const line of analysis.interpretation) {
            html += `<div class="text-xs text-gray-400 mb-1">• ${safeHTML(line)}</div>`;
        }
        html += '</div>';
    }

    // Hedge Suggestions
    if (analysis.hedge_suggestions && analysis.hedge_suggestions.length > 0) {
        html += '<div><div class="text-sm font-medium text-gray-300 mb-2">💡 对冲建议</div>';
        for (const s of analysis.hedge_suggestions) {
            const confColor = s.confidence === 'HIGH' ? '#149e61' : '#f59e0b';
            html += `<div class="bg-gray-800/30 rounded-lg p-3 mb-2 border-l-2" style="border-color:${confColor}">
                <div class="flex items-center gap-2 mb-1">
                    <span class="text-xs font-bold px-1.5 py-0.5 rounded" style="background:${confColor}22;color:${confColor}">${s.confidence}</span>
                    <span class="text-sm font-medium text-gray-200">${safeHTML(s.title)}</span>
                </div>
                <div class="text-xs text-gray-400 mb-1">${safeHTML(s.body)}</div>
                <div class="text-xs text-[#7132f5]">→ ${safeHTML(s.action)}</div>
            </div>`;
        }
        html += '</div>';
    }

    html += '</div>';
    div.innerHTML = html;
}

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    loadIVSmile();
    loadGreeksSummary();

    // 统一投资推荐 — 注入信号灯 + 顶部汇总条
    if (window.Rec) {
        window.Rec.injectAllSignals().catch(() => {});
        window.Rec.renderSummaryBar().catch(() => {});
    }
});
