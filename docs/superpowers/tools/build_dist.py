"""마크다운 문서를 배포용 단일 파일로 만든다.

  *.html         SVG를 문서 안에 직접 넣은 자립형 HTML
  *.pdf          위 HTML을 A4로 출력 (그림이 벡터로 들어간다)
  *.embedded.md  이미지를 base64로 박은 마크다운

사용법:

    python3 docs/superpowers/tools/build_dist.py                 # 기본 문서
    python3 docs/superpowers/tools/build_dist.py <문서.md>       # 지정 문서

이미지가 PNG로 참조돼 있고 같은 이름의 SVG가 옆에 있으면, HTML에는 SVG를
넣는다. 확대해도 깨지지 않고 파일도 훨씬 작다.
"""

from __future__ import annotations

import base64
import html as ht
import re
import sys
from pathlib import Path

from _common import SPECS, rel, run_chrome, warn_no_chrome

DEFAULT_DOC = SPECS / "2026-08-26-llm-autonomy-review.md"
IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def resolve_doc() -> Path:
    if len(sys.argv) > 1:
        doc = Path(sys.argv[1]).expanduser().resolve()
    else:
        doc = DEFAULT_DOC
    if not doc.is_file():
        sys.exit(f"문서를 찾을 수 없습니다: {doc}")
    return doc


DOC = resolve_doc()
BASE = DOC.parent
md = DOC.read_text(encoding="utf-8")


def image_path(link: str) -> Path | None:
    """마크다운 이미지 링크 → 실제 파일. 외부 URL이면 None."""
    if "://" in link or link.startswith("data:"):
        return None
    path = (BASE / link).resolve()
    return path if path.is_file() else None


# ── 1. base64 마크다운 ────────────────────────────────────────────
SUFFIX_MIME = {".png": "image/png", ".jpg": "image/jpeg",
               ".jpeg": "image/jpeg", ".gif": "image/gif", ".svg": "image/svg+xml"}


def to_data_uri(m: re.Match) -> str:
    path = image_path(m.group(2))
    if path is None:
        return m.group(0)
    mime = SUFFIX_MIME.get(path.suffix.lower(), "application/octet-stream")
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f"![{m.group(1)}](data:{mime};base64,{b64})"


out_md = DOC.with_suffix(".embedded.md")
out_md.write_text(IMG_RE.sub(to_data_uri, md), encoding="utf-8")


