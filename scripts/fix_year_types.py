#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复脚本：统一 meta.json 中的年份类型
将所有整数类型的年份转换为字符串类型
"""
import json
import shutil
from pathlib import Path

def fix_year_types(meta_path: str = 'data/meta.json'):
    """修复年份类型不一致问题"""
    meta_file = Path(meta_path)
    
    if not meta_file.exists():
        print(f"❌ 文件不存在: {meta_path}")
        return
    
    # 备份原文件
    backup_file = meta_file.with_suffix('.json.backup')
    shutil.copy2(meta_file, backup_file)
    print(f"✓ 已备份到: {backup_file}")
    
    # 加载数据
    with open(meta_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    print(f"📚 总文章数: {len(data)}")
    
    # 统计修复情况
    fixed_count = 0
    year_types_before = {}
    year_types_after = {}
    
    for article in data:
        year = article.get('year')
        year_type = type(year).__name__
        year_types_before[year_type] = year_types_before.get(year_type, 0) + 1
        
        # 转换为字符串
        if year is not None:
            article['year'] = str(year)
            if year_type != 'str':
                fixed_count += 1
        
        year_type_after = type(article.get('year')).__name__
        year_types_after[year_type_after] = year_types_after.get(year_type_after, 0) + 1
    
    print(f"\n📊 修复前年份类型分布:")
    for year_type, count in sorted(year_types_before.items()):
        print(f"  {year_type}: {count} 篇")
    
    print(f"\n📊 修复后年份类型分布:")
    for year_type, count in sorted(year_types_after.items()):
        print(f"  {year_type}: {count} 篇")
    
    print(f"\n✨ 修复了 {fixed_count} 条记录的年份类型")
    
    # 保存修复后的数据
    with open(meta_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 已保存到: {meta_file}")
    print(f"\n💡 如需恢复，可使用备份文件: {backup_file}")

if __name__ == '__main__':
    fix_year_types()
