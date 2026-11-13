"""Compile monthly and quarterly newsletter issues (JSON for the web + PDF for download)."""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from . import config
from .build_site import _write, compact
from .focus import is_tech
from .scoring import topic_counts
from .textutil import parse_iso, to_utc_iso

log = logging.getLogger(__name__)

MIN_STORIES = 15  # don't publish an issue with less material than this
SIZES = {  # top stories, chapters, research papers
    "monthly": {"top": 6, "chapters": 7, "papers": 6},
    "quarterly": {"top": 6, "chapters": 8, "papers": 6},
}
CHAPTER_ITEMS, CHAPTER_MORE, CHAPTER_READY = 4, 4, 5

# Chapter headings and the standing advice behind "What this means for AIT Global".
CHAPTER_COPY = {
    "genai-llm": ("Frontier models", "Re-run model benchmarks on representative client tasks. Routing work across models by "
                  "cost, latency and quality is now a design decision, not a procurement detail."),
    "agents": ("Agentic AI", "Pick one measurable workflow to delegate to an agent, with least-privilege tool access, "
               "approval steps for consequential actions, and full action logs."),
    "tools-launches": ("New tools and launches", "Shortlist the launches that match current client needs and run "
                       "time-boxed pilots with clear success criteria before committing."),
    "computer-vision": ("Vision AI", "Look for document, inspection and video-analytics workloads where new vision models "
                        "can cut manual review."),
    "nlp": ("Language and speech", "Test the new speech and language models on multilingual and voice-first use cases, "
            "including Indic languages."),
    "robotics": ("Physical AI", "Track factory and logistics pilots, and plan physical-safety and fleet-security "
                 "requirements early."),
    "enterprise": ("AI in the enterprise", "Reuse these deployments as reference patterns for client proposals and "
                   "ROI cases."),
    "security": ("Safety, security and governance", "Check agent permissions, prompt-injection defences and audit "
                 "logging against this period's findings."),
    "open-source": ("Open models", "Evaluate open-weight releases for self-hosted, cost-sensitive or data-resident "
                    "workloads."),
    "mlops": ("AI infrastructure", "Re-benchmark inference cost and latency on the new hardware and runtimes before the "
              "next capacity decision."),
    "ai-ml": ("Machine learning", "Note the techniques that could improve existing ML pipelines and evaluation."),
    "research": ("Research breakthroughs", "Track these results: the strongest ones tend to reach products within "
                 "months."),
}
READY_TAGS = ("Available now", "Open source", "API", "Preview")
MAX_TOP_PER_ORG = 2  # keep the top-stories list from being one company's press feed


# --------------------------------------------------------------------------- periods
def _month_bounds(y: int, m: int) -> tuple[date, date]:
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1)
    return start, end


