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
        # Calculate Greeks for each contract
        for c in contracts:
            bs = black_scholes_price(c["type"], c["strike"], spot, c["dte"], c["iv"])
            c["delta"] = bs["delta"]
            c["gamma"] = bs["gamma"]
            c["theta"] = bs["theta"]
            c["vega"] = bs["vega"]

        greeks_summary = cls._calc_greeks_summary(contracts)
        by_expiry = cls._calc_by_expiry(contracts, spot)
        gex = cls._calc_gex(contracts, spot)
        scenarios = cls._calc_scenarios(contracts, spot, gex)
        analysis = cls._build_analysis(contracts, spot, greeks_summary, gex)

        return {
            "currency": currency, "spot": round(spot, 2),
            "contract_count": len(contracts),
            "put_count": sum(1 for c in contracts if c["type"] == "P"),
            "call_count": sum(1 for c in contracts if c["type"] == "C"),
            "total_oi": round(sum(c["oi"] for c in contracts), 0),
            "greeks_summary": greeks_summary,
            "gex": gex,
            "by_expiry": by_expiry,
            "scenarios": scenarios,
            "analysis": analysis,
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

    @classmethod
    def _calc_greeks_summary(cls, contracts: list) -> dict:
        total_delta = 0.0
        total_gamma = 0.0
        total_theta = 0.0
        total_vega = 0.0
        total_oi = 0.0

        for c in contracts:
            weight = c["oi"]
            total_delta += c["delta"] * weight
            total_gamma += c["gamma"] * weight
            total_theta += c["theta"] * weight
            total_vega += c["vega"] * weight
            total_oi += weight

        if total_oi <= 0:
            return {}

        return {
            "per_contract": {
                "delta": round(total_delta / total_oi, 4),
                "gamma": round(total_gamma / total_oi, 6),
                "theta": round(total_theta / total_oi, 2),
                "vega": round(total_vega / total_oi, 2),
            },
            "total_exposure": {
                "delta": round(total_delta, 2),
                "gamma": round(total_gamma, 4),
                "theta": round(total_theta, 2),
                "vega": round(total_vega, 2),
            },
        }

    @classmethod
    def _calc_by_expiry(cls, contracts: list, spot: float) -> list:
        expiries = {}
        for c in contracts:
            key = c["dte"]
            if key not in expiries:
                expiries[key] = {"contracts": [], "oi": 0}
            expiries[key]["contracts"].append(c)
            expiries[key]["oi"] += c["oi"]

        result = []
        for dte, data in sorted(expiries.items()):
            total_delta = 0.0
            total_gamma = 0.0
            total_theta = 0.0
            total_vega = 0.0
            total_oi = 0.0
            atm_iv = 0.0
            closest_strike_dist = float("inf")

            for c in data["contracts"]:
                weight = c["oi"]
                total_delta += c["delta"] * weight
                total_gamma += c["gamma"] * weight
                total_theta += c["theta"] * weight
                total_vega += c["vega"] * weight
                total_oi += weight

                dist = abs(c["strike"] - spot)
                if dist < closest_strike_dist:
                    closest_strike_dist = dist
                    atm_iv = c["iv"]

            result.append({
                "dte": dte,
                "delta": round(total_delta, 2),
                "gamma": round(total_gamma, 4),
                "theta": round(total_theta, 2),
                "vega": round(total_vega, 2),
                "atm_iv": round(atm_iv, 2),
                "contract_count": len(data["contracts"]),
                "total_oi": round(total_oi, 0),
            })
        return result

    @classmethod
    def _calc_gex(cls, contracts: list, spot: float) -> dict:
        """Calculate Gamma Exposure by strike."""
        strike_data = {}
        for c in contracts:
            strike = c["strike"]
            if strike not in strike_data:
                strike_data[strike] = {"call_gex": 0.0, "put_gex": 0.0}
            gex_val = c["gamma"] * c["oi"] * spot * spot * 0.01
            if c["type"] == "C":
                strike_data[strike]["call_gex"] += gex_val
            else:
                strike_data[strike]["put_gex"] -= gex_val  # Put GEX is negative

        by_strike = []
        for strike in sorted(strike_data.keys()):
            d = strike_data[strike]
            net = d["call_gex"] + d["put_gex"]
            by_strike.append({
                "strike": strike,
                "call_gex": round(d["call_gex"], 0),
                "put_gex": round(d["put_gex"], 0),
                "net_gex": round(net, 0),
            })

        total_gex = sum(e["net_gex"] for e in by_strike)

        # Find flip strike (where net_gex crosses zero)
        flip_strike = 0
        for i in range(len(by_strike) - 1):
            if by_strike[i]["net_gex"] * by_strike[i + 1]["net_gex"] < 0:
                flip_strike = by_strike[i]["strike"]
                break
        if flip_strike == 0 and by_strike:
            flip_strike = by_strike[0]["strike"]

        # Find pin strike (highest total OI concentration)
        oi_by_strike = {}
        for c in contracts:
            s = c["strike"]
            oi_by_strike[s] = oi_by_strike.get(s, 0) + c["oi"]
        pin_strike = max(oi_by_strike, key=oi_by_strike.get) if oi_by_strike else 0

        # Pin risk level
        pin_oi = oi_by_strike.get(pin_strike, 0)
        avg_oi = sum(oi_by_strike.values()) / len(oi_by_strike) if oi_by_strike else 1
        concentration = pin_oi / avg_oi if avg_oi > 0 else 0
        if concentration > 10:
            pin_risk_level = "HIGH"
        elif concentration > 3:
            pin_risk_level = "MEDIUM"
        else:
            pin_risk_level = "LOW"

        return {
            "by_strike": by_strike,
            "total_gex": round(total_gex, 0),
            "flip_strike": flip_strike,
            "pin_strike": pin_strike,
            "pin_oi": round(pin_oi, 0),
            "avg_oi": round(avg_oi, 0),
            "concentration": round(concentration, 1),
            "pin_risk_level": pin_risk_level,
        }

    @classmethod
    def _calc_scenarios(cls, contracts: list, spot: float, gex: dict) -> dict:
        """Calculate scenario P&L shocks and pin risk metrics."""
        total_delta = 0.0
        total_vega = 0.0

        for c in contracts:
            weight = c["oi"]
            total_delta += c["delta"] * weight
            total_vega += c["vega"] * weight

        return {
            "down_10pct": round(total_delta * spot * -0.1, 0),
            "up_10pct": round(total_delta * spot * 0.1, 0),
            "iv_up_5pct": round(total_vega * 5, 0),
            "iv_down_5pct": round(total_vega * -5, 0),
            "pin_scenario": {
                "pin_strike": gex.get("pin_strike", 0),
                "pin_oi": gex.get("pin_oi", 0),
                "avg_oi": gex.get("avg_oi", 1),
                "concentration": gex.get("concentration", 0),
            },
        }

    @classmethod
    def _build_analysis(cls, contracts: list, spot: float, greeks_summary: dict,
                        gex: dict) -> Optional[dict]:
        if not greeks_summary or not gex:
            return None

        per = greeks_summary.get("per_contract", {})
        total_gex = gex.get("total_gex", 0)
        pin_risk_level = gex.get("pin_risk_level", "LOW")
        pin_strike = gex.get("pin_strike", 0)
        atm_iv = 0
        closest_dist = float("inf")

        # Get ATM IV from nearest strike
        for c in contracts:
            dist = abs(c["strike"] - spot)
            if dist < closest_dist:
                closest_dist = dist
                atm_iv = c["iv"]

        # GEX Regime
        if total_gex > 0:
            gex_regime = {"state": "POSITIVE", "label": "正 Gamma", "icon": "shield",
                          "description": "做市商净多 gamma，价格趋于均值回归"}
        elif total_gex < 0:
            gex_regime = {"state": "NEGATIVE", "label": "负 Gamma", "icon": "zap",
                          "description": "做市商净空 gamma，趋势可能加速"}
        else:
            gex_regime = {"state": "NEUTRAL", "label": "中性 Gamma", "icon": "balance",
                          "description": "GEX 接近零，方向不明确"}

        # Pin Risk
        pin_risk_info = {
            "level": pin_risk_level,
            "label": {"HIGH": "高 Pin Risk", "MEDIUM": "中 Pin Risk", "LOW": "低 Pin Risk"}[pin_risk_level],
            "icon": "pin",
            "description": f"{pin_strike} strike OI 集中" if pin_strike else "无明显 pin",
        }

        # Market State
        delta_val = per.get("delta", 0)
        if pin_risk_level == "HIGH":
            market_state = {"state": "PIN_RISK", "label": "Pin 风险", "icon": "pin", "color": "#ef4444"}
        elif total_gex < 0 and atm_iv > 40:
            market_state = {"state": "VOLATILE", "label": "高波动", "icon": "waves", "color": "#ef4444"}
        elif total_gex > 0 and atm_iv < 25:
            market_state = {"state": "CALM", "label": "平静", "icon": "sleep", "color": "#149e61"}
        elif total_gex < 0 and delta_val > 0:
            market_state = {"state": "TRENDING_UP", "label": "趋势上行", "icon": "chart-up", "color": "#149e61"}
        elif total_gex < 0 and delta_val < 0:
            market_state = {"state": "TRENDING_DOWN", "label": "趋势下行", "icon": "chart-down", "color": "#ef4444"}
        else:
            market_state = {"state": "MEAN_REVERTING", "label": "均值回归", "icon": "refresh", "color": "#3b82f6"}

        # Risk Ratings
        def _rate_greek(value, high_thresh, med_thresh):
            av = abs(value)
            if av > high_thresh:
                return "HIGH"
            if av > med_thresh:
                return "MEDIUM"
            return "LOW"

        risk_ratings = {
            "delta": {"level": _rate_greek(delta_val, 0.5, 0.2), "label": "", "value": round(delta_val, 4)},
            "gamma": {"level": _rate_greek(per.get("gamma", 0), 0.01, 0.005), "label": "", "value": round(per.get("gamma", 0), 6)},
            "theta": {"level": _rate_greek(per.get("theta", 0), 100, 50), "label": "", "value": round(per.get("theta", 0), 2)},
            "vega": {"level": _rate_greek(per.get("vega", 0), 500, 200), "label": "", "value": round(per.get("vega", 0), 2)},
        }
        level_labels = {"HIGH": "RED high", "MEDIUM": "YELLOW medium", "LOW": "GREEN low"}
        for g in risk_ratings:
            risk_ratings[g]["label"] = level_labels[risk_ratings[g]["level"]]

        # Interpretation
        interpretation = []
        if total_gex > 0:
            interpretation.append("GEX 为正，做市商处于多 gamma 位置，市场倾向于均值回归")
        elif total_gex < 0:
            interpretation.append("GEX 为负，做市商处于空 gamma 位置，趋势可能加速")
        else:
            interpretation.append("GEX 接近零，方向不明确")

        if pin_risk_level == "HIGH":
            interpretation.append(f"Pin Risk 高，{pin_strike} 附近 OI 集中，到期前价格可能被吸附")
        elif pin_risk_level == "MEDIUM":
            interpretation.append(f"Pin Risk 中等，{pin_strike} 附近有一定 OI 集中")

        if risk_ratings["vega"]["level"] == "HIGH":
            interpretation.append("Vega 敞口大，IV 变动会显著影响持仓价值")
        if risk_ratings["theta"]["level"] == "HIGH":
            interpretation.append("Theta 衰减快，时间价值损耗显著")

        # Hedge Suggestions
        suggestions = []
        if abs(delta_val) > 0.5:
            suggestions.append({
                "type": "delta_hedge", "title": "对冲方向风险",
                "body": f"Delta 敞口较大 ({delta_val:.4f})，建议买入反向期权对冲",
                "action": "买入反向期权使 Delta 接近中性",
                "confidence": "HIGH",
            })
        if pin_risk_level == "HIGH":
            suggestions.append({
                "type": "reduce_position", "title": "到期前减仓",
                "body": f"Pin Risk 高，{pin_strike} 附近 OI 集中度高",
                "action": "将近期仓位移至更远到期日",
                "confidence": "HIGH",
            })
        if total_gex > 0 and atm_iv > 30:
            suggestions.append({
                "type": "sell_straddle", "title": "卖出跨式",
                "body": "正 Gamma 环境适合卖出跨式收取时间价值",
                "action": "在 ATM strike 卖出跨式，Delta 中性",
                "confidence": "MEDIUM",
            })
        if total_gex < 0 and delta_val > 0:
            suggestions.append({
                "type": "trend_follow", "title": "顺势加仓",
                "body": "负 Gamma + 正 Delta，上行趋势可能加速",
                "action": "考虑加仓多头或买入 Call 跟趋势",
                "confidence": "MEDIUM",
            })
        if risk_ratings["vega"]["level"] == "HIGH":
            suggestions.append({
                "type": "vega_hedge", "title": "做空波动率",
                "body": "Vega 敞口大，若预期 IV 下降可卖出宽跨式",
                "action": "卖出宽跨式做空波动率",
                "confidence": "MEDIUM",
            })

        return {
            "gex_regime": gex_regime,
            "pin_risk": pin_risk_info,
            "market_state": market_state,
            "risk_ratings": risk_ratings,
            "interpretation": interpretation,
            "hedge_suggestions": suggestions[:4],
        }
