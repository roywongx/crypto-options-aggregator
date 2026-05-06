/**
 * Freqtrade v3.0 策略引擎前端
 * 组合风险 · 保护守卫 · 参数优化 · 历史回测
 */
import { safeFetch, API_BASE, safeHTML } from './utils.js';

// ── Global State ──────────────────────────────────────────
let _ftTab = 'portfolioRisk';
let _varGaugeChart = null;
let _equityCurveChart = null;
let _protectionsTimer = null;
let _ftInitialized = false;

// ── Guard Icons & Labels ───────────────────────────────────
const GUARD_META = {
    stoploss_guard:        { icon: 'fa-hand', label: '止损守卫', desc: '止损次数 / 价格触发' },
    max_drawdown_guard:    { icon: 'fa-arrow-trend-down', label: '回撤熔断', desc: '全局回撤断路器' },
    consecutive_loss_guard:{ icon: 'fa-rotate-left', label: '连亏冷却', desc: '连续亏损 → 暂停开仓' },
    overtrading_guard:     { icon: 'fa-layer-group', label: '过度交易', desc: '持仓数量上限' },
    var_guard:             { icon: 'fa-chart-pie', label: 'VaR 风险', desc: '组合 VaR 阈值' },
    concentration_guard:   { icon: 'fa-bullseye', label: '集中度', desc: '行权价过度集中' },
};

// ── Tab Switcher ───────────────────────────────────────────
export function setFreqtradeTab(tab) {
    _ftTab = tab;
    document.querySelectorAll('.ft-tab').forEach(el => {
        const isActive = el.dataset.tab === tab;
        el.classList.toggle('active', isActive);
    });
    document.querySelectorAll('.ft-tab-content').forEach(el => el.classList.add('hidden'));
    const active = document.getElementById('ftTab' + tab.charAt(0).toUpperCase() + tab.slice(1));
    if (active) active.classList.remove('hidden');

    _stopProtectionsTimer();
    if (tab === 'portfolioRisk') loadPortfolioRisk();
    else if (tab === 'protections') { loadProtections(); _startProtectionsTimer(); }
}

// ── Tab 1: Portfolio Risk ─────────────────────────────────
export async function loadPortfolioRisk() {
    const loading = document.getElementById('ftRiskLoading');
    const errorEl = document.getElementById('ftRiskError');
    const content = document.getElementById('ftRiskContent');
    try {
        loading?.classList.remove('hidden');
        errorEl?.classList.add('hidden');
        const resp = await safeFetch(API_BASE + '/api/portfolio-risk', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ currency: 'BTC' })
        });
        const data = await resp.json();
        if (!data || !data.success) throw new Error(data?.detail || 'API 返回异常');
        // 先显示容器再渲染图表（Chart.js 需要可见容器才能正确测量尺寸）
        loading?.classList.add('hidden');
        content?.classList.remove('hidden');
        _renderPortfolioRisk(data);
    } catch (e) {
        loading?.classList.add('hidden');
        if (errorEl) { errorEl.classList.remove('hidden'); errorEl.textContent = '⚠ ' + safeHTML(e.message); }
    }
}

