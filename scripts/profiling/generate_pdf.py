"""마크다운 → PDF 변환 스크립트 (한글 지원).

markdown + weasyprint를 사용해 스타일된 PDF를 생성합니다.

Usage:
    uv run --with markdown --with weasyprint --with Pygments \
           python scripts/profiling/generate_pdf.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import markdown

INPUT_MD = Path("outputs/project_analysis_for_pdf.md")
OUTPUT_PDF = Path("outputs/EMS_프로젝트_분석_가이드.pdf")
OUTPUT_HTML = Path("outputs/project_analysis_styled.html")

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700&family=Noto+Sans+Mono&display=swap');

@page {
    size: A4;
    margin: 2cm 1.8cm;
    @bottom-center {
        content: counter(page) " / " counter(pages);
        font-size: 9pt;
        color: #888;
        font-family: 'Noto Sans KR', sans-serif;
    }
    @top-right {
        content: "EMS 프로젝트 분석 가이드";
        font-size: 8pt;
        color: #aaa;
        font-family: 'Noto Sans KR', sans-serif;
    }
}

* {
    box-sizing: border-box;
}

body {
    font-family: 'Noto Sans KR', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
    font-size: 10.5pt;
    line-height: 1.7;
    color: #1a1a1a;
    max-width: 100%;
    padding: 0;
    margin: 0;
}

h1 {
    font-size: 22pt;
    font-weight: 700;
    color: #1565C0;
    border-bottom: 3px solid #1565C0;
    padding-bottom: 8px;
    margin-top: 0;
    margin-bottom: 16px;
    page-break-after: avoid;
}

h2 {
    font-size: 15pt;
    font-weight: 700;
    color: #1976D2;
    border-bottom: 2px solid #E3F2FD;
    padding-bottom: 5px;
    margin-top: 28px;
    margin-bottom: 12px;
    page-break-after: avoid;
}

h3 {
    font-size: 12pt;
    font-weight: 600;
    color: #2196F3;
    margin-top: 20px;
    margin-bottom: 8px;
    page-break-after: avoid;
}

h4 {
    font-size: 11pt;
    font-weight: 600;
    color: #42A5F5;
    margin-top: 16px;
    margin-bottom: 6px;
    page-break-after: avoid;
}

p {
    margin: 6px 0;
    orphans: 3;
    widows: 3;
}

blockquote {
    border-left: 4px solid #42A5F5;
    background: #E3F2FD;
    margin: 12px 0;
    padding: 10px 16px;
    color: #1565C0;
    font-size: 10pt;
    border-radius: 0 6px 6px 0;
}

blockquote > blockquote {
    border-left-color: #90CAF9;
    background: #BBDEFB;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin: 10px 0 16px 0;
    font-size: 9.5pt;
    page-break-inside: auto;
}

thead {
    display: table-header-group;
}

tr {
    page-break-inside: avoid;
    page-break-after: auto;
}

th {
    background: #1976D2;
    color: white;
    font-weight: 600;
    text-align: left;
    padding: 7px 10px;
    border: 1px solid #1565C0;
}

td {
    padding: 6px 10px;
    border: 1px solid #E0E0E0;
    vertical-align: top;
}

tr:nth-child(even) td {
    background: #F5F5F5;
}

tr:hover td {
    background: #E3F2FD;
}

code {
    font-family: 'Noto Sans Mono', 'SF Mono', 'Consolas', monospace;
    background: #F5F5F5;
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 9pt;
    color: #D32F2F;
    border: 1px solid #E0E0E0;
}

pre {
    background: #263238;
    color: #ECEFF1;
    padding: 14px 18px;
    border-radius: 8px;
    overflow-x: auto;
    font-size: 8.5pt;
    line-height: 1.5;
    margin: 10px 0;
    page-break-inside: avoid;
    white-space: pre-wrap;
    word-wrap: break-word;
}

pre code {
    background: none;
    color: #ECEFF1;
    padding: 0;
    border: none;
    font-size: 8.5pt;
}

hr {
    border: none;
    border-top: 2px solid #E0E0E0;
    margin: 24px 0;
}

strong {
    font-weight: 700;
    color: #1565C0;
}

em {
    color: #555;
}

ul, ol {
    margin: 6px 0;
    padding-left: 24px;
}

li {
    margin: 3px 0;
}

a {
    color: #1976D2;
    text-decoration: none;
}
"""


def main() -> None:
    if not INPUT_MD.exists():
        print(f"❌ 입력 파일이 없습니다: {INPUT_MD}")
        sys.exit(1)

    md_text = INPUT_MD.read_text(encoding="utf-8")

    # Markdown → HTML 변환
    extensions = ["tables", "fenced_code", "codehilite", "toc", "nl2br"]
    html_body = markdown.markdown(
        md_text,
        extensions=extensions,
        extension_configs={
            "codehilite": {"css_class": "highlight", "guess_lang": False},
        },
    )

    html_full = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>EMS 프로젝트 분석 가이드</title>
    <style>
{CSS}
    </style>
</head>
<body>
{html_body}
</body>
</html>"""

    # HTML 파일 저장 (디버그용)
    OUTPUT_HTML.write_text(html_full, encoding="utf-8")
    print(f"✅ HTML 저장: {OUTPUT_HTML}")

    # HTML → PDF 변환
    try:
        from weasyprint import HTML as WeasyHTML
        from weasyprint.text.fonts import FontConfiguration

        font_config = FontConfiguration()
        pdf_doc = WeasyHTML(string=html_full).write_pdf(font_config=font_config)

        OUTPUT_PDF.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PDF.write_bytes(pdf_doc)
        print(f"✅ PDF 저장: {OUTPUT_PDF}")
        print(f"   파일 크기: {OUTPUT_PDF.stat().st_size / 1024:.1f} KB")
    except ImportError:
        print("⚠️  weasyprint를 사용할 수 없습니다. HTML만 생성되었습니다.")
        print("   PDF 변환이 필요하면: pip install weasyprint")
    except Exception as e:
        print(f"⚠️  PDF 변환 실패: {e}")
        print("   HTML 파일은 정상 생성되었으니 브라우저에서 '인쇄 > PDF로 저장'으로 대체 가능합니다.")


if __name__ == "__main__":
    main()
