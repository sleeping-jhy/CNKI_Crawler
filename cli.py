"""
CNKI 智能文献爬虫 - 命令行入口
支持期刊文献批量爬取、下载和统计

用法:
    python cli.py                  # 按配置爬取文献
    python cli.py --login          # 手动登录获取 cookies
    python cli.py --stats          # 查看详细统计面板
    python cli.py --count          # 快速查看论文数量和空间占用
    python cli.py --download       # 仅下载未完成的 PDF
    python cli.py --export csv     # 导出元数据为 CSV
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

# 禁用输出缓冲，确保实时显示
sys.stdout.reconfigure(line_buffering=True)

import yaml
from tqdm import tqdm

from crawler.cnki.auth import CnkiSession
from crawler.cnki.login import LoginManager
from crawler.cnki.search import CNKISearcher
from crawler.cnki.downloader import CnkiDownloader, DownloadManager
from crawler.cnki.cookie_refresher import refresh_cookies_file
from crawler.storage.fs import FileStorage
from crawler.journal_manager import JournalProgressManager

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler("crawler.log", encoding="utf-8")],
)
logger = logging.getLogger(__name__)


def default_config() -> Dict:
    year = datetime.now().year
    return {
        "app": {
            "journal_name": ["物理教师"],
            "year_start": year,
            "year_end": year,
            "download_fulltext": True,
            "max_pages": 50,
            "concurrency": 2,
            "rate_limit": 2.0,
            "output_dir": "data",
            "proxy": "",
            "headless": True,
            "relogin_start_minimized": True,
            "headers": {
                "Referer": "https://www.cnki.net/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            },
            "login_url": "https://www.cnki.net/",
        },
        "storage": {
            "use_mongo": False,
            "mongo_uri": "mongodb://localhost:27017",
            "mongo_db": "cnki",
            "mongo_collection": "papers",
        },
    }


def load_config(config_path: str = "config.yaml") -> Dict:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data or default_config()
    except Exception:
        return default_config()


def print_banner():
    """打印程序 Banner"""
    banner = """
╔═══════════════════════════════════════════════════════════╗
║           CNKI 智能文献爬虫 v1.0                           ║
║           支持期刊检索、详情解析、PDF下载                   ║
╚═══════════════════════════════════════════════════════════╝
    """
    print(banner)


def format_size(size_bytes: int) -> str:
    """将字节数格式化为人类可读的大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_pdf_stats(output_dir: str = "data") -> dict:
    """
    获取PDF文件统计信息

    Returns:
        包含文件数量和总大小的字典
    """
    pdf_dir = Path(output_dir) / "pdf"
    total_size = 0
    total_count = 0

    if pdf_dir.exists():
        for pdf_file in pdf_dir.rglob("*.pdf"):
            total_count += 1
            total_size += pdf_file.stat().st_size

    return {
        "count": total_count,
        "size": total_size,
        "size_formatted": format_size(total_size),
    }


def print_count(output_dir: str = "data"):
    """打印简洁的论文数量和大小统计"""
    pdf_stats = get_pdf_stats(output_dir)

    print(f"\n📚 论文总数: {pdf_stats['count']} 篇")
    print(f"💾 占用空间: {pdf_stats['size_formatted']}")


