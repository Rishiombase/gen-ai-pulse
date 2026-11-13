"""Source fetchers. Each returns a list of raw items:

    {"url", "title", "summary", "published": datetime, "source_id", "kind", "extra": {...}}

Fetchers never invent dates: items without a trustworthy publication date are dropped.
"""
from __future__ import annotations

import calendar
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import feedparser

from . import config
from .http import HttpClient
from .textutil import canonical_url, article_id, clean_html, clean_title, parse_iso

log = logging.getLogger(__name__)


def _struct_to_dt(st) -> datetime | None:
    if not st:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
    except (OverflowError, ValueError, TypeError):
        return None


def _item(src: dict, url: str, title: str, summary: str, published: datetime, **extra) -> dict:
    return {"url": url, "title": clean_title(title), "summary": clean_html(summary),
            "published": published, "source_id": src["id"], "kind": src.get("kind", "news"),
            "extra": {k: v for k, v in extra.items() if v not in (None, "", [])}}


_IMG_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.I)
_BAD_IMG = re.compile(r"(feedburner|pixel|1x1|spacer|gravatar|avatar|emoji|\.svg($|\?)|\.gif($|\?))", re.I)


def clean_image(url: str | None, base: str) -> str | None:
    """Absolute https image URL, or None for tracking pixels, icons, and junk."""
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    url = urljoin(base, url)
    if not url.startswith(("http://", "https://")) or _BAD_IMG.search(url):
        return None
    return url.replace("http://", "https://", 1)


def _rss_image(e, html: str, link: str) -> str | None:
    for key in ("media_content", "media_thumbnail"):
        for m in e.get(key) or []:
            if m.get("medium") in (None, "image") and not str(m.get("type", "")).startswith(("video", "audio")):
                img = clean_image(m.get("url"), link)
                if img:
                    return img
    for l in e.get("links", []):
        if l.get("rel") == "enclosure" and str(l.get("type", "")).startswith("image"):
            img = clean_image(l.get("href"), link)
            if img:
                return img
    for m in _IMG_RE.finditer(html or ""):
        img = clean_image(m.group(1), link)
        if img:
            return img
    return None


# --------------------------------------------------------------------------- RSS / Atom
def fetch_rss(src: dict, http: HttpClient, ctx: dict) -> list[dict]:
    resp = http.get(src["url"], conditional=ctx["conditional"])
    if resp is None:
        return []  # 304: nothing new since last run
    feed = feedparser.parse(resp.content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"unparseable feed: {feed.bozo_exception}")
    items = []
    for e in feed.entries[: src.get("limit", 200)]:
        link = e.get("link") or ""
        published = _struct_to_dt(e.get("published_parsed")) or _struct_to_dt(e.get("updated_parsed"))
        if not link or not e.get("title") or not published:
            continue
        summary = e.get("summary") or ""
        content = (e.get("content") or [{}])[0].get("value", "")
        if content:
            s_txt, c_txt = clean_html(summary), clean_html(content)
            # Use the article's own lede when the summary is missing, too short, or unrelated
            # to the body (some feeds put image alt text in <description>).
            if len(s_txt) < 40 or (len(c_txt) >= 80 and s_txt[:60] not in c_txt):
                summary = content
        authors = [a.get("name") for a in e.get("authors", []) if a.get("name")][:4]
        image = _rss_image(e, (e.get("summary") or "") + content, link)
        items.append(_item(src, link, e.title, summary, published, authors=authors, image=image))
    return items


# --------------------------------------------------------------------------- arXiv API
def fetch_arxiv(src: dict, http: HttpClient, ctx: dict) -> list[dict]:
    items = []
    for i, cat in enumerate(src["categories"]):
        if i:
            time.sleep(3)  # arXiv API etiquette: one request every 3 seconds
        resp = http.get(src["url"], params={
            "search_query": f"cat:{cat}", "sortBy": "submittedDate", "sortOrder": "descending",
            "start": 0, "max_results": src.get("limit", 30)})
        feed = feedparser.parse(resp.content)
        for e in feed.entries:
            published = _struct_to_dt(e.get("published_parsed"))
            if not published:
                continue
            primary = (e.get("arxiv_primary_category") or {}).get("term", cat)
            authors = [a.get("name") for a in e.get("authors", [])][:6]
            items.append(_item(src, e.get("id") or e.get("link"), e.title, e.get("summary", ""), published,
                               authors=authors, arxiv_category=primary))
    return items


