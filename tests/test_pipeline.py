"""Unit tests for the parts of the pipeline most likely to break silently."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pipeline import config
from pipeline.classify import Classifier, is_ai_relevant
from pipeline.dedupe import cluster, merge_exact, same_story
from pipeline.embeddings import Embedder
from pipeline.enrich import _candidates, generic_summaries
from pipeline.focus import FocusFilter, usage_tags
from pipeline.newsletter import build_issue, periods
from pipeline.scoring import importance
from pipeline.textutil import article_id, canonical_url, clean_html, summarize, to_utc_iso, tokens

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
LEXICON_ONLY = Embedder(enabled=False)


def art(title, source="techcrunch", summary="", days_ago=0.0, kind="news", **extra):
    url = f"https://example.com/{abs(hash((title, source)))}"
    return {"id": article_id(canonical_url(url)), "url": url, "title": title, "summary": summary,
            "source_id": source, "kind": kind, "published": to_utc_iso(NOW - timedelta(days=days_ago)),
            "extra": extra, "coverage": []}


# ------------------------------------------------------------------ text
@pytest.mark.parametrize("a,b", [
    ("https://www.Example.com/post/?utm_source=x&id=3#top", "https://example.com/post?id=3"),
    ("http://arxiv.org/pdf/2611.01234v2", "https://arxiv.org/abs/2611.01234"),
    ("https://arxiv.org/abs/2611.01234v1", "https://arxiv.org/abs/2611.01234"),
])
def test_canonical_url(a, b):
    assert canonical_url(a) == b


def test_clean_html_handles_gt_inside_attributes():
    raw = '<div class="[&>a]:hidden">Back</div><p>Real <b>text</b> &amp; more</p>'
    out = clean_html(raw)
    assert "hidden" not in out and "Real text & more" in out


def test_clean_html_strips_feed_boilerplate():
    assert clean_html("Great news. The post Great news appeared first on Example.") == "Great news."


def test_summarize_respects_sentence_boundaries():
    text = "First sentence is here. Second one follows. " + "Third is very long " * 30
    out = summarize(text, 60)
    assert out == "First sentence is here. Second one follows."


def test_tokens_drop_stopwords_and_plurals():
    assert tokens("OpenAI launches the new models") == ["openai", "launche", "model"]


# ------------------------------------------------------------------ dedupe
def test_same_story_on_rewritten_headline():
    a = set(tokens("Jensen Huang is out at Nvidia — who is Colette Kress, his interim replacement"))
    b = set(tokens("Meet Colette Kress, Nvidia's new interim CEO"))
    idf = {"colette": 4.0, "kres": 4.0, "interim": 3.0, "nvidia": 1.0}
    assert same_story(a, b, emb_sim=0.93, idf=idf)


def test_different_stories_from_same_company_do_not_merge():
    a = set(tokens("OpenAI launches Sora app for Android"))
    b = set(tokens("OpenAI launches GPT-6 with longer context"))
    # Realistic IDF: company names and "launches" are common, product names are rare.
    idf = {"openai": 0.4, "launche": 0.6, "sora": 4.0, "android": 3.5, "app": 1.5, "gpt-6": 3.0,
           "longer": 2.5, "context": 2.5}
    assert not same_story(a, b, emb_sim=0.8, idf=idf)


def test_cluster_promotes_more_authoritative_source():
    store = {}
    press = art("Google introduces Gemini 9 Ultra model", source="techcrunch", days_ago=0.2)
    official = art("Introducing Gemini 9 Ultra model", source="google-ai", days_ago=0.1)
    to_classify, merged = cluster([press, official], store, LEXICON_ONLY)
    assert merged == 1
    assert list(store) == [official["id"]]
    assert [c["source_id"] for c in store[official["id"]]["coverage"]] == ["techcrunch"]


def test_merge_exact_keeps_max_upvotes_and_better_source():
    a = art("Paper", source="arxiv", kind="paper")
    b = dict(art("Paper", source="hf-papers", kind="paper"), extra={"upvotes": 40})
    merged = merge_exact(a, b)
    assert merged["source_id"] == "hf-papers" and merged["extra"]["upvotes"] == 40


# ------------------------------------------------------------------ classification
@pytest.mark.parametrize("title,expected", [
    ("How a bank uses AI agents to automate customer support in production", "enterprise"),
    ("Meta releases open-weights Llama model under Apache 2.0", "open-source"),
    ("New guardrails defend LLM apps against prompt injection and jailbreaks", "security"),
    ("New humanoid robot learns household manipulation tasks", "robotics"),
    ("Multi-agent framework lets AI agents coordinate tool calling", "agents"),
])
def test_lexicon_classification(title, expected):
    a = art(title)
    Classifier(LEXICON_ONLY).classify([a])
    assert expected in a["categories"]


def test_papers_always_tagged_research():
    p = art("Segmentation with vision transformers", source="arxiv", kind="paper", arxiv_category="cs.CV")
    Classifier(LEXICON_ONLY).classify([p])
    assert p["categories"][0] == "computer-vision" and "research" in p["categories"]


def test_ai_relevance_filter():
    assert is_ai_relevant("New GPU boosts LLM inference", "")
    assert not is_ai_relevant("Our new gaming laptop lineup", "Faster displays and better battery life.")


# ------------------------------------------------------------------ enrichment
def test_generic_summaries_detected_per_source():
    items = [art(f"Post {i}", source="anthropic", summary="Anthropic is an AI safety company.") for i in range(2)]
    assert generic_summaries(items)["anthropic"] == {"Anthropic is an AI safety company."}


def test_page_candidates_skip_bylines():
    html = ('<meta property="og:description" content="Short.">'
            '<article><p>Published August 25, 2026 by Someone Important at the Company Blog</p>'
            '<p>' + "This release closes the gap between simulation and real robots with new tools. " * 2 + '</p></article>')
    cands = _candidates(html)
    assert cands and cands[0].startswith("This release closes the gap")


# ------------------------------------------------------------------ scoring & newsletters
def test_coverage_from_other_orgs_raises_importance():
    solo = art("Introducing a new model", source="openai")
    covered = dict(solo, coverage=[{"source_id": "techcrunch"}, {"source_id": "verge"}])
    same_org = dict(art("Introducing a new model", source="google-ai"), coverage=[{"source_id": "deepmind"}])
    assert importance(covered) > importance(solo)
    assert importance(same_org) == importance(dict(same_org, coverage=[]))


def test_periods_only_include_fully_tracked_ranges():
    ps = periods(datetime(2026, 7, 1, tzinfo=timezone.utc), NOW)
    ids = {p["id"] for p in ps}
    assert {"2026-07", "2026-10", "2026-11", "2026-Q3", "2026-Q4"} <= ids
    assert "2026-06" not in ids
    assert next(p for p in ps if p["id"] == "2026-11")["status"] == "in_progress"
    assert next(p for p in ps if p["id"] == "2026-10")["status"] == "final"


def test_build_issue_and_render_pdf(tmp_path):
    from pipeline.pdf_render import render_pdf
    cls = Classifier(LEXICON_ONLY)
    items = [art(f"OpenAI launches tool number {i} for developers", source=s, days_ago=1 + i % 9,
                 summary="A new tool for developers. It ships today with an API.")
             for i, s in enumerate(["openai", "techcrunch", "verge", "anthropic", "deepmind"] * 5)]
    items.append(art("A paper on agents", source="hf-papers", kind="paper", days_ago=2, upvotes=50,
                     summary="We propose agents. Café ünïcode — and emoji 🚀 are handled."))
    cls.classify(items)
    for a in items:
        a["score"] = importance(a)
    period = next(p for p in periods(datetime(2026, 11, 1, tzinfo=timezone.utc), NOW) if p["id"] == "2026-11")
    issue = build_issue(period, items, {"first_run": "2026-11-01T00:00:00Z"}, NOW)
    assert issue and issue["stats"]["stories"] == 25
    assert len({a["source"] for a in issue["top_stories"]}) >= 3  # diversity cap
    research = issue["chapters"][-1]
    assert research["research"] and research["items"][0]["upvotes"] == 50
    assert all(c["advice"] for c in issue["chapters"])
    # Fallback (ReportLab) renderer
    out = tmp_path / "issue.pdf"
    render_pdf(issue, out)
    data = out.read_bytes()
    assert data.startswith(b"%PDF") and b"/URI" in data  # clickable source links present
    # Primary (HTML) layout: one A4 page per section, logo embedded, links to sources
    from pipeline.pdf_html import build_html
    page = build_html(issue)
    assert page.count('class="page') == 2 + len(issue["chapters"]) + 1
    assert "data:image/png;base64," in page and 'href="https://example.com/' in page


# ------------------------------------------------------------------ editorial focus
@pytest.mark.parametrize("title,source,expected", [
    ("Jensen Huang resigns as Nvidia CEO amid scandal", "wired", "off"),
    ("Senators launch investigation into AI company's lobbying", "techcrunch", "off"),
    ("AI startup raises $300 million Series D at $5 billion valuation", "techcrunch", "off"),
    ("Meta releases open-weights Llama model with a new API for developers", "techcrunch", "tech"),
    ("How NVIDIA AI agents investigate production failures on the factory floor", "nvidia-dev", "tech"),
])
def test_focus_filter(title, source, expected):
    a = art(title, source=source)
    FocusFilter(LEXICON_ONLY).apply([a])
    assert a["focus"] == expected


def test_papers_are_always_technical():
    p = art("Lawsuit-proof training: a study of court records", source="arxiv", kind="paper")
    FocusFilter(LEXICON_ONLY).apply([p])
    assert p["focus"] == "tech"


def test_usage_tags():
    a = art("Mistral releases open-weights model with an API, now available", summary="")
    assert usage_tags(a) == ["Open source", "API", "Available now"]


def test_quarterly_selection_spreads_across_months():
    from pipeline.newsletter import _spread
    oct_items = [dict(art(f"October story {i}", days_ago=30 + i), score=40.0) for i in range(5)]
    nov_items = [dict(art(f"November story {i}", days_ago=1 + i), score=40.0) for i in range(5)]
    first_four = _spread(oct_items + nov_items)[:4]
    months = {a["published"][:7] for a in first_four}
    assert len(months) == 2, "equal scores must not let the newest month crowd out the others"
