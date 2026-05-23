#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
项目清理脚本
删除临时文件、缓存文件和调试文件
"""
import os
import shutil
from pathlib import Path

def clean_project(root_dir: str = '.'):
    """
    清理项目中的临时文件
    
    Args:
        root_dir: 项目根目录
    """
    root = Path(root_dir).resolve()
    
    # 定义要删除的模式
    patterns_to_remove = [
        # Python 缓存
        '**/__pycache__',
        '**/*.pyc',
        '**/*.pyo',
        '**/*.pyd',
        '**/.pytest_cache',
        
        # 临时文件
        '**/*.tmp',
        '**/*.bak',
        '**/*.backup',
        '**/._*',
        
        # 日志文件
        '**/*.log',
        
        # 调试文件
        '**/debug_*.py',
        '**/debug_*.png',
        '**/debug_*.jpg',
        '**/debug_*.html',
        
        # IDE 配置
        '**/.DS_Store',
        '**/*.swp',
        '**/*.swo',
        
        # AI 助手临时文件
        '**/.claude',
    ]
    
    removed_count = 0
    removed_size = 0
    
    print("🧹 开始清理项目...")
    print(f"📂 项目路径: {root}")
    print()
    
    for pattern in patterns_to_remove:
        for item in root.glob(pattern):
            try:
                # 计算大小
                if item.is_file():
                    size = item.stat().st_size
                    item.unlink()
                    removed_size += size
                    removed_count += 1
                    print(f"  ✓ 删除文件: {item.relative_to(root)}")
                elif item.is_dir():
                    size = sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
                    shutil.rmtree(item)
                    removed_size += size
                    removed_count += 1
                    print(f"  ✓ 删除目录: {item.relative_to(root)}/")
            except Exception as e:
                print(f"  ✗ 无法删除 {item.relative_to(root)}: {e}")
    
    print()
    print(f"✨ 清理完成!")
    print(f"   删除项目: {removed_count} 个")
    print(f"   释放空间: {removed_size / 1024:.2f} KB")

if __name__ == '__main__':
    clean_project()
