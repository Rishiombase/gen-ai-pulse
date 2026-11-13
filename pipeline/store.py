"""Git-friendly JSON storage: data/articles/YYYY-MM.json (one file per publication month)."""
from __future__ import annotations

import json
from collections import defaultdict

from . import config
from .textutil import article_id, canonical_url

STATE_FILE = config.STATE_DIR / "state.json"
SOURCE_STATUS_FILE = config.STATE_DIR / "source_status.json"


def load_articles() -> dict[str, dict]:
    store: dict[str, dict] = {}
    if not config.ARCHIVE_DIR.exists():
        return store
    for path in sorted(config.ARCHIVE_DIR.glob("*.json")):
        for a in json.loads(path.read_text()):
            store[a["id"]] = a
    return store


def save_articles(store: dict[str, dict]) -> None:
    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    by_month = defaultdict(list)
    for a in store.values():
        by_month[a["published"][:7]].append(a)
    existing = {p.stem for p in config.ARCHIVE_DIR.glob("*.json")}
    for month, items in by_month.items():
        items.sort(key=lambda a: (a["published"], a["id"]), reverse=True)
        path = config.ARCHIVE_DIR / f"{month}.json"
        payload = json.dumps(items, ensure_ascii=False, indent=1, sort_keys=True)
        if not path.exists() or path.read_text() != payload:
            path.write_text(payload)
    for stale in existing - set(by_month):
        (config.ARCHIVE_DIR / f"{stale}.json").unlink()


def known_ids(store: dict[str, dict]) -> set[str]:
    """Ids of every article we have seen, including ones folded into a cluster as coverage."""
    ids = set(store)
    for a in store.values():
        for c in a.get("coverage", []):
            ids.add(article_id(canonical_url(c["url"])))
    return ids


def load_state() -> dict:
    try:
        state = json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    for k, v in list(state.items()):
        if k.startswith("sitemap_seen:"):
            state[k] = set(v)
    return state


def save_state(state: dict) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    out = {k: (sorted(v) if isinstance(v, set) else v) for k, v in state.items()}
    STATE_FILE.write_text(json.dumps(out, indent=1, sort_keys=True))


def save_source_status(status: dict) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_STATUS_FILE.write_text(json.dumps(status, indent=1, sort_keys=True))


def load_source_status() -> dict:
    try:
        return json.loads(SOURCE_STATUS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
