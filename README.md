# Gen AI Pulse by AIT Global

An automated AI news site. It collects news, research, and launches from 30 authoritative sources, removes duplicates, categorises everything with a local AI model, and publishes a fast static website with downloadable monthly and quarterly PDF newsletters.

It runs entirely on free resources: public RSS feeds and APIs, open-source Python libraries, a local embedding model, GitHub Actions, and GitHub Pages. There are no API keys, no paid services, and no servers to maintain.

## What it does

| Feature | How |
|---|---|
| News aggregation | RSS/Atom feeds, the arXiv API, the Hugging Face Daily Papers API, and sitemaps for labs without feeds (Anthropic) |
| Source credibility | Every source has a tier: primary source, research institution, established press, specialist analysis, or preprint. The tier drives ranking and is shown on every story |
| AI categorisation | Local `bge-small-en-v1.5` embeddings (via fastembed, CPU-only) compared against category descriptions, combined with a weighted domain vocabulary. Up to 3 of 12 categories per story |
| Technology focus | Stories mainly about people, politics, lawsuits, or money are hidden from the site and newsletters (kept in the archive). Keyword scoring plus the embedding model decide; run `python -m pipeline.focus` to see each decision |
| Usage badges | Available now, Preview, Open source, API, For developers, Enterprise, Research: how a company could act on each story |
| Duplicate removal | Canonical URLs merge exact duplicates. The same story from several outlets is grouped under its most authoritative report, with the others listed as "also reported by" |
| Ranking | Source tier + number of independent outlets + launch/funding/regulation signals + community upvotes (papers) − noise (tutorials, podcasts, deals) |
| Dashboard | Lead story, category filters, full-text search, time period and monthly archives, trending topics, top papers, newsletter downloads |
| Newsletters | Monthly and quarterly *Gen AI Pulse* editions: cover with KPIs, top stories, one chapter page per theme (4 key developments, quick stats, ready-to-use list, "What this means for AIT Global"), research, trends and sources. Laid out in HTML/CSS and printed to PDF by headless Chrome (`pipeline/pdf_html.py`); falls back to ReportLab if Chrome is missing. Also readable online |
| Automation | GitHub Actions runs every 3 hours: fetch, dedupe, classify, rebuild, commit the archive, deploy |

## Quick start (local)

