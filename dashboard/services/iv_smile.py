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

    @staticmethod
    def _assess_sentiment(skew_25d: float, put_skew_pct: float, call_skew_pct: float) -> dict:
        if skew_25d > 15 or put_skew_pct > 30:
            return {"state": "PANIC", "label": "极度恐慌", "icon": "😱", "color": "#ef4444"}
        if skew_25d > 8 or put_skew_pct > 15:
            return {"state": "FEAR", "label": "市场恐慌", "icon": "😰", "color": "#ef4444"}
        if skew_25d > 3 or put_skew_pct > 5:
            return {"state": "CAUTIOUS", "label": "偏谨慎", "icon": "🤔", "color": "#f59e0b"}
        if skew_25d < -8 or call_skew_pct > 15:
            return {"state": "EUPHORIA", "label": "极度狂热", "icon": "🚀", "color": "#7132f5"}
        if skew_25d < -3 or call_skew_pct > 5:
            return {"state": "GREED", "label": "市场贪婪", "icon": "🤑", "color": "#7132f5"}
        return {"state": "NEUTRAL", "label": "中性", "icon": "😐", "color": "#9497a9"}

    @classmethod
    def _build_recommendations(cls, form: str, metrics: dict, sentiment: dict) -> list:
        recs = []
        atm_iv = metrics["atm_iv"]
        put_skew = metrics["put_skew_pct"]
        call_skew = metrics["call_skew_pct"]
        state = sentiment["state"]

        if form == "put_skew" and atm_iv > 40 and state in ("FEAR", "PANIC"):
            recs.append({
                "type": "sell_put", "title": "卖 OTM Put",
                "body": f"下行 IV 显著偏高 ({put_skew:.1f}%)，卖出虚值 Put 可收取超额恐慌溢价",
                "action": "Delta 0.15-0.25，DTE 7-14",
                "confidence": "HIGH",
            })
        elif form == "put_skew" and atm_iv > 30:
            recs.append({
                "type": "put_spread", "title": "卖 Put Spread",
                "body": f"下行 IV 偏高 ({put_skew:.1f}%)，用价差策略限制风险",
                "action": "卖 Put Delta 0.20 / 买 Put Delta 0.10",
                "confidence": "MEDIUM",
            })

        if form == "call_skew" and atm_iv > 40 and state in ("GREED", "EUPHORIA"):
            recs.append({
                "type": "sell_call", "title": "卖 OTM Call",
                "body": f"上行 IV 显著偏高 ({call_skew:.1f}%)，卖出虚值 Call 收取狂热溢价",
                "action": "Delta 0.15-0.25，DTE 7-14",
                "confidence": "HIGH",
            })

        if form == "flat" and atm_iv > 45:
            recs.append({
                "type": "iron_condor", "title": "铁鹰策略",
                "body": f"微笑平坦且 IV 偏高 ({atm_iv:.1f}%)，适合同时卖出虚值 Put 和 Call",
                "action": "Put Delta 0.15 / Call Delta 0.10，DTE 14-30",
                "confidence": "HIGH",
            })
        elif form == "flat" and atm_iv < 25:
            recs.append({
                "type": "long_straddle", "title": "买跨式",
                "body": f"微笑平坦且 IV 偏低 ({atm_iv:.1f}%)，买入跨式赌波动率上升",
                "action": "ATM Call + ATM Put，DTE 30+",
                "confidence": "MEDIUM",
            })

        if form == "smile" and atm_iv > 40:
            recs.append({
                "type": "sell_strangle", "title": "卖宽跨式",
                "body": "两端 IV 偏高，同时卖出虚值 Put 和 Call 收取双端溢价",
                "action": "Put Delta 0.15 / Call Delta 0.15",
                "confidence": "MEDIUM",
            })

        if abs(metrics["skew_25d"]) > 15:
            if metrics["skew_25d"] > 0:
                recs.append({
                    "type": "risk_reversal", "title": "Risk Reversal (卖Put买Call)",
                    "body": f"极端 put skew ({metrics['skew_25d']:.1f})，卖高IV端买低IV端",
                    "action": "卖 OTM Put + 买 OTM Call",
                    "confidence": "HIGH",
                })
            else:
                recs.append({
                    "type": "risk_reversal", "title": "Risk Reversal (卖Call买Put)",
                    "body": f"极端 call skew ({metrics['skew_25d']:.1f})，卖高IV端买低IV端",
                    "action": "卖 OTM Call + 买 OTM Put",
                    "confidence": "HIGH",
                })

        return recs[:3]

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
        sentiment = cls._assess_sentiment(agg["skew_25d"], agg["put_skew_pct"], agg["call_skew_pct"])
        recommendations = cls._build_recommendations(form, agg, sentiment)

        return {
            "form": form,
            "form_label": cls._FORM_LABELS.get(form, form),
            "form_icon": cls._FORM_ICONS.get(form, ""),
            "sentiment": sentiment,
            "metrics": agg,
            "by_expiry": expiry_metrics,
            "recommendations": recommendations,
        }