function _renderPortfolioRisk(data) {
    // VaR Gauge — half-doughnut 仪表盘
    const canvas = document.getElementById('varGaugeCanvas');
    if (canvas) {
        if (_varGaugeChart) { _varGaugeChart.destroy(); _varGaugeChart = null; }
        const varPct = Math.min(data.var_95_pct || 0, 10);
        const color = varPct > 5 ? '#ef4444' : varPct > 2 ? '#f59e0b' : '#149e61';
        const ctx = canvas.getContext('2d');
        // Ensure canvas is clear before new chart
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        _varGaugeChart = new Chart(ctx, {
            type: 'doughnut',
            data: {
                datasets: [{
                    data: [varPct, Math.max(0.1, 10 - varPct)],
                    backgroundColor: [color, '#2a2b37'],
                    borderWidth: 0,
                }]
            },
            options: {
                rotation: -90,
                circumference: 180,
                cutout: '70%',
                responsive: false,
                maintainAspectRatio: false,
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
            },
            plugins: [{
                id: 'varCenterText',
                afterDraw(chart) {
                    const { ctx: cctx, chartArea } = chart;
                    const w = chartArea.width;
                    const cx = chartArea.left + w / 2;
                    // 半圆底部即为圆弧底部，文字放在底部偏上
                    const textY = chartArea.bottom - 18;
                    cctx.save();
                    cctx.textAlign = 'center';
                    cctx.textBaseline = 'middle';
                    cctx.font = 'bold 20px "JetBrains Mono", monospace';
                    cctx.fillStyle = color;
                    cctx.fillText(data.var_95_pct?.toFixed(2) + '%', cx, textY);
                    cctx.restore();
                }
            }]
        });
    }

    // Risk level badge
    const lvlEl = document.getElementById('ftRiskLevel');
    if (lvlEl) {
        const lvl = data.risk_level || 'NORMAL';
        const bg = lvl === 'HIGH' ? 'bg-[#ef4444]/20 text-[#ef4444]' : lvl === 'ELEVATED' ? 'bg-[#f59e0b]/20 text-[#f59e0b]' : 'bg-[#149e61]/20 text-[#149e61]';
        lvlEl.className = 'text-xs px-2 py-0.5 rounded-full mt-1 font-medium ' + bg;
        lvlEl.textContent = lvl === 'HIGH' ? '高风险' : lvl === 'ELEVATED' ? '偏高' : '正常';
    }

    // Stat cards
    _setText('ftPositionCount', data.position_count ?? '-');
    _setText('ftTotalMargin', '$' + ((data.total_margin_used || 0) / 1000).toFixed(1) + 'K');
    _setText('ftTotalPremium', '$' + ((data.total_premium || 0) / 1000).toFixed(1) + 'K');
    _setText('ftCvar', '$' + ((data.cvar_95 || 0) / 1000).toFixed(1) + 'K');

    // Risk details
    _setText('ftConcentration', data.concentration_risk || 'LOW');
    const concEl = document.getElementById('ftConcentration');
    if (concEl) {
        concEl.style.color = data.concentration_risk === 'DANGER' ? '#ef4444' : data.concentration_risk === 'CAUTION' ? '#f59e0b' : '#149e61';
    }
    _setText('ftBandRatio', ((data.max_strike_band_ratio || 0) * 100).toFixed(0) + '%');
    _setText('ftDrawdown', ((data.drawdown_from_peak || 0) * 100).toFixed(1) + '%');
    _setText('ftStopLoss', '$' + (data.stop_loss_price || 0).toLocaleString(undefined, { maximumFractionDigits: 0 }));

    const cb = document.getElementById('ftCircuitBreaker');
    if (cb) {
        if (data.circuit_breaker_tripped) {
            cb.classList.remove('hidden');
            cb.textContent = '熔断触发: ' + (data.circuit_breaker_reason || '');
        } else {
            cb.classList.add('hidden');
        }
    }
}

function _setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

// ── Tab 2: Protections ────────────────────────────────────
export async function loadProtections() {
    const loading = document.getElementById('ftProtLoading');
    const errorEl = document.getElementById('ftProtError');
    const content = document.getElementById('ftProtContent');
    try {
        loading?.classList.remove('hidden');
        errorEl?.classList.add('hidden');
        const resp = await safeFetch(API_BASE + '/api/strategy/protections-check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ currency: 'BTC', current_equity: 100000, peak_equity: 105000 })
        });
        const data = await resp.json();
        loading?.classList.add('hidden');
        content?.classList.remove('hidden');
        _renderProtections(data);
    } catch (e) {
        loading?.classList.add('hidden');
        if (errorEl) { errorEl.classList.remove('hidden'); errorEl.textContent = '⚠ ' + safeHTML(e.message); }
    }
}

