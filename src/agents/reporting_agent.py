"""
Reporting Agent.
monthly_report 테이블 KPI를 집계하고 자연어 보고서를 생성한다.
monthly_report 없으면 load_range()로 실시간 집계해 사용.
PDF 저장: reportlab 기반 (generate_pdf() 호출).
"""

import os
import io
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from anomaly_agent import _REGIME_EVENTS, _GATEWAY_FAILURES

import psycopg2
from llm_client import chat as llm_chat
from dotenv import load_dotenv

load_dotenv()

DB_URL    = os.getenv("DATABASE_URL")
PDF_DIR   = Path(__file__).parent.parent.parent / "docs" / "reports"


# ── KPI 조회 / 실시간 집계 ────────────────────────────────────────

def _fetch_kpi(months: int = 3) -> list[dict]:
    """monthly_report 테이블에서 최근 N개월 KPI 조회."""
    try:
        conn = psycopg2.connect(DB_URL)
        cur  = conn.cursor()
        cur.execute("""
            SELECT period, total_consumption_kwh, self_sufficiency_pct,
                   avg_cop, anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh
            FROM monthly_report
            ORDER BY period DESC LIMIT %s;
        """, (months,))
        rows = cur.fetchall()
        conn.close()
        if rows:
            cols = ["period", "consumption", "self_sufficiency", "avg_cop",
                    "anomaly_count", "grid_dependency", "pv_kwh", "chp_kwh"]
            return [dict(zip(cols, r)) for r in rows]
    except Exception:
        pass
    return []


