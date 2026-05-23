# -*- coding: utf-8 -*-
"""
CNKI 点选文字验证码求解器

处理 "请依次点击: X Y Z" 类型的验证码
需要识别顶部提示的文字，然后在图片中按顺序点击对应位置
"""
import asyncio
import logging
import random
from io import BytesIO
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# 检查依赖库
HAS_DDDDOCR = False
HAS_IMAGE_LIBS = False

try:
    import ddddocr
    HAS_DDDDOCR = True
except ImportError:
    logger.warning("未安装 ddddocr，点选验证码自动求解功能受限")

try:
    from PIL import Image
    import numpy as np
    HAS_IMAGE_LIBS = True
except ImportError:
    pass


class ClickCaptchaSolver:
    """
    点选文字验证码求解器
    
    处理 CNKI 的 "请依次点击: 盒 5 1" 类型验证码
    """
    
    def __init__(self, page, debug: bool = False):
        """
        初始化
        
        Args:
            page: Playwright 页面对象
            debug: 是否保存调试图片
        """
        self.page = page
        self.debug = debug
        self._ocr = None
        self._det_ocr = None
        self._init_ocr()
    
    def _init_ocr(self):
        """初始化 OCR 引擎"""
        if HAS_DDDDOCR:
            try:
                # 用于识别文字
                self._ocr = ddddocr.DdddOcr(show_ad=False)
                # 用于检测文字位置（目标检测模式）
                self._det_ocr = ddddocr.DdddOcr(det=True, show_ad=False)
                logger.info("ddddocr 初始化成功")
            except Exception as e:
                logger.warning(f"ddddocr 初始化失败: {e}")
    
    async def detect_click_captcha(self) -> bool:
        """
        检测页面是否是点选文字验证码
        
        Returns:
            是否是点选验证码
        """
        try:
            is_click = await self.page.evaluate('''() => {
                const body = document.body ? document.body.innerText : '';
                return body.includes('请依次点击') || body.includes('依次点击') || 
                       body.includes('请点击') || body.includes('按顺序点击');
            }''')
            return is_click
        except:
            return False
    
    async def solve(self, max_attempts: int = 3) -> bool:
        """
        主求解流程
        
        Args:
            max_attempts: 最大尝试次数
            
        Returns:
            是否成功
        """
        for attempt in range(max_attempts):
            try:
                print(f"    🔤 点选验证码求解 第 {attempt + 1}/{max_attempts} 次...", flush=True)
                
                # 1. 等待页面加载
                await asyncio.sleep(1.0 + random.uniform(0.3, 0.6))
                
                # 2. 检查是否仍需要验证
                if not await self._is_captcha_present():
                    print(f"    ✅ 验证码已消失，验证通过", flush=True)
                    return True
                
                # 3. 获取验证码信息
                captcha_info = await self._get_captcha_info()
                if not captcha_info:
                    logger.warning("无法获取验证码信息")
                    await asyncio.sleep(1)
                    continue
                
                # 4. 获取目标文字
                target_chars = captcha_info.get('target_chars', [])
                if not target_chars:
                    print(f"    ⚠️ 未获取到目标文字，尝试从截图解析...", flush=True)
                    target_chars = await self._parse_target_from_screenshot(captcha_info)
                
                if not target_chars:
                    logger.warning("无法识别目标文字")
                    continue
                
                print(f"    🎯 需要点击: {' → '.join(target_chars)}", flush=True)
                
                # 5. 在图片中定位文字位置
                char_positions = await self._find_char_positions(captcha_info, target_chars)
                
                if len(char_positions) < len(target_chars):
                    print(f"    ⚠️ 只找到 {len(char_positions)}/{len(target_chars)} 个文字", flush=True)
                
                # 6. 按顺序点击
                if char_positions:
                    await self._click_chars_in_order(char_positions)
                    
                    # 7. 点击确定按钮
                    await asyncio.sleep(random.uniform(0.4, 0.7))
                    await self._click_confirm_button()
                    
                    # 8. 等待并检查结果
                    await asyncio.sleep(1.5 + random.uniform(0.3, 0.6))
                    
                    result = await self._check_result()
                    if result == 'success':
                        print(f"    ✅ 点选验证成功!", flush=True)
                        return True
                    elif result == 'failed':
                        print(f"    ❌ 验证失败，重试...", flush=True)
                        # 刷新验证码
                        await self._refresh_captcha()
                        await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"点选验证码求解出错: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(1)
        
        print(f"    ⏳ 自动点选失败，请手动完成...", flush=True)
        return False
    
    async def _is_captcha_present(self) -> bool:
        """检查验证码是否仍然存在"""
        try:
            if self.page.is_closed():
                return False
            url = self.page.url
            if 'verify' not in url and 'bar.cnki.net' not in url:
                return False
            return await self.detect_click_captcha()
        except:
            return False
    
    async def _get_captcha_info(self) -> Optional[Dict]:
        """获取验证码的完整信息"""
        try:
            info = await self.page.evaluate(r'''() => {
                const result = {
                    prompt_text: '',
                    target_chars: [],
                    image_box: null,
                    confirm_btn: null,
                    prompt_box: null
                };
                
                // 1. 查找提示文字
                const allText = document.body.innerText || '';
                
                // 匹配 "请依次点击：盒 5 1" 格式
                const promptMatch = allText.match(/请依次点击[：:]\s*([^\n]+)/);
                if (promptMatch) {
                    result.prompt_text = promptMatch[0];
                    const charsText = promptMatch[1].trim();
                    // 按空格或其他分隔符分割
                    result.target_chars = charsText.split(/[\s\u3000]+/).filter(c => c.length > 0);
                }
                
                // 查找提示区域的位置
                const allElements = document.querySelectorAll('*');
                for (const el of allElements) {
                    const text = (el.innerText || '').trim();
                    if (text.includes('请依次点击')) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 100) {
                            result.prompt_box = {
                                x: rect.x, y: rect.y,
                                width: rect.width, height: rect.height
                            };
                            break;
                        }
                    }
                }
                
                // 2. 查找验证码图片（通常是较大的图片）
                const images = document.querySelectorAll('img');
                for (const img of images) {
                    const rect = img.getBoundingClientRect();
                    if (rect.width >= 200 && rect.width <= 500 && 
                        rect.height >= 150 && rect.height <= 400 &&
                        img.offsetParent !== null) {
                        result.image_box = {
                            x: rect.x, y: rect.y,
                            width: rect.width, height: rect.height
                        };
                        break;
                    }
                }
                
                // 3. 查找确定按钮
                for (const el of allElements) {
                    const text = (el.innerText || '').trim();
                    const rect = el.getBoundingClientRect();
                    const tagName = el.tagName.toLowerCase();
                    
                    if ((text === '确定' || text === '验证' || text === '提交') &&
                        rect.width > 50 && rect.height > 20 &&
                        (tagName === 'button' || tagName === 'div' || tagName === 'span' || tagName === 'a')) {
                        result.confirm_btn = {
                            x: rect.x, y: rect.y,
                            width: rect.width, height: rect.height,
                            centerX: rect.x + rect.width / 2,
                            centerY: rect.y + rect.height / 2
                        };
                        break;
                    }
                }
                
                return result;
            }''')
            
            return info
            
        except Exception as e:
            logger.error(f"获取验证码信息失败: {e}")
            return None
    
    async def _parse_target_from_screenshot(self, captcha_info: Dict) -> List[str]:
        """从截图中解析目标文字"""
        if not HAS_IMAGE_LIBS or not self._ocr:
            return []
        
        try:
            prompt_box = captcha_info.get('prompt_box')
            if not prompt_box:
                return []
            
            # 截取整个页面
            screenshot_bytes = await self.page.screenshot()
            full_image = Image.open(BytesIO(screenshot_bytes))
            
            # 裁剪提示区域
            x, y = int(prompt_box['x']), int(prompt_box['y'])
            w, h = int(prompt_box['width']), int(prompt_box['height'])
            prompt_img = full_image.crop((x, y, x + w, y + h))
            
            if self.debug:
                prompt_img.save('debug_prompt.png')
            
            # OCR 识别
            img_bytes = BytesIO()
            prompt_img.save(img_bytes, format='PNG')
            text = self._ocr.classification(img_bytes.getvalue())
            
            logger.debug(f"OCR 识别提示文字: {text}")
            
            # 解析
            if '：' in text or ':' in text:
                parts = text.replace('：', ':').split(':')
                if len(parts) > 1:
                    chars_text = parts[-1].strip()
                    return [c for c in chars_text.split() if c]
            
            return []
            
        except Exception as e:
            logger.error(f"解析目标文字失败: {e}")
            return []
    
    async def _find_char_positions(self, captcha_info: Dict, target_chars: List[str]) -> List[Dict]:
        """
        在验证码图片中找到目标文字的位置
        """
        if not HAS_IMAGE_LIBS:
            return []
        
        image_box = captcha_info.get('image_box')
        if not image_box:
            logger.warning("未找到验证码图片区域")
            return []
        
        try:
            # 截取验证码图片
            screenshot_bytes = await self.page.screenshot()
            full_image = Image.open(BytesIO(screenshot_bytes))
            
            x, y = int(image_box['x']), int(image_box['y'])
            w, h = int(image_box['width']), int(image_box['height'])
            captcha_img = full_image.crop((x, y, x + w, y + h))
            
            if self.debug:
                captcha_img.save('debug_captcha_click.png')
            
            # 使用 ddddocr 检测文字位置
            if self._det_ocr:
                return self._detect_with_ddddocr(captcha_img, target_chars, x, y)
            
            # 备用方案：使用颜色分析估算位置
            return self._estimate_char_positions(captcha_img, target_chars, x, y)
            
        except Exception as e:
            logger.error(f"查找文字位置失败: {e}")
            return []
    
    def _detect_with_ddddocr(self, img: Image.Image, target_chars: List[str],
                             offset_x: int, offset_y: int) -> List[Dict]:
        """使用 ddddocr 检测文字位置"""
        try:
            # 转换图片格式
            img_bytes = BytesIO()
            img.save(img_bytes, format='PNG')
            img_data = img_bytes.getvalue()
            
            # 检测文字边界框
            bboxes = self._det_ocr.detection(img_data)
            
            if not bboxes:
                logger.warning("ddddocr 未检测到文字")
                return self._estimate_char_positions(img, target_chars, offset_x, offset_y)
            
            logger.debug(f"检测到 {len(bboxes)} 个文字区域")
            
            # 识别每个区域的文字
            detected_chars = []
            for bbox in bboxes:
                try:
                    x1, y1, x2, y2 = [int(v) for v in bbox]
                    
                    # 裁剪单个字符
                    char_img = img.crop((x1, y1, x2, y2))
                    char_bytes = BytesIO()
                    char_img.save(char_bytes, format='PNG')
                    
                    # 识别
                    char_text = self._ocr.classification(char_bytes.getvalue())
                    char_text = char_text.strip()
                    
                    if char_text:
                        center_x = offset_x + (x1 + x2) / 2
                        center_y = offset_y + (y1 + y2) / 2
                        
                        detected_chars.append({
                            'char': char_text,
                            'x': center_x,
                            'y': center_y,
                            'bbox': bbox,
                            'used': False
                        })
                        logger.debug(f"识别到文字: '{char_text}' @ ({center_x:.0f}, {center_y:.0f})")
                        
                except Exception as e:
                    logger.debug(f"识别单个字符失败: {e}")
                    continue
            
            # 按目标顺序匹配
            result = []
            for target in target_chars:
                best_match = None
                best_score = 0
                
                for detected in detected_chars:
                    if detected['used']:
                        continue
                    
                    score = self._char_similarity(target, detected['char'])
                    if score > best_score:
                        best_score = score
                        best_match = detected
                
                if best_match and best_score >= 0.5:
                    best_match['used'] = True
                    result.append({
                        'char': target,
                        'x': best_match['x'],
                        'y': best_match['y'],
                        'confidence': best_score
                    })
                    print(f"    📍 匹配 '{target}' → '{best_match['char']}' (置信度: {best_score:.0%})", flush=True)
                else:
                    print(f"    ⚠️ 未找到 '{target}' 的匹配", flush=True)
            
            return result
            
        except Exception as e:
            logger.error(f"ddddocr 检测失败: {e}")
            return self._estimate_char_positions(img, target_chars, offset_x, offset_y)
    
    def _estimate_char_positions(self, img: Image.Image, target_chars: List[str],
                                 offset_x: int, offset_y: int) -> List[Dict]:
        """
        估算文字位置（当 OCR 失败时的备用方案）
        
        基于常见的验证码文字分布模式
        """
        w, h = img.size
        
        # 文字通常分布在图片的多个区域
        # 定义一些常见的分布位置（相对坐标）
        common_positions = [
            (0.20, 0.25), (0.50, 0.20), (0.80, 0.30),  # 上部
            (0.15, 0.50), (0.50, 0.55), (0.85, 0.50),  # 中部
            (0.25, 0.75), (0.55, 0.80), (0.75, 0.70),  # 下部
        ]
        
        result = []
        for i, char in enumerate(target_chars):
            if i < len(common_positions):
                px, py = common_positions[i]
                result.append({
                    'char': char,
                    'x': offset_x + w * px,
                    'y': offset_y + h * py,
                    'confidence': 0.3  # 低置信度
                })
        
        print(f"    ⚠️ 使用估算位置（OCR 未成功）", flush=True)
        return result
    
    def _char_similarity(self, char1: str, char2: str) -> float:
        """计算两个字符的相似度"""
        if not char1 or not char2:
            return 0.0
        
        # 完全匹配
        if char1 == char2:
            return 1.0
        
        # 大小写不敏感
        if char1.lower() == char2.lower():
            return 0.95
        
        # 常见的 OCR 混淆
        confusions = {
            '0': ['O', 'o', 'Q', 'D'],
            'O': ['0', 'Q', 'D'],
            '1': ['l', 'I', 'i', '|', '!'],
            'l': ['1', 'I', 'i', '|'],
            'I': ['1', 'l', 'i', '|'],
            '2': ['Z', 'z'],
            'Z': ['2', 'z'],
            '5': ['S', 's'],
            'S': ['5', 's'],
            '6': ['b', 'G'],
            '8': ['B'],
            'B': ['8'],
            '9': ['g', 'q'],
            'g': ['9', 'q'],
        }
        
        # 检查是否是混淆字符
        c1_confusions = confusions.get(char1, [])
        c2_confusions = confusions.get(char2, [])
        
        if char2 in c1_confusions or char1 in c2_confusions:
            return 0.8
        
        # 包含关系（多字符识别结果）
        if char1 in char2 or char2 in char1:
            return 0.7
        
        return 0.0
    
    async def _click_chars_in_order(self, positions: List[Dict]) -> bool:
        """按顺序点击文字位置，模拟人类行为"""
        try:
            for i, pos in enumerate(positions):
                x, y = pos['x'], pos['y']
                char = pos.get('char', '?')
                confidence = pos.get('confidence', 1.0)
                
                print(f"    👆 点击 '{char}' @ ({int(x)}, {int(y)})", flush=True)
                
                # 添加随机偏移（模拟人类不精确点击）
                jitter_x = random.uniform(-5, 5)
                jitter_y = random.uniform(-5, 5)
                
                # 先移动到位置附近
                await self.page.mouse.move(
                    x + jitter_x + random.uniform(-20, 20),
                    y + jitter_y + random.uniform(-20, 20)
                )
                await asyncio.sleep(random.uniform(0.05, 0.15))
                
                # 移动到精确位置
                await self.page.mouse.move(x + jitter_x, y + jitter_y)
                await asyncio.sleep(random.uniform(0.05, 0.1))
                
                # 点击
                await self.page.mouse.click(x + jitter_x, y + jitter_y)
                
                # 点击间隔（模拟人类阅读和移动时间）
                if i < len(positions) - 1:
                    await asyncio.sleep(random.uniform(0.3, 0.7))
            
            return True
            
        except Exception as e:
            logger.error(f"点击文字失败: {e}")
            return False
    
    async def _click_confirm_button(self) -> bool:
        """点击确定按钮"""
        try:
            # 尝试 JavaScript 点击
            clicked = await self.page.evaluate('''() => {
                const elements = document.querySelectorAll('button, div, span, a, input');
                for (const el of elements) {
                    const text = (el.innerText || el.value || '').trim();
                    if (text === '确定' || text === '验证' || text === '提交') {
                        el.click();
                        return true;
                    }
                }
                return false;
            }''')
            
            if clicked:
                print(f"    🔘 点击确定按钮", flush=True)
                return True
            
            # 备用：通过坐标点击
            btn_info = await self.page.evaluate('''() => {
                const elements = document.querySelectorAll('*');
                for (const el of elements) {
                    const text = (el.innerText || '').trim();
                    const rect = el.getBoundingClientRect();
                    if ((text === '确定' || text === '验证') && rect.width > 50) {
                        return { x: rect.x + rect.width/2, y: rect.y + rect.height/2 };
                    }
                }
                return null;
            }''')
            
            if btn_info:
                await self.page.mouse.click(btn_info['x'], btn_info['y'])
                print(f"    🔘 点击确定按钮", flush=True)
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"点击确定按钮失败: {e}")
            return False
    
    async def _refresh_captcha(self):
        """刷新验证码"""
        try:
            # 尝试点击刷新按钮
            await self.page.evaluate('''() => {
                const refreshIcons = document.querySelectorAll('[class*="refresh"], [class*="reload"], svg, i');
                for (const icon of refreshIcons) {
                    const rect = icon.getBoundingClientRect();
                    if (rect.width > 10 && rect.width < 50) {
                        icon.click();
                        return true;
                    }
                }
                return false;
            }''')
        except:
            pass
    
    async def _check_result(self) -> str:
        """检查验证结果"""
        try:
            if self.page.is_closed():
                return 'success'
            
            url = self.page.url
            if 'verify' not in url and 'bar.cnki.net' not in url:
                return 'success'
            
            result = await self.page.evaluate('''() => {
                const body = document.body ? document.body.innerText : '';
                
                if (body.includes('验证成功') || body.includes('通过')) {
                    return 'success';
                }
                if (body.includes('失败') || body.includes('错误') || 
                    body.includes('不正确') || body.includes('重试')) {
                    return 'failed';
                }
                
                // 检查验证码是否还在
                if (!body.includes('请依次点击') && !body.includes('点击')) {
                    return 'success';
                }
                
                return 'unknown';
            }''')
            
            return result
            
        except:
            return 'success'