function _renderProtections(data) {
    const summary = data?.summary || {};
    const details = data?.details || {};

    // Summary bar
    const summaryEl = document.getElementById('ftProtSummary');
    if (summaryEl) {
        if (summary.all_clear) {
            summaryEl.innerHTML = '<div class="bg-[#149e61]/10 border border-[#149e61] rounded-lg px-4 py-2.5 text-sm flex items-center gap-2"><i class="fas fa-check-circle text-[#149e61]"></i><span class="text-[#e2e4f0]">全部通过</span><span class="text-[#686b82] text-xs">6 个保护守卫均正常</span></div>';
        } else {
            const critCount = summary.critical_count || 0;
            summaryEl.innerHTML = `<div class="bg-[#ef4444]/10 border border-[#ef4444] rounded-lg px-4 py-2.5 text-sm flex items-center gap-2"><i class="fas fa-exclamation-triangle text-[#ef4444]"></i><span class="text-[#e2e4f0]">${summary.tripped_count} 个守卫触发</span><span class="text-[#f59e0b] text-xs ml-1">${critCount > 0 ? critCount + ' 严重' : ''}</span><span class="text-[#686b82] text-xs ml-auto">${summary.guards_tripped?.join(', ') || ''}</span></div>`;
        }
    }

    // Guard cards
    const cardsEl = document.getElementById('ftProtCards');
    if (!cardsEl) return;
    cardsEl.innerHTML = '';
    for (const [name, meta] of Object.entries(GUARD_META)) {
        const d = details[name] || { tripped: false, severity: 'info', reason: '-' };
        const cls = d.tripped ? (d.severity === 'critical' ? 'tripped-critical' : 'tripped-warning') : 'clear';
        const statusIcon = d.tripped ? (d.severity === 'critical' ? '<i class="fas fa-circle-xmark text-[#ef4444]"></i>' : '<i class="fas fa-triangle-exclamation text-[#f59e0b]"></i>') : '<i class="fas fa-circle-check text-[#149e61]"></i>';
        const card = document.createElement('div');
        card.className = 'guard-card border rounded-lg p-3 ' + cls;
        card.innerHTML = `
            <div class="flex items-center gap-2 mb-1.5">
                <i class="fas ${meta.icon} text-[#686b82] text-sm"></i>
                <span class="text-xs font-medium text-[#e2e4f0]">${meta.label}</span>
                ${statusIcon}
                <span class="text-[10px] text-[#686b82] ml-auto">${meta.desc}</span>
            </div>
            <div class="text-[11px] text-[#9497a9] leading-relaxed">${d.reason || '状态正常'}</div>
            ${d.tripped && d.suggested_action ? `<div class="text-[10px] text-[#f59e0b] mt-1"><i class="fas fa-lightbulb mr-1"></i>${d.suggested_action}</div>` : ''}
            ${d.until ? `<div class="text-[10px] text-[#686b82] mt-1">锁定至: ${new Date(d.until).toLocaleTimeString()}</div>` : ''}
        `;
        cardsEl.appendChild(card);
    }
}

function _startProtectionsTimer() {
    _stopProtectionsTimer();
    _protectionsTimer = setInterval(() => {
        if (_ftTab === 'protections') loadProtections();
    }, 5 * 60 * 1000);
}

function _stopProtectionsTimer() {
    if (_protectionsTimer) { clearInterval(_protectionsTimer); _protectionsTimer = null; }
}

// ── Tab 3: Param Optimizer ────────────────────────────────
export async function runOptimization() {
    const btn = document.getElementById('ftOptBtn');
    const loading = document.getElementById('ftOptLoading');
    const errorEl = document.getElementById('ftOptError');
    const content = document.getElementById('ftOptContent');
    const mode = document.getElementById('ftOptMode')?.value || 'bayesian';
    const objective = document.getElementById('ftOptObjective')?.value || 'sortino_loss';
    const currency = document.getElementById('ftOptCurrency')?.value || 'BTC';
    const optionType = document.getElementById('ftOptType')?.value || 'PUT';

    try {
        btn && (btn.disabled = true);
        loading?.classList.remove('hidden');
        errorEl?.classList.add('hidden');
        const resp = await safeFetch(API_BASE + '/api/strategy/optimize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ currency, option_type: optionType, mode, objective, n_calls: 50 })
        });
        const data = await resp.json();
        if (!data || !data.success) throw new Error(data?.detail || '优化失败');
        // 先显示容器再渲染（表格需要可见容器）
        loading?.classList.add('hidden');
        content?.classList.remove('hidden');
        _renderOptimizerResults(data);
    } catch (e) {
        loading?.classList.add('hidden');
        if (errorEl) { errorEl.classList.remove('hidden'); errorEl.textContent = '⚠ ' + safeHTML(e.message); }
    } finally {
        btn && (btn.disabled = false);
    }
}

