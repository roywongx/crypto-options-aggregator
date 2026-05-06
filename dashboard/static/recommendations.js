/**
 * recommendations.js — 统一投资推荐系统前端
 *
 * 三层递进展示:
 *   1. 信号灯（自动）— 面板卡片左侧色条 + 标题区圆点
 *   2. 规则报告（点击展开）— 右侧滑入面板 (420px)
 *   3. LLM 抽屉（用户触发）— 右侧全屏抽屉 + 遮罩层 SSE 流式
 */
import { $, safeHTML, safeFetch, API_BASE, getApiKey } from './utils.js';

// ============================================================
// 信号灯映射
// ============================================================

const SIGNAL_CONFIG = {
    bullish:  { emoji: '🟢', label: '看多', cls: 'bg-[#149e61]/15 text-[#149e61] border-[#149e61]/30' },
    bearish:  { emoji: '🔴', label: '看空', cls: 'bg-[#ef4444]/15 text-[#ef4444] border-[#ef4444]/30' },
    neutral:  { emoji: '🟡', label: '中性', cls: 'bg-[#f59e0b]/15 text-[#f59e0b] border-[#f59e0b]/30' },
    caution:  { emoji: '⚠️', label: '谨慎', cls: 'bg-[#f59e0b]/20 text-[#f59e0b] border-[#f59e0b]/50' },
    disabled: { emoji: '⚪', label: 'N/A',  cls: 'bg-[#686b82]/10 text-[#686b82] border-[#686b82]/20' },
};

// ============================================================
// 面板->CSS选择器映射（信号灯注入目标）
// ============================================================

const PANEL_TARGETS = {
    metric_cards:        '#metricCardsSection',
    risk_command_center: '#riskDashboard',
    strategy_center:     '#strategyCenterSection',
    greeks_matrix:       '#greeksSection',
    ai_analyst_center:   '#llmAnalystSection',
    iv_term_structure:   '#ivTermStructureSection',
    iv_smile:            '#ivSmileSection',
    dvol_trend:          '#dvolSection',
    pcr_chart:           '#pcrSection',
    max_pain:            '#maxPainSection',
    large_trades:        '#largeTradesSection',
    martingale_sandbox:  '#sandboxSection',
    opportunities_table: '#opportunitiesSection',
    gex_chart:           '#gexChartContainer',
    money_flow:          '#moneyFlowSection',
    onchain_metrics:     '#onchainGrid',
    derivative_metrics:  '#derivativeSection',
};

// ============================================================
// API 辅助
// ============================================================

async function fetchRecommendation(panelId, currency = 'BTC') {
    const key = getApiKey();
    const headers = key ? { 'x-api-key': key } : {};
    const res = await safeFetch(`${API_BASE}/api/recommendation/${panelId}?currency=${currency}`, { headers });
    return res.json();
}

async function fetchSummary(currency = 'BTC') {
    const key = getApiKey();
    const headers = key ? { 'x-api-key': key } : {};
    const res = await safeFetch(`${API_BASE}/api/recommendations/summary?currency=${currency}`, { headers, timeout: 60000 });
    return res.json();
}

// ============================================================
// Component 1: 信号灯渲染 — 左侧色条 + 标题区圆点
// ============================================================

/**
 * 在面板容器上注入金融精致风格的信号指示器
 * 左侧色条 (3px border-left) + 标题区小圆点
 * @param {HTMLElement|string} container - 面板容器或选择器
 * @param {Object} signal - {signal, signal_emoji, signal_text, confidence}
 */
