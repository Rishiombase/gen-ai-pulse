"""Fill in missing or boilerplate summaries from the article page itself.

Some feeds ship no description (Hugging Face blog) or a site-wide one (every
Anthropic page shares the same og:description). For those items we fetch the
page and take the first specific description we can find.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

from . import config
from .fetchers import _meta, clean_image
from .http import HttpClient
from .textutil import clean_html, summarize

log = logging.getLogger(__name__)

MAX_PAGES_PER_RUN = 80
_JUNK_RE = re.compile(r"^(tags?|categories|filed under|posted in)\s*:|^\W*$", re.I)
_BYLINE_RE = re.compile(r"^(published|updated|posted|by |back to|share|update on github|written by)", re.I)


class _Paragraphs(HTMLParser):
    """Collect the text of <p> elements, ignoring navigation, headers, footers, and scripts."""
    SKIP = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg", "button"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paras: list[str] = []
        self._skip = 0
        self._buf: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "p" and not self._skip:
            self._buf = []

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "p" and self._buf is not None:
            self.paras.append(" ".join("".join(self._buf).split()))
            self._buf = None

    def handle_data(self, data):
        if self._buf is not None and not self._skip:
            self._buf.append(data)


def _paragraphs(html: str) -> list[str]:
    parser = _Paragraphs()
    try:
        parser.feed(html)
    except Exception:
        return []
    return [p for p in parser.paras if len(p) >= 80 and len(p.split()) >= 12 and not _BYLINE_RE.match(p)]


def generic_summaries(articles: list[dict]) -> dict[str, set[str]]:
    """Per source, summaries shared by 2+ different articles (site-wide boilerplate)."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for a in articles:
        if a.get("summary"):
            counts[a["source_id"]][a["summary"]] += 1
    return {sid: {s for s, n in c.items() if n >= 2} for sid, c in counts.items()}


def is_weak(summary: str, generic: set[str]) -> bool:
    s = (summary or "").strip()
    return len(s) < 60 or s in generic or bool(_JUNK_RE.search(s))


def _candidates(html: str) -> list[str]:
    """Possible summaries from a page, best first: meta descriptions, then opening paragraphs."""
    out = [clean_html(_meta(html, n) or "") for n in ("og:description", "description", "twitter:description")]
    out += _paragraphs(html)[:8]
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen and len(c) >= 60:
            seen.add(c)
            uniq.append(summarize(c, 320))
    return uniq


def enrich(items: list[dict], archive: list[dict], http: HttpClient, limit: int = MAX_PAGES_PER_RUN) -> int:
    """Fill weak summaries and missing images from each article's own page."""
    generic = generic_summaries(archive + items)
    for a in archive + items:  # scrub boilerplate, identically for archived and new items
        if a.get("summary") and (a["summary"] in generic.get(a["source_id"], set()) or _JUNK_RE.search(a["summary"])):
            a["summary"] = ""
    targets = [a for a in items if a["kind"] == "news"
               and (is_weak(a["summary"], set()) or not a.get("extra", {}).get("image"))]
    # Weak summaries first: they matter more than images.
    targets.sort(key=lambda a: (not is_weak(a["summary"], set()), a["published"]), reverse=False)
    targets = targets[:limit]

    def work(a):
        try:
            html = http.get(a["url"]).text
        except Exception as exc:
            log.debug("enrich %s: %s", a["url"], exc)
            return a, [], None
        image = clean_image(_meta(html, "og:image") or _meta(html, "twitter:image"), a["url"])
        return a, (_candidates(html) if is_weak(a["summary"], set()) else []), image

    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
        results = list(pool.map(work, targets))
    # A candidate (summary or image) that several pages of one site share is boilerplate.
    shared: dict[str, Counter] = defaultdict(Counter)
    for a, cands, image in results:
        for c in cands:
            shared[a["source_id"]][c] += 1
        if image:
            shared[a["source_id"]]["img:" + image] += 1
    existing = {(a["source_id"], a["summary"]) for a in archive if a.get("summary")}
    fixed = images = 0
    for a, cands, image in results:
        for c in cands:
            if shared[a["source_id"]][c] < 2 and (a["source_id"], c) not in existing and not _JUNK_RE.search(c):
                a["summary"] = c
                fixed += 1
                break
        if image and not a.get("extra", {}).get("image") and shared[a["source_id"]]["img:" + image] < 3:
            a.setdefault("extra", {})["image"] = image
            images += 1
    log.info("enrich: %d pages fetched, %d summaries and %d images filled", len(targets), fixed, images)
    return fixed