Requires Python 3.10+ (CI uses 3.12).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m pipeline.run                 # first run backfills ~140 days; about 3 minutes
python -m pipeline.serve               # open http://127.0.0.1:8080
```

`pipeline.serve` serves the site and fetches new stories every 30 minutes while it runs (`--every 60` changes the interval, `--port` the port). Open pages notice new data and offer a refresh. To serve without updating, use `python -m http.server 8080 -d site`.

The first run downloads the 64 MB embedding model into `.cache/fastembed/` and backfills the archive so the previous quarter's newsletter can be built. Later runs take under a minute.

Useful flags:

```bash
python -m pipeline.run --no-fetch         # rebuild site data and PDFs from the archive only
python -m pipeline.run --reclassify       # re-categorise the whole archive after editing categories
python -m pipeline.run --no-embeddings    # vocabulary-only categorisation (no model download)
python -m pipeline.run --sources openai,anthropic   # fetch only some sources
python -m pytest -q                       # run the tests
```

## Deploy for free on GitHub Pages

1. Create a GitHub repository and push this project to the `main` branch. Include `data/` if you ran the pipeline locally; it seeds the archive.
2. In the repository, open **Settings → Pages** and set **Source** to **GitHub Actions**.
3. Open **Settings → Actions → General** and make sure workflows have **Read and write permissions** (needed to commit the archive).
4. Open the **Actions** tab, choose **Update news and deploy**, and click **Run workflow**.

From then on the workflow runs every 3 hours. Each run commits `data/` (the archive) and deploys `site/`. The PDF links printed in newsletters use your Pages address automatically.

**Free-tier budget.** A run takes 1–3 minutes, so about 8 runs a day stays well inside GitHub's free Actions minutes (unlimited for public repositories, 2,000 minutes a month for private ones). The archive grows by roughly 1 MB per month as compressed git history.

**Other free hosts.** `site/` is plain static files. On Netlify or Cloudflare Pages, use the build command `pip install -r requirements.txt && python -m pipeline.run --no-fetch` and the publish directory `site`, and keep the GitHub Actions workflow for scheduled updates.

## How it works

```
 sources (RSS, arXiv API, HF Daily Papers, sitemaps)
        │  fetchers.py      concurrent, ETag/Last-Modified caching, per-source error isolation
        ▼
 normalise                  clean HTML, extractive summary, canonical URL, date checks, AI-relevance filter
        ▼
 enrich.py                  replace missing or boilerplate summaries with the page's own lede
        ▼
 dedupe.py                  exact URL merge, then same-story clustering (IDF title overlap + embeddings)
        ▼
 classify.py                12 categories: embeddings (zero-shot) + weighted vocabulary + source hints
        ▼
 scoring.py                 importance score, trending topics
        ▼
 store.py                   data/articles/YYYY-MM.json (git is the database)
        ▼
 build_site.py ──► site/data/*.json ──► site/ (static HTML/CSS/JS, no build step)
 newsletter.py ──► site/newsletters/*.pdf  (pdf_render.py, ReportLab)
```

### Accuracy safeguards

- Items without a real publication date are dropped, never given a guessed one.
- Summaries are the publisher's own words, shortened at sentence boundaries. No model rewrites them, so nothing is invented.
- Boilerplate descriptions (site-wide taglines, "Tags: …") are detected and replaced with the article's opening paragraph.
- Mixed-topic feeds (NVIDIA, Microsoft Research, Meta Engineering, Simon Willison) pass through an AI-relevance filter.
- Cross-posts within one organisation (Google AI and DeepMind, for example) don't count as independent coverage.
- The Sources page shows each feed's health and last successful fetch, so a broken feed is visible.

## Configuration

Everything lives in [`pipeline/config.py`](pipeline/config.py).

**Add a source:**

```python
{"id": "mistral", "name": "Mistral AI", "type": "rss", "tier": 1,
 "url": "https://example.com/feed.xml", "home": "https://mistral.ai/news"},
```

Types are `rss`, `sitemap` (with `include` URL patterns, for sites without a feed), `arxiv`, and `hf_papers`. Add `"filter_ai": True` for feeds that cover more than AI, `"org": "…"` to group sources from one company, and `"hint": "research"` to nudge a source's default category.

**Edit categories:** each category has `keywords` (regex → weight; title matches count double) and `prototypes` (plain sentences for the embedding model). After editing, run with `--reclassify`, or tick **Re-categorise** when running the workflow by hand.

**Site name and URL:** set the environment variables `SITE_NAME` and `SITE_URL`. CI sets `SITE_URL` automatically.

**Newsletter layout:** the PDF design lives in [`pipeline/pdf_html.py`](pipeline/pdf_html.py) (HTML + CSS). After changing it, bump `RENDER_VERSION` so existing PDFs are regenerated. Chapter advice text is in `CHAPTER_COPY` in [`pipeline/newsletter.py`](pipeline/newsletter.py). Set `CHROME_PATH` if Chrome is installed somewhere unusual; GitHub's Ubuntu runners include it.

**Branding:** the AIT Global mark is `site/assets/ait-global-mark.png` (also copied to `pipeline/assets/` for PDFs).

## Project layout

```
pipeline/            Python pipeline (fetch → enrich → dedupe → classify → score → build)
  assets/fonts/      Bricolage Grotesque and Literata (SIL Open Font License), embedded in PDFs
site/                Static website (index.html, assets/); data/ and newsletters/ are generated
data/articles/       Archive, one JSON file per month (committed by CI)
data/state/          Feed cache, source health, run metadata
tests/               pytest suite
.github/workflows/   Scheduled update and deploy
```

## Licences

Code: yours to license. Fonts: SIL Open Font License 1.1 (see `pipeline/assets/fonts/`). Article titles and summaries belong to their publishers; every item links to the original.
