# DocLing PDF Evaluation Pipeline

Evaluates [Docling](https://github.com/docling-project/docling) as a PDF extraction engine for a mobile reading-view SaaS. Pipeline is **PDF → JSON (our schema) → self-contained HTML**.

## Layout

```
.
├── extract.py       # PDF -> output/<name>.json (Docling + refine)
├── refine.py        # pdfplumber cross-check: merges split paragraphs, fixes
│                    # headings/TOC/title, rebuilds inline <b>/<i>/<sup> spans
├── render.py        # output/<name>.json -> output/<name>.html
├── run_all.sh       # extract + render + summary table
├── test_pdfs/       # drop input PDFs here (git-ignored in spirit)
├── output/          # generated JSON, MD, HTML, error logs
└── .venv/           # Python 3.11 venv (docling, psutil, pdfplumber)
```

## Accuracy: hybrid Docling + pdfplumber

Docling alone splits paragraphs at every page break (~12% of paragraphs on the
test book) and misclassifies TOC pages. `refine.py` re-opens the PDF with
[pdfplumber](https://github.com/jsvine/pdfplumber) (MIT) for per-word
font/size/position data and repairs the block list:

1. **Flow repair** — merges continuation fragments (block ends mid-sentence,
   next starts lowercase); rejoins hyphenated seams. Survivors carry
   `"merged_from": [...]`.
2. **Heading validation** — demotes "headings" set in body-size non-bold type;
   promotes short large-font paragraphs (level from size tier).
3. **TOC normalization** — pages that are >60% numbered entries become uniform
   `list_item`s and are excluded from title selection.
4. **Title** — PDF metadata `Title` first, else largest-font early text.
5. **Inline spans** — real `<b>/<i>/<sup>/<sub>` runs recovered from font names
   and baseline offsets (Docling only has block-level formatting); attached
   super/subscripts are glued back (`mc<sup>2</sup>`, author markers).
6. **Column order** — pages with a clean vertical gutter (real two-column
   layouts, not prose that merely crosses the midline) are re-sorted to
   column-major reading order; full-width blocks (title, abstract) act as band
   separators and keep their position.
7. **Watermarks** — rotated-glyph text (e.g. a diagonal DRAFT stamp) is
   stripped from blocks or moved to `dropped` as `watermark`.
8. **Footnotes** — small-font bottom-area blocks starting with a marker are
   retyped `footnote` and collected at the end of the Markdown.

Known limits: glyphs whose PDF ToUnicode mapping is wrong (e.g. `≥` encoded as
`‡`) are unfixable from the text layer — both extractors read the same wrong
character; only OCR would recover it. Hyphenated words glued by Docling itself
("superand") need a lexicon to repair.

Each JSON gets a `"repairs"` summary with counts and a `refine_seconds` timing
(~0.03 s/page on top of Docling's ~0.2 s/page).

## One-time setup

Already done in this checkout, but for reference:

```bash
/opt/homebrew/opt/python@3.11/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install docling psutil pdfplumber
```

Notes:
- CPU-only (no CUDA / MPS wired in).
- Venv is ~1.4 GB after install (torch + transformers + docling models package).
- Docling downloads layout/table models on the **first** `convert()` call. Expect ~500 MB–1 GB extra and an extra minute of wall time on the first PDF only. Subsequent runs use the cached models under `~/.cache/docling/`.

## Run everything

```bash
# 1. Put PDFs in ./test_pdfs/
cp ~/Downloads/*.pdf test_pdfs/

# 2. Run the full pipeline (extract -> render -> summary table)
./run_all.sh
```

Output lands in `./output/`:
- `<name>.md` — reading Markdown rendered from the **refined** blocks (not
  Docling's native export): fixed reading order, merged paragraphs, watermarks
  removed, real `<sup>/<sub>` spans, footnotes collected at the end
- `<name>.error.txt` — traceback if extraction failed for that file

Markdown is the only output by default. `./run_all.sh --full` also writes the
intermediate `<name>.json`, which `python render.py` can turn into the
self-contained HTML reader view.

## Run steps individually

```bash
source .venv/bin/activate

# Extract only
python extract.py

# Render only (needs JSON in ./output/)
python render.py
```

## Reader view features

Open any `output/<name>.html` — no server, no external deps.

- Toolbar: `A−` / `A+` font size, **Light / Sepia / Dark** theme (saved to localStorage)
- Reading column: 680 px max, 18 px serif, line-height 1.6
- Hover any block to see a small chip with its type + page number (spot misclassifications fast)
- Bottom: collapsible **Dropped content** listing everything Docling flagged as page header/footer/page number
- Mobile-tuned at ≤ 480 px viewport

## JSON schema

```jsonc
{
  "title": "string or null",
  "source_file": "name.pdf",
  "page_count": 12,
  "extraction_seconds": 4.2,
  "blocks": [
    {
      "id": "b1",
      "type": "heading | paragraph | list_item | table | caption | footnote | code | figure",
      "level": 1,                 // headings only
      "text": "clean text, hyphens re-joined, no hard breaks",
      "html": "inline: <b>, <i>, <sup>, <sub>",
      "page": 3,
      "rows": [["cell", "cell"]]  // tables only
    }
  ],
  "dropped": [
    { "type": "page_header | page_footer | watermark | page_number", "text": "...", "page": 1 }
  ]
}
```

Reading order matches Docling's `iterate_items()` output — no re-sorting.

## Troubleshooting

- **`No PDFs found in test_pdfs/`** — drop at least one `.pdf` in there.
- **First run is slow / hangs on "Downloading..."** — Docling is fetching layout & table models. One-time; check `~/.cache/docling/` (or `~/.cache/huggingface/`) for progress.
- **Extraction error for a specific file** — see `output/<name>.error.txt`. The rest of the batch still runs.
- **Wrong Python** — must use the venv's Python (3.11). `source .venv/bin/activate` first, or call `.venv/bin/python` directly.
