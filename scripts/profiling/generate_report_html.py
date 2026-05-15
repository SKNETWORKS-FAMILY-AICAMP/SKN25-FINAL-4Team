"""마크다운 발표자료를 스타일 적용된 standalone HTML로 변환"""
import base64
import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
MD_PATH = PROJECT / "docs" / "분석_기획" / "EMS_데이터_분석_발표자료.md"
CSS_PATH = PROJECT / "docs" / "분석_기획" / "report_style.css"
OUT_PATH = PROJECT / "docs" / "분석_기획" / "EMS_데이터_분석_보고서.html"


def embed_image(match):
    """이미지 경로를 base64 data URI로 변환"""
    alt = match.group(1)
    src = match.group(2)
    # 상대경로를 절대경로로
    if src.startswith("../../"):
        img_path = PROJECT / src.replace("../../", "")
    else:
        img_path = Path(src)
    if img_path.exists():
        data = base64.b64encode(img_path.read_bytes()).decode()
        return f'<figure><img src="data:image/png;base64,{data}" alt="{alt}" style="cursor: zoom-in;" onclick="openModal(this.src, this.alt)"><figcaption>{alt}</figcaption></figure>'
    return f'<p>[이미지 없음: {src}]</p>'


def md_to_html(md_text: str) -> str:
    """간이 마크다운→HTML 변환"""
    lines = md_text.split("\n")
    html_parts = []
    in_code = False
    in_table = False
    in_list = False
    table_rows = []
    list_items = []

    def flush_table():
        nonlocal table_rows, in_table
        if not table_rows:
            return ""
        out = "<table><thead><tr>"
        headers = [c.strip() for c in table_rows[0].strip("|").split("|")]
        for h in headers:
            out += f"<th>{h}</th>"
        out += "</tr></thead><tbody>"
        for row in table_rows[2:]:  # skip header + separator
            cols = [c.strip() for c in row.strip("|").split("|")]
            out += "<tr>" + "".join(f"<td>{c}</td>" for c in cols) + "</tr>"
        out += "</tbody></table>"
        table_rows = []
        in_table = False
        return out

    def flush_list():
        nonlocal list_items, in_list
        if not list_items:
            return ""
        out = "<ul>" + "".join(f"<li>{item}</li>" for item in list_items) + "</ul>"
        list_items = []
        in_list = False
        return out

    i = 0
    while i < len(lines):
        line = lines[i]

        # Code block
        if line.startswith("```"):
            if in_list:
                html_parts.append(flush_list())
            if in_table:
                html_parts.append(flush_table())
            if not in_code:
                lang = line[3:].strip()
                in_code = True
                html_parts.append(f'<pre><code class="{lang}">')
            else:
                in_code = False
                html_parts.append("</code></pre>")
            i += 1
            continue
        if in_code:
            html_parts.append(line.replace("<", "&lt;").replace(">", "&gt;") + "\n")
            i += 1
            continue

        # Empty line
        if not line.strip():
            if in_list:
                html_parts.append(flush_list())
            if in_table:
                html_parts.append(flush_table())
            i += 1
            continue

        # Table
        if "|" in line and line.strip().startswith("|"):
            if in_list:
                html_parts.append(flush_list())
            in_table = True
            table_rows.append(line)
            i += 1
            continue

        if in_table:
            html_parts.append(flush_table())

        # Headings
        if line.startswith("# "):
            if in_list:
                html_parts.append(flush_list())
            html_parts.append(f'<h1>{line[2:]}</h1>')
            i += 1
            continue
        if line.startswith("## "):
            if in_list:
                html_parts.append(flush_list())
            html_parts.append(f'<h2>{line[3:]}</h2>')
            i += 1
            continue
        if line.startswith("### "):
            if in_list:
                html_parts.append(flush_list())
            html_parts.append(f'<h3>{line[4:]}</h3>')
            i += 1
            continue

        # HR
        if line.strip() == "---":
            if in_list:
                html_parts.append(flush_list())
            html_parts.append("<hr>")
            i += 1
            continue

        # Image
        img_match = re.match(r'!\[([^\]]*)\]\(([^)]+)\)', line.strip())
        if img_match:
            if in_list:
                html_parts.append(flush_list())
            html_parts.append(embed_image(img_match))
            i += 1
            continue

        # Blockquote / callout
        if line.startswith("> "):
            if in_list:
                html_parts.append(flush_list())
            content = line[2:]
            css_class = "callout-info"
            if "⚠️" in content or "주의" in content or "WARNING" in content:
                css_class = "callout-warn"
            elif "📌" in content or "IMPORTANT" in content or "중요" in content:
                css_class = "callout-danger"
            html_parts.append(f'<div class="callout {css_class}">{inline_fmt(content)}</div>')
            i += 1
            continue

        # List
        if line.strip().startswith("- ") or line.strip().startswith("* "):
            in_list = True
            list_items.append(inline_fmt(line.strip()[2:]))
            i += 1
            continue

        # Paragraph
        if in_list:
            html_parts.append(flush_list())
        html_parts.append(f"<p>{inline_fmt(line)}</p>")
        i += 1

    if in_list:
        html_parts.append(flush_list())
    if in_table:
        html_parts.append(flush_table())

    return "\n".join(html_parts)


