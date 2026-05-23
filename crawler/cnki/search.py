# -*- coding: utf-8 -*-
"""
CNKI高级搜索模块
实现：期刊搜索 -> 年份筛选 -> 文章列表提取 -> 分页处理
"""
import asyncio
import json
import re
import logging
from pathlib import Path
from playwright.async_api import async_playwright, Page, BrowserContext
from typing import List, Dict, Optional, AsyncGenerator, Tuple

# 尝试导入期刊进度管理器
try:
    from crawler.journal_manager import JournalProgressManager
    HAS_JOURNAL_MANAGER = True
except ImportError:
    HAS_JOURNAL_MANAGER = False

logger = logging.getLogger(__name__)

# 常见期刊名到代码的映射
JOURNAL_CODE_MAP = {
    "哲学研究": "ZXYJ",
    "文艺研究": "WYYJ",
    "文学评论": "WXPL",
    "历史研究": "LSYJ",
    "经济研究": "JJYJ",
    "考古学报": "KGXB",
    "心理学报": "XLXB",
    "中国语文": "ZGYW",
    "物理教师": "WLJS",
}


class CNKISearcher:
    """CNKI高级搜索器
    
    支持两种使用方式：
    1. 异步上下文管理器（推荐用于精细控制）:
        async with CNKISearcher(cookies_path) as searcher:
            await searcher.search_journal('中国语文')
            await searcher.filter_by_year(2022)
            async for article in searcher.iter_all_articles():
                print(article)
    
    2. 兼容cli.py的便捷方法:
        searcher = CNKISearcher.create(headers, cookies, rate_limit)
        async for article in searcher.search_journal_articles(
            journal_name='中国语文',
            year_start=2020,
            year_end=2022
        ):
            print(article)
    """
    
    @staticmethod
    def list_supported_journals() -> List[str]:
        """列出支持的期刊"""
        return list(JOURNAL_CODE_MAP.keys())
    
    @classmethod
    def create(cls, headers: Dict = None, cookies: Dict = None, 
               rate_limit: float = 2.0, proxy: str = None, 
               max_pages: int = 50, cookies_path: str = 'cookies.json',
               username: str = None, password: str = None,
               debug_screenshots: bool = False,
               session_id: int = None):
        """
        工厂方法：创建搜索器实例
        
        Args:
            headers: HTTP请求头（Playwright模式下忽略）
            cookies: cookies字典，如果提供则临时保存到文件
            rate_limit: 请求间隔（秒）
            proxy: 代理地址
            max_pages: 最大页数限制
            cookies_path: cookies文件路径
            username: 用户名（可选，用于自动登录）
            password: 密码（可选，用于自动登录）
            debug_screenshots: 是否保存调试截图
            session_id: 会话ID（用于多并发时区分不同会话）
            
        Returns:
            CNKISearcher实例
        """
        instance = cls(cookies_path=cookies_path)
        instance._rate_limit = rate_limit
        instance._max_pages = max_pages
        instance._proxy = proxy
        instance._username = username
        instance._password = password
        instance._debug_screenshots = debug_screenshots
        instance._session_id = session_id
        
        # 如果传入了cookies字典，保存到文件
        if cookies:
            with open(cookies_path, 'w', encoding='utf-8') as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
        
        return instance
    """CNKI高级搜索器"""
    
    ADVANCED_SEARCH_URL = 'https://kns.cnki.net/kns8s/AdvSearch'
    
    # 验证码处理锁和重登录锁（类级别，所有实例共享）
    _captcha_lock = None
    _relogin_lock = None
    
    def __init__(self, cookies_path: str = 'cookies.json'):
        self.cookies_path = cookies_path
        self.playwright = None
        self.browser = None
        self._session_id = None  # 会话ID
        # 初始化验证码锁和重登录锁
        if CNKISearcher._captcha_lock is None:
            CNKISearcher._captcha_lock = asyncio.Lock()
        if CNKISearcher._relogin_lock is None:
            CNKISearcher._relogin_lock = asyncio.Lock()
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._current_journal = None
        self._current_search_journal = None
        self._current_year = None
        # 保存浏览器设置，用于重新登录后恢复
        self._headless_mode = False
        self._force_show_browser = False
        self._relogin_start_minimized = True
        self._keep_browser_minimized = False
        self._browser_context_id = None
        self._next_start_minimized = False

    def _build_chromium_launch_args(self, headless: bool, start_minimized: bool = False) -> List[str]:
        """构建 Chromium 启动参数。"""
        args: List[str] = []
        # 仅在有头模式下通过 Chromium 窗口参数最小化；不使用键盘按键。
        if not headless and start_minimized:
            args.append('--start-minimized')
        return args

    async def _minimize_page_window(self, page: Optional[Page]) -> bool:
        """将页面所属浏览器窗口最小化到任务栏。

        仅使用 Chromium/CDP 窗口接口，不发送 Esc 等键盘事件。
        """
        if page is None:
            return False

        try:
            cdp_session = await page.context.new_cdp_session(page)
            window_info = await cdp_session.send('Browser.getWindowForTarget')
            window_id = window_info.get('windowId')
            if not window_id:
                logger.debug('未获取到 Chromium windowId，跳过最小化；不使用键盘按键回退')
                return False

            await cdp_session.send(
                'Browser.setWindowBounds',
                {
                    'windowId': window_id,
                    'bounds': {'windowState': 'minimized'}
                }
            )
            return True
        except Exception as e:
            logger.debug(f'最小化浏览器窗口失败（未使用键盘按键回退）: {e}')
            return False

    async def _get_browser_context_id(self, page: Optional[Page]) -> Optional[str]:
        """获取当前 Playwright context 对应的 Chromium browserContextId。"""
        if self._browser_context_id:
            return self._browser_context_id
        if page is None:
            return None

        try:
            cdp_session = await page.context.new_cdp_session(page)
            target_info = await cdp_session.send('Target.getTargetInfo')
            browser_context_id = target_info.get('targetInfo', {}).get('browserContextId')
            if browser_context_id:
                self._browser_context_id = browser_context_id
            return browser_context_id
        except Exception as e:
            logger.debug(f'获取 browserContextId 失败: {e}')
            return None

    async def _create_background_target_page(self, context: BrowserContext) -> Optional[Page]:
        """通过 CDP 在后台创建标签页，尽量避免抢占前台。"""
        if self.browser is None:
            return None

        browser_context_id = await self._get_browser_context_id(self.page)
        if not browser_context_id:
            return None

        try:
            browser_cdp_session = await self.browser.new_browser_cdp_session()
            async with context.expect_page(timeout=5000) as page_info:
                await browser_cdp_session.send(
                    'Target.createTarget',
                    {
                        'url': 'about:blank',
                        'background': True,
                        'browserContextId': browser_context_id,
                    }
                )
            return await page_info.value
        except Exception as e:
            logger.debug(f'后台创建标签页失败，回退到默认 new_page: {e}')
            return None

    async def _create_page(
        self,
        context: BrowserContext,
        keep_window_minimized: Optional[bool] = None
    ) -> Page:
        """创建新标签页，优先在后台打开，并在需要时保持浏览器窗口最小化。"""
        should_minimize = self._keep_browser_minimized if keep_window_minimized is None else keep_window_minimized
        page = await self._create_background_target_page(context)

        if page is None:
            page = await context.new_page()

        if should_minimize:
            await self._minimize_page_window(page)
        return page

    async def _prepare_same_tab_navigation(self, page: Page) -> None:
        """强制站点在当前标签页中打开后续页面，避免弹出新前台标签。"""
        await page.evaluate('''() => {
            window.open = (url) => {
                if (typeof url === 'string' && url) {
                    window.location.href = url;
                }
                return window;
            };

            const selectors = ['#pdfDown', 'a.btn-dlpdf', 'a[id="pdfDown"]'];
            for (const selector of selectors) {
                const el = document.querySelector(selector);
                if (!el) continue;
                el.removeAttribute('target');
                el.setAttribute('target', '_self');
            }
        }''')

    async def _find_first_visible(self, page: Page, selectors: List[str]):
        """按顺序查找第一个可见元素。"""
        for sel in selectors:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    return btn
            except Exception:
                continue
        return None

    def _normalize_search_journal_name(self, journal_name: str) -> str:
        """规范化网页检索使用的期刊名。

        规则：
        1. 中文冒号“：”统一替换为英文冒号“:”。
        2. 中文括号“（”、“）”统一替换为英文括号“(”、“)”。
        """
        if not isinstance(journal_name, str):
            return journal_name

        cleaned = journal_name.strip()
        normalized = (
            cleaned
            .replace('：', ':')
            .replace('（', '(')
            .replace('）', ')')
        )
        return normalized
    
    async def __aenter__(self):
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def start(
        self,
        headless: bool = False,
        force_show_browser: bool = False,
        start_minimized: bool = False
    ):
        """启动浏览器
        
        Args:
            headless: 是否使用无头模式（来自配置）
            force_show_browser: 是否强制显示浏览器（用于验证码场景，优先级高于headless）
            start_minimized: 是否以最小化方式启动有头浏览器（仅本次启动生效）
        """
        # 保存设置，用于重新登录后恢复
        self._headless_mode = headless
        self._force_show_browser = force_show_browser
        
        # 加载cookies
        with open(self.cookies_path, 'r', encoding='utf-8') as f:
            cookies_dict = json.load(f)
        
        # 如果需要处理验证码，强制使用非headless模式
        actual_headless = False if force_show_browser else headless
        effective_start_minimized = start_minimized or self._next_start_minimized
        self._keep_browser_minimized = bool(effective_start_minimized and not actual_headless)
        launch_args = self._build_chromium_launch_args(
            headless=actual_headless,
            start_minimized=effective_start_minimized
        )
        # 一次性标记，消费后重置，避免后续普通启动也被最小化。
        self._next_start_minimized = False
        
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=actual_headless,
            slow_mo=100,
            args=launch_args
        )
        self._browser_context_id = None
        self.context = await self.browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
        )
        
        # 添加cookies
        cookie_list = [
            {'name': k, 'value': str(v), 'domain': '.cnki.net', 'path': '/'}
            for k, v in cookies_dict.items()
        ]
        await self.context.add_cookies(cookie_list)
        
        self.page = await self._create_page(self.context, keep_window_minimized=self._keep_browser_minimized)
        logger.info('浏览器已启动')
    
    async def close(self):
        """关闭浏览器"""
        try:
            if self.browser:
                await self.browser.close()
                self.browser = None
        except Exception as e:
            logger.debug(f'关闭浏览器时出错: {e}')
        
        try:
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
        except Exception as e:
            logger.debug(f'停止playwright时出错: {e}')

        # 避免后续误用已失效的 page/context
        self.context = None
        self.page = None
        self._keep_browser_minimized = False
        self._browser_context_id = None
        
        logger.info('浏览器已关闭')
    
    async def search_journal(self, journal_name: str) -> int:
        """
        搜索期刊
        
        Args:
            journal_name: 期刊名称
            
        Returns:
            总结果数
        """
        search_journal_name = self._normalize_search_journal_name(journal_name)
        logger.info(f'搜索期刊: {journal_name}')
        if search_journal_name != journal_name:
            logger.info(f'检索名称调整为: {search_journal_name}')
        self._current_journal = journal_name
        self._current_search_journal = search_journal_name
        
        # 访问高级搜索页面 - 使用 domcontentloaded 而非 networkidle 避免无限等待
        try:
            await self.page.goto(self.ADVANCED_SEARCH_URL, wait_until='domcontentloaded', timeout=30000)
        except Exception as e:
            logger.warning(f'页面加载超时，继续尝试: {e}')
        
        await asyncio.sleep(3)  # 等待动态内容加载
        
        # 尝试多种方式找到文献来源输入框
        source_input = None
        
        # 方法1: 直接通过placeholder查找
        source_input = await self.page.query_selector('input[placeholder*="文献来源"], input[placeholder*="期刊"], input[placeholder*="来源"]')
        
        # 方法2: 通过data属性查找
        if not source_input:
            source_input = await self.page.query_selector('input[data-field="LY"], input[name="LY"]')
        
        # 方法3: 通过标签文字定位
        if not source_input:
            source_input = await self.page.evaluate('''() => {
                const labels = document.querySelectorAll('label, span');
                for (const label of labels) {
                    if (label.textContent.includes('来源') || label.textContent.includes('期刊')) {
                        const input = label.parentElement.querySelector('input[type="text"]');
                        if (input) return input;
                    }
                }
                return null;
            }''')
            if source_input:
                source_input = await self.page.query_selector(f'#{source_input.get("id")}' if source_input.get("id") else 'input')
        
        # 方法4: 按顺序取第3个可见输入框
        if not source_input:
            all_inputs = await self.page.query_selector_all('input[type="text"]')
            visible_inputs = []
            for inp in all_inputs:
                if await inp.is_visible():
                    visible_inputs.append(inp)
            
            if len(visible_inputs) >= 3:
                source_input = visible_inputs[2]
            elif len(visible_inputs) >= 1:
                # 如果没有3个，尝试使用最后一个
                source_input = visible_inputs[-1]
        
        if not source_input:
            # 保存截图以便调试
            await self.page.screenshot(path='debug_search_page.png')
            raise Exception('未找到文献来源输入框，已保存debug_search_page.png')
        
        await source_input.click()
        await asyncio.sleep(0.3)
        await source_input.fill(search_journal_name)
        
        # 隐藏可能遮挡的弹出框
        await self.page.evaluate('''() => {
            const elements = document.querySelectorAll('.search-sidebar-b, .recommend-info-body, .recommend-info');
            elements.forEach(el => el.style.display = 'none');
        }''')
        
        # 点击搜索
        btn = await self.page.query_selector('input.btn-search[value="检索"]')
        if btn:
            await btn.click(force=True)
        
        # 等待结果加载
        await asyncio.sleep(3)
        
        # 获取总结果数
        total = await self._get_total_count()
        logger.info(f'找到 {total} 条结果')
        
        return total
    
    async def filter_by_year(self, year: int) -> int:
        """
        按年份筛选
        
        Args:
            year: 目标年份
            
        Returns:
            筛选后的结果数
        """
        year = str(year)  # 统一转为字符串类型
        logger.info(f'筛选年份: {year}')
        self._current_year = year
        
        # 点击展开年度筛选
        try:
            year_section = await self.page.query_selector('dl[groupid="YE"] dt.tit')
            if year_section:
                await year_section.click()
                await asyncio.sleep(2)  # 等待展开动画
        except Exception as e:
            logger.debug(f'展开年度筛选失败: {e}')
        
        # 等待年份选项出现
        try:
            await self.page.wait_for_selector('dd[field="YE"] .resultlist li', state='attached', timeout=8000)
        except Exception as e:
            logger.warning(f'等待年份选项超时: {e}')
            # 继续尝试，可能元素已经存在
        
        await asyncio.sleep(1)
        
        # 使用JavaScript点击年份（更可靠）
        clicked = await self.page.evaluate(f'''(year) => {{
            const yearLinks = document.querySelectorAll('dd[field="YE"] .resultlist li a');
            for (const link of yearLinks) {{
                if (link.textContent?.includes(year)) {{
                    link.click();
                    return true;
                }}
            }}
            return false;
        }}''', str(year))
        
        if clicked:
            await asyncio.sleep(3)
        else:
            # 尝试checkbox方式
            checkbox_clicked = await self.page.evaluate(f'''(year) => {{
                const checkboxes = document.querySelectorAll('dd[field="YE"] input[type="checkbox"]');
                for (const cb of checkboxes) {{
                    if (cb.value === year) {{
                        cb.click();
                        return true;
                    }}
                }}
                return false;
            }}''', str(year))
            
            if checkbox_clicked:
                await asyncio.sleep(1)
                # 点击确定
                confirm_btn = await self.page.query_selector('.sidebar-filter-btns .btn-submit')
                if confirm_btn and await confirm_btn.is_visible():
                    await confirm_btn.click()
                    await asyncio.sleep(3)
            else:
                raise Exception(f'未找到 {year} 年的筛选选项')
        
        # 年份筛选后，点击文献来源中的期刊
        if self._current_search_journal:
            try:
                await self._click_journal_source(self._current_search_journal)
            except Exception as e:
                logger.warning(f'文献来源期刊点击失败: {e}')

        # 获取筛选后的结果数
        total = await self._get_total_count()
        logger.info(f'{year}年 共 {total} 条结果')
        
        return total

    async def _click_journal_source(self, journal_name: str) -> bool:
        """在文献来源中点击对应期刊

        Args:
            journal_name: 期刊名称

        Returns:
            是否点击成功
        """
        logger.info(f'文献来源点击期刊: {journal_name}')

        # 尝试展开文献来源筛选
        try:
            source_section = await self.page.query_selector('dl[groupid="WXLY"] dt.tit, dl[groupid="WXLY"] dt')
            if source_section:
                await source_section.click()
                await asyncio.sleep(1)
        except Exception as e:
            logger.debug(f'展开文献来源筛选失败: {e}')

        # 等待来源选项出现
        try:
            await self.page.wait_for_selector('dd[field="WXLY"] a', state='attached', timeout=8000)
        except Exception as e:
            logger.debug(f'等待文献来源选项超时: {e}')

        # 使用JavaScript点击期刊链接
        clicked = await self.page.evaluate('''(journalName) => {
            const container = document.querySelector('dd[field="WXLY"]');
            if (!container) return false;
            const links = container.querySelectorAll('a');
            for (const link of links) {
                const title = link.getAttribute('title') || '';
                const text = link.textContent || '';
                if (title.trim() === journalName || text.trim() === journalName) {
                    link.scrollIntoView({block: 'center'});
                    link.click();
                    return true;
                }
            }
            return false;
        }''', journal_name)

        if clicked:
            await asyncio.sleep(2)
            return True

        logger.warning(f'未在文献来源中找到期刊: {journal_name}')
        return False
    
    async def _get_total_count(self) -> int:
        """获取当前搜索结果总数"""
        count_text = await self.page.evaluate('''() => {
            const el = document.querySelector('.pagerTitleCell');
            if (!el) return '0';
            const match = el.textContent.match(/([\\d,]+)/);
            return match ? match[1] : '0';
        }''')
        return int(count_text.replace(',', ''))
    
    async def get_total_pages(self) -> int:
        """获取总页数"""
        pages_info = await self.page.evaluate('''() => {
            // 首先尝试 .countPageMark 元素 (格式: "1/5")
            const pageMarkEl = document.querySelector('.countPageMark');
            if (pageMarkEl) {
                const text = pageMarkEl.textContent || '';
                const match = text.match(/(\\d+)\\/(\\d+)/);
                if (match) {
                    return {text: text, pages: parseInt(match[2])};
                }
            }
            
            // 备用: 从分页控件获取
            const pagerEl = document.querySelector('.pagerTitleCell');
            if (pagerEl) {
                const text = pagerEl.textContent || '';
                const match = text.match(/(\\d+)\\/(\\d+)/);
                if (match) {
                    return {text: text, pages: parseInt(match[2])};
                }
            }
            
            return {text: '', pages: 1};
        }''')
        logger.debug(f'分页信息: {pages_info}')
        return pages_info.get('pages', 1)
    
    async def extract_articles(self) -> List[Dict]:
        """
        提取当前页的文章列表
        
        Returns:
            文章列表，每篇文章包含：title, authors, source, date, link, dbcode, filename
        """
        articles = await self.page.evaluate('''() => {
            const results = [];
            const rows = document.querySelectorAll('.result-table-list tbody tr');
            
            for (const row of rows) {
                // 跳过表头
                if (row.querySelector('th')) continue;
                
                // 标题和链接
                const titleLink = row.querySelector('.name a');
                if (!titleLink) continue;
                
                const title = titleLink.textContent?.trim() || '';
                const href = titleLink.getAttribute('href') || '';
                
                // 从收藏按钮获取dbcode和filename
                const collectBtn = row.querySelector('.icon-collect');
                let dbcode = '', filename = '';
                if (collectBtn) {
                    dbcode = collectBtn.getAttribute('data-dbname') || '';
                    filename = collectBtn.getAttribute('data-filename') || '';
                }
                
                // 作者
                const authorEl = row.querySelector('.author');
                const authors = authorEl ? authorEl.textContent?.trim() : '';
                
                // 来源
                const sourceEl = row.querySelector('.source a');
                const source = sourceEl ? sourceEl.textContent?.trim() : '';
                
                // 日期
                const dateEl = row.querySelector('.date');
                const date = dateEl ? dateEl.textContent?.trim() : '';
                
                // 下载次数
                const downloadEl = row.querySelector('.downloadCnt');
                const downloads = downloadEl ? parseInt(downloadEl.textContent?.trim() || '0') : 0;
                
                results.push({
                    title,
                    authors,
                    source,
                    date,
                    link: href,
                    dbcode,
                    filename,
                    downloads
                });
            }
            
            return results;
        }''')
        
        return articles

    def _extract_year_from_date(self, date_text: str) -> Optional[str]:
        """从日期文本中提取四位年份"""
        if not date_text:
            return None
        match = re.search(r'(19|20)\d{2}', str(date_text))
        if not match:
            return None
        return match.group(0)
    
    async def goto_next_page(self) -> bool:
        """
        跳转到下一页（增强版，多策略翻页）
        
        Returns:
            是否成功
        """
        # 获取当前页码，用于验证翻页是否成功
        current_page = await self.page.evaluate('''() => {
            // 方法1: 通过 .cur 类获取当前页
            const curPageEl = document.querySelector('.pagesnums a.cur, .pages a.cur');
            if (curPageEl) {
                return parseInt(curPageEl.textContent) || 1;
            }
            // 方法2: 通过 countPageMark 获取
            const pageMarkEl = document.querySelector('.countPageMark');
            if (pageMarkEl) {
                const match = pageMarkEl.textContent.match(/(\\d+)\\//);
                if (match) return parseInt(match[1]);
            }
            return 1;
        }''')
        
        logger.debug(f'当前页码: {current_page}')
        
        # 多种选择器查找下一页按钮
        next_btn = None
        next_selectors = ['a#PageNext', 'a.PageNext', '.pagesnums a:has-text(">")', 'a[id*="Next"]']
        for sel in next_selectors:
            try:
                next_btn = await self.page.query_selector(sel)
                if next_btn and await next_btn.is_visible():
                    break
            except:
                continue
        
        if not next_btn:
            logger.warning('未找到翻页按钮')
            return False
        
        # 检查是否可点击（不是最后一页）
        is_disabled = await next_btn.get_attribute('class') or ''
        if 'disabled' in is_disabled:
            logger.info('已到最后一页，翻页按钮不可用')
            return False
        
        # 获取按钮的目标页码
        target_page = await next_btn.get_attribute('data-curpage')
        logger.debug(f'当前页: {current_page}, 目标页: {target_page}')
        
        # 记录翻页前的第一篇文章标题，用于验证内容是否刷新
        first_title_before = await self.page.evaluate('''() => {
            const firstRow = document.querySelector('.result-table-list tbody tr .name a');
            return firstRow ? firstRow.textContent.trim() : '';
        }''')
        
        # 翻页策略1: JavaScript 点击
        clicked = False
        try:
            await self.page.evaluate('''() => {
                const btn = document.querySelector('a#PageNext') || document.querySelector('a.PageNext');
                if (btn) { btn.click(); return true; }
                return false;
            }''')
            clicked = True
        except Exception as e:
            logger.debug(f'JS点击失败: {e}')
        
        # 翻页策略2: 直接点击元素
        if not clicked:
            try:
                await next_btn.click(force=True)
                clicked = True
            except Exception as e:
                logger.debug(f'直接点击失败: {e}')
        
        # 翻页策略3: 通过页码链接跳转
        if not clicked:
            try:
                next_page_num = current_page + 1
                page_link = await self.page.query_selector(f'.pagesnums a:has-text("{next_page_num}")')
                if page_link:
                    await page_link.click()
                    clicked = True
            except Exception as e:
                logger.debug(f'页码链接点击失败: {e}')
        
        if not clicked:
            logger.warning('所有翻页策略都失败')
            return False
        
        # 等待页面加载：多种验证方式
        success = False
        
        # 方法1: 等待页码变化（最多8秒）
        try:
            await self.page.wait_for_function(
                f'''() => {{
                    const curEl = document.querySelector('.pagesnums a.cur, .pages a.cur');
                    if (curEl) {{
                        const page = parseInt(curEl.textContent) || 0;
                        return page > {current_page};
                    }}
                    return false;
                }}''',
                timeout=8000
            )
            success = True
            logger.debug(f'翻页成功(页码变化): {current_page} -> {current_page + 1}')
        except Exception:
            logger.debug('等待页码变化超时，检查内容是否刷新...')
        
        # 方法2: 如果页码没变，检查内容是否刷新
        if not success:
            await asyncio.sleep(3)  # 增加等待时间
            first_title_after = await self.page.evaluate('''() => {
                const firstRow = document.querySelector('.result-table-list tbody tr .name a');
                return firstRow ? firstRow.textContent.trim() : '';
            }''')
            if first_title_after and first_title_after != first_title_before:
                success = True
                logger.debug(f'翻页成功(内容刷新): 标题从"{first_title_before[:20]}..."变为"{first_title_after[:20]}..."')
        
        # 方法3: 检查 countPageMark 是否更新
        if not success:
            page_mark = await self.page.evaluate('''() => {
                const el = document.querySelector('.countPageMark');
                if (el) {
                    const match = el.textContent.match(/(\\d+)\\//);
                    return match ? parseInt(match[1]) : 0;
                }
                return 0;
            }''')
            if page_mark > current_page:
                success = True
                logger.debug(f'翻页成功(countPageMark): {current_page} -> {page_mark}')
        
        # 额外等待确保内容完全加载
        await asyncio.sleep(1)
        
        # 最终验证
        new_page = await self.page.evaluate('''() => {
            const curEl = document.querySelector('.pagesnums a.cur, .pages a.cur');
            return curEl ? parseInt(curEl.textContent) || 1 : 1;
        }''')
        
        if new_page > current_page:
            return True
        elif success:
            # 内容刷新了但页码没变，可能是页面结构问题，仍然认为成功
            logger.warning(f'页码未变({new_page})，但内容已刷新，继续处理')
            return True
        else:
            logger.warning(f'翻页验证失败: 当前仍在第 {new_page} 页')
            return False
    
    async def iter_all_articles(self, max_pages: int = None) -> AsyncGenerator[Dict, None]:
        """
        遍历所有页的文章
        
        Args:
            max_pages: 最大页数限制
            
        Yields:
            文章字典
        """
        total_pages = await self.get_total_pages()
        if max_pages:
            total_pages = min(total_pages, max_pages)
        
        logger.info(f'共 {total_pages} 页')
        
        for page_num in range(1, total_pages + 1):
            logger.info(f'第 {page_num}/{total_pages} 页')
            
            if page_num > 1:
                success = await self.goto_next_page()
                if not success:
                    logger.warning(f'无法跳转到第 {page_num} 页')
                    break
            
            articles = await self.extract_articles()
            for article in articles:
                # 添加期刊和年份信息
                article['journal'] = self._current_journal
                article['year'] = str(self._current_year)  # 统一转为字符串类型
                yield article
    
    async def download_pdf(self, article: Dict, output_dir: str = 'data') -> Tuple[bool, str]:
        """
        下载单篇文章的PDF
        
        Args:
            article: 文章信息字典（需包含 link, dbcode, filename, journal, year）
            output_dir: 输出目录
            
        Returns:
            (是否成功, 保存路径或错误消息)
        """
        from pathlib import Path
        
        journal = article.get('journal', 'unknown')
        year = str(article.get('year', 'unknown'))
        filename = article.get('filename', '')
        dbcode = article.get('dbcode', 'CJFQ')
        link = article.get('link', '')
        title = article.get('title', '')[:30]
        
        if not filename:
            return False, '缺少文件名'
        
        # 构建保存路径: data/pdf/期刊名/年份/filename.pdf
        save_dir = Path(output_dir) / 'pdf' / journal / year
        
        # 文件系统操作可能因磁盘问题失败，添加重试
        for fs_retry in range(3):
            try:
                save_dir.mkdir(parents=True, exist_ok=True)
                break
            except OSError as e:
                if fs_retry < 2:
                    logger.warning(f'文件系统错误，重试 {fs_retry+1}/3: {e}')
                    await asyncio.sleep(2)
                else:
                    return False, f'文件系统错误: {e}'
        
        save_path = save_dir / f'{filename}.pdf'
        
        # 如果已存在则跳过
        if save_path.exists():
            logger.debug(f'PDF已存在: {save_path}')
            return True, str(save_path)
        
        detail_page = None
        download_save_timeout = 20

        async def _save_download_with_timeout(download) -> bool:
            try:
                await asyncio.wait_for(
                    download.save_as(str(save_path)),
                    timeout=download_save_timeout
                )
                return True
            except asyncio.TimeoutError:
                try:
                    await download.cancel()
                except Exception:
                    pass
                if save_path.exists():
                    try:
                        save_path.unlink()
                    except Exception:
                        pass
                return False
        try:
            # 构建文章详情页URL
            if link and link.startswith('http'):
                detail_url = link
            else:
                detail_url = f'https://kns.cnki.net/kcms2/article/abstract?v=&dbcode={dbcode}&dbname={dbcode}&filename={filename}'
            
            # 访问详情页
            detail_page = await self._create_page(self.context)
            await detail_page.goto(detail_url, wait_until='domcontentloaded', timeout=20000)
            await asyncio.sleep(1.5)

            # 优先检测安全验证页，避免误判为“无PDF下载按钮”
            try:
                current_url = detail_page.url
                in_verify_page = ('verify' in current_url or 'bar.cnki.net' in current_url)
                if not in_verify_page:
                    in_verify_page = await self._check_access_limit(detail_page)

                if in_verify_page:
                    print(f"    🔐 检测到安全验证页面，优先处理验证...", flush=True)
                    async with CNKISearcher._captcha_lock:
                        solved = await self._solve_verify_page(detail_page)

                    if solved == 'relogin_success':
                        print(f"    🔄 安全验证已触发重登录刷新，稍后重试下载...", flush=True)
                        await asyncio.sleep(5)
                        await detail_page.close()
                        return False, '安全验证页面-需要重试'
                    if solved:
                        if not detail_page.is_closed():
                            await detail_page.goto(detail_url, wait_until='domcontentloaded', timeout=20000)
                            await asyncio.sleep(1)
                    else:
                        if not detail_page.is_closed():
                            await detail_page.close()
                        return False, '安全验证页面-需要重新登录'
            except Exception as e:
                logger.debug(f'安全验证页面预检测失败: {e}')
            
            selectors = ['#pdfDown', 'a.btn-dlpdf', 'a[id="pdfDown"]']

            download_btn = await self._find_first_visible(detail_page, selectors)

            # 兜底检查：优先匹配 CNKI 常见的 PDF 下载按钮结构
            # <a onclick="WriteKrsDownLog()" target="_blank" id="pdfDown" name="pdfDown" href="...">PDF下载</a>
            if not download_btn:
                try:
                    fallback_btn = await detail_page.query_selector(
                        'a#pdfDown[name="pdfDown"][onclick*="WriteKrsDownLog"]'
                    )
                    if fallback_btn:
                        href = await fallback_btn.get_attribute('href')
                        is_visible = await fallback_btn.is_visible()
                        if href and href.strip() and is_visible:
                            download_btn = fallback_btn
                except Exception as e:
                    logger.debug(f'兜底检查PDF下载按钮失败: {e}')
            
            if not download_btn:
                # 再次确认是否跳到了验证页，避免误判
                try:
                    current_url = detail_page.url
                    in_verify_page = ('verify' in current_url or 'bar.cnki.net' in current_url)
                    if not in_verify_page:
                        in_verify_page = await self._check_access_limit(detail_page)
                    if in_verify_page:
                        await detail_page.close()
                        return False, '安全验证页面-需要重新登录'
                except Exception as e:
                    logger.debug(f'下载按钮缺失时验证页复检失败: {e}')

                await detail_page.close()
                logger.debug(f'无PDF: {title}...')
                return False, '无PDF下载按钮'
            
            # 检查按钮是否可用
            btn_class = await download_btn.get_attribute('class') or ''
            if 'disabled' in btn_class or 'grey' in btn_class:
                await detail_page.close()
                logger.debug(f'PDF不可下载: {title}...')
                return False, 'PDF按钮不可用'
            
            # 点击下载并处理可能的验证码（在新标签页中）
            max_attempts = 3
            for attempt in range(max_attempts):
                if detail_page.is_closed():
                    detail_page = await self._create_page(self.context)
                    await detail_page.goto(detail_url, wait_until='domcontentloaded', timeout=20000)
                    await asyncio.sleep(1)

                download_btn = await self._find_first_visible(detail_page, selectors)
                if not download_btn:
                    try:
                        fallback_btn = await detail_page.query_selector(
                            'a#pdfDown[name="pdfDown"][onclick*="WriteKrsDownLog"]'
                        )
                        if fallback_btn:
                            href = await fallback_btn.get_attribute('href')
                            is_visible = await fallback_btn.is_visible()
                            if href and href.strip() and is_visible:
                                download_btn = fallback_btn
                    except Exception as e:
                        logger.debug(f'重试阶段兜底检查PDF下载按钮失败: {e}')
                if not download_btn:
                    await detail_page.close()
                    return False, '下载按钮丢失'

                await self._prepare_same_tab_navigation(detail_page)

                try:
                    async with detail_page.expect_download(timeout=8000) as download_info:
                        await download_btn.click()
                    download = await download_info.value
                    saved = await _save_download_with_timeout(download)
                    if not saved:
                        raise asyncio.TimeoutError("下载超时")
                    await detail_page.close()
                    logger.info(f'✓ 下载: {title}...')
                    return True, str(save_path)
                except Exception:
                    pass

                try:
                    await detail_page.wait_for_load_state('domcontentloaded', timeout=5000)
                except Exception:
                    pass

                try:
                    current_url = detail_page.url
                    
                    # 检查是否是验证码页面
                    if 'verify' in current_url or 'bar.cnki.net' in current_url:
                        print(f"    🔐 检测到验证码/验证页面...", flush=True)
                        
                        # 获取验证码锁，确保同一时间只处理一个验证码
                        async with CNKISearcher._captcha_lock:
                            # 统一由 _solve_verify_page 处理（会优先尝试重新登录）
                            solved = await self._solve_verify_page(detail_page)
                        
                        # 处理重新登录成功的情况
                        if solved == 'relogin_success':
                            print(f"    🔄 Cookie已刷新，等待状态同步后重试...", flush=True)
                            await asyncio.sleep(5)  # 等待更长时间让服务器状态同步
                            await detail_page.close()
                            # 返回特殊状态让上层重试
                            return False, 'Cookie已刷新-请重试'
                        elif solved:
                            print(f"    ✅ 验证码已解决", flush=True)
                            if not detail_page.is_closed():
                                await detail_page.goto(detail_url, wait_until='domcontentloaded', timeout=20000)
                                await asyncio.sleep(1)
                            continue
                        else:
                            print(f"    ❌ 验证码解决失败", flush=True)
                            if not detail_page.is_closed():
                                try:
                                    await detail_page.close()
                                except Exception:
                                    pass
                            # 验证码失败可能是访问限制
                            return False, '验证失败-可能需要重新登录'
                except Exception as e:
                    logger.debug(f"检查同标签页下载状态失败: {e}")
                
                # 尝试直接等待下载
                try:
                    async with detail_page.expect_download(timeout=15000) as download_info:
                        await download_btn.click()
                    
                    download = await download_info.value
                    saved = await _save_download_with_timeout(download)
                    if not saved:
                        raise asyncio.TimeoutError("下载超时")
                    await detail_page.close()
                    logger.info(f'✓ 下载: {title}...')
                    return True, str(save_path)
                    
                except Exception as e:
                    if attempt < max_attempts - 1:
                        logger.debug(f"下载尝试 {attempt+1} 失败: {e}")
                        await asyncio.sleep(1)
                        continue
            
            await detail_page.close()
            return False, '下载超时-需要重登'
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f'下载失败 {filename}: {error_msg}')
            if detail_page:
                try:
                    await detail_page.close()
                except:
                    pass
            return False, error_msg
    
    async def _solve_verify_page(self, page, max_attempts: int = 6, retry_after_relogin: bool = True) -> bool | str:
        """
        解决验证码页面（bar.cnki.net/bar/verify/...）
        
        新策略：遇到验证码时优先尝试重新登录刷新身份，而不是直接解决验证码
        只有当重新登录失败后才会尝试解决验证码
        
        Args:
            page: 验证码页面
            max_attempts: 最大尝试次数
            retry_after_relogin: 是否在重新登录后重试验证码
            
        Returns:
            True - 验证成功
            False - 验证失败
            'relogin_success' - 已刷新Cookie需要重试
        """
        # 【核心逻辑】：遇到验证码页面时，优先尝试重新登录刷新身份
        # 调用此函数本身就意味着检测到了验证码，无需再次检测
        if retry_after_relogin:
            print(f"    🔐 检测到验证码，优先尝试重新登录刷新身份...", flush=True)
            
            try:
                await page.close()
            except:
                pass
            
            relogin_success = await self._relogin()
            if relogin_success:
                print(f"    ✅ 重新登录成功，身份已刷新", flush=True)
                return 'relogin_success'
            else:
                print(f"    ⚠️ 重新登录失败，返回失败状态", flush=True)
                # 重新登录失败，页面已关闭，返回 False 让调用方重试
                return False
        
        # retry_after_relogin=False 时才会执行到这里（说明是重登录后的重试）
        # 此时尝试解决验证码
        try:
            from .improved_captcha_solver import ImprovedCaptchaSolver
            
            print(f"    🧩 尝试解决验证码...", flush=True)
            solver = ImprovedCaptchaSolver(page, debug=getattr(self, '_debug_screenshots', False))
            result = await solver.solve(max_attempts=max_attempts)
            
            if result:
                self._cleanup_debug_screenshots()
                return True
            
            # 自动求解失败，等待用户手动完成
            print("    ⏳ 自动验证失败，请手动完成 (90秒)...", flush=True)
            manual_result = await self._wait_for_verify_page_close(page, timeout=90)
            self._cleanup_debug_screenshots()
            return manual_result
            
        except ImportError as e:
            logger.warning(f"改进版验证码模块加载失败: {e}，使用原始方法")
            return await self._solve_verify_page_legacy(page, max_attempts, retry_after_relogin=False)
        except Exception as e:
            logger.error(f"验证码求解出错: {e}")
            self._cleanup_debug_screenshots()
            return False
            return False
    
    async def _solve_verify_page_legacy(self, page, max_attempts: int = 5, retry_after_relogin: bool = True) -> bool | str:
        """
        原始验证码求解方法（备用）
        """
        for attempt in range(max_attempts):
            try:
                print(f"    🧩 拼图验证尝试 {attempt + 1}/{max_attempts}...", flush=True)
                
                # 等待页面完全加载
                await asyncio.sleep(1.5)
                
                # 首先检查是否是"访问次数过多"页面而不是验证码
                access_limit_detected = await page.evaluate('''() => {
                    const body = document.body ? document.body.innerText : '';
                    const limitPatterns = [
                        '访问次数', '访问过于频繁', '访问太频繁', '请求过于频繁',
                        '操作过于频繁', '频繁访问', '请稍后再试', '访问受限',
                        '暂时无法访问', '请重新登录', '登录已过期', '会话已过期'
                        ,'繁忙','系统繁忙','服务器繁忙','当前访问人数较多','网络繁忙'
                    ];
                    for (const pattern of limitPatterns) {
                        if (body.includes(pattern)) return true;
                    }
                    return false;
                }''')
                
                if access_limit_detected:
                    print(f"    ⚠️ 检测到访问限制，尝试自动重新登录刷新Cookie...", flush=True)
                    
                    # 尝试自动重新登录刷新 cookie
                    if retry_after_relogin:
                        try:
                            # 关闭验证码页面
                            await page.close()
                        except:
                            pass
                        
                        # 调用重新登录
                        relogin_success = await self._relogin()
                        if relogin_success:
                            print(f"    ✅ Cookie已刷新，请重新操作触发验证码", flush=True)
                            # 返回特殊状态表示需要重试
                            return 'relogin_success'
                        else:
                            print(f"    ⚠️ 自动刷新Cookie失败，需要手动处理...", flush=True)
                    
                    # 这不是验证码，而是访问限制，需要重新登录
                    return False
                
                # 获取滑块和图片的位置信息
                captcha_data = await page.evaluate('''() => {
                    const result = {
                        success: false,
                        slider: null,
                        image: null,
                        trackWidth: 0,
                        debug: []
                    };
                    
                    // 1. 找到背景图片
                    const images = document.querySelectorAll('img');
                    for (const img of images) {
                        const rect = img.getBoundingClientRect();
                        if (rect.width > 250 && rect.height > 100) {
                            result.image = {
                                x: rect.x,
                                y: rect.y,
                                width: rect.width,
                                height: rect.height
                            };
                            break;
                        }
                    }
                    
                    // 2. 多种方式找滑块
                    const allElements = document.querySelectorAll('*');
                    
                    // 方法1：找 >> 或 » 符号
                    for (const el of allElements) {
                        const text = (el.textContent || '').trim();
                        const rect = el.getBoundingClientRect();
                        
                        if ((text === '»' || text === '>>' || text === '»' || text.includes('»')) && 
                            rect.width > 20 && rect.width < 100 && 
                            rect.height > 20 && rect.height < 100 &&
                            el.offsetParent !== null) {
                            result.slider = {
                                x: rect.x,
                                y: rect.y,
                                width: rect.width,
                                height: rect.height,
                                method: 'text'
                            };
                            result.success = true;
                            break;
                        }
                    }
                    
                    // 方法2：如果方法1没找到，找class包含slider/drag/btn的元素
                    if (!result.slider) {
                        for (const el of allElements) {
                            const className = (el.className || '').toLowerCase();
                            const id = (el.id || '').toLowerCase();
                            const rect = el.getBoundingClientRect();
                            
                            if ((className.includes('slider') || className.includes('drag') || 
                                 className.includes('btn') || className.includes('handler') ||
                                 id.includes('slider') || id.includes('drag')) &&
                                rect.width > 30 && rect.width < 80 && 
                                rect.height > 30 && rect.height < 80 &&
                                el.offsetParent !== null) {
                                result.slider = {
                                    x: rect.x,
                                    y: rect.y,
                                    width: rect.width,
                                    height: rect.height,
                                    method: 'class',
                                    className: className
                                };
                                result.success = true;
                                break;
                            }
                        }
                    }
                    
                    // 方法3：找在图片下方、轨道左侧的可拖动小方块
                    if (!result.slider && result.image) {
                        const imgBottom = result.image.y + result.image.height;
                        
                        for (const el of allElements) {
                            const rect = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            
                            // 在图片下方
                            if (rect.y > imgBottom - 20 && rect.y < imgBottom + 80 &&
                                // 在左侧
                                rect.x < result.image.x + 100 &&
                                // 小方块尺寸
                                rect.width >= 30 && rect.width <= 80 &&
                                rect.height >= 30 && rect.height <= 80 &&
                                // 可见
                                el.offsetParent !== null &&
                                style.display !== 'none' &&
                                // 不是图片
                                el.tagName !== 'IMG') {
                                
                                const cursor = style.cursor;
                                const bg = style.backgroundColor;
                                
                                // 有背景色或特定cursor的可能是滑块
                                if (cursor === 'pointer' || cursor === 'move' || cursor === 'grab' ||
                                    (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent')) {
                                    result.slider = {
                                        x: rect.x,
                                        y: rect.y,
                                        width: rect.width,
                                        height: rect.height,
                                        method: 'position',
                                        cursor: cursor
                                    };
                                    result.success = true;
                                    break;
                                }
                            }
                        }
                    }
                    
                    // 方法4：作为最后手段，直接找轨道区域内最左边的可点击元素
                    if (!result.slider) {
                        const candidates = [];
                        for (const el of allElements) {
                            const rect = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            
                            if (rect.width >= 30 && rect.width <= 80 &&
                                rect.height >= 30 && rect.height <= 80 &&
                                el.offsetParent !== null &&
                                (style.cursor === 'pointer' || style.cursor === 'move' || style.cursor === 'grab')) {
                                candidates.push({
                                    el: el,
                                    rect: rect,
                                    x: rect.x
                                });
                            }
                        }
                        
                        if (candidates.length > 0) {
                            // 取最左边的
                            candidates.sort((a, b) => a.x - b.x);
                            const best = candidates[0];
                            result.slider = {
                                x: best.rect.x,
                                y: best.rect.y,
                                width: best.rect.width,
                                height: best.rect.height,
                                method: 'fallback'
                            };
                            result.success = true;
                        }
                    }
                    
                    // 3. 找到滑动轨道
                    for (const el of allElements) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 250 && rect.width < 400 && 
                            rect.height > 25 && rect.height < 70 &&
                            el.tagName !== 'IMG' && el.offsetParent !== null) {
                            result.trackWidth = rect.width;
                            break;
                        }
                    }
                    
                    return result;
                }''')
                
                if not captcha_data.get('success') or not captcha_data.get('slider'):
                    logger.debug(f"未找到滑块元素: {captcha_data}")
                    if 'verify' not in page.url:
                        return True
                    await asyncio.sleep(0.5)
                    continue
                
                slider = captcha_data['slider']
                image = captcha_data.get('image') or {}
                
                # 🔍 通过截图分析缺口位置
                gap_x = await self._find_gap_by_screenshot(page, image) if image else 0
                
                # 每次重试时添加微调偏移
                import random
                retry_offset = [-8, 0, 8, -15, 15][attempt] if attempt < 5 else random.randint(-20, 20)
                
                if gap_x > 0 and image:
                    # 缺口检测成功
                    slider_init_offset = slider.get('x', 0) - image.get('x', 0)
                    puzzle_piece_width = int(image.get('width', 300) * 0.10)
                    
                    # 基础距离 + 重试微调
                    distance = gap_x - slider_init_offset - puzzle_piece_width // 2 + retry_offset
                    
                    print(f"    🎯 缺口: {gap_x}px, 滑动: {distance}px (微调: {retry_offset:+d})", flush=True)
                else:
                    # 检测失败，使用估算
                    base_width = image.get('width', 280) if image else 280
                    positions = [0.48, 0.55, 0.62, 0.52, 0.58]
                    pos = positions[attempt] if attempt < len(positions) else random.uniform(0.45, 0.65)
                    distance = int(base_width * pos) + retry_offset
                    print(f"    ⚠️ 估算滑动: {distance}px ({pos*100:.0f}%)", flush=True)
                
                # 确保距离在合理范围
                distance = max(80, min(distance, 280))
                
                # 执行滑动
                success = await self._perform_human_slide(page, slider, distance)
                
                if success:
                    # 等待验证结果
                    await asyncio.sleep(1.5)
                    
                    # 检查页面状态
                    try:
                        # 首先检查页面是否已关闭（验证成功后页面通常会自动关闭）
                        if page.is_closed():
                            print(f"    ✅ 验证成功（页面已关闭）", flush=True)
                            self._cleanup_debug_screenshots()
                            return True
                        
                        current_url = page.url
                        if 'verify' not in current_url and 'bar.cnki.net' not in current_url:
                            print(f"    ✅ 验证成功（页面已跳转）", flush=True)
                            self._cleanup_debug_screenshots()
                            return True
                        
                        # 检查是否显示成功/失败提示
                        result_text = await page.evaluate('''() => {
                            const body = document.body.innerText || '';
                            if (body.includes('成功') || body.includes('通过') || body.includes('验证完成')) return 'success';
                            if (body.includes('失败') || body.includes('错误') || body.includes('重试') || body.includes('再试')) return 'failed';
                            const limitPatterns = ['访问次数','频繁','请稍后','重新登录','过期','繁忙','系统繁忙','服务器繁忙','当前访问人数较多','网络繁忙'];
                            for (const p of limitPatterns) {
                                if (body.includes(p)) return 'limit';
                            }
                            return 'unknown';
                        }''')
                        
                        if result_text == 'success':
                            print(f"    ✅ 验证成功", flush=True)
                            self._cleanup_debug_screenshots()
                            return True
                        elif result_text == 'failed':
                            print(f"    ❌ 验证失败，重试...", flush=True)
                        elif result_text == 'limit':
                            print(f"    ⚠️ 验证后仍提示繁忙/访问受限，尝试刷新 Cookie 并重试...", flush=True)
                            self._cleanup_debug_screenshots()
                            if retry_after_relogin:
                                try:
                                    await page.close()
                                except:
                                    pass

                                relogin_success = await self._relogin()
                                if relogin_success:
                                    return 'relogin_success'
                            return False
                        # unknown 状态继续重试
                        
                    except Exception as e:
                        # 页面可能已关闭，视为成功
                        logger.debug(f"检查验证结果时出错: {e}")
                        print(f"    ✅ 验证可能成功", flush=True)
                        self._cleanup_debug_screenshots()
                        return True
                
                # 等待刷新后重试
                await asyncio.sleep(1.5)
                
            except Exception as e:
                logger.error(f"验证码求解出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(1)
        
        # 自动求解失败，检查是否是访问限制
        try:
            if not page.is_closed():
                access_limit = await page.evaluate('''() => {
                    const body = document.body ? document.body.innerText : '';
                    const limitPatterns = ['访问次数', '频繁', '请稍后', '重新登录', '过期', '繁忙', '系统繁忙', '服务器繁忙'];
                    for (const p of limitPatterns) {
                        if (body.includes(p)) return true;
                    }
                    return false;
                }''')
                
                if access_limit:
                    print("    ⚠️ 检测到访问限制，将触发手动登录流程...", flush=True)
                    self._cleanup_debug_screenshots()
                    return False  # 返回 False 触发上层的手动登录流程
        except:
            pass
        
        # 自动求解失败，等待用户手动完成
        print("    ⏳ 自动验证失败，请手动完成 (90秒)...", flush=True)
        result = await self._wait_for_verify_page_close(page, timeout=90)
        
        # 清理调试截图
        self._cleanup_debug_screenshots()
        
        return result
    
    def _cleanup_debug_screenshots(self):
        """清理调试截图文件"""
        import os
        debug_files = ['debug_verify_page.png', 'debug_full_screenshot.png', 'debug_captcha.png']
        for f in debug_files:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except:
                pass
    
    async def _find_gap_by_screenshot(self, page, image_info: dict) -> int:
        """
        通过截图分析缺口位置 - 改进版
        
        使用多种方法检测缺口位置并取最可靠的结果
        
        Args:
            page: 验证码页面
            image_info: 图片位置信息 {x, y, width, height}
            
        Returns:
            缺口的 X 坐标（相对于裁剪图片），0 表示检测失败
        """
        try:
            from PIL import Image
            import numpy as np
            import io
            
            if not image_info:
                return 0
            
            img_x = int(image_info.get('x', 0))
            img_y = int(image_info.get('y', 0))
            img_width = int(image_info.get('width', 0))
            img_height = int(image_info.get('height', 0))
            
            if img_width < 200 or img_height < 50:
                return 0
            
            # 截取整个页面
            screenshot_bytes = await page.screenshot()
            full_image = Image.open(io.BytesIO(screenshot_bytes))
            
            # 裁剪出验证码图片区域（不加边距，精确裁剪）
            crop_box = (img_x, img_y, img_x + img_width, img_y + img_height)
            captcha_img = full_image.crop(crop_box)
            
            # 调试模式下保存
            if hasattr(self, '_debug_screenshots') and self._debug_screenshots:
                captcha_img.save('debug_captcha.png')
            
            # 转换为 numpy 数组
            img_array = np.array(captcha_img.convert('RGB'))
            height, width = img_array.shape[:2]
            
            # 转为灰度图
            gray = np.mean(img_array, axis=2)
            
            # ===== 方法1: 滑块模板匹配 =====
            # 缺口通常是一个矩形区域，我们寻找垂直边缘
            
            # 计算 Sobel 水平梯度（检测垂直边缘）
            sobel_x = np.zeros_like(gray)
            for y in range(1, height - 1):
                for x in range(1, width - 1):
                    # Sobel X 核
                    gx = (gray[y-1, x+1] + 2*gray[y, x+1] + gray[y+1, x+1]) - \
                         (gray[y-1, x-1] + 2*gray[y, x-1] + gray[y+1, x-1])
                    sobel_x[y, x] = abs(gx)
            
            # 在图片的 30%-85% 范围内寻找缺口
            search_start = int(width * 0.30)
            search_end = int(width * 0.85)
            
            # 计算每列的边缘强度总和
            col_edge_sum = []
            for x in range(search_start, search_end):
                # 只看图片中间区域（避开顶部和底部噪声）
                col_sum = np.sum(sobel_x[int(height*0.1):int(height*0.9), x])
                col_edge_sum.append((x, col_sum))
            
            # ===== 方法2: 寻找局部暗区域 =====
            # 缺口区域通常比周围暗
            col_brightness = []
            for x in range(search_start, search_end):
                col_mean = np.mean(gray[int(height*0.2):int(height*0.8), x])
                col_brightness.append((x, col_mean))
            
            # 使用滑动窗口找到最暗的区域
            window_size = int(width * 0.12)  # 缺口宽度约为图片宽度的10-15%
            min_brightness = float('inf')
            gap_center_brightness = 0
            
            for i in range(len(col_brightness) - window_size):
                window_sum = sum(b for _, b in col_brightness[i:i+window_size])
                if window_sum < min_brightness:
                    min_brightness = window_sum
                    gap_center_brightness = col_brightness[i][0] + window_size // 2
            
            # ===== 方法3: 找最强的垂直边缘对 =====
            # 缺口有左右两条明显的垂直边缘
            edge_peaks = sorted(col_edge_sum, key=lambda x: x[1], reverse=True)[:10]
            edge_positions = sorted([p[0] for p in edge_peaks])
            
            # 找间距合适的边缘对（缺口宽度约 30-60px）
            gap_left_edge = 0
            for i in range(len(edge_positions) - 1):
                gap_width = edge_positions[i + 1] - edge_positions[i]
                if 25 <= gap_width <= 70:
                    gap_left_edge = edge_positions[i]
                    break
            
            # ===== 综合判断 =====
            candidates = []
            
            if gap_center_brightness > 0:
                candidates.append(('brightness', gap_center_brightness))
            
            if gap_left_edge > 0:
                # 边缘检测得到的是左边缘，加上半个缺口宽度
                candidates.append(('edge', gap_left_edge + int(width * 0.06)))
            
            if edge_peaks:
                candidates.append(('sobel', edge_peaks[0][0]))
            
            # 如果多个方法结果接近，取平均；否则取边缘检测结果
            if len(candidates) >= 2:
                # 检查一致性
                positions = [c[1] for c in candidates]
                avg_pos = sum(positions) / len(positions)
                
                # 计算方差
                variance = sum((p - avg_pos) ** 2 for p in positions) / len(positions)
                
                if variance < 400:  # 标准差 < 20px，结果一致
                    final_gap_x = int(avg_pos)
                    print(f"    🎯 缺口检测一致: {final_gap_x}px", flush=True)
                else:
                    # 结果不一致，优先使用亮度方法
                    final_gap_x = gap_center_brightness if gap_center_brightness > 0 else int(avg_pos)
                    print(f"    ⚠️ 缺口检测分歧: 使用亮度法 {final_gap_x}px", flush=True)
            elif candidates:
                final_gap_x = candidates[0][1]
                print(f"    🔍 缺口检测: {final_gap_x}px (单一方法)", flush=True)
            else:
                return 0
            
            return final_gap_x
            
        except ImportError as e:
            logger.warning(f"图像处理库未安装: {e}")
            return 0
        except Exception as e:
            logger.error(f"截图分析缺口失败: {e}")
            return 0
    
    async def _find_slider_element(self, page) -> Optional[dict]:
        """查找滑块元素"""
        try:
            # 查找可拖动的元素
            result = await page.evaluate('''() => {
                // 查找所有可能是滑块的元素
                const candidates = [];
                const allElements = document.querySelectorAll('*');
                
                for (const el of allElements) {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    
                    // 滑块通常是小方块，可拖动
                    if (rect.width >= 30 && rect.width <= 80 && 
                        rect.height >= 30 && rect.height <= 80 &&
                        el.offsetParent !== null) {
                        
                        const className = el.className || '';
                        const cursor = style.cursor;
                        
                        // 检查是否看起来像滑块
                        if (cursor === 'pointer' || cursor === 'move' || cursor === 'grab' ||
                            className.includes('slider') || className.includes('drag') || 
                            className.includes('btn') || className.includes('move')) {
                            candidates.push({
                                x: rect.x,
                                y: rect.y,
                                width: rect.width,
                                height: rect.height,
                                className: className
                            });
                        }
                    }
                }
                
                // 返回最可能的一个（通常在左边）
                if (candidates.length > 0) {
                    candidates.sort((a, b) => a.x - b.x);
                    return candidates[0];
                }
                
                return null;
            }''')
            
            return result
        except:
            return None
    
    async def _wait_for_verify_page_close(self, page, timeout: int = 60) -> bool:
        """等待验证码页面关闭（用户手动完成）"""
        import time
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # 检查页面是否还存在/可访问
                is_closed = page.is_closed()
                if is_closed:
                    print("    ✅ 验证码页面已关闭", flush=True)
                    return True
                
                # 检查 URL 是否变化
                url = page.url
                if 'verify' not in url and 'bar.cnki.net' not in url:
                    print("    ✅ 验证已通过", flush=True)
                    return True
                
                # 检查页面内容是否显示成功
                try:
                    content = await page.content()
                    if '验证成功' in content or '通过' in content:
                        print("    ✅ 验证成功", flush=True)
                        await asyncio.sleep(0.5)
                        return True
                except:
                    # 页面可能已关闭
                    print("    ✅ 验证码页面已关闭", flush=True)
                    return True
                    
            except Exception as e:
                # 页面已关闭或不可访问
                logger.debug(f"检测页面状态时出错: {e}")
                print("    ✅ 验证码页面已关闭", flush=True)
                return True
            
            remaining = int(timeout - (time.time() - start_time))
            if remaining % 15 == 0 and remaining > 0:
                print(f"    等待验证码完成... (剩余 {remaining} 秒)", flush=True)
            
            await asyncio.sleep(2)
        
        return False
    
    async def _perform_human_slide(self, page, slider_info: dict, distance: int) -> bool:
        """
        执行模拟人类的滑动操作 - 改进版
        
        更真实的人类滑动行为：
        1. 鼠标移动到滑块上
        2. 短暂停顿后按下
        3. 快速启动 -> 中速滑动 -> 减速接近 -> 微调
        4. 轻微过冲后回调
        5. 停顿后释放
        
        Args:
            page: 页面对象
            slider_info: 滑块位置信息 {x, y, width, height}
            distance: 滑动距离
            
        Returns:
            是否成功执行
        """
        import random
        import math
        
        try:
            # 滑块中心坐标
            start_x = slider_info['x'] + slider_info['width'] / 2
            start_y = slider_info['y'] + slider_info['height'] / 2
            
            # 1. 移动到滑块位置（带轻微偏移，模拟瞄准）
            approach_x = start_x + random.uniform(-3, 3)
            approach_y = start_y + random.uniform(-3, 3)
            await page.mouse.move(approach_x, approach_y)
            await asyncio.sleep(random.uniform(0.1, 0.2))
            
            # 移动到精确位置
            await page.mouse.move(start_x, start_y)
            await asyncio.sleep(random.uniform(0.05, 0.15))
            
            # 2. 按下鼠标
            await page.mouse.down()
            await asyncio.sleep(random.uniform(0.1, 0.2))
            
            # 3. 生成人类化的滑动轨迹
            trajectory = self._generate_human_trajectory(distance)
            
            # 4. 执行轨迹
            for point in trajectory:
                await page.mouse.move(
                    start_x + point['x'],
                    start_y + point['y']
                )
                await asyncio.sleep(point['delay'])
            
            # 5. 轻微过冲
            overshoot = random.uniform(2, 6)
            await page.mouse.move(
                start_x + distance + overshoot,
                start_y + random.uniform(-1, 1)
            )
            await asyncio.sleep(random.uniform(0.05, 0.1))
            
            # 6. 回调修正
            await page.mouse.move(
                start_x + distance + random.uniform(-1, 1),
                start_y + random.uniform(-0.5, 0.5)
            )
            await asyncio.sleep(random.uniform(0.08, 0.15))
            
            # 7. 释放前的短暂停顿
            await asyncio.sleep(random.uniform(0.05, 0.1))
            await page.mouse.up()
            
            return True
            
        except Exception as e:
            logger.error(f"滑动操作失败: {e}")
            return False
    
    def _generate_human_trajectory(self, distance: int) -> list:
        """
        生成人类化的滑动轨迹
        
        使用贝塞尔曲线模拟人类手部运动的加速-匀速-减速模式
        """
        import random
        import math
        
        trajectory = []
        
        # 参数设置
        total_duration = random.uniform(0.4, 0.8)  # 总时长
        num_steps = random.randint(25, 40)  # 步数
        
        # 控制点（贝塞尔曲线）
        # 起点 -> 快速加速点 -> 开始减速点 -> 终点
        control_points = [
            (0, 0),
            (distance * 0.3, random.uniform(-5, 5)),  # 快速加速
            (distance * 0.7, random.uniform(-3, 3)),  # 开始减速
            (distance, 0)
        ]
        
        for i in range(num_steps):
            t = i / (num_steps - 1)
            
            # 三次贝塞尔曲线
            # B(t) = (1-t)³P₀ + 3(1-t)²tP₁ + 3(1-t)t²P₂ + t³P₃
            x = (1-t)**3 * control_points[0][0] + \
                3 * (1-t)**2 * t * control_points[1][0] + \
                3 * (1-t) * t**2 * control_points[2][0] + \
                t**3 * control_points[3][0]
            
            y = (1-t)**3 * control_points[0][1] + \
                3 * (1-t)**2 * t * control_points[1][1] + \
                3 * (1-t) * t**2 * control_points[2][1] + \
                t**3 * control_points[3][1]
            
            # 添加微小噪声（越接近终点噪声越小）
            noise_factor = 1 - t * 0.8
            x += random.gauss(0, 0.5 * noise_factor)
            y += random.gauss(0, 1.0 * noise_factor)
            
            # 时间延迟（非均匀，开始和结束时稍慢）
            # 使用正弦函数模拟速度变化
            speed_factor = 0.5 + 0.5 * math.sin(math.pi * t)
            base_delay = total_duration / num_steps
            delay = base_delay * (0.5 + speed_factor) * random.uniform(0.8, 1.2)
            
            trajectory.append({
                'x': x,
                'y': y,
                'delay': max(0.005, delay)
            })
        
        return trajectory
    
    async def _check_captcha(self, page) -> bool:
        """
        检测页面是否出现验证码（拼图验证）
        
        Returns:
            是否存在验证码
        """
        try:
            # 使用 JavaScript 更精确地检测验证码
            # 只检测真正的拼图/滑块验证码，而不是页面上的其他元素
            has_captcha = await page.evaluate('''() => {
                // 方法1: 检查是否有遮罩层 + 验证码弹窗
                const overlay = document.querySelector('.verify-mask, .captcha-mask, .tcaptcha-overlay');
                if (overlay && overlay.offsetParent !== null) {
                    return true;
                }
                
                // 方法2: 检查是否有拼图验证的图片
                const verifyImg = document.querySelector('.verify-img-panel img, .verify-sub-block');
                if (verifyImg && verifyImg.offsetParent !== null) {
                    return true;
                }
                
                // 方法3: 检查是否有滑块验证条（必须是可见的且有一定高度）
                const verifyBar = document.querySelector('.verify-bar-area, #verify-bar-box');
                if (verifyBar && verifyBar.offsetParent !== null && verifyBar.offsetHeight > 30) {
                    return true;
                }
                
                // 方法4: 检查腾讯验证码
                const tcaptcha = document.querySelector('.tcaptcha-popup, #tcaptcha_popup');
                if (tcaptcha && tcaptcha.offsetParent !== null) {
                    return true;
                }
                
                // 方法5: 检查页面中是否有验证码 iframe
                const iframes = document.querySelectorAll('iframe');
                for (const iframe of iframes) {
                    const src = iframe.src || '';
                    if (src.includes('captcha') || src.includes('verify')) {
                        if (iframe.offsetParent !== null) {
                            return true;
                        }
                    }
                }
                
                return false;
            }''')
            
            if has_captcha:
                logger.debug('检测到验证码')
            
            return has_captcha
            
        except Exception as e:
            logger.debug(f'检测验证码时出错: {e}')
            return False
    
    async def _wait_for_captcha_solved(self, page, timeout: int = 120, auto_solve: bool = True) -> bool:
        """
        处理验证码：首先尝试自动解决，失败则等待用户手动完成
        
        Args:
            page: 页面对象
            timeout: 超时时间（秒）
            auto_solve: 是否尝试自动解决
            
        Returns:
            验证码是否已解决
        """
        import time
        
        # 首先尝试自动解决验证码
        if auto_solve:
            try:
                from .captcha_solver import solve_captcha_if_present
                
                print("    🤖 尝试自动解决拼图验证...", flush=True)
                solved = await solve_captcha_if_present(page, max_attempts=3)
                
                if solved:
                    # 验证是否真的解决了
                    await asyncio.sleep(1)
                    if not await self._check_captcha(page):
                        return True
            except ImportError as e:
                logger.warning(f"自动验证码模块不可用: {e}")
            except Exception as e:
                logger.warning(f"自动解决验证码失败: {e}")
        
        # 如果自动解决失败，等待用户手动完成
        print("    ⏳ 请手动完成验证码...", flush=True)
        
        start_time = time.time()
        check_interval = 2  # 每2秒检查一次
        
        while time.time() - start_time < timeout:
            # 检查验证码是否还存在
            captcha_exists = await self._check_captcha(page)
            
            if not captcha_exists:
                logger.info('验证码已完成')
                await asyncio.sleep(1)  # 等待页面响应
                return True
            
            # 显示剩余时间
            remaining = int(timeout - (time.time() - start_time))
            if remaining % 10 == 0:  # 每10秒提示一次
                print(f"    等待验证码完成... (剩余 {remaining} 秒)", flush=True)
            
            await asyncio.sleep(check_interval)
        
        logger.warning(f'验证码等待超时 ({timeout}秒)')
        return False

    async def _check_need_login(self, page) -> bool:
        """检查页面是否需要登录才能下载"""
        try:
            # 首先检查是否有下载按钮，如果有就不需要登录
            download_btn = await page.query_selector('#pdfDown, a.btn-dlpdf, a[id*="pdfDown"]')
            if download_btn and await download_btn.is_visible():
                return False  # 有下载按钮，不需要登录
            
            # 检查是否有明确的登录提示（在主要内容区域）
            # 只检查特定的登录提示元素，而不是整个页面内容
            login_prompts = await page.query_selector_all('.login-tip, .need-login, .login-required')
            if login_prompts:
                return True
            
            # 检查页面是否显示"请登录后下载"之类的提示
            content_area = await page.query_selector('.doc-top, .wxmain, #mainArea')
            if content_area:
                text = await content_area.inner_text()
                if '请登录' in text and '下载' in text:
                    return True
            
            return False
        except:
            return False
    
    async def _check_access_limit(self, page) -> bool:
        """
        检查页面是否显示访问次数过多/频繁等限制
        
        Returns:
            True 如果检测到访问限制
        """
        try:
            limit_detected = await page.evaluate('''() => {
                const body = document.body ? document.body.innerText : '';
                const html = document.documentElement ? document.documentElement.innerHTML : '';
                const text = body + html;
                
                // 检测常见的访问限制提示
                const limitPatterns = [
                    '访问次数',
                    '访问过于频繁',
                    '访问太频繁',
                    '请求过于频繁',
                    '操作过于频繁',
                    '频繁访问',
                    '请稍后再试',
                    '访问受限',
                    '暂时无法访问',
                    '请重新登录',
                    '登录已过期',
                    '会话已过期',
                    '繁忙',
                    '系统繁忙',
                    '服务器繁忙',
                    '当前访问人数较多',
                    '网络繁忙',
                    'session expired',
                    'too many requests',
                    'rate limit'
                ];
                
                for (const pattern of limitPatterns) {
                    if (text.toLowerCase().includes(pattern.toLowerCase())) {
                        return true;
                    }
                }
                
                return false;
            }''')
            
            return limit_detected
        except Exception as e:
            logger.debug(f'检查访问限制时出错: {e}')
            return False
    
    async def _manual_login_recovery(self, reason: str = '访问受限', quick_mode: bool = False) -> bool:
        """
        弹出浏览器让用户手动登录恢复
        
        Args:
            reason: 需要手动登录的原因
            quick_mode: 快速模式，用于IP登录环境（自动完成，减少等待时间）
            
        Returns:
            是否成功恢复
        """
        if quick_mode:
            print(f"\n    🔐 打开浏览器刷新登录状态...", flush=True)
        else:
            print(f"\n    ⚠️ {reason}，需要手动登录恢复", flush=True)
            print(f"    📢 即将打开浏览器，请手动完成登录操作...", flush=True)
        
        try:
            # 保存旧的浏览器引用
            old_browser = self.browser
            old_playwright = self.playwright
            
            # 启动新的有头浏览器
            from playwright.async_api import async_playwright
            
            # 使用新的 playwright 实例避免冲突
            new_playwright = await async_playwright().start()
            new_browser = await new_playwright.chromium.launch(
                headless=False,  # 必须显示浏览器
                slow_mo=100,
                args=self._build_chromium_launch_args(
                    headless=False,
                    start_minimized=quick_mode and self._relogin_start_minimized
                )
            )
            new_context = await new_browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
            )
            
            # 不加载旧cookies，让IP登录重新建立会话
            login_page = await self._create_page(
                new_context,
                keep_window_minimized=quick_mode and self._relogin_start_minimized
            )
            
            if quick_mode:
                # IP 登录模式：直接访问搜索页面，IP登录会自动完成
                print(f"    🌐 访问CNKI触发IP自动登录...", flush=True)
                try:
                    await login_page.goto('https://www.cnki.net/', wait_until='domcontentloaded', timeout=30000)
                    await asyncio.sleep(3)
                    
                    # 访问搜索页面确认登录状态
                    await login_page.goto('https://kns.cnki.net/kns8s/AdvSearch', wait_until='domcontentloaded', timeout=30000)
                    await asyncio.sleep(3)
                    
                    # 尝试访问一个文章详情页确认能下载（验证登录真正生效）
                    print(f"    🔍 验证登录状态...", flush=True)
                    try:
                        await login_page.goto('https://kns.cnki.net/kcms2/article/abstract?v=&dbcode=CJFQ&dbname=CJFQ&filename=JJYJ202401001', 
                                             wait_until='domcontentloaded', timeout=20000)
                        await asyncio.sleep(2)
                        
                        # 检查是否有下载按钮（真正登录成功的标志）
                        download_btn = await login_page.query_selector('#pdfDown, a.btn-dlpdf')
                        if download_btn and await download_btn.is_visible():
                            print(f"    ✅ 登录验证成功，可以下载PDF", flush=True)
                        else:
                            print(f"    ⚠️ 未检测到下载按钮，可能需要手动操作", flush=True)
                    except Exception as e:
                        logger.debug(f'验证登录时出错: {e}')
                        
                except Exception as e:
                    logger.warning(f'访问页面出错: {e}')
                    print(f"    ⚠️ 页面访问超时，尝试继续...", flush=True)
                
                # 等待更长时间确保cookies完全刷新
                print(f"    ⏳ 等待登录状态稳定...", flush=True)
                await asyncio.sleep(5)
            else:
                # 手动登录模式
                await login_page.goto('https://www.cnki.net/', wait_until='domcontentloaded', timeout=30000)
                print(f"    \n    🔐 浏览器已打开，请在浏览器中：", flush=True)
                print(f"       1. 完成登录操作", flush=True)
                print(f"       2. 如有验证码请手动完成", flush=True)
                print(f"       3. 确认登录成功后关闭浏览器窗口", flush=True)
            
            # 设置超时时间
            timeout = 45 if quick_mode else 180  # 快速模式45秒，手动模式180秒
            if not quick_mode:
                print(f"    \n    ⏳ 等待登录完成 (最长 {timeout} 秒)...", flush=True)
            
            # 等待并收集cookies
            import time
            start_time = time.time()
            success = False
            
            target_cookies = {'Ecp_ClientId', 'Ecp_SessionId', 'cnkiUserKey', 'SID_kns8', 'SID_kcms'}
            
            while time.time() - start_time < timeout:
                try:
                    # 检查浏览器是否已关闭
                    if not new_browser.is_connected():
                        print(f"    📋 浏览器已关闭，检查登录状态...", flush=True)
                        break
                    
                    # 检查是否有登录 cookie
                    cookies = await new_context.cookies()
                    cookie_names = {c.get('name') for c in cookies}
                    
                    # 检测到目标cookies
                    found_cookies = cookie_names & target_cookies
                    if found_cookies:
                        # 保存新的 cookies
                        new_cookies_dict = {c['name']: c['value'] for c in cookies if '.cnki.net' in c.get('domain', '')}
                        if new_cookies_dict:
                            with open(self.cookies_path, 'w', encoding='utf-8') as f:
                                json.dump(new_cookies_dict, f, ensure_ascii=False, indent=2)
                            print(f"    ✅ 检测到登录成功，Cookies 已保存 ({len(found_cookies)} 个关键cookie)", flush=True)
                            success = True
                            
                            # quick_mode下立即退出，不需要等待浏览器关闭
                            if quick_mode:
                                break
                            else:
                                # 手动模式继续等待用户关闭浏览器
                                break
                    
                    if not quick_mode:
                        remaining = int(timeout - (time.time() - start_time))
                        if remaining % 30 == 0 and remaining > 0:
                            print(f"    ⏳ 等待登录... (剩余 {remaining} 秒)", flush=True)
                    
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    logger.debug(f'等待登录时出错: {e}')
                    # 浏览器可能已关闭
                    break
            
            # 清理新浏览器
            try:
                await new_browser.close()
                await new_playwright.stop()
            except:
                pass
            
            if success:
                # 额外等待，让服务器端状态同步
                print(f"    ⏳ 等待服务器状态同步...", flush=True)
                await asyncio.sleep(3)
                
                # 重新初始化原来的浏览器上下文
                print(f"    🔄 重新初始化爬虫会话...", flush=True)
                
                # 关闭旧的资源
                try:
                    if old_browser:
                        await old_browser.close()
                    if old_playwright:
                        await old_playwright.stop()
                except:
                    pass
                
                # 重新启动（使用原来的 headless 设置）
                await self.start(
                    headless=self._headless_mode,
                    force_show_browser=self._force_show_browser,
                    # 可配置：重登录后的第一次恢复启动是否最小化。
                    start_minimized=self._relogin_start_minimized
                )
                print(f"    ✅ 会话恢复成功，继续爬取...", flush=True)
                return True
            else:
                print(f"    ❌ 登录超时或未完成", flush=True)
                return False
                
        except Exception as e:
            logger.error(f'手动登录恢复出错: {e}')
            print(f"    ❌ 恢复过程出错: {e}", flush=True)
            return False
    
    async def _relogin(self, max_auto_retries: int = 0) -> bool:
        """
        重新登录 - 彻底关闭浏览器并重新打开登录页面
        
        当遇到"系统繁忙"、"访问受限"等情况时调用此方法，
        直接打开浏览器登录页面（IP登录会自动完成），跳过无效的Cookie刷新尝试。
        
        使用锁确保同一时间只有一个会话在重登录，避免并发冲突。
        
        Args:
            max_auto_retries: 自动刷新最大重试次数（默认0，不尝试自动刷新）
            
        Returns:
            是否成功恢复
        """
        session_tag = f"[会话{self._session_id}]" if self._session_id is not None else ""
        
        # 使用锁确保同一时间只有一个会话在重登录
        async with CNKISearcher._relogin_lock:
            try:
                print(f"\n    🔄 {session_tag} 开始重新登录流程...", flush=True)
                logger.info(f'{session_tag} 开始重新登录...')

                # 第一步：彻底关闭现有浏览器会话，清理所有资源
                print(f"    📤 {session_tag} 关闭现有浏览器会话...", flush=True)
                await self._force_close_all()
                
                # 等待资源释放
                await asyncio.sleep(1)

                # 直接进入手动登录恢复流程（IP登录会自动完成）
                # 跳过无效的自动刷新尝试，因为IP登录环境下自动刷新不会获得登录态
                print(f"    🔐 {session_tag} 打开登录页面（IP登录将自动完成）...", flush=True)
                logger.info(f'{session_tag} 直接进入登录恢复流程')
                return await self._manual_login_recovery('需要刷新登录状态', quick_mode=True)
                
            except Exception as e:
                logger.error(f'{session_tag} 重新登录出错: {e}')
                print(f"    ❌ {session_tag} 重新登录过程出错: {e}", flush=True)
                return False
    
    async def _force_close_all(self):
        """
        强制关闭所有浏览器资源
        
        确保彻底清理，避免残留状态导致后续问题
        """
        # 关闭页面
        if self.page:
            try:
                if not self.page.is_closed():
                    await self.page.close()
            except Exception as e:
                logger.debug(f'关闭页面时出错: {e}')
            self.page = None
        
        # 关闭上下文
        if self.context:
            try:
                await self.context.close()
            except Exception as e:
                logger.debug(f'关闭上下文时出错: {e}')
            self.context = None
        
        # 关闭浏览器
        if self.browser:
            try:
                await self.browser.close()
            except Exception as e:
                logger.debug(f'关闭浏览器时出错: {e}')
            self.browser = None
        
        # 停止 Playwright
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception as e:
                logger.debug(f'停止 Playwright 时出错: {e}')
            self.playwright = None
        
        logger.info('所有浏览器资源已清理')

    def _load_progress(self, output_dir: str, journal_name: str) -> dict:
        """加载爬取进度"""
        progress_file = Path(output_dir) / 'progress.json'
        if progress_file.exists():
            try:
                with open(progress_file, 'r', encoding='utf-8') as f:
                    progress = json.load(f)
                # 检查是否是同一个期刊的进度
                if progress.get('journal') == journal_name:
                    return progress
            except:
                pass
        return None
    
    def _save_progress(self, output_dir: str, journal_name: str, year: int, page: int, 
                       article_index: int, last_filename: str = ''):
        """保存爬取进度"""
        from datetime import datetime
        progress_file = Path(output_dir) / 'progress.json'
        progress = {
            'journal': journal_name,
            'year': year,
            'page': page,
            'article_index': article_index,
            'last_filename': last_filename,
            'updated_at': datetime.now().isoformat()
        }
        try:
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(progress, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f'保存进度失败: {e}')
    
    def _clear_progress(self, output_dir: str):
        """清除进度文件（爬取完成时调用）"""
        progress_file = Path(output_dir) / 'progress.json'
        try:
            if progress_file.exists():
                progress_file.unlink()
        except:
            pass
    
    async def crawl_and_download(
        self,
        journal_name: str,
        year_start: int,
        year_end: int,
        output_dir: str = 'data',
        storage = None,
        headless: bool = False,
        download_pdf: bool = True,
        rate_limit: float = 2.0,
        relogin_start_minimized: bool = True,
        resume_from_progress: bool = False
    ) -> Dict:
        """
        完整爬取流程：搜索 -> 遍历所有页 -> 下载PDF -> 保存元数据
        
        Args:
            journal_name: 期刊名称
            year_start: 起始年份
            year_end: 结束年份
            output_dir: 输出目录
            storage: FileStorage实例（用于保存元数据和检查已爬取文章）
            headless: 是否无头模式
            download_pdf: 是否下载PDF
            rate_limit: 请求间隔（秒）
            relogin_start_minimized: 重登录恢复时是否最小化浏览器到任务栏
            resume_from_progress: 是否从 progress.json 的上次位置继续
            
        Returns:
            统计信息字典
        """
        from pathlib import Path
        
        stats = {
            'total': 0,
            'new': 0,
            'skipped': 0,
            'downloaded': 0,
            'download_failed': 0
        }
        
        max_pages = getattr(self, '_max_pages', 50)
        self._relogin_start_minimized = relogin_start_minimized
        
        # 可选加载上次进度（默认关闭，避免重启后从中间位置继续）
        saved_progress = self._load_progress(output_dir, journal_name) if resume_from_progress else None
        resume_year = None
        resume_page = None
        resume_article_idx = None
        
        if saved_progress:
            resume_year = saved_progress.get('year')
            resume_page = saved_progress.get('page')
            resume_article_idx = saved_progress.get('article_index', 0)
            last_filename = saved_progress.get('last_filename', '')
            print(f"  📌 发现上次进度: {journal_name} {resume_year}年 第{resume_page}页 第{resume_article_idx}篇", flush=True)
            print(f"     上次处理: {last_filename}", flush=True)
            print(f"     将从此处继续爬取...", flush=True)
        elif not resume_from_progress:
            print("  🔄 本次不使用断点续传：将从每个年份第一页开始。", flush=True)
        
        try:
            # 如果需要下载PDF，验证码界面需要显示浏览器，因此强制使用非headless模式
            # 其他情况跟随配置的headless设置
            force_show_browser = download_pdf
            await self.start(headless=headless, force_show_browser=force_show_browser)
            
            for year in range(year_start, year_end + 1):
                year = str(year)  # 统一转为字符串类型
                # 如果有保存的进度，跳过已完成的年份
                if resume_year is not None and int(year) < int(resume_year):
                    print(f"  ⏭️ 跳过已完成的 {year} 年", flush=True)
                    continue
                
                logger.info(f'🔍 开始爬取 {journal_name} {year}年')
                print(f"  🔍 搜索期刊中...", flush=True)
                
                # 搜索期刊
                await self.search_journal(journal_name)
                print(f"  ✓ 搜索完成，筛选年份...", flush=True)
                
                # 筛选年份
                try:
                    count = await self.filter_by_year(year)
                    logger.info(f'{year}年 共 {count} 条结果')
                    print(f"  📅 {year}年: {count} 条结果")
                except Exception as e:
                    logger.warning(f'{year}年筛选失败: {e}')
                    print(f"  ⚠️ {year}年筛选失败: {e}")
                    continue
                
                # 获取总页数
                total_pages = await self.get_total_pages()
                logger.info(f'共 {total_pages} 页')
                print(f"  📄 共 {total_pages} 页")
                
                # 年度统计
                year_total = count  # 该年总数
                year_extracted = 0  # 该年已提取（新增）
                year_skipped = 0    # 该年跳过
                year_downloaded = 0 # 该年已下载
                year_incomplete = False  # 标记该年是否因翻页失败而未完成
                last_completed_page = 0  # 记录最后完成的页码
                
                # 确定起始页码
                start_page = 1
                if resume_year is not None and year == resume_year and resume_page is not None:
                    start_page = resume_page
                    print(f"  ⏩ 从第 {start_page} 页继续...", flush=True)
                
                # 如果需要跳页，先翻到目标页
                if start_page > 1:
                    print(f"  📄 快速跳转到第 {start_page} 页...", flush=True)
                    for skip_page in range(1, start_page):
                        # 先读取当前页文章数，仅在成功翻到下一页后才计入“已跳过”
                        skip_page_count = 0
                        try:
                            skip_articles = await self.extract_articles()
                            skip_page_count = len(skip_articles)
                        except Exception as e:
                            logger.warning(f'快速跳转统计第 {skip_page} 页失败: {e}')

                        success = await self.goto_next_page()
                        if not success:
                            logger.warning(f'跳转到第 {skip_page + 1} 页失败')
                            start_page = skip_page
                            break

                        # 成功翻到下一页，当前页确认已跳过，计入统计
                        if skip_page_count > 0:
                            stats['total'] += skip_page_count
                            stats['skipped'] += skip_page_count
                            year_skipped += skip_page_count

                        year_remaining = year_total - year_extracted - year_skipped
                        print(
                            f"  📃 第 {skip_page}/{total_pages} 页: 新增 0, 跳过 {skip_page_count}, 下载 0 | 年度进度: {year_extracted + year_skipped}/{year_total} (剩余 {year_remaining})",
                            flush=True
                        )
                        last_completed_page = skip_page
                        await asyncio.sleep(0.5)
                
                # 遍历所有页
                for page_num in range(start_page, total_pages + 1):
                    if page_num > max_pages:
                        logger.warning(f'已达到最大页数限制 {max_pages}')
                        print(f"  ⚠️ 已达到最大页数限制 {max_pages}")
                        break
                    
                    logger.info(f'第 {page_num}/{total_pages} 页')
                    
                    if page_num > start_page:
                        # 翻页重试机制
                        max_page_retries = 3
                        page_success = False
                        
                        for page_retry in range(max_page_retries):
                            try:
                                # 检查主页面是否还有效
                                if self.page.is_closed():
                                    logger.warning('主页面已关闭，尝试恢复...')
                                    print(f"  ⚠️ 页面已关闭，尝试恢复...")
                                    # 重新创建页面并搜索
                                    self.page = await self._create_page(self.context)
                                    await self.search_journal(journal_name)
                                    await self.filter_by_year(year)
                                    # 跳转到当前页
                                    for skip_idx in range(page_num - 1):
                                        if not await self.goto_next_page():
                                            raise Exception(f'恢复时跳转到第 {skip_idx + 2} 页失败')
                                        await asyncio.sleep(1)
                                    page_success = True
                                    print(f"  ✓ 页面恢复成功")
                                    break
                                
                                success = await self.goto_next_page()
                                if success:
                                    page_success = True
                                    break
                                else:
                                    logger.warning(f'翻页到第 {page_num} 页失败，重试 {page_retry + 1}/{max_page_retries}')
                                    print(f"  ⚠️ 第 {page_num} 页翻页失败，重试 {page_retry + 1}/{max_page_retries}...")
                                    await asyncio.sleep(2)
                                    
                                    # 重试前尝试刷新页面状态
                                    if page_retry < max_page_retries - 1:
                                        try:
                                            # 尝试重新搜索并跳转
                                            print(f"  🔄 重新搜索并跳转到第 {page_num} 页...")
                                            await self.search_journal(journal_name)
                                            await self.filter_by_year(year)
                                            for skip_idx in range(page_num - 1):
                                                if not await self.goto_next_page():
                                                    break
                                                await asyncio.sleep(0.5)
                                            # 验证是否成功跳到目标页
                                            current = await self.page.evaluate('''() => {
                                                const curEl = document.querySelector('.pagesnums a.cur, .pages a.cur');
                                                return curEl ? parseInt(curEl.textContent) || 1 : 1;
                                            }''')
                                            if current >= page_num:
                                                page_success = True
                                                print(f"  ✓ 成功跳转到第 {current} 页")
                                                break
                                        except Exception as nav_error:
                                            logger.warning(f'重新导航失败: {nav_error}')
                                            
                            except Exception as e:
                                error_msg = str(e)
                                logger.error(f'翻页异常: {e}')
                                print(f"  ❌ 第 {page_num} 页翻页异常: {e}")
                                
                                # 如果是页面关闭错误，尝试恢复
                                if 'closed' in error_msg.lower() or 'Target' in error_msg:
                                    print(f"  🔄 尝试恢复页面 (重试 {page_retry + 1}/{max_page_retries})...")
                                    try:
                                        self.page = await self._create_page(self.context)
                                        await self.search_journal(journal_name)
                                        await self.filter_by_year(year)
                                        # 跳转到当前页
                                        for skip_idx in range(page_num - 1):
                                            if not await self.goto_next_page():
                                                raise Exception(f'跳转到第 {skip_idx + 2} 页失败')
                                            await asyncio.sleep(1)
                                        page_success = True
                                        print(f"  ✓ 页面恢复成功")
                                        break
                                    except Exception as recover_error:
                                        logger.warning(f'恢复失败: {recover_error}')
                                        if page_retry >= max_page_retries - 1:
                                            print(f"  ❌ 恢复失败: {recover_error}")
                                else:
                                    if page_retry >= max_page_retries - 1:
                                        break
                        
                        if not page_success:
                            logger.error(f'翻页到第 {page_num} 页最终失败，已完成 {last_completed_page} 页')
                            print(f"  ❌ 第 {page_num} 页翻页最终失败! 该年份标记为未完成。")
                            year_incomplete = True
                            # 保存进度，记录失败位置
                            self._save_progress(output_dir, journal_name, year, page_num, 0, '')
                            break
                    
                    # 提取当前页文章
                    try:
                        articles = await self.extract_articles()
                    except Exception as e:
                        logger.error(f'提取文章异常: {e}')
                        print(f"  ❌ 提取文章失败: {e}")
                        break
                    
                    # 本页统计
                    page_new = 0
                    page_skipped = 0
                    page_downloaded = 0
                    
                    # 连续失败计数器
                    consecutive_failures = 0
                    max_consecutive_failures = 5  # 连续失败5次后尝试重新登录
                    
                    # 确定起始文章索引
                    start_article_idx = 0
                    if (resume_year is not None and year == resume_year and 
                        resume_page is not None and page_num == resume_page and
                        resume_article_idx is not None):
                        start_article_idx = resume_article_idx
                        if start_article_idx > 0:
                            print(f"      ⏩ 跳过前 {start_article_idx} 篇已处理文章...", flush=True)
                        # 重置 resume 标记，后续页面从头开始
                        resume_article_idx = None
                    
                    for idx, article in enumerate(articles, 1):
                        # 跳过已处理的文章
                        if idx <= start_article_idx:
                            stats['total'] += 1
                            stats['skipped'] += 1
                            page_skipped += 1
                            year_skipped += 1
                            continue
                        
                        stats['total'] += 1
                        
                        # 补充信息
                        article['journal'] = journal_name
                        article['year'] = str(year)  # 统一转为字符串类型
                        article['id'] = f"{article.get('filename', '')}"
                        
                        # 构建详情页URL
                        dbcode = article.get('dbcode', 'CJFQ')
                        filename = article.get('filename', '')
                        article['url'] = f'https://kns.cnki.net/kcms2/article/abstract?v=&dbcode={dbcode}&dbname={dbcode}&filename={filename}'
                        
                        # 获取论文标题（截断显示）
                        title_short = article.get('title', '未知')[:35]
                        if len(article.get('title', '')) > 35:
                            title_short += '...'

                        # 严格校验文章真实年份，避免年份筛选失效导致越界下载
                        article_date = article.get('date', '')
                        actual_year = self._extract_year_from_date(article_date)
                        if actual_year != str(year):
                            stats['skipped'] += 1
                            page_skipped += 1
                            year_skipped += 1
                            logger.warning(
                                f'年份校验不通过，跳过: 目标年={year}, 解析年={actual_year}, 日期="{article_date}", 标题={article.get("title", "")[:30]}...'
                            )
                            self._save_progress(output_dir, journal_name, year, page_num, idx, filename)
                            continue
                        
                        # 检查是否已爬取（检查meta.json记录和PDF文件）
                        should_skip = False
                        if storage:
                            existing = storage.get_article(article['id'])
                            if existing and existing.get('downloaded'):
                                should_skip = True
                        
                        # 即使meta.json没有记录，也检查PDF文件是否已存在
                        if not should_skip and download_pdf:
                            from pathlib import Path
                            pdf_path = Path(output_dir) / 'pdf' / journal_name / str(year) / f'{filename}.pdf'
                            if pdf_path.exists():
                                should_skip = True
                                # 如果PDF存在但meta.json没记录，补充记录
                                if storage:
                                    article['downloaded'] = True
                                    article['pdf_path'] = str(pdf_path)
                                    storage.add_article(article)
                                    logger.debug(f'PDF已存在，补充元数据: {article["title"][:30]}...')
                        
                        if should_skip:
                            stats['skipped'] += 1
                            page_skipped += 1
                            year_skipped += 1
                            logger.debug(f'跳过已爬取: {article["title"][:30]}...')
                            # 保存进度（即使跳过也更新进度）
                            self._save_progress(output_dir, journal_name, year, page_num, idx, filename)
                            continue
                        
                        stats['new'] += 1
                        page_new += 1
                        year_extracted += 1
                        
                        # 下载PDF
                        if download_pdf:
                            # 显示下载中状态
                            print(f"      [{idx}/{len(articles)}] 📥 下载中: {title_short}", flush=True)
                            
                            success, path_or_msg = await self.download_pdf(article, output_dir)
                            if success:
                                stats['downloaded'] += 1
                                page_downloaded += 1
                                year_downloaded += 1
                                article['downloaded'] = True
                                article['pdf_path'] = path_or_msg
                                consecutive_failures = 0  # 重置失败计数
                                # 显示下载成功
                                print(f"      [{idx}/{len(articles)}] ✅ 下载成功: {title_short}", flush=True)
                                # 保存进度
                                self._save_progress(output_dir, journal_name, year, page_num, idx, filename)
                            else:
                                stats['download_failed'] += 1
                                article['downloaded'] = False
                                article['download_error'] = path_or_msg
                                # 显示下载失败原因
                                print(f"      [{idx}/{len(articles)}] ❌ 下载失败: {title_short} ({path_or_msg})", flush=True)
                                # 即使失败也保存进度，避免重复处理
                                self._save_progress(output_dir, journal_name, year, page_num, idx, filename)
                                
                                # 检查是否是 Cookie 已刷新的情况（自动重试）
                                if 'Cookie已刷新' in path_or_msg:
                                    print(f"      🔄 Cookie已刷新，等待5秒后重试下载...", flush=True)
                                    await asyncio.sleep(5)  # 等待更长时间确保状态同步
                                    retry_success, retry_msg = await self.download_pdf(article, output_dir)
                                    if retry_success:
                                        stats['download_failed'] -= 1
                                        stats['downloaded'] += 1
                                        page_downloaded += 1
                                        year_downloaded += 1
                                        article['downloaded'] = True
                                        article['pdf_path'] = retry_msg
                                        consecutive_failures = 0
                                        print(f"      [{idx}/{len(articles)}] ✅ 重试成功: {title_short}", flush=True)
                                    else:
                                        print(f"      [{idx}/{len(articles)}] ❌ 重试仍失败: {title_short} ({retry_msg})", flush=True)
                                        consecutive_failures += 1
                                    continue
                                
                                # 检查是否需要重新登录
                                needs_relogin = (
                                    '需要重登' in path_or_msg or 
                                    '需手动登录' in path_or_msg or
                                    '访问受限' in path_or_msg or
                                    '验证失败' in path_or_msg or
                                    '繁忙' in path_or_msg or
                                    'Timeout' in path_or_msg
                                )
                                
                                if needs_relogin:
                                    print(f"      🔄 检测到需要重新登录，尝试恢复...", flush=True)
                                    logger.info(f'触发重新登录: {path_or_msg}')
                                                                        # 重新登录（会自动判断是否需要手动登录）
                                    login_success = await self._relogin()
                                    if login_success:
                                        logger.info('重新登录成功')
                                        print(f"      ✓ 登录状态已恢复，重试下载...", flush=True)
                                        
                                        # 重试下载这篇文章
                                        await asyncio.sleep(2)
                                        retry_success, retry_msg = await self.download_pdf(article, output_dir)
                                        if retry_success:
                                            # 更新统计
                                            stats['download_failed'] -= 1
                                            stats['downloaded'] += 1
                                            page_downloaded += 1
                                            year_downloaded += 1
                                            article['downloaded'] = True
                                            article['pdf_path'] = retry_msg
                                            consecutive_failures = 0
                                            print(f"      [{idx}/{len(articles)}] ✅ 重试成功: {title_short}", flush=True)
                                        else:
                                            print(f"      [{idx}/{len(articles)}] ❌ 重试仍失败: {title_short}", flush=True)
                                            consecutive_failures += 1
                                    else:
                                        logger.warning('重新登录失败')
                                        print(f"      ❌ 登录恢复失败，跳过当前文章", flush=True)
                                        consecutive_failures += 1
                                # PDF不可用的情况不算失败（可能是会议通知、目录等）
                                elif 'PDF不可用' not in path_or_msg and '无法下载' not in path_or_msg and '无PDF' not in path_or_msg:
                                    consecutive_failures += 1
                                
                                # 连续失败太多次，尝试重新登录
                                if consecutive_failures >= max_consecutive_failures:
                                    logger.warning(f'连续下载失败 {consecutive_failures} 次，尝试重新登录...')
                                    print(f"      ⚠️ 连续失败 {consecutive_failures} 次，尝试重新登录...", flush=True)
                                    login_success = await self._relogin()
                                    if login_success:
                                        consecutive_failures = 0
                                        logger.info('重新登录成功，继续下载')
                                        print(f"      ✓ 重新登录成功，继续下载", flush=True)
                                    else:
                                        logger.error('重新登录失败，连续重试仍失败，终止程序')
                                        print(f"      🛑 连续重试失败已达 {max_consecutive_failures} 次，且重登录失败，程序即将退出。", flush=True)
                                        raise RuntimeError(
                                            f"连续重试失败达到 {max_consecutive_failures} 次且重登录失败，已终止"
                                        )
                            
                            # 下载间隔
                            await asyncio.sleep(rate_limit)
                        
                        # 保存元数据
                        if storage:
                            storage.add_article(article)
                    
                    # 标记该页完成
                    last_completed_page = page_num
                    
                    # 显示本页统计
                    year_remaining = year_total - year_extracted - year_skipped
                    print(f"  📃 第 {page_num}/{total_pages} 页: 新增 {page_new}, 跳过 {page_skipped}, 下载 {page_downloaded} | 年度进度: {year_extracted + year_skipped}/{year_total} (剩余 {year_remaining})", flush=True)
                    
                    # 页面间隔
                    await asyncio.sleep(0.5)
                
                # 年度总结 - 根据是否完整完成决定状态
                if year_incomplete:
                    print(f"  ⚠️ {year}年未完成: 新增 {year_extracted}, 跳过 {year_skipped}, 下载 {year_downloaded} (停在第 {last_completed_page}/{total_pages} 页)", flush=True)
                    logger.warning(f'⚠️ {journal_name} {year}年 未完成，停在第 {last_completed_page}/{total_pages} 页')
                    
                    # 更新期刊进度状态为 partial
                    if HAS_JOURNAL_MANAGER:
                        try:
                            progress_manager = JournalProgressManager()
                            progress_manager.update_journal_progress(
                                journal_name=journal_name,
                                year=int(year),
                                status='partial',
                                count=year_downloaded + year_skipped,
                                total=year_total
                            )
                            logger.info(f'✓ 已更新 {journal_name} {year}年 状态为 partial')
                        except Exception as e:
                            logger.warning(f'更新期刊进度失败: {e}')
                else:
                    print(f"  ✅ {year}年完成: 新增 {year_extracted}, 跳过 {year_skipped}, 下载 {year_downloaded}", flush=True)
                    logger.info(f'✅ {journal_name} {year}年 完成')
                    
                    # 更新期刊进度状态为 completed
                    if HAS_JOURNAL_MANAGER:
                        try:
                            progress_manager = JournalProgressManager()
                            progress_manager.update_journal_progress(
                                journal_name=journal_name,
                                year=int(year),
                                status='completed',
                                count=year_downloaded + year_skipped
                            )
                            logger.info(f'✓ 已更新 {journal_name} {year}年 状态为 completed')
                        except Exception as e:
                            logger.warning(f'更新期刊进度失败: {e}')
                
                # 年份完成后，重置 resume 标记
                if resume_year is not None and year == resume_year:
                    resume_year = None
                    resume_page = None
                
                # 每年结束时强制保存元数据
                if storage and hasattr(storage, 'flush'):
                    storage.flush()
            
            # 全部完成，清除进度文件
            self._clear_progress(output_dir)
            print(f"\n  🎉 爬取全部完成！进度已清除。", flush=True)
            
            return stats
            
        finally:
            # 确保所有数据都保存
            if storage and hasattr(storage, 'flush'):
                storage.flush()
            await self.close()
    
    async def search_journal_articles(
        self,
        journal_name: str,
        year_start: int,
        year_end: int,
        month_start: int = 1,
        month_end: int = 12,
        headless: bool = False
    ) -> AsyncGenerator[Dict, None]:
        """
        兼容cli.py的便捷方法：搜索期刊在指定年份范围内的所有文章
        
        Args:
            journal_name: 期刊名称
            year_start: 起始年份
            year_end: 结束年份
            month_start: 起始月份（当前实现中忽略，仅按年筛选）
            month_end: 结束月份（当前实现中忽略，仅按年筛选）
            headless: 是否无头模式
            
        Yields:
            文章字典，包含：id, title, authors, source, date, link, dbcode, filename, journal, year
        """
        max_pages = getattr(self, '_max_pages', 50)
        rate_limit = getattr(self, '_rate_limit', 2.0)
        
        try:
            await self.start(headless=headless)
            
            # 首先搜索期刊（不指定年份）
            await self.search_journal(journal_name)
            
            # 按年份遍历
            for year in range(year_start, year_end + 1):
                logger.info(f'处理 {journal_name} {year}年')
                
                # 需要重新搜索，因为每次筛选会改变结果集
                if year > year_start:
                    await self.search_journal(journal_name)
                
                # 筛选年份
                try:
                    await self.filter_by_year(year)
                except Exception as e:
                    logger.warning(f'{year}年筛选失败: {e}')
                    continue
                
                # 遍历该年份的所有文章
                article_count = 0
                async for article in self.iter_all_articles(max_pages=max_pages):
                    # 生成唯一ID
                    article['id'] = f"{article.get('dbcode', 'CJFQ')}_{article.get('filename', '')}"
                    article_count += 1
                    yield article
                    
                    # 限速
                    if rate_limit > 0:
                        await asyncio.sleep(rate_limit / 10)  # 页面内文章之间短暂延迟
                
                logger.info(f'{journal_name} {year}年: {article_count} 篇文章')
                
                # 年份之间的延迟
                if rate_limit > 0 and year < year_end:
                    await asyncio.sleep(rate_limit)
                    
        finally:
            await self.close()


