"""Hybrid AI categorisation: weighted lexicon + zero-shot embedding similarity.

score(category) = LEX_WEIGHT * lexicon_score_normalised + EMB_WEIGHT * embedding_score_normalised

The lexicon gives precise, explainable matches on domain vocabulary; the embedding
model (bge-small) catches articles that discuss a topic without its keywords.
Articles can carry up to MAX_LABELS categories; the first one is the primary.
"""
from __future__ import annotations

import re

from . import config
from .embeddings import Embedder

LEX_WEIGHT, EMB_WEIGHT = 0.55, 0.45
SELECT_THRESHOLD = 0.62   # relative score needed for a secondary label
MIN_LEX_FOR_LABEL = 2.0   # or an embedding similarity above EMB_ABS_MIN
EMB_ABS_MIN = 0.70
MAX_LABELS = 3

_COMPILED = {c["id"]: [(re.compile(p, re.I), w) for p, w in c["keywords"].items()] for c in config.CATEGORIES}
_AI_RE = re.compile(config.AI_RELEVANCE_PATTERN, re.I)
_STRONG_AI_RE = re.compile(
    r"\b(ai|artificial intelligence|machine learning|deep learning|neural networks?|llms?|"
    r"language models?|chatgpt|gpt[- ]?\d|claude|gemini|generative|agentic|copilot|transformers?|"
    r"diffusion models?|computer vision|robotics?|reinforcement learning)\b", re.I)

# arXiv primary categories map strongly onto our taxonomy.
_ARXIV_HINTS = {"cs.CV": "computer-vision", "cs.CL": "nlp", "cs.RO": "robotics", "cs.MA": "agents",
                "cs.LG": "ai-ml", "cs.AI": "ai-ml"}
_ARXIV_HINT_BONUS = 0.35
_SOURCE_HINT_BONUS = 0.2


def lexicon_scores(title: str, summary: str) -> dict[str, float]:
    scores = {}
    for cid, pats in _COMPILED.items():
        s = 0.0
        for pat, w in pats:
            if pat.search(title):
                s += 2 * w
            elif pat.search(summary):
                s += w
        scores[cid] = s
    return scores


def is_ai_relevant(title: str, summary: str) -> bool:
    """Used for mixed-topic feeds: require clear AI vocabulary."""
    if _STRONG_AI_RE.search(title):
        return True
    strong = len(_STRONG_AI_RE.findall(summary))
    weak = len(_AI_RE.findall(summary))
    return strong >= 2 or (strong >= 1 and weak >= 3)


class Classifier:
    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self.cat_ids = [c["id"] for c in config.CATEGORIES]
        self._protos = None
        if embedder.available:
            import numpy as np
            rows = []
            for c in config.CATEGORIES:
                vecs = embedder.embed([f"{c['name']}."] + c["prototypes"])
                centroid = vecs.mean(axis=0)
                rows.append(centroid / np.linalg.norm(centroid))
            self._protos = np.stack(rows)

    def classify(self, articles: list[dict]) -> None:
        """Annotate articles in place with `categories` and `category_scores`."""
        if not articles:
            return
        sims = None
        if self._protos is not None:
            texts = [f"{a['title']}. {a['summary'][:600]}" for a in articles]
            sims = self.embedder.embed(texts) @ self._protos.T
        for i, a in enumerate(articles):
            lex = lexicon_scores(a["title"], a["summary"])
            lex_max = max(lex.values()) or 1.0
            combined = {}
            if sims is not None:
                row = sims[i]
                lo, hi = float(row.min()), float(row.max())
                span = (hi - lo) or 1.0
                for j, cid in enumerate(self.cat_ids):
                    combined[cid] = LEX_WEIGHT * (lex[cid] / lex_max) + EMB_WEIGHT * ((float(row[j]) - lo) / span)
            else:
                combined = {cid: lex[cid] / lex_max for cid in self.cat_ids}
            hint = _ARXIV_HINTS.get(a.get("extra", {}).get("arxiv_category", ""))
            if hint:
                combined[hint] += _ARXIV_HINT_BONUS
            src_hint = config.SOURCE_BY_ID[a["source_id"]].get("hint")
            if src_hint:
                combined[src_hint] += _SOURCE_HINT_BONUS

            top = max(v for k, v in combined.items() if k != config.FALLBACK_CATEGORY) or 1.0
            labels = []
            for cid, score in sorted(combined.items(), key=lambda kv: -kv[1]):
                if cid == config.FALLBACK_CATEGORY:
                    continue
                emb_ok = sims is not None and float(sims[i][self.cat_ids.index(cid)]) >= EMB_ABS_MIN
                evidence = lex[cid] >= MIN_LEX_FOR_LABEL or emb_ok or cid in (hint, src_hint)
                if not evidence:
                    continue
                if not labels or score >= SELECT_THRESHOLD * top:
                    labels.append(cid)
                if len(labels) == MAX_LABELS:
                    break
            if a["kind"] == "paper" and "research" not in labels:
                labels = (labels[: MAX_LABELS - 1] + ["research"]) if labels else ["research"]
            if not labels:
                labels = [config.FALLBACK_CATEGORY]
            a["categories"] = labels
            a["category_scores"] = {k: round(v, 3) for k, v in combined.items() if v > 0.25}
