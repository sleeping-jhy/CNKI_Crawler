import json
import pathlib
import asyncio
from typing import Optional
from playwright.async_api import async_playwright


class LoginManager:
    def __init__(self, site_url: str, cookies_path: str = "cookies.json", user_agent: Optional[str] = None):
        self.site_url = site_url
        self.cookies_path = cookies_path
        self.user_agent = user_agent

    def _save(self, cookies):
        jar = {c["name"]: c["value"] for c in cookies}
        p = pathlib.Path(self.cookies_path)
        p.write_text(json.dumps(jar, ensure_ascii=False), encoding="utf-8")

    async def manual_login(self, headless: bool = False, timeout_seconds: int = 300) -> bool:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=headless)
                context = await browser.new_context(user_agent=self.user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36")
                page = await context.new_page()
                
                try:
                    await page.goto(self.site_url, wait_until="domcontentloaded", timeout=60000)
                except Exception as e:
                    print(f"\n❌ 无法打开登录页面: {self.site_url}")
                    print(f"   错误: {e}")
                    await browser.close()
                    return False
                
                # 记录初始 cookies，用于检测登录状态变化
                # 等待页面完全加载，再记录初始 cookie 状态
                await asyncio.sleep(3)
                initial_cookies = await context.cookies()
                
                start = asyncio.get_event_loop().time()
                target = {"Ecp_ClientId", "Ecp_SessionId", "cnkiUserKey", "KRSFSessionId"}
                
                initial_target_cookies = {c["name"]: c["value"] for c in initial_cookies if c["name"] in target}
                
                print("\n⏳ 等待登录... 请在浏览器中完成登录操作")
                print("   登录成功后程序会自动检测并保存 cookies")
                print("   （如果您已登录，请刷新页面或重新访问 CNKI）\n")
                
                ok = False
                check_count = 0
                while asyncio.get_event_loop().time() - start < timeout_seconds:
                    cookies = await context.cookies()
                    current_target_cookies = {c["name"]: c["value"] for c in cookies if c["name"] in target}
                    
                    # 检测是否有新的目标 cookie，或者目标 cookie 的值发生了变化
                    new_cookies_appeared = bool(current_target_cookies.keys() - initial_target_cookies.keys())
                    cookies_value_changed = any(
                        current_target_cookies.get(name) != initial_target_cookies.get(name)
                        for name in current_target_cookies
                    )
                    
                    # 只有当 cookie 发生变化时才认为登录成功
                    if new_cookies_appeared or cookies_value_changed:
                        # 额外等待一下确保所有 cookie 都已设置
                        await asyncio.sleep(2)
                        cookies = await context.cookies()
                        self._save(cookies)
                        ok = True
                        print("✅ 检测到登录成功，cookies 已保存！")
                        break
                    
                    check_count += 1
                    if check_count % 15 == 0:  # 每 30 秒提示一次
                        elapsed = int(asyncio.get_event_loop().time() - start)
                        remaining = timeout_seconds - elapsed
                        print(f"   ⏱️  已等待 {elapsed} 秒，剩余 {remaining} 秒...")
                    
                    await asyncio.sleep(2)
                
                if not ok:
                    # 超时后仍然保存当前 cookies
                    cookies = await context.cookies()
                    if cookies:
                        self._save(cookies)
                        print("⚠️  登录超时，但已保存当前 cookies")
                        ok = True
                await browser.close()
                return ok
        except Exception as e:
            print(f"\n❌ 登录过程出错: {e}")
            return False