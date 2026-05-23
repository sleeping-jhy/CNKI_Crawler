import json
from pathlib import Path
from collections import defaultdict

def generate_summary():
    meta_path = Path('data/meta.json')
    if not meta_path.exists():
        print("Error: data/meta.json not found.")
        return

    with open(meta_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Use a dictionary to store counts: {(year, journal): count}
    summary = defaultdict(int)
    for item in data:
        year = str(item.get('year', 'Unknown'))
        journal = item.get('journal', 'Unknown')
        summary[(year, journal)] += 1

    # Print the table header
    print("| 年份 | 期刊 | 数量 |")
    print("| :--- | :--- | :--- |")

    # Sort by year then journal
    sorted_keys = sorted(summary.keys(), key=lambda x: (x[0], x[1]))

    for year, journal in sorted_keys:
        print(f"| {year} | {journal} | {summary[(year, journal)]} |")

if __name__ == "__main__":
    generate_summary()
