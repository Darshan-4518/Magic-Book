"""Render our refined block JSON structure to clean reading Markdown.

Unlike Docling's native export_to_markdown, this runs AFTER refine.py, so it
inherits column-order fixes, merged paragraphs, watermark removal, glyph
repairs, and real <sup>/<sub> spans.
"""

from __future__ import annotations

import html as html_lib
import re

TAG_B = re.compile(r"</?b>")
TAG_I = re.compile(r"</?i>")
KEEP_TAGS = re.compile(r"(</?su[bp]>)")
PIPE = re.compile(r"\|")


def md_inline(block_html: str) -> str:
    """Convert our restricted inline HTML (<b> <i> <sup> <sub>) to Markdown."""
    if not block_html:
        return ""
    parts = KEEP_TAGS.split(block_html)
    out: list[str] = []
    for part in parts:
        if KEEP_TAGS.fullmatch(part):
            out.append(part)  # sup/sub stay as HTML; Markdown has no native form
            continue
        part = TAG_B.sub("**", part)
        part = TAG_I.sub("*", part)
        out.append(html_lib.unescape(part))
    return "".join(out)


def _table_md(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [r + [""] * (width - len(r)) for r in rows]
    esc = [[PIPE.sub(r"\|", c).replace("\n", " ") for c in r] for r in norm]
    lines = ["| " + " | ".join(esc[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in esc[1:]]
    return "\n".join(lines)


def _same_title(a: str, b: str) -> bool:
    na = re.sub(r"\W+", "", a).casefold()
    nb = re.sub(r"\W+", "", b).casefold()
    return bool(na and nb) and (na in nb or nb in na)


def to_markdown(data: dict) -> str:
    out: list[str] = []
    title = data.get("title")
    blocks = data.get("blocks", [])
    # if the document's first heading IS the title, promote it instead of
    # printing the title twice
    title_heading = None
    if title and blocks and blocks[0].get("type") == "heading":
        if _same_title(title, blocks[0].get("text", "")):
            title_heading = blocks[0]
    if title and title_heading is None:
        out.append(f"# {title.strip()}")
    footnotes: list[dict] = []

    for b in blocks:
        btype = b.get("type")
        if btype == "heading":
            text = md_inline(b.get("html") or "") or b.get("text", "")
            # strip markdown emphasis inside headings; the # already emphasises
            text = text.replace("**", "").replace("*", "")
            if b is title_heading:
                out.append(f"# {text.strip()}")
                continue
            level = max(1, min(5, int(b.get("level") or 1)))
            out.append(f"{'#' * (level + 1)} {text.strip()}")
        elif btype == "paragraph":
            out.append(md_inline(b.get("html") or "") or b.get("text", ""))
        elif btype == "list_item":
            out.append(f"- {md_inline(b.get('html') or '') or b.get('text', '')}")
        elif btype == "caption":
            text = (md_inline(b.get("html") or "") or b.get("text", "")).strip("*")
            out.append(f"*{text}*")
        elif btype == "table":
            md = _table_md(b.get("rows") or [])
            if md:
                out.append(md)
        elif btype == "code":
            out.append(f"```\n{b.get('text', '')}\n```")
        elif btype == "figure":
            cap = b.get("text", "")
            label = cap if cap and cap != "[figure]" else "figure"
            out.append(f"*({label} — p.{b.get('page', '?')})*")
        elif btype == "footnote":
            footnotes.append(b)
        else:
            out.append(b.get("text", ""))

    if footnotes:
        out.append("---")
        out.append("**Notes**")
        for f in footnotes:
            out.append(f"- {md_inline(f.get('html') or '') or f.get('text', '')}")

    return "\n\n".join(s for s in out if s.strip()) + "\n"