function _renderOptimizerResults(data) {
    // Best params card
    const bp = data.best_params || {};
    const bestEl = document.getElementById('ftOptBestParams');
    if (bestEl) {
        bestEl.innerHTML = `
            <div class="flex items-center gap-2 mb-2">
                <i class="fas fa-trophy text-[#f59e0b]"></i>
                <span class="text-xs font-medium text-[#e2e4f0]">最优参数</span>
                <span class="text-[10px] text-[#686b82] ml-auto">${data.method || ''} · ${data.note || ''}</span>
            </div>
            <div class="grid grid-cols-3 sm:grid-cols-5 gap-2 text-center">
                ${_paramBadge('max_delta', bp.max_delta)}
                ${_paramBadge('min_dte', bp.min_dte)}
                ${_paramBadge('max_dte', bp.max_dte)}
                ${_paramBadge('min_apr', bp.min_apr, '%')}
                ${_paramBadge('margin_ratio', bp.margin_ratio)}
            </div>`;
    }

    // Stats row
    const statsEl = document.getElementById('ftOptStats');
    if (statsEl) {
        statsEl.innerHTML = `
            <span>⏱ ${data.elapsed_seconds?.toFixed(1)}s</span>
            <span>🔢 ${data.total_combos_tested} 组合</span>
            <span>📊 Sharpe ${data.sharpe?.toFixed(2) || '-'}</span>
            <span>📉 Sortino ${data.sortino?.toFixed(2) || '-'}</span>
            <span>📈 Calmar ${data.calmar?.toFixed(2) || '-'}</span>
            <span>📌 Max DD ${data.max_drawdown_pct?.toFixed(1) || '-'}%</span>`;
    }

    // Results table
    const tbody = document.getElementById('ftOptTbody');
    const thead = document.querySelector('#ftOptTable thead');
    if (thead) {
        thead.innerHTML = '<tr><th class="text-left py-1.5 px-2">#</th><th class="text-right py-1.5 px-2">Δmax</th><th class="text-right py-1.5 px-2">DTE min</th><th class="text-right py-1.5 px-2">DTE max</th><th class="text-right py-1.5 px-2">APR min</th><th class="text-right py-1.5 px-2">Margin</th><th class="text-right py-1.5 px-2">Sortino</th><th class="text-right py-1.5 px-2">Sharpe</th><th class="text-right py-1.5 px-2">Avg APR</th><th class="text-right py-1.5 px-2">Win%</th></tr>';
    }
    if (tbody) {
        tbody.innerHTML = '';
        const topN = data.top_n || [];
        topN.forEach((row, i) => {
            const p = row.params || {};
            const tr = document.createElement('tr');
            tr.className = 'border-b border-[rgba(71,73,85,0.15)] hover:bg-[#1a1b23] transition-colors';
            const isBest = row.loss === data.loss_value;
            tr.innerHTML = `
                <td class="py-1.5 px-2 ${isBest ? 'text-[#f59e0b] font-medium' : 'text-[#686b82]'}">${i + 1}${isBest ? ' ★' : ''}</td>
                <td class="text-right py-1.5 px-2 font-mono text-[#e2e4f0]">${p.max_delta?.toFixed(2) || '-'}</td>
                <td class="text-right py-1.5 px-2 font-mono text-[#e2e4f0]">${p.min_dte ?? '-'}</td>
                <td class="text-right py-1.5 px-2 font-mono text-[#e2e4f0]">${p.max_dte ?? '-'}</td>
                <td class="text-right py-1.5 px-2 font-mono text-[#e2e4f0]">${p.min_apr?.toFixed(1) || '-'}%</td>
                <td class="text-right py-1.5 px-2 font-mono text-[#e2e4f0]">${p.margin_ratio?.toFixed(2) || '-'}</td>
                <td class="text-right py-1.5 px-2 font-mono ${(row.sortino || 0) > 2 ? 'text-[#149e61]' : 'text-[#e2e4f0]'}">${row.sortino?.toFixed(2) || '-'}</td>
                <td class="text-right py-1.5 px-2 font-mono text-[#e2e4f0]">${row.sharpe?.toFixed(2) || '-'}</td>
                <td class="text-right py-1.5 px-2 font-mono ${(row.avg_apr || 0) > 50 ? 'text-[#149e61]' : 'text-[#e2e4f0]'}">${row.avg_apr?.toFixed(0) || '-'}%</td>
                <td class="text-right py-1.5 px-2 font-mono text-[#e2e4f0]">${row.avg_win_rate?.toFixed(0) || '-'}%</td>`;
            tbody.appendChild(tr);
        });
    }
}

