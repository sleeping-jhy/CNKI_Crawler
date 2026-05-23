"""
文件存储模块
管理 PDF 文件和元数据的存储
"""
import json
import pathlib
import threading
from typing import Dict, List, Optional
from datetime import datetime


class FileStorage:
    """文件存储管理器"""
    
    def __init__(self, root: str):
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._meta_file = self.root / "meta.json"
        self._meta_cache: List[Dict] = []
        self._lock = threading.Lock()  # 线程锁保护并发写入
        self._pending_changes = 0  # 待保存的更改数
        self._batch_size = 10  # 每10次更改保存一次
        self._load_meta()
    
    def _load_meta(self):
        """加载元数据缓存"""
        if self._meta_file.exists():
            try:
                with self._meta_file.open("r", encoding="utf-8") as f:
                    self._meta_cache = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._meta_cache = []
        else:
            self._meta_cache = []
    
    def _save_meta(self):
        """保存元数据到文件（带重试）"""
        import time
        max_retries = 3
        for retry in range(max_retries):
            try:
                with self._meta_file.open("w", encoding="utf-8") as f:
                    json.dump(self._meta_cache, f, ensure_ascii=False, indent=2)
                return  # 成功则返回
            except OSError as e:
                if retry < max_retries - 1:
                    print(f"⚠️ 保存元数据失败 ({retry+1}/{max_retries}): {e}", flush=True)
                    time.sleep(2)
                else:
                    print(f"❌ 保存元数据最终失败: {e}", flush=True)
                    # 尝试备份到临时文件
                    try:
                        backup_file = self._meta_file.with_suffix('.json.bak')
                        with backup_file.open("w", encoding="utf-8") as f:
                            json.dump(self._meta_cache, f, ensure_ascii=False, indent=2)
                        print(f"📁 已备份到: {backup_file}", flush=True)
                    except:
                        pass
                    raise
    
    def pdf_path(self, journal: str, year: str, filename: str) -> pathlib.Path:
        """
        获取 PDF 文件路径
        
        Args:
            journal: 期刊名称
            year: 年份
            filename: 文件名
        
        Returns:
            PDF 文件路径
        """
        y = year or "unknown"
        j = journal or "unknown"
        p = self.root / "pdf" / j / y / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    
    def meta_dir(self) -> pathlib.Path:
        """获取元数据目录"""
        p = self.root / "meta"
        p.mkdir(parents=True, exist_ok=True)
        return p
    
    def add_article(self, article: Dict) -> bool:
        """
        添加或更新文章元数据
        
        Args:
            article: 文章元数据字典
        
        Returns:
            是否为新文章
        """
        with self._lock:
            article_id = article.get("id", "")
            
            # 查找是否已存在
            for i, existing in enumerate(self._meta_cache):
                if existing.get("id") == article_id:
                    # 更新现有记录
                    self._meta_cache[i].update(article)
                    self._pending_changes += 1
                    self._maybe_save()
                    return False
            
            # 添加新记录
            article["added_at"] = datetime.now().isoformat()
            self._meta_cache.append(article)
            self._pending_changes += 1
            self._maybe_save()
            return True
    
    def _maybe_save(self):
        """检查是否需要保存（批量保存优化）"""
        if self._pending_changes >= self._batch_size:
            self._save_meta()
            self._pending_changes = 0
    
    def flush(self):
        """强制保存所有待保存的更改"""
        with self._lock:
            if self._pending_changes > 0:
                self._save_meta()
                self._pending_changes = 0
    
    def get_article(self, article_id: str) -> Optional[Dict]:
        """获取文章元数据"""
        for article in self._meta_cache:
            if article.get("id") == article_id:
                return article
        return None
    
    def get_articles_by_journal(self, journal: str) -> List[Dict]:
        """获取指定期刊的所有文章"""
        return [a for a in self._meta_cache if a.get("journal") == journal]
    
    def get_articles_by_year(self, year: str) -> List[Dict]:
        """获取指定年份的所有文章"""
        return [a for a in self._meta_cache if a.get("year") == year]
    
    def get_undownloaded_articles(self) -> List[Dict]:
        """获取未下载的文章"""
        return [a for a in self._meta_cache if not a.get("downloaded", False)]
    
    def mark_downloaded(self, article_id: str, pdf_path: str) -> bool:
        """
        标记文章已下载
        
        Args:
            article_id: 文章ID
            pdf_path: PDF 文件路径
        
        Returns:
            是否成功
        """
        for article in self._meta_cache:
            if article.get("id") == article_id:
                article["downloaded"] = True
                article["pdf_path"] = pdf_path
                article["downloaded_at"] = datetime.now().isoformat()
                self._save_meta()
                return True
        return False
    
    def get_all_articles(self) -> List[Dict]:
        """获取所有文章元数据"""
        return list(self._meta_cache)
    
    def get_stats(self) -> Dict:
        """
        获取统计信息
        
        Returns:
            统计字典
        """
        total = len(self._meta_cache)
        downloaded = sum(1 for a in self._meta_cache if a.get("downloaded", False))
        
        # 按期刊统计
        journals = {}
        for article in self._meta_cache:
            journal = article.get("journal", "unknown")
            if journal not in journals:
                journals[journal] = {"total": 0, "downloaded": 0}
            journals[journal]["total"] += 1
            if article.get("downloaded", False):
                journals[journal]["downloaded"] += 1
        
        # 按年份统计（统一转为字符串类型）
        years = {}
        for article in self._meta_cache:
            year = str(article.get("year", "unknown"))
            if year not in years:
                years[year] = {"total": 0, "downloaded": 0}
            years[year]["total"] += 1
            if article.get("downloaded", False):
                years[year]["downloaded"] += 1
        
        # 按期刊-年份细分统计（统一转为字符串类型）
        journal_years = {}
        for article in self._meta_cache:
            journal = article.get("journal", "unknown")
            year = str(article.get("year", "unknown"))
            
            if journal not in journal_years:
                journal_years[journal] = {}
            if year not in journal_years[journal]:
                journal_years[journal][year] = {"total": 0, "downloaded": 0}
            
            journal_years[journal][year]["total"] += 1
            if article.get("downloaded", False):
                journal_years[journal][year]["downloaded"] += 1
        
        return {
            "total": total,
            "downloaded": downloaded,
            "pending": total - downloaded,
            "journals": journals,
            "years": years,
            "journal_years": journal_years
        }
    
    def export_to_csv(self, output_path: str) -> str:
        """
        导出元数据到 CSV
        
        Args:
            output_path: 输出文件路径
        
        Returns:
            输出文件路径
        """
        import csv
        
        fields = ["id", "title", "authors", "journal", "year", "doi", "downloaded", "pdf_path"]
        
        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for article in self._meta_cache:
                row = dict(article)
                # 处理列表字段
                if isinstance(row.get("authors"), list):
                    row["authors"] = "; ".join(row["authors"])
                writer.writerow(row)
        
        return output_path
    
    def import_from_json(self, json_path: str) -> int:
        """
        从 JSON 文件导入元数据
        
        Args:
            json_path: JSON 文件路径
        
        Returns:
            导入的文章数量
        """
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        if isinstance(data, list):
            count = 0
            for article in data:
                if self.add_article(article):
                    count += 1
            return count
        return 0
    
    def clear(self):
        """清空所有元数据（谨慎使用）"""
        self._meta_cache = []
        self._save_meta()