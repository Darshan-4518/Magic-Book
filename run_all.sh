#!/usr/bin/env bash
# Extract, render, and summarize every PDF in ./test_pdfs/.
set -u
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  echo "Missing .venv. Create it first: python3.11 -m venv .venv && pip install docling psutil"
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

mkdir -p output test_pdfs

shopt -s nullglob
pdfs=(test_pdfs/*.pdf)
if [[ ${#pdfs[@]} -eq 0 ]]; then
  echo "No PDFs in test_pdfs/ — drop some in and re-run."
  exit 1
fi

echo "=== Extracting ($(date '+%H:%M:%S')) ==="
python extract.py
extract_rc=$?

echo
echo "=== Rendering ==="
python render.py
render_rc=$?

echo
echo "=== Summary ==="
python - <<'PY'
import json, os
from pathlib import Path
out = Path("output")
rows = []
for pdf in sorted(Path("test_pdfs").glob("*.pdf")):
    stem = pdf.stem
    jf = out / f"{stem}.json"
    ef = out / f"{stem}.error.txt"
    err = ""
    pages = secs = blocks = dropped = 0
    per_page = 0.0
    if ef.exists():
        err = ef.read_text().splitlines()[0][:60] if ef.stat().st_size else "unknown"
    if jf.exists():
        try:
            d = json.loads(jf.read_text())
            pages = d.get("page_count", 0) or 0
            secs = float(d.get("extraction_seconds", 0) or 0)
            blocks = len(d.get("blocks", []))
            dropped = len(d.get("dropped", []))
            per_page = (secs / pages) if pages else 0.0
        except Exception as e:
            err = f"json: {e}"[:60]
    rows.append((pdf.name, pages, secs, per_page, blocks, dropped, err))

hdr = ("file", "pages", "seconds", "s/page", "blocks", "dropped", "errors")
widths = [max(len(str(r[i])) for r in rows + [hdr]) for i in range(len(hdr))]
def fmt(r):
    return " | ".join(str(r[i]).ljust(widths[i]) for i in range(len(hdr)))
print(fmt(hdr))
print("-+-".join("-" * w for w in widths))
for r in rows:
    disp = (r[0], r[1], f"{r[2]:.2f}", f"{r[3]:.2f}", r[4], r[5], r[6] or "-")
    print(fmt(disp))
PY

if [[ $extract_rc -ne 0 || $render_rc -ne 0 ]]; then
  echo
  echo "(one or more steps returned non-zero; see output/*.error.txt)"
  exit 2
fi
