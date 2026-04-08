import re
with open(r'C:\gemini\crypto-options-aggregator\dashboard\main.py', encoding='utf-8') as f:
    text = f.read()
    m = re.search(r'@app.get\("/api/metrics/max-pain"\).*?return \w+', text, re.DOTALL)
    if m: print(m.group(0)[:1500])
