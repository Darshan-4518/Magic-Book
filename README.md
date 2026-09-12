# DocLing PDF Evaluation Pipeline

Evaluates [Docling](https://github.com/docling-project/docling) as a PDF extraction engine for a mobile reading-view SaaS. Pipeline is **PDF → JSON (our schema) → self-contained HTML**.

## Layout

```
.
├── extract.py       # PDF -> output/<name>.json
├── render.py        # output/<name>.json -> output/<name>.html
├── run_all.sh       # extract + render + summary table
├── test_pdfs/       # drop input PDFs here (git-ignored in spirit)
├── output/          # generated JSON, HTML, error logs
└── .venv/           # Python 3.11 venv (docling, psutil)
```

## One-time setup

Already done in this checkout, but for reference:

```bash
/opt/homebrew/opt/python@3.11/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install docling psutil
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
- `<name>.json` — normalized extraction (our schema, not Docling's raw dict)
- `<name>.md` — Docling's native Markdown export (BODY layer only, headers/footers stripped)
- `<name>.html` — self-contained reader view (open in any browser)
- `<name>.error.txt` — traceback if extraction failed for that file

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
