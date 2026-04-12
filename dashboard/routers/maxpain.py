# Max Pain and GEX calculation routes
from fastapi import APIRouter, Query
from datetime import datetime

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _fetch_deribit_summaries(currency="BTC"):
    try:
        from main import _get_deribit_monitor
        mon = _get_deribit_monitor()
        return mon._get_book_summaries(currency)
    except Exception:
        return []


@router.get("/max-pain")
async def get_max_pain(currency: str = Query(default="BTC")):
    return await _calc_max_pain_internal(currency)


async def _calc_max_pain_internal(currency: str):
    from services.instrument import _parse_inst_name
    from services.spot_price import get_spot_price, _get_spot_from_scan
    from services.dvol_analyzer import calc_delta_bs

    summaries = _fetch_deribit_summaries(currency)
    if not summaries:
        return {"error": "No data"}

    parsed = []
    for s in summaries:
        meta = _parse_inst_name(s.get("instrument_name", ""))
        if not meta or meta.dte < 1:
            continue
        oi = float(s.get("open_interest") or 0)
        gamma = float(s.get("gamma") or 0)
        if oi < 1:
            continue
        parsed.append({"strike": meta.strike, "expiry": meta.expiry, "dte": meta.dte, "option_type": meta.option_type, "oi": oi, "gamma": gamma})

    if not parsed:
        total = len(summaries)
        no_meta = sum(1 for s in summaries if not _parse_inst_name(s.get("instrument_name", "")))
        oi_zero = sum(1 for s in summaries if float(s.get("open_interest") or 0) < 1)
        return {"error": "No OI data", "debug": {"total": total, "no_meta": no_meta, "oi_zero": oi_zero}}

    strikes = sorted(set(p["strike"] for p in parsed))
    expiries = sorted(set((p["expiry"], p["dte"]) for p in parsed))

    try:
        spot = get_spot_price(currency)
    except Exception:
        db_spot = _get_spot_from_scan()
        spot = db_spot if db_spot > 1000 else (strikes[len(strikes)//2] if strikes else 0)

    results = []
    for exp_name, exp_dte in expiries[:4]:
        calls = [p for p in parsed if p["expiry"] == exp_name and p["option_type"] == "C"]
        puts = [p for p in parsed if p["expiry"] == exp_name and p["option_type"] == "P"]
        if not calls and not puts:
            continue
        co_map = {p["strike"]: p["oi"] for p in calls}
        po_map = {p["strike"]: p["oi"] for p in puts}
        cg_map = {p["strike"]: p["gamma"] * p["oi"] for p in calls}
        pg_map = {p["strike"]: p["gamma"] * p["oi"] for p in puts}

        mp_strike = strikes[0]
        min_pain = float('inf')
        pain_at_s = 0
        pc = []
        gc = []
        flip = None
        prev_sign = None

        for ts in strikes:
            cp = sum(max(0, ts - k) * v for k, v in co_map.items())
            pp = sum(max(0, k - ts) * v for k, v in po_map.items())
            tp = cp + pp
            pc.append({"strike": ts, "pain": round(tp, 0), "call_pain": round(cp, 0), "put_pain": round(pp, 0)})
            if tp < min_pain:
                min_pain = tp
                mp_strike = ts
            if int(round(ts)) == int(round(spot)):
                pain_at_s = tp
            ng = sum(g for k, g in cg_map.items() if k >= ts) + sum(-g for k, g in pg_map.items() if k <= ts)
            call_oi_above = sum(v for k, v in co_map.items() if k >= ts)
            put_oi_below = sum(v for k, v in po_map.items() if k <= ts)
            net_oi_exposure = call_oi_above - put_oi_below
            if ng != 0:
                ngex = ng * spot * spot / 100
            else:
                ngex = net_oi_exposure * 100
            gc.append({"strike": ts, "gex": round(ngex, 0), "net_gamma": round(ng, 2),
                       "net_oi_exposure": round(net_oi_exposure, 0),
                       "call_oi_above": round(call_oi_above, 0), "put_oi_below": round(put_oi_below, 0)})
            cs = 1 if net_oi_exposure >= 0 else -1
            if prev_sign is not None and cs != prev_sign:
                flip = ts
            prev_sign = cs

        dist = ((mp_strike - spot) / spot * 100) if spot > 0 else 0
        tco = sum(co_map.values())
        tpo = sum(po_map.values())
        pcr = tpo / tco if tco > 0 else 0
        sig = "中性"
        if dist > 3:
            sig = "偏多: 价格在最大痛点下方"
        elif dist < -3:
            sig = "偏空: 价格在最大痛点上方"
        mm = ""
        if flip:
            if spot < flip:
                mm = f"⚠️ 危险: 现货 ${spot:,.0f} < Flip点 ${flip:,.0f} | 空头Gamma区，波动放大风险"
            else:
                mm = f"✅ 安全: 现货 ${spot:,.0f} > Flip点 ${flip:,.0f} | 多头Gamma区，波动受抑"

        results.append({"expiry": exp_name, "dte": exp_dte, "max_pain": round(mp_strike, 0),
            "dist_pct": round(dist, 2), "pain_at_spot": round(pain_at_s, 0),
            "pcr": round(pcr, 3), "call_oi": round(tco, 0), "put_oi": round(tpo, 0),
            "signal": sig, "pain_curve": pc, "gex_curve": gc,
            "flip_point": flip, "mm_signal": mm})

    best = results[0] if results else {}
    return {"currency": currency, "spot": round(spot, 0), "expiries": results,
        "nearest_mp": best.get("max_pain"), "nearest_dist": best.get("dist_pct"),
        "signal": best.get("signal", ""), "mm_overview": best.get("mm_signal", "")}