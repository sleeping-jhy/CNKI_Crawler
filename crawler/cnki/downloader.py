"""
CNKI PDF 下载器模块
支持通过 Playwright 和 aiohttp 下载 PDF
"""
import re
import time
import logging
import asyncio
from pathlib import Path
from typing import Optional, Dict, Tuple
from urllib.parse import urljoin, urlparse, parse_qs

import aiohttp
from playwright.async_api import async_playwright, Page, Browser

logger = logging.getLogger(__name__)


class CnkiDownloader:
    """CNKI PDF 下载器"""
    
    # 下载 API 端点
    DOWNLOAD_API = "https://kns.cnki.net/dm8/api/download"
    PDF_DOWNLOAD_URL = "https://kns.cnki.net/kcms2/download/file"
    
    def __init__(
        self,
        headers: dict = None,
        cookies: dict = None,
        rate_limit: float = 2.0,
        proxy: str = None,
        output_dir: str = "data"
    ):
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.rate_limit = rate_limit
        self.proxy = proxy
        self.output_dir = Path(output_dir)
        self._last_request = 0
        self._browser: Optional[Browser] = None
        self._playwright = None
    
    async def _rate_limit_wait(self):
        """请求速率限制"""
        now = time.time()
        elapsed = now - self._last_request
        if elapsed < self.rate_limit:
            await asyncio.sleep(self.rate_limit - elapsed)
        self._last_request = time.time()
    
    async def init_browser(self, headless: bool = True):
        """初始化 Playwright 浏览器"""
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=headless,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox'
                ]
            )
        return self._browser
    
    async def close_browser(self):
        """关闭浏览器"""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
    
    async def download_pdf_via_browser(
        self,
        article_url: str,
        save_path: str,
        headless: bool = True,
        timeout: int = 60
    ) -> Tuple[bool, str]:
        """
        通过浏览器下载 PDF（处理验证码等）
        
        Args:
            article_url: 文章详情页 URL
            save_path: 保存路径
            headless: 是否无头模式
            timeout: 超时时间（秒）
        
        Returns:
            (是否成功, 消息)
        """
        await self._rate_limit_wait()
        
        browser = await self.init_browser(headless=headless)
        context = await browser.new_context(
            user_agent=self.headers.get("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"),
            extra_http_headers={k: v for k, v in self.headers.items() if k != "User-Agent"}
        )
        
        # 设置 cookies
        if self.cookies:
            cookie_list = [
                {
                    "name": k,
                    "value": v,
                    "domain": ".cnki.net",
                    "path": "/"
                }
                for k, v in self.cookies.items()
            ]
            await context.add_cookies(cookie_list)
        
        page = await context.new_page()
        
        try:
            # 访问文章详情页
            await page.goto(article_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            await asyncio.sleep(2)
            
            # 设置下载监听
            download_path = None
            
            async def handle_download(download):
                nonlocal download_path
                # 等待下载完成
                download_path = await download.path()
            
            page.on("download", handle_download)
            
            # 查找并点击下载按钮
            download_btn = await self._find_download_button(page)
            
            if not download_btn:
                return False, "未找到下载按钮"
            
            save_path = Path(save_path)
            save_path.parent.mkdir(parents=True, exist_ok=True)

            download_save_timeout = 20
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    # 点击下载
                    async with page.expect_download(timeout=timeout * 1000) as download_info:
                        await download_btn.click()
                    
                    download = await download_info.value
                    
                    try:
                        await asyncio.wait_for(
                            download.save_as(str(save_path)),
                            timeout=download_save_timeout
                        )
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
                        if attempt < max_attempts - 1:
                            logger.warning(
                                f"下载超过{download_save_timeout}秒，已取消，重试 {attempt + 1}/{max_attempts}"
                            )
                            continue
                        return False, "下载超时-已取消"
                    
                    logger.info(f"下载成功: {save_path}")
                    return True, str(save_path)
                except Exception as e:
                    if attempt < max_attempts - 1:
                        logger.warning(f"下载失败，重试 {attempt + 1}/{max_attempts}: {e}")
                        await asyncio.sleep(1)
                        continue
                    raise
            
        except Exception as e:
            logger.error(f"浏览器下载失败: {e}")
            return False, str(e)
        finally:
            await page.close()
            await context.close()
    
    async def _find_download_button(self, page: Page):
        """查找下载按钮"""
        selectors = [
            "#pdfDown",
            "a.btn-dlpdf",
            "a[id*='pdf']",
            "a[onclick*='download']",
            "button:has-text('PDF')",
            "a:has-text('下载')",
            "#DownLoadParts a"
        ]
        
        for selector in selectors:
            try:
                btn = await page.query_selector(selector)
                if btn:
                    visible = await btn.is_visible()
                    if visible:
                        return btn
            except:
                continue
        
        return None
    
    async def download_pdf_direct(
        self,
        download_url: str,
        save_path: str,
        session: aiohttp.ClientSession = None
    ) -> Tuple[bool, str]:
        """
        直接通过 HTTP 下载 PDF
        
        Args:
            download_url: PDF 下载 URL
            save_path: 保存路径
            session: aiohttp 会话（可选）
        
        Returns:
            (是否成功, 消息)
        """
        await self._rate_limit_wait()
        
        own_session = session is None
        if own_session:
            session = aiohttp.ClientSession()
        
        try:
            # 构造 cookies 字符串
            cookie_str = "; ".join([f"{k}={v}" for k, v in self.cookies.items()])
            headers = {
                **self.headers,
                "Cookie": cookie_str
            }
            
            async with session.get(
                download_url,
                headers=headers,
                proxy=self.proxy,
                timeout=aiohttp.ClientTimeout(total=120),
                allow_redirects=True
            ) as resp:
                if resp.status != 200:
                    return False, f"HTTP {resp.status}"
                
                content_type = resp.headers.get("Content-Type", "")
                
                # 检查是否是 PDF
                if "pdf" not in content_type.lower() and "octet-stream" not in content_type.lower():
                    # 可能是 HTML 错误页面
                    text = await resp.text()
                    if "验证" in text or "登录" in text:
                        return False, "需要登录或验证"
                    return False, f"非 PDF 内容: {content_type}"
                
                # 保存文件
                save_path = Path(save_path)
                save_path.parent.mkdir(parents=True, exist_ok=True)
                
                content = await resp.read()
                
                # 验证 PDF 格式
                if not content.startswith(b"%PDF"):
                    return False, "下载内容不是有效的 PDF"
                
                with open(save_path, "wb") as f:
                    f.write(content)
                
                logger.info(f"直接下载成功: {save_path}")
                return True, str(save_path)
                
        except asyncio.TimeoutError:
            return False, "下载超时"
        except Exception as e:
            return False, str(e)
        finally:
            if own_session:
                await session.close()
    
    async def download_article(
        self,
        article: Dict,
        journal_name: str,
        use_browser: bool = True,
        headless: bool = True
    ) -> Tuple[bool, str]:
        """
        下载文章 PDF
        
        Args:
            article: 文章元数据字典
            journal_name: 期刊名称
            use_browser: 是否使用浏览器下载
            headless: 是否无头模式
        
        Returns:
            (是否成功, 保存路径或错误消息)
        """
        article_id = article.get("id", "")
        article_url = article.get("url", "")
        download_url = article.get("download_url", "")
        year = article.get("year", "unknown")
        
        if not article_id and not article_url:
            return False, "文章信息不完整"
        
        # 生成文件名
        filename = f"{article_id}.pdf" if article_id else f"{hash(article_url)}.pdf"
        save_path = self.output_dir / "pdf" / journal_name / year / filename
        
        # 检查是否已存在
        if save_path.exists():
            return True, str(save_path)
        
        # 尝试直接下载
        if download_url:
            success, msg = await self.download_pdf_direct(download_url, str(save_path))
            if success:
                return True, msg
        
        # 使用浏览器下载
        if use_browser and article_url:
            success, msg = await self.download_pdf_via_browser(
                article_url,
                str(save_path),
                headless=headless
            )
            if success:
                return True, msg
        
        return False, "所有下载方式均失败"
    
    def get_pdf_path(self, article: Dict, journal_name: str) -> Path:
        """获取文章 PDF 的保存路径"""
        article_id = article.get("id", "")
        year = article.get("year", "unknown")
        filename = f"{article_id}.pdf" if article_id else f"article.pdf"
        return self.output_dir / "pdf" / journal_name / year / filename


class DownloadManager:
    """下载管理器，支持并发下载和进度跟踪"""
    
    def __init__(
        self,
        downloader: CnkiDownloader,
        concurrency: int = 4,
        max_retries: int = 3
    ):
        self.downloader = downloader
        self.concurrency = concurrency
        self.max_retries = max_retries
        self._semaphore = asyncio.Semaphore(concurrency)
        self._stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0
        }
    
    async def download_with_retry(
        self,
        article: Dict,
        journal_name: str,
        use_browser: bool = True,
        headless: bool = True
    ) -> Tuple[bool, str]:
        """带重试的下载"""
        async with self._semaphore:
            for attempt in range(self.max_retries):
                success, msg = await self.downloader.download_article(
                    article,
                    journal_name,
                    use_browser=use_browser,
                    headless=headless
                )
                
                if success:
                    self._stats["success"] += 1
                    return True, msg
                
                if attempt < self.max_retries - 1:
                    wait_time = (attempt + 1) * 5
                    logger.warning(f"下载失败，{wait_time}秒后重试: {msg}")
                    await asyncio.sleep(wait_time)
            
            self._stats["failed"] += 1
            return False, msg
    
    async def batch_download(
        self,
        articles: list,
        journal_name: str,
        use_browser: bool = True,
        headless: bool = True,
        progress_callback=None
    ) -> Dict:
        """
        批量下载文章
        
        Args:
            articles: 文章列表
            journal_name: 期刊名称
            use_browser: 是否使用浏览器
            headless: 是否无头模式
            progress_callback: 进度回调函数
        
        Returns:
            下载统计信息
        """
        self._stats = {
            "total": len(articles),
            "success": 0,
            "failed": 0,
            "skipped": 0
        }
        
        tasks = []
        for article in articles:
            # 检查是否已下载
            pdf_path = self.downloader.get_pdf_path(article, journal_name)
            if pdf_path.exists():
                self._stats["skipped"] += 1
                if progress_callback:
                    progress_callback(self._stats)
                continue
            
            task = self.download_with_retry(
                article,
                journal_name,
                use_browser=use_browser,
                headless=headless
            )
            tasks.append(task)
        
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"下载异常: {result}")
                    self._stats["failed"] += 1
                
                if progress_callback:
                    progress_callback(self._stats)
        
        return self._stats
    
    def get_stats(self) -> Dict:
        """获取下载统计"""
        return dict(self._stats)
