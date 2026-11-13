"""Pipeline entry point.

    python -m pipeline.run                 # fetch, dedupe, classify, score, build site + newsletters
    python -m pipeline.run --no-fetch      # rebuild site + newsletters from the archive only
    python -m pipeline.run --reclassify    # re-run categorisation over the whole archive
    python -m pipeline.run --no-embeddings # lexicon-only mode (no model download)
"""
from __future__ import annotations

import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from . import build_site, config, newsletter, store
from .classify import Classifier, is_ai_relevant
from .dedupe import cluster, merge_exact
from .embeddings import Embedder
from .enrich import enrich
from .focus import FocusFilter
from .fetchers import FETCHERS
from .http import HttpClient
from .scoring import importance
from .textutil import article_id, canonical_url, now_utc, summarize, to_utc_iso

log = logging.getLogger("pipeline")


def fetch_all(http: HttpClient, ctx: dict, only: set[str] | None) -> tuple[list[dict], dict]:
    status, raw = {}, []
    sources = [s for s in config.SOURCES if not only or s["id"] in only]

    def run(src):
        t0 = time.monotonic()
        items = FETCHERS[src["type"]](src, http, ctx)
        return src, items, time.monotonic() - t0

    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
        futures = {pool.submit(run, s): s for s in sources}
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                _, items, secs = fut.result()
                raw += items
                status[src["id"]] = {"ok": True, "items": len(items), "seconds": round(secs, 1),
                                     "checked": to_utc_iso(now_utc())}
                log.info("  %-18s %4d items  %.1fs", src["id"], len(items), secs)
            except Exception as exc:
                status[src["id"]] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300],
                                     "checked": to_utc_iso(now_utc())}
                log.warning("  %-18s FAILED: %s", src["id"], exc)
    return raw, status


def normalise(raw: list[dict], ctx: dict) -> list[dict]:
    now, out = ctx["now"], []
    dropped = {"old": 0, "future": 0, "off_topic": 0, "bad": 0}
    for r in raw:
        src = config.SOURCE_BY_ID[r["source_id"]]
        pub = r["published"]
        if len(r["title"]) < 8 or not r["url"]:
            dropped["bad"] += 1
            continue
        if pub > now + timedelta(days=1):
            dropped["future"] += 1
            continue
        if pub < ctx["cutoff"]:
            dropped["old"] += 1
            continue
        if src.get("filter_ai") and not is_ai_relevant(r["title"], r["summary"]):
            dropped["off_topic"] += 1
            continue
        canon = canonical_url(r["url"])
        out.append({
            "id": article_id(canon), "url": r["url"], "title": r["title"],
            "summary": summarize(r["summary"], 420 if r["kind"] == "paper" else 320),
            "source_id": r["source_id"], "kind": r["kind"],
            "published": to_utc_iso(min(pub, now)), "fetched": to_utc_iso(now),
            "extra": r.get("extra", {}), "coverage": [],
        })
    log.info("normalised %d items (dropped %s)", len(out), dropped)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-fetch", action="store_true", help="skip fetching; rebuild outputs from the archive")
    ap.add_argument("--no-embeddings", action="store_true", help="disable the local embedding model")
    ap.add_argument("--reclassify", action="store_true", help="re-categorise every archived article")
    ap.add_argument("--no-cache", action="store_true", help="ignore ETag/Last-Modified cache")
    ap.add_argument("--sources", help="comma-separated source ids to fetch (default: all)")
    ap.add_argument("--no-newsletters", action="store_true", help="skip newsletter generation")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("urllib3", "httpx", "huggingface_hub", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    now = now_utc()
    state = store.load_state()
    articles = store.load_articles()
    first_run = not articles
    if first_run:
        state.setdefault("first_run", to_utc_iso(now))
    state.setdefault("tracking_since", to_utc_iso(now - timedelta(days=config.BACKFILL_DAYS)))
    log.info("archive: %d articles (%s run)", len(articles), "first" if first_run else "regular")

    embedder = Embedder(enabled=not args.no_embeddings)
    classifier = Classifier(embedder)
    focus = FocusFilter(embedder)

    if not args.no_fetch:
        max_age = config.BACKFILL_DAYS if first_run else config.REGULAR_MAX_AGE_DAYS
        ctx = {"now": now, "backfill": first_run, "max_age_days": max_age,
               "cutoff": now - timedelta(days=max_age), "known_ids": store.known_ids(articles),
               "state": state, "conditional": not (args.no_cache or first_run)}
        http = HttpClient()
        log.info("fetching %d sources…", len(config.SOURCES))
        raw, status = fetch_all(http, ctx, set(args.sources.split(",")) if args.sources else None)
        http.save_cache()
        prev_status = store.load_source_status()
        for sid, st in status.items():
            prev = prev_status.get(sid, {})
            if st["ok"]:
                st["last_success"] = st["checked"]
            elif prev.get("last_success"):
                st["last_success"] = prev["last_success"]
        store.save_source_status({**prev_status, **status})

        items = normalise(raw, ctx)
        # Layer 1: exact duplicates (same canonical URL) within this batch and against the archive.
        batch: dict[str, dict] = {}
        for it in items:
            batch[it["id"]] = merge_exact(batch[it["id"]], it) if it["id"] in batch else it
        new, updated = [], 0
        known = ctx["known_ids"]
        for aid, it in batch.items():
            if aid in articles:
                old = articles[aid]
                up = it.get("extra", {}).get("upvotes")
                if up is not None and up != old.get("extra", {}).get("upvotes"):
                    old.setdefault("extra", {})["upvotes"] = max(up, old["extra"].get("upvotes", 0))
                    updated += 1
            elif aid not in known:
                new.append(it)
        enrich(new, list(articles.values()), http, limit=700 if first_run else 120)
        # Layer 2: same story across outlets.
        to_classify, merged = cluster(new, articles, embedder)
        classifier.classify(to_classify)
        hidden = focus.apply(to_classify)
        log.info("focus: %d of %d new stories hidden as non-technical", hidden, len(to_classify))
        log.info("new: %d primary, %d folded into existing stories, %d updated", len(to_classify), merged, updated)
        state["last_fetch"] = to_utc_iso(now_utc())  # when the data was actually collected

    if args.reclassify:
        log.info("reclassifying %d articles…", len(articles))
        classifier.classify(list(articles.values()))
        hidden = focus.apply(list(articles.values()))
        log.info("focus: %d stories hidden as non-technical", hidden)
    missing = [a for a in articles.values() if "focus" not in a]
    if missing:  # archives written before the focus filter existed
        focus.apply(missing)
    for a in articles.values():
        a["score"] = importance(a)

    store.save_articles(articles)
    store.save_state(state)

    build_site.build(articles, state, now)
    if not args.no_newsletters:
        newsletter.build_all(articles, state, now)
    log.info("done.")


if __name__ == "__main__":
    main()
