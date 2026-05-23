# -*- coding: utf-8 -*-
"""
测试脚本：分析 CNKI 拼图验证码的 HTML 结构
"""
import asyncio
from playwright.async_api import async_playwright

async def analyze_captcha():
    """分析当前页面的验证码结构"""
    async with async_playwright() as p:
        # 连接到已有的浏览器实例（如果有的话）
        # 或者打开一个新的浏览器
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        # 访问 CNKI 的一个文章页面
        print("正在打开 CNKI 页面...")
        await page.goto('https://kns.cnki.net/kcms2/article/abstract?v=YMwpULBJqz5Nj08n_OfrCfhRLPXOYa5sj5kE-8G1EQ9fI4JsK1VGlwmj0-Bnvb2UjhFBnANGo_0IhJCyqVYN7Gk7hPKPdxBQ_ZSHzBbGBQ4kNhDTyG4Z7g==&uniplatform=NZKPT')
        
        await asyncio.sleep(3)
        
        print("\n分析页面中的验证码元素...")
        
        # 查找所有可能的验证码相关元素
        result = await page.evaluate('''() => {
            const selectors = {
                // 可能的验证码容器
                containers: [
                    '.verify-img-panel',
                    '#verify-img-box',
                    '.slide-verify-panel',
                    '.tcaptcha-popup',
                    '#tcaptcha_popup',
                    '.verify-wrap',
                    '.captcha-wrap',
                    '[class*="verify"]',
                    '[class*="captcha"]',
                    '[id*="verify"]',
                    '[id*="captcha"]',
                ],
                // 可能的滑块元素
                sliders: [
                    '.verify-move-block',
                    '.verify-gap',
                    '#verify-bar-box',
                    '.slide-verify-slider',
                    '.tc-drag-thumb',
                    '[class*="slider"]',
                    '[class*="drag"]',
                ],
                // 可能的图片元素
                images: [
                    '.verify-img-panel img',
                    '.verify-sub-block img',
                    'canvas',
                ],
            };
            
            const found = {};
            
            for (const [category, selectorList] of Object.entries(selectors)) {
                found[category] = [];
                for (const sel of selectorList) {
                    const elements = document.querySelectorAll(sel);
                    elements.forEach((el, i) => {
                        const rect = el.getBoundingClientRect();
                        const isVisible = rect.width > 0 && rect.height > 0 && el.offsetParent !== null;
                        found[category].push({
                            selector: sel,
                            index: i,
                            tagName: el.tagName,
                            className: el.className,
                            id: el.id,
                            isVisible: isVisible,
                            rect: { width: rect.width, height: rect.height, top: rect.top, left: rect.left }
                        });
                    });
                }
            }
            
            // 特别检查 iframe
            const iframes = document.querySelectorAll('iframe');
            found.iframes = [];
            iframes.forEach((iframe, i) => {
                found.iframes.push({
                    index: i,
                    src: iframe.src,
                    className: iframe.className,
                    id: iframe.id,
                });
            });
            
            return found;
        }''')
        
        print("\n=== 找到的元素 ===")
        for category, elements in result.items():
            if elements:
                print(f"\n{category}:")
                for elem in elements:
                    print(f"  - {elem}")
        
        # 保存页面截图
        await page.screenshot(path='captcha_analysis.png')
        print("\n页面截图已保存为 captcha_analysis.png")
        
        # 等待用户查看
        print("\n按 Enter 键关闭浏览器...")
        input()
        
        await browser.close()

if __name__ == '__main__':
    asyncio.run(analyze_captcha())
