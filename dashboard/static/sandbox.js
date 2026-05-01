/**
 * Martingale Sandbox 模块
 * v1.0: 从 app.js 提取的沙盘推演功能
 */

export async function runSandbox(deps) {
    const { safeFetch, safeHTML, API_BASE } = deps;
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

        var crashScen = d.crash_scenario || {};
        var loss = d.loss_analysis || {};
        html += '<div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">';

        html += '<div class="p-3 rounded-lg bg-gray-800 border border-gray-700/30">';
        html += '<div class="text-xs font-semibold text-gray-400 mb-2">📉 崩盘情景</div>';
        html += '<div class="space-y-1 text-xs">';
        html += '<div class="flex justify-between"><span class="text-gray-500">当前价格</span><span class="font-mono">$' + (crashScen.from_price || 0).toLocaleString() + '</span></div>';
        html += '<div class="flex justify-between"><span class="text-gray-500">崩盘目标</span><span class="font-mono text-red-400">$' + (crashScen.to_price || 0).toLocaleString() + '</span></div>';
        html += '<div class="flex justify-between"><span class="text-gray-500">跌幅</span><span class="font-mono text-red-400">' + (crashScen.drop_pct || 0) + '%</span></div>';
        html += '</div></div>';

        html += '<div class="p-3 rounded-lg bg-gray-800 border border-gray-700/30">';
        html += '<div class="text-xs font-semibold text-gray-400 mb-2">💥 损失分解</div>';
        html += '<div class="space-y-1 text-xs">';
        html += '<div class="flex justify-between"><span class="text-gray-500">本金损失</span><span class="font-mono text-red-400">$' + (loss.intrinsic_loss || 0).toLocaleString() + '</span></div>';
        html += '<div class="flex justify-between"><span class="text-gray-500">Vega 冲击</span><span class="font-mono text-orange-400">$' + (loss.vega_impact || 0).toLocaleString() + '</span></div>';
        html += '<div class="flex justify-between"><span class="text-gray-500">总损失</span><span class="font-mono text-red-300 font-bold">$' + (loss.total_loss || 0).toLocaleString() + '</span></div>';
        html += '<div class="flex justify-between"><span class="text-gray-500">损失比例</span><span class="font-mono text-red-400">' + (loss.loss_pct || 0) + '%</span></div>';
        html += '</div></div>';
        html += '</div>';

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

export function exportCSV(API_BASE) {
    const currency = document.getElementById('currencySelect')?.value || 'BTC';
    const url = `${API_BASE}/api/export/csv?currency=${currency}&hours=168`;
    const a = document.createElement('a');
    a.href = url;
    a.download = `options_${currency}_168h.csv`;
    a.click();
}
