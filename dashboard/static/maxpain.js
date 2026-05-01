/**
 * Max Pain & GEX 模块
 * v1.0: 从 app.js 提取的最大痛点和 GEX 图表
 */

let mpChart = null;

export function getMpChart() { return mpChart; }
export function setMpChart(chart) { mpChart = chart; }

export async function loadMaxPain(deps, retryCount = 0) {
    const { safeFetch, safeHTML, API_BASE } = deps;
    const maxRetries = 2;
    const spotEl = document.getElementById('mpSpot');
    if (!spotEl) { console.warn('MP: container not found'); return; }
    try {
        const currency = document.getElementById('currencySelect')?.value || 'BTC';
        const resp = await safeFetch(API_BASE + '/api/metrics/max-pain?currency=' + currency);
        const d = await resp.json();
        if (d.error || !d.expiries) {
            if (retryCount < maxRetries) {
                setTimeout(() => loadMaxPain(deps, retryCount + 1), 2000);
                return;
            }
            showMaxPainError(d.error || '无数据', safeHTML);
            return;
        }

        const exp = d.expiries[0];

        document.getElementById('mpSpot').textContent = '$' + (d.spot || 0).toLocaleString();
        document.getElementById('mpFlip').textContent = exp.gamma_status && exp.gamma_status.flip_strike ? '$' + exp.gamma_status.flip_strike.toLocaleString() : '--';
        document.getElementById('mpPrice').textContent = '$' + (exp.max_pain || 0).toLocaleString();
        document.getElementById('mpDist').textContent = (exp.dist_pct || 0).toFixed(1) + '%';
        document.getElementById('mpPCR').textContent = (exp.pcr || 0).toFixed(2);
        document.getElementById('mpSignal').textContent = exp.signal || '';

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

            const regionDistEl = document.getElementById('mpRegionDist');
            if (regionDistEl && exp.gamma_status.distance_pct !== undefined) {
                regionDistEl.textContent = (exp.gamma_status.distance_pct > 0 ? '+' : '') + exp.gamma_status.distance_pct.toFixed(1) + '%';
                regionDistEl.className = 'font-mono text-xs ' +
                    (exp.gamma_status.distance_pct > 5 ? 'text-emerald-400' :
                     exp.gamma_status.distance_pct < -5 ? 'text-red-400' : 'text-gray-400');
            }
        }

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
            setTimeout(() => loadMaxPain(deps, retryCount + 1), 2000);
        } else {
            showMaxPainError(e.message, safeHTML);
        }
    }
}

export function showMaxPainError(message, safeHTML) {
    const container = document.getElementById('painGexChart');
    if (container && container.parentElement) {
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
