# -*- coding: utf-8 -*-
"""
CNKI 滑动验证码改进版求解器

主要改进：
1. 多算法融合的缺口检测（边缘检测 + 颜色差异 + 纹理分析）
2. 更真实的人类滑动轨迹（加入微停顿、抖动、过冲回调）
3. 自适应的滑动距离校正
"""
import asyncio
import logging
import random
import math
from typing import Tuple, List, Optional, Dict
from io import BytesIO

logger = logging.getLogger(__name__)

# 检查图像库是否可用
try:
    from PIL import Image
    import numpy as np
    HAS_IMAGE_LIBS = True
except ImportError:
    HAS_IMAGE_LIBS = False
    logger.warning("未安装 PIL 或 numpy，验证码自动求解功能受限")


class ImprovedCaptchaSolver:
    """
    改进版滑动验证码求解器
    
    专为 CNKI 的拼图滑动验证码优化
    """
    
    def __init__(self, page, debug: bool = False):
        """
        初始化求解器
        
        Args:
            page: Playwright 页面对象
            debug: 是否保存调试截图
        """
        self.page = page
        self.debug = debug
        self._attempt_history = []  # 记录历史尝试，用于智能调整
    
    async def solve(self, max_attempts: int = 6) -> bool:
        """
        主求解流程
        
        Args:
            max_attempts: 最大尝试次数
            
        Returns:
            是否成功
        """
        for attempt in range(max_attempts):
            try:
                print(f"    🧩 验证码求解 第 {attempt + 1}/{max_attempts} 次...", flush=True)
                
                # 1. 等待验证码完全加载
                await asyncio.sleep(1.0 + random.uniform(0.2, 0.5))
                
                # 2. 检测页面状态
                page_state = await self._check_page_state()
                if page_state == 'success':
                    print(f"    ✅ 验证已通过", flush=True)
                    return True
                elif page_state == 'limit':
                    print(f"    ⚠️ 检测到访问限制", flush=True)
                    return False
                elif page_state == 'no_captcha':
                    print(f"    ✅ 未检测到验证码", flush=True)
                    return True
                
                # 3. 获取验证码元素信息
                captcha_info = await self._get_captcha_elements()
                if not captcha_info or not captcha_info.get('slider'):
                    logger.warning("未找到验证码元素")
                    await asyncio.sleep(0.5)
                    continue
                
                # 4. 计算滑动距离
                distance = await self._calculate_distance(captcha_info, attempt)
                
                if distance <= 0:
                    # 使用基于历史的估算
                    distance = self._estimate_distance_from_history(captcha_info, attempt)
                
                # 确保在合理范围内
                distance = max(60, min(distance, 300))
                
                print(f"    🎯 计算滑动距离: {distance}px", flush=True)
                
                # 5. 执行滑动
                success = await self._perform_slide(captcha_info['slider'], distance)
                
                if not success:
                    continue
                
                # 6. 等待并检查结果
                await asyncio.sleep(1.2 + random.uniform(0.2, 0.5))
                
                result = await self._check_result()
                
                if result == 'success':
                    print(f"    ✅ 验证成功!", flush=True)
                    self._record_attempt(distance, True)
                    return True
                elif result == 'failed':
                    print(f"    ❌ 验证失败，调整后重试...", flush=True)
                    self._record_attempt(distance, False)
                    # 等待刷新
                    await asyncio.sleep(0.8 + random.uniform(0.2, 0.4))
                elif result == 'limit':
                    print(f"    ⚠️ 访问受限", flush=True)
                    return False
                else:
                    # unknown - 可能成功了
                    await asyncio.sleep(0.5)
                    if await self._check_page_state() == 'success':
                        return True
                
            except Exception as e:
                logger.error(f"验证码求解出错: {e}")
                await asyncio.sleep(1)
        
        return False
    
    async def _check_page_state(self) -> str:
        """
        检查当前页面状态
        
        Returns:
            'success' - 验证已通过
            'limit' - 访问受限
            'captcha' - 需要验证
            'no_captcha' - 无验证码
        """
        try:
            if self.page.is_closed():
                return 'success'
            
            url = self.page.url
            if 'verify' not in url and 'bar.cnki.net' not in url:
                return 'success'
            
            state = await self.page.evaluate('''() => {
                const body = document.body ? document.body.innerText : '';
                
                // 先检查是否有验证码元素（优先级最高）
                const hasSlider = document.querySelector('[class*="slider"], [class*="drag"], [class*="move-block"], .slide-bar, #sliderBtn');
                const hasVerifyImg = document.querySelector('img[src*="verify"], [class*="verify"] img, .captcha-img, #captchaImg');
                const hasClickCaptcha = body.includes('请依次点击') || body.includes('依次点击') || body.includes('按顺序点击');
                
                // 如果有验证码元素，就是验证码页面
                if (hasSlider || hasVerifyImg || hasClickCaptcha) {
                    return 'captcha';
                }
                
                // 检查成功
                if (body.includes('验证成功') || body.includes('验证通过') || body.includes('验证完成')) {
                    return 'success';
                }
                
                // 只有在没有验证码元素的情况下，才检查访问限制
                // 使用更严格的模式，避免误判
                const limitPatterns = [
                    '访问次数已达', '访问过于频繁', '请求过于频繁',
                    '账号已过期', '登录已过期', '会话已过期',
                    '系统繁忙，请稍后再试', '服务器繁忙'
                ];
                for (const p of limitPatterns) {
                    if (body.includes(p)) return 'limit';
                }
                
                // 页面上没有明显元素，可能需要滚动或等待
                return 'no_captcha';
            }''')
            
            return state
            
        except Exception as e:
            logger.debug(f"检查页面状态出错: {e}")
            return 'success'  # 页面可能已关闭
    
    async def _get_captcha_elements(self) -> Optional[Dict]:
        """
        获取验证码相关元素的位置信息
        """
        try:
            result = await self.page.evaluate('''() => {
                const info = {
                    slider: null,
                    image: null,
                    track: null,
                    sliderBlock: null
                };
                
                // 查找背景大图
                const images = document.querySelectorAll('img');
                for (const img of images) {
                    const rect = img.getBoundingClientRect();
                    if (rect.width > 240 && rect.width < 400 && rect.height > 100 && rect.height < 200) {
                        info.image = {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height
                        };
                        break;
                    }
                }
                
                // 查找滑块（多种方式）
                const sliderSelectors = [
                    // 方式1: 带 >> 或 » 符号的元素
                    '*',
                    // 方式2: class 包含特定关键词
                    '[class*="move-block"]',
                    '[class*="slider-btn"]',
                    '[class*="drag-btn"]',
                    '[class*="handler"]',
                ];
                
                const allElements = document.querySelectorAll('*');
                
                // 首先找带 » 符号的
                for (const el of allElements) {
                    const text = (el.textContent || '').trim();
                    const rect = el.getBoundingClientRect();
                    
                    if ((text === '»' || text === '>>') && 
                        rect.width > 25 && rect.width < 80 && 
                        rect.height > 25 && rect.height < 80 &&
                        el.offsetParent !== null) {
                        
                        info.slider = {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height,
                            centerX: rect.x + rect.width / 2,
                            centerY: rect.y + rect.height / 2
                        };
                        break;
                    }
                }
                
                // 如果没找到，用 class 匹配
                if (!info.slider) {
                    for (const el of allElements) {
                        const className = (el.className || '').toLowerCase();
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        
                        const isSliderLike = (
                            className.includes('move') ||
                            className.includes('slider') ||
                            className.includes('drag') ||
                            className.includes('handler') ||
                            className.includes('btn')
                        );
                        
                        const isRightSize = (
                            rect.width >= 30 && rect.width <= 80 &&
                            rect.height >= 30 && rect.height <= 80
                        );
                        
                        const isVisible = (
                            el.offsetParent !== null &&
                            style.display !== 'none' &&
                            style.visibility !== 'hidden'
                        );
                        
                        const isCursorPointer = (
                            style.cursor === 'pointer' ||
                            style.cursor === 'move' ||
                            style.cursor === 'grab'
                        );
                        
                        if (isSliderLike && isRightSize && isVisible) {
                            info.slider = {
                                x: rect.x,
                                y: rect.y,
                                width: rect.width,
                                height: rect.height,
                                centerX: rect.x + rect.width / 2,
                                centerY: rect.y + rect.height / 2
                            };
                            break;
                        }
                    }
                }
                
                // 如果还没找到，找位置在图片下方的小方块
                if (!info.slider && info.image) {
                    const imgBottom = info.image.y + info.image.height;
                    let bestCandidate = null;
                    let minX = Infinity;
                    
                    for (const el of allElements) {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        
                        // 在图片下方
                        const isBelow = rect.y >= imgBottom - 10 && rect.y <= imgBottom + 60;
                        // 在左侧
                        const isLeft = rect.x < info.image.x + 100;
                        // 正确的尺寸
                        const isRightSize = rect.width >= 30 && rect.width <= 80 && rect.height >= 30 && rect.height <= 80;
                        // 可见
                        const isVisible = el.offsetParent !== null;
                        // 有背景或边框
                        const hasBg = style.backgroundColor !== 'rgba(0, 0, 0, 0)' && style.backgroundColor !== 'transparent';
                        
                        if (isBelow && isLeft && isRightSize && isVisible && rect.x < minX) {
                            minX = rect.x;
                            bestCandidate = {
                                x: rect.x,
                                y: rect.y,
                                width: rect.width,
                                height: rect.height,
                                centerX: rect.x + rect.width / 2,
                                centerY: rect.y + rect.height / 2
                            };
                        }
                    }
                    
                    if (bestCandidate) {
                        info.slider = bestCandidate;
                    }
                }
                
                // 查找滑块轨道
                for (const el of allElements) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 240 && rect.width < 400 && 
                        rect.height > 25 && rect.height < 60 &&
                        el.tagName !== 'IMG' && el.offsetParent !== null) {
                        info.track = {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height
                        };
                        break;
                    }
                }
                
                return info;
            }''')
            
            return result
            
        except Exception as e:
            logger.error(f"获取验证码元素失败: {e}")
            return None
    
    async def _calculate_distance(self, captcha_info: Dict, attempt: int) -> int:
        """
        计算滑动距离
        
        使用多种方法并综合结果
        """
        if not HAS_IMAGE_LIBS:
            return 0
        
        image_info = captcha_info.get('image')
        slider_info = captcha_info.get('slider')
        
        if not image_info:
            return 0
        
        try:
            # 截取页面获取验证码图片
            screenshot_bytes = await self.page.screenshot()
            full_image = Image.open(BytesIO(screenshot_bytes))
            
            # 裁剪验证码区域
            img_x = int(image_info['x'])
            img_y = int(image_info['y'])
            img_width = int(image_info['width'])
            img_height = int(image_info['height'])
            
            crop_box = (img_x, img_y, img_x + img_width, img_y + img_height)
            captcha_img = full_image.crop(crop_box)
            
            if self.debug:
                captcha_img.save(f'debug_captcha_{attempt}.png')
            
            # 转换为 numpy 数组
            img_array = np.array(captcha_img.convert('RGB'))
            
            # 使用多种方法检测缺口
            gap_positions = []
            
            # 方法1: 边缘检测
            edge_gap = self._detect_gap_by_edge(img_array)
            if edge_gap > 0:
                gap_positions.append(('edge', edge_gap))
            
            # 方法2: 颜色异常检测
            color_gap = self._detect_gap_by_color(img_array)
            if color_gap > 0:
                gap_positions.append(('color', color_gap))
            
            # 方法3: 列亮度变化检测
            brightness_gap = self._detect_gap_by_brightness(img_array)
            if brightness_gap > 0:
                gap_positions.append(('brightness', brightness_gap))
            
            # 方法4: 垂直线连续性检测
            continuity_gap = self._detect_gap_by_continuity(img_array)
            if continuity_gap > 0:
                gap_positions.append(('continuity', continuity_gap))
            
            if not gap_positions:
                return 0
            
            # 综合判断
            # 如果多个方法结果接近，取平均；否则取边缘检测结果
            positions = [p[1] for p in gap_positions]
            avg_pos = sum(positions) / len(positions)
            
            # 计算方差
            if len(positions) > 1:
                variance = sum((p - avg_pos) ** 2 for p in positions) / len(positions)
                std_dev = math.sqrt(variance)
                
                if std_dev < 25:  # 结果一致性好
                    final_gap_x = int(avg_pos)
                else:
                    # 使用加权平均，边缘检测权重更高
                    weights = {'edge': 1.5, 'color': 1.2, 'brightness': 1.0, 'continuity': 0.8}
                    weighted_sum = sum(p[1] * weights.get(p[0], 1.0) for p in gap_positions)
                    weight_total = sum(weights.get(p[0], 1.0) for p in gap_positions)
                    final_gap_x = int(weighted_sum / weight_total)
            else:
                final_gap_x = int(positions[0])
            
            # 计算滑动距离
            # 需要考虑滑块初始位置相对于图片的偏移
            if slider_info:
                slider_offset_x = slider_info['x'] - img_x
                # 滑块中心到缺口中心的距离
                slider_center = slider_info['width'] / 2
                gap_width = img_width * 0.12  # 缺口宽度约为图片宽度的 12%
                
                distance = final_gap_x - slider_offset_x - slider_center + gap_width / 2
            else:
                # 没有滑块信息，直接使用缺口位置减去估计的滑块起始位置
                distance = final_gap_x - img_width * 0.05
            
            # 根据历史调整
            distance = self._adjust_by_history(distance, attempt)
            
            return int(distance)
            
        except Exception as e:
            logger.error(f"计算滑动距离失败: {e}")
            return 0
    
    def _detect_gap_by_edge(self, img_array: np.ndarray) -> int:
        """
        边缘检测方法
        使用 Sobel 算子检测垂直边缘
        """
        try:
            height, width = img_array.shape[:2]
            
            # 转灰度
            gray = np.mean(img_array, axis=2).astype(np.float32)
            
            # Sobel X（检测垂直边缘）
            sobel_x = np.zeros_like(gray)
            for y in range(1, height - 1):
                for x in range(1, width - 1):
                    gx = (
                        -gray[y-1, x-1] + gray[y-1, x+1] +
                        -2*gray[y, x-1] + 2*gray[y, x+1] +
                        -gray[y+1, x-1] + gray[y+1, x+1]
                    )
                    sobel_x[y, x] = abs(gx)
            
            # 只在中间区域搜索（跳过左边滑块和右边边缘）
            search_start = int(width * 0.25)
            search_end = int(width * 0.85)
            
            # 计算每列的边缘强度
            col_edge = np.sum(sobel_x[int(height*0.1):int(height*0.9), :], axis=0)
            
            # 使用滑动窗口找到边缘强度突变的位置
            window_size = int(width * 0.06)  # 约为缺口宽度的一半
            max_diff = 0
            gap_left = 0
            
            for x in range(search_start, search_end - window_size):
                # 左侧和右侧的边缘强度差异
                left_sum = np.sum(col_edge[x:x+window_size])
                right_sum = np.sum(col_edge[x+window_size:x+2*window_size]) if x + 2*window_size < width else left_sum
                
                diff = abs(left_sum - right_sum)
                if diff > max_diff:
                    max_diff = diff
                    gap_left = x
            
            # 返回缺口中心位置
            return gap_left + window_size // 2
            
        except Exception as e:
            logger.debug(f"边缘检测失败: {e}")
            return 0
    
    def _detect_gap_by_color(self, img_array: np.ndarray) -> int:
        """
        颜色异常检测方法
        缺口区域通常颜色较暗或与周围不同
        """
        try:
            height, width = img_array.shape[:2]
            
            # 计算每列的颜色均值和标准差
            col_mean = np.mean(img_array, axis=(0, 2))
            col_std = np.std(img_array, axis=(0, 2))
            
            # 搜索范围
            search_start = int(width * 0.25)
            search_end = int(width * 0.85)
            
            # 使用滑动窗口找到颜色异常区域
            window_size = int(width * 0.12)
            min_mean = float('inf')
            gap_center = 0
            
            for x in range(search_start, search_end - window_size):
                window_mean = np.mean(col_mean[x:x+window_size])
                window_std = np.mean(col_std[x:x+window_size])
                
                # 缺口区域通常更暗且标准差较低
                score = window_mean - window_std * 0.5
                
                if score < min_mean:
                    min_mean = score
                    gap_center = x + window_size // 2
            
            return gap_center
            
        except Exception as e:
            logger.debug(f"颜色检测失败: {e}")
            return 0
    
    def _detect_gap_by_brightness(self, img_array: np.ndarray) -> int:
        """
        亮度变化检测方法
        """
        try:
            height, width = img_array.shape[:2]
            
            # 转灰度
            gray = np.mean(img_array, axis=2)
            
            # 只看中间区域
            middle_region = gray[int(height*0.2):int(height*0.8), :]
            
            # 计算每列亮度
            col_brightness = np.mean(middle_region, axis=0)
            
            # 搜索范围
            search_start = int(width * 0.25)
            search_end = int(width * 0.85)
            
            # 找到最暗的连续区域
            window_size = int(width * 0.10)
            min_brightness = float('inf')
            gap_center = 0
            
            for x in range(search_start, search_end - window_size):
                window_brightness = np.mean(col_brightness[x:x+window_size])
                if window_brightness < min_brightness:
                    min_brightness = window_brightness
                    gap_center = x + window_size // 2
            
            return gap_center
            
        except Exception as e:
            logger.debug(f"亮度检测失败: {e}")
            return 0
    
    def _detect_gap_by_continuity(self, img_array: np.ndarray) -> int:
        """
        垂直连续性检测方法
        缺口处垂直方向的像素连续性会被打断
        """
        try:
            height, width = img_array.shape[:2]
            
            # 转灰度
            gray = np.mean(img_array, axis=2)
            
            # 计算每列相邻像素的差异
            col_discontinuity = np.zeros(width)
            for x in range(width):
                col_diff = np.abs(np.diff(gray[:, x]))
                col_discontinuity[x] = np.sum(col_diff)
            
            # 搜索范围
            search_start = int(width * 0.25)
            search_end = int(width * 0.85)
            
            # 找不连续性最高的区域
            window_size = int(width * 0.12)
            max_discontinuity = 0
            gap_center = 0
            
            for x in range(search_start, search_end - window_size):
                window_disc = np.mean(col_discontinuity[x:x+window_size])
                if window_disc > max_discontinuity:
                    max_discontinuity = window_disc
                    gap_center = x + window_size // 2
            
            return gap_center
            
        except Exception as e:
            logger.debug(f"连续性检测失败: {e}")
            return 0
    
    def _estimate_distance_from_history(self, captcha_info: Dict, attempt: int) -> int:
        """
        根据历史记录和当前尝试次数估算距离
        """
        image_info = captcha_info.get('image', {})
        img_width = image_info.get('width', 280)
        
        # 基础位置分布（根据 CNKI 验证码的常见缺口位置）
        base_positions = [0.45, 0.52, 0.58, 0.48, 0.55, 0.62]
        
        # 根据尝试次数选择位置
        pos = base_positions[attempt % len(base_positions)]
        
        # 添加随机偏移
        pos += random.uniform(-0.05, 0.05)
        
        return int(img_width * pos)
    
    def _adjust_by_history(self, distance: int, attempt: int) -> int:
        """
        根据历史尝试调整距离
        """
        if not self._attempt_history:
            return distance
        
        # 分析历史失败记录
        failed_distances = [h['distance'] for h in self._attempt_history if not h['success']]
        
        if failed_distances:
            # 如果当前距离与失败记录接近，调整一下
            for fd in failed_distances:
                if abs(distance - fd) < 20:
                    # 根据尝试次数决定调整方向
                    adjustment = (-1) ** attempt * (15 + attempt * 3)
                    distance += adjustment
                    break
        
        return distance
    
    def _record_attempt(self, distance: int, success: bool):
        """记录尝试结果"""
        self._attempt_history.append({
            'distance': distance,
            'success': success
        })
        # 只保留最近 10 次记录
        if len(self._attempt_history) > 10:
            self._attempt_history.pop(0)
    
    async def _perform_slide(self, slider_info: Dict, distance: int) -> bool:
        """
        执行滑动操作
        
        模拟真实人类的滑动行为
        """
        try:
            start_x = slider_info['centerX']
            start_y = slider_info['centerY']
            
            # 1. 移动到滑块（带随机偏移模拟瞄准）
            approach_x = start_x + random.uniform(-5, 5)
            approach_y = start_y + random.uniform(-5, 5)
            await self.page.mouse.move(approach_x, approach_y)
            await asyncio.sleep(random.uniform(0.08, 0.15))
            
            # 精确定位
            await self.page.mouse.move(start_x, start_y)
            await asyncio.sleep(random.uniform(0.05, 0.12))
            
            # 2. 按下鼠标（模拟按压时间）
            await self.page.mouse.down()
            await asyncio.sleep(random.uniform(0.08, 0.18))
            
            # 3. 生成并执行滑动轨迹
            trajectory = self._generate_trajectory(distance)
            
            for point in trajectory:
                await self.page.mouse.move(
                    start_x + point['x'],
                    start_y + point['y']
                )
                if point['delay'] > 0:
                    await asyncio.sleep(point['delay'])
            
            # 4. 过冲 (overshoot)
            overshoot = random.uniform(2, 8)
            await self.page.mouse.move(
                start_x + distance + overshoot,
                start_y + random.uniform(-2, 2)
            )
            await asyncio.sleep(random.uniform(0.04, 0.1))
            
            # 5. 回调修正
            await self.page.mouse.move(
                start_x + distance + random.uniform(-1, 1),
                start_y + random.uniform(-1, 1)
            )
            await asyncio.sleep(random.uniform(0.06, 0.12))
            
            # 6. 释放
            await asyncio.sleep(random.uniform(0.03, 0.08))
            await self.page.mouse.up()
            
            return True
            
        except Exception as e:
            logger.error(f"滑动执行失败: {e}")
            return False
    
    def _generate_trajectory(self, distance: int) -> List[Dict]:
        """
        生成人类化的滑动轨迹
        
        使用三阶贝塞尔曲线 + 噪声模拟人类手部运动
        """
        trajectory = []
        
        # 总时间和步数
        total_duration = random.uniform(0.35, 0.7)
        num_steps = random.randint(20, 35)
        
        # 贝塞尔曲线控制点
        # P0: 起点
        # P1: 快速启动阶段
        # P2: 减速阶段
        # P3: 终点
        p0 = (0, 0)
        p1 = (distance * random.uniform(0.25, 0.35), random.uniform(-8, 8))
        p2 = (distance * random.uniform(0.65, 0.75), random.uniform(-5, 5))
        p3 = (distance, 0)
        
        for i in range(num_steps):
            t = i / (num_steps - 1)
            
            # 三阶贝塞尔曲线
            x = (1-t)**3 * p0[0] + 3*(1-t)**2*t * p1[0] + 3*(1-t)*t**2 * p2[0] + t**3 * p3[0]
            y = (1-t)**3 * p0[1] + 3*(1-t)**2*t * p1[1] + 3*(1-t)*t**2 * p2[1] + t**3 * p3[1]
            
            # 添加高斯噪声（接近终点时减少噪声）
            noise_factor = max(0.1, 1 - t * 0.9)
            x += random.gauss(0, 0.8 * noise_factor)
            y += random.gauss(0, 1.5 * noise_factor)
            
            # 时间延迟（使用正弦模拟速度变化：开始快、中间平稳、结束慢）
            base_delay = total_duration / num_steps
            speed_curve = 0.6 + 0.4 * math.sin(math.pi * t)  # 0.6 到 1.0
            delay = base_delay * (2 - speed_curve) * random.uniform(0.85, 1.15)
            
            # 随机微停顿（模拟人类的微调）
            if random.random() < 0.08 and 0.3 < t < 0.7:
                delay += random.uniform(0.02, 0.06)
            
            trajectory.append({
                'x': x,
                'y': y,
                'delay': max(0.003, delay)
            })
        
        return trajectory
    
    async def _check_result(self) -> str:
        """
        检查验证结果
        
        Returns:
            'success' - 成功
            'failed' - 失败
            'limit' - 访问受限
            'unknown' - 未知
        """
        try:
            if self.page.is_closed():
                return 'success'
            
            url = self.page.url
            if 'verify' not in url and 'bar.cnki.net' not in url:
                return 'success'
            
            result = await self.page.evaluate('''() => {
                const body = document.body ? document.body.innerText : '';
                
                if (body.includes('成功') || body.includes('通过') || body.includes('验证完成')) {
                    return 'success';
                }
                
                if (body.includes('失败') || body.includes('错误') || 
                    body.includes('重试') || body.includes('再试一次')) {
                    return 'failed';
                }
                
                const limitPatterns = ['访问次数', '频繁', '请稍后', '繁忙', '受限'];
                for (const p of limitPatterns) {
                    if (body.includes(p)) return 'limit';
                }
                
                return 'unknown';
            }''')
            
            return result
            
        except Exception as e:
            logger.debug(f"检查结果出错: {e}")
            return 'unknown'


async def solve_captcha(page, max_attempts: int = 6, debug: bool = False) -> bool:
    """
    便捷函数：求解验证码
    
    Args:
        page: Playwright 页面对象
        max_attempts: 最大尝试次数
        debug: 是否开启调试模式
        
    Returns:
        是否成功
    """
    solver = ImprovedCaptchaSolver(page, debug=debug)
    return await solver.solve(max_attempts)
