# Script to delete trade endpoints from main.py
with open('main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

endpoints = ['/api/trades/history', '/api/trades/strike-distribution', '/api/trades/wind-analysis']
delete_ranges = []
i = 0
while i < len(lines):
    line = lines[i]
    for ep in endpoints:
        if f'"/{ep.lstrip("/")}"' in line or f"'{ep}'" in line:
            start = i
            j = i + 1
            while j < len(lines):
                if lines[j].startswith('@app.') or lines[j].startswith('async def ') or lines[j].startswith('def '):
                    break
                j += 1
            delete_ranges.append((start, j))
            i = j
            break
    else:
        i += 1

for start, end in reversed(delete_ranges):
    del lines[start:end]

with open('main.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print(f"Deleted {len(delete_ranges)} endpoints, remaining lines: {len(lines)}")
