"""Render a newsletter issue to a professionally typeset PDF with ReportLab.

Every headline links to its original source; the contents list and PDF outline
(sidebar bookmarks) link to each section.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Flowable, Frame, KeepTogether, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

from . import config

RENDER_VERSION = "3"  # bump when the layout changes so existing PDFs are re-rendered

# --------------------------------------------------------------------------- palette & fonts
INK = colors.HexColor("#141B34")
COBALT = colors.HexColor("#2B47D9")
SLATE = colors.HexColor("#5A6480")
RULE = colors.HexColor("#D8DDE8")
WASH = colors.HexColor("#EEF1F8")
WHITE = colors.white

_FONTS = {
    "Sans": "BricolageGrotesque-Regular.ttf", "Sans-Semi": "BricolageGrotesque-SemiBold.ttf",
    "Sans-Heavy": "BricolageGrotesque-ExtraBold.ttf", "Serif": "Literata-Regular.ttf",
    "Serif-Italic": "Literata-Italic.ttf", "Serif-Semi": "Literata-SemiBold.ttf",
}
_GLYPHS: set[int] = set()


def _register_fonts() -> None:
    if _GLYPHS:
        return
    for name, file in _FONTS.items():
        font = TTFont(name, str(config.FONT_DIR / file))
        pdfmetrics.registerFont(font)
        cmap = set(font.face.charToGlyph)
        _GLYPHS.update(cmap) if not _GLYPHS else _GLYPHS.intersection_update(cmap)
    pdfmetrics.registerFontFamily("Serif", normal="Serif", bold="Serif-Semi", italic="Serif-Italic",
                                  boldItalic="Serif-Semi")
    pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Semi", italic="Sans", boldItalic="Sans-Semi")


def txt(s: str | None) -> str:
    """Escape for Paragraph markup and replace glyphs the embedded fonts lack."""
    out = []
    for ch in s or "":
        if ord(ch) in _GLYPHS or ch in "\n ":
            out.append(ch)
            continue
        base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
        out.append(base if base else "")
    return escape("".join(out), {'"': "&quot;"})


def url(u: str) -> str:
    return escape(u, {'"': "%22"})


# --------------------------------------------------------------------------- styles
def _styles() -> dict[str, ParagraphStyle]:
    S = {}
    S["body"] = ParagraphStyle("body", fontName="Serif", fontSize=9.6, leading=14.2, textColor=INK)
    S["brief"] = ParagraphStyle("brief", parent=S["body"], fontSize=11, leading=16.5, spaceAfter=6)
    S["meta"] = ParagraphStyle("meta", fontName="Sans", fontSize=7.8, leading=10, textColor=SLATE, spaceAfter=1.5)
    S["headline"] = ParagraphStyle("headline", fontName="Sans-Semi", fontSize=12.2, leading=15, textColor=INK,
                                   spaceAfter=3)
    S["lead"] = ParagraphStyle("lead", parent=S["headline"], fontSize=15, leading=18.5)
    S["also"] = ParagraphStyle("also", fontName="Sans", fontSize=7.8, leading=10.5, textColor=SLATE, spaceBefore=3)
    S["h1"] = ParagraphStyle("h1", fontName="Sans-Heavy", fontSize=19, leading=23, textColor=INK)
    S["h2"] = ParagraphStyle("h2", fontName="Sans-Semi", fontSize=10.5, leading=14, textColor=INK, spaceBefore=6,
                             spaceAfter=4)
    S["toc"] = ParagraphStyle("toc", fontName="Sans", fontSize=9.5, leading=15, textColor=INK)
    S["small"] = ParagraphStyle("small", fontName="Sans", fontSize=8, leading=11, textColor=SLATE)
    S["cell"] = ParagraphStyle("cell", fontName="Sans", fontSize=8.6, leading=11, textColor=INK, alignment=TA_LEFT)
    S["rank"] = ParagraphStyle("rank", fontName="Sans-Heavy", fontSize=15, leading=18, textColor=COBALT)
    S["note"] = ParagraphStyle("note", parent=S["body"], fontName="Serif-Italic", fontSize=8.8, leading=12.5,
                               textColor=SLATE)
    return S


# --------------------------------------------------------------------------- flowables
class SectionHeading(Flowable):
    """Section title with a category-coloured bar, an anchor, and a PDF outline entry."""

    def __init__(self, title: str, key: str, color=COBALT, subtitle: str | None = None):
        super().__init__()
        self.title, self.key, self.color, self.subtitle = title, key, color, subtitle
        self._para = Paragraph(txt(title), _styles()["h1"])
        self._sub = Paragraph(txt(subtitle), _styles()["small"]) if subtitle else None

    def wrap(self, aw, ah):
        self.aw = aw
        _, h1 = self._para.wrap(aw - 14, ah)
        h2 = self._sub.wrap(aw - 14, ah)[1] + 2 if self._sub else 0
        self.h1, self.h2 = h1, h2
        self.height = h1 + h2 + 14
        return aw, self.height

    def draw(self):
        c = self.canv
        c.bookmarkPage(self.key)
        c.addOutlineEntry(self.title, self.key, level=0)
        c.setFillColor(self.color)
        c.rect(0, 8, 4, self.height - 8, stroke=0, fill=1)
        self._para.drawOn(c, 14, 8 + self.h2)
        if self._sub:
            self._sub.drawOn(c, 14, 8)


class Rule(Flowable):
    def __init__(self, color=RULE, width=0.6, space=6):
        super().__init__()
        self.color, self.lw, self.space = color, width, space

    def wrap(self, aw, ah):
        self.aw = aw
        return aw, self.space * 2

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.lw)
        self.canv.line(0, self.space, self.aw, self.space)


# --------------------------------------------------------------------------- page decoration
class IssueDoc(BaseDocTemplate):
    def __init__(self, path: Path, issue: dict):
        self.issue = issue
        super().__init__(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=22 * mm,
                         bottomMargin=20 * mm, title=f"{config.SITE['name']}: {issue['title']}",
                         author=config.SITE["name"], subject="AI industry newsletter",
                         creator=config.SITE["name"], keywords="artificial intelligence, AI news, newsletter")
        w, h = A4
        cover_band = 98 * mm
        cover = Frame(self.leftMargin, self.bottomMargin, w - 40 * mm, h - cover_band - self.bottomMargin - 10 * mm,
                      id="cover", showBoundary=0)
        body = Frame(self.leftMargin, self.bottomMargin, w - 40 * mm, h - self.topMargin - self.bottomMargin,
                     id="body", showBoundary=0)
        self.addPageTemplates([PageTemplate("cover", [cover], onPage=self._cover, autoNextPageTemplate="body"),
                               PageTemplate("body", [body], onPage=self._page)])

    def _cover(self, c, doc):
        w, h = A4
        issue = self.issue
        band = 98 * mm
        c.saveState()
        c.setFillColor(INK)
        c.rect(0, h - band, w, band, stroke=0, fill=1)
        c.setFillColor(COBALT)
        c.rect(0, h - band, w, 2.2 * mm, stroke=0, fill=1)
        x = 20 * mm
        c.setFillColor(colors.white)
        c.roundRect(x - 2 * mm, h - 22.5 * mm, 40 * mm, 11 * mm, 2 * mm, stroke=0, fill=1)
        c.drawImage(str(config.ROOT / "pipeline" / "assets" / "ait-global-mark.png"), x, h - 21.5 * mm,
                    width=8 * mm, height=8.9 * mm, mask="auto")
        c.setFillColor(INK)
        c.setFont("Sans-Heavy", 10)
        c.drawString(x + 10 * mm, h - 18.5 * mm, "AIT GLOBAL")
        c.setFont("Sans", 10)
        kind = "Monthly edition" if issue["type"] == "monthly" else "Quarterly edition"
        c.drawRightString(w - x, h - 18 * mm, kind)
        c.setFillColor(WHITE)
        c.setFont("Sans-Heavy", 46)
        c.drawString(x - 1.5, h - 46 * mm, issue["title"])
        c.setFont("Serif-Italic", 12.5)
        c.setFillColor(colors.HexColor("#D9DEEE"))
        c.drawString(x, h - 56 * mm, config.SITE["tagline"])
        start = datetime.fromisoformat(issue["start"]).strftime("%-d %B %Y")
        end = datetime.fromisoformat(issue["end"]).strftime("%-d %B %Y")
        published = datetime.fromisoformat(issue["generated"].replace("Z", "+00:00")).strftime("%-d %B %Y")
        status = "Edition in progress, updated " if issue["status"] == "in_progress" else "Published "
        c.setFont("Sans", 9.5)
        c.setFillColor(colors.HexColor("#AEB8D6"))
        c.drawString(x, h - 66 * mm, f"Coverage period {start} to {end}")
        c.drawString(x, h - 71.5 * mm, f"{status}{published}")
        # Stats row
        stats = [("stories", issue["stats"]["stories"]), ("research papers", issue["stats"]["papers"]),
                 ("sources", issue["stats"]["sources"]), ("categories", issue["stats"]["categories"])]
        sx = x
        for label, val in stats:
            c.setFillColor(WHITE)
            c.setFont("Sans-Heavy", 17)
            c.drawString(sx, h - 86 * mm, f"{val:,}")
            c.setFont("Sans", 8.5)
            c.setFillColor(colors.HexColor("#AEB8D6"))
            c.drawString(sx, h - 91 * mm, label)
            sx += 40 * mm
        c.restoreState()
        self._footer(c, doc)

    def _page(self, c, doc):
        w, h = A4
        c.saveState()
        c.setFont("Sans-Semi", 8)
        c.setFillColor(INK)
        c.drawString(20 * mm, h - 13 * mm, config.SITE["name"])
        c.setFont("Sans", 8)
        c.setFillColor(SLATE)
        kind = "Monthly" if self.issue["type"] == "monthly" else "Quarterly"
        c.drawRightString(w - 20 * mm, h - 13 * mm, f"{kind} edition, {self.issue['title']}")
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(20 * mm, h - 15.5 * mm, w - 20 * mm, h - 15.5 * mm)
        c.restoreState()
        self._footer(c, doc)

    def _footer(self, c, doc):
        w, _ = A4
        c.saveState()
        c.setFont("Sans", 7.5)
        c.setFillColor(SLATE)
        c.drawString(20 * mm, 11 * mm, f"Compiled automatically from public sources. Headlines link to the original publication.")
        c.drawRightString(w - 20 * mm, 11 * mm, f"Page {doc.page}")
        c.linkURL(config.SITE["url"], (20 * mm, 6 * mm, 80 * mm, 10 * mm), relative=0)
        c.drawString(20 * mm, 7 * mm, config.SITE["url"])
        c.restoreState()


# --------------------------------------------------------------------------- content builders
def _date(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%-d %b %Y")


def _cat_label(a: dict) -> str:
    c = config.CATEGORY_BY_ID.get(a["cats"][0])
    return f'<font color="{c["color"]}" name="Sans-Semi">{txt(c["name"])}</font>' if c else ""


def _story(a: dict, S: dict, rank: int | None = None, lead: bool = False) -> Flowable:
    src = config.SOURCE_BY_ID[a["source"]]["name"]
    tags = "".join(f'&nbsp;&nbsp;<font name="Sans-Semi" color="#2B47D9">[{txt(t)}]</font>' for t in a.get("tags", []))
    meta = f'{_cat_label(a)}&nbsp;&nbsp;&nbsp;{txt(src)}, {_date(a["date"])}{tags}'
    parts = [Paragraph(meta, S["meta"]),
             Paragraph(f'<link href="{url(a["url"])}">{txt(a["title"])}</link>', S["lead" if lead else "headline"])]
    if a.get("summary"):
        parts.append(Paragraph(txt(a["summary"]), S["body"]))
    cov = a.get("coverage") or []
    if cov:
        links = ", ".join(f'<link href="{url(c["url"])}" color="#2B47D9">{txt(config.SOURCE_BY_ID[c["source"]]["name"])}</link>'
                          for c in cov[:6])
        parts.append(Paragraph(f"Also reported by {links}", S["also"]))
    parts.append(Spacer(1, 10))
    if rank is None:
        return KeepTogether(parts)
    t = Table([[Paragraph(str(rank), S["rank"]), parts]], colWidths=[10 * mm, None])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return KeepTogether([t])


def _paper(p: dict, S: dict) -> Flowable:
    bits = [txt(", ".join(p.get("authors", [])[:3]) + (" et al." if len(p.get("authors", [])) > 3 else ""))]
    if p.get("upvotes"):
        bits.append(f'{p["upvotes"]} upvotes on <link href="{url(p.get("hf") or p["url"])}" color="#2B47D9">Hugging Face</link>')
    bits.append(_date(p["date"]))
    parts = [Paragraph(f'<link href="{url(p["url"])}">{txt(p["title"])}</link>', S["headline"]),
             Paragraph("&nbsp;&nbsp;&nbsp;".join(b for b in bits if b), S["meta"])]
    if p.get("summary"):
        parts.append(Paragraph(txt(p["summary"]), S["body"]))
    parts.append(Spacer(1, 10))
    return KeepTogether(parts)


def _table(rows: list[list], widths: list, S: dict, header: bool = True) -> Table:
    data = [[Paragraph(str(c) if isinstance(c, (int, float)) else c, S["cell"]) for c in r] for r in rows]
    t = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1 if header else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
             ("LEFTPADDING", (0, 0), (-1, -1), 4)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), WASH), ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK)]
    t.setStyle(TableStyle(style))
    return t


def _legacy_view(issue: dict) -> dict:
    """Map the chapter-based issue onto this fallback layout."""
    chapters = issue.get("chapters", [])
    research = next((c for c in chapters if c.get("research")), None)
    return issue | {
        "launches": [], "research_news": [],
        "papers": research["items"] + research["more"] if research else [],
        "sections": [{"id": c["id"], "name": c["kicker"], "color": c["color"], "total": c["stats"]["stories"],
                      "items": c["items"]} for c in chapters if not c.get("research")],
    }


def render_pdf(issue: dict, path: Path) -> None:
    """Fallback renderer (no Chrome available). The primary layout is pdf_html.py."""
    issue = _legacy_view(issue)
    _register_fonts()
    S = _styles()
    story: list[Flowable] = []
    is_q = issue["type"] == "quarterly"

    # ---- Cover page: brief + contents
    story.append(Paragraph("In brief", S["h2"]))
    for line in issue["brief"]:
        story.append(Paragraph(txt(line), S["brief"]))
    if issue.get("backfilled"):
        story.append(Spacer(1, 2))
        story.append(Paragraph(
            "This edition was assembled from source archives when the service started, so news-outlet coverage "
            "is thinner than in later editions. Official announcements and research are complete.", S["note"]))
    if issue["status"] == "in_progress":
        story.append(Paragraph("This period is still under way. The edition is refreshed as new stories arrive.",
                               S["note"]))
    story.append(Spacer(1, 6))
    story.append(Rule())

    sections = [("top", "Top stories", COBALT, f"The {len(issue['top_stories'])} most significant developments")]
    if issue["launches"]:
        sections.append(("launches", "New tools and launches", colors.HexColor(config.CATEGORY_BY_ID["tools-launches"]["color"]),
                         "Products, models, and features that became available"))
    if issue["papers"] or issue["research_news"]:
        sections.append(("research", "Research breakthroughs", colors.HexColor(config.CATEGORY_BY_ID["research"]["color"]),
                         "Notable papers and lab results, ranked by community attention"))
    for sec in issue["sections"]:
        more = sec["total"] - len(sec["items"])
        sub = f"{sec['total']} stories this {'quarter' if is_q else 'month'}" + (f"; top {len(sec['items'])} shown" if more > 0 else "")
        sections.append((f"cat-{sec['id']}", sec["name"], colors.HexColor(sec["color"]), sub))
    sections.append(("trends", "Trends and emerging topics", INK, "What the coverage was about, and what changed"))
    sections.append(("sources", "Sources and method", SLATE, "Where this edition comes from"))

    story.append(Paragraph("In this edition", S["h2"]))
    toc_rows = []
    for key, title, color, _ in sections:
        toc_rows.append(f'<font name="ZapfDingbats" size="7" color="{color.hexval().replace("0x", "#")}">n</font>&nbsp;&nbsp;'
                        f'<a href="#{key}">{txt(title)}</a>')
    half = (len(toc_rows) + 1) // 2
    cols = [toc_rows[:half], toc_rows[half:]]
    toc = Table([[Paragraph(cols[0][i], S["toc"]) if i < len(cols[0]) else "",
                  Paragraph(cols[1][i], S["toc"]) if i < len(cols[1]) else ""] for i in range(half)],
                colWidths=["50%", "50%"], hAlign="LEFT")
    toc.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    story.append(toc)
    story.append(PageBreak())

    sec_meta = {k: (t, c, s) for k, t, c, s in sections}

    def heading(key):
        t, c, s = sec_meta[key]
        story.append(CondPageBreak(60 * mm))
        story.append(SectionHeading(t, key, c, s))
        story.append(Spacer(1, 8))

    heading("top")
    for i, a in enumerate(issue["top_stories"], 1):
        story.append(_story(a, S, rank=i, lead=i <= 3))

    if "launches" in sec_meta:
        heading("launches")
        for a in issue["launches"]:
            story.append(_story(a, S))

    if "research" in sec_meta:
        heading("research")
        for a in issue["research_news"]:
            story.append(_story(a, S))
        if issue["papers"]:
            story.append(Paragraph("Papers the research community upvoted most", S["h2"]))
            for p in issue["papers"]:
                story.append(_paper(p, S))

    for sec in issue["sections"]:
        heading(f"cat-{sec['id']}")
        for a in sec["items"]:
            story.append(_story(a, S))

    # ---- Trends
    heading("trends")
    w = A4[0] - 40 * mm
    if issue["rising"]:
        story.append(Paragraph("Most discussed topics", S["h2"]))
        rows = [["Topic", "Mentions", "Previous period", "Change in share"]]
        for r in issue["rising"]:
            ch = "new" if r["change_pct"] is None else f'{r["change_pct"]:+d}%'
            rows.append([txt(r["topic"]), r["mentions"], r["previous"], ch])
        story.append(_table(rows, [w * 0.46, w * 0.16, w * 0.19, w * 0.19], S))
        story.append(Spacer(1, 4))
        story.append(Paragraph("Mentions count each story once, weighted by how many outlets covered it. "
                               "Change compares each topic's share of coverage with the preceding period of equal length.",
                               S["small"]))
        story.append(Spacer(1, 10))
    story.append(Paragraph("Coverage by category", S["h2"]))
    total = sum(c["count"] for c in issue["category_share"]) or 1
    rows = [["Category", "Stories", "Share"]]
    for c in issue["category_share"]:
        rows.append([f'<font name="ZapfDingbats" size="7" color="{c["color"]}">n</font>&nbsp;&nbsp;{txt(c["name"])}', c["count"],
                     f'{100 * c["count"] / total:.0f}%'])
    story.append(_table(rows, [w * 0.6, w * 0.2, w * 0.2], S))
    if is_q and issue.get("months"):
        story.append(Spacer(1, 10))
        story.append(Paragraph("Stories by month", S["h2"]))
        story.append(_table([["Month", "Stories"]] + [[m["label"], m["stories"]] for m in issue["months"]],
                            [w * 0.6, w * 0.4], S))

    # ---- Sources & method
    heading("sources")
    rows = [["Source", "Type", "Items"]]
    for s in issue["sources"]:
        src = config.SOURCE_BY_ID[s["id"]]
        rows.append([f'<link href="{url(src["home"])}" color="#2B47D9">{txt(s["name"])}</link>',
                     txt(config.TIERS[s["tier"]]["label"]), s["count"]])
    story.append(_table(rows, [w * 0.5, w * 0.35, w * 0.15], S))
    story.append(Spacer(1, 10))
    method = (
        f"{config.SITE['name']} collects articles from official AI company blogs, research groups, arXiv, "
        "Hugging Face Daily Papers, and established technology publications through their public RSS feeds and APIs. "
        "Stories that are mainly about people, politics, lawsuits, or money are left out so the edition stays focused "
        "on technology companies can use. Duplicate articles are merged, and the same story reported by several outlets is grouped under its most "
        "authoritative source. Each story is categorised by a local language model combined with a domain vocabulary, "
        "then ranked by source authority, how many independent outlets covered it, and the significance of the "
        "announcement. Summaries are taken from the publishers' own descriptions. Always check the original source "
        "before relying on a detail."
    )
    story.append(Paragraph(txt(method), S["body"]))

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".pdf.tmp")
    IssueDoc(tmp, issue).build(story)
    tmp.replace(path)