function _paramBadge(name, value, suffix = '') {
    const displayVal = typeof value === 'number' ? (Number.isInteger(value) ? value : value.toFixed(2)) : (value ?? '-');
    return `<div class="bg-[#12131a] rounded px-2 py-1"><span class="text-[10px] text-[#686b82] block">${name}</span><span class="text-sm font-mono text-[#e2e4f0]">${displayVal}${suffix}</span></div>`;
}

// ── Tab 4: Backtest Engine ─────────────────────────────────
export async function runBacktest() {
    const btn = document.getElementById('ftBtBtn');
    const loading = document.getElementById('ftBtLoading');
    const errorEl = document.getElementById('ftBtError');
    const content = document.getElementById('ftBtContent');
    const currency = document.getElementById('ftBtCurrency')?.value || 'BTC';
    const days = parseInt(document.getElementById('ftBtDays')?.value || '365');
    const exchange = document.getElementById('ftBtExchange')?.value || 'binance';
    const capital = parseFloat(document.getElementById('ftBtCapital')?.value || '100000');

    try {
        btn && (btn.disabled = true);
        loading?.classList.remove('hidden');
        errorEl?.classList.add('hidden');
        const resp = await safeFetch(API_BASE + '/api/strategy/backtest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ currency, days, exchange, initial_capital: capital })
        });
        const data = await resp.json();
        if (!data || !data.success) throw new Error(data?.detail || '回测失败');
        // 先显示容器再渲染图表（Chart.js 需要可见容器才能正确测量尺寸）
        loading?.classList.add('hidden');
        content?.classList.remove('hidden');
        _renderBacktestResults(data);
    } catch (e) {
        loading?.classList.add('hidden');
        if (errorEl) { errorEl.classList.remove('hidden'); errorEl.textContent = '⚠ ' + safeHTML(e.message); }
    } finally {
        btn && (btn.disabled = false);
    }
}

