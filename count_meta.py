import json
p='data/meta.json'
with open(p,encoding='utf-8') as f:
    data=json.load(f)
print(len(data))
