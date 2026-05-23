# -*- coding: utf-8 -*-
"""
多窗口会话管理器

实现功能：
1. 每个并发窗口使用独立的 Cookie 文件
2. 各窗口独立处理验证码和重新登录
3. 支持 Cookie 池管理和自动恢复
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class SessionManager:
    """
    多窗口会话管理器

    为每个并发窗口分配独立的 Cookie 文件和浏览器上下文，
    支持独立的验证码处理和重新登录。

    使用示例:
        session_mgr = SessionManager(concurrency=4, base_cookies_path='cookies.json')
        await session_mgr.initialize()

        # 获取可用会话
        session = await session_mgr.acquire_session()
        try:
            # 使用会话
            searcher = CNKISearcher.create(cookies_path=session.cookies_path)
            ...
        finally:
            session_mgr.release_session(session)
    """

    def __init__(
        self,
        concurrency: int = 2,
        base_cookies_path: str = "cookies.json",
        sessions_dir: str = "sessions",
    ):
        """
        初始化会话管理器

        Args:
            concurrency: 并发数量（即会话池大小）
            base_cookies_path: 主 Cookie 文件路径（用于初始化各会话）
            sessions_dir: 会话文件存储目录
        """
        self.concurrency = concurrency
        self.base_cookies_path = Path(base_cookies_path)
        self.sessions_dir = Path(sessions_dir)

        # 会话池
        self._sessions: List["CrawlerSession"] = []
        self._available_sessions: asyncio.Queue = asyncio.Queue()
        self._session_locks: Dict[int, asyncio.Lock] = {}

        # 全局状态
        self._initialized = False

    async def initialize(self) -> bool:
        """
        初始化所有会话

        Returns:
            是否成功
        """
        if self._initialized:
            return True

        # 创建会话目录
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        # 加载基础 Cookie
        if not self.base_cookies_path.exists():
            logger.error(f"基础 Cookie 文件不存在: {self.base_cookies_path}")
            logger.info("请先运行 python cli.py --login 登录获取 Cookie")
            return False

        try:
            with open(self.base_cookies_path, "r", encoding="utf-8") as f:
                base_cookies = json.load(f) or {}
        except Exception as e:
            logger.error(f"读取基础 Cookie 失败: {e}")
            return False

        # 为每个并发槽创建独立会话
        for i in range(self.concurrency):
            session = CrawlerSession(
                session_id=i,
                cookies_path=self.sessions_dir / f"session_{i}_cookies.json",
                base_cookies=base_cookies,
            )

            # 初始化会话文件
            await session.initialize()

            self._sessions.append(session)
            self._session_locks[i] = asyncio.Lock()
            await self._available_sessions.put(session)

        self._initialized = True
        logger.info(f"会话管理器已初始化: {self.concurrency} 个独立会话")
        return True

    async def acquire_session(self, timeout: float = 30.0) -> "CrawlerSession":
        """
        获取一个可用会话

        Args:
            timeout: 等待超时时间（秒）

        Returns:
            可用的会话对象

        Raises:
            asyncio.TimeoutError: 等待超时
        """
        session = await asyncio.wait_for(
            self._available_sessions.get(), timeout=timeout
        )
        session.mark_in_use()
        logger.debug(f"会话 {session.session_id} 已分配")
        return session

    def release_session(self, session: "CrawlerSession"):
        """
        释放会话

        Args:
            session: 要释放的会话
        """
        session.mark_available()
        self._available_sessions.put_nowait(session)
        logger.debug(f"会话 {session.session_id} 已释放")

    async def refresh_all_cookies(self) -> int:
        """
        刷新所有会话的 Cookie（从基础文件重新复制）

        Returns:
            成功刷新的会话数
        """
        if not self.base_cookies_path.exists():
            return 0

        try:
            with open(self.base_cookies_path, "r", encoding="utf-8") as f:
                base_cookies = json.load(f) or {}
        except Exception:
            return 0

        success_count = 0
        for session in self._sessions:
            if await session.update_cookies(base_cookies):
                success_count += 1

        return success_count

    def get_session_by_id(self, session_id: int) -> Optional["CrawlerSession"]:
        """获取指定 ID 的会话"""
        for session in self._sessions:
            if session.session_id == session_id:
                return session
        return None

    def get_all_sessions(self) -> List["CrawlerSession"]:
        """获取所有会话"""
        return list(self._sessions)

    def get_stats(self) -> Dict:
        """获取会话池统计信息"""
        in_use = sum(1 for s in self._sessions if s.in_use)
        return {
            "total": len(self._sessions),
            "in_use": in_use,
            "available": len(self._sessions) - in_use,
            "sessions": [s.get_stats() for s in self._sessions],
        }


class CrawlerSession:
    """
    单个爬虫会话

    包含独立的 Cookie 文件和状态管理
    """

    def __init__(
        self,
        session_id: int,
        cookies_path: Path,
        base_cookies: Dict | None = None,
    ):
        """
        初始化会话

        Args:
            session_id: 会话 ID
            cookies_path: 该会话的 Cookie 文件路径
            base_cookies: 基础 Cookie 数据
        """
        self.session_id = session_id
        self.cookies_path = cookies_path
        self._base_cookies = base_cookies or {}

        # 状态追踪
        self.in_use = False
        self.captcha_count = 0
        self.relogin_count = 0
        self.last_used = None
        self.last_captcha = None
        self.last_relogin = None

        # 独立的验证码锁（每个会话独立处理验证码）
        self._captcha_lock = asyncio.Lock()

    async def initialize(self):
        """初始化会话文件"""
        # 如果会话 Cookie 文件不存在，从基础文件复制
        if not self.cookies_path.exists() and self._base_cookies:
            await self.update_cookies(self._base_cookies)
            logger.debug(f"会话 {self.session_id} Cookie 文件已创建")

    async def update_cookies(self, cookies: Dict) -> bool:
        """
        更新会话的 Cookie

        Args:
            cookies: 新的 Cookie 数据

        Returns:
            是否成功
        """
        try:
            with open(self.cookies_path, "w", encoding="utf-8") as f:
                json.dump(cookies or {}, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"会话 {self.session_id} 更新 Cookie 失败: {e}")
            return False

    async def save_cookies_from_context(self, context) -> bool:
        """
        从浏览器上下文保存 Cookie

        Args:
            context: Playwright BrowserContext

        Returns:
            是否成功
        """
        try:
            cookies = await context.cookies()
            cookie_dict = {c["name"]: c["value"] for c in cookies}
            return await self.update_cookies(cookie_dict)
        except Exception as e:
            logger.error(f"会话 {self.session_id} 保存 Cookie 失败: {e}")
            return False

    def load_cookies(self) -> Dict:
        """加载会话的 Cookie"""
        try:
            if self.cookies_path.exists():
                with open(self.cookies_path, "r", encoding="utf-8") as f:
                    return json.load(f) or {}
        except Exception as e:
            logger.error(f"会话 {self.session_id} 加载 Cookie 失败: {e}")
        return {}

    def mark_in_use(self):
        """标记会话正在使用"""
        self.in_use = True
        self.last_used = datetime.now()

    def mark_available(self):
        """标记会话可用"""
        self.in_use = False

    def record_captcha(self):
        """记录一次验证码事件"""
        self.captcha_count += 1
        self.last_captcha = datetime.now()

    def record_relogin(self):
        """记录一次重新登录事件"""
        self.relogin_count += 1
        self.last_relogin = datetime.now()

    async def handle_captcha(self, callback) -> bool:
        """
        处理验证码（带锁保护）

        Args:
            callback: 验证码处理回调函数

        Returns:
            是否成功
        """
        async with self._captcha_lock:
            self.record_captcha()
            return await callback()

    async def handle_relogin(self, callback) -> bool:
        """
        处理重新登录（带锁保护）

        Args:
            callback: 重新登录处理回调函数

        Returns:
            是否成功
        """
        async with self._captcha_lock:  # 使用同一个锁，避免验证码和重登录冲突
            self.record_relogin()
            return await callback()

    def get_stats(self) -> Dict:
        """获取会话统计信息"""
        return {
            "session_id": self.session_id,
            "cookies_path": str(self.cookies_path),
            "in_use": self.in_use,
            "captcha_count": self.captcha_count,
            "relogin_count": self.relogin_count,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "last_captcha": self.last_captcha.isoformat()
            if self.last_captcha
            else None,
            "last_relogin": self.last_relogin.isoformat()
            if self.last_relogin
            else None,
        }

    def __repr__(self):
        return f"CrawlerSession(id={self.session_id}, in_use={self.in_use})"


async def create_session_manager(
    concurrency: int = 2, base_cookies_path: str = "cookies.json"
) -> SessionManager:
    """
    创建并初始化会话管理器的便捷函数

    Args:
        concurrency: 并发数
        base_cookies_path: 基础 Cookie 文件路径

    Returns:
        初始化好的 SessionManager
    """
    manager = SessionManager(
        concurrency=concurrency, base_cookies_path=base_cookies_path
    )
    await manager.initialize()
    return manager
