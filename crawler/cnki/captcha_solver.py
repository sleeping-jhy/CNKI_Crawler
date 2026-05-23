# -*- coding: utf-8 -*-
"""
CNKI 拼图验证码自动求解模块

实现思路：
1. 检测拼图验证码的出现
2. 获取背景图片和滑块图片
3. 使用图像处理算法计算滑块需要移动的距离
4. 模拟人类滑动行为完成验证
"""
import asyncio
import base64
import logging
import random
import math
from io import BytesIO
from typing import Tuple, List, Optional

logger = logging.getLogger(__name__)

# 尝试导入图像处理库
try:
    from PIL import Image
    import numpy as np
    HAS_IMAGE_LIBS = True
except ImportError:
    HAS_IMAGE_LIBS = False
    logger.warning("未安装 PIL 或 numpy，将使用备用方案")


class CaptchaSolver:
    """CNKI 拼图验证码求解器"""
    
    # CNKI 验证码的常见选择器
    SELECTORS = {
        # 验证码容器
        'container': [
            '.verify-img-panel',           # 标准拼图验证
            '#verify-img-box',
            '.slide-verify-panel',
            '.tcaptcha-popup',             # 腾讯验证码
        ],
        # 背景图片
        'background': [
            '.verify-img-panel img:first-of-type',
            '.verify-sub-block img',
            '#verify-img-box img',
            '.tcaptcha-imgarea img',
        ],
        # 滑块图片（小拼图块）
        'slider_block': [
            '.verify-sub-block',
            '.verify-img-panel .slide-block',
            '.slide-verify-block',
        ],
        # 滑块按钮（拖动条）
        'slider_btn': [
            '.verify-move-block',
            '.verify-gap',
            '#verify-bar-box .verify-move-block',
            '.slide-verify-slider',
            '.tc-drag-thumb',              # 腾讯验证码
        ],
        # 滑块轨道
        'slider_track': [
            '.verify-bar-area',
            '#verify-bar-box',
            '.slide-verify-track',
            '.tc-drag-track',
        ],
        # 刷新按钮
        'refresh': [
            '.verify-refresh',
            '.slide-verify-refresh',
            '.tc-action-icon',
        ],
    }
    
    def __init__(self, page):
        """
        初始化验证码求解器
        
        Args:
            page: Playwright 页面对象
        """
        self.page = page
    
    async def detect_captcha(self) -> dict:
        """
        检测页面上的验证码类型和元素
        
        Returns:
            包含验证码信息的字典，如果未检测到则返回 None
        """
        result = {
            'type': None,
            'container': None,
            'background': None,
            'slider_block': None,
            'slider_btn': None,
            'slider_track': None,
        }
        
        # 检测验证码容器
        for selector in self.SELECTORS['container']:
            try:
                elem = await self.page.query_selector(selector)
                if elem and await elem.is_visible():
                    result['container'] = selector
                    result['type'] = 'slide'  # 滑动验证
                    break
            except:
                continue
        
        if not result['container']:
            return None
        
        # 检测其他元素
        for key in ['background', 'slider_block', 'slider_btn', 'slider_track']:
            for selector in self.SELECTORS[key]:
                try:
                    elem = await self.page.query_selector(selector)
                    if elem and await elem.is_visible():
                        result[key] = selector
                        break
                except:
                    continue
        
        return result
    
    async def get_captcha_images(self) -> Tuple[Optional[Image.Image], Optional[Image.Image]]:
        """
        获取验证码的背景图和滑块图
        
        Returns:
            (背景图, 滑块图) 或 (None, None)
        """
        if not HAS_IMAGE_LIBS:
            return None, None
        
        try:
            # 方法1: 尝试从 canvas 获取图片
            images = await self.page.evaluate('''() => {
                const result = { background: null, slider: null };
                
                // 尝试从 canvas 获取
                const canvases = document.querySelectorAll('.verify-img-panel canvas, .slide-verify-panel canvas');
                for (const canvas of canvases) {
                    const dataUrl = canvas.toDataURL('image/png');
                    if (canvas.width > 200) {
                        result.background = dataUrl;
                    } else {
                        result.slider = dataUrl;
                    }
                }
                
                // 如果没有 canvas，尝试从 img 获取
                if (!result.background) {
                    const bgImg = document.querySelector('.verify-img-panel img, #verify-img-box img');
                    if (bgImg && bgImg.src) {
                        result.background = bgImg.src;
                    }
                }
                
                if (!result.slider) {
                    const sliderImg = document.querySelector('.verify-sub-block img, .slide-block img');
                    if (sliderImg && sliderImg.src) {
                        result.slider = sliderImg.src;
                    }
                }
                
                return result;
            }''')
            
            bg_image = None
            slider_image = None
            
            if images.get('background'):
                bg_image = await self._load_image(images['background'])
            
            if images.get('slider'):
                slider_image = await self._load_image(images['slider'])
            
            return bg_image, slider_image
            
        except Exception as e:
            logger.error(f"获取验证码图片失败: {e}")
            return None, None
    
    async def _load_image(self, source: str) -> Optional[Image.Image]:
        """
        从 URL 或 base64 加载图片
        """
        try:
            if source.startswith('data:image'):
                # Base64 编码的图片
                header, data = source.split(',', 1)
                image_data = base64.b64decode(data)
                return Image.open(BytesIO(image_data))
            else:
                # URL，需要下载
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(source) as response:
                        image_data = await response.read()
                        return Image.open(BytesIO(image_data))
        except Exception as e:
            logger.debug(f"加载图片失败: {e}")
            return None
    
    def find_gap_position(self, bg_image: Image.Image, slider_image: Image.Image = None) -> int:
        """
        使用图像处理算法找到缺口位置
        
        Args:
            bg_image: 背景图（带缺口）
            slider_image: 滑块图（可选）
            
        Returns:
            缺口的 x 坐标位置
        """
        if not HAS_IMAGE_LIBS:
            return 0
        
        # 转换为 numpy 数组
        bg_array = np.array(bg_image.convert('L'))  # 转灰度
        
        # 方法1: 边缘检测找缺口
        gap_x = self._find_gap_by_edge(bg_array)
        
        if gap_x > 0:
            return gap_x
        
        # 方法2: 如果有滑块图，使用模板匹配
        if slider_image:
            slider_array = np.array(slider_image.convert('L'))
            gap_x = self._find_gap_by_template(bg_array, slider_array)
            if gap_x > 0:
                return gap_x
        
        # 方法3: 寻找不连续的区域
        gap_x = self._find_gap_by_discontinuity(bg_array)
        
        return gap_x
    
    def _find_gap_by_edge(self, img_array: np.ndarray) -> int:
        """
        通过边缘检测找缺口
        使用 Sobel 算子检测垂直边缘
        """
        try:
            from scipy import ndimage
            
            # Sobel 边缘检测
            sobel_x = ndimage.sobel(img_array, axis=1)
            sobel_y = ndimage.sobel(img_array, axis=0)
            edges = np.hypot(sobel_x, sobel_y)
            
            # 取每列的边缘强度之和
            edge_sum = np.sum(edges, axis=0)
            
            # 缺口通常在图片中间偏右的位置（跳过左边的滑块区域）
            start_x = int(len(edge_sum) * 0.2)  # 从 20% 位置开始
            end_x = int(len(edge_sum) * 0.85)   # 到 85% 位置结束
            
            # 在有效范围内找最大边缘变化
            search_range = edge_sum[start_x:end_x]
            
            # 使用滑动窗口找到边缘突变区域
            window_size = 5
            max_change = 0
            gap_pos = 0
            
            for i in range(len(search_range) - window_size):
                left_avg = np.mean(search_range[i:i+window_size])
                right_avg = np.mean(search_range[i+window_size:i+2*window_size]) if i+2*window_size < len(search_range) else left_avg
                change = abs(right_avg - left_avg)
                
                if change > max_change:
                    max_change = change
                    gap_pos = i + start_x
            
            return gap_pos
            
        except ImportError:
            # 如果没有 scipy，使用简化版本
            return self._find_gap_simple(img_array)
    
    def _find_gap_simple(self, img_array: np.ndarray) -> int:
        """
        简化版的缺口检测（不依赖 scipy）
        """
        # 计算每列的标准差
        col_std = np.std(img_array, axis=0)
        
        # 缺口位置通常标准差会有明显变化
        start_x = int(len(col_std) * 0.2)
        end_x = int(len(col_std) * 0.85)
        
        # 寻找标准差突变的位置
        search_range = col_std[start_x:end_x]
        diff = np.diff(search_range)
        
        # 找到变化最大的位置
        max_idx = np.argmax(np.abs(diff))
        
        return start_x + max_idx
    
    def _find_gap_by_template(self, bg_array: np.ndarray, slider_array: np.ndarray) -> int:
        """
        使用模板匹配找缺口
        """
        try:
            import cv2
            
            # 使用 OpenCV 的模板匹配
            result = cv2.matchTemplate(bg_array, slider_array, cv2.TM_CCOEFF_NORMED)
            _, _, _, max_loc = cv2.minMaxLoc(result)
            
            return max_loc[0]
        except ImportError:
            return 0
    
    def _find_gap_by_discontinuity(self, img_array: np.ndarray) -> int:
        """
        通过寻找不连续区域找缺口
        """
        # 计算相邻列的差异
        col_diff = np.sum(np.abs(np.diff(img_array, axis=1)), axis=0)
        
        # 在中间区域寻找
        start_x = int(len(col_diff) * 0.2)
        end_x = int(len(col_diff) * 0.85)
        
        search_range = col_diff[start_x:end_x]
        
        # 找到变化最大的位置
        max_idx = np.argmax(search_range)
        
        return start_x + max_idx
    
    def generate_human_track(self, distance: int, duration: float = None) -> List[dict]:
        """
        生成模拟人类的滑动轨迹
        
        采用物理模型模拟：
        1. 初始加速阶段
        2. 减速接近阶段
        3. 超过目标并回弹（Overshoot）
        4. 随机抖动
        """
        if duration is None:
            # 距离越长，时间越长，但有随机性
            duration = 0.5 + (distance / 500.0) + random.uniform(0.2, 0.5)
        
        tracks = []
        current_x = 0
        current_y = 0
        
        # 模拟物理运动
        # 阶段1: 加速和主要移动
        # 使用 tanh 函数模拟 S 型速度曲线
        steps_main = int(duration * 0.8 * 60)  # 80% 的时间用于主要移动
        
        for i in range(steps_main):
            t = i / steps_main
            # x = distance * (1 / (1 + math.exp(-10 * (t - 0.5)))) # Sigmoid
            # 使用更自然的 easeOutQuart
            progress = 1 - pow(1 - t, 4)
            
            target_x = distance * progress
            
            # y 轴抖动
            y_offset = random.uniform(-2, 2) if i % 3 == 0 else 0
            
            tracks.append({
                'x': target_x,
                'y': y_offset,
                'time': (i / 60) * 1000
            })
            
        last_time = tracks[-1]['time']
        
        # 阶段2: 超过并回弹 (Overshoot)
        overshoot_dist = random.uniform(3, 8)
        overshoot_steps = random.randint(5, 10)
        
        for i in range(overshoot_steps):
            t = i / overshoot_steps
            # 稍微超过一点
            target_x = distance + overshoot_dist * math.sin(t * math.pi)
            
            tracks.append({
                'x': target_x,
                'y': random.uniform(-1, 1),
                'time': last_time + (i + 1) * 16  # ~60fps
            })
            
        last_time = tracks[-1]['time']
        
        # 阶段3: 修正回目标位置
        correction_steps = random.randint(5, 8)
        for i in range(correction_steps):
            t = i / correction_steps
            # 从 overshoot 位置回到 distance
            start_correction = tracks[-1]['x']
            target_x = start_correction + (distance - start_correction) * t
            
            tracks.append({
                'x': target_x,
                'y': random.uniform(-1, 1),
                'time': last_time + (i + 1) * 20  # 稍微慢一点
            })
            
        return tracks
    
    async def solve(self, max_attempts: int = 3) -> bool:
        """
        自动解决拼图验证码
        
        Args:
            max_attempts: 最大尝试次数
            
        Returns:
            是否成功
        """
        for attempt in range(max_attempts):
            print(f"    🧩 拼图验证尝试 {attempt + 1}/{max_attempts}...", flush=True)
            
            try:
                # 1. 检测验证码
                captcha_info = await self.detect_captcha()
                if not captcha_info:
                    logger.info("未检测到验证码")
                    return True
                
                logger.info(f"检测到验证码: {captcha_info}")
                
                # 2. 等待验证码完全加载
                await asyncio.sleep(1.0)
                
                # 3. 获取滑动距离
                distance = await self._calculate_slide_distance()
                
                if distance <= 0:
                    logger.warning("无法计算滑动距离，尝试使用默认值")
                    distance = random.randint(150, 250)
                
                logger.info(f"计算得到滑动距离: {distance}px")
                
                # 4. 执行滑动
                success = await self._perform_slide(distance, captcha_info)
                
                if success:
                    # 5. 等待验证结果
                    # 增加等待时间，确保验证结果返回
                    await asyncio.sleep(2.0)
                    
                    # 检查验证码是否消失
                    still_exists = await self.detect_captcha()
                    
                    # 双重检查：有时候验证码消失会有延迟，或者会显示"验证失败"但元素还在
                    if not still_exists:
                        # 再等一下确认真的消失了
                        await asyncio.sleep(1.0)
                        still_exists = await self.detect_captcha()
                        
                        if not still_exists:
                            print("    ✅ 拼图验证成功!", flush=True)
                            return True
                    
                    # 如果还存在，检查是否有错误提示
                    if still_exists:
                        error_text = await self.page.evaluate('''() => {
                            const errorElem = document.querySelector('.verify-error, .verify-msg');
                            return errorElem ? errorElem.innerText : '';
                        }''')
                        if error_text:
                            logger.info(f"验证失败提示: {error_text}")
                        
                        print("    ❌ 验证未通过，重试...", flush=True)
                
                # 等待后重试
                await asyncio.sleep(1.5)
                
                # 尝试刷新验证码
                await self._refresh_captcha()
                await asyncio.sleep(1.0)
                
            except Exception as e:
                logger.error(f"验证码求解出错: {e}")
                await asyncio.sleep(1)
        
        print("    ⚠️ 拼图验证多次失败，可能需要手动处理", flush=True)
        return False
    
    async def _calculate_slide_distance(self) -> int:
        """
        计算滑块需要移动的距离
        """
        # 方法1: 尝试通过图像处理计算
        if HAS_IMAGE_LIBS:
            bg_image, slider_image = await self.get_captcha_images()
            if bg_image:
                distance = self.find_gap_position(bg_image, slider_image)
                if distance > 0:
                    # 可能需要根据实际显示尺寸进行缩放
                    scale = await self._get_image_scale()
                    return int(distance * scale)
        
        # 方法2: 通过 JavaScript 分析 canvas 或样式
        distance = await self.page.evaluate('''() => {
            // 尝试从各种属性中获取距离
            
            // 检查是否有 data 属性存储距离
            const container = document.querySelector('.verify-img-panel, .slide-verify-panel');
            if (container) {
                const gap = container.getAttribute('data-gap') || 
                            container.getAttribute('data-distance');
                if (gap) return parseInt(gap);
            }
            
            // 检查缺口元素的位置
            const gapElem = document.querySelector('.verify-gap, .slide-gap');
            if (gapElem) {
                const rect = gapElem.getBoundingClientRect();
                const containerRect = container?.getBoundingClientRect();
                if (containerRect) {
                    return rect.left - containerRect.left;
                }
            }
            
            // 尝试分析背景图片找缺口
            const bgCanvas = document.querySelector('.verify-img-panel canvas');
            if (bgCanvas) {
                const ctx = bgCanvas.getContext('2d');
                const imageData = ctx.getImageData(0, 0, bgCanvas.width, bgCanvas.height);
                const data = imageData.data;
                
                // 简单方法：找最暗的垂直区域
                const colBrightness = [];
                for (let x = 0; x < bgCanvas.width; x++) {
                    let sum = 0;
                    for (let y = 0; y < bgCanvas.height; y++) {
                        const idx = (y * bgCanvas.width + x) * 4;
                        sum += (data[idx] + data[idx+1] + data[idx+2]) / 3;
                    }
                    colBrightness.push(sum / bgCanvas.height);
                }
                
                // 在中间区域找亮度突变
                const startX = Math.floor(bgCanvas.width * 0.2);
                const endX = Math.floor(bgCanvas.width * 0.85);
                
                let maxDiff = 0;
                let gapX = 0;
                
                for (let x = startX; x < endX - 1; x++) {
                    const diff = Math.abs(colBrightness[x+1] - colBrightness[x]);
                    if (diff > maxDiff) {
                        maxDiff = diff;
                        gapX = x;
                    }
                }
                
                return gapX;
            }
            
            return 0;
        }''')
        
        return distance or 0
    
    async def _get_image_scale(self) -> float:
        """获取图片显示缩放比例"""
        try:
            scale = await self.page.evaluate('''() => {
                const canvas = document.querySelector('.verify-img-panel canvas');
                if (canvas) {
                    const rect = canvas.getBoundingClientRect();
                    return rect.width / canvas.width;
                }
                return 1;
            }''')
            return scale or 1.0
        except:
            return 1.0
    
    async def _perform_slide(self, distance: int, captcha_info: dict) -> bool:
        """
        执行滑动操作
        
        Args:
            distance: 滑动距离
            captcha_info: 验证码信息
        """
        try:
            # 找到滑块按钮
            slider_btn = None
            for selector in self.SELECTORS['slider_btn']:
                try:
                    elem = await self.page.query_selector(selector)
                    if elem and await elem.is_visible():
                        slider_btn = elem
                        break
                except:
                    continue
            
            if not slider_btn:
                logger.error("找不到滑块按钮")
                return False
            
            # 获取滑块位置
            box = await slider_btn.bounding_box()
            if not box:
                return False
            
            # 滑块中心坐标
            start_x = box['x'] + box['width'] / 2
            start_y = box['y'] + box['height'] / 2
            
            # 生成人类化的滑动轨迹
            track = self.generate_human_track(distance)
            
            # 移动到滑块位置 (带轨迹，不要瞬移)
            await self.page.mouse.move(start_x, start_y, steps=random.randint(5, 10))
            await asyncio.sleep(random.uniform(0.2, 0.4))
            
            # 按下鼠标
            await self.page.mouse.down()
            await asyncio.sleep(random.uniform(0.1, 0.3))
            
            # 按照轨迹移动
            last_time = 0
            for point in track:
                # 计算需要等待的时间
                wait_time = (point['time'] - last_time) / 1000
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                
                # 移动到新位置
                await self.page.mouse.move(
                    start_x + point['x'],
                    start_y + point['y']
                )
                last_time = point['time']
            
            # 释放鼠标前稍微停顿
            await asyncio.sleep(random.uniform(0.2, 0.5))
            await self.page.mouse.up()
            
            return True
            
        except Exception as e:
            logger.error(f"滑动操作失败: {e}")
            return False
    
    async def _refresh_captcha(self):
        """刷新验证码"""
        try:
            for selector in self.SELECTORS['refresh']:
                elem = await self.page.query_selector(selector)
                if elem and await elem.is_visible():
                    await elem.click()
                    await asyncio.sleep(0.5)
                    return
        except Exception as e:
            logger.debug(f"刷新验证码失败: {e}")


async def solve_captcha_if_present(page, max_attempts: int = 3) -> bool:
    """
    便捷函数：如果页面上存在验证码则尝试解决
    
    Args:
        page: Playwright 页面对象
        max_attempts: 最大尝试次数
        
    Returns:
        True 如果验证码已解决或不存在，False 如果解决失败
    """
    solver = CaptchaSolver(page)
    
    # 先检测是否存在验证码
    captcha_info = await solver.detect_captcha()
    
    if not captcha_info:
        return True  # 没有验证码
    
    # 存在验证码，尝试解决
    return await solver.solve(max_attempts)
