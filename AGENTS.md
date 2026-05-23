CNKI Intelligent Crawler — Agent Handbook
==============================

Purpose
- Give coding agents fast orientation: how to run, test, lint, and how to match the repository’s style.
- Keep responses concise; prefer command examples and short checklists.

Repo Snapshot
- Stack: Python 3 (asyncio, Playwright, aiohttp, PyYAML, tqdm, Pillow, numpy, scipy, opencv-python, ddddocr).
- Entry: `cli.py` orchestrates config parsing, login, crawling, downloads, stats, and exports.
- Core modules: `crawler/cnki/search.py` (search + pagination + crawl loop), `crawler/cnki/downloader.py` (PDF fetching via browser or HTTP), `crawler/cnki/auth.py` (session), `crawler/cnki/login.py` (login flows), `crawler/journal_manager.py` (progress/state), `crawler/storage/fs.py` (metadata + PDF storage), captcha solvers in `crawler/cnki/*captcha*.py`.
- Data/outputs: `data/meta.json`, `data/progress.json`, `data/pdf/{journal}/{year}`, `sessions/session_{id}_cookies.json`.

Environment + Setup
- Python deps: `pip install -r requirements.txt` (use venv).
- Playwright browsers: `playwright install chromium` (required for downloads/login).
- Config: edit `config.yaml`; defaults live in `cli.default_config()` if file missing.
- Encoding: UTF-8; codebase uses Chinese log/user strings—preserve them.

Run Commands
- Main crawl: `python cli.py` (uses `config.yaml`).
- Login: `python cli.py --login` (saves cookies to `cookies.json`).
- Stats panels: `python cli.py --stats`; quick counts: `python cli.py --count`.
- Download-only: `python cli.py --download`.
- Export metadata: `python cli.py --export csv`.
- List journals: `python cli.py --list-journals`.
- Utility scripts (under `scripts/`):
  - Rebuild meta from PDFs: `python scripts/rebuild_meta.py`.
  - Clean temp files: `python scripts/clean_project.py`.
  - Detect incomplete journals: `python scripts/check_incomplete_journals.py`.
  - Fix incomplete journals: `python scripts/fix_incomplete_journals.py`.

Tests
- Current repo has minimal tests; sanity script: `python test_unfinished.py` (prints pending journals counts).
- If adding pytest: structure under `tests/` and run `pytest path::TestClass::test_case` for single cases. Prefer markers for slow/network; default tests should not hit CNKI.

Lint/Format (conventions; no enforced tool in repo)
- Imports: standard lib, third-party, local; avoid unused; prefer explicit symbols over `*`.
- Formatting: follow Black-like 88–100 cols; keep readability; align multiline args; use trailing commas on multiline structures.
- Strings: double quotes in this codebase; keep Chinese user-facing text as-is.
- Types: use `typing` (Optional, Tuple, Dict, List); annotate public functions/methods; async functions should annotate return types.
- Logging: use module logger (`logging.getLogger(__name__)`); info for high-level steps; warning for recoverable issues; error for failures; keep Chinese messages consistent.
- Path handling: use `pathlib.Path`; avoid stringly paths; create parents with `mkdir(parents=True, exist_ok=True)`.
- Rate limiting: respect `_rate_limit_wait()` before network/browser actions; do not bypass shared locks (e.g., `_captcha_lock`).
- Concurrency: guard shared state with `asyncio.Lock`; per-session cookies via `sessions/session_{id}_cookies.json`; session IDs are reused round-robin.
- Error handling: prefer retries with bounded attempts; capture exceptions with context; keep browser close in `finally`; on captcha/expired sessions trigger relogin flow.
- Network/browser: use Playwright async APIs; wait for `domcontentloaded`; avoid `networkidle` on CNKI pages; use `expect_download` when saving PDFs.
- Storage: write JSON with `ensure_ascii=False, indent=2`; call `storage.flush()` after batch updates; PDFs saved under `data/pdf/{journal}/{year}`.
- Progress: `JournalProgressManager` tracks statuses (`pending`, `partial`, `completed`); when mutating, persist via manager methods, not ad-hoc writes.
- CLI UX: keep stdout prints user-friendly (emojis already in use); avoid blocking calls in async flows.
- Exceptions: raise descriptive errors; include journal/year context; avoid blanket bare `except`—log and re-raise or return status.
- Retries: paging/download operations already have retries; if adding, cap attempts and backoff respecting `rate_limit`.
- Timeouts: `page.goto(..., timeout=30000)` style; prefer explicit timeouts on awaits that touch network/browser.
- Downloads: prefer direct HTTP (`download_pdf_direct`) when available; fall back to browser flow for captchas; ensure `save_path.parent.mkdir(...)`.

Data Contracts + Structures
- Journal map: `JOURNAL_CODE_MAP` at top of `search.py`; extend cautiously (code -> name).
- Article dicts typically include `title`, `authors`, `journal`, `year`, `pdf_path`, `downloaded` flags.
- Config keys (app): `journal_name`, `use_journal_file`, `journal_file`, `year_start`, `year_end`, `download_fulltext`, `max_pages`, `concurrency`, `rate_limit`, `headless`, `output_dir`, optional `username/password`, `debug_screenshots`.
- Cookies: base `cookies.json`; per-session copies under `sessions/`; ensure fresh copies before concurrent runs.

Copilot Instructions (must follow)
- Source: `.github/copilot-instructions.md`.
- Emphases:
  - All network/browser ops use `async/await`; call `_rate_limit_wait()` before requests.
  - Path handling via `pathlib.Path`.
  - Multi-session concurrency: independent cookies per session; isolated captcha/login handling.
  - Captcha/relogin strategy: on failure, close browser, open login page with `quick_mode=True`, save new cookies, re-init session.
  - Pagination fallback: multiple strategies, retry 3 times, mark `year_incomplete=True` if still failing.
  - Storage/progress locations: meta/progress/PDF paths, session cookies, downloaded flags.
  - Logging: module loggers to `crawler.log`; CLI may print progress; keep messages readable.

When Adding Code
- Mirror existing async patterns; do not introduce sync blocking in crawl pipeline.
- Respect headless/force_show flags; store on instance to reuse after relogin.
- Avoid hard-coded sleeps; prefer awaited waits with reasoned durations.
- Keep user-facing text bilingual style consistent (Chinese present); do not translate existing strings.
- Handle clean shutdown: close pages/contexts/playwright in `finally`; null out handles to avoid reuse.
- If introducing new scripts, keep them under `scripts/` and document commands in this file.

Git / Contributions
- Branch from current state; no commit hooks documented; avoid committing secrets (`cookies.json`, `sessions/*.json`, `data/**`).
- Do not delete user data files; avoid resetting progress metadata unless feature demands.

Single-Test/Script Guidance
- For targeted checks today: `python test_unfinished.py` (no external I/O, reads progress files).
- Future pytest pattern: `pytest tests/test_x.py::TestClass::test_case` to run a single test; ensure tests mock CNKI network or use fixtures to avoid live hits.

Operational Tips
- Before long crawls ensure `cookies.json` is fresh; run `python cli.py --login` if unsure.
- To change concurrency, update `config.yaml` `concurrency` and ensure sessions folder exists; main runner copies cookies per session automatically.
- For debugging pagination/search, turn off headless (`--no-headless` in config or set headless false) and consider `debug_screenshots` flag on `CNKISearcher.create`.
- For download flakiness, prefer browser download path which handles captchas; check `crawler.log` for context.

Line Budget Note
- This handbook targets ~150 lines to stay readable for agents.