export function renderSignalBadge(container, signal) {
    if (typeof container === 'string') container = document.querySelector(container);
    if (!container) return;

    const cfg = SIGNAL_CONFIG[signal?.signal] || SIGNAL_CONFIG.disabled;
    const conf = signal?.confidence ?? 0;

    // 左侧色条 — 金融精致风格
    const borderColorMap = { bullish: '#149e61', bearish: '#ef4444', neutral: '#f59e0b', caution: '#f59e0b', disabled: 'transparent' };
    const borderColor = borderColorMap[signal?.signal] || borderColorMap.disabled;
    container.style.borderLeft = `4px solid ${borderColor}`;
    container.style.transition = 'border-left-color 0.3s ease';

    // 移除旧的 dot（如果定位变了需要重建）
    const oldDot = container.querySelector('.rec-signal-dot');
    if (oldDot && oldDot.style.position === 'absolute') {
        oldDot.remove();
    }

    // 信号胶囊 — 嵌入面板标题区
    let dot = container.querySelector('.rec-signal-dot');
    if (!dot) {
        dot = document.createElement('button');
        dot.className = 'rec-signal-dot';
        dot.style.cssText = 'display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:6px;border:1px solid transparent;cursor:pointer;background:transparent;transition:background 0.2s ease, border-color 0.2s ease;white-space:nowrap;flex-shrink:0;';
        dot.onmouseenter = () => { dot.style.background = 'rgba(255,255,255,0.04)'; dot.style.borderColor = 'rgba(255,255,255,0.08)'; };
        dot.onmouseleave = () => { dot.style.background = 'transparent'; dot.style.borderColor = 'transparent'; };

        // 策略：找标题元素，优先 header bar 模式，其次直接 h2/h3 标题
        const headerBar = container.querySelector(':scope > div.flex.items-center, :scope > .flex.items-center.justify-between, :scope > div:first-child.flex');
        const directTitle = container.querySelector(':scope > h2:first-child.flex, :scope > h3:first-child.flex');

        if (headerBar) {
            // 模式 A: 面板有 header bar（flex justify-between）→ 嵌入标题区
            const titleArea = headerBar.querySelector(':scope > div:first-child, :scope > h2:first-child, :scope > h3:first-child, :scope > .flex.items-center.gap-2, :scope > .flex.items-center.gap-3');
            if (titleArea) {
                titleArea.appendChild(dot);
            } else {
                const firstChild = headerBar.children[0];
                if (firstChild && firstChild.nextSibling) {
                    headerBar.insertBefore(dot, firstChild.nextSibling);
                } else {
                    headerBar.appendChild(dot);
                }
            }
        } else if (directTitle) {
            // 模式 B: 面板以 h2/h3 标题开头（无 header bar）→ 加到标题末尾
            directTitle.appendChild(dot);
        } else {
            // 模式 C: 无标题结构（如 metricCardsSection, gexChartContainer）→ 绝对定位
            container.style.position = 'relative';
            dot.style.cssText += 'position:absolute;top:10px;left:12px;z-index:5;background:rgba(26,27,35,0.92);backdrop-filter:blur(4px);border-radius:20px;padding:2px 10px 2px 6px;';
            container.appendChild(dot);
        }
    }

    const dotColor = borderColorMap[signal?.signal] || borderColorMap.disabled;
    dot.innerHTML = `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${dotColor};box-shadow:0 0 6px ${dotColor}40;flex-shrink:0;"></span>
        <span style="font-size:11px;font-weight:600;color:${dotColor};">${cfg.emoji} ${cfg.label}</span>
        <span class="tabular-nums" style="font-size:10px;color:#686b82;">${conf}</span>`;
    dot.title = `${signal?.signal_text || cfg.label} (置信度: ${conf}) — 点击查看规则分析`;

    dot.onclick = (e) => {
        e.stopPropagation();
        const panelId = container.dataset?.recPanelId;
        if (panelId) openRuleSlide(container, panelId);
    };
}

// ============================================================
// Component 2: 规则报告（右侧滑入面板 + 遮罩层）
// ============================================================

let _activeRuleSlide = null;

function closeRuleSlide() {
    if (_activeRuleSlide) {
        _activeRuleSlide.backdrop?.remove();
        _activeRuleSlide.panel?.remove();
        _activeRuleSlide = null;
    }
}

/**
 * 打开右侧滑入规则分析面板 (420px 宽)
 */
