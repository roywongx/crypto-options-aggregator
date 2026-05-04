"""IV Smile Analyzer — skew metrics, form classification, sentiment, strategy recommendations."""
from typing import List, Dict, Optional


class IVSmileAnalyzer:
    @classmethod
    def analyze(cls, contracts_data: list, spot: float, currency: str = "BTC") -> dict:
        smiles = cls._extract_smiles(contracts_data, spot)
        analysis = cls._build_analysis(smiles, spot) if len(smiles) >= 2 else None
        return {"currency": currency, "spot": round(spot, 2), "smiles": smiles, "analysis": analysis}

    @classmethod
    def _extract_smiles(cls, contracts_data: list, spot: float) -> dict:
        by_expiry = {}
        for c in contracts_data:
            iv = c.get("mark_iv") or c.get("iv") or 0
            strike = c.get("strike", 0)
            dte = c.get("dte", 0)
            option_type = c.get("option_type", "")
            oi = c.get("oi") if c.get("oi") is not None else c.get("open_interest", 0)
            volume = c.get("volume") if c.get("volume") is not None else 0

            iv_float = float(iv) if iv else 0
            if 0 < iv_float < 1.0:
                iv_float *= 100
            elif iv_float > 200:
                continue

            if iv_float <= 0 or float(strike) <= 0 or float(dte) <= 0:
                continue
            if float(oi) < 1:
                continue

            exp_key = int(float(dte))
            if exp_key not in by_expiry:
                by_expiry[exp_key] = []
            by_expiry[exp_key].append({
                "strike": float(strike),
                "iv": round(iv_float, 2),
                "type": option_type.upper()[0] if option_type else "?",
                "oi": float(oi),
                "volume": float(volume) if volume else 0,
                "moneyness": round((float(strike) - spot) / spot * 100, 2) if spot > 0 else 0,
            })

        sorted_expiries = sorted(by_expiry.keys())
        result = {}
        for exp_dte in sorted_expiries[:3]:
            points = sorted(by_expiry[exp_dte], key=lambda x: x["strike"])
            puts = [p for p in points if p["type"] == "P"]
            calls = [p for p in points if p["type"] == "C"]
            result[f"dte_{exp_dte}"] = {"dte": exp_dte, "puts": puts, "calls": calls, "all": points}
        return result

    @classmethod
    def _build_analysis(cls, smiles: dict, spot: float) -> Optional[dict]:
        return None  # placeholder — implemented in Task 2
