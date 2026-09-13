"""Cross-check and repair Docling block output using pdfplumber char/font data.

Passes (in execution order, see refine_document):
  index      - per-page words with font/size/position flags, filled rects,
               rotated (watermark) glyphs, page geometry; document body size
  watermark  - rotated-glyph text stripped from blocks / moved to dropped
  toc        - normalize TOC-page entries to list_item, exclude from title pick
  headings   - demote body-size non-bold "headings"; promote large-font paragraphs
  footnotes  - small-font bottom-area marker blocks retyped to footnote
  columns    - banded column-major re-sort on two-column pages (full-width
               blocks act as band separators and keep their position)
  inline     - rebuild block html with <b>/<i>/<sup>/<sub> runs, glue attached
               superscripts (mc2, H2O), repair mistranslated glyphs (‡ -> ≥)
  flow       - merge split paragraph continuations (cross-page and intra-page)
  title      - PDF metadata title, else largest-font early text, else Docling's
"""

from __future__ import annotations

import html as html_lib
import re
import statistics
from collections import Counter
from pathlib import Path

import pdfplumber

TERMINAL_END = re.compile(r"[.!?:;…](?:[)\"'”’\]]*)$")
HYPHEN_END = re.compile(r"[\w][‐-—-]$")
TOC_ENTRY = re.compile(r"^\d+[.)]\s+\S")
DOT_LEADER = re.compile(r"\.{3,}\s*\d+\s*$")
BOLD_RE = re.compile(r"bold|black|heavy", re.IGNORECASE)
ITALIC_RE = re.compile(r"italic|oblique", re.IGNORECASE)
FOOTNOTE_START = re.compile(r"^(\d{1,3}|[*†‡§¶])\s+\S")
WS = re.compile(r"\s+")
PUNCT = "\"'“”‘’.,;:!?()[]{}|-‐‑–—"

MERGEABLE = {"paragraph", "list_item"}
SKIPPABLE_BETWEEN = {"figure", "footnote", "caption"}


def _norm(s: str) -> str:
    return s.strip(PUNCT).casefold()


# ------------------------------------------------------------------- style index

class PageStyle:
    __slots__ = ("words", "two_col", "gutter_x", "width", "height", "fill_rects", "rot_chars")

    def __init__(self, words, two_col, gutter_x, width, height, fill_rects, rot_chars):
        self.words = words
        self.two_col = two_col
        self.gutter_x = gutter_x
        self.width = width
        self.height = height
        self.fill_rects = fill_rects
        self.rot_chars = rot_chars


def _word_style_flags(words: list[dict]) -> None:
    """Annotate words with b/i/sup/sub flags (in place); lines are groups of
    words with overlapping vertical extents."""
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        best_line, best_ov = None, 0.0
        for line in lines[-4:]:
            ref = line[0]
            ov = min(w["bottom"], ref["bottom"]) - max(w["top"], ref["top"])
            if ov > best_ov:
                best_line, best_ov = line, ov
        # require the overlap to cover most of the smaller extent, so a raised
        # superscript attaches to its own baseline line, not the line above
        if best_line is not None and best_ov > 0.5 * min(
            w["bottom"] - w["top"], best_line[0]["bottom"] - best_line[0]["top"]
        ):
            best_line.append(w)
        else:
            lines.append([w])
    for line in lines:
        mode_size = Counter(round(w["size"], 1) for w in line).most_common(1)[0][0]
        med_bottom = statistics.median(w["bottom"] for w in line)
        for w in line:
            small = w["size"] < mode_size * 0.82
            w["sup"] = small and w["bottom"] < med_bottom - 0.8
            w["sub"] = small and w["bottom"] > med_bottom + 0.8
            w["b"] = bool(BOLD_RE.search(w["fontname"]))
            w["i"] = bool(ITALIC_RE.search(w["fontname"]))