export async function openRuleSlide(container, panelId, currency = 'BTC') {
    closeRuleSlide();

    // 遮罩层
    const backdrop = document.createElement('div');
    backdrop.className = 'rec-rule-backdrop';
    backdrop.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.4);z-index:101;';
    backdrop.onclick = closeRuleSlide;
    document.body.appendChild(backdrop);

    // 滑入面板
    const panel = document.createElement('div');
    panel.className = 'rec-rule-slide';
    panel.style.cssText = 'position:fixed;right:0;top:0;height:100%;width:420px;max-width:95vw;background:#1a1b23;border-left:1px solid rgba(71,73,85,0.3);z-index:102;overflow-y:auto;transform:translateX(100%);transition:transform 0.25s ease;display:flex;flex-direction:column;';
    panel.innerHTML = `
        <div class="flex items-center justify-between px-5 py-4 border-b border-[rgba(71,73,85,0.2)] sticky top-0 bg-[#1a1b23] z-10">
            <div class="flex items-center gap-2">
                <i class="fas fa-clipboard-list text-[#8b5cf6]"></i>
                <span class="font-semibold text-sm">规则分析报告</span>
                <span class="text-xs text-[#686b82]">${panelId}</span>
            </div>
            <button class="rec-rule-close text-[#686b82] hover:text-[#e4e4e7] transition-colors text-lg">
                <i class="fas fa-times"></i>
            </button>
        </div>
        <div class="flex-1 overflow-y-auto p-5">
            <div class="text-sm text-[#9497a9]"><i class="fas fa-spinner fa-pulse mr-2"></i>加载分析报告...</div>
        </div>
    `;
    document.body.appendChild(panel);

    _activeRuleSlide = { backdrop, panel };

    // 关闭
    panel.querySelector('.rec-rule-close').onclick = closeRuleSlide;

    // 动画滑入
    requestAnimationFrame(() => { panel.style.transform = 'translateX(0)'; });

    // 加载数据
    const body = panel.querySelector('.flex-1.overflow-y-auto');

    try {
        const result = await fetchRecommendation(panelId, currency);
        if (!result?.report) {
            body.innerHTML = `<div class="text-sm text-[#f59e0b]"><i class="fas fa-exclamation-triangle mr-2"></i>该面板暂无分析数据</div>`;
            return;
        }
        const signal = result.signal || {};
        const report = result.report;
        const meta = result.meta || {};

        const factorsHtml = (report.factors || []).map(f => {
            const scoreColor = f.score >= 70 ? '#149e61' : f.score >= 40 ? '#f59e0b' : '#ef4444';
            return `<div class="flex items-center justify-between py-2 px-3 rounded-lg bg-[#22232e]">
                <span class="text-sm">${safeHTML(f.name)}</span>
                <div class="flex items-center gap-2">
                    <span class="text-xs text-[#686b82] max-w-[180px] truncate" title="${safeHTML(f.verdict)}">${safeHTML(f.verdict)}</span>
                    <span class="text-sm font-mono font-semibold tabular-nums" style="color:${scoreColor}">${f.score}</span>
                </div>
            </div>`;
        }).join('');

        const logicHtml = (report.logic_chain || []).map(l => `<li class="text-xs text-[#9497a9]">${safeHTML(l)}</li>`).join('');

        const flagsHtml = (report.risk_flags || []).length
            ? (report.risk_flags || []).map(f => `<span class="px-2 py-0.5 rounded-full text-xs bg-[#ef4444]/10 text-[#ef4444] border border-[#ef4444]/20">${safeHTML(f)}</span>`).join('')
            : '<span class="text-xs text-[#686b82]">无显著风险</span>';

        body.innerHTML = `
            <div class="flex items-center justify-between mb-4">
                <div class="flex items-center gap-2">
                    <span class="text-lg">${signal.signal_emoji}</span>
                    <span class="font-semibold text-sm">${safeHTML(signal.signal_text)}</span>
                    <span class="text-xs text-[#686b82]">${signal.confidence}</span>
                </div>
                <span class="text-[10px] text-[#686b82]">${meta?.computation_ms ?? 0}ms</span>
            </div>
            <div class="text-xs text-[#9497a9] mb-4 leading-relaxed">${safeHTML(report.summary)}</div>
            <div class="space-y-1 mb-4">${factorsHtml}</div>
            ${logicHtml ? `<div class="mb-4"><div class="text-xs font-semibold text-[#9497a9] mb-1.5">推理链</div><ul class="space-y-0.5 pl-4">${logicHtml}</ul></div>` : ''}
            <div class="flex items-center gap-2 mb-4 p-3 rounded-lg bg-[#7132f5]/5 border border-[#7132f5]/10">
                <span class="text-xs text-[#686b82]">建议操作:</span>
                <span class="text-sm font-semibold text-[#e4e4e7]">${safeHTML(report.suggested_action)}</span>
            </div>
            <div class="flex flex-wrap items-center gap-1.5 mb-4">${flagsHtml}</div>
            <button class="rec-llm-btn w-full py-2.5 rounded-lg bg-[#7132f5]/10 border border-[#7132f5]/30 text-[#8b5cf6] text-sm font-semibold hover:bg-[#7132f5]/20 transition-colors">
                <i class="fas fa-brain mr-2"></i>LLM 深度分析
            </button>
        `;

        panel.querySelector('.rec-llm-btn').onclick = () => {
            openLLMDrawer(panelId, currency);
        };

    } catch (err) {
        body.innerHTML = `<div class="text-sm text-[#ef4444]"><i class="fas fa-exclamation-circle mr-2"></i>${safeHTML(err.message)}</div>`;
    }
}