def periods(tracking_since: datetime, now: datetime) -> list[dict]:
    """All months and quarters we have tracked fully enough to publish, newest first."""
    out = []
    first = tracking_since.date() - timedelta(days=3)  # small grace window
    today = now.date()
    y, m = first.year, first.month
    while (y, m) <= (today.year, today.month):
        start, end = _month_bounds(y, m)
        if start >= first:
            out.append({"id": f"{y}-{m:02d}", "type": "monthly", "start": start, "end": end,
                        "title": start.strftime("%B %Y")})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    y, q = first.year, (first.month - 1) // 3 + 1
    while (y, q) <= (today.year, (today.month - 1) // 3 + 1):
        start = date(y, 3 * q - 2, 1)
        end = _month_bounds(y, 3 * q)[1]
        if start >= first:
            out.append({"id": f"{y}-Q{q}", "type": "quarterly", "start": start, "end": end,
                        "title": f"Q{q} {y}"})
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    for p in out:
        p["status"] = "in_progress" if p["end"] > today else "final"
    return sorted(out, key=lambda p: (p["end"], p["type"] == "quarterly"), reverse=True)


# --------------------------------------------------------------------------- issue content
def _org(sid: str) -> str:
    return config.SOURCE_BY_ID[sid].get("org", sid)


def _in(a: dict, start: date, end: date) -> bool:
    d = parse_iso(a["published"]).date()
    return start <= d < end


def _rank(a: dict) -> tuple:
    """Deterministic ranking: score, then newest, then id (stable across runs)."""
    return (-a["score"], -parse_iso(a["published"]).timestamp(), a["id"])


def _spread(arts: list[dict], key=_rank) -> list[dict]:
    """Rank articles, then interleave calendar months so a quarter is covered evenly.

    Takes each month's best story, then each month's second best, and so on. For a
    single month this is simply the ranking. Without it, equal scores favour the newest
    stories and a quarterly edition collapses into a copy of its latest month.
    """
    by_month: dict[str, list[dict]] = defaultdict(list)
    for a in sorted(arts, key=key):
        by_month[a["published"][:7]].append(a)
    if len(by_month) <= 1:
        return sorted(arts, key=key)
    out, i = [], 0
    while len(out) < len(arts):
        layer = [m[i] for m in by_month.values() if i < len(m)]
        out += sorted(layer, key=key)
        i += 1
    return out


def _pick_top(news: list[dict], n: int) -> list[dict]:
    picked, per_org = [], Counter()
    for a in _spread(news):
        if per_org[_org(a["source_id"])] >= MAX_TOP_PER_ORG:
            continue
        picked.append(a)
        per_org[_org(a["source_id"])] += 1
        if len(picked) == n:
            break
    return picked


def _rising(cur: list[dict], prev: list[dict], top: int = 8) -> list[dict]:
    c, p = topic_counts(cur), topic_counts(prev)
    ct, pt = max(1, sum(c.values())), max(1, sum(p.values()))
    rows = []
    for name, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:25]:
        if n < 3:
            continue
        before = p.get(name, 0)
        change = None if before == 0 else round(100 * ((n / ct) / (before / pt) - 1))
        rows.append({"topic": name, "mentions": n, "previous": before, "change_pct": change})
    rows.sort(key=lambda r: (-(r["mentions"] * (1 + max(-0.5, (r["change_pct"] or 100) / 100))), r["topic"]))
    return rows[:top]


def _ready(arts: list[dict]) -> list[dict]:
    out = []
    for a in arts:
        tag = next((t for t in READY_TAGS if t in a.get("tags", [])), None)
        if tag:
            out.append({"title": a["title"], "url": a["url"], "tag": tag,
                        "source": config.SOURCE_BY_ID[a["source_id"]]["name"]})
    return out


def _actions(name: str, arts: list[dict]) -> list[str]:
    """Concrete, data-derived next steps for a chapter."""
    def names(tag, n=2):
        hits = [a["title"] for a in arts if tag in a.get("tags", [])][:n]
        return "; ".join(f"\u201c{h}\u201d" for h in hits)
    acts = []
    if names("Available now"):
        acts.append(f"Ready to pilot now: {names('Available now', 1)}.")
    if names("Open source"):
        acts.append(f"Open source to evaluate in-house: {names('Open source', 1)}.")
    if names("API") and len(acts) < 2:
        acts.append(f"Available through an API: {names('API', 1)}.")
    if names("Preview") and len(acts) < 2:
        acts.append(f"Request early access to assess fit: {names('Preview', 1)}.")
    return acts[:2]


