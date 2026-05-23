#!/usr/bin/env python3
"""
统计期刊爬取数量的脚本

从 journals.yaml 和 crawl_progress.yaml 文件统计期刊爬取状态。
"""

import yaml
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOURNALS_YAML = ROOT / 'journals.yaml'
CRAWL_PROGRESS_YAML = ROOT / 'crawl_progress.yaml'


def normalize_progress_data(data):
    """规范化 crawl_progress.yaml 数据，避免空节点被解析为 None。"""
    if not isinstance(data, dict):
        data = {}
    else:
        data = dict(data)

    for key in ('completed', 'partial', 'pending', 'statistics'):
        value = data.get(key)
        if key == 'pending':
            if value is None:
                data[key] = {}
        elif key == 'statistics':
            if not isinstance(value, dict):
                data[key] = {}
        elif not isinstance(value, dict):
            data[key] = {}

    return data


def load_yaml(file_path):
    """加载YAML文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"错误: 文件不存在 {file_path}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"错误: YAML解析失败 {file_path}: {e}")
        sys.exit(1)


def count_journals_from_journals_yaml(data):
    """从journals.yaml统计期刊状态"""
    if not data:
        return 0, 0, 0, 0

    total = 0
    completed = 0
    partial = 0
    pending = 0

    # 期刊数据在多个类别下，动态获取所有以 _journals 结尾的键
    categories = [key for key in data.keys() if key.endswith('_journals')]

    for category in categories:
        journals = data.get(category, [])
        total += len(journals)
        for journal in journals:
            status = journal.get('status', 'pending')
            if status == 'completed':
                completed += 1
            elif status == 'partial':
                partial += 1
            else:
                pending += 1

    return total, completed, partial, pending


def count_journals_from_crawl_progress(data):
    """从crawl_progress.yaml统计实际爬取进度"""
    data = normalize_progress_data(data)
    if not data:
        return 0, 0, 0

    # 统计completed部分的期刊
    completed_journals = set()
    completed_data = data.get('completed', {})
    if completed_data:
        completed_journals.update(completed_data.keys())

    # 统计partial部分的期刊
    partial_journals = set()
    partial_data = data.get('partial', {})
    if partial_data:
        partial_journals.update(partial_data.keys())

    # 统计pending部分的期刊（如果有）
    pending_journals = set()
    pending_data = data.get('pending', {})
    if pending_data:
        # pending部分可能是列表或字典
        if isinstance(pending_data, dict):
            pending_journals.update(pending_data.keys())
        elif isinstance(pending_data, list):
            for item in pending_data:
                if isinstance(item, dict) and 'name' in item:
                    pending_journals.add(item['name'])

    completed_count = len(completed_journals)
    partial_count = len(partial_journals)
    pending_count = len(pending_journals)

    return completed_count, partial_count, pending_count


def parse_args():
    """解析命令行参数"""
    import argparse
    parser = argparse.ArgumentParser(description='统计期刊爬取数量')
    parser.add_argument('--simple', action='store_true', help='简单输出，只显示已完成期刊数')
    parser.add_argument('--completed', action='store_true', help='输出已完成期刊数量（数字）')
    parser.add_argument('--source', choices=['journals', 'progress', 'auto'], default='auto',
                       help='数据源: journals=journals.yaml, progress=crawl_progress.yaml, auto=自动选择')
    return parser.parse_args()


def main():
    """主函数"""
    args = parse_args()

    # 从journals.yaml统计
    journals_data = load_yaml(JOURNALS_YAML)
    total, completed, partial, pending = count_journals_from_journals_yaml(journals_data)

    # 从crawl_progress.yaml统计实际爬取进度
    progress_data = normalize_progress_data(load_yaml(CRAWL_PROGRESS_YAML))
    completed_actual, partial_actual, pending_actual = count_journals_from_crawl_progress(progress_data)

    # 获取统计信息
    stats = progress_data.get('statistics', {})

    # 决定使用哪个数据源
    if args.source == 'journals':
        final_completed = completed
        final_partial = partial
        final_pending = pending
        final_total = total
    elif args.source == 'progress':
        final_completed = completed_actual
        final_partial = partial_actual
        final_pending = pending_actual
        final_total = stats.get('total_journals', final_completed + final_partial + final_pending)
    else:  # auto
        # 优先使用crawl_progress.yaml的实际爬取数据
        final_completed = completed_actual
        final_partial = partial_actual
        final_pending = pending_actual
        final_total = stats.get('total_journals', total)

    # 简单输出模式
    if args.simple:
        print(f"总期刊: {final_total}, 已完成: {final_completed}, 部分完成: {final_partial}, 待爬取: {final_pending}")
        return

    # 只输出已完成数量模式
    if args.completed:
        print(final_completed)
        return

    # 完整输出模式
    print("正在统计期刊爬取数量...")
    print("=" * 50)

    print("基于 journals.yaml 的统计:")
    print(f"  总期刊数: {total}")
    print(f"  已完成: {completed}")
    print(f"  部分完成: {partial}")
    print(f"  待爬取: {pending}")
    if total > 0:
        print(f"  完成比例: {completed/total*100:.1f}%")

    print("-" * 50)

    print("基于 crawl_progress.yaml 的统计:")
    print(f"  实际已完成爬取的期刊: {completed_actual}")
    print(f"  实际部分完成的期刊: {partial_actual}")
    print(f"  实际待爬取的期刊: {pending_actual}")

    # 显示爬取进度中的统计信息（如果存在）
    if stats:
        print("-" * 50)
        print("爬取进度中的统计信息:")
        print(f"  总期刊数: {stats.get('total_journals', 'N/A')}")
        print(f"  已完成期刊: {stats.get('completed_journals', 'N/A')}")
        print(f"  部分完成期刊: {stats.get('partial_journals', 'N/A')}")
        print(f"  待爬取期刊: {stats.get('pending_journals', 'N/A')}")
        print(f"  已下载文章数: {stats.get('total_articles_downloaded', 'N/A')}")
        print(f"  占用空间: {stats.get('total_size_gb', 'N/A')} GB")
        last_updated = stats.get('last_updated', 'N/A')
        print(f"  最后更新: {last_updated}")

    print("=" * 50)
    print("自动选择数据源统计:")
    print(f"  总期刊数: {final_total}")
    print(f"  已完成: {final_completed}")
    print(f"  部分完成: {final_partial}")
    print(f"  待爬取: {final_pending}")
    if final_total > 0:
        print(f"  总体完成比例: {final_completed/final_total*100:.1f}%")

    print("=" * 50)
    print("提示: 使用 --simple 查看简单统计")
    print("提示: 使用 --completed 只输出已完成期刊数（方便脚本调用）")
    print("提示: 使用 --source journals|progress|auto 选择数据源")
    print("提示: 使用 python cli.py --stats 查看更详细的统计面板")
    print("提示: 使用 python cli.py --count 查看论文数量和空间占用")


if __name__ == '__main__':
    main()