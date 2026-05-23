#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期刊爬取进度管理模块

提供以下功能：
1. 从 journals.yaml 读取期刊列表
2. 跟踪每个期刊每个年份的爬取状态
3. 支持断点续传
4. 生成统计报告
"""
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import json


class JournalProgressManager:
    """期刊爬取进度管理器"""

    _PROGRESS_DICT_KEYS = ("partial", "completed")
    
    def __init__(self, 
                 journals_file: str = "journals.yaml",
                 progress_file: str = "crawl_progress.yaml",
                 data_dir: str = "data"):
        """
        初始化进度管理器
        
        Args:
            journals_file: 期刊列表文件路径
            progress_file: 进度跟踪文件路径
            data_dir: 数据目录路径
        """
        self.journals_file = Path(journals_file)
        self.progress_file = Path(progress_file)
        self.data_dir = Path(data_dir)
        
        # 加载配置
        self.journals_config = self._load_yaml(self.journals_file)
        self.progress_config = self._normalize_progress_config(
            self._load_yaml(self.progress_file)
        )
        
        # 缓存的期刊列表
        self._all_journals: List[Dict] = []
    
    def _load_yaml(self, file_path: Path) -> Dict:
        """加载 YAML 文件"""
        if not file_path.exists():
            return {}
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            print(f"⚠️ 加载 {file_path} 失败: {e}")
            return {}
    
    def _save_yaml(self, file_path: Path, data: Dict):
        """保存 YAML 文件"""
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as e:
            print(f"⚠️ 保存 {file_path} 失败: {e}")

    def _normalize_progress_config(self, config: Optional[Dict]) -> Dict:
        """规范化进度配置，避免空 YAML 节点被解析为 None。"""
        if not isinstance(config, dict):
            config = {}
        else:
            config = dict(config)

        for key in self._PROGRESS_DICT_KEYS:
            if not isinstance(config.get(key), dict):
                config[key] = {}

        last_run = config.get('last_run')
        if last_run is not None and not isinstance(last_run, dict):
            config['last_run'] = {}

        return config
    
    def get_all_journals(self) -> List[Dict]:
        """
        获取所有期刊列表（扁平化）
        
        Returns:
            期刊列表，每个元素包含 name, priority, status, alias 等字段
        """
        if self._all_journals:
            return self._all_journals
        
        journals = []
        
        # 遍历所有分类
        categories = [
            'priority_journals',
            'science_journals',
            'engineering_journals',
            'medical_journals',
            'agriculture_journals',
            'humanities_journals',
            'economics_journals',
            'law_politics_journals',
            'education_sports_journals',
            'media_library_journals',
            'comprehensive_journals',
            'china_science_journals',
            'english_journals',
            'renamed_journals',
            'psychology_journals',
            'abstract_journals',
        ]
        
        for category in categories:
            if category in self.journals_config:
                for journal in self.journals_config[category]:
                    if isinstance(journal, dict) and 'name' in journal:
                        journal['category'] = category
                        journals.append(journal)
        
        self._all_journals = journals
        return journals
    
    def get_pending_journals(self, priority: Optional[int] = None, include_partial: bool = False) -> List[Dict]:
        """
        获取待爬取的期刊列表（仅 status=pending）
        
        Args:
            priority: 可选的优先级筛选（1, 2, 3）
            
        Returns:
            待爬取的期刊列表
        """
        all_journals = self.get_all_journals()
        if include_partial:
            pending = [j for j in all_journals if j.get('status') in ['pending', 'partial']]
        else:
            pending = [j for j in all_journals if j.get('status') == 'pending']
        if priority is not None:
            pending = [j for j in pending if j.get('priority') == priority]
        
        # 按优先级排序
        pending.sort(key=lambda x: x.get('priority', 99))
        
        return pending
    
    def get_unfinished_journals(self, priority: Optional[int] = None) -> List[Dict]:
        """
        获取所有未完成的期刊列表（partial + pending）
        
        优先返回 partial 状态的期刊（已开始但未完成），
        然后返回 pending 状态的期刊（完全未开始）。
        
        Args:
            priority: 可选的优先级筛选（1, 2, 3）
            
        Returns:
            未完成的期刊列表，partial 在前，pending 在后
        """
        all_journals = self.get_all_journals()
        
        # 分别获取 partial 和 pending
        partial = [j for j in all_journals if j.get('status') == 'partial']
        pending = [j for j in all_journals if j.get('status') == 'pending']
        
        # 优先级筛选
        if priority is not None:
            partial = [j for j in partial if j.get('priority') == priority]
            pending = [j for j in pending if j.get('priority') == priority]
        
        # 分别按优先级排序
        partial.sort(key=lambda x: x.get('priority', 99))
        pending.sort(key=lambda x: x.get('priority', 99))
        
        # partial 优先，然后是 pending
        return partial + pending
    
    def get_partial_journals(self) -> List[Dict]:
        """获取部分完成的期刊列表"""
        all_journals = self.get_all_journals()
        return [j for j in all_journals if j.get('status') == 'partial']
    
    def get_completed_journals(self) -> List[Dict]:
        """获取已完成的期刊列表"""
        all_journals = self.get_all_journals()
        return [j for j in all_journals if j.get('status') == 'completed']
    
    def get_year_range(self) -> Tuple[int, int]:
        """获取爬取年份范围"""
        year_range = self.journals_config.get('year_range', {})
        return (
            year_range.get('start', 2018),
            year_range.get('end', 2022)
        )
    
    def get_journal_progress(self, journal_name: str) -> Dict:
        """
        获取单个期刊的爬取进度
        
        Args:
            journal_name: 期刊名称
            
        Returns:
            包含各年份状态的字典
        """
        # 先检查 completed
        if 'completed' in self.progress_config:
            if journal_name in self.progress_config['completed']:
                return self.progress_config['completed'][journal_name]
        
        # 再检查 partial
        if 'partial' in self.progress_config:
            if journal_name in self.progress_config['partial']:
                return self.progress_config['partial'][journal_name]
        
        return {}
    
    def update_journal_progress(self, 
                                journal_name: str, 
                                year: int, 
                                status: str, 
                                count: int = 0,
                                total: int = 0):
        """
        更新期刊爬取进度
        
        Args:
            journal_name: 期刊名称
            year: 年份
            status: 状态 (completed, partial, pending, failed)
            count: 已爬取数量
            total: 总数量（仅 partial 状态需要）
        """
        self.progress_config = self._normalize_progress_config(self.progress_config)
        
        year_str = str(year)
        
        if status == 'completed':
            # 移动到 completed 区域
            if journal_name not in self.progress_config['completed']:
                self.progress_config['completed'][journal_name] = {}
            self.progress_config['completed'][journal_name][year_str] = {
                'status': 'completed',
                'count': count
            }
            
            # 从 partial 中移除
            if journal_name in self.progress_config.get('partial', {}):
                if year_str in self.progress_config['partial'][journal_name]:
                    del self.progress_config['partial'][journal_name][year_str]
                    
        elif status == 'partial':
            if journal_name not in self.progress_config['partial']:
                self.progress_config['partial'][journal_name] = {}
            self.progress_config['partial'][journal_name][year_str] = {
                'status': 'partial',
                'count': count,
                'total': total
            }
        
        # 检查期刊是否所有年份都已完成，更新 journals.yaml 中的状态
        journal_status = self._check_journal_completion(journal_name)
        self._update_journal_status(journal_name, journal_status)
        
        # 保存进度
        self._save_yaml(self.progress_file, self.progress_config)
    
    def _check_journal_completion(self, journal_name: str) -> str:
        """
        检查期刊是否所有年份都已完成
        
        Returns:
            'completed' - 所有年份都完成
            'partial' - 部分年份完成
            'pending' - 未开始
        """
        year_start, year_end = self.get_year_range()
        completed_years = set()
        has_partial = False
        
        # 检查 completed 区域
        if 'completed' in self.progress_config:
            if journal_name in self.progress_config['completed']:
                for year_str, info in self.progress_config['completed'][journal_name].items():
                    if info.get('status') == 'completed':
                        completed_years.add(int(year_str))
        
        # 检查 partial 区域（部分年份可能已完成但记录在 partial 区域）
        if 'partial' in self.progress_config:
            if journal_name in self.progress_config['partial']:
                for year_str, info in self.progress_config['partial'][journal_name].items():
                    if info.get('status') == 'completed':
                        completed_years.add(int(year_str))
                    elif info.get('status') == 'partial':
                        has_partial = True
        
        # 需要完成的所有年份
        required_years = set(range(year_start, year_end + 1))
        
        if completed_years >= required_years:
            return 'completed'
        elif completed_years or has_partial:
            return 'partial'
        else:
            return 'pending'
    
    def _update_journal_status(self, journal_name: str, status: str):
        """更新 journals.yaml 中的期刊状态"""
        updated = False
        for category in self.journals_config:
            if isinstance(self.journals_config[category], list):
                for journal in self.journals_config[category]:
                    if isinstance(journal, dict) and journal.get('name') == journal_name:
                        journal['status'] = status
                        updated = True

        if updated:
            self._save_yaml(self.journals_file, self.journals_config)
    
    def get_last_run_info(self) -> Optional[Dict]:
        """获取上次运行信息"""
        return self.progress_config.get('last_run')
    
    def save_last_run_info(self, journal: str, year: int, page: int, article_index: int):
        """保存本次运行信息"""
        self.progress_config['last_run'] = {
            'timestamp': datetime.now().isoformat(),
            'journal': journal,
            'year': year,
            'page': page,
            'article_index': article_index
        }
        self._save_yaml(self.progress_file, self.progress_config)
    
    def get_next_journal_to_crawl(self) -> Optional[Tuple[str, int]]:
        """
        获取下一个需要爬取的期刊和起始年份
        
        Returns:
            (期刊名称, 起始年份) 或 None
        """
        year_start, year_end = self.get_year_range()
        
        # 1. 先检查上次中断的位置
        last_run = self.get_last_run_info()
        if last_run and last_run.get('journal'):
            journal_name = last_run['journal']
            year = last_run.get('year', year_start)
            return (journal_name, year)
        
        # 2. 检查 partial 状态的期刊
        partial_journals = self.get_partial_journals()
        if partial_journals:
            journal = partial_journals[0]
            progress = self.get_journal_progress(journal['name'])
            
            # 找到第一个未完成的年份
            for year in range(year_start, year_end + 1):
                year_str = str(year)
                if year_str not in progress or progress[year_str].get('status') != 'completed':
                    return (journal['name'], year)
        
        # 3. 获取待爬取的期刊
        pending_journals = self.get_pending_journals()
        if pending_journals:
            return (pending_journals[0]['name'], year_start)
        
        return None
    
    def print_progress_report(self):
        """打印爬取进度报告"""
        all_journals = self.get_all_journals()
        completed = self.get_completed_journals()
        partial = self.get_partial_journals()
        pending = self.get_pending_journals()
        
        print("\n" + "=" * 60)
        print("📊 期刊爬取进度报告")
        print("=" * 60)
        
        print(f"\n📚 总期刊数: {len(all_journals)}")
        print(f"✅ 已完成: {len(completed)}")
        print(f"⏳ 部分完成: {len(partial)}")
        print(f"📝 待爬取: {len(pending)}")
        
        year_start, year_end = self.get_year_range()
        print(f"\n📅 爬取年份范围: {year_start} - {year_end}")
        
        if completed:
            print(f"\n✅ 已完成的期刊:")
            for j in completed:
                print(f"   - {j['name']}")
        
        if partial:
            print(f"\n⏳ 部分完成的期刊:")
            for j in partial:
                progress = self.get_journal_progress(j['name'])
                print(f"   - {j['name']}")
                for year, info in progress.items():
                    if info.get('status') == 'partial':
                        print(f"     {year}: {info.get('count', 0)}/{info.get('total', '?')} 篇")
        
        # 下一个要爬取的
        next_journal = self.get_next_journal_to_crawl()
        if next_journal:
            print(f"\n🎯 下一个爬取目标: {next_journal[0]} ({next_journal[1]}年)")
        else:
            print(f"\n🎉 所有期刊已爬取完成！")
        
        print("=" * 60 + "\n")
    
    def export_journal_list(self, output_file: str = "journal_list.txt"):
        """导出期刊列表为文本文件"""
        all_journals = self.get_all_journals()
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("CNKI 期刊爬取列表\n")
            f.write("=" * 50 + "\n\n")
            
            # 按状态分组
            status_groups = {
                'completed': '已完成',
                'partial': '部分完成',
                'pending': '待爬取'
            }
            
            for status, label in status_groups.items():
                journals = [j for j in all_journals if j.get('status') == status]
                if journals:
                    f.write(f"\n{label} ({len(journals)} 本):\n")
                    f.write("-" * 30 + "\n")
                    for j in journals:
                        f.write(f"  {j['name']}\n")
        
        print(f"✓ 已导出期刊列表到: {output_file}")


def main():
    """测试进度管理器"""
    manager = JournalProgressManager()
    manager.print_progress_report()


if __name__ == '__main__':
    main()