def _detect_columns(words: list[dict], page_width: float) -> tuple[bool, float | None]:
    """Two-column iff most text LINES stay on one side of the page midline.
    Single-column prose fails this: every line crosses the middle."""
    if len(words) < 40:
        return False, None
    mid = page_width / 2
    band = 8.0
    rows: dict[int, list[dict]] = {}
    for w in words:
        rows.setdefault(int(w["top"] // 4), []).append(w)
    if len(rows) < 15:
        return False, None
    split = spanning = 0
    for row in rows.values():
        has_left = any(w["x1"] < mid - band for w in row)
        has_right = any(w["x0"] > mid + band for w in row)
        overlaps_gutter = any(w["x0"] < mid + band and w["x1"] > mid - band for w in row)
        if has_left and has_right:
            if overlaps_gutter:
                spanning += 1  # one continuous line straight across (prose)
            else:
                split += 1  # two runs with a clean gap at the gutter
    if split >= 8 and split > 2 * spanning:
        return True, mid
    return False, None


def build_style_index(pdf_path: Path) -> tuple[dict[int, PageStyle], float, dict]:
    pages: dict[int, PageStyle] = {}
    size_weight: Counter = Counter()
    with pdfplumber.open(str(pdf_path)) as pdf:
        metadata = dict(pdf.metadata or {})
        for i, page in enumerate(pdf.pages, start=1):
            try:
                words = page.extract_words(extra_attrs=["fontname", "size"])
            except Exception:
                words = []
            rot = Counter(
                c["text"]
                for c in page.chars
                if abs(c.get("matrix", (1, 0, 0, 1, 0, 0))[1]) > 0.05 and c["text"].strip()
            )
            # rotated glyphs are watermark candidates; exclude them from body stats
            words = [w for w in words if not (len(w["text"]) == 1 and w["text"] in rot)]
            for w in words:
                size_weight[round(w["size"], 1)] += len(w["text"])
            _word_style_flags(words)
            two_col, gutter = _detect_columns(words, page.width)
            fills = [
                (r["x0"], r["top"], r["x1"], r["bottom"])
                for r in page.rects
                if r.get("fill") and (r["x1"] - r["x0"]) * (r["bottom"] - r["top"]) > 800
            ]
            pages[i] = PageStyle(words, two_col, gutter, page.width, page.height, fills, rot)
    body_size = size_weight.most_common(1)[0][0] if size_weight else 12.0
    return pages, body_size, metadata


# ------------------------------------------------------------------- alignment

class Alignment:
    """Maps a block's whitespace tokens to page words with style flags."""

    __slots__ = ("tokens", "pword_lists", "repaired", "glue", "coverage")

    def __init__(self, tokens, pword_lists, repaired, glue, coverage):
        self.tokens = tokens          # list[str] docling tokens
        self.pword_lists = pword_lists  # list[list[dict]] parallel to tokens
        self.repaired = repaired      # list[str|None] substituted token text
        self.glue = glue              # list[bool] no-space-before for token i
        self.coverage = coverage

    def flat_words(self) -> list[dict]:
        return [w for lst in self.pword_lists for w in lst]

    def token_flags(self) -> list[tuple[bool, bool, bool, bool]]:
        flags = []
        for lst in self.pword_lists:
            flags.append(
                (
                    any(w["b"] for w in lst),
                    any(w["i"] for w in lst),
                    any(w["sup"] for w in lst) and all(w["sup"] or not w["text"].strip() for w in lst),
                    any(w["sub"] for w in lst) and all(w["sub"] or not w["text"].strip() for w in lst),
                )
                if lst
                else (False, False, False, False)
            )
        return flags


def _walk(tokens: list[str], pwords: list[dict], start: int):
    """Greedy 3-way token/word alignment from pwords[start]. Returns
    (pword_lists, repaired) parallel to tokens, truncated where alignment dies."""
    pword_lists: list[list[dict]] = []
    repaired: list[str | None] = []
    ti, pi = 0, start
    n, m = len(tokens), len(pwords)
    while ti < n and pi < m:
        tn, pn = _norm(tokens[ti]), _norm(pwords[pi]["text"])
        if tn == pn:
            pword_lists.append([pwords[pi]])
            repaired.append(None)
            ti += 1
            pi += 1
            continue
        if not tn:  # punctuation-only docling token with no page counterpart
            pword_lists.append([])
            repaired.append(None)
            ti += 1
            continue
        if not pn:  # punctuation-only page word; skip it
            pi += 1
            continue
        # merge: token == concat of next k page words ("mc2" vs "mc"+"2")
        acc, k = pn, 1
        while len(acc) < len(tn) and pi + k < m and k < 5:
            acc += _norm(pwords[pi + k]["text"])
            k += 1
        if acc == tn:
            pword_lists.append(pwords[pi : pi + k])
            repaired.append(None)
            ti += 1
            pi += k
            continue
        # split: page word == concat of next j tokens ("mc" "2" vs "mc2")
        acc2, j = tn, 1
        while len(acc2) < len(pn) and ti + j < n and j < 5:
            acc2 += _norm(tokens[ti + j])
            j += 1
        if acc2 == pn:
            for _ in range(j):
                pword_lists.append([pwords[pi]])
                repaired.append(None)
            ti += j
            pi += 1
            continue
        # substitution (glyph repair): 1:1 swap confirmed by the following pair
        nt = _norm(tokens[ti + 1]) if ti + 1 < n else None
        np_ = _norm(pwords[pi + 1]["text"]) if pi + 1 < m else None
        if nt is not None and nt == np_ and abs(len(tn) - len(pn)) <= 2:
            pword_lists.append([pwords[pi]])
            repaired.append(pwords[pi]["text"])
            ti += 1
            pi += 1
            continue
        break
    return pword_lists, repaired


def align_block(block: dict, style: PageStyle) -> Alignment | None:
    tokens = WS.split(block["text"].strip())
    if not tokens or not style.words:
        return None
    first = next((t for t in tokens if _norm(t)), None)
    if first is None:
        return None
    fnorm = _norm(first)
    pwords = style.words
    best: tuple[list, list] | None = None
    candidates = [i for i, w in enumerate(pwords) if _norm(w["text"]) == fnorm][:40]
    for start in candidates:
        pls, rep = _walk(tokens, pwords, start)
        if best is None or len(pls) > len(best[0]):
            best = (pls, rep)
        if len(pls) == len(tokens):
            break
    if best is None:
        return None
    pls, rep = best
    coverage = len(pls) / len(tokens)
    if coverage < 0.8 or (len(tokens) > 1 and sum(1 for l in pls if l) < 2):
        return None
    # pad to full token length so callers can zip safely
    while len(pls) < len(tokens):
        pls.append([])
        rep.append(None)
    glue = [False] * len(tokens)
    for i in range(1, len(tokens)):
        prev = pls[i - 1][-1] if pls[i - 1] else None
        cur = pls[i][0] if pls[i] else None
        if prev and cur and cur is not prev:
            same_line = cur["top"] < prev["bottom"] and cur["bottom"] > prev["top"]
            if same_line and (cur["x0"] - prev["x1"]) < 0.7:
                glue[i] = True
    return Alignment(tokens, pls, rep, glue, coverage)


class AlignCache:
    def __init__(self, styles: dict[int, PageStyle]):
        self.styles = styles
        self.cache: dict[int, Alignment | None] = {}

    def get(self, block: dict) -> Alignment | None:
        key = id(block)
        if key not in self.cache:
            style = self.styles.get(block.get("page") or -1)
            self.cache[key] = align_block(block, style) if style else None
        return self.cache[key]


# ------------------------------------------------------------------- watermark

def strip_watermarks(blocks: list[dict], dropped: list[dict], styles: dict[int, PageStyle]) -> int:
    removed = 0
    kept: list[dict] = []
    for b in blocks:
        style = styles.get(b.get("page") or -1)
        rot = style.rot_chars if style else None
        if not rot or sum(rot.values()) < 3:
            kept.append(b)
            continue

        def is_wm(tok: str) -> bool:
            t = tok.strip(PUNCT)
            return (
                len(t) >= 3
                and t.isalpha()
                and t.isupper()
                and not (Counter(t) - rot)  # token's letters all appear in rotated glyphs
            )

        tokens = b["text"].split()
        if tokens and all(is_wm(t) for t in tokens):
            dropped.append(
                {"id": b["id"], "type": "watermark", "text": b["text"], "page": b.get("page")}
            )
            removed += 1
            continue
        changed = False
        while tokens and is_wm(tokens[0]):
            tokens.pop(0)
            changed = True
        while tokens and is_wm(tokens[-1]):
            tokens.pop()
            changed = True
        if changed:
            b["text"] = " ".join(tokens)
            b["html"] = html_lib.escape(b["text"])
            removed += 1
            # Docling merged the watermark glyphs into this block, so its bbox
            # spans the watermark area; recompute from the surviving words.
            al = align_block(b, style)
            if al and al.coverage == 1.0:
                ws = al.flat_words()
                if ws:
                    b["bbox"] = [
                        round(min(w["x0"] for w in ws), 1),
                        round(min(w["top"] for w in ws), 1),
                        round(max(w["x1"] for w in ws), 1),
                        round(max(w["bottom"] for w in ws), 1),
                    ]
        kept.append(b)
    blocks[:] = kept
    return removed


# ------------------------------------------------------------------- TOC pages

def detect_toc_pages(blocks: list[dict]) -> set[int]:
    by_page: dict[int, list[dict]] = {}
    for b in blocks:
        if b.get("page") and b["type"] in ("paragraph", "heading", "list_item"):
            by_page.setdefault(b["page"], []).append(b)
    toc_pages: set[int] = set()
    for page, blist in by_page.items():
        if len(blist) < 4:
            continue
        hits = sum(1 for b in blist if TOC_ENTRY.match(b["text"]) or DOT_LEADER.search(b["text"]))
        if hits / len(blist) > 0.6:
            toc_pages.add(page)
    return toc_pages


def normalize_toc(blocks: list[dict], toc_pages: set[int]) -> int:
    changed = 0
    for b in blocks:
        if b.get("page") in toc_pages and b["type"] in ("paragraph", "heading"):
            b["type"] = "list_item"
            b.pop("level", None)
            changed += 1
    return changed


# ------------------------------------------------------------------- headings

def validate_headings(
    blocks: list[dict], aligns: AlignCache, body_size: float, toc_pages: set[int]
) -> tuple[int, int]:
    demoted = promoted = 0
    for b in blocks:
        if b.get("page") in toc_pages or b["type"] not in ("heading", "paragraph"):
            continue
        al = aligns.get(b)
        if al is None:
            continue
        words = al.flat_words()
        if not words:
            continue
        med_size = statistics.median(w["size"] for w in words)
        mostly_bold = sum(w["b"] for w in words) / len(words) > 0.6
        if b["type"] == "heading":
            if med_size <= body_size * 1.05 and not mostly_bold:
                b["type"] = "paragraph"
                b.pop("level", None)
                demoted += 1
        else:
            if len(b["text"]) < 90 and med_size >= body_size * 1.3:
                b["type"] = "heading"
                b["level"] = 1 if med_size >= body_size * 1.6 else 2
                promoted += 1
    return demoted, promoted


# ------------------------------------------------------------------- footnotes

def _in_fill_rect(bbox: list[float], style: PageStyle) -> bool:
    l, t, r, btm = bbox
    cx, cy = (l + r) / 2, (t + btm) / 2
    return any(x0 - 3 <= cx <= x1 + 3 and y0 - 3 <= cy <= y1 + 3 for x0, y0, x1, y1 in style.fill_rects)


def detect_footnotes(
    blocks: list[dict], aligns: AlignCache, styles: dict[int, PageStyle], body_size: float
) -> int:
    changed = 0
    for b in blocks:
        if b["type"] not in ("paragraph", "list_item"):
            continue
        if not FOOTNOTE_START.match(b["text"]):
            continue
        style = styles.get(b.get("page") or -1)
        bbox = b.get("bbox")
        if not style or not bbox:
            continue
        if bbox[1] < style.height * 0.5 or _in_fill_rect(bbox, style):
            continue
        al = aligns.get(b)
        if al is None:
            continue
        words = al.flat_words()
        if words and statistics.median(w["size"] for w in words) <= body_size * 0.85:
            b["type"] = "footnote"
            b.pop("level", None)
            changed += 1
    return changed


# ------------------------------------------------------------------- column order

def fix_column_order(blocks: list[dict], styles: dict[int, PageStyle]) -> list[int]:
    """Banded column-major re-sort. Full-width blocks split the page into
    vertical bands and keep their own position; within a band, blocks read
    left column top-to-bottom, then right column."""
    fixed_pages: list[int] = []
    by_page: dict[int, list[int]] = {}
    for idx, b in enumerate(blocks):
        if b.get("page") and b.get("bbox"):
            by_page.setdefault(b["page"], []).append(idx)
    for page, idxs in by_page.items():
        style = styles.get(page)
        if not style or not style.two_col or len(idxs) < 3:
            continue
        gutter = style.gutter_x

        def is_span(i: int) -> bool:
            l, _, r, _ = blocks[i]["bbox"]
            return (r - l) > style.width * 0.55 or (l < gutter - 20 and r > gutter + 20)

        spans = sorted((i for i in idxs if is_span(i)), key=lambda i: blocks[i]["bbox"][1])
        span_mids = [(blocks[i]["bbox"][1] + blocks[i]["bbox"][3]) / 2 for i in spans]

        def sort_key(i: int):
            l, t, r, btm = blocks[i]["bbox"]
            mid_y = (t + btm) / 2
            if is_span(i):
                band = sum(1 for m in span_mids if m < mid_y)  # its own slot
                return (band, -1, t)
            band = sum(1 for m in span_mids if m < t)
            col = 0 if (l + r) / 2 < gutter else 1
            return (band, col, t)

        want = sorted(idxs, key=sort_key)
        if want != idxs:
            reordered = [blocks[i] for i in want]
            for slot, blk in zip(idxs, reordered):
                blocks[slot] = blk
            fixed_pages.append(page)
    return fixed_pages


# ------------------------------------------------------------------- inline spans

def _render_tokens(al: Alignment) -> tuple[str, str]:
    """Return (plain_text, inline_html) from an alignment, applying glyph
    repairs, glue (attached super/subscripts), and style runs."""
    flags = al.token_flags()
    texts = [al.repaired[i] or al.tokens[i] for i in range(len(al.tokens))]
    # plain text: glued tokens join without a space
    plain_parts: list[str] = []
    for i, t in enumerate(texts):
        if i and not al.glue[i]:
            plain_parts.append(" ")
        plain_parts.append(t)
    plain = "".join(plain_parts)

    # two-level nesting: group runs by (bold, italic) first so sup/sub sit
    # INSIDE a continuous <b>/<i> span — otherwise the Markdown conversion
    # produces adjacent */** markers that don't render
    outer: list[tuple[tuple[bool, bool], list[tuple[tuple[bool, bool], str, bool]]]] = []
    for i, t in enumerate(texts):
        bold, italic, sup, sub = flags[i]
        entry = ((sup, sub), t, al.glue[i] if i else False)
        if outer and outer[-1][0] == (bold, italic):
            outer[-1][1].append(entry)
        else:
            outer.append(((bold, italic), [entry]))

    html_parts: list[str] = []
    for (bold, italic), entries in outer:
        inner_parts: list[str] = []
        seg_ss: tuple | None = None
        seg: list[str] = []

        def flush():
            nonlocal seg, seg_ss
            if not seg:
                return
            text = html_lib.escape("".join(seg))
            if seg_ss[0]:
                text = f"<sup>{text}</sup>"
            elif seg_ss[1]:
                text = f"<sub>{text}</sub>"
            inner_parts.append(text)
            seg = []

        first_in_outer = True
        for ss, t, glued in entries:
            joiner = "" if (first_in_outer or glued) else " "
            first_in_outer = False
            if ss != seg_ss:
                flush()
                if joiner and inner_parts:
                    inner_parts.append(joiner)
                seg_ss = ss
                seg.append(t)
            else:
                seg.append(joiner + t)
        flush()
        inner = "".join(inner_parts)
        if italic:
            inner = f"<i>{inner}</i>"
        if bold:
            inner = f"<b>{inner}</b>"
        if html_parts and not (entries and entries[0][2]):
            html_parts.append(" ")
        html_parts.append(inner)
    return plain, "".join(html_parts)


def rebuild_inline_html(blocks: list[dict], aligns: AlignCache) -> tuple[int, int]:
    rebuilt = repaired_ct = 0
    for b in blocks:
        if b["type"] in ("table", "figure", "code"):
            continue
        al = aligns.get(b)
        if al is None:
            continue
        has_style = any(any(f) for f in al.token_flags())
        has_repair = any(r is not None for r in al.repaired)
        has_glue = any(al.glue)
        if not (has_style or has_repair or has_glue):
            b["html"] = html_lib.escape(b["text"])
            continue
        if al.coverage < 1.0:
            continue  # partial alignment: don't risk dropping text
        plain, inline = _render_tokens(al)
        b["text"] = plain
        b["html"] = inline
        rebuilt += 1
        if has_repair:
            repaired_ct += 1
    return rebuilt, repaired_ct


# ------------------------------------------------------------------- flow repair

def _is_continuation(prev: dict, nxt: dict) -> bool:
    pt = prev["text"].rstrip()
    nt = nxt["text"].lstrip()
    if not pt or not nt:
        return False
    prev_open = not TERMINAL_END.search(pt) or HYPHEN_END.search(pt)
    first_alpha = next((c for c in nt if c.isalpha()), "")
    next_lower = bool(first_alpha) and first_alpha.islower()
    return bool(prev_open and next_lower)


def _merge_html(prev_html: str, next_html: str, hyphen: bool) -> str:
    if hyphen:
        prev_html = re.sub(r"[‐-—-]((?:</[a-z]+>)*)\s*$", r"\1", prev_html)
        return prev_html + next_html
    return prev_html + " " + next_html


def merge_continuations(blocks: list[dict]) -> int:
    merged = 0
    i = 0
    while i < len(blocks):
        cur = blocks[i]
        if cur["type"] not in MERGEABLE:
            i += 1
            continue
        j = i + 1
        hops = 0
        while j < len(blocks) and blocks[j]["type"] in SKIPPABLE_BETWEEN and hops < 3:
            j += 1
            hops += 1
        if j >= len(blocks):
            break
        nxt = blocks[j]
        if nxt["type"] not in MERGEABLE or not _is_continuation(cur, nxt):
            i += 1
            continue
        hyphen = bool(HYPHEN_END.search(cur["text"].rstrip()))
        if hyphen:
            cur["text"] = re.sub(r"[‐-—-]\s*$", "", cur["text"].rstrip()) + nxt["text"].lstrip()
        else:
            cur["text"] = cur["text"].rstrip() + " " + nxt["text"].lstrip()
        cur["html"] = _merge_html(cur["html"], nxt["html"], hyphen)
        cur.setdefault("merged_from", []).append(nxt["id"])
        del blocks[j]
        merged += 1
    return merged


# ------------------------------------------------------------------- title

def pick_title(
    docling_title: str | None,
    metadata: dict,
    styles: dict[int, PageStyle],
    body_size: float,
    toc_pages: set[int],
) -> str | None:
    meta_title = (metadata.get("Title") or "").strip()
    if meta_title and not meta_title.lower().endswith((".pdf", ".doc", ".docx", ".indd")):
        return meta_title
    best, best_size = None, 0.0
    for page in (1, 2, 3):
        style = styles.get(page)
        if not style or page in toc_pages:
            continue
        for w in style.words:
            if w["size"] > best_size:
                best_size = w["size"]
    if best_size >= body_size * 1.3:
        for page in (1, 2, 3):
            style = styles.get(page)
            if not style:
                continue
            line = [w["text"] for w in style.words if abs(w["size"] - best_size) < 0.5]
            if line:
                best = " ".join(line[:20])
                break
    return best or docling_title


# ------------------------------------------------------------------- entry point

def refine_document(
    blocks: list[dict], dropped: list[dict], pdf_path: Path, docling_title: str | None
) -> tuple[list[dict], list[dict], str | None, dict]:
    styles, body_size, metadata = build_style_index(pdf_path)
    aligns = AlignCache(styles)

    watermarks = strip_watermarks(blocks, dropped, styles)
    toc_pages = detect_toc_pages(blocks)
    toc_changed = normalize_toc(blocks, toc_pages)
    demoted, promoted = validate_headings(blocks, aligns, body_size, toc_pages)
    footnotes = detect_footnotes(blocks, aligns, styles, body_size)
    order_fixed = fix_column_order(blocks, styles)
    inline_rebuilt, glyphs_repaired = rebuild_inline_html(blocks, aligns)
    merged = merge_continuations(blocks)
    title = pick_title(docling_title, metadata, styles, body_size, toc_pages)

    repairs = {
        "body_font_size": body_size,
        "merged": merged,
        "headings_demoted": demoted,
        "headings_promoted": promoted,
        "toc_pages": sorted(toc_pages),
        "toc_blocks_normalized": toc_changed,
        "column_order_fixed_pages": order_fixed,
        "inline_html_rebuilt": inline_rebuilt,
        "glyphs_repaired_blocks": glyphs_repaired,
        "watermarks_removed": watermarks,
        "footnotes_detected": footnotes,
        "title_source": ("metadata" if (metadata.get("Title") or "").strip() else "font-heuristic"),
    }
    return blocks, dropped, title, repairs
