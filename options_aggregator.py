import subprocess
import json
import argparse
import sys
import concurrent.futures
from datetime import datetime

STRATEGY_PRESETS = {
    "PUT": {
        "conservative": {"max_delta": 0.20, "min_dte": 30, "max_dte": 45, "margin_ratio": 0.18, "min_apr": 12.0, "label": "纯收租(高胜率)"},
        "standard":     {"max_delta": 0.30, "min_dte": 14, "max_dte": 35, "margin_ratio": 0.20, "min_apr": 15.0, "label": "标准平衡"},
        "aggressive":   {"max_delta": 0.40, "min_dte": 7,  "max_dte": 28, "margin_ratio": 0.22, "min_apr": 20.0, "label": "折价接货"}
    },
    "CALL": {
        "conservative": {"max_delta": 0.30, "min_dte": 30, "max_dte": 45, "margin_ratio": 0.18, "min_apr": 10.0, "label": "保留上涨空间"},
        "standard":     {"max_delta": 0.45, "min_dte": 14, "max_dte": 35, "margin_ratio": 0.20, "min_apr": 12.0, "label": "标准备兑"},
        "aggressive":   {"max_delta": 0.55, "min_dte": 7,  "max_dte": 28, "margin_ratio": 0.22, "min_apr": 18.0, "label": "强横盘预期"}
    }
}

def adapt_params_by_dvol(params: dict, dvol_raw: dict) -> dict:
    """根据DVOL状态自适应调整扫描参数"""
    pct_7d = (dvol_raw.get('iv_percentile_7d') or 50)
    trend = dvol_raw.get('trend', '')
    signal = dvol_raw.get('signal', '')
    
    adjusted = dict(params)
    advice = []
    
    if isinstance(pct_7d, (int, float)):
        if pct_7d >= 80:
            adjusted['max_delta'] = round(adjusted['max_delta'] * 0.70, 2)
            adjusted['min_apr'] = adjusted.get('min_apr', 15) + 5
            advice.append("高波动环境: 自动收紧Delta，提高APR门槛")
        elif pct_7d <= 20:
            adjusted['max_delta'] = min(round(adjusted['max_delta'] * 1.20, 2), 0.60)
            adjusted['min_apr'] = max(adjusted.get('min_apr', 15) - 5, 8)
            advice.append("低波动环境: 权利金偏薄，适当放宽参数")
    
    if trend == '上涨' and params.get('option_type') == 'PUT':
        advice.append("DVOL上升趋势+反弹阶段，适合Sell Put接货")
    elif trend == '下跌':
        if params.get('option_type') == 'CALL':
            advice.append("市场下跌后反弹，Covered Call可锁定收益")
        else:
            advice.append("市场处于下跌阶段，建议降低仓位或观望")
    
    adjusted['_dvol_advice'] = advice
    return adjusted

# Fix Windows Unicode
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def run_command(cmd):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if result.returncode != 0:
            return {"error": result.stderr}
        return json.loads(result.stdout)
    except Exception as e:
        return {"error": str(e)}

def simulate_loss(item, drop_pct, spot_price):
    """
    Stress Test: Approximate loss if Spot drops by drop_pct.
    Formula: dPrice = (Delta * dSpot) + (0.5 * Gamma * (dSpot)^2)
    """
    try:
        dSpot = - (spot_price * drop_pct / 100)
        delta = float(item.get('delta', 0))
        gamma = float(item.get('gamma', 0))
        dPrice = (delta * dSpot) + (0.5 * gamma * (dSpot ** 2))
        return round(dPrice, 2)
    except:
        return 0

