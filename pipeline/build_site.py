"""Write the JSON files the static frontend reads (site/data/*)."""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta

from . import config, store
from .focus import is_tech
from .scoring import category_stats, hotness, trending
from .textutil import parse_iso, to_utc_iso

log = logging.getLogger(__name__)


def compact(a: dict, now: datetime | None = None) -> dict:
    out = {
        "id": a["id"], "title": a["title"], "url": a["url"], "summary": a["summary"],
        "source": a["source_id"], "date": a["published"], "kind": a["kind"],
        "cats": a["categories"], "score": a["score"],
    }
    if a.get("tags"):
        out["tags"] = a["tags"]
    if a.get("coverage"):
        out["coverage"] = [{"source": c["source_id"], "url": c["url"], "title": c["title"]} for c in a["coverage"]]
    extra = a.get("extra", {})
    if extra.get("upvotes"):
        out["upvotes"] = extra["upvotes"]
    if extra.get("image"):
        out["img"] = extra["image"]
    if extra.get("hf_url"):
        out["hf"] = extra["hf_url"]
    if extra.get("authors") and a["kind"] == "paper":
        out["authors"] = extra["authors"][:4]
    if now is not None:
        out["hot"] = round(hotness(a, now), 2)
    return out


def _write(path, payload) -> None:
    """Atomic write, so a browser never reads a half-written file while the site is live."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    tmp.replace(path)


def build(articles: dict[str, dict], state: dict, now: datetime) -> None:
    all_items = sorted((a for a in articles.values() if is_tech(a)), key=lambda a: a["published"], reverse=True)
    hidden = sum(1 for a in articles.values() if not is_tech(a))
    news = [a for a in all_items if a["kind"] == "news"]
    papers = [a for a in all_items if a["kind"] == "paper"]
    cutoff = now - timedelta(days=config.LATEST_WINDOW_DAYS)
    latest = [a for a in news if parse_iso(a["published"]) >= cutoff]

    # Papers: last 30 days, most upvoted / highest scored first, capped for payload size.
    paper_cut = now - timedelta(days=30)
    recent_papers = sorted((p for p in papers if parse_iso(p["published"]) >= paper_cut),
                           key=lambda p: (-p.get("extra", {}).get("upvotes", 0), -p["score"]))[:400]

    _write(config.SITE_DATA_DIR / "latest.json", {"generated": to_utc_iso(now),
                                                  "articles": [compact(a, now) for a in latest]})
    _write(config.SITE_DATA_DIR / "papers.json", {"generated": to_utc_iso(now),
                                                  "papers": [compact(p) for p in recent_papers]})

    # Monthly archives (news only; papers are too numerous to be useful in a month list).
    months = Counter(a["published"][:7] for a in news)
    for month in months:
        _write(config.SITE_DATA_DIR / "archive" / f"{month}.json",
               {"month": month, "articles": [compact(a) for a in news if a["published"].startswith(month)]})

    status = store.load_source_status()
    month_ago = now - timedelta(days=30)
    per_source = Counter(a["source_id"] for a in all_items if parse_iso(a["published"]) >= month_ago)
    for a in all_items:  # coverage links count toward their outlet too
        if parse_iso(a["published"]) >= month_ago:
            for c in a.get("coverage", []):
                per_source[c["source_id"]] += 1

    window_7 = [a for a in news if parse_iso(a["published"]) >= now - timedelta(days=7)]
    meta = {
        "site": config.SITE,
        "generated": to_utc_iso(now),
        "last_fetch": state.get("last_fetch"),
        "tracking_since": state.get("tracking_since"),
        "categories": [{"id": c["id"], "name": c["name"], "color": c["color"]} for c in config.CATEGORIES],
        "category_counts": category_stats(latest),
        "tiers": {str(k): {"label": v["label"], "desc": v["desc"]} for k, v in config.TIERS.items()},
        "sources": [{
            "id": s["id"], "name": s["name"], "tier": s["tier"], "home": s["home"],
            "kind": s.get("kind", "news"),
            "ok": status.get(s["id"], {}).get("ok"),
            "last_success": status.get(s["id"], {}).get("last_success"),
            "items_30d": per_source.get(s["id"], 0),
        } for s in config.SOURCES],
        "trending": trending(news, now),
        "stats": {
            "articles_7d": len(window_7),
            "articles_latest": len(latest),
            "papers_30d": len([p for p in papers if parse_iso(p["published"]) >= paper_cut]),
            "sources": len(config.SOURCES),
            "archive_total": len(all_items),
            "hidden_non_technical": hidden,
        },
        "archive_months": sorted(({"month": m, "count": n} for m, n in months.items()),
                                 key=lambda r: r["month"], reverse=True),
    }
    _write(config.SITE_DATA_DIR / "meta.json", meta)
    log.info("site data: %d latest, %d papers, %d archive months", len(latest), len(recent_papers), len(months))
