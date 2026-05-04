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

    _FORM_LABELS = {
        "smile": "对称微笑型", "put_skew": "下行恐慌型",
        "call_skew": "上行狂热型", "flat": "平坦型",
    }
    _FORM_ICONS = {"smile": "😐", "put_skew": "📉", "call_skew": "📈", "flat": "➡️"}

    @staticmethod
    def _find_atm_iv(points: list, spot: float) -> float:
        closest = min(points, key=lambda p: abs(p["strike"] - spot))
        return closest["iv"]

    @staticmethod
    def _calc_25d_skew(points: list, spot: float) -> float:
        put_candidates = [p for p in points if p["type"] == "P" and p["moneyness"] < -5 and p["moneyness"] > -15]
        call_candidates = [p for p in points if p["type"] == "C" and p["moneyness"] > 5 and p["moneyness"] < 15]
        put_iv = sum(p["iv"] for p in put_candidates) / len(put_candidates) if put_candidates else 0
        call_iv = sum(p["iv"] for p in call_candidates) / len(call_candidates) if call_candidates else 0
        return put_iv - call_iv

    @staticmethod
    def _calc_side_skew(points: list, spot: float, side: str, atm_iv: float) -> float:
        if atm_iv <= 0:
            return 0
        if side == "P":
            otm = [p for p in points if p["type"] == "P" and p["moneyness"] < -3]
        else:
            otm = [p for p in points if p["type"] == "C" and p["moneyness"] > 3]
        if not otm:
            return 0
        avg_otm_iv = sum(p["iv"] for p in otm) / len(otm)
        return (avg_otm_iv - atm_iv) / atm_iv * 100

    @staticmethod
    def _calc_skew_slope(points: list, spot: float) -> float:
        valid = [(p["moneyness"], p["iv"]) for p in points if p["moneyness"] != 0]
        if len(valid) < 3:
            return 0
        x_mean = sum(v[0] for v in valid) / len(valid)
        y_mean = sum(v[1] for v in valid) / len(valid)
        num = sum((x - x_mean) * (y - y_mean) for x, y in valid)
        den = sum((x - x_mean) ** 2 for x, _ in valid)
        return num / den if den > 0 else 0

    @staticmethod
    def _calc_curvature(points: list, spot: float, atm_iv: float) -> float:
        if atm_iv <= 0:
            return 0
        wings = [p["iv"] for p in points if abs(p["moneyness"]) > 7]
        if not wings:
            return 0
        wing_avg = sum(wings) / len(wings)
        return (wing_avg - atm_iv) / atm_iv * 100

    @staticmethod
    def _classify_form(put_skew_pct: float, call_skew_pct: float) -> str:
        if put_skew_pct > 5 and call_skew_pct > 5:
            return "smile"
        if put_skew_pct > 5:
            return "put_skew"
        if call_skew_pct > 5:
            return "call_skew"
        return "flat"

    @classmethod
    def _build_analysis(cls, smiles: dict, spot: float) -> Optional[dict]:
        if not smiles or not spot:
            return None

        expiry_metrics = []
        for key, smile in smiles.items():
            all_pts = smile.get("all", [])
            if len(all_pts) < 3:
                continue

            atm_iv = cls._find_atm_iv(all_pts, spot)
            skew_25d = cls._calc_25d_skew(all_pts, spot)
            put_skew_pct = cls._calc_side_skew(all_pts, spot, "P", atm_iv)
            call_skew_pct = cls._calc_side_skew(all_pts, spot, "C", atm_iv)
            skew_slope = cls._calc_skew_slope(all_pts, spot)
            curvature = cls._calc_curvature(all_pts, spot, atm_iv)
            form = cls._classify_form(put_skew_pct, call_skew_pct)

            expiry_metrics.append({
                "dte": smile["dte"],
                "atm_iv": round(atm_iv, 2),
                "skew_25d": round(skew_25d, 2),
                "put_skew_pct": round(put_skew_pct, 2),
                "call_skew_pct": round(call_skew_pct, 2),
                "skew_slope": round(skew_slope, 4),
                "curvature": round(curvature, 2),
                "form": form,
                "form_label": cls._FORM_LABELS.get(form, form),
                "point_count": len(all_pts),
            })

        if not expiry_metrics:
            return None

        # Aggregate metrics (weighted by 1/dte — near-term matters more)
        total_weight = sum(1.0 / m["dte"] for m in expiry_metrics)
        agg = {}
        for key in ["atm_iv", "skew_25d", "put_skew_pct", "call_skew_pct", "skew_slope", "curvature"]:
            agg[key] = round(sum(m[key] / m["dte"] for m in expiry_metrics) / total_weight, 2)

        form = cls._classify_form(agg["put_skew_pct"], agg["call_skew_pct"])
        sentiment = {"state": "NEUTRAL", "label": "中性", "icon": "😐", "color": "#9497a9"}
        recommendations = []

        return {
            "form": form,
            "form_label": cls._FORM_LABELS.get(form, form),
            "form_icon": cls._FORM_ICONS.get(form, ""),
            "sentiment": sentiment,
            "metrics": agg,
            "by_expiry": expiry_metrics,
            "recommendations": recommendations,
        }