def inline_fmt(text: str) -> str:
    """인라인 서식 (bold, code, link)"""
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Inline code
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # Links
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    # Arrow
    text = text.replace("→", "→").replace("←", "←")
    return text


def build_html(body: str, css: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EMS 데이터 분석 보고서</title>
<style>
{css}

/* Image Modal */
.modal {{
  display: none;
  position: fixed;
  z-index: 1000;
  left: 0;
  top: 0;
  width: 100%;
  height: 100%;
  background-color: rgba(0,0,0,0.85);
}}
.modal-content {{
  margin: auto;
  display: block;
  max-width: 95%;
  max-height: 90%;
  position: absolute;
  top: 48%;
  left: 50%;
  transform: translate(-50%, -50%);
  border-radius: 4px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.5);
}}
.modal-close {{
  position: absolute;
  top: 15px;
  right: 35px;
  color: #f1f1f1;
  font-size: 40px;
  font-weight: bold;
  cursor: pointer;
}}
.modal-caption {{
  margin: auto;
  display: block;
  width: 100%;
  text-align: center;
  color: #ccc;
  padding: 10px 0;
  position: absolute;
  bottom: 2%;
  left: 50%;
  transform: translateX(-50%);
  font-size: 16px;
}}
@media print {{
  .modal {{ display: none !important; }}
  figure img {{ cursor: default !important; }}
}}
</style>
</head>
<body>

<div class="cover">
  <h1 style="border:none;">스마트 건물 에너지 데이터<br>분석 보고서</h1>
  <div class="subtitle">Honda R&D Europe 시설 6년 에너지 데이터 프로파일링 및 흐름 검증</div>
  <div class="meta">
    <strong>작성일</strong> 2025-05-15<br>
    <strong>작성자</strong> 이근행<br>
    <strong>데이터 출처</strong> Gruner et al., Scientific Data (2025)
  </div>
</div>

{body}

<!-- The Modal -->
<div id="imageModal" class="modal" onclick="closeModal()">
  <span class="modal-close">&times;</span>
  <img class="modal-content" id="img01">
  <div id="caption" class="modal-caption"></div>
</div>

<script>
function openModal(src, alt) {{
  document.getElementById("imageModal").style.display = "block";
  document.getElementById("img01").src = src;
  document.getElementById("caption").innerHTML = alt;
}}
function closeModal() {{
  document.getElementById("imageModal").style.display = "none";
}}
document.addEventListener('keydown', function(event) {{
  if (event.key === "Escape") {{
    closeModal();
  }}
}});
</script>

</body>
</html>"""


def main():
    md = MD_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")

    # 마크다운 최상단 제목/메타 라인 제거 (cover에서 이미 표시)
    lines = md.split("\n")
    start = 0
    for j, l in enumerate(lines):
        if l.startswith("## 목차"):
            start = j
            break
        if l.startswith("## 1."):
            start = j
            break
    md_body = "\n".join(lines[start:])

    body = md_to_html(md_body)
    html = build_html(body, css)

    OUT_PATH.write_text(html, encoding="utf-8")
    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"✅ HTML 보고서 생성 완료: {OUT_PATH}")
    print(f"   파일 크기: {size_kb:.0f} KB")
    print(f"\n   브라우저에서 열어 Cmd+P → PDF로 저장하세요!")


if __name__ == "__main__":
    main()
