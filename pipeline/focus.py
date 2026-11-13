"""Editorial focus: keep technology that companies can use, hide people/politics/money stories.

Each news article gets:
    focus      "tech" or "off"
    tags       usage badges such as "Open source", "API", "Available now"

The decision combines two signals:
  * lexicon: weighted off-topic phrases (resignations, lawsuits, funding...) vs technical vocabulary
  * embeddings: similarity to technical vs off-topic prototype descriptions (when the model is available)
Research papers are always technical.
"""
from __future__ import annotations

import re

from . import config
from .embeddings import Embedder

_OFF = [(re.compile(p, re.I), w) for p, w in config.OFF_TOPIC_PATTERNS.items()]
_TECH = [(re.compile(p, re.I), w) for p, w in config.TECH_PATTERNS.items()]
_TAGS = {name: re.compile(p, re.I) for name, p in config.USAGE_TAGS.items()}

# Thresholds, tuned by inspecting the archive. Run `python -m pipeline.focus` for a report.
# News outlets: hide when off-topic vocabulary clearly outweighs technical vocabulary.
PRESS_OFF_MIN, PRESS_DOMINANCE = 4.0, 3.0
# Company and research blogs rarely publish gossip but do say "investigate" or "partners":
# hide only when the embedding model also leans off-topic.
BLOG_OFF_MIN, BLOG_EMB_MARGIN = 5.0, 0.03


# Headlines that are about a person's job, whatever the source: always hidden.
_PEOPLE_TITLE = re.compile(
    r"\b(appoints?|appointed|names? .{0,40}\b(ceo|cto|cfo|chief|president|head of)|chief [a-z ]{0,30}officer|"
    r"joins .{0,40} as|steps? down|resigns?|resignation|ousted|new (ceo|cto|cfo)|interim (ceo|cto|cfo)|board of directors)\b",
    re.I)


def _is_press(source_id: str) -> bool:
    src = config.SOURCE_BY_ID[source_id]
    return src["tier"] == 3 or bool(src.get("press"))


def _lex(patterns, title: str, summary: str) -> float:
    return sum(2 * w if p.search(title) else (w if p.search(summary) else 0) for p, w in patterns)


def usage_tags(a: dict) -> list[str]:
    text_t, text_s = a["title"], a.get("summary", "")
    tags = [name for name, pat in _TAGS.items() if pat.search(text_t) or pat.search(text_s)]
    if a["kind"] == "paper" or "research" in a.get("categories", []):
        tags.append("Research")
    if "Available now" in tags and "Preview" in tags:
        tags.remove("Available now")
    return tags[:3]


class FocusFilter:
    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self._tech = self._off = None
        if embedder.available:
            import numpy as np
            t = embedder.embed(config.TECH_PROTOTYPES)
            o = embedder.embed(config.OFF_TOPIC_PROTOTYPES)
            self._tech, self._off = t, o
            self._np = np

    def signals(self, articles: list[dict]) -> list[tuple[float, float, float | None]]:
        """(off-topic lexicon score, technical lexicon score, embedding margin off-minus-tech)."""
        margins = [None] * len(articles)
        if self._tech is not None and articles:
            vecs = self.embedder.embed([f"{a['title']}. {a.get('summary', '')[:400]}" for a in articles])
            tech = (vecs @ self._tech.T).max(axis=1)
            off = (vecs @ self._off.T).max(axis=1)
            margins = [float(o - t) for o, t in zip(off, tech)]
        return [(_lex(_OFF, a["title"], a.get("summary", "")), _lex(_TECH, a["title"], a.get("summary", "")), m)
                for a, m in zip(articles, margins)]

    def apply(self, articles: list[dict]) -> int:
        """Set focus and tags in place. Returns the number of articles hidden."""
        hidden = 0
        for a, (off, tech, margin) in zip(articles, self.signals(articles)):
            a["tags"] = usage_tags(a)
            if a["kind"] == "paper":
                a["focus"] = "tech"
                continue
            if _PEOPLE_TITLE.search(a["title"]):
                off_topic = True
            elif _is_press(a["source_id"]):
                off_topic = off >= PRESS_OFF_MIN and off >= tech + PRESS_DOMINANCE
            elif margin is not None:
                off_topic = off >= BLOG_OFF_MIN and margin >= BLOG_EMB_MARGIN
            else:  # lexicon-only mode: be conservative with first-party sources
                off_topic = off >= BLOG_OFF_MIN and off >= 2 * tech + 6
            a["focus"] = "off" if off_topic else "tech"
            hidden += off_topic
        return hidden


def is_tech(a: dict) -> bool:
    return a.get("focus", "tech") == "tech"


def report(days: int = 21) -> None:
    """Print which recent stories are hidden or kept, for tuning the thresholds."""
    import json
    from datetime import timedelta
    from .textutil import now_utc, to_utc_iso
    cutoff = to_utc_iso(now_utc() - timedelta(days=days))
    arts = [a for f in sorted(config.ARCHIVE_DIR.glob("*.json")) for a in json.loads(f.read_text())
            if a["kind"] == "news" and a["published"] >= cutoff]
    ff = FocusFilter(Embedder())
    for a, (off, tech, m) in sorted(zip(arts, ff.signals(arts)), key=lambda r: -r[1][0]):
        if off == 0:
            continue
        ff.apply([a])
        flag = "HIDE" if a["focus"] == "off" else "keep"
        print(f"{flag} off={off:4.1f} tech={tech:4.1f} emb={m if m is None else round(m, 3)!s:>6} "
              f"{a['source_id'][:12]:12} {a['title'][:80]}")


if __name__ == "__main__":
    report()
