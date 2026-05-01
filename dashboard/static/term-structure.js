/**
 * IV Term Structure 模块
 * v1.0: 从 app.js 提取的 IV 期限结构图表和分析
 */

let tsChart = null;

export function getTsChart() { return tsChart; }
export function setTsChart(chart) { tsChart = chart; }

export async function loadTermStructure(deps, retryCount = 0) {
    const { safeFetch, safeHTML, API_BASE, showAlert } = deps;
    const maxRetries = 2;
    const statusEl = document.getElementById('ts7');
    if (!statusEl) { console.warn('TS: container not found'); return; }
    try {
        const currency = document.getElementById('currencySelect')?.value || 'BTC';
        const resp = await safeFetch(API_BASE + '/api/charts/vol-surface?currency=' + currency);
        const d = await resp.json();
        if (d.error) {
            if (retryCount < maxRetries) {
                setTimeout(() => loadTermStructure(deps, retryCount + 1), 2000);
                return;
            }
            showTermStructureError(d.error, safeHTML);
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

        const analysis = d.analysis;
        if (!analysis || analysis.error) {
            console.warn('IV分析不可用:', analysis?.error || '无数据');
        } else {
            const structLabel2 = document.getElementById('tsStructureLabel');
            if (structLabel2 && analysis.structure_type) {
                const st = analysis.structure_type;
                structLabel2.textContent = st.icon + ' ' + st.name;
                structLabel2.className = 'text-xs px-2 py-0.5 rounded-full font-medium ' + (st.color.includes('text-') ? 'bg-' + st.color.replace('text-','').replace('-400','-500/20').replace('-300','-500/20') + ' ' + st.color : 'bg-gray-700 text-gray-400');
            }
            const slopeLabel2 = document.getElementById('tsSlopeLabel');
            if (slopeLabel2 && analysis.slope) {
                const s = analysis.slope;
                slopeLabel2.textContent = (s.percent > 0 ? '+' : '') + s.percent + '%';
                slopeLabel2.className = 'text-xs px-2 py-0.5 rounded-full ' + (s.percent >= 0 ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400');
            }
            const msEl = document.getElementById('ivMarketState');
            const msAdvice = document.getElementById('ivMarketAdvice');
            if (msEl && analysis.market_state) {
                const ms = analysis.market_state;
                msEl.textContent = ms.icon + ' ' + ms.name;
                msEl.className = 'text-lg font-bold ' + ms.color;
                if (msAdvice) msAdvice.textContent = ms.advice;
            }
            const vrpEl = document.getElementById('ivVRPValue');
            const vrpDesc = document.getElementById('ivVRPDesc');
            if (vrpEl && analysis.vrp) {
                const v = analysis.vrp;
                const vrpColor = v.signal && v.signal.includes('SELL') ? 'text-green-400' : v.signal === 'BUY_EDGE' ? 'text-blue-400' : 'text-gray-400';
                vrpEl.textContent = (v.value > 0 ? '+' : '') + v.value + '%';
                vrpEl.className = 'text-lg font-bold ' + vrpColor;
                if (vrpDesc) vrpDesc.textContent = v.description;
            }
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
            setTimeout(() => loadTermStructure(deps, retryCount + 1), 2000);
        } else {
            showTermStructureError(e.message, safeHTML);
        }
    }
}

export function showTermStructureError(message, safeHTML) {
    const el = document.getElementById('termStructureChart');
    if (el && el.parentElement) {
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
