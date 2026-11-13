"""Duplicate removal and same-story clustering.

Layer 1 (exact): items whose canonical URL matches are the same article; fields merge.
Layer 2 (story): different outlets covering the same story are clustered. The most
authoritative item (lowest tier number, then earliest) becomes the primary article;
the others are kept as `coverage` links so readers can compare reporting.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import timedelta

from . import config
from .embeddings import Embedder
from .textutil import parse_iso, tokens

JACCARD_MIN = 0.5                    # plain token Jaccard on titles
OVERLAP_MIN, OVERLAP_MIN_SHARED = 0.8, 4
WJ_ALONE = 0.45                      # IDF-weighted Jaccard strong enough on its own
# (embedding similarity of title+lede, IDF-weighted title Jaccard) pairs; either rule matches.
EMB_RULES = ((0.86, 0.15), (0.80, 0.25))


def merge_exact(a: dict, b: dict) -> dict:
    """Merge b into a (same canonical URL). Prefer the more authoritative source's metadata."""
    if config.SOURCE_BY_ID[b["source_id"]]["tier"] < config.SOURCE_BY_ID[a["source_id"]]["tier"]:
        a, b = b, a
    ea, eb = a.setdefault("extra", {}), b.get("extra", {})
    for k, v in eb.items():
        if k == "upvotes":
            ea[k] = max(int(ea.get(k, 0)), int(v))
        else:
            ea.setdefault(k, v)
    if len(b.get("summary", "")) > len(a.get("summary", "")) * 1.5:
        a["summary"] = b["summary"]
    return a


def _title_sim(ta: set, tb: set) -> tuple[float, float, int]:
    if not ta or not tb:
        return 0.0, 0.0, 0
    inter = len(ta & tb)
    return inter / len(ta | tb), inter / min(len(ta), len(tb)), inter


def weighted_jaccard(ta: set, tb: set, idf: dict) -> float:
    union = ta | tb
    if not union:
        return 0.0
    return sum(idf.get(t, 1.0) for t in ta & tb) / sum(idf.get(t, 1.0) for t in union)


def same_story(ta: set, tb: set, emb_sim: float | None, idf: dict | None = None) -> bool:
    """Two titles describe the same story. Rare shared words (product names) weigh most."""
    jac, ovl, inter = _title_sim(ta, tb)
    if jac >= JACCARD_MIN:
        return True
    if ovl >= OVERLAP_MIN and inter >= OVERLAP_MIN_SHARED:
        return True
    wj = weighted_jaccard(ta, tb, idf or {})
    if wj >= WJ_ALONE:
        return True
    return emb_sim is not None and any(emb_sim >= e and wj >= w for e, w in EMB_RULES)


def org_of(source_id: str) -> str:
    src = config.SOURCE_BY_ID[source_id]
    return src.get("org", src["id"])


def _coverage_entry(a: dict) -> dict:
    return {"source_id": a["source_id"], "url": a["url"], "title": a["title"], "published": a["published"]}


def cluster(new_items: list[dict], store: dict[str, dict], embedder: Embedder) -> tuple[list[dict], int]:
    """Attach new items to existing or new clusters.

    Returns (articles that should be (re)classified as primaries, number of items merged as coverage).
    `store` is mutated: primaries are added, replaced primaries are removed.
    """
    if not new_items:
        return [], 0
    window = timedelta(days=config.CLUSTER_WINDOW_DAYS)
    earliest = min(parse_iso(a["published"]) for a in new_items) - window
    pool = [a for a in store.values() if a["kind"] == "news" and parse_iso(a["published"]) >= earliest]
    candidates = pool + [a for a in new_items if a["kind"] == "news"]
    toks = {a["id"]: set(tokens(a["title"])) for a in candidates}
    df = Counter(t for ts in toks.values() for t in ts)
    idf = {t: math.log((len(toks) + 1) / (n + 0.5)) for t, n in df.items()}

    emb_index = {}
    if embedder.available and candidates:
        vecs = embedder.embed([f"{a['title']}. {a['summary'][:200]}" for a in candidates])
        emb_index = {a["id"]: vecs[i] for i, a in enumerate(candidates)}

    primaries: list[dict] = list(pool)   # news primaries we can attach to
    to_classify: list[dict] = []
    merged = 0
    for item in sorted(new_items, key=lambda a: a["published"]):
        if item["kind"] != "news":
            store[item["id"]] = item
            to_classify.append(item)
            continue
        t_item = parse_iso(item["published"])
        match = None
        best = 0.0
        for p in primaries:
            if p["source_id"] == item["source_id"]:  # an outlet never duplicates itself
                continue
            if abs(parse_iso(p["published"]) - t_item) > window:
                continue
            emb_sim = float(emb_index[p["id"]] @ emb_index[item["id"]]) if emb_index else None
            if same_story(toks[item["id"]], toks[p["id"]], emb_sim, idf):
                score = emb_sim if emb_sim is not None else _title_sim(toks[item["id"]], toks[p["id"]])[0]
                if score >= best:
                    match, best = p, score
        if match is None:
            item.setdefault("coverage", [])
            store[item["id"]] = item
            primaries.append(item)
            to_classify.append(item)
            continue
        merged += 1
        covered = {c["source_id"] for c in match.get("coverage", [])} | {match["source_id"]}
        tier_item = config.SOURCE_BY_ID[item["source_id"]]["tier"]
        tier_match = config.SOURCE_BY_ID[match["source_id"]]["tier"]
        if tier_item < tier_match:
            # The new item is more authoritative: promote it to primary.
            item["coverage"] = [_coverage_entry(match)] + match.get("coverage", [])
            store.pop(match["id"], None)
            primaries.remove(match)
            store[item["id"]] = item
            primaries.append(item)
            to_classify.append(item)
            if match in to_classify:
                to_classify.remove(match)
        elif item["source_id"] not in covered:
            match.setdefault("coverage", []).append(_coverage_entry(item))
    return to_classify, merged
