/**
 * 通用工具模块
 * v1.0: 从 app.js 提取的共享工具函数
 */

export function $(id) { return document.getElementById(id); }

export function safeHTML(str) {
    if (str == null) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

export const STRATEGY_PRESETS = {
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

export const API_BASE = '';
export const API_TIMEOUT_MS = 30000;
export const FETCH_MAX_RETRIES = 1;
export const TABLE_PAGE_SIZE = 30;

export function getApiKey() {
    // Security note: localStorage is accessible to any JS on the page.
    // Mitigated by CSP headers in index.html restricting script sources.
    // For higher security deployments, consider httpOnly cookie auth.
    try {
        return localStorage.getItem('dashboard_api_key') || '';
    } catch (_) {
        return '';
    }
}

export async function safeFetch(url, options = {}, retries = FETCH_MAX_RETRIES) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), API_TIMEOUT_MS);
    try {
        const apiKey = getApiKey();
        const headers = apiKey ? {'X-API-Key': apiKey} : {};
        const opts = {
            ...options,
            signal: controller.signal,
            headers: {
                ...headers,
                ...(options.headers || {})
            }
        };
        const res = await fetch(url, opts);
        clearTimeout(timer);
        if (!res.ok) {
            let detail = '';
            try {
                const errData = await res.clone().json();
                detail = errData.detail || errData.message || errData.error || '';
            } catch (_) {
                try { detail = await res.clone().text(); } catch (_) {}
            }
            throw new Error(`HTTP ${res.status}: ${res.statusText}${detail ? ' - ' + detail : ''}`);
        }
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

export function getFieldName(field) {
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

export function getRecommendationColor(rec) {
    const colors = {
        'BEST': 'text-green-400 bg-green-900/30',
        'GOOD': 'text-blue-400 bg-blue-900/30',
        'OK': 'text-gray-400 bg-gray-800/30',
        'CAUTION': 'text-orange-400 bg-orange-900/30',
        'SKIP': 'text-red-400 bg-red-900/30',
    };
    return colors[rec] || colors['SKIP'];
}

export function getRecommendationLabel(rec) {
    const labels = {
        'BEST': '强烈推荐',
        'GOOD': '推荐',
        'OK': '可考虑',
        'CAUTION': '谨慎',
        'SKIP': '不推荐',
    };
    return labels[rec] || rec;
}
