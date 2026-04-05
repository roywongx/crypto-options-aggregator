# Crypto Options Aggregator & Stress Tester

A professional-grade, dual-platform (Binance + Deribit) crypto options scanner designed for aggressive Sell Put and Covered Call strategies.

This project was built upon the excellent foundation provided by [lianyanshe-ai/deribit-options-monitor](https://github.com/lianyanshe-ai/deribit-options-monitor). We extended the core logic to support Binance, advanced risk modeling, and a unified cross-platform view.

## ✨ Key Features & Enhancements over the Original Repo

*   **Dual-Platform Integration**: Consolidates data from both Deribit (Coin-margined) and Binance (USDT-margined) into a single, unified leaderboard.
*   **True Capital Efficiency (Margin APR)**: Replaces the flawed `Premium / Strike` APR formula with a margin-based calculation (`Premium / (Strike * Margin_Ratio)`). This prevents the systemic overestimation of APR for deep ITM options and gives you the real yield on your locked capital.
*   **Delta & Liquidity Filtering for Binance**: Implemented strict max-delta filtering and liquidity scoring (Volume + Bid/Ask Spread) for the Binance API to keep you out of low-depth traps.
*   **Risk Stress Testing**: Calculates real-time Gamma and Vega. Evaluates your approximate floating loss if the spot price suddenly drops by 10% (using Taylor series approximation: $dPrice = \Delta \cdot dSpot + 0.5 \cdot \Gamma \cdot dSpot^2$).
*   **Covered Call Support**: The original repo was hardcoded for "Sell Put". We added full support for `--option-type CALL` to assist spot holders in generating yield.
*   **Macro Context**: Automatically fetches the current Deribit Implied Volatility Index (DVOL) and recent large institutional block trades to give you market sentiment context before you execute.

## 🚀 Installation

Ensure you have Python 3.10+ installed.

```bash
git clone https://github.com/roywongx/crypto-options-aggregator.git
cd crypto-options-aggregator
pip install -r requirements.txt
```

## 🛠️ Usage

The main entry point is the aggregator script.

### 1. Unified Scan (Default)
Finds the best Sell Put opportunities across both platforms with DTE between 3 and 30 days, Max Delta 0.5.
```bash
python options_aggregator.py
```

### 2. Specific Strike Stress Test (e.g., Aggressive Buy-the-Dip)
If you want to sell puts exactly at the 64,000 strike and want to see the 10% drop stress test:
```bash
python options_aggregator.py --strike 64000
```

### 3. Covered Call (Yield Generation on Spot)
If you hold BTC and want to sell calls at the 75,000 strike:
```bash
python options_aggregator.py --option-type CALL --strike 75000
```

### Advanced Parameters
*   `--currency`: BTC, ETH, SOL, XRP, BNB, DOGE (Note: Deribit only supports BTC/ETH/SOL/XRP, Binance supports all).
*   `--min-dte` / `--max-dte`: Filter by days to expiration.
*   `--max-delta`: Filter out deep ITM or excessively risky options (Default 0.5).
*   `--strike-range`: Filter by a range (e.g., `--strike-range 60000-65000`).
*   `--margin-ratio`: Adjust the margin requirement for APR calculation (Default 0.2 i.e., 20%).

## 🙏 Acknowledgments

Huge thanks to the original author **[lianyanshe-ai](https://github.com/lianyanshe-ai/deribit-options-monitor)** for the robust Deribit API wrapper, Greek calculations, and the DVOL/Large-trade logic. The solid architectural foundation made these trading-focused enhancements possible.

## ⚠️ Disclaimer
Options trading is extremely risky. This tool is for informational purposes only and does not constitute financial advice. The stress test calculations are approximations.
