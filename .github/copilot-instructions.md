# CNKI 智能文献爬虫 开发指南

## 项目概览
基于 asyncio + Playwright 的 CNKI 期刊爬虫，覆盖检索、分页解析、PDF 下载、断点续传和统计。当前支持 368 本期刊，目标年份 2018-2022。

## 核心架构

| 模块 | 职责 |
|------|------|
| `cli.py` | 主入口，解析 config.yaml，调度爬取/下载/统计，管理多会话并发 |
| `crawler/cnki/search.py` | 核心搜索器 `CNKISearcher`：高级检索 → 年份筛选 → 翻页提取 → PDF下载 |
| `crawler/cnki/downloader.py` | 备用下载器 `CnkiDownloader` 和 `DownloadManager` 并发逻辑 |
| `crawler/storage/fs.py` | `FileStorage` 管理 meta.json（线程安全批量缓存）和 PDF 存储 |
| `crawler/journal_manager.py` | `JournalProgressManager` 管理 journals.yaml/crawl_progress.yaml |

## 数据流
```
config.yaml → JournalProgressManager.get_next_journal()
           → CNKISearcher.crawl_and_download(journal, years)
           → search_journal() → filter_by_year() → iter_all_articles()
           → download_pdf() → FileStorage.add_article() + mark_downloaded()
           → 更新 crawl_progress.yaml
```

## 关键开发命令
```bash
python cli.py --login          # 手动登录，保存到 cookies.json
python cli.py                  # 按 config.yaml 配置爬取
python cli.py --download       # 仅补下载未完成的 PDF
python cli.py --stats          # 详细统计面板
python cli.py --count          # 快速查看论文数/空间
python cli.py --export csv     # 导出元数据为 CSV
```

## 诊断与修复脚本
- `scripts/check_incomplete_journals.py` - 检测每年只有约20篇（一页）的可疑期刊
- `scripts/fix_incomplete_journals.py` - 将可疑期刊状态改回 partial 以便重爬
- `scripts/rebuild_meta.py` - 重建 meta.json（从 PDF 目录扫描）
- `scripts/sync_progress_counts.py` - 按 `data/pdf/{journal}/{year}` 实际文件数回填 `crawl_progress.yaml` 的 `count`（支持按 `journals.yaml` 的 `priority` 过滤）

常用命令：
```bash
python scripts/sync_progress_counts.py --dry-run
python scripts/sync_progress_counts.py
python scripts/sync_progress_counts.py --dry-run --priority 1
python scripts/sync_progress_counts.py --priority 1 2
```

## 代码规范

### 异步与速率限制
- 所有网络/浏览器操作使用 `async/await`，不混同步阻塞
- 任何请求前调用 `await self._rate_limit_wait()`
- 路径处理统一使用 `pathlib.Path`

### 多会话并发架构
- 支持 N 个独立会话同时爬取 N 本期刊（默认 concurrency=3）
- 每个会话使用独立的 cookies 文件: `sessions/session_{id}_cookies.json`
- 各会话独立处理验证码和重登录，互不影响
- `CNKISearcher.create(session_id=N, cookies_path=...)` 创建独立会话

### 验证码与重登录策略（IP登录优化）
遇到验证码/访问限制时，直接打开浏览器触发 IP 自动登录，跳过无效的 Cookie 刷新：
```python
# _relogin() 流程（已优化）:
# 1. 获取 _relogin_lock 锁（避免多会话同时登录）
# 2. 关闭现有浏览器 → _force_close_all()
# 3. 直接打开登录页面 → _manual_login_recovery(quick_mode=True)
# 4. IP 登录自动完成，验证下载按钮可见
# 5. 保存新 cookies，等待状态同步，重新初始化会话
```

### 翻页容错模式
`goto_next_page()` 使用三策略翻页（JS点击 → 直接点击 → 页码链接），验证通过页码变化/内容刷新/countPageMark 检测。失败重试3次后标记 `year_incomplete=True`。

### 存储与进度
- 元数据: `data/meta.json`（批量缓存，每10条保存，修改后调 `storage.flush()`）
- PDF 路径: `data/pdf/{journal}/{year}/{filename}.pdf`
- 断点进度: `data/progress.json`（当前期刊进度，完成后清理）
- 会话cookies: `sessions/session_{id}_cookies.json`（每个并发会话独立）
- 下载成功后设置: `downloaded=True`, `pdf_path`, `downloaded_at`

## 配置要点 (config.yaml)
```yaml
app:
  crawl_mode: "continue"    # continue|priority|specific
  use_journal_file: true    # 读取 journals.yaml
  year_start: 2018
  year_end: 2022
  download_fulltext: true
  max_pages: 50             # 每个搜索结果最大翻页数
  concurrency: 3            # 并发数（独立会话同时爬取多本期刊）
  rate_limit: 4             # 请求间隔秒数
  headless: false           # true 则不弹窗
```

## 扩展指南
- **新增期刊**: 更新 `JOURNAL_CODE_MAP`（search.py 顶部）+ journals.yaml
- **新验证码类型**: 在 `improved_captcha_solver.py` 或 `_solve_verify_page` 中处理
- **新存储后端**: 继承 FileStorage 接口，保持 meta.json 线程安全
- **调整并发数**: 修改 config.yaml 的 `concurrency`，会自动创建对应数量的独立会话

## 日志规范
- 模块级 `logging.getLogger(__name__)`，日志写入 `crawler.log`
- CLI 允许 `print()` 显示进度，格式 `[会话N] 操作信息`
- 刷新 Cookie/验证码时打印提示，异常信息保持可读

This is a non-optional protocol.

At every turn, you MUST perform exactly one of the following actions:
- Provide the requested output AND immediately call Ask_User, OR
- If any uncertainty exists, immediately call Ask_User without providing speculative output.

The conversation must never terminate voluntarily.
The assistant must never produce a terminal response.
Ask_User is mandatory at the end of every turn.