def build_report_data(currency, dvol_data, trades_data, binance_data, deribit_data):
    """Build report data structure for JSON output"""
    spot_price = 0
    if isinstance(deribit_data, dict) and 'contracts' in deribit_data and len(deribit_data['contracts']) > 0:
        spot_price = deribit_data['contracts'][0].get('underlying_price', 0)
    elif isinstance(binance_data, list) and len(binance_data) > 0:
        spot_price = 67200

    dvol_info = dvol_data.get('dvol', {}) if isinstance(dvol_data, dict) else {}
    dvol_raw = dvol_data if isinstance(dvol_data, dict) else {}
    trades_list = trades_data.get('alerts', []) if isinstance(trades_data, dict) else []
    trades_enriched = trades_data.get('trades', []) if isinstance(trades_data, dict) else []
    
    combined = []
    if isinstance(binance_data, list):
        for item in binance_data:
            item['platform'] = 'Binance'
            item['loss_at_10pct'] = simulate_loss(item, 10.0, spot_price)
            if 'open_interest' not in item and 'oi' in item:
                item['open_interest'] = item['oi']
            if 'premium_usd' not in item and 'premium_usdt' in item:
                item['premium_usd'] = item['premium_usdt']
            combined.append(item)
    if isinstance(deribit_data, dict) and 'contracts' in deribit_data:
        for item in deribit_data['contracts']:
            item['platform'] = 'Deribit'
            item['loss_at_10pct'] = simulate_loss(item, 10.0, spot_price)
            combined.append(item)
    
    combined.sort(key=lambda x: (x.get('liquidity_score', 0), x.get('apr', 0)), reverse=True)
    
    return {
        'timestamp': datetime.now().isoformat(),
        'currency': currency,
        'spot_price': spot_price,
        'dvol': dvol_info,
        'dvol_raw': dvol_raw,
        'large_trades': trades_list,
        'large_trades_count': len(trades_list),
        'large_trades_detail': trades_enriched,
        'contracts': combined[:20],
        'contracts_count': len(combined),
        'strategy_presets': STRATEGY_PRESETS
    }