def print_stats(storage: FileStorage, output_dir: str = "data"):
    """打印统计信息"""
    stats = storage.get_stats()
    pdf_stats = get_pdf_stats(output_dir)

    # 计算进度百分比
    total_pct = (
        (stats["downloaded"] / stats["total"] * 100) if stats["total"] > 0 else 0
    )

    # 获取所有年份并排序
    all_years = sorted(stats["years"].keys(), key=lambda x: str(x))

    # 打印标题
    print("\n")
    print("╔" + "═" * 70 + "╗")
    print("║" + "📊 CNKI 文献爬虫 - 统计面板".center(62) + "║")
    print("╚" + "═" * 70 + "╝")

    # 总览区域
    print("\n┌" + "─" * 40 + "┐")
    print("│  📈 总览" + " " * 31 + "│")
    print("├" + "─" * 40 + "┤")
    print(f"│    📚 文章总数    {stats['total']:>6} 篇            │")
    print(f"│    ✅ 已下载      {stats['downloaded']:>6} 篇 ({total_pct:>5.1f}%)    │")
    print(f"│    ⏳ 待下载      {stats['pending']:>6} 篇            │")
    print(f"│    💾 磁盘占用    {pdf_stats['size_formatted']:>10}          │")
    print("└" + "─" * 40 + "┘")

    # 按期刊分别显示年份分布
    if stats.get("journal_years"):
        # 按下载数量排序期刊
        sorted_journals = sorted(
            stats["journals"].items(), key=lambda x: x[1]["downloaded"], reverse=True
        )

        for journal, journal_data in sorted_journals:
            j_total = journal_data["total"]
            j_downloaded = journal_data["downloaded"]
            j_pct = (j_downloaded / j_total * 100) if j_total > 0 else 0

            # 期刊标题
            print(f"\n┌{'─' * 70}┐")
            print(f"│  📖 {journal}")
            print(f"│     总计: {j_total} 篇, 已下载: {j_downloaded} 篇 ({j_pct:.1f}%)")
            print(f"├{'─' * 70}┤")

            # 该期刊的年份数据
            journal_year_data = stats["journal_years"].get(journal, {})
            sorted_years = sorted(journal_year_data.keys(), key=lambda x: str(x))

            # 找出该期刊最大的文章数，用于计算条形图比例
            max_count = max((d["total"] for d in journal_year_data.values()), default=1)

            for year in sorted_years:
                year_data = journal_year_data[year]
                y_total = year_data["total"]
                y_downloaded = year_data["downloaded"]
                y_pct = (y_downloaded / y_total * 100) if y_total > 0 else 0

                # 条形图（基于该期刊内的最大值）
                bar_width = 30
                filled = int(bar_width * y_total / max_count) if max_count > 0 else 0
                bar = "█" * filled + "░" * (bar_width - filled)

                # 状态指示
                status = "✅" if y_downloaded == y_total else "⏳"

                print(f"│  {year}  {bar}  {y_downloaded:>4}/{y_total:<4} {status}")

            print(f"└{'─' * 70}┘")

    # 汇总：按年份统计
    if stats["years"]:
        print(f"\n┌{'─' * 70}┐")
        print(f"│  📅 年份汇总（所有期刊）")
        print(f"├{'─' * 70}┤")

        # 找出最大的文章数
        max_count = max((d["total"] for d in stats["years"].values()), default=1)
        sorted_years = sorted(stats["years"].items(), key=lambda x: str(x[0]))

        for year, data in sorted_years:
            y_total = data["total"]
            y_downloaded = data["downloaded"]
            y_pct = (y_downloaded / y_total * 100) if y_total > 0 else 0

            # 条形图
            bar_width = 35
            filled = int(bar_width * y_total / max_count) if max_count > 0 else 0
            bar = "█" * filled + "░" * (bar_width - filled)

            status = "✅" if y_downloaded == y_total else "⏳"

            print(f"│  {year}  {bar}  {y_downloaded:>5}/{y_total:<5} {status}")

        print(f"└{'─' * 70}┘")

    print()