def _realtime_kpi(months: int = 3) -> list[dict]:
    """monthly_report가 없을 때 load_range로 실시간 집계."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from data.loader import load_range
        import pandas as pd
        from datetime import timedelta

        end   = datetime.now()
        start = end - timedelta(days=30 * months)
        df    = load_range(str(start.date()), str(end.date()))
        if df.empty:
            return []

        df["ts"]     = pd.to_datetime(df["ts"])
        df["period"] = df["ts"].dt.to_period("M").astype(str)

        result = []
        for period, g in df.groupby("period"):
            total    = (g["grid_P"].abs().sum() + g["pv_P"].abs().sum() + g["chp_P"].abs().sum()) / 1000
            pv_kwh   = g["pv_P"].abs().sum() / 1000
            chp_kwh  = g["chp_P"].abs().sum() / 1000
            local    = g["pv_P"].abs().sum() + g["chp_P"].abs().sum()
            demand   = g["grid_P"].abs().sum() + local
            ss_pct   = (local / demand * 100) if demand > 0 else 0
            grid_pct = (g["grid_P"].abs().sum() / demand * 100) if demand > 0 else 0
            avg_cop  = g["cop"].mean(skipna=True) if "cop" in g else None
            result.append({
                "period": str(period), "consumption": round(total, 1),
                "self_sufficiency": round(ss_pct, 1), "avg_cop": round(float(avg_cop), 2) if avg_cop else None,
                "anomaly_count": 0, "grid_dependency": round(grid_pct, 1),
                "pv_kwh": round(pv_kwh, 1), "chp_kwh": round(chp_kwh, 1),
            })
        return sorted(result, key=lambda x: x["period"], reverse=True)
    except Exception:
        return []


def _format_kpi_block(kpis: list[dict]) -> str:
    if not kpis:
        return "KPI 데이터 없음"
    lines = []
    for k in kpis:
        cop_str = f"COP {k['avg_cop']:.2f}" if k.get("avg_cop") else "COP N/A"
        lines.append(
            f"- {k['period']}: 소비 {k['consumption']:,.0f} kWh | "
            f"자급률 {k['self_sufficiency']:.1f}% | {cop_str} | "
            f"이상 {k['anomaly_count']}건 | PV {k['pv_kwh']:,.0f} kWh | CHP {k['chp_kwh']:,.0f} kWh"
        )
    return "\n".join(lines)


# ── PDF 생성 ─────────────────────────────────────────────────────

def generate_pdf(report_text: str, kpis: list[dict], period_label: str = "") -> bytes:
    """보고서 텍스트 + KPI를 PDF로 변환. bytes 반환."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # 한글 폰트: 시스템에 있으면 등록, 없으면 기본 폰트 사용
    _font = "Helvetica"
    for path in [
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ]:
        if Path(path).exists():
            try:
                pdfmetrics.registerFont(TTFont("KorFont", path))
                _font = "KorFont"
            except Exception:
                pass
            break

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Title"], fontName=_font, fontSize=16)
    body_style  = ParagraphStyle("body",  parent=styles["Normal"], fontName=_font, fontSize=10, leading=16)
    head_style  = ParagraphStyle("head",  parent=styles["Heading2"], fontName=_font, fontSize=12)

    story = []

    # 제목
    story.append(Paragraph("에너지 관리 보고서", title_style))
    story.append(Paragraph(f"Honda R&D Europe GmbH — {period_label or datetime.now().strftime('%Y-%m')}", body_style))
    story.append(Paragraph(f"생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}", body_style))
    story.append(Spacer(1, 6*mm))

    # KPI 테이블
    if kpis:
        story.append(Paragraph("KPI 요약", head_style))
        headers = ["기간", "소비(MWh)", "자급률(%)", "COP", "이상건수", "PV(MWh)", "CHP(MWh)"]
        tdata   = [headers]
        for k in kpis:
            tdata.append([
                k["period"],
                f"{k['consumption']/1000:.1f}",
                f"{k['self_sufficiency']:.1f}",
                f"{k['avg_cop']:.2f}" if k.get("avg_cop") else "-",
                str(k["anomaly_count"]),
                f"{k['pv_kwh']/1000:.1f}",
                f"{k['chp_kwh']/1000:.1f}",
            ])
        tbl = Table(tdata, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f6feb")),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTNAME",   (0, 0), (-1, -1), _font),
            ("FONTSIZE",   (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fa")]),
            ("GRID",       (0, 0), (-1, -1), 0.5, colors.HexColor("#30363d")),
            ("ALIGN",      (1, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 6*mm))

    # 보고서 본문 (줄바꿈 처리)
    story.append(Paragraph("AI 분석 보고서", head_style))
    for line in report_text.split("\n"):
        line = line.strip()
        if not line:
            story.append(Spacer(1, 3*mm))
        elif line.startswith("##") or line.startswith("**"):
            clean = line.lstrip("#").strip().strip("*")
            story.append(Paragraph(clean, head_style))
        else:
            story.append(Paragraph(line, body_style))

    doc.build(story)
    return buf.getvalue()


# ── 메인 실행 ────────────────────────────────────────────────────

def run(state: dict) -> dict:

    question = state.get("question", "")

    # KPI 조회 → 없으면 실시간 집계
    kpis = _fetch_kpi(months=6)
    source = "monthly_report"
    if not kpis:
        kpis   = _realtime_kpi(months=3)
        source = "실시간 집계"

    kpi_block = _format_kpi_block(kpis)

    # 대화 히스토리
    history_lines = []
    for m in (state.get("messages") or [])[-6:]:
        role = "사용자" if m.__class__.__name__ == "HumanMessage" else "AI"
        history_lines.append(f"{role}: {m.content}")
    history_block = ("\n## 이전 대화\n" + "\n".join(history_lines)) if history_lines else ""

    prompt = f"""당신은 에너지 관리 보고서 작성 전문가입니다.
시설: Honda R&D Europe GmbH, 독일 오펜바흐. 전력망: 독일 공공 전력망.
전력 용어: "계통 전력" 또는 "외부 계통 전력"만 사용 (한전·수전량 등 한국 용어 사용 금지).
{history_block}

## 시설 이벤트 참조
{_REGIME_EVENTS}
{_GATEWAY_FAILURES}

## 최근 KPI 데이터 (출처: {source})
{kpi_block}

## 사용자 요청
{question}

아래 형식으로 보고서를 작성하세요:
## 1. 핵심 요약
(3줄 이내)

## 2. KPI 분석
(소비량, 자급률, COP 추이 — 수치와 단위 명시)

## 3. 이상탐지 현황
(anomaly_count 기반 — 데이터 없으면 "별도 조회 필요" 명시)

## 4. 개선 권고사항
(구체적 수치 포함, 확실하지 않으면 추측 명시)"""

    report_text = llm_chat([{"role": "user", "content": prompt}], max_tokens=1500)

    # PDF 저장
    pdf_path = None
    try:
        PDF_DIR.mkdir(parents=True, exist_ok=True)
        period_label = kpis[0]["period"] if kpis else datetime.now().strftime("%Y-%m")
        pdf_bytes = generate_pdf(report_text, kpis, period_label)
        pdf_path  = PDF_DIR / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        pdf_path.write_bytes(pdf_bytes)
        print(f"[Reporting] PDF 저장: {pdf_path}")
    except Exception as e:
        print(f"[Reporting] PDF 저장 실패: {e}")

    return {
        **state,
        "report_result": report_text,
        "rag_answer":    report_text,
        "pdf_path":      str(pdf_path) if pdf_path else "",
    }


def langgraph_node(state: dict) -> dict:
    return run(state)