function _renderBacktestResults(data) {
    // Equity curve chart
    const canvas = document.getElementById('equityCurveCanvas');
    if (canvas) {
        if (_equityCurveChart) _equityCurveChart.destroy();
        const curve = data.equity_curve || [];
        const ctx = canvas.getContext('2d');
        _equityCurveChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: curve.map(p => p.date?.slice(5) || ''),
                datasets: [{
                    label: 'Equity',
                    data: curve.map(p => p.equity),
                    borderColor: '#7132f5',
                    backgroundColor: 'rgba(113,50,245,0.08)',
                    borderWidth: 2,
                    fill: true,
                    pointRadius: 0,
                    tension: 0.3,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { intersect: false, mode: 'index' },
                plugins: {
                    legend: { labels: { color: '#9ca3af', font: { size: 11 } } },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => 'Equity: $' + ctx.parsed.y.toLocaleString()
                        }
                    }
                },
                scales: {
                    x: { ticks: { color: '#686b82', maxTicksLimit: 12, font: { size: 10 } }, grid: { color: 'rgba(75,85,99,0.15)' } },
                    y: { ticks: { color: '#686b82', font: { size: 10 }, callback: (v) => '$' + (v / 1000).toFixed(0) + 'K' }, grid: { color: 'rgba(75,85,99,0.15)' } }
                }
            }
        });
    }

    // Stats cards
    const statsEl = document.getElementById('ftBtStats');
    if (statsEl) {
        const items = [
            ['总交易', data.total_trades || 0],
            ['胜率', (data.win_rate || 0).toFixed(1) + '%'],
            ['总PnL', '$' + (data.total_pnl_usd || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })],
            ['总回报', (data.total_return_pct || 0).toFixed(2) + '%'],
            ['最大回撤', (data.max_drawdown_pct || 0).toFixed(2) + '%'],
            ['Sharpe', (data.sharpe_ratio || 0).toFixed(2)],
            ['Sortino', (data.sortino_ratio || 0).toFixed(2)],
            ['盈亏比', (data.profit_factor || 0).toFixed(2)],
        ];
        statsEl.innerHTML = items.map(([label, val]) =>
            `<div class="bg-[#1a1b23] rounded-lg p-2"><div class="text-xs font-bold text-[#e2e4f0]">${val}</div><div class="text-[10px] text-[#686b82]">${label}</div></div>`
        ).join('');
    }

    // Trade list
    const tbody = document.getElementById('ftBtTbody');
    if (tbody) {
        tbody.innerHTML = '';
        const trades = data.trade_summary || [];
        if (trades.length === 0) {
            const tr = document.createElement('tr');
            tr.innerHTML = '<td colspan="6" class="text-center text-[#686b82] py-4">无交易记录</td>';
            tbody.appendChild(tr);
        } else {
            trades.forEach(t => {
                const tr = document.createElement('tr');
                tr.className = 'border-b border-[rgba(71,73,85,0.15)] hover:bg-[#1a1b23] transition-colors';
                const pnlColor = t.pnl > 0 ? 'text-[#149e61]' : t.pnl < 0 ? 'text-[#ef4444]' : 'text-[#9497a9]';
                tr.innerHTML = `
                    <td class="py-1 px-2 text-[#9497a9]">${t.date?.slice(0, 10) || '-'}</td>
                    <td class="text-right py-1 px-2 font-mono text-[#e2e4f0]">${t.strike ? '$' + t.strike.toLocaleString() : '-'}</td>
                    <td class="text-right py-1 px-2 font-mono text-[#9497a9]">${t.premium_usd ? '$' + t.premium_usd.toFixed(0) : '-'}</td>
                    <td class="text-right py-1 px-2 font-mono font-medium ${pnlColor}">${t.pnl != null ? (t.pnl >= 0 ? '+$' : '-$') + Math.abs(t.pnl).toFixed(0) : '-'}</td>
                    <td class="text-right py-1 px-2 text-[#686b82]">${t.dte ?? '-'}</td>
                    <td class="py-1 px-2 text-[#686b82] text-[11px]">${t.exit_reason || t.assigned ? '行权' : '-'}</td>`;
                tbody.appendChild(tr);
            });
        }
    }
}

// ── Init ──────────────────────────────────────────────────
export function initFreqtrade() {
    if (_ftInitialized) return;
    _ftInitialized = true;
    // Render guards once in the background so card structure is visible
    const emptyState = { summary: { all_clear: false, tripped_count: 0, critical_count: 0, guards_tripped: [] }, details: {} };
    _renderProtections(emptyState);

    // Auto-load default tab (portfolio risk)
    loadPortfolioRisk();

    window.addEventListener('beforeunload', _stopProtectionsTimer);
}

// Mount to window for HTML onclick
window.setFreqtradeTab = setFreqtradeTab;
window.loadPortfolioRisk = loadPortfolioRisk;
window.loadProtections = loadProtections;
window.runOptimization = runOptimization;
window.runBacktest = runBacktest;
window.initFreqtrade = initFreqtrade;