async def fetch_articles(
    config: Dict, session: CnkiSession, storage: FileStorage
) -> Dict:
    """
    获取文章列表并下载PDF（使用新的crawl_and_download方法）

    Args:
        config: 配置字典
        session: CNKI 会话
        storage: 存储管理器

    Returns:
        统计信息字典
    """
    app_config = config.get("app", {})

    def _normalize_journal_list(value):
        if not value:
            return []
        if isinstance(value, str):
            value = [value]
        normalized = []
        for name in value:
            if isinstance(name, str):
                cleaned = name.strip()
                if cleaned:
                    normalized.append(cleaned)
        return normalized

    def _dedup_preserve_order(items):
        seen = set()
        ordered = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                ordered.append(item)
        return ordered

    def _summary_for_unfinished(journal_file, mode, entries):
        partial_count = sum(1 for j in entries if j.get("status") == "partial")
        pending_count = sum(1 for j in entries if j.get("status") == "pending")
        return [
            f"\n📂 从 {journal_file} 读取期刊（{mode} 模式）",
            f"   📌 partial（继续爬取）: {partial_count} 本",
            f"   ⏳ pending（待开始）: {pending_count} 本",
        ]

    use_journal_file = app_config.get("use_journal_file", False)
    crawl_mode = app_config.get("crawl_mode", "specific")
    journals: List[str] = []
    selection_summary: List[str] = []

    if use_journal_file:
        journal_file = app_config.get("journal_file", "journals.yaml")
        progress_file = "crawl_progress.yaml"
        output_dir = app_config.get("output_dir", "data")

        manager = JournalProgressManager(
            journals_file=journal_file,
            progress_file=progress_file,
            data_dir=output_dir,
        )

        if crawl_mode in {"continue", "priority"}:
            unfinished_journals = manager.get_unfinished_journals()
            if crawl_mode == "priority":
                unfinished_journals = sorted(
                    unfinished_journals,
                    key=lambda j: (j.get("priority", 99), j.get("status") != "partial"),
                )
            journals = _dedup_preserve_order(
                [j.get("name") for j in unfinished_journals if j.get("name")]
            )
            selection_summary = _summary_for_unfinished(
                journal_file, crawl_mode, unfinished_journals
            )
        else:
            journals = _normalize_journal_list(app_config.get("journal_name"))
            selection_summary = [
                f"\n📂 从 {journal_file} 读取期刊（specific 模式）",
                f"   使用 config.yaml 中指定的 {len(journals)} 本期刊",
            ]
    else:
        journals = _normalize_journal_list(app_config.get("journal_name"))
        if journals:
            preview = ", ".join(journals[:5])
            suffix = "..." if len(journals) > 5 else ""
            selection_summary = [
                "\n📂 使用 config.yaml 中的期刊列表",
                f"   共 {len(journals)} 本: {preview}{suffix}",
            ]
        else:
            selection_summary = ["\n⚠️ 配置中缺少待爬取的期刊"]

    if selection_summary:
        for line in selection_summary:
            print(line)

    if not journals:
        print("\n⚠️ 未找到待爬取的期刊！")
        print("   请检查以下配置:")
        print("   1. 如果使用 journals.yaml: 确保有 status: pending 的期刊")
        print("   2. 如果直接指定: 请在 config.yaml 中设置 journal_name")
        return {
            "total": 0,
            "new": 0,
            "skipped": 0,
            "downloaded": 0,
            "download_failed": 0,
        }

    # 时间范围配置
    year_start = app_config.get("year_start", 2020)
    year_end = app_config.get("year_end", 2023)

    # 爬取配置
    rate_limit = app_config.get("rate_limit", 2.0)
    headless = app_config.get("headless", False)
    max_pages = app_config.get("max_pages", 50)
    output_dir = app_config.get("output_dir", "data")
    download_fulltext = app_config.get("download_fulltext", True)
    relogin_start_minimized = app_config.get("relogin_start_minimized", True)

    # 显示配置信息
    concurrency = app_config.get("concurrency", 3)
    print("\n📋 爬取配置:")
    print(
        f"   期刊: {', '.join(journals[:5])}{'...' if len(journals) > 5 else ''} (共{len(journals)}本)"
    )
    print(f"   年份: {year_start} - {year_end}")
    print(f"   下载PDF: {'是' if download_fulltext else '否'}")
    print(f"   无头模式: {'是' if headless else '否'}")
    print(f"   并发数: {concurrency} (独立会话)")
    print(f"   请求间隔: {rate_limit}秒")
    print(f"   最大页数: {max_pages}")
    print(f"   重登录后最小化: {'是' if relogin_start_minimized else '否'}")

    total_stats = {
        "total": 0,
        "new": 0,
        "skipped": 0,
        "downloaded": 0,
        "download_failed": 0,
    }

    # 初始化独立会话的cookies文件
    sessions_dir = Path("sessions")
    sessions_dir.mkdir(exist_ok=True)

    # 确保每个会话都有自己的cookies副本
    base_cookies_path = Path("cookies.json")
    if base_cookies_path.exists():
        import shutil

        for i in range(concurrency):
            session_cookies = sessions_dir / f"session_{i}_cookies.json"
            # 始终从主cookies复制，确保最新
            shutil.copy(base_cookies_path, session_cookies)
        print(f"   📁 已初始化 {concurrency} 个独立会话")

    # 用于分配会话ID的计数器
    session_counter = [0]
    session_lock = asyncio.Lock()

    async def get_next_session_id():
        async with session_lock:
            sid = session_counter[0] % concurrency
            session_counter[0] += 1
            return sid

    async def crawl_journal_task(journal: str, sem: asyncio.Semaphore):
        # 获取独立的会话ID
        session_id = await get_next_session_id()
        session_cookies_path = str(sessions_dir / f"session_{session_id}_cookies.json")

        async with sem:
            print(
                f"\n📖 [会话{session_id}] 正在爬取期刊: {journal} ({year_start}-{year_end})"
            )
            searcher = CNKISearcher.create(
                rate_limit=rate_limit,
                max_pages=max_pages,
                cookies_path=session_cookies_path,  # 使用独立的cookies文件
                username=app_config.get("username"),
                password=app_config.get("password"),
                debug_screenshots=app_config.get("debug_screenshots", False),
                session_id=session_id,
            )
            stats = await searcher.crawl_and_download(
                journal_name=journal,
                year_start=year_start,
                year_end=year_end,
                output_dir=output_dir,
                storage=storage,
                headless=headless,
                download_pdf=download_fulltext,
                rate_limit=rate_limit,
                relogin_start_minimized=relogin_start_minimized,
            )
            print(
                f"  ✅ [会话{session_id}] {journal} 完成: 共 {stats['total']} 篇, 新增 {stats['new']} 篇, 跳过 {stats['skipped']} 篇"
            )
            if download_fulltext:
                print(
                    f"     下载成功 {stats['downloaded']} 篇, 失败 {stats['download_failed']} 篇"
                )
            return stats

    sem = asyncio.Semaphore(concurrency)
    tasks = [crawl_journal_task(journal, sem) for journal in journals]
    results = await asyncio.gather(*tasks)
    for stats in results:
        for key in total_stats:
            total_stats[key] += stats.get(key, 0)

    return total_stats


