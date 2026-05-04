"""Greeks Analyzer — GEX, Pin Risk, market state, hedge suggestions."""
from typing import List, Dict, Optional
from services.shared_calculations import black_scholes_price


class GreeksAnalyzer:
    @classmethod
    def analyze(cls, contracts_data: list, spot: float, currency: str = "BTC") -> dict:
        contracts = cls._extract_contracts(contracts_data, spot)
        if not contracts:
            return {
                "currency": currency, "spot": round(spot, 2),
                "contract_count": 0, "put_count": 0, "call_count": 0, "total_oi": 0,
                "greeks_summary": {}, "gex": {}, "by_expiry": [],
                "scenarios": {}, "analysis": None,
            }
        return {
            "currency": currency, "spot": round(spot, 2),
            "contract_count": len(contracts),
            "put_count": sum(1 for c in contracts if c["type"] == "P"),
            "call_count": sum(1 for c in contracts if c["type"] == "C"),
            "total_oi": round(sum(c["oi"] for c in contracts), 0),
            "greeks_summary": {},
            "gex": {},
            "by_expiry": [],
            "scenarios": {},
            "analysis": None,
        }

    @classmethod
    def _extract_contracts(cls, contracts_data: list, spot: float) -> list:
        result = []
        for c in contracts_data:
            iv = c.get("mark_iv") or c.get("iv") or 0
            strike = float(c.get("strike", 0))
            dte = int(float(c.get("dte", 0)))
            option_type = c.get("option_type", "")
            oi_raw = c.get("oi") if c.get("oi") is not None else c.get("open_interest", 0)
            oi = float(oi_raw) if oi_raw else 0
            premium = float(c.get("premium_usd", c.get("premium", 0)) or 0)

            iv_float = float(iv) if iv else 0
            if 0 < iv_float < 1.0:
                iv_float *= 100
            elif iv_float > 200 or iv_float <= 0:
                continue

            if strike <= 0 or dte <= 0 or oi < 1:
                continue

            result.append({
                "strike": strike,
                "dte": dte,
                "iv": round(iv_float, 2),
                "type": option_type.upper()[0] if option_type else "?",
                "oi": oi,
                "premium": premium,
            })
        return result
