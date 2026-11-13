"""Importance scoring and trending-topic detection.

importance (0-100) =  source tier weight            (8-30)
                    + cross-source coverage         (0-30)  more outlets => bigger story
                    + high-signal phrasing          (0-16)  launches, releases, funding, regulation
                    + community upvotes (papers)    (0-30)  Hugging Face Daily Papers
                    - noise phrasing                       tutorials, webinars, deals, recaps
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from . import config
from .textutil import parse_iso

_SIGNAL = [(re.compile(p, re.I), w) for p, w in config.SIGNAL_PATTERNS.items()]
_NOISE = [(re.compile(p, re.I), w) for p, w in config.NOISE_PATTERNS.items()]
_ENTITIES = {name: re.compile(p, re.I) for name, p in config.ENTITIES.items()}
HOT_HALF_LIFE_HOURS = 36


def importance(a: dict) -> float:
    src = config.SOURCE_BY_ID[a["source_id"]]
    score = config.TIERS[src["tier"]]["weight"]
    # Count independent organisations, so Google AI + DeepMind cross-posts do not inflate coverage.
    org = lambda sid: config.SOURCE_BY_ID[sid].get("org", sid)
    outlets = {org(c["source_id"]) for c in a.get("coverage", [])} - {org(a["source_id"])}
    score += min(30.0, 12.0 * math.log2(1 + len(outlets)))
    title = a["title"]
    score += min(16.0, sum(w for pat, w in _SIGNAL if pat.search(title)))
    score -= sum(w for pat, w in _NOISE if pat.search(title))
    upvotes = int(a.get("extra", {}).get("upvotes", 0))
    if upvotes:
        score += min(30.0, 6.0 * math.log2(1 + upvotes))
    return round(max(0.0, min(100.0, score)), 1)


def hotness(a: dict, now: datetime) -> float:
    age_h = max(0.0, (now - parse_iso(a["published"])).total_seconds() / 3600)
    return a["score"] * 0.5 ** (age_h / HOT_HALF_LIFE_HOURS)


def topic_counts(articles: list[dict]) -> Counter:
    """Count articles mentioning each tracked entity (a story counts once, weighted by coverage)."""
    counts: Counter = Counter()
    for a in articles:
        text = f"{a['title']} {a['summary'][:300]}"
        weight = 1 + len(a.get("coverage", []))
        for name, pat in _ENTITIES.items():
            if pat.search(text):
                counts[name] += weight
    return counts


def trending(articles: list[dict], now: datetime, recent_days: int = 7, baseline_days: int = 28,
             top: int = 12) -> list[dict]:
    """Topics whose share of recent coverage rose most against the preceding baseline."""
    news = [a for a in articles if a["kind"] == "news"]
    recent_cut, base_cut = now - timedelta(days=recent_days), now - timedelta(days=recent_days + baseline_days)
    recent = [a for a in news if parse_iso(a["published"]) >= recent_cut]
    base = [a for a in news if base_cut <= parse_iso(a["published"]) < recent_cut]
    rc, bc = topic_counts(recent), topic_counts(base)
    r_total, b_total = max(1, sum(rc.values())), max(1, sum(bc.values()))
    out = []
    for name, n in rc.items():
        if n < 3:
            continue
        share_now, share_before = n / r_total, (bc.get(name, 0) + 1) / (b_total + 1)
        change = share_now / share_before
        out.append({"topic": name, "count": n, "change": round(change, 2),
                    "query": name.lower() if name not in ("GPT",) else "gpt"})
    # Rank by volume with a boost for rising topics, so trending != merely frequent.
    out.sort(key=lambda t: -(t["count"] * min(3.0, max(0.5, t["change"]))))
    return out[:top]


def category_stats(articles: list[dict]) -> dict[str, int]:
    c = defaultdict(int)
    for a in articles:
        for cid in a["categories"]:
            c[cid] += 1
    return dict(c)