async def download_articles(
    config: Dict,
    session: CnkiSession,
    storage: FileStorage,
    articles: List[Dict] = None,
):
    """
    下载文章 PDF

    Args:
        config: 配置字典
        session: CNKI 会话
        storage: 存储管理器
        articles: 要下载的文章列表（可选，默认下载所有未完成的）
    """
    app_config = config.get("app", {})

    if not app_config.get("download_fulltext", True):
        print("⚠️ 配置中未启用 PDF 下载")
        return

    output_dir = app_config.get("output_dir", "data")
    headless = app_config.get("headless", False)
    concurrency = app_config.get("concurrency", 4)
    rate_limit = app_config.get("rate_limit", 2.0)
    proxy = app_config.get("proxy", "")
    year_start = int(app_config.get("year_start", 2020))
    year_end = int(app_config.get("year_end", 2023))

    # 获取未下载的文章
    if articles is None:
        articles = storage.get_undownloaded_articles()
    else:
        articles = [a for a in articles if not a.get("downloaded", False)]

    # 仅下载配置年份范围内的文章
    def in_year_range(article: Dict) -> bool:
        year_text = str(article.get("year", "")).strip()
        match = re.search(r"(19|20)\d{2}", year_text)
        if not match:
            return False
        article_year = int(match.group(0))
        return year_start <= article_year <= year_end

    before_filter = len(articles)
    articles = [a for a in articles if in_year_range(a)]
    filtered_out = before_filter - len(articles)
    if filtered_out > 0:
        print(
            f"ℹ️ 已按年份范围过滤待下载文章: 跳过 {filtered_out} 篇（范围 {year_start}-{year_end}）"
        )

    if not articles:
        print(f"✅ {year_start}-{year_end} 年范围内无待下载文章")
        return

    print(f"\n⬇️ 开始下载 PDF: {len(articles)} 篇待下载")

    # 创建下载器
    downloader = CnkiDownloader(
        headers=session.get_headers(),
        cookies=session.get_cookies(),
        rate_limit=rate_limit,
        proxy=proxy if proxy else None,
        output_dir=output_dir,
    )

    download_manager = DownloadManager(
        downloader=downloader, concurrency=concurrency, max_retries=3
    )

    # 按期刊分组下载
    journals = {}
    for article in articles:
        journal = article.get("journal", "unknown")
        if journal not in journals:
            journals[journal] = []
        journals[journal].append(article)

    total_success = 0
    total_failed = 0

    try:
        await downloader.init_browser(headless=headless)

        for journal, journal_articles in journals.items():
            print(f"\n📖 下载 {journal}: {len(journal_articles)} 篇")

            # 使用进度条
            with tqdm(total=len(journal_articles), desc=f"  {journal}") as pbar:

                def update_progress(stats):
                    pbar.update(1)
                    pbar.set_postfix(
                        {
                            "成功": stats["success"],
                            "失败": stats["failed"],
                            "跳过": stats["skipped"],
                        }
                    )

                stats = await download_manager.batch_download(
                    articles=journal_articles,
                    journal_name=journal,
                    use_browser=True,
                    headless=headless,
                    progress_callback=update_progress,
                )

            # 更新存储中的下载状态
            for article in journal_articles:
                pdf_path = downloader.get_pdf_path(article, journal)
                if pdf_path.exists():
                    storage.mark_downloaded(article.get("id"), str(pdf_path))

            total_success += stats["success"]
            total_failed += stats["failed"]

    finally:
        await downloader.close_browser()

    print(f"\n✅ 下载完成: 成功 {total_success}, 失败 {total_failed}")


