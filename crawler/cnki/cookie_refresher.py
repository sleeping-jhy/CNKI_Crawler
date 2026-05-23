# -*- coding: utf-8 -*-
"""
Cookie 刷新模块

实现功能：
1. 关闭现有浏览器会话
2. 打开新浏览器访问 CNKI 重新获取 Cookie
3. 彻底覆盖 cookies.json 文件
4. 支持自动刷新和手动刷新两种模式
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Dict

logger = logging.getLogger(__name__)


async def refresh_cookies_file(
    cookies_path: str = 'cookies.json',
    user_agent: str = None,
    headless: bool = True,
    timeout_seconds: int = 30,
    overwrite: bool = True
) -> bool:
    """
    彻底刷新 Cookie 文件
    
    通过打开新浏览器访问 CNKI 获取全新的 Cookie，
    适用于遇到"系统繁忙"、"访问受限"等情况后的恢复。
    
    Args:
        cookies_path: Cookie 文件路径
        user_agent: User-Agent 字符串
        headless: 是否使用无头模式
        timeout_seconds: 超时时间
        overwrite: 是否覆盖原有 Cookie（True=完全替换，False=合并）
        
    Returns:
        是否成功刷新
    """
    from playwright.async_api import async_playwright
    
    default_ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
    ua = user_agent or default_ua
    
    playwright = None
    browser = None
    
    try:
        logger.info('开始刷新 Cookie...')
        
        # 加载现有 Cookie（如果需要合并）
        existing_cookies = {}
        cookies_file = Path(cookies_path)
        if cookies_file.exists() and not overwrite:
            try:
                with open(cookies_file, 'r', encoding='utf-8') as f:
                    existing_cookies = json.load(f)
            except:
                pass
        
        # 启动全新的浏览器
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage'
            ]
        )
        
        context = await browser.new_context(user_agent=ua)
        
        # 如果不是完全覆盖，先添加旧的 Cookie
        if existing_cookies and not overwrite:
            old_cookie_list = [
                {'name': k, 'value': str(v), 'domain': '.cnki.net', 'path': '/'}
                for k, v in existing_cookies.items()
            ]
            await context.add_cookies(old_cookie_list)
        
        page = await context.new_page()
        
        # 访问 CNKI 主页获取新 Cookie
        try:
            await page.goto('https://www.cnki.net/', wait_until='domcontentloaded', timeout=timeout_seconds * 1000)
            await asyncio.sleep(2)
        except Exception as e:
            logger.warning(f'访问主页超时: {e}')
        
        # 尝试访问搜索页面触发更多 Cookie
        try:
            await page.goto('https://kns.cnki.net/kns8s/AdvSearch', wait_until='domcontentloaded', timeout=timeout_seconds * 1000)
            await asyncio.sleep(2)
        except Exception as e:
            logger.debug(f'访问搜索页面超时: {e}')
        
        # 收集新 Cookie
        new_cookies = await context.cookies()
        
        # 筛选有效的 CNKI Cookie
        new_cookies_dict = {}
        for c in new_cookies:
            domain = c.get('domain', '')
            if '.cnki.net' in domain or 'cnki.net' in domain:
                new_cookies_dict[c['name']] = c['value']
        
        if not new_cookies_dict:
            logger.warning('未获取到有效的 Cookie')
            return False
        
        # 合并或覆盖
        if overwrite:
            final_cookies = new_cookies_dict
        else:
            final_cookies = {**existing_cookies, **new_cookies_dict}
        
        # 保存到文件
        with open(cookies_path, 'w', encoding='utf-8') as f:
            json.dump(final_cookies, f, ensure_ascii=False, indent=2)
        
        logger.info(f'Cookie 刷新成功，共 {len(final_cookies)} 个 Cookie')
        
        # 检查关键 Cookie 是否存在
        key_cookies = {'Ecp_ClientId', 'Ecp_SessionId', 'cnkiUserKey', 'SID_kns8'}
        found_keys = set(final_cookies.keys()) & key_cookies
        if found_keys:
            logger.info(f'检测到有效登录状态: {found_keys}')
            return True
        else:
            logger.warning('未检测到登录 Cookie，需要重新登录')
            # 没有登录态的Cookie无法下载PDF，返回失败让上层处理
            return False
        
    except Exception as e:
        logger.error(f'刷新 Cookie 失败: {e}')
        return False
        
    finally:
        # 确保资源被清理
        if browser:
            try:
                await browser.close()
            except:
                pass
        if playwright:
            try:
                await playwright.stop()
            except:
                pass


async def force_relogin(
    cookies_path: str = 'cookies.json',
    login_url: str = 'https://www.cnki.net/',
    user_agent: str = None,
    timeout_seconds: int = 180
) -> bool:
    """
    强制重新登录（有头模式，等待用户手动操作）
    
    用于自动刷新失败后的手动恢复。
    
    Args:
        cookies_path: Cookie 文件路径
        login_url: 登录页面 URL
        user_agent: User-Agent
        timeout_seconds: 等待超时时间
        
    Returns:
        是否成功
    """
    from playwright.async_api import async_playwright
    import time
    
    default_ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
    ua = user_agent or default_ua
    
    playwright = None
    browser = None
    
    try:
        print("\n" + "=" * 60)
        print("🔐 需要手动重新登录")
        print("=" * 60)
        print("  1. 浏览器即将打开 CNKI 网站")
        print("  2. 请手动完成登录操作")
        print("  3. 登录成功后关闭浏览器窗口")
        print(f"  4. 超时时间: {timeout_seconds} 秒")
        print("=" * 60 + "\n")
        
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=False,  # 必须显示浏览器
            args=['--disable-blink-features=AutomationControlled']
        )
        
        context = await browser.new_context(user_agent=ua)
        
        # 加载现有 Cookie
        cookies_file = Path(cookies_path)
        if cookies_file.exists():
            try:
                with open(cookies_file, 'r', encoding='utf-8') as f:
                    old_cookies = json.load(f)
                cookie_list = [
                    {'name': k, 'value': str(v), 'domain': '.cnki.net', 'path': '/'}
                    for k, v in old_cookies.items()
                ]
                await context.add_cookies(cookie_list)
            except:
                pass
        
        page = await context.new_page()
        await page.goto(login_url, wait_until='domcontentloaded', timeout=30000)
        
        # 等待用户操作
        start_time = time.time()
        key_cookies = {'Ecp_ClientId', 'Ecp_SessionId', 'cnkiUserKey', 'KRSFSessionId'}
        success = False
        
        while time.time() - start_time < timeout_seconds:
            try:
                # 检查浏览器是否被关闭
                if not browser.is_connected():
                    break
                    
                # 检查 Cookie
                cookies = await context.cookies()
                names = {c['name'] for c in cookies}
                
                if names & key_cookies:
                    # 找到有效 Cookie，保存并退出
                    cookies_dict = {c['name']: c['value'] for c in cookies if '.cnki.net' in c.get('domain', '')}
                    with open(cookies_path, 'w', encoding='utf-8') as f:
                        json.dump(cookies_dict, f, ensure_ascii=False, indent=2)
                    success = True
                    print("✅ 检测到登录成功，Cookie 已保存")
                    break
                
                await asyncio.sleep(2)
                
            except Exception as e:
                # 浏览器可能已关闭
                break
        
        # 如果没有检测到登录成功但浏览器被关闭，仍然尝试保存 Cookie
        if not success:
            try:
                cookies = await context.cookies()
                if cookies:
                    cookies_dict = {c['name']: c['value'] for c in cookies if '.cnki.net' in c.get('domain', '')}
                    if cookies_dict:
                        with open(cookies_path, 'w', encoding='utf-8') as f:
                            json.dump(cookies_dict, f, ensure_ascii=False, indent=2)
                        success = True
                        print("✅ Cookie 已保存（请确认是否登录成功）")
            except:
                pass
        
        return success
        
    except Exception as e:
        logger.error(f'强制重新登录失败: {e}')
        return False
        
    finally:
        if browser:
            try:
                await browser.close()
            except:
                pass
        if playwright:
            try:
                await playwright.stop()
            except:
                pass


class CookieRefresher:
    """
    Cookie 刷新管理器
    
    提供更高级的 Cookie 管理功能，包括：
    - 定期自动刷新
    - 失败重试
    - 状态监控
    """
    
    def __init__(self, cookies_path: str = 'cookies.json'):
        self.cookies_path = cookies_path
        self._last_refresh_time = None
        self._refresh_count = 0
        self._max_auto_refresh = 3  # 自动刷新最大次数
    
    async def auto_refresh(self) -> bool:
        """
        自动刷新 Cookie（无头模式）
        
        Returns:
            是否成功
        """
        if self._refresh_count >= self._max_auto_refresh:
            logger.warning(f'自动刷新次数已达上限 ({self._max_auto_refresh})，需要手动登录')
            return False
        
        self._refresh_count += 1
        
        success = await refresh_cookies_file(
            cookies_path=self.cookies_path,
            headless=True,
            overwrite=True
        )
        
        if success:
            self._last_refresh_time = asyncio.get_event_loop().time()
            # 成功后重置计数器
            self._refresh_count = 0
        
        return success
    
    async def manual_refresh(self, timeout: int = 180) -> bool:
        """
        手动刷新 Cookie（有头模式）
        
        Args:
            timeout: 超时时间
            
        Returns:
            是否成功
        """
        success = await force_relogin(
            cookies_path=self.cookies_path,
            timeout_seconds=timeout
        )
        
        if success:
            self._last_refresh_time = asyncio.get_event_loop().time()
            self._refresh_count = 0
        
        return success
    
    def reset_counter(self):
        """重置自动刷新计数器"""
        self._refresh_count = 0
