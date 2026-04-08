import re
with open(r'C:\gemini\crypto-options-aggregator\dashboard\main.py', encoding='utf-8') as f:
    text = f.read()
    m = re.search(r'@app.post\("/api/quick-scan"\)(.*?)return \{', text, re.DOTALL)
    if m:
        print('@app.post("/api/quick-scan")' + m.group(1) + 'return {')
