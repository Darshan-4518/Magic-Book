"""Render each ./output/<name>.json to a self-contained ./output/<name>.html reader view."""

from __future__ import annotations

import html as html_lib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "output"

CSS = """
:root {
  --bg: #ffffff;
  --fg: #1a1a1a;
  --muted: #6b6b6b;
  --rule: #e5e5e5;
  --accent: #2a5db0;
  --chip-bg: rgba(0,0,0,0.68);
  --chip-fg: #ffffff;
  --toolbar-bg: rgba(255,255,255,0.92);
  --font-size: 18px;
}
body.theme-sepia {
  --bg: #f4ecd8;
  --fg: #2b2622;
  --muted: #7a6a58;
  --rule: #d9cdb4;
  --accent: #8a4b1c;
  --toolbar-bg: rgba(244,236,216,0.94);
}
body.theme-dark {
  --bg: #14161a;
  --fg: #e6e6e6;
  --muted: #9aa0a6;
  --rule: #2a2d33;
  --accent: #7aa7ff;
  --chip-bg: rgba(255,255,255,0.86);
  --chip-fg: #14161a;
  --toolbar-bg: rgba(20,22,26,0.94);
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg); }
body {
  font-family: Georgia, "Iowan Old Style", "Times New Roman", serif;
  font-size: var(--font-size);
  line-height: 1.6;
  -webkit-text-size-adjust: 100%;
}
.toolbar {
  position: sticky; top: 0; z-index: 20;
  display: flex; gap: 6px; align-items: center;
  padding: 10px 14px;
  background: var(--toolbar-bg);
  backdrop-filter: saturate(160%) blur(6px);
  border-bottom: 1px solid var(--rule);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 14px;
}
.toolbar button {
  border: 1px solid var(--rule);
  background: transparent;
  color: var(--fg);
  border-radius: 6px;
  padding: 6px 10px;
  cursor: pointer;
  font: inherit;
}
.toolbar button:hover { border-color: var(--accent); color: var(--accent); }
.toolbar button.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.toolbar .spacer { flex: 1; }
.toolbar .meta { color: var(--muted); font-size: 12px; }
main {
  max-width: 680px;
  margin: 0 auto;
  padding: 24px 20px 96px;
}
.title { font-size: 1.9em; margin: 12px 0 4px; line-height: 1.2; }
.source { color: var(--muted); font-size: 13px; margin-bottom: 24px; }
.block { position: relative; margin: 0.9em 0; }
.block:hover::after {
  content: attr(data-label);
  position: absolute; right: -6px; top: -18px;
  background: var(--chip-bg); color: var(--chip-fg);
  padding: 2px 8px; border-radius: 10px;
  font: 11px/1 -apple-system, sans-serif;
  white-space: nowrap;
}
h1.b, h2.b, h3.b, h4.b, h5.b, h6.b { line-height: 1.25; margin: 1.4em 0 0.35em; }
h1.b { font-size: 1.7em; }
h2.b { font-size: 1.4em; }
h3.b { font-size: 1.2em; }
h4.b, h5.b, h6.b { font-size: 1.05em; }
p.b { margin: 0.6em 0; }
ul.b { padding-left: 1.4em; margin: 0.4em 0; }
ul.b li + li { margin-top: 0.25em; }
.b-caption { color: var(--muted); font-size: 0.9em; font-style: italic; }
.b-footnote { color: var(--muted); font-size: 0.85em; border-top: 1px solid var(--rule); padding-top: 6px; margin-top: 1em; }
.b-code {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.9em;
  background: color-mix(in srgb, var(--fg) 6%, transparent);
  padding: 10px 12px; border-radius: 6px;
  overflow-x: auto; white-space: pre-wrap; word-break: break-word;
}
.b-figure {
  color: var(--muted); font-style: italic;
  border: 1px dashed var(--rule); padding: 12px; border-radius: 6px;
  text-align: center;
}
table.b {
  border-collapse: collapse; width: 100%; margin: 0.8em 0;
  font-size: 0.92em; display: block; overflow-x: auto;
}
table.b th, table.b td {
  border: 1px solid var(--rule); padding: 6px 8px; text-align: left; vertical-align: top;
}
table.b tr:first-child td { background: color-mix(in srgb, var(--fg) 5%, transparent); font-weight: 600; }
.dropped {
  margin-top: 48px; border-top: 1px solid var(--rule); padding-top: 16px;
  font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 14px;
}
.dropped summary { cursor: pointer; color: var(--muted); }
.dropped ul { padding-left: 1.2em; }
.dropped .dtype { color: var(--accent); font-family: ui-monospace, monospace; font-size: 12px; }
@media (max-width: 480px) {
  main { padding: 16px 14px 80px; }
  .toolbar { padding: 8px 10px; flex-wrap: wrap; }
  .toolbar .meta { flex-basis: 100%; order: 10; }
  .title { font-size: 1.5em; }
}
"""

