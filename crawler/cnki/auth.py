import json
import pathlib
import random


class CnkiSession:
    def __init__(
        self,
        cookies_path: str | None = None,
        headers_override: dict[str, str] | None = None,
    ):
        self.cookies_path = cookies_path or "cookies.json"
        self.cookies = {}
        self.headers = {
            "User-Agent": self._ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.cnki.net/",
        }
        if headers_override:
            self.headers.update(
                {k: v for k, v in headers_override.items() if v is not None}
            )

    def _ua(self) -> str:
        choices = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ]
        return random.choice(choices)

    def load(self):
        p = pathlib.Path(self.cookies_path)
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                self.cookies = json.load(f) or {}
        return self

    def save(self):
        p = pathlib.Path(self.cookies_path)
        with p.open("w", encoding="utf-8") as f:
            json.dump(self.cookies, f, ensure_ascii=False)
        return self

    def get_headers(self):
        return dict(self.headers)

    def get_cookies(self):
        return dict(self.cookies)

    def update_headers(self, h: dict[str, str]):
        self.headers.update(h or {})
        return self
