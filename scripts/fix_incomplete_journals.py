#!/usr/bin/env python3
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
        "中国图书馆学报",
        "中国社会科学",
        "体育科学",
        "兽类学报",
        "分析化学",
        "古生物学报",
        "地球化学",
        "地球物理学报",
        "地理学报",
        "地理研究",
        "地理科学",
        "地质学报",
        "地质科学",
        "地震学报",
        "外国文学评论",
        "天文学报",
        "岩石学报",
        "应用生态学报",
        "微生物学报",
        "政治学研究",
        "新闻与传播研究",
        "无机化学学报",
        "昆虫学报",
        "有机化学",
        "植物保护学报",
        "植物生态学报",
        "植物病理学报",
        "植物营养与肥料学报",
        "水生生物学报",
        "法学研究",
        "物理化学学报",
        "物理学报",
        "生态学报",
        "生物多样性",
        "生物工程学报",
        "生理学报",
        "病毒学报",
        "矿物学报",
        "社会学研究",
        "管理世界",
        "考古学报",
        "菌物学报",
        "遗传",
        "遗传学报",
        "马克思主义研究",
        "高等学校化学学报",
    ]
    
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
        
        print(f"\n已修复 {fixed_count} 个期刊的状态")
    
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
