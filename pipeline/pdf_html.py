"""Gen AI Pulse PDF: an HTML/CSS layout printed to PDF by headless Chrome.

Every section is exactly one A4 page: cover, top stories, one page per chapter,
research, then trends and sources. Headlines link to the original publications,
and the contents list links to each chapter. If Chrome is not available,
`render_pdf_html` returns False and the ReportLab layout is used instead.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import html
import io
import logging
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

RENDER_VERSION = "html-4"   # bump when the layout changes so existing PDFs are re-rendered
FONT_FILES = {
    ("Bricolage Grotesque", 400, "normal"): "BricolageGrotesque-Regular.ttf",
    ("Bricolage Grotesque", 600, "normal"): "BricolageGrotesque-SemiBold.ttf",
    ("Bricolage Grotesque", 800, "normal"): "BricolageGrotesque-ExtraBold.ttf",
    ("Literata", 400, "normal"): "Literata-Regular.ttf",
    ("Literata", 400, "italic"): "Literata-Italic.ttf",
    ("Literata", 600, "normal"): "Literata-SemiBold.ttf",
}
CHROME_CANDIDATES = [
    os.environ.get("CHROME_PATH", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
]


def find_chrome() -> str | None:
    for c in CHROME_CANDIDATES:
        if not c:
            continue
        path = c if os.path.isabs(c) else shutil.which(c)
        if path and os.path.exists(path):
            return path
    return None


def e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _fonts_css() -> str:
    rules = []
    for (family, weight, style), file in FONT_FILES.items():
        uri = _data_uri(config.FONT_DIR / file, "font/ttf")
        rules.append(f"@font-face{{font-family:'{family}';font-weight:{weight};font-style:{style};src:url({uri}) format('truetype');}}")
    return "\n".join(rules)


def _d(iso: str, fmt: str = "%-d %b %Y") -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime(fmt)


def _src(sid: str) -> str:
    return config.SOURCE_BY_ID.get(sid, {"name": sid})["name"]


def _cat(cid: str) -> dict:
    return config.CATEGORY_BY_ID.get(cid, config.CATEGORY_BY_ID[config.FALLBACK_CATEGORY])


# --------------------------------------------------------------------------- building blocks
def _thumb(a: dict, cls: str = "") -> str:
    color = _cat(a["cats"][0])["color"]
    img = (f'<img src="{e(a["img"])}" alt="" referrerpolicy="no-referrer" '
           f'onerror="this.remove()">' if a.get("img") else "")
    return f'<div class="thumb {cls}" style="--c:{color}"><span>{e(_src(a["source"]))}</span>{img}</div>'


def _tags(a: dict) -> str:
    return "".join(f'<span class="tag t-{e(t.lower().replace(" ", "-"))}">{e(t)}</span>' for t in a.get("tags", []))


def _also(a: dict) -> str:
    cov = a.get("coverage") or []
    if not cov:
        return ""
    links = ", ".join(f'<a href="{e(c["url"])}">{e(_src(c["source"]))}</a>' for c in cov[:4])
    return f'<div class="also">Also reported by {links}</div>'


def _header(page: int) -> str:
    return (f'<header class="run"><div class="logo"><img src="{LOGO}" alt=""><b>AIT GLOBAL</b></div>'
            f'<div class="run-r">GEN AI PULSE <i></i> {page:02d}</div></header>')


def _footer(right: str) -> str:
    return (f'<footer class="run-f"><span><i></i>AIT GLOBAL <em>/</em> GEN AI PULSE</span>'
            f'<span>{e(right)}</span></footer>')


def _banner(kicker: str, title: str, sub: str, color: str, number: str) -> str:
    return (f'<div class="banner" style="--c:{color}"><div class="kicker">{e(kicker)}</div>'
            f'<h2>{e(title)}</h2><p>{e(sub)}</p><div class="num">{e(number)}</div>{NETWORK_SVG}</div>')


def _dev_card(a: dict) -> str:
    meta = f'{e(_src(a["source"]))}, {_d(a["date"], "%-d %b")}'
    if a.get("upvotes"):
        meta += f' <b class="up">{a["upvotes"]} upvotes</b>'
    return (f'<article class="dev glass" style="--c:{_cat(a["cats"][0])["color"]}">'
            f'<div class="dev-top">{_thumb(a, "sm")}<div><h3><a href="{e(a["url"])}">{e(a["title"])}</a></h3>'
            f'<div class="meta">{meta}</div></div></div>'
            f'<p>{e(a.get("summary", ""))}</p><div class="dev-foot">{_tags(a)}</div></article>')


# --------------------------------------------------------------------------- pages
def _cover(issue: dict) -> str:
    kind = "Monthly" if issue["type"] == "monthly" else "Quarterly"
    period = f'{_d(issue["start"], "%-d %b")} to {_d(issue["end"], "%-d %b %Y")}'
    status = " (in progress)" if issue["status"] == "in_progress" else ""
    toc = "".join(
        f'<li><a href="#ch-{c["number"]}"><b style="background:{c["color"]}">{c["number"]:02d}</b>{e(c["kicker"])}</a></li>'
        for c in issue["chapters"])
    pills = "".join(f"<span>{e(p)}</span>" for p in issue.get("pills", []))
    brief = "".join(f"<p>{e(x)}</p>" for x in issue["brief"])
    kpis = "".join(f'<div class="kpi k{i}"><b>{k["value"]:,}</b><span>{e(k["label"])}</span></div>'
                   for i, k in enumerate(issue["kpis"]))
    note = ('<p class="note">Built from source archives when the service started, so news-outlet coverage is '
            'thinner than in later editions.</p>' if issue.get("backfilled") else "")
    return f'''
<section class="page cover"><div class="body">
  <header class="mast">
    <div><div class="brand">Gen <span>AI</span> Pulse</div><div class="brand-sub">AIT GLOBAL <em>/</em> AI TECHNOLOGY BRIEFING</div></div>
    <div class="mast-r"><div class="badge">ISSUE #{issue.get("issue_no", 1)} <em>/</em> {e(issue["title"].upper())}</div>
      <div class="logo big"><img src="{LOGO}" alt=""><b>AIT GLOBAL</b></div></div>
  </header>
  <div class="hero">{NETWORK_SVG}
    <div class="eyebrow">{kind.upper()} REVIEW <em>/</em> {e(period.upper())}{e(status.upper())}</div>
    <div class="lead-label">Leading this {"month" if issue["type"] == "monthly" else "quarter"}</div>
    <h1>{e(issue["headline"])}</h1>
    <div class="pills">{pills}</div>
    <div class="ribbon">TECHNOLOGY ONLY <em>/</em> {issue["stats"]["sources"]} SOURCES <em>/</em> EVERY HEADLINE LINKS TO ITS SOURCE</div>
  </div>
  <div class="cover-grid">
    <div class="whatsnew"><h2>What&#8217;s new in AI: {e(issue["title"])}</h2><div class="bar"></div>{brief}{note}</div>
    <aside class="toc glass"><h3>In this edition</h3><ol>
      <li><a href="#top"><b style="background:#5B3DF5">&#9733;</b>Top stories</a></li>{toc}
      <li><a href="#trends"><b style="background:#0F1A3A">&#8599;</b>Trends and sources</a></li></ol></aside>
  </div>
  <div class="kpis">{kpis}</div></div>
  {_footer(f"{kind.upper()} EDITION / {issue['title'].upper()}")}
</section>'''


def _top_page(issue: dict, page: int) -> str:
    tops = issue["top_stories"]
    if not tops:
        return ""
    f = tops[0]
    rest = "".join(f'''<article class="ts glass" style="--c:{_cat(a["cats"][0])["color"]}">{_thumb(a, "ts-img")}
      <div><span class="chip">{e(_cat(a["cats"][0])["name"])}</span><h3><a href="{e(a["url"])}">{e(a["title"])}</a></h3>
      <div class="meta">{e(_src(a["source"]))}, {_d(a["date"], "%-d %b")}</div><p>{e(a.get("summary", ""))}</p>
      <div class="dev-foot">{_tags(a)}</div></div></article>''' for a in tops[1:5])
    return f'''
<section class="page" id="top">
  {_header(page)}<div class="body">
  {_banner("THE BIG PICTURE", "Top stories", "The most significant technology developments, ranked by source authority, independent coverage and real-world availability.", "#5B3DF5", "★")}
  <article class="feature glass" style="--c:{_cat(f["cats"][0])["color"]}">{_thumb(f, "feat-img")}
    <div class="feat-body"><span class="chip">{e(_cat(f["cats"][0])["name"])}</span>
      <h3><a href="{e(f["url"])}">{e(f["title"])}</a></h3>
      <div class="meta">{e(_src(f["source"]))}, {_d(f["date"])}</div><p>{e(f.get("summary", ""))}</p>
      <div class="dev-foot">{_tags(f)}</div>{_also(f)}</div></article>
  <div class="ts-grid">{rest}</div></div>
  {_footer("TOP STORIES")}
</section>'''


def _chapter_page(ch: dict, page: int, unit: str) -> str:
    st, labels = ch["stats"], ch.get("stat_labels", {})
    stats = [(st["stories"], labels.get("stories", "Stories")), (st["sources"], "Sources"),
             (st["available"], labels.get("available", "Available now")), (st["open_source"], "Open source")]
    stat_html = "".join(f'<div class="stat"><b>{v}</b><span>{e(l)}</span></div>' for v, l in stats)
    more = "".join(f'<li><a href="{e(a["url"])}">{e(a["title"])}</a> <span>{e(_src(a["source"]))}</span></li>'
                   for a in ch["more"]) or "<li>No further stories this period.</li>"
    ready = "".join(f'<li><span class="tag t-{e(r["tag"].lower().replace(" ", "-"))}">{e(r["tag"])}</span>'
                    f'<a href="{e(r["url"])}">{e(r["title"])}</a></li>' for r in ch["ready"])
    ready_box = (f'<div class="box glass"><div class="box-h ready-h">READY TO USE</div>'
                 f'<p class="box-sub">Released, open, or reachable through an API.</p><ul class="ready">{ready}</ul></div>'
                 if ready else
                 f'<div class="box glass"><div class="box-h ready-h">WATCH LIST</div>'
                 f'<p class="box-sub">Nothing in this chapter is generally available yet. These are worth tracking.</p>'
                 f'<ul class="more">{more}</ul></div>')
    actions = "".join(f"<li>{e(x)}</li>" for x in ch["actions"])
    return f'''
<section class="page" id="ch-{ch["number"]}">
  {_header(page)}<div class="body">
  {_banner(f"CHAPTER {ch['number']:02d} / {ch['name'].upper()}", ch["kicker"], ch["intro"], ch["color"], f"{ch['number']:02d}")}
  <div class="dev-grid">{"".join(_dev_card(a) for a in ch["items"])}</div>
  <div class="boxes">
    <div class="box glass"><div class="box-h" style="--c:{ch["color"]}">QUICK STATS <em>/</em> THIS {unit.upper()}</div>
      <div class="stats" style="--c:{ch["color"]}">{stat_html}</div><div class="box-t">More this {unit}</div><ul class="more">{more}</ul></div>
    {ready_box}
  </div>
  <div class="callout"><div class="ico">&#10148;</div><div><h4>WHAT THIS MEANS FOR AIT GLOBAL</h4>
    <p>{e(ch["advice"])}</p>{f"<ul>{actions}</ul>" if actions else ""}</div></div></div>
  {_footer(f"CHAPTER {ch['number']:02d} / {ch['kicker'].upper()}")}
</section>'''


def _trends_page(issue: dict, page: int) -> str:
    rising = "".join(
        f'<tr><td>{e(r["topic"])}</td><td class="n">{r["mentions"]}</td><td class="n">{r["previous"]}</td>'
        f'<td class="n {"pos" if (r["change_pct"] or 1) > 0 else "neg"}">'
        f'{"new" if r["change_pct"] is None else ("+" if r["change_pct"] > 0 else "") + str(r["change_pct"]) + "%"}</td></tr>'
        for r in issue["rising"][:8])
    mx = max([c["count"] for c in issue["category_share"]] or [1])
    total = sum(c["count"] for c in issue["category_share"]) or 1
    share = "".join(
        f'<div class="sh"><span>{e(c["name"])}</span><div><i style="width:{100 * c["count"] / mx:.1f}%;background:{c["color"]}"></i></div>'
        f'<b>{100 * c["count"] / total:.0f}%</b></div>' for c in issue["category_share"])
    months = ""
    if issue.get("months"):
        cols = "".join(
            f'<div class="mcol"><div class="mhead"><b>{e(m["label"])}</b><span>{m["stories"]} stories</span></div><ol>' +
            "".join(f'<li><a href="{e(a["url"])}">{e(a["title"])}</a><span>{e(_src(a["source"]))}</span></li>'
                    for a in m.get("highlights", [])) + "</ol></div>" for m in issue["months"])
        months = f'<div class="box glass"><div class="box-h">MONTH BY MONTH</div><div class="mgrid">{cols}</div></div>' 
    srcs = "".join(f'<li><a href="{e(config.SOURCE_BY_ID[s["id"]]["home"])}">{e(s["name"])}</a>'
                   f'<span>{e(config.TIERS[s["tier"]]["label"])}</span><b>{s["count"]}</b></li>' for s in issue["sources"][:26])
    return f'''
<section class="page" id="trends">
  {_header(page)}<div class="body">
  {_banner("SIGNALS", "Trends and sources", "What the coverage was about, what changed against the previous period, and where this edition comes from.", "#0F1A3A", "↗")}
  <div class="trend-grid">
    <div class="box glass"><div class="box-h">MOST DISCUSSED TOPICS</div>
      <table class="tt"><thead><tr><th>Topic</th><th class="n">Mentions</th><th class="n">Before</th><th class="n">Change</th></tr></thead><tbody>{rising}</tbody></table></div>
    <div class="box glass"><div class="box-h">COVERAGE BY CATEGORY</div><div class="share">{share}</div></div>
  </div>
  {months}
  <div class="box glass"><div class="box-h">SOURCES IN THIS EDITION</div><ul class="srcs">{srcs}</ul>
    <p class="method">Collected automatically from official AI company blogs, research groups, Hugging Face Daily Papers, arXiv
    and established technology publications. Stories about people, politics, lawsuits or money are left out. Duplicates are merged and
    each story is categorised by a local language model with a domain vocabulary. Summaries are the publishers&#8217; own words;
    recommendations are generated from what shipped. Check the original source before relying on a detail.</p></div></div>
  {_footer("TRENDS AND SOURCES")}
</section>'''


def build_html(issue: dict) -> str:
    unit = "month" if issue["type"] == "monthly" else "quarter"
    pages, n = [_cover(issue)], 2
    top = _top_page(issue, n)
    if top:
        pages.append(top)
        n += 1
    for ch in issue["chapters"]:
        pages.append(_chapter_page(ch, n, unit))
        n += 1
    pages.append(_trends_page(issue, n))
    title = f"AIT Global | Gen AI Pulse | Issue #{issue.get('issue_no', 1)} | {issue['title']}"
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(title)}</title>
<style>{_fonts_css()}{CSS}</style></head><body>{"".join(pages)}<script>{FIT_JS}</script></body></html>'''


IMG_CACHE = config.ROOT / ".cache" / "pdf-images"
IMG_MAX = (520, 340)


def _fetch_image(url: str) -> str | None:
    """Download, downscale and cache an image; return a data URI (or None if unusable)."""
    key = hashlib.sha1(url.encode()).hexdigest()
    jpg, fail = IMG_CACHE / f"{key}.jpg", IMG_CACHE / f"{key}.fail"
    if jpg.exists():
        return _data_uri(jpg, "image/jpeg")
    if fail.exists():
        return None
    IMG_CACHE.mkdir(parents=True, exist_ok=True)
    try:
        import requests
        from PIL import Image
        import time
        deadline = time.monotonic() + 15          # hard cap per image, however slowly the server drips
        buf = io.BytesIO()
        with requests.get(url, timeout=(5, 8), stream=True, headers={"User-Agent": config.USER_AGENT}) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_content(64 * 1024):
                buf.write(chunk)
                if buf.tell() > 8_000_000 or time.monotonic() > deadline:
                    raise TimeoutError("image too large or too slow")
        im = Image.open(io.BytesIO(buf.getvalue()))
        im.draft("RGB", (IMG_MAX[0] * 2, IMG_MAX[1] * 2))
        im = im.convert("RGB")
        if im.width < 120 or im.height < 80:
            raise ValueError("too small")
        im.thumbnail(IMG_MAX)
        im.save(jpg, "JPEG", quality=70, optimize=True, progressive=True)
        return _data_uri(jpg, "image/jpeg")
    except Exception as exc:
        log.debug("pdf image %s: %s", url, exc)
        fail.write_text(str(exc)[:200])
        return None


def _inline_images(issue: dict) -> dict:
    issue = copy.deepcopy(issue)
    arts = list(issue["top_stories"])
    for ch in issue["chapters"]:
        arts += ch["items"]
    urls = sorted({a["img"] for a in arts if a.get("img")})
    uris = {}
    pool = ThreadPoolExecutor(max_workers=8)
    futures = {pool.submit(_fetch_image, u): u for u in urls}
    from concurrent.futures import wait
    done, _ = wait(futures, timeout=45)           # overall cap: never stall a pipeline run on images
    for f in done:
        uris[futures[f]] = f.result()
    pool.shutdown(wait=False, cancel_futures=True)
    for a in arts:
        if a.get("img"):
            if uris.get(a["img"]):
                a["img"] = uris[a["img"]]
            else:
                a.pop("img")
    return issue


def render_pdf_html(issue: dict, path: Path) -> bool:
    chrome = find_chrome()
    if not chrome:
        log.warning("Chrome not found; using the ReportLab layout (set CHROME_PATH to enable the full design)")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "issue.html"
        src.write_text(build_html(_inline_images(issue)), encoding="utf-8")
        out = Path(tmp) / "out.pdf"
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--hide-scrollbars", "--no-pdf-header-footer", "--generate-pdf-document-outline",
               "--run-all-compositor-stages-before-draw", "--virtual-time-budget=20000",
               f"--user-data-dir={tmp}/profile", f"--print-to-pdf={out}", src.as_uri()]
        if os.environ.get("CI") or (hasattr(os, "geteuid") and os.geteuid() == 0):
            cmd.insert(1, "--no-sandbox")
        try:
            subprocess.run(cmd, check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("Chrome PDF rendering failed (%s); using the ReportLab layout", exc)
            return False
        if not out.exists() or out.stat().st_size < 1000:
            return False
        out.replace(path)
    return True


# Shrinks content that would spill past a page's content area, least important first.
# Nothing is ever allowed to run into the footer or off the page.
FIT_JS = r"""
(function () {
  const fits = (b) => b.scrollHeight <= b.clientHeight + 1;
  const drop = (sel, min) => (b) => { const n = b.querySelectorAll(sel); if (n.length > min) { n[n.length - 1].remove(); return true; } return false; };
  // Remove from whichever column currently has the most items, so every month keeps its share.
  const dropEven = (col, item, min) => (b) => { let best = null, n = min;
      b.querySelectorAll(col).forEach((c) => { const k = c.querySelectorAll(item).length; if (k > n) { n = k; best = c; } });
      if (!best) return false; const items = best.querySelectorAll(item); items[items.length - 1].remove(); return true; };
  const clamp = (sel, lines) => (b) => { let c = false; b.querySelectorAll(sel).forEach((el) => {
      if (el.dataset.clamp !== String(lines)) { el.style.webkitLineClamp = lines; el.dataset.clamp = lines; c = true; } }); return c; };
  const steps = [drop(".more li", 2), drop(".ready li", 3), drop(".callout li", 1), clamp(".dev p", 3), drop(".srcs li", 16),
                 drop(".more li", 1), drop(".ready li", 2), clamp(".dev p", 2), drop(".callout li", 0), clamp(".ts p", 1),
                 drop(".whatsnew p", 3), drop(".srcs li", 12), drop(".tt tbody tr", 6), drop(".srcs li", 8),
                 dropEven(".mcol", "li", 2), dropEven(".mcol", "li", 1), clamp(".dev p", 1), drop(".ready li", 1), drop(".more li", 0)];
  function fit() {
    document.querySelectorAll(".page .body").forEach((b) => {
      for (const step of steps) { let guard = 0; while (!fits(b) && guard++ < 40 && step(b)) {} if (fits(b)) break; }
      b.dataset.fit = fits(b) ? "ok" : "overflow";
    });
  }
  fit();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);
  window.addEventListener("load", fit);
})();
"""

LOGO = _data_uri(Path(__file__).resolve().parent / "assets" / "ait-global-mark.png", "image/png")

NETWORK_SVG = '''<svg class="net" viewBox="0 0 600 200" preserveAspectRatio="none" aria-hidden="true"><g stroke="rgba(255,255,255,.35)" stroke-width="1" fill="none"><path d="M20 160 L110 40 L230 120 L330 30 L450 110 L580 50"/><path d="M110 40 L170 180 L330 30"/><path d="M230 120 L400 190 L450 110"/><path d="M450 110 L520 180"/></g><g fill="rgba(255,255,255,.75)"><circle cx="20" cy="160" r="3"/><circle cx="110" cy="40" r="4"/><circle cx="230" cy="120" r="3"/><circle cx="330" cy="30" r="5"/><circle cx="450" cy="110" r="4"/><circle cx="580" cy="50" r="3"/><circle cx="170" cy="180" r="3"/><circle cx="400" cy="190" r="3"/><circle cx="520" cy="180" r="3"/></g></svg>'''

CSS = r'''
@page { size: A4; margin: 0; }
* { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
html, body { margin: 0; padding: 0; }
body { font-family: 'Bricolage Grotesque', Arial, sans-serif; color: #0F1A3A; font-size: 9.6pt; line-height: 1.45; }
a { color: inherit; text-decoration: none; }
em { font-style: normal; opacity: .55; padding: 0 2px; }
.page { position: relative; width: 210mm; height: 297mm; overflow: hidden; page-break-after: always; break-after: page;
  padding: 13mm 14mm 8mm; display: flex; flex-direction: column; gap: 4.2mm;
  background: radial-gradient(60% 40% at 100% 0%, #E6EEFF 0%, transparent 70%),
              radial-gradient(50% 35% at 0% 100%, #F1E9FF 0%, transparent 70%),
              radial-gradient(40% 30% at 100% 100%, #FFF1E2 0%, transparent 70%), #FBFCFF; }
.glass { background: rgba(255,255,255,.78); border: 1px solid rgba(214,222,240,.9); border-radius: 14px;
  box-shadow: 0 1px 2px rgba(15,26,58,.04), 0 10px 26px -14px rgba(37,48,110,.28); }

/* running header / footer */
.run { display: flex; justify-content: space-between; align-items: center; }
.logo { display: flex; align-items: center; gap: 7px; }
.logo img { height: 22px; }
.logo b { font-weight: 800; font-size: 10pt; letter-spacing: .08em; }
.logo.big img { height: 30px; } .logo.big b { font-size: 12.5pt; }
.run-r { font-size: 7.5pt; letter-spacing: .14em; color: #7A84A3; font-weight: 600; display: flex; align-items: center; gap: 6px; }
.run-r i { width: 14px; height: 2px; background: #5B3DF5; display: inline-block; }
.body { flex: 1; min-height: 0; overflow: hidden; display: flex; flex-direction: column; gap: 4.2mm; }
.run-f { flex: none; display: flex; justify-content: space-between;
  border-top: 1px solid #E3E7F2; padding-top: 3mm; font-size: 7pt; letter-spacing: .14em; color: #8A93AD; font-weight: 600; }
.run-f i { display: inline-block; width: 16px; height: 2px; background: linear-gradient(90deg,#1E88F0,#5B3DF5); margin-right: 6px; vertical-align: 2px; }

/* cover */
.mast { display: flex; justify-content: space-between; align-items: flex-start; }
.brand { font-family: 'Literata', Georgia, serif; font-weight: 600; font-size: 40pt; line-height: 1; letter-spacing: -.02em; }
.brand span { color: #5B3DF5; }
.brand-sub { margin-top: 6px; font-size: 7.5pt; letter-spacing: .3em; color: #7A84A3; font-weight: 600; }
.mast-r { display: flex; flex-direction: column; align-items: flex-end; gap: 9px; }
.badge { background: linear-gradient(90deg,#5B3DF5,#7B5CFF); color: #fff; font-weight: 700; font-size: 7.5pt; letter-spacing: .14em;
  padding: 5px 11px; border-radius: 6px; }
.hero { position: relative; overflow: hidden; border-radius: 16px; padding: 7mm 8mm 0; color: #fff; min-height: 72mm;
  background: radial-gradient(70% 90% at 90% 0%, rgba(30,136,240,.75), transparent 60%),
              radial-gradient(60% 80% at 0% 100%, rgba(200,80,220,.45), transparent 60%),
              linear-gradient(120deg,#24165E,#3B2AA8 55%,#1A4FC4); }
.net { position: absolute; inset: 0; width: 100%; height: 100%; opacity: .55; }
.hero > * { position: relative; }
.hero > .net, .banner > .net { position: absolute; }
.eyebrow { font-size: 7.5pt; letter-spacing: .24em; font-weight: 700; color: #C9D4FF; }
.lead-label { margin-top: 5mm; font-size: 8pt; color: #DDE3FF; font-weight: 600; }
.hero h1 { font-family: 'Literata', Georgia, serif; font-weight: 600; font-size: 23pt; line-height: 1.12; margin: 1.5mm 0 4mm; max-width: 150mm; }
.pills { display: flex; flex-wrap: wrap; gap: 6px; }
.pills span { border: 1px solid rgba(255,255,255,.5); background: rgba(255,255,255,.14); border-radius: 999px; padding: 4px 12px; font-size: 8pt; font-weight: 600; }
.ribbon { margin: 5mm -8mm 0; padding: 3mm 8mm; background: rgba(8,10,40,.55); font-size: 7pt; letter-spacing: .2em; font-weight: 700; color: #E6EAFF; }
.cover-grid { display: grid; grid-template-columns: 1fr 66mm; gap: 6mm; flex: 1; min-height: 0; }
.whatsnew { overflow: hidden; }
.whatsnew h2 { font-family: 'Literata', Georgia, serif; font-weight: 600; font-size: 15pt; margin: 1mm 0 2mm; }
.whatsnew .bar { width: 26mm; height: 3px; border-radius: 2px; background: linear-gradient(90deg,#1E88F0,#5B3DF5); margin-bottom: 3mm; }
.whatsnew p { margin: 0 0 2.4mm; font-size: 9.3pt; color: #2D3757; }
.note { font-style: italic; color: #7A84A3 !important; font-size: 8pt !important; }
.toc { padding: 5mm; }
.toc h3 { font-family: 'Literata', Georgia, serif; font-weight: 600; font-size: 12pt; margin: 0 0 3mm; }
.toc ol { list-style: none; margin: 0; padding: 0; display: grid; gap: 2.2mm; }
.toc a { display: flex; align-items: center; gap: 8px; font-size: 8.6pt; color: #2D3757; }
.toc b { flex: none; width: 20px; height: 20px; border-radius: 5px; color: #fff; font-size: 7.5pt; display: grid; place-items: center; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4mm; }
.kpi { border-radius: 14px; padding: 4.5mm 5mm; color: #fff; box-shadow: 0 12px 24px -14px rgba(37,48,110,.6); }
.kpi b { display: block; font-family: 'Literata', Georgia, serif; font-size: 22pt; line-height: 1; font-weight: 600; }
.kpi span { font-size: 7.5pt; letter-spacing: .08em; font-weight: 700; text-transform: uppercase; opacity: .92; }
.k0 { background: linear-gradient(135deg,#4F46E5,#7C3AED); } .k1 { background: linear-gradient(135deg,#1E88F0,#06B6D4); }
.k2 { background: linear-gradient(135deg,#0F9F6E,#22C55E); } .k3 { background: linear-gradient(135deg,#F59E0B,#F97316); }

/* banners */
.banner { position: relative; overflow: hidden; border-radius: 16px; padding: 5.5mm 7mm; color: #fff; min-height: 34mm;
  background: radial-gradient(60% 120% at 100% 0%, rgba(255,255,255,.25), transparent 60%),
              linear-gradient(120deg, color-mix(in srgb, var(--c) 82%, #0B1030), var(--c) 60%, color-mix(in srgb, var(--c) 70%, #1E88F0)); }
.banner > * { position: relative; }
.banner .net { opacity: .35; }
.kicker { font-size: 7.5pt; letter-spacing: .22em; font-weight: 700; opacity: .85; }
.banner h2 { font-family: 'Literata', Georgia, serif; font-weight: 600; font-size: 21pt; line-height: 1.1; margin: 1.5mm 0 1.5mm; max-width: 140mm; }
.banner p { margin: 0; font-size: 8.8pt; opacity: .92; max-width: 145mm; }
.num { position: absolute !important; right: 6mm; bottom: -6mm; font-family: 'Literata', Georgia, serif; font-size: 64pt; font-weight: 600; opacity: .18; line-height: 1; }

/* content */
.lede { margin: 0; padding: 0 0 0 4mm; border-left: 3px solid var(--c); font-size: 9.3pt; color: #2D3757;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.dev-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }
.dev { padding: 3.6mm 4mm; display: flex; flex-direction: column; gap: 2mm; border-top: 3px solid var(--c); }
.dev-top { display: grid; grid-template-columns: 24mm 1fr; gap: 3mm; align-items: start; }
.dev h3 { margin: 0; font-size: 9.6pt; line-height: 1.25; font-weight: 700;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.meta { font-size: 7.4pt; color: #7A84A3; margin-top: 1mm; }
.meta .up { color: #5B3DF5; }
.dev p, .ts p, .feat-body p { margin: 0; font-family: 'Literata', Georgia, serif; font-size: 8.3pt; line-height: 1.45; color: #39425F;
  display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
.dev-foot { display: flex; flex-wrap: wrap; gap: 4px; margin-top: auto; }
.thumb { position: relative; overflow: hidden; border-radius: 9px; background: linear-gradient(135deg, var(--c), color-mix(in srgb, var(--c) 35%, #0B1030)); }
.thumb span { position: absolute; left: 6px; bottom: 5px; right: 6px; color: #fff; font-size: 6.5pt; font-weight: 700; line-height: 1.1; }
.thumb img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.thumb.sm { width: 24mm; height: 17mm; }
.tag { font-size: 6.6pt; font-weight: 700; padding: 1.5px 6px; border-radius: 5px; border: 1px solid #D9DFEE; color: #4A5578; background: #F5F7FD; }
.t-available-now { color: #1F4FD8; border-color: #BFD0FA; background: #EEF3FF; }
.t-open-source { color: #0F7A5A; border-color: #BFE7D6; background: #EAF8F1; }
.t-api, .t-for-developers { color: #6D3FD6; border-color: #DCCDFA; background: #F4EEFF; }
.t-enterprise { color: #B4470F; border-color: #F6D3BD; background: #FFF3EA; }
.t-preview { color: #A16207; border-color: #F3DFA8; background: #FFF8E6; }
.t-research { color: #334155; }
.boxes { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }
.box { padding: 4mm 4.5mm; }
.box-h { display: inline-block; font-size: 7pt; letter-spacing: .16em; font-weight: 800; color: #fff; padding: 3px 9px; border-radius: 5px;
  background: linear-gradient(90deg, var(--c, #5B3DF5), color-mix(in srgb, var(--c, #5B3DF5) 60%, #1E88F0)); margin-bottom: 3mm; }
.ready-h { --c: #DC2655; }
.box-sub { margin: -1mm 0 2mm; font-size: 7.8pt; color: #7A84A3; }
.box-t { font-weight: 700; font-size: 9pt; margin: 3mm 0 1.5mm; }
.stats { display: grid; grid-template-columns: repeat(4,1fr); gap: 2mm; text-align: center; }
.stat b { display: grid; place-items: center; margin: 0 auto 1mm; width: 12mm; height: 12mm; border-radius: 50%;
  background: linear-gradient(135deg, var(--c), color-mix(in srgb, var(--c) 55%, #1E88F0)); color: #fff; font-size: 10.5pt; }
.stat span { font-size: 6.8pt; color: #5B6685; font-weight: 600; }
.more, .ready { list-style: none; margin: 0; padding: 0; display: grid; gap: 1.6mm; }
.more li { font-size: 8pt; line-height: 1.3; padding-left: 9px; position: relative; color: #2D3757; }
.more li::before { content: ""; position: absolute; left: 0; top: 5px; width: 4px; height: 4px; border-radius: 50%; background: #5B3DF5; }
.more li span { color: #8A93AD; font-size: 7pt; }
.ready li { display: flex; gap: 6px; align-items: baseline; font-size: 8pt; line-height: 1.3; }
.ready .tag { flex: none; }
.callout { margin-top: auto; display: grid; grid-template-columns: 11mm 1fr; gap: 4mm; align-items: start; border-radius: 14px; padding: 4mm 5mm;
  background: linear-gradient(120deg, #F1EDFF, #EAF3FF); border: 1px solid #DCD3FF; }
.callout .ico { width: 11mm; height: 11mm; border-radius: 10px; display: grid; place-items: center; color: #fff; font-size: 13pt;
  background: linear-gradient(135deg,#5B3DF5,#1E88F0); }
.callout h4 { margin: 0 0 1mm; font-size: 8pt; letter-spacing: .16em; color: #5B3DF5; font-weight: 800; }
.callout p { margin: 0; font-size: 8.8pt; color: #2D3757; }
.callout ul { margin: 1.5mm 0 0; padding-left: 4mm; font-size: 8.2pt; color: #2D3757; }
.callout li { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.more li, .ready li a { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.mgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 5mm; }
.mhead { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 2px solid #5B3DF5; padding-bottom: 1.5mm; margin-bottom: 2mm; }
.mhead b { font-family: 'Literata', Georgia, serif; font-size: 11pt; } .mhead span { font-size: 7.4pt; color: #7A84A3; }
.mcol ol { margin: 0; padding-left: 4mm; display: grid; gap: 2mm; font-size: 8pt; line-height: 1.3; }
.mcol li span { display: block; font-size: 7pt; color: #8A93AD; }

/* top stories */
.feature { display: grid; grid-template-columns: 78mm 1fr; overflow: hidden; border-top: 3px solid var(--c); }
.feat-img { height: 100%; min-height: 58mm; border-radius: 0; }
.feat-body { padding: 4.5mm 5mm; display: flex; flex-direction: column; gap: 1.8mm; }
.feat-body h3 { font-family: 'Literata', Georgia, serif; font-weight: 600; font-size: 14pt; line-height: 1.2; margin: 0; }
.feat-body p { -webkit-line-clamp: 5; font-size: 8.8pt; }
.chip { align-self: flex-start; font-size: 7pt; font-weight: 700; padding: 2px 8px; border-radius: 999px; color: var(--c);
  background: color-mix(in srgb, var(--c) 12%, white); }
.also { font-size: 7.4pt; color: #7A84A3; } .also a { color: #1F4FD8; }
.ts-grid { display: grid; grid-template-columns: 1fr 1fr; grid-auto-rows: 1fr; gap: 4mm; flex: 1; min-height: 0; }
.ts { overflow: hidden; display: grid; grid-template-rows: 27mm 1fr; border-top: 3px solid var(--c); }
.ts-img { border-radius: 0; }
.ts > div:last-child { padding: 3mm 4mm; display: flex; flex-direction: column; gap: 1.4mm; }
.ts h3 { margin: 0; font-size: 9.8pt; line-height: 1.25; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.ts p { -webkit-line-clamp: 2; }
.ts .dev-foot { margin-top: 0; }

/* trends */
.trend-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }
.tt { width: 100%; border-collapse: collapse; font-size: 8.2pt; }
.tt th { text-align: left; font-size: 7pt; color: #7A84A3; letter-spacing: .06em; padding: 0 0 1.5mm; border-bottom: 1px solid #E3E7F2; }
.tt td { padding: 1.4mm 0; border-bottom: 1px solid #EEF1F8; }
.tt .n { text-align: right; } .pos { color: #0F9F6E; font-weight: 700; } .neg { color: #B4470F; }
.share { display: grid; gap: 1.6mm; }
.sh { display: grid; grid-template-columns: 34mm 1fr 9mm; gap: 2mm; align-items: center; font-size: 7.6pt; }
.sh span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sh div { height: 6px; border-radius: 3px; background: #EEF1F8; } .sh i { display: block; height: 6px; border-radius: 3px; }
.sh b { text-align: right; }
.months { display: flex; gap: 8mm; align-items: flex-end; height: 30mm; }
.months div { display: flex; flex-direction: column; align-items: center; gap: 1mm; font-size: 7.6pt; }
.months i { width: 14mm; border-radius: 6px 6px 2px 2px; background: linear-gradient(180deg,#5B3DF5,#1E88F0); }
.srcs { list-style: none; padding: 0; margin: 0; columns: 2; column-gap: 8mm; font-size: 7.8pt; }
.srcs li { display: grid; grid-template-columns: 1fr auto 8mm; gap: 2mm; padding: 1mm 0; border-bottom: 1px solid #EEF1F8; break-inside: avoid; }
.srcs span { color: #8A93AD; font-size: 6.8pt; } .srcs b { text-align: right; }
.method { margin: 3mm 0 0; font-size: 7.6pt; color: #5B6685; line-height: 1.5; }
'''
