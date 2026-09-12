"""Extract PDFs in ./test_pdfs/ to normalized JSON in ./output/<name>.json using Docling."""

from __future__ import annotations

import html as html_lib
import json
import re
import sys
import time
import traceback
from pathlib import Path

from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc.common.content_layer import ContentLayer
from docling_core.types.doc.document import (
    CodeItem,
    FloatingItem,
    ListItem,
    PictureItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
    TitleItem,
)
from docling_core.types.doc.labels import DocItemLabel

ROOT = Path(__file__).parent
IN_DIR = ROOT / "test_pdfs"
OUT_DIR = ROOT / "output"

HYPHEN_LINEBREAK = re.compile(r"(\w)[‐-—-]\n(\w)")
NEWLINES = re.compile(r"\s*\n\s*")
MULTI_SPACE = re.compile(r"[ \t]{2,}")
PAGE_NUM_ONLY = re.compile(r"^\s*(?:page\s+)?\d+(?:\s*(?:/|of)\s*\d+)?\s*$", re.IGNORECASE)


def clean_text(raw: str) -> str:
    """Rejoin hyphenated line-ends, flatten hard line breaks, collapse whitespace."""
    if not raw:
        return ""
    s = HYPHEN_LINEBREAK.sub(r"\1\2", raw)
    s = NEWLINES.sub(" ", s)
    s = MULTI_SPACE.sub(" ", s)
    return s.strip()


def format_html(text: str, formatting) -> str:
    """Wrap text in inline HTML using item-level formatting. Escape first."""
    esc = html_lib.escape(text)
    if not formatting:
        return esc
    out = esc
    script = getattr(formatting, "script", None)
    script_val = getattr(script, "value", script) if script is not None else None
    if script_val == "super":
        out = f"<sup>{out}</sup>"
    elif script_val == "sub":
        out = f"<sub>{out}</sub>"
    if getattr(formatting, "italic", False):
        out = f"<i>{out}</i>"
    if getattr(formatting, "bold", False):
        out = f"<b>{out}</b>"
    return out


def first_page(item) -> int | None:
    prov = getattr(item, "prov", None)
    if prov:
        return prov[0].page_no
    return None


def label_to_block_type(label) -> str | None:
    """Map a Docling label to our block type, or return None to skip."""
    L = DocItemLabel
    mapping = {
        L.TITLE: "heading",
        L.SECTION_HEADER: "heading",
        L.PARAGRAPH: "paragraph",
        L.TEXT: "paragraph",
        L.REFERENCE: "paragraph",
        L.FORMULA: "paragraph",
        L.LIST_ITEM: "list_item",
        L.TABLE: "table",
        L.DOCUMENT_INDEX: "table",
        L.CAPTION: "caption",
        L.FOOTNOTE: "footnote",
        L.CODE: "code",
        L.PICTURE: "figure",
        L.CHART: "figure",
    }
    return mapping.get(label)


def classify_dropped(item, text: str) -> str:
    """Bucket a furniture/dropped item as page_header/page_footer/page_number/watermark."""
    label = item.label
    if label == DocItemLabel.PAGE_HEADER:
        return "page_number" if PAGE_NUM_ONLY.match(text) else "page_header"
    if label == DocItemLabel.PAGE_FOOTER:
        return "page_number" if PAGE_NUM_ONLY.match(text) else "page_footer"
    return "page_header"


def table_rows(item: TableItem) -> list[list[str]]:
    data = item.data
    if not data or not data.table_cells:
        return []
    grid: list[list[str]] = [["" for _ in range(data.num_cols)] for _ in range(data.num_rows)]
    for cell in data.table_cells:
        r0 = cell.start_row_offset_idx
        c0 = cell.start_col_offset_idx
        if 0 <= r0 < data.num_rows and 0 <= c0 < data.num_cols:
            grid[r0][c0] = clean_text(cell.text)
    return grid


def caption_text(doc, item: FloatingItem) -> str:
    parts: list[str] = []
    for ref in getattr(item, "captions", []) or []:
        try:
            resolved = ref.resolve(doc)
            t = getattr(resolved, "text", None)
            if t:
                parts.append(clean_text(t))
        except Exception:
            continue
    return " ".join(parts)