# ── 2. 자립형 HTML ────────────────────────────────────────────────
def inline(text: str) -> str:
    """인라인 마크업 → HTML."""
    text = ht.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*\n]+)\*", r"<em>\1</em>", text)
    text = re.sub(r"(?<!\!)\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def figure(link: str, alt: str) -> str:
    """PNG 링크 옆에 SVG가 있으면 그것을 통째로 넣는다."""
    path = image_path(link)
    if path is None:
        return f'<figure><img src="{ht.escape(link)}" alt="{ht.escape(alt)}"></figure>'
    svg = path.with_suffix(".svg")
    if svg.is_file():
        body = svg.read_text(encoding="utf-8")
        # 고정 크기를 지우고 컨테이너 폭에 맞춘다. viewBox가 비율을 유지한다.
        body = re.sub(r'\swidth="\d+"\sheight="\d+"', ' width="100%"', body, count=1)
        return f"<figure>{body}</figure>"
    mime = SUFFIX_MIME.get(path.suffix.lower(), "application/octet-stream")
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f'<figure><img src="data:{mime};base64,{b64}" alt="{ht.escape(alt)}"></figure>'


def render(md_text: str) -> str:
    out: list[str] = []
    lines = md_text.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]

        m = IMG_RE.fullmatch(ln.strip())
        if m:
            out.append(figure(m.group(2), m.group(1)))
            i += 1
            continue

        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1]):
            head = [c.strip() for c in ln.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            t = "<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head)
            t += "</tr></thead><tbody>"
            for row in rows:
                t += "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>"
            out.append(t + "</tbody></table>")
            continue

        m = re.match(r"^(#{1,4})\s+(.*)", ln)
        if m:
            lv = len(m.group(1))
            out.append(f"<h{lv}>{inline(m.group(2))}</h{lv}>")
            i += 1
            continue

        if re.match(r"^---+\s*$", ln):
            out.append("<hr>")
            i += 1
            continue

        if ln.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(ht.escape(lines[i], quote=False))
                i += 1
            i += 1
            out.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
            continue

        if ln.startswith("> "):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").rstrip())
                i += 1
            out.append("<blockquote>" + inline(" ".join(buf)) + "</blockquote>")
            continue

        if re.match(r"^(-|\d+\.)\s+", ln):
            tag = "ol" if re.match(r"^\d+\.", ln) else "ul"
            items = []
            while i < len(lines) and re.match(r"^(-|\d+\.)\s+", lines[i]):
                items.append(re.sub(r"^(-|\d+\.)\s+", "", lines[i]))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue

        if not ln.strip():
            i += 1
            continue

        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,4}\s|---+\s*$|\||>\s|-\s|\d+\.\s|```|!\[)", lines[i]):
            buf.append(lines[i])
            i += 1
        if buf:
            out.append("<p>" + "<br>".join(inline(b) for b in buf) + "</p>")
        else:
            i += 1
    return "\n".join(out)


CSS = """
:root{--ink:#1f2933;--muted:#5c6b7a;--line:#d7dee6;--accent:#2f5d9e}
*{box-sizing:border-box}
body{margin:0;padding:48px 24px 96px;background:#fff;color:var(--ink);
 font-family:'Noto Sans CJK KR','Noto Sans KR','Malgun Gothic',-apple-system,sans-serif;
 font-size:16px;line-height:1.75;-webkit-print-color-adjust:exact;print-color-adjust:exact}
main{max-width:940px;margin:0 auto}
h1{font-size:29px;line-height:1.35;margin:0 0 8px;padding-bottom:14px;border-bottom:3px solid var(--ink)}
h2{font-size:23px;margin:56px 0 16px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:18.5px;margin:36px 0 12px;color:var(--accent)}
h4{font-size:16px;margin:26px 0 10px}
p{margin:0 0 15px}
ul,ol{margin:0 0 16px;padding-left:26px}
li{margin-bottom:7px}
code{background:#f1f4f8;border:1px solid #e2e8f0;border-radius:4px;
 padding:1px 5px;font-size:.88em;font-family:'DejaVu Sans Mono',monospace}
pre{background:#f7f9fb;border:1px solid var(--line);border-radius:8px;
 padding:14px 16px;overflow-x:auto;margin:0 0 18px}
pre code{background:none;border:0;padding:0;font-size:13px;line-height:1.6}
strong{font-weight:700}
a{color:var(--accent)}
hr{border:0;border-top:1px solid var(--line);margin:38px 0}
blockquote{margin:20px 0;padding:14px 20px;background:#f7f9fb;
 border-left:4px solid var(--accent);color:#33414f}
blockquote p{margin:0}
table{border-collapse:collapse;width:100%;margin:18px 0 24px;font-size:14.5px}
th,td{border:1px solid var(--line);padding:9px 12px;text-align:left;vertical-align:top}
th{background:#f2f5f9;font-weight:700}
tbody tr:nth-child(even){background:#fafbfd}
figure{margin:30px 0;padding:14px;border:1px solid var(--line);border-radius:10px;background:#fff}
figure svg,figure img{display:block;width:100%;height:auto}
@media print{
 body{padding:0;font-size:11.5pt}
 main{max-width:none}
 h2,h3{page-break-after:avoid}
 figure,table,blockquote,pre{page-break-inside:avoid}
 @page{size:A4;margin:16mm 14mm}
}
"""

heading = re.search(r"^#\s+(.*)", md, re.M)
title = heading.group(1).strip() if heading else DOC.stem
out_html = DOC.with_suffix(".html")
out_html.write_text(
    '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    f"<title>{ht.escape(title)}</title><style>{CSS}</style></head>"
    f"<body><main>{render(md)}</main></body></html>",
    encoding="utf-8",
)

# ── 3. PDF ────────────────────────────────────────────────────────
out_pdf = DOC.with_suffix(".pdf")
out_pdf.unlink(missing_ok=True)     # 남아 있으면 Chrome이 덧쓰지 않는 경우가 있다
made_pdf = run_chrome(
    "--no-pdf-header-footer", f"--print-to-pdf={out_pdf}", out_html.as_uri()
)

for path in (out_html, out_pdf, out_md):
    if path.is_file():
        print(f"  {rel(path)}  ({path.stat().st_size / 1024:.0f} KB)")
if not made_pdf:
    warn_no_chrome("PDF")
