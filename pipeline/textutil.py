"""Text helpers: HTML cleaning, extractive summaries, URL canonicalisation, tokens."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Tags, allowing ">" inside quoted attribute values (e.g. Tailwind classes like "[&>a]:hidden").
_TAG_RE = re.compile(r"""<(?:[^>"']|"[^"]*"|'[^']*')*>""")
_WS_RE = re.compile(r"\s+")
_SCRIPT_RE = re.compile(r"<(script|style|figure|figcaption)[^>]*>.*?</\1>", re.S | re.I)
_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“])")

# Boilerplate that feeds append to descriptions.
_BOILERPLATE = [
    re.compile(p, re.I) for p in (
        r"The post .{0,200}? appeared first on .{0,120}?\.?$",
        r"Continue reading.*$", r"Read more.*$", r"\[(…|\.\.\.)\]\s*$",
        r"^arXiv:\S+\s+Announce Type:\s*\w+\s*Abstract:\s*",
        r"Subscribe now.*$", r"Comments$",
    )
]

_TRACKING_PARAMS = re.compile(r"^(utm_|ref$|ref_src$|source$|fbclid$|gclid$|mc_|cmpid$|ncid$|guccounter)", re.I)

STOPWORDS = set("""
a about above after again against all also am an and any are as at be because been before being below between
both but by can could did do does doing down during each few for from further had has have having he her here
hers herself him himself his how i if in into is it its itself just me more most my myself no nor not now of off
on once only or other our ours ourselves out over own same she should so some such than that the their theirs
them themselves then there these they this those through to too under until up very was we were what when where
which while who whom why will with would you your yours yourself yourselves new via vs says say said get gets
using use uses used make makes made one two first now how why what latest today week year years way ways
""".split())


def clean_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = _SCRIPT_RE.sub(" ", raw)
    text = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>", ". ", text, flags=re.I)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = _WS_RE.sub(" ", text).strip()
    text = re.sub(r"(\.\s*){2,}", ". ", text)
    text = re.sub(r"^\.\s*", "", text)
    for pat in _BOILERPLATE:
        text = pat.sub("", text).strip()
    return text


def summarize(text: str, max_chars: int = 320) -> str:
    """Extractive summary: leading sentences that fit within max_chars."""
    text = clean_html(text)
    if len(text) <= max_chars:
        return text
    out = ""
    for sent in _SENT_RE.split(text):
        candidate = f"{out} {sent}".strip()
        if len(candidate) > max_chars:
            break
        out = candidate
    if not out:  # one very long sentence: cut on a word boundary
        out = text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
    return out


def clean_title(title: str | None) -> str:
    t = clean_html(title or "")
    t = re.sub(r"\s*[|–—-]\s*(TechCrunch|The Verge|WIRED|Ars Technica|MIT Technology Review)\s*$", "", t)
    return t.strip(" .")


def canonical_url(url: str) -> str:
    """Normalise a URL so the same article from different feeds maps to one id."""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme
    netloc = parts.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+", "/", parts.path).rstrip("/") or "/"
    # arXiv: collapse versions and pdf/abs variants to one canonical abstract URL.
    if netloc.endswith("arxiv.org"):
        m = re.search(r"(\d{4}\.\d{4,5})", path)
        if m:
            return f"https://arxiv.org/abs/{m.group(1)}"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING_PARAMS.match(k)])
    return urlunsplit((scheme, netloc, path, query, ""))


def article_id(canonical: str) -> str:
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9.+-]*[a-z0-9]|[a-z0-9]", text.lower())
    out = []
    for w in words:
        if w in STOPWORDS or len(w) < 2:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