def format_report(currency, dvol_data, trades_data, binance_data, deribit_data, json_output=False):
    report_data = build_report_data(currency, dvol_data, trades_data, binance_data, deribit_data)
    
    if json_output:
        print(json.dumps(report_data, ensure_ascii=False, indent=2))
        return report_data
    
    spot_price = report_data['spot_price']
    
    print("\n" + "="*145)
    print(f"加密期权全景扫描报告 | 币种: {currency} | 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*145)
    
    dvol_info = report_data['dvol']
    if dvol_info and 'error' not in dvol_info:
        print(f"【宏观环境】 DVOL: {dvol_info.get('current_dvol', 'N/A')} | 7天Z-Score: {dvol_info.get('z_score_7d', 'N/A')} | 信号: {dvol_info.get('signal', 'N/A')}")
    
    trades_list = report_data['large_trades']
    if trades_list:
        print(f"【大宗异动】 发现 {len(trades_list)} 条最近1小时大单预警:")
        for t in trades_list[:2]:
            print(f"   - {t.get('title')}: {t.get('message')}")

    print("-" * 145)
    header = f"{'平台':<8} {'合约':<22} {'DTE':<5} {'Strike':<8} {'Delta':<8} {'Gamma':<10} {'Margin-APR':<12} {'Liq分'} | {'-10%亏损(Est)'}"
    print(header)
    print("-" * len(header))

    for item in report_data['contracts']:
        platform = item.get('platform', 'N/A')
        name = (item.get('symbol') or item.get('instrument_name', 'N/A')).replace('BTC-', '').replace('USDT', '')
        dte, strike = item.get('dte', 0), item.get('strike', 0)
        delta, gamma = item.get('delta', 0), item.get('gamma', 0)
        apr, liq = item.get('apr', 0), item.get('liquidity_score', 0)
        loss_at_10 = item.get('loss_at_10pct', 0)
        print(f"{platform:<8} {name:<22} {dte:<5.1f} {strike:<8.0f} {delta:<8.4f} {gamma:<10.6f} {apr:<11.2f}% {liq:<6.0f} | -${loss_at_10:<12.0f}")

    print("="*145)
    print("注：APR基于20%保证金测算。亏损基于 Delta/Gamma 近似。")
    print("="*145 + "\n")
    return report_data

def main():
    parser = argparse.ArgumentParser(description="Aggregator v4.0 - JSON Mode Support")
    parser.add_argument('--currency', default='BTC')
    parser.add_argument('--max-delta', type=float, default=0.3)
    parser.add_argument('--min-dte', type=int, default=7)
    parser.add_argument('--max-dte', type=int, default=45)
    parser.add_argument('--strike', type=float)
    parser.add_argument('--strike-range')
    parser.add_argument('--margin-ratio', type=float, default=0.2)
    parser.add_argument('--option-type', type=str, default='PUT', choices=['PUT', 'CALL'])
    parser.add_argument('--preset', type=str, default=None, choices=['conservative', 'standard', 'aggressive'],
                        help='Strategy preset: conservative/standard/aggressive')
    parser.add_argument('--json', action='store_true', help='Output JSON format for API integration')
    args = parser.parse_args()

    import os
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    
    binance_script = os.path.join(BASE_DIR, "binance_options.py")
    deribit_script = os.path.join(BASE_DIR, "deribit-options-monitor", "__init__.py")

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        f_dvol = executor.submit(run_command, [sys.executable, deribit_script, "dvol", "--currency", args.currency])
        f_trades = executor.submit(run_command, [sys.executable, deribit_script, "large-trades", "--currency", args.currency])
        
        bin_cmd = [
            sys.executable, binance_script, 
            "--currency", args.currency, 
            "--min-dte", str(args.min_dte), 
            "--max-dte", str(args.max_dte), 
            "--max-delta", str(args.max_delta), 
            "--margin-ratio", str(args.margin_ratio),
            "--option-type", args.option_type
        ]
        if args.strike: bin_cmd += ["--strike", str(args.strike)]
        if args.strike_range: bin_cmd += ["--strike-range", args.strike_range]
        f_bin = executor.submit(run_command, bin_cmd)

        der_cmd = [
            sys.executable, deribit_script,
            "recommend", 
            "--currency", args.currency, 
            "--min-dte", str(args.min_dte), 
            "--max-dte", str(args.max_dte), 
            "--max-delta", str(args.max_delta), 
            "--margin-ratio", str(args.margin_ratio), 
            "--option-type", args.option_type,
            "--top-k", "20"
        ]
        if args.strike: der_cmd += ["--strike", str(args.strike)]
        if args.strike_range: der_cmd += ["--strike-range", args.strike_range]
        f_der = executor.submit(run_command, der_cmd)

        dvol_res, trades_res, bin_res, der_res = f_dvol.result(), f_trades.result(), f_bin.result(), f_der.result()

    # 应用策略预设
    preset_name = getattr(args, 'preset', None)
    opt_type = args.option_type.upper()
    if preset_name and preset_name in STRATEGY_PRESETS.get(opt_type, {}):
        preset = STRATEGY_PRESETS[opt_type][preset_name]
        args.max_delta = preset['max_delta']
        args.min_dte = preset['min_dte']
        args.max_dte = preset['max_dte']
        args.margin_ratio = preset['margin_ratio']

    report_data = format_report(args.currency, dvol_res, trades_res, bin_res, der_res, json_output=args.json)

    # DVOL自适应提示
    if not args.json and isinstance(dvol_res, dict) and 'dvol' in dvol_res:
        dvol_raw = dvol_res.get('dvol', {})
        params_for_adapt = {
            'max_delta': args.max_delta, 'min_apr': 15,
            'option_type': args.option_type
        }
        adapted = adapt_params_by_dvol(params_for_adapt, dvol_raw)
        if adapted.get('_dvol_advice'):
            print(f"\n[DVOL自适应建议] {' | '.join(adapted['_dvol_advice'])}")

    return report_data

if __name__ == "__main__":
    main()
