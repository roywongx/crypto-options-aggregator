---
name: crypto-options-monitor
description: Use when the user asks about options, Deribit, Binance, BTC/ETH options, DVOL (波动率), Sell Put opportunities (收租/抄底), or large trades (大宗异动).
---

# Crypto Options Monitor (Deribit & Binance)

## Overview
This skill analyzes BTC and ETH options data to find "Sell Put" (卖出看跌期权) opportunities. It provides side-by-side comparisons between **Deribit (Coin-margined)** and **Binance (USDT-margined)**.

### Key Improvements
1. **Margin-based APR**: APR is calculated as `Premium / (Strike * Margin_Ratio)`, providing a realistic estimate of capital efficiency (Default Margin Ratio is 20%).
2. **Aggregated Reporting**: Use the aggregator script for unified side-by-side comparison.
3. **Risk Analysis**: Includes **Gamma** and **Vega** for monitoring risk acceleration.
4. **Specific Strike Support**: Allows filtering by specific strike price or strike range.

## Execution Command (Unified Aggregator)
Always use the aggregator for a complete picture:
```bash
python C:\gemini\options_aggregator.py --currency BTC --min-dte 3 --max-dte 30 --max-delta 0.5
```

### Advanced Parameters:
- `--strike <price>`: Filter for a specific strike (e.g., 64000).
- `--strike-range <min>-<max>`: Filter for a range (e.g., 60000-65000).
- `--margin-ratio <float>`: Adjust the margin requirement for APR calculation (default 0.2).
- `--currency <BTC|ETH|SOL|XRP|BNB|DOGE>`: Supports multiple assets (Binance).

## Response Guidelines
1. **Never dump raw JSON.** Use the formatted table output from the aggregator.
2. **Analyze Risk**: Mention **Gamma** especially if price is close to strike.
3. **Compare Platforms**: Point out where the best "Real APR" exists, noting the settlement difference (USDT vs BTC).
4. **Actionable Advice**: For aggressive styles (Delta 0.3-0.5), highlight the highest APR options with decent liquidity (OI/Volume).
