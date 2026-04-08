import json
with open('main.py', 'r', encoding='utf-8') as f:
    text = f.read()

new_api = """
@app.get("/api/metrics/max-pain")
async def get_max_pain(currency: str = Query(default="BTC")):
    '''计算当月最大痛点 (Max Pain)'''
    import sqlite3, json
    from datetime import datetime
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT contracts_data, spot_price FROM scan_records 
        WHERE currency = ? 
        ORDER BY timestamp DESC LIMIT 1
    ''', (currency,))
    row = cursor.fetchone()
    conn.close()
    
    if not row or not row[0]:
        return {"max_pain": 0, "current_price": 0, "error": "No data"}
        
    try:
        contracts = json.loads(row[0])
        spot = row[1] or 0
        
        expiries = {}
        for c in contracts:
            exp = c.get('expiry_ts', 0)
            if not exp: continue
            if exp not in expiries: expiries[exp] = []
            expiries[exp].append(c)
            
        if not expiries:
            return {"max_pain": 0}
            
        target_exp = max(expiries.keys(), key=lambda k: sum(c.get('open_interest', 0) for c in expiries[k]))
        target_contracts = expiries[target_exp]
        
        strikes = sorted(list(set(c.get('strike', 0) for c in target_contracts)))
        if not strikes:
            return {"max_pain": 0}
            
        pain_points = {}
        for strike in strikes:
            total_pain = 0
            for c in target_contracts:
                s = c.get('strike', 0)
                oi = c.get('open_interest', 0)
                opt_type = c.get('option_type', 'P').upper()
                
                if opt_type == 'C' and strike > s:
                    total_pain += (strike - s) * oi
                elif opt_type == 'P' and strike < s:
                    total_pain += (s - strike) * oi
            pain_points[strike] = total_pain
            
        max_pain_strike = min(pain_points.keys(), key=lambda k: pain_points[k])
        
        return {
            "max_pain": max_pain_strike,
            "current_price": spot,
            "diff_pct": round((max_pain_strike - spot) / spot * 100, 2) if spot else 0,
            "target_expiry_dt": datetime.fromtimestamp(target_exp/1000).strftime('%Y-%m-%d')
        }
    except Exception as e:
        return {"error": str(e)}
"""

if 'api/metrics/max-pain' not in text:
    text = text.replace('@app.get("/api/stats")', new_api + '\n@app.get("/api/stats")')
    with open('main.py', 'w', encoding='utf-8') as f:
        f.write(text)
    print('Max Pain API successfully injected.')
else:
    print('Max Pain API already exists.')
