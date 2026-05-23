# 🕷️ CNKI 智能文献爬虫 (CNKI Intelligent Crawler)

> 🚀 一个高效、可配置的中国知网 (CNKI) 期刊文献爬取工具。支持批量检索、元数据解析、PDF 全文下载及数据统计。

![Python](assets/python-badge.svg)
![Playwright](https://img.shields.io/badge/Playwright-Automation-green)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Journals](https://img.shields.io/badge/期刊数-368本-blue)
![Articles](https://img.shields.io/badge/已爬取-6750篇-brightgreen)

## 📖 简介

本项目旨在自动化获取 CNKI 期刊文献数据。通过模拟浏览器行为（Playwright）和异步请求（aiohttp），实现了从检索到下载的全流程自动化。它能够处理复杂的反爬虫机制，支持断点续传，并提供灵活的配置选项，适合学术研究、文献分析等场景。

### 📊 当前爬取进度

| 指标 | 数值 |
|------|------|
| 📚 支持期刊数 | 368 本 |
| ✅ 已完成期刊 | 8 本 |
| ⏳ 进行中 | 1 本 |
| 📝 待爬取 | 359 本 |
| 📄 已下载论文 | 135345 篇 |
| 💾 占用空间 | 8.63 GB |
| 📅 年份范围 | 2018-2022 |

## ✨ 核心功能

- **🔍 批量检索**：支持同时爬取多本期刊，自定义年份和月份范围。
- **📄 全文下载**：自动检测并下载 PDF 全文，智能处理下载链接。
- **⚡ 高效并发**：基于 `asyncio` 和 `aiohttp`，支持自定义并发数和请求间隔。
- **📋 期刊管理**：内置 368 本核心期刊列表，支持按优先级和类别管理。
- **🔄 断点续传**：自动记录爬取进度，中断后可从上次位置继续。
- **🛡️ 智能反爬**：
  - 内置浏览器模拟 (Playwright) 以绕过简单的检测。
  - 支持手动登录获取 Cookie，解决权限问题。
  - 自动检测和处理滑动验证码。
  - 访问受限时自动暂停并提示重新登录。
- **💾 数据存储**：
  - 默认支持本地文件存储（JSON 元数据 + PDF 文件）。
  - 可选 MongoDB 数据库存储。
- **📊 统计与导出**：提供爬取进度统计，支持导出元数据为 CSV 格式。

## 📂 目录结构

```text
.
├── cli.py                  # 🚀 程序主入口
├── config.yaml             # ⚙️ 配置文件
├── journals.yaml           # 📚 期刊列表（368本）
├── crawl_progress.yaml     # 📊 爬取进度跟踪
├── cookies.json            # 🍪 登录凭证（自动生成）
├── requirements.txt        # 📦 依赖列表
├── crawler/                # 核心代码包
│   ├── cnki/               # CNKI 专用模块 (搜索, 下载, 验证码等)
│   ├── storage/            # 存储模块 (文件系统/数据库)
│   └── journal_manager.py  # 期刊进度管理
├── data/                   # 💾 数据输出目录
│   ├── meta.json           # 文献元数据
│   ├── progress.json       # 断点续传进度
│   └── pdf/                # 下载的 PDF 文件 (按期刊/年份分类)
└── scripts/                # 🛠️ 辅助脚本
```

## 📚 支持的期刊列表

项目内置 **368 本** 核心学术期刊，涵盖以下类别：

| 类别 | 数量 | 示例期刊 |
|------|------|----------|
| 人文社科（优先）| 21 | 文艺研究、心理学报、经济研究、哲学研究 |
| 自然科学 | 52 | 物理学报、化学学报、生态学报 |
| 工程技术 | 67 | 计算机学报、自动化学报、机械工程学报 |
| 医学类 | 53 | 中华医学杂志、药学学报 |
| 农业类 | 24 | 中国农业科学、作物学报 |
| 经济管理 | 25 | 管理世界、金融研究、会计研究 |
| 其他类别 | 126 | 教育研究、法学研究、新闻传播等 |

> 📋 完整期刊列表见 [journals.yaml](journals.yaml)

## 🛠️ 安装指南

1.  **克隆项目**
    ```bash
    git clone https://gitee.com/jesonnnn/cnkicrawler_xjx
    cd Crawler
    ```

2.  **安装 Python 依赖**
    建议使用 Conda 或虚拟环境：
    ```bash
    .venv\Scripts\activate
    pip install -r requirements.txt
    ```

3.  **安装 Playwright 浏览器**
    本项目依赖 Playwright 进行页面渲染：
    ```bash
    playwright install chromium
    ```

## ⚙️ 配置说明

修改根目录下的 `config.yaml` 文件以适应你的需求：

```yaml
app:
  # ========== 爬取模式 ==========
  # "continue" - 从上次中断处继续（推荐）
  # "priority" - 按优先级顺序爬取
  # "specific" - 只爬取指定的期刊
  crawl_mode: "continue"
  
  # 使用期刊列表文件（推荐）
  use_journal_file: true
  journal_file: "journals.yaml"
  
  # 或者直接指定期刊（crawl_mode 为 "specific" 时）
  # journal_name:
  #   - "经济研究"
  #   - "哲学研究"
  
  # ========== 时间范围 ==========
  year_start: 2018
  year_end: 2022
  
  # ========== 爬取选项 ==========
  download_fulltext: true  # 是否下载 PDF
  max_pages: 50            # 每个搜索结果最大翻页数
  concurrency: 1           # 并发下载数 (建议 1-2)
  rate_limit: 5.0          # 请求间隔秒数 (防止封禁)
  headless: true           # 是否隐藏浏览器窗口
```

## 🚀 使用方法

### 1. 首次运行 / 登录
如果需要下载全文，通常需要登录权限。运行以下命令启动登录流程：
```bash
python cli.py --login
```
*程序会弹出一个浏览器窗口，请手动在窗口中完成 CNKI 登录。登录成功后关闭窗口，Cookies 将自动保存到 `cookies.json`。*

### 2. 开始爬取
配置好 `config.yaml` 后，直接运行：
```bash
python cli.py
```
*可选参数：*
- `--dry-run`: 仅爬取元数据，不下载 PDF。
- `--no-headless`: 显示浏览器窗口，方便观察爬取过程。

### 3. 查看统计
查看当前爬取进度、论文数量及存储占用：
```bash
python cli.py --stats   # 查看详细统计面板
python cli.py --count   # 快速查看数量和占用
```

### 4. 仅下载 PDF
如果元数据已爬取但 PDF 下载中断，可以使用此命令仅下载未完成的文件：
```bash
python cli.py --download
```

### 5. 导出数据
将爬取的元数据导出为 CSV 格式，方便 Excel 分析：
```bash
python cli.py --export csv
```

### 6. 查看期刊爬取进度
```bash
python -c "from crawler.journal_manager import JournalProgressManager; JournalProgressManager().print_progress_report()"
```

### 7. 其他实用命令
```bash
python cli.py --list-journals  # 列出所有支持的期刊
python scripts/clean_project.py  # 清理临时文件
```

### 8. 同步 crawl_progress.yaml 中的已爬取数量
根据 `data/pdf/期刊名/年份` 目录里的 PDF 文件数量，回写 `crawl_progress.yaml` 中各年份的 `count`：
```bash
python scripts/sync_progress_counts.py --dry-run  # 仅预览，不写入
python scripts/sync_progress_counts.py            # 正式写回
python scripts/sync_progress_counts.py --set-missing-zero  # 缺失目录也写 0
python scripts/sync_progress_counts.py --dry-run --priority 1  # 仅同步 priority=1 的期刊
python scripts/sync_progress_counts.py --priority 1 2  # 同步 priority=1 和 2 的期刊
```

## 🛠️ 辅助脚本

项目在 `scripts/` 目录下提供了一些辅助工具：

| 脚本 | 用途 |
|------|------|
| `rebuild_meta.py` | 根据已下载的 PDF 文件重建 `meta.json` |
| `fix_year_types.py` | 修复元数据中年份类型不一致的问题 |
| `clean_project.py` | 清理项目中的临时文件、缓存和调试文件 |
| `sync_progress_counts.py` | 按 PDF 实际文件数同步 `crawl_progress.yaml` 里的 `count`（支持 `--priority` 过滤） |

```bash
# 示例：重建元数据
python scripts/rebuild_meta.py

# 示例：清理临时文件
python scripts/clean_project.py

# 示例：预览并同步已爬取数量
python scripts/sync_progress_counts.py --dry-run
python scripts/sync_progress_counts.py

# 示例：按 journals.yaml 中 priority 过滤同步
python scripts/sync_progress_counts.py --dry-run --priority 1
python scripts/sync_progress_counts.py --priority 1 2
```

## 🔧 错误处理机制

爬虫内置了智能错误恢复机制，可以自动处理以下情况：

### 滑动拼图验证码
- 自动检测验证码页面并尝试通过图像识别算法计算缺口位置
- 模拟人类滑动轨迹完成验证
- 自动求解失败时会等待用户手动完成（90秒超时）

### 访问次数过多 / 会话过期
当检测到 CNKI 提示"访问次数过多"、"请求频繁"或"会话过期"时：

```
下载失败 → 检测错误类型 → 需要重登录?
                            ↓ 是
                      尝试自动刷新 Cookie
                            ↓ 失败
                      检测到访问限制?
                            ↓ 是
                    ┌───────────────────┐
                    │ 弹出浏览器窗口    │
                    │ 请手动完成登录    │
                    │ (最长等待180秒)   │
                    └───────────────────┘
                            ↓
                      用户完成登录
                            ↓
                    自动保存新 Cookies
                            ↓
                    重新初始化爬虫会话
                            ↓
                        继续爬取
```

### 连续失败保护
- 连续下载失败 5 次后自动触发重新登录流程
- 下载超时自动重试并尝试恢复会话

> 💡 **提示**：如果频繁触发访问限制，建议在 `config.yaml` 中增大 `rate_limit` 值（如设为 3.0-5.0 秒）。


## ⚠️ 免责声明

1.  **仅供学习交流**：本项目仅用于 Python 爬虫技术交流与学习，**严禁用于商业用途**。
2.  **遵守法律法规**：请严格遵守《中华人民共和国网络安全法》及相关法律法规。
3.  **尊重版权**：请勿大量恶意抓取数据，尊重 CNKI 及相关版权方的权益。使用本工具产生的任何法律后果由使用者自行承担。

## 📈 更新日志

### v1.1 (2025-12-26)
- ✨ 新增 368 本期刊列表管理 (`journals.yaml`)
- ✨ 新增期刊爬取进度跟踪 (`crawl_progress.yaml`)
- ✨ 新增期刊进度管理模块 (`journal_manager.py`)
- 🔧 优化滑动验证码识别算法，提升成功率
- 🔧 修复年份类型不一致导致统计重复的问题
- 🔧 优化断点续传功能

### v1.0 (2025-12)
- 🎉 首次发布
- ✅ 支持期刊检索和 PDF 下载
- ✅ 支持断点续传
- ✅ 支持验证码自动处理

---
*Created with ❤️ by **XJX & Claude***
