with open('binance_options.py', 'r', encoding='utf-8') as f:
    c = f.read()

c = c.replace('logger.error("fetch_binance_options error: %s", str(e))', 'logger.error("fetch_binance_options error: %s", str(e))\n        return []')

with open('binance_options.py', 'w', encoding='utf-8') as f:
    f.write(c)
