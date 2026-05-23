#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检测爬取不完整的期刊

问题：很多期刊每年只爬取了约20篇（一页），实际应该有更多页
这个脚本会：
1. 统计每个期刊每年的已下载数量
2. 标记可疑的期刊（每年只有约20篇的）
3. 输出需要重新爬取的期刊列表
"""
import json
from pathlib import Path
from collections import defaultdict


def normalize_progress_data(data):
    """规范化 crawl_progress.yaml 数据，避免空节点被解析为 None。"""
    if not isinstance(data, dict):
        data = {}
    else:
        data = dict(data)

    for key in ('completed', 'partial'):
        if not isinstance(data.get(key), dict):
            data[key] = {}

    return data


def analyze_meta():
    """分析 meta.json 中的数据"""
    meta_path = Path("data/meta.json")
    if not meta_path.exists():
        print("❌ meta.json 不存在")
        return
    
    with open(meta_path, "r", encoding="utf-8") as f:
        articles = json.load(f)
    
    print(f"📊 总文章数: {len(articles)}")
    
    # 按期刊和年份统计
    journal_year_stats = defaultdict(lambda: defaultdict(int))
    
    for article in articles:
        journal = article.get("journal", "unknown")
        year = str(article.get("year", "unknown"))
        journal_year_stats[journal][year] += 1
    
    # 分析可疑期刊（每年只有约20篇的）
    suspicious_journals = []
    normal_journals = []
    
    print("\n" + "=" * 80)
    print("📋 各期刊各年份统计")
    print("=" * 80)
    
    for journal in sorted(journal_year_stats.keys()):
        years = journal_year_stats[journal]
        sorted_years = sorted(years.keys())
        
        # 检查是否有可疑年份（15-25篇，正好一页左右）
        suspicious_years = []
        normal_years = []
        
        for year in sorted_years:
            count = years[year]
            if 15 <= count <= 25:
                suspicious_years.append((year, count))
            else:
                normal_years.append((year, count))
        
        # 判断这个期刊是否可疑
        is_suspicious = len(suspicious_years) > 0 and len(suspicious_years) >= len(normal_years)
        
        # 打印统计
        status = "⚠️ 可疑" if suspicious_years else "✅ 正常"
        year_details = ", ".join([f"{y}:{c}" for y, c in sorted(years.items())])
        print(f"\n{status} {journal}")
        print(f"   年份分布: {year_details}")
        
        if suspicious_years:
            suspicious_journals.append({
                "name": journal,
                "suspicious_years": suspicious_years,
                "normal_years": normal_years,
                "total_years": len(sorted_years)
            })
        else:
            normal_journals.append(journal)
    
    # 汇总
    print("\n" + "=" * 80)
    print("📊 汇总分析")
    print("=" * 80)
    
    print(f"\n✅ 正常期刊数: {len(normal_journals)}")
    print(f"⚠️ 可疑期刊数: {len(suspicious_journals)}")
    
    if suspicious_journals:
        print("\n⚠️ 需要重新检查的期刊（可能翻页失败）:")
        print("-" * 60)
        
        for j in suspicious_journals:
            name = j["name"]
            sus_years = j["suspicious_years"]
            print(f"\n  📖 {name}")
            print(f"     可疑年份 (约20篇，可能只爬了一页):")
            for year, count in sus_years:
                print(f"       - {year}年: {count} 篇")
        
        # 输出需要重新爬取的期刊列表
        print("\n" + "=" * 80)
        print("📝 建议操作")
        print("=" * 80)
        print("\n1. 将以下期刊的状态改回 'partial' 或 'pending':")
        for j in suspicious_journals:
            print(f"   - {j['name']}")
        
        # 生成修复脚本
        generate_fix_script(suspicious_journals)


def analyze_pdf_files():
    """通过实际 PDF 文件统计"""
    pdf_dir = Path("data/pdf")
    if not pdf_dir.exists():
        print("❌ PDF 目录不存在")
        return
    
    print("\n" + "=" * 80)
    print("📁 通过 PDF 文件统计（更准确）")
    print("=" * 80)
    
    journal_year_stats = defaultdict(lambda: defaultdict(int))
    
    for journal_dir in pdf_dir.iterdir():
        if not journal_dir.is_dir():
            continue
        journal_name = journal_dir.name
        
        for year_dir in journal_dir.iterdir():
            if not year_dir.is_dir():
                continue
            year = year_dir.name
            
            pdf_count = len(list(year_dir.glob("*.pdf")))
            if pdf_count > 0:
                journal_year_stats[journal_name][year] = pdf_count
    
    # 打印统计并标记可疑期刊
    suspicious = []
    
    for journal in sorted(journal_year_stats.keys()):
        years = journal_year_stats[journal]
        sorted_years = sorted(years.items())
        
        # 检查可疑年份
        sus_years = [(y, c) for y, c in sorted_years if 15 <= c <= 25]
        
        if sus_years:
            status = "⚠️"
            suspicious.append({
                "name": journal,
                "years": dict(sorted_years),
                "suspicious_years": sus_years
            })
        else:
            status = "✅"
        
        year_str = ", ".join([f"{y}:{c}" for y, c in sorted_years])
        print(f"{status} {journal}: {year_str}")
    
    print(f"\n⚠️ 可疑期刊数: {len(suspicious)}")
    
    return suspicious


def generate_fix_script(suspicious_journals):
    """生成修复脚本"""
    script_content = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动生成的修复脚本
将可疑期刊的状态改回 partial，以便重新爬取
"""
import yaml
from pathlib import Path