def _chapter(cid: str, arts: list[dict], used: set, unit: str) -> dict:
    """One themed chapter. `used` holds the ids already shown (top stories), so nothing repeats."""
    cat = config.CATEGORY_BY_ID[cid]
    kicker, advice = CHAPTER_COPY.get(cid, (cat["name"], ""))
    ordered = _spread(arts)
    fresh = [a for a in ordered if a["id"] not in used]
    key = fresh[:CHAPTER_ITEMS]
    shown = used | {a["id"] for a in key}
    rest = [a for a in ordered if a["id"] not in shown]
    more = rest[:CHAPTER_MORE]
    # "Ready to use": prefer items not already on the page, then the cards themselves.
    ready = _ready([a for a in rest if a["id"] not in {m["id"] for m in more}]) + _ready(rest) + _ready(key)
    seen_ready, ready_out = set(), []
    for r_ in ready:
        if r_["url"] not in seen_ready:
            seen_ready.add(r_["url"])
            ready_out.append(r_)
    sources = {a["source_id"] for a in arts}
    avail = sum(1 for a in arts if "Available now" in a.get("tags", []))
    oss = sum(1 for a in arts if "Open source" in a.get("tags", []))
    lead = arts[0]
    intro = (f"{len(arts)} stories from {len(sources)} sources this {unit}. "
             f"The most significant: \u201c{lead['title']}\u201d from {config.SOURCE_BY_ID[lead['source_id']]['name']}.")
    return {
        "id": cid, "name": cat["name"], "kicker": kicker, "color": cat["color"], "intro": intro,
        "items": [compact(a) for a in key], "more": [compact(a) for a in more],
        "ready": ready_out[:CHAPTER_READY],
        "stats": {"stories": len(arts), "sources": len(sources), "available": avail, "open_source": oss},
        "actions": _actions(cat["name"], ordered), "advice": advice,
    }


def _research_chapter(research_news: list[dict], top_papers: list[dict], research_all: list[dict],
                      papers: list[dict], unit: str) -> dict:
    kicker, advice = CHAPTER_COPY["research"]
    upvoted = [p for p in papers if p.get("extra", {}).get("upvotes")]
    best = top_papers[0] if top_papers else None
    intro = (f"{len(papers)} research papers and {len(research_all)} lab research posts this {unit}."
             + (f" The paper the community upvoted most: \u201c{best['title']}\u201d." if best else ""))
    return {
        "id": "research", "name": config.CATEGORY_BY_ID["research"]["name"], "kicker": kicker,
        "color": config.CATEGORY_BY_ID["research"]["color"], "intro": intro,
        "items": [compact(a) for a in (research_news + top_papers)[:CHAPTER_ITEMS]],
        "more": [compact(p) for p in (research_news + top_papers)[CHAPTER_ITEMS: CHAPTER_ITEMS + CHAPTER_MORE]],
        "ready": _ready([p for p in papers if "Open source" in p.get("tags", [])])[:CHAPTER_READY],
        "stats": {"stories": len(papers), "sources": len({p["source_id"] for p in papers}),
                  "available": sum(1 for p in upvoted if p["extra"]["upvotes"] >= 20),
                  "open_source": sum(1 for p in papers if "Open source" in p.get("tags", []))},
        "stat_labels": {"stories": "Papers", "available": "20+ upvotes"},
        "actions": [], "advice": advice, "research": True,
    }


