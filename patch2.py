with open('dashboard/services/exchange_abstraction.py', 'r', encoding='utf-8') as f:
    c = f.read()

if 'import httpx' not in c:
    c = c.replace('import asyncio', 'import asyncio\nimport httpx')

with open('dashboard/services/exchange_abstraction.py', 'w', encoding='utf-8') as f:
    f.write(c)
