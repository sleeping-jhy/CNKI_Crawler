#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根据 data/pdf 目录中的实际 PDF 文件数量，回写 crawl_progress.yaml 中的 count 字段。

默认会直接写回文件；可使用 --dry-run 仅预览。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

import yaml


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROGRESS_FILE = ROOT / "crawl_progress_2018-2022.yaml"
DEFAULT_PDF_ROOT = Path(r"E:\data\pdf")
DEFAULT_JOURNALS_FILE = ROOT / "journals.yaml"


def normalize_progress_data(data: Dict) -> Dict:
    """规范化进度结构，避免空节点被解析为 None。"""
    if not isinstance(data, dict):
        data = {}
    else:
        data = dict(data)

    for key in ("partial", "completed"):
        if not isinstance(data.get(key), dict):
            data[key] = {}

    return data


def load_progress(progress_file: Path) -> Dict:
    if not progress_file.exists():
        raise FileNotFoundError(f"进度文件不存在: {progress_file}")

    with open(progress_file, "r", encoding="utf-8") as f:
        return normalize_progress_data(yaml.safe_load(f) or {})


def load_journals_by_priority(journals_file: Path, priorities: Set[int]) -> Set[str]:
    """从 journals.yaml 中加载指定 priority 的期刊名称集合。"""
    if not journals_file.exists():
        raise FileNotFoundError(f"期刊文件不存在: {journals_file}")

    with open(journals_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    allowed: Set[str] = set()
    if not isinstance(data, dict):
        return allowed

    for key, value in data.items():
        if not key.endswith("_journals") or not isinstance(value, list):
            continue

        for item in value:
            if not isinstance(item, dict):
                continue

            name = item.get("name")
            priority = item.get("priority")
            if isinstance(name, str) and priority in priorities:
                allowed.add(name)

    return allowed


def count_pdf_files(pdf_root: Path, journal: str, year: str) -> int:
    year_dir = pdf_root / journal / str(year)
    if not year_dir.exists() or not year_dir.is_dir():
        return 0

    return sum(1 for _ in year_dir.glob("*.pdf"))


def _is_year_in_range(year: str, year_start: Optional[int], year_end: Optional[int]) -> bool:
    """判断年份是否落在指定区间；非数字年份在指定区间时跳过。"""
    if year_start is None and year_end is None:
        return True

    if not str(year).isdigit():
        return False

    year_int = int(year)
    if year_start is not None and year_int < year_start:
        return False
    if year_end is not None and year_int > year_end:
        return False
    return True


def sync_counts(
    progress_data: Dict,
    pdf_root: Path,
    set_missing_zero: bool = False,
    allowed_journals: Optional[Set[str]] = None,
    year_start: Optional[int] = None,
    year_end: Optional[int] = None,
) -> Tuple[int, int, int, int]:
    """
    返回值：
    - scanned_entries: 扫描到的年份条目数
    - changed_entries: 实际修改的条目数
    - missing_dirs: 缺失目录条目数
    - filtered_entries: 按 priority 过滤掉的条目数
    """
    scanned_entries = 0
    changed_entries = 0
    missing_dirs = 0
    filtered_entries = 0

    for section in ("completed", "partial"):
        journals = progress_data.get(section, {})
        if not isinstance(journals, dict):
            continue

        for journal_name, year_map in journals.items():
            if not isinstance(year_map, dict):
                continue

            if allowed_journals is not None and journal_name not in allowed_journals:
                filtered_entries += sum(1 for info in year_map.values() if isinstance(info, dict))
                continue

            for year, info in year_map.items():
                if not isinstance(info, dict):
                    continue

                if not _is_year_in_range(str(year), year_start, year_end):
                    continue

                scanned_entries += 1
                year_dir = pdf_root / journal_name / str(year)
                year_count = count_pdf_files(pdf_root, journal_name, str(year))

                if not year_dir.exists() and not set_missing_zero:
                    missing_dirs += 1
                    continue

                old_count = info.get("count", 0)
                if old_count != year_count:
                    info["count"] = year_count
                    changed_entries += 1
                    print(
                        f"[{section}] {journal_name} {year}: "
                        f"count {old_count} -> {year_count}"
                    )

    return scanned_entries, changed_entries, missing_dirs, filtered_entries


def save_progress(progress_file: Path, data: Dict) -> None:
    with open(progress_file, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="根据 PDF 文件数量同步 crawl_progress.yaml 中的 count 字段"
    )
    parser.add_argument(
        "--progress-file",
        type=Path,
        default=DEFAULT_PROGRESS_FILE,
        help=f"进度文件路径，默认: {DEFAULT_PROGRESS_FILE}",
    )
    parser.add_argument(
        "--pdf-root",
        type=Path,
        default=DEFAULT_PDF_ROOT,
        help=f"PDF 根目录，默认: {DEFAULT_PDF_ROOT}",
    )
    parser.add_argument(
        "--journals-file",
        type=Path,
        default=DEFAULT_JOURNALS_FILE,
        help=f"期刊配置文件路径，默认: {DEFAULT_JOURNALS_FILE}",
    )
    parser.add_argument(
        "--priority",
        type=int,
        nargs="+",
        help="仅同步指定 priority 的期刊，如: --priority 1 或 --priority 1 2",
    )
    parser.add_argument(
        "--year-start",
        type=int,
        help="仅同步该年份及之后的数据（可与 --year-end 组合）",
    )
    parser.add_argument(
        "--year-end",
        type=int,
        help="仅同步该年份及之前的数据（可与 --year-start 组合）",
    )
    parser.add_argument(
        "--set-missing-zero",
        action="store_true",
        help="当期刊/年份目录不存在时，也将 count 写为 0（默认跳过缺失目录）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅预览变更，不写回文件",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if (
        args.year_start is not None
        and args.year_end is not None
        and args.year_start > args.year_end
    ):
        raise ValueError("--year-start 不能大于 --year-end")

    progress_file = args.progress_file
    pdf_root = args.pdf_root
    allowed_journals: Optional[Set[str]] = None

    if args.priority:
        priorities = set(args.priority)
        allowed_journals = load_journals_by_priority(args.journals_file, priorities)
        print(f"按 priority 过滤: {sorted(priorities)}")
        print(f"匹配到期刊数: {len(allowed_journals)}")

    if args.year_start is not None or args.year_end is not None:
        print(
            "按年份区间过滤: "
            f"{args.year_start if args.year_start is not None else '-inf'}"
            f" ~ {args.year_end if args.year_end is not None else '+inf'}"
        )

    progress_data = load_progress(progress_file)

    scanned_entries, changed_entries, missing_dirs, filtered_entries = sync_counts(
        progress_data,
        pdf_root,
        set_missing_zero=args.set_missing_zero,
        allowed_journals=allowed_journals,
        year_start=args.year_start,
        year_end=args.year_end,
    )

    print("\n同步结果:")
    print(f"- 扫描条目数: {scanned_entries}")
    print(f"- 修改条目数: {changed_entries}")
    print(f"- 缺失目录条目数: {missing_dirs}")
    print(f"- 过滤条目数: {filtered_entries}")

    if args.dry_run:
        print("\n已启用 dry-run，未写入文件。")
        return

    if changed_entries == 0:
        print("\n无变化，不写入。")
        return

    save_progress(progress_file, progress_data)
    print(f"\n已写回: {progress_file}")


if __name__ == "__main__":
    main()