# --------------------------------------------------------------------------- Hugging Face Daily Papers
def fetch_hf_papers(src: dict, http: HttpClient, ctx: dict) -> list[dict]:
    """Community-curated papers with upvotes. Fetches one listing per day in the lookback window."""
    days = 3 if not ctx["backfill"] else min(ctx["max_age_days"], 120)
    today = datetime.now(timezone.utc).date()
    items = []
    for d in range(days):
        day = today - timedelta(days=d)
        try:
            resp = http.get(src["url"], params={"date": day.isoformat(), "limit": 100})
        except Exception as exc:  # one missing day should not fail the source
            log.debug("hf papers %s: %s", day, exc)
            continue
        for row in resp.json():
            p = row.get("paper") or {}
            pid, title = p.get("id"), p.get("title") or row.get("title")
            published = p.get("publishedAt") or row.get("publishedAt")
            if not pid or not title or not published:
                continue
            authors = [a.get("name") for a in p.get("authors", []) if a.get("name")][:6]
            items.append(_item(src, f"https://arxiv.org/abs/{pid}", title, p.get("summary", ""),
                               parse_iso(published), upvotes=int(p.get("upvotes") or 0),
                               hf_url=f"https://huggingface.co/papers/{pid}", authors=authors,
                               github=p.get("githubRepo"), image=clean_image(row.get("thumbnail"), src["home"])))
        if ctx["backfill"]:
            time.sleep(0.3)
    return items


# --------------------------------------------------------------------------- Sitemaps (labs without feeds)
_SM_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_META_RE = r'<meta[^>]+(?:property|name)=["\']{}["\'][^>]+content=["\']([^"\']*)["\']'


def _meta(html: str, name: str) -> str | None:
    m = re.search(_META_RE.format(re.escape(name)), html, re.I)
    if not m:  # attribute order can be reversed
        m = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']%s["\']' % re.escape(name),
                      html, re.I)
    return m.group(1) if m else None


def _sitemap_urls(http: HttpClient, url: str, depth: int = 0) -> list[tuple[str, str | None]]:
    root = ET.fromstring(http.get(url).content)
    if root.tag.endswith("sitemapindex") and depth < 2:
        out = []
        for loc in root.findall("sm:sitemap/sm:loc", _SM_NS):
            out += _sitemap_urls(http, loc.text.strip(), depth + 1)
        return out
    return [(u.findtext("sm:loc", "", _SM_NS).strip(), u.findtext("sm:lastmod", None, _SM_NS))
            for u in root.findall("sm:url", _SM_NS)]


def fetch_sitemap(src: dict, http: HttpClient, ctx: dict) -> list[dict]:
    patterns = [re.compile(p) for p in src.get("include", [])]
    cutoff = ctx["cutoff"]
    seen: set = ctx["state"].setdefault(f"sitemap_seen:{src['id']}", set())
    candidates = []
    for loc, lastmod in _sitemap_urls(http, src["url"]):
        if patterns and not any(p.search(loc) for p in patterns):
            continue
        aid = article_id(canonical_url(loc))
        if aid in ctx["known_ids"] or aid in seen:
            continue
        if lastmod:
            try:
                if parse_iso(lastmod) < cutoff:
                    continue
            except ValueError:
                pass
        candidates.append((lastmod or "", loc))
    candidates.sort(reverse=True)
    items = []
    for _, loc in candidates[: src.get("limit", 30)]:
        try:
            page = http.get(loc).text
        except Exception as exc:
            log.debug("sitemap page %s: %s", loc, exc)
            continue
        title = _meta(page, "og:title") or _meta(page, "twitter:title")
        desc = _meta(page, "og:description") or _meta(page, "description") or ""
        published = _meta(page, "article:published_time")
        if not published:
            m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', page)
            published = m.group(1) if m else None
        seen.add(article_id(canonical_url(loc)))
        if not title or not published:
            continue
        try:
            pub_dt = parse_iso(published)
        except ValueError:
            continue
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        items.append(_item(src, loc, title, desc, pub_dt,
                           image=clean_image(_meta(page, "og:image") or _meta(page, "twitter:image"), loc)))
        time.sleep(0.5)
    return items


FETCHERS = {"rss": fetch_rss, "arxiv": fetch_arxiv, "hf_papers": fetch_hf_papers, "sitemap": fetch_sitemap}
