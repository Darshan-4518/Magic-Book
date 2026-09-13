#!/usr/bin/env bash
# Extract every PDF in ./test_pdfs/ to reading Markdown in ./output/.
# Pass --full to also write intermediate JSON (render.py can turn it into HTML).
set -u
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  echo "Missing .venv. Create it first: python3.11 -m venv .venv && pip install docling psutil pdfplumber"
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
python extract.py "$@"
exit $?