def _brief(issue: dict, news: list[dict], papers: list[dict], top: list[dict], cat_counts: Counter,
           rising: list[dict]) -> list[str]:
    name = issue["title"]
    span = "so far in " if issue["status"] == "in_progress" else "in "
    lines = [f"We tracked {len(news)} stories from {issue['stats']['sources']} sources {span}{name}, "
             f"along with {len(papers)} research papers."]
    widest = max(news, key=lambda a: (len({_org(c['source_id']) for c in a.get('coverage', [])}), a["score"]))
    n_out = len({_org(c["source_id"]) for c in widest.get("coverage", [])} - {_org(widest["source_id"])})
    if n_out >= 1:
        lines.append(f"The most widely reported story was “{widest['title']}”, "
                     f"covered by {n_out + 1} independent outlets.")
    elif top:
        lines.append(f"The highest-ranked announcement was “{top[0]['title']}” "
                     f"from {config.SOURCE_BY_ID[top[0]['source_id']]['name']}.")
    leaders = [config.CATEGORY_BY_ID[c]["name"] for c, _ in sorted(cat_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
    if leaders:
        lines.append(f"{leaders[0]} drew the most coverage"
                     + (f", followed by {leaders[1]} and {leaders[2]}." if len(leaders) == 3 else "."))
    climbing = [r["topic"] for r in rising if (r["change_pct"] or 0) >= 25][:3]
    if climbing:
        lines.append("Topics gaining attention compared with the previous period: " + ", ".join(climbing) + ".")
    if papers:
        best = max(papers, key=lambda p: (p.get("extra", {}).get("upvotes", 0), p["score"]))
        ups = best.get("extra", {}).get("upvotes", 0)
        if ups:
            lines.append(f"In research, the most upvoted paper on Hugging Face was "
                         f"“{best['title']}” ({ups} upvotes).")
    return lines


def build_issue(period: dict, articles: list[dict], state: dict, now: datetime) -> dict | None:
    start, end = period["start"], period["end"]
    sizes = SIZES[period["type"]]
    items = [a for a in articles if _in(a, start, end)]
    news = [a for a in items if a["kind"] == "news"]
    papers = [a for a in items if a["kind"] == "paper"]
    if len(news) < MIN_STORIES:
        return None
    length = end - start
    prev = [a for a in articles if a["kind"] == "news" and _in(a, start - length, start)]

    top = _pick_top([a for a in news if a["score"] >= 25], sizes["top"])
    used = {a["id"] for a in top}
    top_papers = _spread(papers, key=lambda p: (-p.get("extra", {}).get("upvotes", 0),) + _rank(p))[: sizes["papers"]]
    unit = "month" if period["type"] == "monthly" else "quarter"

    by_cat = defaultdict(list)
    for a in sorted(news, key=_rank):
        by_cat[a["categories"][0]].append(a)
    ranked_cats = sorted((c for c in by_cat if c not in ("research", config.FALLBACK_CATEGORY) and len(by_cat[c]) >= 3),
                         key=lambda c: (-len(by_cat[c]), c))
    chapters = [_chapter(cid, by_cat[cid], used, unit) for cid in ranked_cats[: sizes["chapters"]]]
    research_news = [a for a in _spread(by_cat.get("research", [])) if a["id"] not in used][:2]
    chapters.append(_research_chapter(research_news, top_papers, by_cat.get("research", []), papers, unit))
    for i, ch in enumerate(chapters, 1):
        ch["number"] = i

    cat_counts = Counter(c for a in news for c in a["categories"][:1])
    rising = _rising(news, prev)
    src_counts = Counter(a["source_id"] for a in items)
    for a in items:
        for c in a.get("coverage", []):
            src_counts[c["source_id"]] += 1
    first_run = parse_iso(state.get("first_run", to_utc_iso(now))).date()

    issue = {
        "id": period["id"], "type": period["type"], "title": period["title"], "status": period["status"],
        "start": start.isoformat(), "end": (end - timedelta(days=1)).isoformat(),
        "generated": to_utc_iso(now),
        "backfilled": start < first_run,
        "stats": {"stories": len(news), "papers": len(papers),
                  "sources": len(src_counts), "categories": len(cat_counts)},
        "top_stories": [compact(a) for a in top],
        "chapters": chapters,
        "kpis": [
            {"value": len(news), "label": "Technology stories"},
            {"value": sum(1 for a in news if "Available now" in a.get("tags", [])), "label": "Ready to use now"},
            {"value": sum(1 for a in news if "Open source" in a.get("tags", [])), "label": "Open-source releases"},
            {"value": len(papers), "label": "Research papers"},
        ],
        "category_share": [{"id": c, "name": config.CATEGORY_BY_ID[c]["name"], "count": n,
                            "color": config.CATEGORY_BY_ID[c]["color"]} for c, n in sorted(cat_counts.items(), key=lambda kv: (-kv[1], kv[0]))],
        "rising": rising,
        "sources": [{"id": s, "name": config.SOURCE_BY_ID[s]["name"], "tier": config.SOURCE_BY_ID[s]["tier"],
                     "count": n} for s, n in sorted(src_counts.items(), key=lambda kv: (-kv[1], kv[0]))],
    }
    if period["type"] == "quarterly":
        issue["months"] = [
            {"month": m, "label": datetime.strptime(m, "%Y-%m").strftime("%B"), "stories": n,
             "highlights": [compact(a) for a in _pick_top([x for x in news if x["published"].startswith(m)], 3)]}
            for m, n in sorted(Counter(a["published"][:7] for a in news).items())]
    issue["brief"] = _brief(issue, news, papers, top, cat_counts, rising)
    issue["headline"] = top[0]["title"] if top else f"The AI technology that shipped in {period['title']}"
    issue["pills"] = [r["topic"] for r in rising[:4]]
    from .pdf_html import RENDER_VERSION
    content = {k: v for k, v in issue.items() if k != "generated"} | {"render_version": RENDER_VERSION}
    issue["hash"] = hashlib.sha1(json.dumps(content, sort_keys=True).encode()).hexdigest()[:16]
    return issue


# --------------------------------------------------------------------------- driver
def build_all(articles: dict[str, dict], state: dict, now: datetime) -> list[dict]:
    from .pdf_html import render_pdf_html
    from .pdf_render import render_pdf  # ReportLab fallback when Chrome is unavailable

    items = sorted((a for a in articles.values() if is_tech(a)), key=lambda a: (a["published"], a["id"]))
    tracking = parse_iso(state.get("tracking_since") or to_utc_iso(now - timedelta(days=config.BACKFILL_DAYS)))
    out_json = config.SITE_DATA_DIR / "newsletters"
    out_json.mkdir(parents=True, exist_ok=True)
    config.SITE_NEWSLETTER_DIR.mkdir(parents=True, exist_ok=True)
    index_path = config.SITE_DATA_DIR / "newsletters.json"
    try:
        previous = {i["id"]: i for i in json.loads(index_path.read_text())["issues"]}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        previous = {}

    index = []
    plist = periods(tracking, now)
    numbers = {}
    for kind in ("monthly", "quarterly"):
        for n, p in enumerate(sorted((p for p in plist if p["type"] == kind), key=lambda p: p["start"]), 1):
            numbers[p["id"]] = n
    for f in config.SITE_NEWSLETTER_DIR.glob("ait-*.pdf"):
        if not f.name.startswith("ait-gen-ai-pulse-"):
            f.unlink()  # files from the previous naming scheme
    for period in plist:
        issue = build_issue(period, items, state, now)
        if issue is None:
            continue
        issue["issue_no"] = numbers[period["id"]]
        slug = f"ait-gen-ai-pulse-{issue['type']}-{issue['id'].lower()}"
        pdf_path = config.SITE_NEWSLETTER_DIR / f"{slug}.pdf"
        prev = previous.get(issue["id"])
        if prev and prev.get("hash") == issue["hash"] and pdf_path.exists():
            issue["generated"] = prev["generated"]  # unchanged: keep the original timestamp
        else:
            if not render_pdf_html(issue, pdf_path):
                render_pdf(issue, pdf_path)
            log.info("newsletter: rendered %s (%d stories)", pdf_path.name, issue["stats"]["stories"])
        _write(out_json / f"{issue['id']}.json", issue)
        index.append({k: issue[k] for k in ("id", "type", "title", "status", "start", "end", "generated",
                                            "backfilled", "stats", "hash", "issue_no", "pills")}
                     | {"pdf": f"newsletters/{pdf_path.name}", "json": f"data/newsletters/{issue['id']}.json",
                        "pdf_bytes": pdf_path.stat().st_size,
                        "headline": issue["top_stories"][0]["title"] if issue["top_stories"] else None})
    _write(index_path, {"generated": to_utc_iso(now), "issues": index})
    log.info("newsletters: %d issues", len(index))
    return index
