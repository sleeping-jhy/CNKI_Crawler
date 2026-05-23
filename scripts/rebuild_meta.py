import json
import os
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
PDF_ROOT = ROOT / 'data' / 'pdf'
META_PATH = ROOT / 'data' / 'meta.json'

EXCLUDE_JOURNAL = '物理教师'

def main():
    records = []
    if not PDF_ROOT.exists():
        print('pdf 目录不存在:', PDF_ROOT)
        return
    for journal_dir in PDF_ROOT.iterdir():
        if not journal_dir.is_dir():
            continue
        journal = journal_dir.name
        if journal == EXCLUDE_JOURNAL:
            continue
        for year_dir in journal_dir.iterdir():
            if not year_dir.is_dir():
                continue
            year = year_dir.name
            for pdf in year_dir.glob('*.pdf'):
                rec = {
                    'id': pdf.stem,
                    'title': '',
                    'authors': '',
                    'journal': journal,
                    'year': year,
                    'dbcode': '',
                    'filename': pdf.stem,
                    'downloaded': True,
                    'pdf_path': str(pdf.relative_to(ROOT)),
                    'added_at': datetime.now().isoformat(),
                }
                records.append(rec)
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    with META_PATH.open('w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f'已写入 {len(records)} 条记录到', META_PATH)

if __name__ == '__main__':
    main()
