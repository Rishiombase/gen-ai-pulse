"""Polite HTTP client with retries and conditional GET (ETag / Last-Modified)."""
from __future__ import annotations

import json
import logging
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

log = logging.getLogger(__name__)

_CACHE_FILE = config.STATE_DIR / "http_cache.json"
_lock = threading.Lock()


class HttpClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET",), respect_retry_after_header=True)
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=config.MAX_WORKERS * 2)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, "
                      "application/json, text/html;q=0.8, */*;q=0.5",
            "Accept-Language": "en",
        })
        try:
            self.cache: dict = json.loads(_CACHE_FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.cache = {}

    def get(self, url: str, *, params: dict | None = None, conditional: bool = False) -> requests.Response | None:
        """GET a URL. With conditional=True returns None when the server says 304 Not Modified."""
        key = url if not params else f"{url}?{sorted(params.items())}"
        headers = {}
        if conditional and key in self.cache:
            entry = self.cache[key]
            if entry.get("etag"):
                headers["If-None-Match"] = entry["etag"]
            if entry.get("last_modified"):
                headers["If-Modified-Since"] = entry["last_modified"]
        started = time.monotonic()
        resp = self.session.get(url, params=params, headers=headers, timeout=config.HTTP_TIMEOUT)
        log.debug("GET %s -> %s in %.1fs", url, resp.status_code, time.monotonic() - started)
        if resp.status_code == 304:
            return None
        resp.raise_for_status()
        if conditional:
            with _lock:
                self.cache[key] = {"etag": resp.headers.get("ETag"),
                                   "last_modified": resp.headers.get("Last-Modified")}
        return resp

    def forget(self, url: str) -> None:
        with _lock:
            self.cache.pop(url, None)

    def save_cache(self) -> None:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(self.cache, indent=1, sort_keys=True))