def normalize_progress_data(data):
    """规范化 crawl_progress.yaml 数据，避免空节点被解析为 None。"""
    if not isinstance(data, dict):
        data = {}
    else:
        data = dict(data)

    for key in ('completed', 'partial'):
        if not isinstance(data.get(key), dict):
            data[key] = {}

    return data

def fix_journal_status():
    """修复期刊状态"""
    journals_file = Path("journals.yaml")
    progress_file = Path("crawl_progress.yaml")
    
    # 需要修复的期刊
    journals_to_fix = [
'''
    
    for j in suspicious_journals:
        script_content += f'        "{j["name"]}",\n'
    
    script_content += '''    ]
    
    # 更新 journals.yaml
    if journals_file.exists():
        with open(journals_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        fixed_count = 0
        for category in config:
            if isinstance(config[category], list):
                for journal in config[category]:
                    if isinstance(journal, dict) and journal.get('name') in journals_to_fix:
                        old_status = journal.get('status', 'unknown')
                        journal['status'] = 'partial'
                        print(f"✓ {journal['name']}: {old_status} -> partial")
                        fixed_count += 1
        
        with open(journals_file, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        
        print(f"\\n已修复 {fixed_count} 个期刊的状态")
    
    # 清除 crawl_progress.yaml 中这些期刊的 completed 记录
    if progress_file.exists():
        with open(progress_file, 'r', encoding='utf-8') as f:
            progress = normalize_progress_data(yaml.safe_load(f))
        
        for journal_name in journals_to_fix:
            if journal_name in progress['completed']:
                del progress['completed'][journal_name]
                print(f"✓ 清除 {journal_name} 的 completed 记录")
        
        with open(progress_file, 'w', encoding='utf-8') as f:
            yaml.dump(progress, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

if __name__ == "__main__":
    fix_journal_status()
'''
    
    fix_script_path = Path("scripts/fix_incomplete_journals.py")
    with open(fix_script_path, "w", encoding="utf-8") as f:
        f.write(script_content)
    
    print(f"\n📄 已生成修复脚本: {fix_script_path}")
    print("   运行 'python scripts/fix_incomplete_journals.py' 来修复期刊状态")


if __name__ == "__main__":
    print("🔍 检测爬取不完整的期刊\n")
    
    # 分析 meta.json
    analyze_meta()
    
    # 分析实际 PDF 文件
    print("\n")
    analyze_pdf_files()