JS = """
(function(){
  var STEP = 1, MIN = 13, MAX = 28;
  var root = document.documentElement;
  function fs(){ return parseInt(getComputedStyle(document.body).fontSize, 10) || 18; }
  function setFs(px){
    px = Math.max(MIN, Math.min(MAX, px));
    document.body.style.setProperty('--font-size', px + 'px');
    try { localStorage.setItem('reader.fs', px); } catch(e){}
  }
  function setTheme(t){
    document.body.classList.remove('theme-light','theme-sepia','theme-dark');
    document.body.classList.add('theme-' + t);
    document.querySelectorAll('[data-theme]').forEach(function(b){
      b.classList.toggle('active', b.dataset.theme === t);
    });
    try { localStorage.setItem('reader.theme', t); } catch(e){}
  }
  document.addEventListener('DOMContentLoaded', function(){
    var savedFs = null, savedTheme = null;
    try { savedFs = parseInt(localStorage.getItem('reader.fs'), 10); } catch(e){}
    try { savedTheme = localStorage.getItem('reader.theme'); } catch(e){}
    if (savedFs) setFs(savedFs);
    setTheme(savedTheme || 'light');
    document.getElementById('fs-inc').addEventListener('click', function(){ setFs(fs() + STEP); });
    document.getElementById('fs-dec').addEventListener('click', function(){ setFs(fs() - STEP); });
    document.querySelectorAll('[data-theme]').forEach(function(b){
      b.addEventListener('click', function(){ setTheme(b.dataset.theme); });
    });
  });
})();
"""


def esc(s: str) -> str:
    return html_lib.escape(s or "")


def render_block(b: dict) -> str:
    btype = b.get("type", "paragraph")
    page = b.get("page")
    label = f"{btype} · p.{page}" if page else btype
    data_attr = f'data-label="{esc(label)}"'
    inner_html = b.get("html") or esc(b.get("text", ""))

    if btype == "heading":
        level = max(1, min(6, int(b.get("level") or 2)))
        return f'<div class="block" {data_attr}><h{level} class="b">{inner_html}</h{level}></div>'
    if btype == "list_item":
        return f'<div class="block" {data_attr}><ul class="b"><li>{inner_html}</li></ul></div>'
    if btype == "caption":
        return f'<div class="block" {data_attr}><p class="b b-caption">{inner_html}</p></div>'
    if btype == "footnote":
        return f'<div class="block" {data_attr}><p class="b b-footnote">{inner_html}</p></div>'
    if btype == "code":
        raw = esc(b.get("text", ""))
        return f'<div class="block" {data_attr}><pre class="b b-code">{raw}</pre></div>'
    if btype == "figure":
        return f'<div class="block" {data_attr}><div class="b b-figure">{inner_html}</div></div>'
    if btype == "table":
        rows = b.get("rows") or []
        if not rows:
            return f'<div class="block" {data_attr}><p class="b b-caption">[empty table]</p></div>'
        body = "".join(
            "<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>" for row in rows
        )
        return f'<div class="block" {data_attr}><table class="b">{body}</table></div>'
    return f'<div class="block" {data_attr}><p class="b">{inner_html}</p></div>'


def render_dropped(items: list[dict]) -> str:
    if not items:
        return ""
    lis = "".join(
        f'<li><span class="dtype">{esc(d.get("type","?"))}</span> · p.{d.get("page","?")} — {esc(d.get("text",""))}</li>'
        for d in items
    )
    return (
        '<details class="dropped">'
        f"<summary>Dropped content ({len(items)} items)</summary>"
        f"<ul>{lis}</ul></details>"
    )


def render_doc(data: dict) -> str:
    title = data.get("title") or data.get("source_file", "Document")
    source = data.get("source_file", "")
    pages = data.get("page_count", "?")
    secs = data.get("extraction_seconds", "?")
    blocks_html = "\n".join(render_block(b) for b in data.get("blocks", []))
    dropped_html = render_dropped(data.get("dropped", []))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body class="theme-light">
<div class="toolbar">
  <button id="fs-dec" aria-label="Smaller text">A−</button>
  <button id="fs-inc" aria-label="Larger text">A+</button>
  <button data-theme="light">Light</button>
  <button data-theme="sepia">Sepia</button>
  <button data-theme="dark">Dark</button>
  <span class="spacer"></span>
  <span class="meta">{esc(source)} · {pages}p · {secs}s</span>
</div>
<main>
  <h1 class="title">{esc(title)}</h1>
  <div class="source">{esc(source)}</div>
  {blocks_html}
  {dropped_html}
</main>
<script>{JS}</script>
</body>
</html>
"""


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    jsons = sorted(p for p in OUT_DIR.glob("*.json") if not p.name.endswith(".error.json"))
    if not jsons:
        print(f"No JSON files in {OUT_DIR}/", file=sys.stderr)
        return 1
    for jf in jsons:
        try:
            data = json.loads(jf.read_text())
        except Exception as exc:
            print(f"  {jf.name}: JSON parse error {exc}", file=sys.stderr)
            continue
        out_path = OUT_DIR / f"{jf.stem}.html"
        out_path.write_text(render_doc(data))
        print(f"  {jf.name} -> {out_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
