# 📁 项目文件说明

## 核心文件

### 主程序
- **cli.py** - 命令行入口，处理所有用户交互和任务调度
- **config.yaml** - 配置文件，包含期刊列表、年份范围、爬取选项等
- **requirements.txt** - Python 依赖包列表

### 文档
- **README.md** - 项目使用说明和功能介绍
- **.gitignore** - Git 版本控制忽略规则

## 目录结构

### crawler/ - 核心爬虫代码
```
crawler/
├── __init__.py
├── cnki/                    # CNKI 专用模块
│   ├── __init__.py
│   ├── auth.py              # 认证和会话管理
│   ├── captcha_solver.py    # 验证码自动识别和解决
│   ├── downloader.py        # PDF 下载器
│   ├── login.py             # 登录处理
│   └── search.py            # 搜索和数据提取
└── storage/                 # 存储管理
    └── fs.py                # 文件系统存储（meta.json 和 PDF）
```

### data/ - 数据输出目录
```
data/
├── meta.json                # 文献元数据（JSON 格式）
├── progress.json            # 爬取进度记录（断点续传）
└── pdf/                     # PDF 文件存储
    └── {期刊名}/
        └── {年份}/
            └── *.pdf
```

### scripts/ - 辅助工具脚本
```
scripts/
├── analyze_captcha.py       # 验证码结构分析工具（调试用）
├── test_captcha.py          # 验证码测试脚本（调试用）
├── rebuild_meta.py          # 根据 PDF 文件重建 meta.json
├── fix_year_types.py        # 修复年份类型不一致问题
├── clean_project.py         # 项目清理脚本
└── sync_progress_counts.py  # 按 PDF 文件数同步 crawl_progress.yaml 里的 count
```

### assets/ - 资源文件
```
assets/
└── python-badge.svg         # README 中使用的徽章图片
```

### .github/ - GitHub 配置
```
.github/
└── copilot-instructions.md  # GitHub Copilot 指令文件
```

## 运行时生成的文件

以下文件在程序运行时自动生成，**不应提交到版本控制**：

- **cookies.json** - 登录凭证（敏感信息）
- **data/meta.json** - 文献元数据
- **data/progress.json** - 爬取进度
- **data/pdf/** - 下载的 PDF 文件
- **\*.log** - 日志文件
- **__pycache__/** - Python 字节码缓存

## 文件用途分类

### ✅ 必需文件（核心功能）
- cli.py, config.yaml, requirements.txt
- crawler/ 目录下所有 .py 文件
- README.md

### 🔧 工具脚本（可选，按需使用）
- scripts/rebuild_meta.py - 用于恢复丢失的元数据
- scripts/fix_year_types.py - 用于修复数据类型问题
- scripts/clean_project.py - 用于清理临时文件
- scripts/sync_progress_counts.py - 用于按 PDF 文件数回填 crawl_progress.yaml（支持 --priority 按 journals.yaml 过滤）

### 🐛 调试脚本（开发调试用）
- scripts/analyze_captcha.py
- scripts/test_captcha.py

### 📦 配置和文档
- .gitignore, .github/, assets/

## 清理建议

### 定期清理
运行以下命令清理临时文件：
```bash
python scripts/clean_project.py
```

### 进度数量同步
按 PDF 实际文件数同步 `crawl_progress.yaml` 中各年份 `count`：
```bash
python scripts/sync_progress_counts.py --dry-run
python scripts/sync_progress_counts.py
python scripts/sync_progress_counts.py --dry-run --priority 1
python scripts/sync_progress_counts.py --priority 1 2
```

### 手动清理
如需完全重置项目（保留代码，删除所有数据）：
```bash
# 删除所有下载数据
rm -rf data/pdf
rm -f data/meta.json
rm -f data/progress.json
rm -f cookies.json
```

### 备份数据
在清理前，建议备份重要数据：
```bash
# 备份元数据
cp data/meta.json data/meta.json.backup

# 备份 PDF 目录（可选，文件较大）
tar -czf data_backup.tar.gz data/pdf
```