async def do_login(config: Dict) -> bool:
    """
    执行手动登录

    Args:
        config: 配置字典

    Returns:
        是否成功
    """
    app_config = config.get("app", {})
    login_url = app_config.get("login_url", "https://www.cnki.net/")
    cookies_path = "cookies.json"
    user_agent = app_config.get("headers", {}).get("User-Agent")

    print("\n🔐 手动登录模式")
    print("=" * 60)
    print("  1. 浏览器将自动打开 CNKI 网站")
    print("  2. 请手动登录您的账号（或通过校园网IP认证）")
    print("  3. 登录成功后，程序会自动保存 cookies")
    print("  4. 超时时间: 5 分钟")
    print("=" * 60)

    manager = LoginManager(
        site_url=login_url, cookies_path=cookies_path, user_agent=user_agent
    )

    success = await manager.manual_login(headless=False, timeout_seconds=300)

    if success:
        print("\n✅ 登录成功！Cookies 已保存到 cookies.json")
    else:
        print("\n❌ 登录失败或超时")

    return success


async def auto_refresh_cookies(config: Dict) -> bool:
    """
    自动刷新 Cookie（通过打开浏览器访问知网主页重新获取 Cookie）

    Args:
        config: 配置字典

    Returns:
        是否成功
    """
    app_config = config.get("app", {})
    cookies_path = "cookies.json"

    print("\n🔄 自动刷新 Cookie...")

    user_agent = app_config.get("headers", {}).get("User-Agent")

    try:
        refreshed = await refresh_cookies_file(
            cookies_path=cookies_path,
            user_agent=user_agent,
            headless=True,
            timeout_seconds=60,
            overwrite=True,
        )

        if refreshed:
            print("   ✅ Cookie 已刷新")
        else:
            print("   ⚠️ 未能刷新 Cookie，请先手动登录: python cli.py --login")

        return refreshed
    except Exception as e:
        logger.error(f"自动刷新 Cookie 失败: {e}")
        print(f"   ❌ 刷新失败: {e}")
        return False


def do_export(storage: FileStorage, format: str, output_path: str = None):
    """
    导出元数据

    Args:
        storage: 存储管理器
        format: 导出格式 (csv/json)
        output_path: 输出路径
    """
    if format.lower() == "csv":
        output_path = output_path or "export.csv"
        storage.export_to_csv(output_path)
        print(f"✅ 已导出为 CSV: {output_path}")
    elif format.lower() == "json":
        output_path = output_path or "export.json"
        articles = storage.get_all_articles()
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        print(f"✅ 已导出为 JSON: {output_path}")
    else:
        print(f"❌ 不支持的格式: {format}")


