# -*- coding: utf-8 -*-
"""
测试脚本：分析 CNKI 滑动验证码的结构
"""
import asyncio
import json
from playwright.async_api import async_playwright

async def analyze_captcha():
    """分析验证码结构"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        
        # 加载 cookies
        try:
            with open('cookies.json', 'r', encoding='utf-8') as f:
                cookies_dict = json.load(f)
            
            cookie_list = [
                {'name': k, 'value': str(v), 'domain': '.cnki.net', 'path': '/'}
                for k, v in cookies_dict.items()
            ]
            await context.add_cookies(cookie_list)
            print("✓ Cookies 已加载")
        except Exception as e:
            print(f"加载 cookies 失败: {e}")
        
        page = await context.new_page()
        
        # 访问一个文章页面
        print("正在打开文章页面...")
        await page.goto('https://kns.cnki.net/kcms2/article/abstract?v=YMwpULBJqz5Nj08n_OfrCfhRLPXOYa5sj5kE-8G1EQ9fI4JsK1VGlwmj0-Bnvb2UjhFBnANGo_0IhJCyqVYN7Gk7hPKPdxBQ_ZSHzBbGBQ4=&uniplatform=NZKPT', timeout=30000)
        await asyncio.sleep(3)
        
        # 查找下载按钮
        btn = await page.query_selector('#pdfDown')
        if btn:
            print("找到下载按钮，点击触发验证码...")
            await btn.click()
            await asyncio.sleep(2)
            
            # 分析页面中的验证码元素
            result = await page.evaluate('''() => {
                const info = {
                    allElements: [],
                    possibleCaptcha: [],
                    iframes: [],
                    pageTitle: document.title,
                };
                
                // 查找所有包含特定关键词的元素
                const keywords = ['verify', 'captcha', 'slide', 'drag', 'puzzle', 'slider'];
                const allElements = document.querySelectorAll('*');
                
                allElements.forEach(el => {
                    const className = el.className || '';
                    const id = el.id || '';
                    const classStr = typeof className === 'string' ? className : '';
                    
                    for (const kw of keywords) {
                        if (classStr.toLowerCase().includes(kw) || id.toLowerCase().includes(kw)) {
                            const rect = el.getBoundingClientRect();
                            info.possibleCaptcha.push({
                                tag: el.tagName,
                                id: id,
                                class: classStr,
                                visible: rect.width > 0 && rect.height > 0,
                                rect: {
                                    x: Math.round(rect.x),
                                    y: Math.round(rect.y),
                                    w: Math.round(rect.width),
                                    h: Math.round(rect.height)
                                }
                            });
                            break;
                        }
                    }
                });
                
                // 检查 iframe
                document.querySelectorAll('iframe').forEach(iframe => {
                    info.iframes.push({
                        src: iframe.src,
                        id: iframe.id,
                        class: iframe.className
                    });
                });
                
                // 检查弹窗/遮罩层
                const overlays = document.querySelectorAll('[class*="mask"], [class*="modal"], [class*="popup"], [class*="overlay"]');
                info.overlays = [];
                overlays.forEach(el => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 100 && rect.height > 100) {
                        info.overlays.push({
                            tag: el.tagName,
                            class: el.className,
                            visible: rect.width > 0 && rect.height > 0
                        });
                    }
                });
                
                return info;
            }''')
            
            print("\n=== 验证码分析结果 ===")
            print(f"页面标题: {result['pageTitle']}")
            
            print(f"\n找到 {len(result['possibleCaptcha'])} 个可能的验证码元素:")
            for elem in result['possibleCaptcha']:
                if elem['visible']:
                    print(f"  ✓ {elem['tag']} id='{elem['id']}' class='{elem['class'][:50]}' 位置={elem['rect']}")
            
            print(f"\n找到 {len(result['iframes'])} 个 iframe:")
            for iframe in result['iframes']:
                print(f"  - src='{iframe['src'][:100]}' id='{iframe['id']}'")
            
            print(f"\n找到 {len(result.get('overlays', []))} 个遮罩/弹窗:")
            for overlay in result.get('overlays', []):
                print(f"  - {overlay['tag']} class='{overlay['class'][:50]}'")
            
            # 截图
            await page.screenshot(path='captcha_screenshot.png', full_page=False)
            print("\n截图已保存为 captcha_screenshot.png")
        else:
            print("未找到下载按钮")
        
        print("\n请查看浏览器中的验证码，按 Ctrl+C 结束...")
        try:
            await asyncio.sleep(120)
        except:
            pass
        
        await browser.close()

if __name__ == '__main__':
    asyncio.run(analyze_captcha())