def build_block(bid: str, item, doc) -> dict | None:
    btype = label_to_block_type(item.label)
    if btype is None:
        return None
    page = first_page(item)
    block: dict = {"id": bid, "type": btype, "text": "", "html": "", "page": page}

    if isinstance(item, TableItem):
        rows = table_rows(item)
        block["rows"] = rows
        flat = "\n".join(" | ".join(r) for r in rows) if rows else ""
        cap = caption_text(doc, item)
        block["text"] = (cap + "\n" + flat).strip() if cap else flat
        block["html"] = html_lib.escape(block["text"])
        return block

    if isinstance(item, PictureItem):
        cap = caption_text(doc, item) or "[figure]"
        block["text"] = cap
        block["html"] = html_lib.escape(cap)
        return block

    text_raw = getattr(item, "text", "") or ""
    text = clean_text(text_raw)
    block["text"] = text
    block["html"] = format_html(text, getattr(item, "formatting", None))

    if isinstance(item, (SectionHeaderItem,)):
        block["level"] = int(getattr(item, "level", 1) or 1)
    elif isinstance(item, TitleItem) or item.label == DocItemLabel.TITLE:
        block["level"] = 1

    if isinstance(item, ListItem):
        marker = getattr(item, "marker", None)
        if marker:
            block["text"] = f"{marker} {text}".strip()
            block["html"] = f"{html_lib.escape(marker)} {block['html']}"

    if isinstance(item, CodeItem):
        block["html"] = f"<code>{html_lib.escape(text)}</code>"

    return block


def extract_one(pdf_path: Path, converter: DocumentConverter) -> tuple[dict, str]:
    t0 = time.perf_counter()
    result = converter.convert(str(pdf_path))
    doc = result.document
    elapsed = time.perf_counter() - t0
    markdown = doc.export_to_markdown(
        included_content_layers={ContentLayer.BODY},
    )

    layers = {ContentLayer.BODY, ContentLayer.FURNITURE}
    blocks: list[dict] = []
    dropped: list[dict] = []
    title = None
    b_id = 0
    d_id = 0

    for item, _lvl in doc.iterate_items(with_groups=False, included_content_layers=layers):
        if not hasattr(item, "label"):
            continue
        label = item.label
        if label in (DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER):
            text = clean_text(getattr(item, "text", "") or "")
            if not text:
                continue
            d_id += 1
            dropped.append(
                {
                    "id": f"d{d_id}",
                    "type": classify_dropped(item, text),
                    "text": text,
                    "page": first_page(item),
                }
            )
            continue

        if getattr(item, "content_layer", ContentLayer.BODY) != ContentLayer.BODY:
            continue

        b_id += 1
        block = build_block(f"b{b_id}", item, doc)
        if block is None:
            b_id -= 1
            continue
        if not block["text"] and block["type"] != "figure":
            b_id -= 1
            continue
        if title is None and (label == DocItemLabel.TITLE or (
            label == DocItemLabel.SECTION_HEADER and block.get("level", 99) == 1
        )):
            title = block["text"]
        blocks.append(block)

    data = {
        "title": title,
        "source_file": pdf_path.name,
        "page_count": doc.num_pages(),
        "extraction_seconds": round(elapsed, 3),
        "blocks": blocks,
        "dropped": dropped,
    }
    return data, markdown


def build_converter() -> DocumentConverter:
    opts = PdfPipelineOptions()
    opts.accelerator_options = AcceleratorOptions(num_threads=4, device=AcceleratorDevice.CPU)
    opts.do_ocr = False
    opts.do_table_structure = True
    opts.table_structure_options.do_cell_matching = True
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    IN_DIR.mkdir(exist_ok=True)
    pdfs = sorted(IN_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {IN_DIR}/", file=sys.stderr)
        return 1

    converter = build_converter()
    print(f"Processing {len(pdfs)} PDF(s)...", flush=True)
    exit_code = 0
    for pdf in pdfs:
        out_path = OUT_DIR / f"{pdf.stem}.json"
        err_path = OUT_DIR / f"{pdf.stem}.error.txt"
        if err_path.exists():
            err_path.unlink()
        try:
            data, markdown = extract_one(pdf, converter)
            out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            md_path = OUT_DIR / f"{pdf.stem}.md"
            md_path.write_text(markdown)
            print(
                f"  {pdf.name}: {data['page_count']}p, "
                f"{data['extraction_seconds']}s, "
                f"{len(data['blocks'])} blocks, {len(data['dropped'])} dropped, "
                f"md {len(markdown):,} chars",
                flush=True,
            )
        except Exception as exc:
            exit_code = 2
            err_path.write_text(f"{exc}\n\n{traceback.format_exc()}")
            print(f"  {pdf.name}: ERROR {exc}", file=sys.stderr, flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
