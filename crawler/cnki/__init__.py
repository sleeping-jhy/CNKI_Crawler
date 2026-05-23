# -*- coding: utf-8 -*-
"""
CNKI 爬虫模块
"""
from crawler.cnki.auth import CnkiSession
from crawler.cnki.login import LoginManager
from crawler.cnki.search import CNKISearcher, search_journal_year
from crawler.cnki.downloader import CnkiDownloader, DownloadManager

__all__ = [
    "CnkiSession",
    "LoginManager",
    "CNKISearcher",
    "search_journal_year",
    "CnkiDownloader",
    "DownloadManager",
]