async def main_async(args):
    """异步主函数"""
    # 加载配置（支持命令行覆盖）
    if getattr(args, "_config_override", None):
        config = args._config_override
    else:
        config = load_config(args.config)
    app_config = config.get("app", {})
    output_dir = app_config.get("output_dir", "data")

    # 初始化存储
    storage = FileStorage(output_dir)

    # 初始化会话
    session = CnkiSession(
        cookies_path="cookies.json", headers_override=app_config.get("headers", {})
    ).load()

    # 登录模式
    if args.login:
        await do_login(config)
        return

    # 快速计数模式
    if args.count:
        print_count(output_dir)
        return

    # 详细统计模式
    if args.stats:
        print_stats(storage, output_dir)
        return

    # 导出模式
    if args.export:
        do_export(storage, args.export, args.output)
        return

    # 仅下载模式
    if args.download:
        await download_articles(config, session, storage)
        return

    # 正常爬取模式
    print_banner()

    # 检查 cookies
    if not session.get_cookies():
        print("⚠️ 未找到有效的 cookies，请先登录")
        print("  运行: python cli.py --login")
        return

    # 🔄 启动时自动刷新 Cookie
    await auto_refresh_cookies(config)

    # 重新加载刷新后的 cookies
    session = CnkiSession(
        cookies_path="cookies.json", headers_override=app_config.get("headers", {})
    ).load()

    # 执行爬取（包含下载）
    stats = await fetch_articles(config, session, storage)

    print(f"\n📊 爬取完成统计:")
    print(f"   总文章数: {stats['total']}")
    print(f"   新增文章: {stats['new']}")
    print(f"   跳过已有: {stats['skipped']}")
    if app_config.get("download_fulltext", True):
        print(f"   下载成功: {stats['downloaded']}")
        print(f"   下载失败: {stats['download_failed']}")

    # 显示最终统计
    print_stats(storage, app_config.get("output_dir", "data"))

    # 保存会话
    session.save()


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="CNKI 智能文献爬虫",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python cli.py                  # 按配置爬取文献
  python cli.py --login          # 手动登录获取 cookies
  python cli.py --stats          # 查看统计信息
  python cli.py --download       # 仅下载未完成的 PDF
  python cli.py --export csv     # 导出元数据为 CSV
  python cli.py --export json    # 导出元数据为 JSON
  python cli.py --list-journals  # 列出支持的期刊
        """,
    )

    parser.add_argument(
        "--config", "-c", default="config.yaml", help="配置文件路径 (默认: config.yaml)"
    )
    parser.add_argument(
        "--login",
        "-l",
        action="store_true",
        help="手动登录模式，打开浏览器登录并保存 cookies",
    )
    parser.add_argument(
        "--stats", "-s", action="store_true", help="显示详细统计信息面板"
    )
    parser.add_argument(
        "--count", action="store_true", help="快速显示论文总数和占用空间"
    )
    parser.add_argument(
        "--download", "-d", action="store_true", help="仅下载模式，下载所有未完成的 PDF"
    )
    parser.add_argument(
        "--export", "-e", choices=["csv", "json"], help="导出元数据为指定格式"
    )
    parser.add_argument("--output", "-o", help="导出文件路径")
    parser.add_argument(
        "--list-journals", action="store_true", help="列出支持的期刊列表"
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="关闭无头模式，显示浏览器窗口以便观察",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="仅爬取元数据，不下载 PDF"
    )
    parser.add_argument(
        "--debug-screenshots",
        action="store_true",
        help="启用调试截图，在关键步骤保存页面截图",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细日志")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    # 命令行开关覆盖配置
    try:
        cfg = load_config(args.config)
        app = cfg.setdefault("app", {})
        if args.no_headless:
            app["headless"] = False
        if args.dry_run:
            app["download_fulltext"] = False
        if args.debug_screenshots:
            app["debug_screenshots"] = True
        args._config_override = cfg
    except Exception:
        args._config_override = None

    # 列出支持的期刊
    if args.list_journals:
        from crawler.cnki.search import CNKISearcher

        journals = CNKISearcher.list_supported_journals()
        print("\n📚 支持的期刊列表:")
        print("=" * 40)
        for j in sorted(journals):
            print(f"  • {j}")
        print("=" * 40)
        print(f"共 {len(journals)} 本期刊")
        return

    # 运行异步主函数
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断")
    except Exception as e:
        logger.exception(f"程序错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