// 向后兼容旧接口
export function renderRuleReport(container, panelId, currency = 'BTC') {
    openRuleSlide(container, panelId, currency);
}

// ============================================================
// Component 3: LLM 抽屉（SSE 流式 + 遮罩层）
// ============================================================

let _activeDrawer = null;

export function openLLMDrawer(panelId, currency = 'BTC') {
    closeLLMDrawer();
    closeRuleSlide(); // 关闭规则幻灯片

    // 遮罩层
    const backdrop = document.createElement('div');
    backdrop.className = 'rec-llm-backdrop';
    backdrop.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:150;';
    backdrop.onclick = closeLLMDrawer;
    document.body.appendChild(backdrop);

    const drawer = document.createElement('div');
    drawer.className = 'rec-llm-drawer fixed right-0 top-0 h-full w-[520px] max-w-[95vw] bg-[#1a1b23] border-l border-[rgba(71,73,85,0.3)] z-[151] flex flex-col shadow-2xl';
    drawer.style.transform = 'translateX(100%)';
    drawer.style.transition = 'transform 0.25s ease';
    drawer.innerHTML = `
        <div class="flex items-center justify-between px-5 py-4 border-b border-[rgba(71,73,85,0.2)]">
            <div class="flex items-center gap-2">
                <i class="fas fa-brain text-[#8b5cf6]"></i>
                <span class="font-semibold text-sm">LLM 深度分析</span>
                <span class="text-xs text-[#686b82]">${panelId}</span>
            </div>
            <button class="rec-drawer-close text-[#686b82] hover:text-[#e4e4e7] transition-colors text-lg">
                <i class="fas fa-times"></i>
            </button>
        </div>
        <div class="flex border-b border-[rgba(71,73,85,0.2)]">
            ${['synthesis','bull','bear','judge'].map((tab, i) => `
                <button class="rec-tab-btn flex-1 py-2.5 text-xs font-semibold text-[#686b82] hover:text-[#e4e4e7] transition-colors ${i===0?'border-b-2 border-[#7132f5] text-[#e4e4e7]':''}" data-tab="${tab}">
                    ${tab === 'synthesis' ? '📋 合成' : tab === 'bull' ? '🟢 多头' : tab === 'bear' ? '🔴 空头' : '⚖️ 判決'}
                </button>
            `).join('')}
        </div>
        <div class="flex-1 overflow-y-auto custom-scrollbar p-5">
            <div class="rec-tab-content" data-tab="synthesis"><div class="text-sm text-[#9497a9]">等待分析...</div></div>
            <div class="rec-tab-content hidden" data-tab="bull"><div class="text-sm text-[#9497a9]">等待辩论...</div></div>
            <div class="rec-tab-content hidden" data-tab="bear"><div class="text-sm text-[#9497a9]">等待辩论...</div></div>
            <div class="rec-tab-content hidden" data-tab="judge"><div class="text-sm text-[#9497a9]">等待判決...</div></div>
        </div>
        <div class="px-5 py-3 border-t border-[rgba(71,73,85,0.2)] text-xs text-[#686b82]">
            <span class="rec-status"><i class="fas fa-circle text-[10px] text-[#f59e0b] mr-1"></i>准备中...</span>
        </div>
    `;
    document.body.appendChild(drawer);
    _activeDrawer = { drawer, backdrop };

    // 动画滑入
    requestAnimationFrame(() => { drawer.style.transform = 'translateX(0)'; });

    // Tab 切换
    drawer.querySelectorAll('.rec-tab-btn').forEach(btn => {
        btn.onclick = () => {
            drawer.querySelectorAll('.rec-tab-btn').forEach(b => b.className = b.className.replace('border-b-2 border-[#7132f5] text-[#e4e4e7]', 'text-[#686b82]'));
            btn.className = btn.className.replace('text-[#686b82]', '').trim() + ' border-b-2 border-[#7132f5] text-[#e4e4e7]';
            const targetTab = btn.dataset.tab;
            drawer.querySelectorAll('.rec-tab-content').forEach(c => c.classList.toggle('hidden', c.dataset.tab !== targetTab));
        };
    });

    // 关闭按钮
    drawer.querySelector('.rec-drawer-close').onclick = closeLLMDrawer;

    // 启动 SSE
    startSSEAnalysis(panelId, currency, drawer);
}

