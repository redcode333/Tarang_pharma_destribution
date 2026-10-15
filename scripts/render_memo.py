"""Render memo.md to memo.pdf (one A4 page) with headless Chromium.

Handles only what memo.md uses: ## headings, paragraphs, **bold**, pipe tables, - bullets, 1. lists.
Needs playwright (see requirements.txt): python scripts/render_memo.py
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC, OUT = REPO / "memo.md", REPO / "memo.pdf"

CSS = """
@page { size: A4; margin: 12mm 14mm; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 10.5pt; line-height: 1.32; color: #1d1d1f; margin: 0; }
h2 { color: #1f3a5f; font-size: 13pt; margin: 7pt 0 1pt; }
p { margin: 0 0 3pt; }
table { border-collapse: collapse; margin: 2pt 0 3pt; }
th { text-align: left; font-weight: 600; padding: 1.5pt 8pt 1.5pt 0; border-bottom: 1px solid #c8d0dc; }
td { padding: 1.5pt 8pt 1.5pt 0; border-bottom: 1px solid #c8d0dc; }
ul, ol { margin: 0 0 3pt; padding-left: 16pt; }
li { margin: 0; }
"""


def inline(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(text, quote=False))


def to_html(md: str) -> str:
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
            i += 1
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            head = "".join(f"<th>{inline(c)}</th>" for c in rows[0])
            body = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
            out.append(f"<table><tr>{head}</tr>{body}</table>")
        elif re.match(r"(- |\d+\. )", line):
            tag = "ul" if line.startswith("- ") else "ol"
            items = []
            while i < len(lines) and re.match(r"(- |\d+\. )", lines[i]):
                items.append(re.sub(r"^(- |\d+\. )", "", lines[i].rstrip()))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{inline(t)}</li>" for t in items) + f"</{tag}>")
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not re.match(r"(## |\||- |\d+\. )", lines[i]):
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{inline(' '.join(para))}</p>")
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{''.join(out)}</body></html>"


def main() -> int:
    from playwright.sync_api import sync_playwright

    page_html = to_html(SRC.read_text(encoding="utf-8"))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(page_html)
        page.pdf(path=str(OUT), format="A4", print_background=True)
        browser.close()
    pages = OUT.read_bytes().count(b"/Type /Page\n") or len(re.findall(rb"/Type\s*/Page[^s]", OUT.read_bytes()))
    print(f"Wrote {OUT.name}: {pages} page(s)")
    return 0 if pages == 1 else 1


if __name__ == "__main__":
    sys.exit(main())
