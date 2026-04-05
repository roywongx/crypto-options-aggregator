    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    large_trades = data.get('large_trades_details', []) or data.get('large_trades', [])
    
    cursor.execute("""
        INSERT INTO scan_records 
        (currency, spot_price, dvol_current, dvol_z_score, dvol_signal, 
         large_trades_count, large_trades_details, contracts_data, raw_output)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get('currency', 'BTC'),
        data.get('spot_price', 0),
        data.get('dvol_current', 0),
        data.get('dvol_z_score', 0),
        data.get('dvol_signal', ''),
        data.get('large_trades_count', 0) or len(large_trades),
        json.dumps(large_trades, ensure_ascii=False),
        json.dumps(data.get('contracts', []), ensure_ascii=False),
        data.get('raw_output', '')
    ))