async def search_journal_year(
    journal_name: str,
    year: int,
    cookies_path: str = 'cookies.json',
    headless: bool = False,
    max_pages: int = None
) -> List[Dict]:
    """
    便捷函数：搜索期刊特定年份的所有文章
    
    Args:
        journal_name: 期刊名称
        year: 年份
        cookies_path: cookies文件路径
        headless: 是否无头模式
        max_pages: 最大页数限制
        
    Returns:
        文章列表
    """
    articles = []
    
    async with CNKISearcher(cookies_path) as searcher:
        await searcher.search_journal(journal_name)
        await searcher.filter_by_year(year)
        
        async for article in searcher.iter_all_articles(max_pages):
            articles.append(article)
    
    return articles


# 测试代码
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    
    async def test():
        async with CNKISearcher('cookies.json') as searcher:
            # 搜索期刊
            await searcher.search_journal('中国语文')
            
            # 筛选年份
            await searcher.filter_by_year(2022)
            
            # 提取文章
            articles = await searcher.extract_articles()
            print(f'\n提取到 {len(articles)} 篇文章')
            
            # 获取总页数
            total_pages = await searcher.get_total_pages()
            print(f'总页数: {total_pages}')
            
            # 遍历所有页
            print('\n\n遍历所有文章...')
            count = 0
            async for art in searcher.iter_all_articles():
                count += 1
                if count <= 25:  # 只打印前25个
                    print(f'  {count}. [{art["filename"]}] {art["title"]}')
            
            print(f'\n总计: {count} 篇文章')
    
    asyncio.run(test())