export function closeLLMDrawer() {
    if (_activeDrawer) {
        _activeDrawer.drawer.remove();
        if (_activeDrawer.backdrop) _activeDrawer.backdrop.remove();
        _activeDrawer = null;
    }
}

function startSSEAnalysis(panelId, currency, drawer) {
    const key = getApiKey();

    const statusEl = drawer.querySelector('.rec-status');
    const tabContents = {};
    drawer.querySelectorAll('.rec-tab-content').forEach(c => { tabContents[c.dataset.tab] = c; });

    statusEl.innerHTML = '<i class="fas fa-circle text-[10px] text-[#f59e0b] mr-1 animate-pulse"></i>正在调用 LLM...';

    fetch(`${API_BASE}/api/recommendation/${panelId}/llm`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            ...(key ? { 'x-api-key': key } : {}),
            'Accept': 'text/event-stream',
        },
        body: JSON.stringify({ currency, force_refresh: false }),
    }).then(async response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        handleSSEEvent(data, tabContents, statusEl);
                    } catch (e) { /* skip parse errors */ }
                }
            }
        }
    }).catch(err => {
        statusEl.innerHTML = `<i class="fas fa-circle text-[10px] text-[#ef4444] mr-1"></i>${safeHTML(err.message)}`;
        Object.values(tabContents).forEach(c => {
            if (c.querySelector('.text-[#9497a9]'))
                c.innerHTML = `<div class="text-sm text-[#ef4444]">分析请求失败: ${safeHTML(err.message)}</div>`;
        });
    });
}

