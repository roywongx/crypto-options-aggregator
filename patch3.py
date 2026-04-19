with open('dashboard/main.py', 'r', encoding='utf-8') as f:
    c = f.read()

bad_code = '''    try:
        from db.repository import ContractRepository
        repo = ContractRepository()
        currency = params.currency if params.currency else "BTC"
        all_contracts = repo.get_all_contracts(currency)
    except Exception as e:
        logger.warning("閼惧嘲褰囬崥鍫㈠閺佺増宓佹径杈Е: %s", e)
        all_contracts = []'''

good_code = '''    currency = params.currency if params.currency else "BTC"
    all_contracts = []
    try:
        rows = execute_read("SELECT contracts_data FROM scan_records WHERE currency = ? ORDER BY timestamp DESC LIMIT 1", (currency,))
        if rows and rows[0][0]:
            import json
            all_contracts = json.loads(rows[0][0])
    except Exception as e:
        logger.warning("Error fetching contracts: %s", e)'''

c = c.replace(bad_code, good_code)

with open('dashboard/main.py', 'w', encoding='utf-8') as f:
    f.write(c)
