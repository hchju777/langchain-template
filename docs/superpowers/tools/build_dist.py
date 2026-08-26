"""메모를 배포용 단일 파일로 만든다.

  1) *.embedded.md  — 이미지를 base64로 박은 자립형 마크다운
  2) *.html         — SVG를 통째로 넣은 자립형 HTML (어떤 브라우저에서도 열림)
"""
import base64
import html as ht
import re
from pathlib import Path

ROOT = Path("/home/hchju777/langchain_ws/langchain-template")
SRC = ROOT / "docs/superpowers/specs/2026-08-26-llm-autonomy-review.md"
IMG = ROOT / "docs/images"
md = SRC.read_text(encoding="utf-8")

# ── 1. base64 마크다운 ────────────────────────────────────────────
def to_data_uri(m):
    alt, rel = m.group(1), m.group(2)
    png = (SRC.parent / rel).resolve()
    b64 = base64.b64encode(png.read_bytes()).decode()
    return f"![{alt}](data:image/png;base64,{b64})"

emb = re.sub(r"!\[([^\]]*)\]\((\.\./\.\./images/[^)]+)\)", to_data_uri, md)
out_md = SRC.with_suffix(".embedded.md")
out_md.write_text(emb, encoding="utf-8")

# ── 2. 자립형 HTML ────────────────────────────────────────────────
SVG_FOR = {
    "arch-current.png": "arch-current.svg",
    "arch-target.png": "arch-target.svg",
    "subgraph-process.png": "subgraph-process.svg",
}


def inline(text):
    """인라인 마크업 → HTML."""
    text = ht.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*\n]+)\*", r"<em>\1</em>", text)
    text = re.sub(r"(?<!\!)\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def render(md_text):
    out, i = [], 0
    lines = md_text.split("\n")
    while i < len(lines):
        ln = lines[i]

        m = re.match(r"^!\[([^\]]*)\]\(\.\./\.\./images/([^)]+)\)", ln)
        if m:
            svg = (IMG / SVG_FOR[m.group(2)]).read_text(encoding="utf-8")
            svg = re.sub(r'\swidth="\d+"\sheight="\d+"', ' width="100%"', svg, count=1)
            out.append(f'<figure>{svg}</figure>')
            i += 1
            continue

        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1]):
            head = [c.strip() for c in ln.strip("|").split("|")]
            i += 2
            body = []
            while i < len(lines) and lines[i].startswith("|"):
                body.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            t = "<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head)
            t += "</tr></thead><tbody>"
            for row in body:
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

        if ln.startswith("> "):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").rstrip())
                i += 1
            out.append("<blockquote>" + inline(" ".join(buf)) + "</blockquote>")
            continue

        if re.match(r"^(-|\d+\.)\s+", ln):
            ordered = bool(re.match(r"^\d+\.", ln))
            tag = "ol" if ordered else "ul"
            items = []
            while i < len(lines) and re.match(r"^(-|\d+\.)\s+", lines[i]):
                items.append(re.sub(r"^(-|\d+\.)\s+", "", lines[i]))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue

        if ln.strip() == "":
            i += 1
            continue

        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,4}\s|---+\s*$|\||>\s|-\s|\d+\.\s|!\[)", lines[i]):
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
 font-family:'Noto Sans CJK KR','Noto Sans KR',-apple-system,sans-serif;
 font-size:16px;line-height:1.75;-webkit-print-color-adjust:exact;print-color-adjust:exact}
main{max-width:940px;margin:0 auto}
h1{font-size:29px;line-height:1.35;margin:0 0 8px;padding-bottom:14px;border-bottom:3px solid var(--ink)}
h2{font-size:23px;margin:56px 0 16px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:18.5px;margin:36px 0 12px;color:var(--accent)}
p{margin:0 0 15px}
ul,ol{margin:0 0 16px;padding-left:26px}
li{margin-bottom:7px}
code{background:#f1f4f8;border:1px solid #e2e8f0;border-radius:4px;
 padding:1px 5px;font-size:.88em;font-family:'DejaVu Sans Mono',monospace}
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
figure svg{display:block;width:100%;height:auto}
@media print{
 body{padding:0;font-size:11.5pt}
 main{max-width:none}
 h2{page-break-after:avoid}
 figure,table,blockquote{page-break-inside:avoid}
 @page{size:A4;margin:16mm 14mm}
}
"""

body = render(md)
title = "LLM 자율성 확대 방향 기술 검토"
doc = (f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
       f'<meta name="viewport" content="width=device-width,initial-scale=1">'
       f'<title>{title}</title><style>{CSS}</style></head>'
       f'<body><main>{body}</main></body></html>')
out_html = SRC.with_suffix(".html")
out_html.write_text(doc, encoding="utf-8")

print(f"{out_md.name}  {out_md.stat().st_size/1024:.0f} KB")
print(f"{out_html.name}  {out_html.stat().st_size/1024:.0f} KB")