function handleSSEEvent(data, tabContents, statusEl) {
    const tabMap = { 'synthesis': 'synthesis', 'bull_context': 'bull', 'bear_context': 'bear', 'judge_criteria': 'judge' };
    const labelMap = { 'synthesis': '合成分析', 'bull_context': '多头辩论', 'bear_context': '空头辩论', 'judge_criteria': '最终判決' };

    switch (data.type) {
        case 'start':
            statusEl.innerHTML = '<i class="fas fa-circle text-[10px] text-[#f59e0b] mr-1 animate-pulse"></i>分析中...';
            break;
        case 'step': {
            const tab = tabMap[data.label] || 'synthesis';
            const label = labelMap[data.label] || data.label;
            const content = safeHTML(data.content || '')
                .replace(/\n/g, '<br>')
                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
            tabContents[tab].innerHTML = `
                <div class="mb-3">
                    <div class="text-xs font-semibold text-[#8b5cf6] mb-2">${label}</div>
                    <div class="text-sm text-[#e4e4e7] leading-relaxed whitespace-pre-wrap">${content}</div>
                </div>`;
            break;
        }
        case 'done':
            statusEl.innerHTML = '<i class="fas fa-check-circle text-[10px] text-[#149e61] mr-1"></i>分析完成';
            break;
        default:
            statusEl.innerHTML = `<i class="fas fa-circle text-[10px] text-[#8b5cf6] mr-1"></i>${safeHTML(data.type || '')}`;
    }
}

// ============================================================
// Component 4: 顶部汇总条 — 嵌入 header 右侧
// ============================================================

export async function renderSummaryBar(currency = 'BTC') {
    let bar = document.querySelector('.rec-summary-bar');
    if (!bar) {
        bar = document.createElement('div');
        bar.className = 'rec-summary-bar flex items-center gap-1.5 overflow-x-auto custom-scrollbar mt-2 pt-2 border-t border-[rgba(71,73,85,0.15)]';
        const header = document.querySelector('header');
        if (header) {
            header.appendChild(bar);
        }
    }

    bar.innerHTML = '<span class="text-xs text-[#686b82] mr-2"><i class="fas fa-spinner fa-pulse"></i></span>';

    try {
        const summary = await fetchSummary(currency);
        if (!summary?.summary) { bar.innerHTML = '<span class="text-xs text-[#ef4444]">汇总无数据</span>'; return; }

        bar.innerHTML = '';
        for (const [panelId, info] of Object.entries(summary.summary)) {
            const cfg = SIGNAL_CONFIG[info.signal] || SIGNAL_CONFIG.disabled;
            const chip = document.createElement('span');
            chip.className = `inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-semibold cursor-pointer transition-opacity hover:opacity-80 ${cfg.cls}`;
            chip.title = `${info.name}: ${info.signal_text} (${info.confidence})`;
            chip.innerHTML = `${cfg.emoji}<span class="hidden sm:inline">${info.name}</span>`;
            chip.onclick = () => {
                const target = document.querySelector(PANEL_TARGETS[panelId]);
                if (target) {
                    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    target.dataset.recPanelId = panelId;
                    renderRuleReport(target, panelId, currency);
                }
            };
            bar.appendChild(chip);
        }
    } catch (err) {
        bar.innerHTML = `<span class="text-xs text-[#ef4444]">汇总加载失败: ${safeHTML(err.message)}</span>`;
    }
}

// ============================================================
// 为所有面板注入信号灯
// ============================================================

export async function injectAllSignals(currency = 'BTC') {
    const promises = Object.entries(PANEL_TARGETS).map(async ([panelId, selector]) => {
        const container = document.querySelector(selector);
        if (!container) return;
        container.dataset.recPanelId = panelId;
        try {
            const result = await fetchRecommendation(panelId, currency);
            renderSignalBadge(container, result.signal);
        } catch (err) {
            renderSignalBadge(container, { signal: 'disabled', signal_text: '不可用', confidence: 0 });
        }
    });
    await Promise.allSettled(promises);
}

// 挂载到 window 供外部调用
window.Rec = {
    renderSignalBadge, renderRuleReport, renderSummaryBar, injectAllSignals,
    openLLMDrawer, closeLLMDrawer, fetchRecommendation, fetchSummary,
};
