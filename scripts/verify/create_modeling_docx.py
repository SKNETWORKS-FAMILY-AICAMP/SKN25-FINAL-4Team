from __future__ import annotations

import html
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import xml.etree.ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ET.register_namespace("w", W_NS)
W = f"{{{W_NS}}}"

ROOT = Path("/home/viowlet/Projects/SKN25-FINAL-4Team")
FINAL_DIR = ROOT / "reports/modeling_evaluation_docs/final"

TEMPLATES = {
    "ai_system_architecture": Path("/mnt/hgfs/Windows/[모델링 및 평가] AI 시스템 아키텍처 (멀티 에이전트 아키텍처)_양식.docx"),
    "multi_agent_test_report": Path("/mnt/hgfs/Windows/[모델링 및 평가] 멀티 에이전트 테스트 계획 및 결과 보고서_양식.docx"),
    "vector_graph_db_result": Path("/mnt/hgfs/Windows/[모델링 및 평가] 벡터DB_GraphDB 구축 결과서_양식.docx"),
}

OUTPUTS = {
    "ai_system_architecture": FINAL_DIR / "ai_system_architecture.docx",
    "multi_agent_test_report": FINAL_DIR / "multi_agent_test_report.docx",
    "vector_graph_db_result": FINAL_DIR / "vector_graph_db_result.docx",
}

@dataclass(frozen=True)
class Section:
    title: str
    paragraphs: tuple[str, ...] = ()
    table: tuple[tuple[str, ...], ...] | None = None


def e(tag: str, attrs: dict[str, str] | None = None, text: str | None = None) -> ET.Element:
    el = ET.Element(f"{W}{tag}")
    if attrs:
        for key, value in attrs.items():
            el.set(f"{W}{key}", value)
    if text is not None:
        el.text = text
    return el


def paragraph(
    text: str,
    style: str | None = None,
    bold: bool = False,
    size: int = 21,
    color: str = "111827",
    before: int = 0,
    after: int = 80,
) -> ET.Element:
    p = e("p")
    ppr = e("pPr")
    if style:
        ppr.append(e("pStyle", {"val": style}))
    ppr.append(e("spacing", {"before": str(before), "after": str(after), "line": "276", "lineRule": "auto"}))
    p.append(ppr)
    r = e("r")
    rpr = e("rPr")
    rpr.append(e("rFonts", {"ascii": "Malgun Gothic", "hAnsi": "Malgun Gothic", "eastAsia": "Malgun Gothic", "cs": "Malgun Gothic"}))
    if bold:
        rpr.append(e("b"))
    rpr.append(e("color", {"val": color}))
    rpr.append(e("sz", {"val": str(size)}))
    rpr.append(e("szCs", {"val": str(size)}))
    r.append(rpr)
    t = e("t")
    t.set(f"{{http://www.w3.org/XML/1998/namespace}}space", "preserve")
    t.text = text
    r.append(t)
    p.append(r)
    return p


def empty_para() -> ET.Element:
    return e("p")


def table(rows: tuple[tuple[str, ...], ...]) -> ET.Element:
    max_cols = max(len(r) for r in rows)
    tbl = e("tbl")
    tblpr = e("tblPr")
    tblpr.append(e("tblStyle", {"val": "TableGrid"}))
    tblpr.append(e("tblW", {"w": "5000", "type": "pct"}))
    tblpr.append(e("tblLayout", {"type": "autofit"}))
    cell_mar = e("tblCellMar")
    for side in ("top", "left", "bottom", "right"):
        cell_mar.append(e(side, {"w": "80", "type": "dxa"}))
    tblpr.append(cell_mar)
    borders = e("tblBorders")
    for name in ("top", "left", "bottom", "right", "insideH", "insideV"):
        borders.append(e(name, {"val": "single", "sz": "4", "space": "0", "color": "94A3B8"}))
    tblpr.append(borders)
    tblpr.append(e("tblLook", {"firstRow": "1", "lastRow": "0", "firstColumn": "0", "lastColumn": "0", "noHBand": "0", "noVBand": "1", "val": "04A0"}))
    tbl.append(tblpr)
    grid = e("tblGrid")
    for _ in range(max_cols):
        grid.append(e("gridCol", {"w": str(max(1200, 9000 // max_cols))}))
    tbl.append(grid)
    for row_i, row in enumerate(rows):
        tr = e("tr")
        for col_i in range(max_cols):
            text = row[col_i] if col_i < len(row) else ""
            tc = e("tc")
            tcpr = e("tcPr")
            tcpr.append(e("tcW", {"w": str(max(1200, 9000 // max_cols)), "type": "dxa"}))
            tcpr.append(e("vAlign", {"val": "center"}))
            if row_i == 0:
                tcpr.append(e("shd", {"val": "clear", "color": "auto", "fill": "E2E8F0"}))
            tc.append(tcpr)
            for idx, line in enumerate(str(text).split("\n")):
                tc.append(paragraph(line, bold=(row_i == 0), size=19, after=35, color="0F172A" if row_i == 0 else "111827"))
                if idx == 0 and "\n" not in str(text):
                    pass
            tr.append(tc)
        tbl.append(tr)
    return tbl


def build_elements(sections: list[Section]) -> list[ET.Element]:
    out: list[ET.Element] = []
    for i, sec in enumerate(sections):
        is_h1 = bool(re.match(r"^\d+\.\s", sec.title))
        style = "Heading1" if is_h1 else "Heading2"
        out.append(paragraph(sec.title, style=style, bold=True, size=28 if is_h1 else 24, color="0F172A", before=180 if is_h1 else 100, after=100))
        for para in sec.paragraphs:
            out.append(paragraph(para, size=21, color="111827", after=90))
        if sec.table:
            out.append(table(sec.table))
        out.append(empty_para())
    return out


def first_two_tables_xml(doc_xml: bytes) -> list[bytes]:
    root = ET.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        raise ValueError("missing body")
    tables = [child for child in body if child.tag == f"{W}tbl"][:2]
    return [ET.tostring(t, encoding="utf-8") for t in tables]


def replace_after_second_table(src: Path, dst: Path, sections: list[Section]) -> None:
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    with ZipFile(src, "r") as zin:
        doc_xml = zin.read("word/document.xml")
        members = {item.filename: zin.read(item.filename) for item in zin.infolist()}
    root = ET.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        raise ValueError("missing body")
    children = list(body)
    tbl_positions = [idx for idx, child in enumerate(children) if child.tag == f"{W}tbl"]
    if len(tbl_positions) < 2:
        raise ValueError(f"{src} has fewer than two tables")
    preserve_until = tbl_positions[1]
    sect_pr = children[-1] if children and children[-1].tag == f"{W}sectPr" else None
    preserved = children[: preserve_until + 1]
    new_children = preserved + build_elements(sections)
    if sect_pr is not None:
        new_children.append(sect_pr)
    body[:] = new_children
    members["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with ZipFile(dst, "w", ZIP_DEFLATED) as zout:
        for name, data in members.items():
            zout.writestr(name, data)


def doc_text(path: Path) -> str:
    with ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    return "\n".join((node.text or "") for node in root.iter(f"{W}t"))


def validate_pair(src: Path, dst: Path) -> list[str]:
    issues: list[str] = []
    with ZipFile(src) as z:
        src_xml = z.read("word/document.xml")
    with ZipFile(dst) as z:
        dst_xml = z.read("word/document.xml")
    ET.fromstring(dst_xml)
    src_tables = first_two_tables_xml(src_xml)
    dst_tables = first_two_tables_xml(dst_xml)
    if src_tables != dst_tables:
        issues.append("first_two_tables_changed")
    text = doc_text(dst)
    forbidden_exact = ["EMS", "ems.", "RAGAS 0.89", "Fine-tuned", "Llama", "ChromaDB", "Cypher", "전체 pytest pass", "production 완료"]
    for token in forbidden_exact:
        if token in text:
            issues.append(f"forbidden_token:{token}")
    if re.search(r"[\U0001F300-\U0001FAFF]", text):
        issues.append("emoji_found")
    return issues


AI_SECTIONS = [
    Section("1. 에이전트 설계 개요", ("이 문서의 범위는 CMS 데이터 인사이트와 실시간·재처리 골격 프로젝트의 멀티 에이전트형 AI 시스템 아키텍처다. CMS는 Honda R&D Europe 에너지 관리 실측 데이터, 프로젝트 기준 문서, 품질검증 근거, 온톨로지 기반 맥락을 결합해 읽기 전용 근거 조회, 보고서 검토, 재처리 계획 수립, 승인 검토를 지원한다.",)),
    Section("1.1 서비스 개요 및 목적", ("CMS 프로젝트의 목적은 실시간·재처리 계측 이벤트를 안전하게 처리하고, MongoDB 원천 버퍼와 PostgreSQL 적재·후보·확정 영역을 분리하며, 품질검증 근거를 바탕으로 승인 가능한 후보 데이터를 관리하는 것이다. 현재 범위는 가져오기 안전성이 보장된 골격, 처리 계약, 예행 실행 검증 관문, 지식·온톨로지 기반 응답 구조다.",)),
    Section("1.2 주요 사용자 및 핵심 기능", table=(("사용자", "핵심 기능"), ("시설·에너지 운영 담당자", "계량기 상태, 관측 커버리지, 누락 관측치, 품질검증 근거 확인"), ("데이터 품질검증 담당자", "비어 있는 시간 버킷, 보정·참조 데이터 유입 여부, 이력 추적, 확정 반영 차단 규칙 검토"), ("분석·모델링 담당자", "확정 데이터 또는 명시적 미리보기 원천 기반의 특성·모델 예행 실행 준비"), ("운영 승인권자", "후보 데이터와 확정 반영 점검 결과를 검토하고 통제된 확정 반영 승인"))),
    Section("1.3 에이전트 시스템 설계 목표", ("FastAPI는 빠른 상태 응답, 읽기 전용 조회, 경량 대화, 수동 작업 등록, 산출물 내려받기를 담당한다. Airflow, 스케줄러, 백그라운드 작업자는 일괄 처리, 재처리, 정기 보고서를 담당한다. LangGraph는 일반 대화 경로가 아니라 품질검증 검토, 보고서 검토, 재처리 계획, 승인 검토를 위한 선택형 비동기 검토 계층이다.",)),
    Section("2. 멀티 에이전트 아키텍처", table=(("구성 요소", "역할", "근거"), ("서비스 라우터 / 대화 에이전트", "FastAPI 경로에서 빠른 상태 응답, 읽기 전용 조회, 작업 등록으로 분기", "src/cms/service/api.py"), ("조회 계획 에이전트", "제한된 시간 범위와 확정 테이블 허용 목록에 따라 매개변수화된 조회 계획 생성", "src/cms/service/query_planner.py"), ("검색·근거화 에이전트", "Vector DB 대상 기준 문서와 온톨로지 원천을 이용한 답변 근거화", "docs/specs/knowledge_db_contract.md"), ("품질검증 검토 에이전트", "품질검증 근거 묶음을 통과·주의·차단 관점으로 검토", "src/cms/workflow/langgraph_skeleton.py"), ("승인 검토 에이전트", "후보 데이터와 확정 반영 점검 결과를 검토하되 직접 확정 반영은 실행하지 않음", "docs/specs/data_platform_contract.md"))),
    Section("2.1 에이전트 아키텍처", ("아키텍처는 데이터 영역, 서비스 영역, 작업흐름 영역으로 분리된다. 데이터 영역은 원천 보관소, MongoDB 원천 버퍼, PostgreSQL 적재·후보·확정 영역, 품질검증 근거를 포함한다. 서비스 영역은 FastAPI와 읽기 전용 조회를 포함한다. 작업흐름 영역은 Airflow, 스케줄러, 보고서 작업자, 선택형 LangGraph 검토 흐름을 포함한다.",)),
    Section("2.2 구성 및 역할 정의", table=(("영역", "포함", "금지"), ("데이터 영역", "원천, MongoDB 원천 버퍼, PostgreSQL 적재·후보·확정 영역, 품질검증 근거", "대화 경로에서 직접 확정 반영"), ("서비스 영역", "FastAPI, 대시보드, 자연어-SQL 변환, 산출물·상태 응답", "대량 ETL 또는 장시간 일괄 처리 직접 실행"), ("작업흐름 영역", "Airflow, 스케줄러, 재처리 작업자, 보고서 작업자, LangGraph 검토", "일반 대화 응답 경로 대체"))),
    Section("2.3 오케스트레이션 및 상태 관리", ("LangGraph 검토 흐름은 품질검증 근거 묶음, 보고서 초안, 재처리 요청, 승인 요청을 입력으로 받아 검토 메모와 권고안을 생성한다. 이 흐름은 데이터베이스 쓰기, 확정 데이터 반영, 배포, 이메일 발송을 직접 실행하지 않는다.",)),
    Section("2.4 기술 구성 및 연동 구조", table=(("영역", "구성"), ("응용 서비스", "가져오기 안전성이 보장된 FastAPI 골격"), ("작업흐름", "기본 비활성 Airflow 골격, LangGraph 검토 골격"), ("데이터 저장소", "PostgreSQL cms와 MongoDB cms 처리 계약"), ("지식 기반", "Vector DB 적재 계약, RDF/OWL 온톨로지, Graphify 기준 문서 탐색 그래프"))),
    Section("3. 작업흐름", ("사용자 또는 스케줄러 요청은 FastAPI에서 빠른 응답, 읽기 전용 조회, 작업 등록으로 분기된다. 재처리·일괄 처리·보고서는 Airflow 또는 백그라운드 작업자가 처리한다. 계측 처리 작업자는 원천 이벤트를 MongoDB 버퍼와 PostgreSQL 적재 영역으로 전달하고, 품질검증 관문은 관측 커버리지, 비어 있는 값, 이력 추적, 보정·참조 데이터 분리를 검토한다. 승인 이후에만 통제된 확정 반영 절차가 확정 테이블을 갱신할 수 있다.",)),
    Section("4. 시스템 장애 대응 및 신뢰성 설계", table=(("위험", "대응"), ("누락 관측치", "관측값 영역에서 비어 있는 값과 커버리지 비율 0 유지"), ("보정·참조 데이터 유입", "reference.corrected_resampled_*를 관측 기반 확정 사실로 사용 금지"), ("안전하지 않은 SQL", "읽기 전용 SELECT, 확정 테이블 허용 목록, 제한된 시간 범위"), ("확정 데이터 오염", "승인과 통제된 확정 반영 전 쓰기 금지"), ("작업흐름 지연", "FastAPI와 장시간 실행 작업자 분리"))),
    Section("5. 평가 및 테스트 전략", ("평가는 골격 계약 검증, 단위 테스트 목록, 예행 실행 경로 검증, 실험용 데이터베이스 보호 장치, 조회 계획 안전성, LangGraph 검토 경계 중심으로 수행한다. 현재 확인된 기준 검증 명령은 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py`이며 결과는 `cms skeleton contracts ok`이다.",)),
    Section("6. 결론 및 향후 개선 방향", ("CMS 아키텍처는 원천 기반 검색, 읽기 전용 서비스 경계, 백그라운드 작업흐름, 품질검증 근거, 승인 기반 확정 반영을 분리해 안전한 분석 기반을 제공한다. 제출 전 전체 pytest, 온톨로지 검증, 실제 Vector DB 적재 명세, 실행 화면 캡처를 추가하면 근거 수준을 높일 수 있다.",)),
]

TEST_SECTIONS = [
    Section("1. 테스트 개요 및 환경 설정", ("이 문서의 범위는 CMS 멀티 에이전트 골격의 검증 계획과 확인 기준이다. 검증 기준은 가져오기 안전성, 부작용 차단, 근거 기반 처리 계약 준수 여부다.",)),
    Section("1.1 테스트 목적 및 범위 정의", ("범위는 FastAPI 예행 실행 경로, 읽기 전용 조회 계획기, LangGraph 검토 골격, 비활성 Airflow 골격, 실시간 등간격 처리기, 시각 품질검증, 실험용 데이터베이스 보호 장치, 마이그레이션 계약, 온톨로지·지식 계약이다. 운영 데이터베이스 쓰기나 확정 데이터 반영 실행은 테스트 범위가 아니다.",)),
    Section("1.2 기술 구성 및 테스트 환경", table=(("구분", "내용"), ("언어", "Python"), ("API", "FastAPI 골격"), ("작업흐름", "Airflow 골격, LangGraph 검토 골격"), ("데이터베이스 계약", "PostgreSQL cms, MongoDB cms"), ("검증 명령", "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/verify/verify_skeleton_contracts.py"))),
    Section("1.3 에이전트 구성 개요", table=(("구성", "검증 초점"), ("서비스 라우터", "예행 실행 입력값과 부작용 차단 경계"), ("조회 계획기", "확정 테이블 허용 목록과 읽기 전용 SQL"), ("품질검증 검토", "통과·주의·차단 권고"), ("보고서 작업자", "근거 묶음과 산출물 인계"), ("승인 검토", "부작용 발생 전 중단"))),
    Section("2. 구성요소 연동 및 단위 기능 테스트", table=(("영역", "테스트 파일", "검증 내용"), ("데이터 처리", "tests/data/test_live_equalization_processor.py", "비어 있는 시간 버킷, 관측 커버리지, 목표 시간 단위"), ("시각 처리", "tests/data/test_timestamp_*.py", "UTC 정규화, 중복, 정책 충돌, 누락 경고"), ("실험용 DB 보호 장치", "tests/data/test_db_scratch_guard.py", "기본 차단, 안전한 실행 식별자, 정확한 실험 대상"), ("서비스", "tests/service/test_query_planner.py", "읽기 전용 SQL 계획과 위험 조회 차단"), ("작업흐름", "tests/workflow/test_langgraph_review.py", "검토 경로 지정과 승인 전 부작용 중단"))),
    Section("2.1 데이터베이스 및 저장소 연동 검증", ("데이터베이스 검증은 원천 수준 처리 계약과 로컬 실험용 테스트 목록 중심이다. 실제 운영 데이터베이스 쓰기 또는 확정 데이터 반영은 수행하지 않는다. 실험용 쓰기는 `ALLOW_DB_SCRATCH_WRITE=1`, 실행 시점 `allow_write=True`, 안전한 실행 식별자, 정확한 대상 스키마·컬렉션 조건을 만족해야 한다.",)),
    Section("2.2 검색 증강 생성 성능 및 검색 정확도 테스트", ("현재 확인 범위는 Vector DB 적재 계약과 검색 경로 지정 정책이다. 실제 임베딩 모델, 조각 수, 유사도 점수, RAGAS 점수는 확인되지 않았으므로 성능 수치로 기재하지 않는다. 제출 전 실제 적재 명세와 표본 검색 결과를 확보해야 한다.",)),
    Section("2.3 외부 도구 및 workflow 연동 테스트", ("FastAPI와 LangGraph 골격은 부작용 차단 경계를 유지한다. 이메일 경로는 예행 실행 계약으로만 검증하며 실제 이메일 발송 주장은 포함하지 않는다. Airflow 골격은 기본 비활성 상태이므로 실제 스케줄러 화면 구동 주장은 별도 근거가 필요하다.",)),
    Section("3. 자체 소형 언어모델 미세조정 평가 계획", ("저장소 근거에서 소형 언어모델 미세조정 데이터셋, 학습 로그, SQL 정확도, LLM 평가 점수는 확인되지 않는다. 이 절은 수행 결과가 아니라 향후 평가 계획으로 정의한다. 향후에는 스키마 기반 SQL 생성, 보고서 문체 정합성, 품질검증 검토의 보수성을 별도 기준으로 평가한다.",)),
    Section("4. 시나리오 기반 멀티 에이전트 통합 테스트", ("대표 시나리오는 사용자의 근거 조회가 FastAPI 경로로 들어오면 조회 계획기가 읽기 전용 SQL 계획을 만들고, 품질검증 근거와 Vector DB·온톨로지 맥락을 결합해 근거 기반 응답을 생성하는 흐름이다. 재처리·보고서·승인은 작업 등록 후 작업흐름 영역에서 처리된다.",)),
    Section("5. 종합 실행 결과 및 개선 내용", table=(("구분", "현재 확인 결과", "비고"), ("골격 계약", "통과: cms skeleton contracts ok", "exit code 0"), ("테스트 목록", "테스트 파일 20개, 원천 수준 검색 기준 테스트 함수 107개", "전체 pytest 통과 주장 아님"), ("온톨로지 검증", "제출 전 재검증 필요", "rdflib 환경 필요"), ("Vector DB/GraphDB 실행 환경", "미확인", "적재 명세와 조회 근거 필요"))),
    Section("5.1 발견된 결함 및 개선 조치", table=(("이슈", "원인", "조치 방향"), ("미확인 성능 수치", "실제 Vector DB/GraphDB 실행 근거 없음", "성능 수치 삭제 또는 향후 계획으로 표기"), ("운영 완료 주장 위험", "예행 실행·로컬 근거·원천 목록과 운영 근거를 혼동할 가능성", "근거 수준 분리"), ("LangGraph 배치 오해", "일반 대화 경로로 오인 가능", "선택형 비동기 검토 계층으로 명시"))),
]

VDB_SECTIONS = [
    Section("1. 구축 목적 및 적용 범위", ("이 문서의 범위는 CMS 프로젝트의 Vector DB, Graphify, RDF/OWL 온톨로지 기반 지식 근거화 구조다. Vector DB는 기준 문서 의미 검색을 위한 적재 계약이고, Graphify는 기준 문서 중심 탐색 그래프이며, RDF/OWL 온톨로지는 계량기, 설비, 계측값, 중복 계량기, 원천 출처 관계를 표현하는 정형 지식 산출물이다.",)),
    Section("1.1 문제 정의 및 구축 범위", ("CMS 프로젝트는 계량기 관측 데이터, 계측 용어, 품질검증 정책, 원천 출처, 실행 경계가 여러 문서에 분산되어 있다. 지식 근거화 계층은 LLM과 에이전트가 근거 없는 응답을 생성하지 않도록 원천 등급, 문서 조각 메타데이터, 온톨로지 관계를 제공한다.",)),
    Section("1.2 데이터 저장 목적 및 활용 구분", table=(("데이터 유형", "저장소/산출물", "활용 목적"), ("0등급 원천", "Nature 논문, Honda RI PDF, Dryad 데이터셋", "원천 출처와 원문 사실 확인"), ("1등급 기준 문서", "Vector DB 적재 대상", "프로젝트 규칙 근거화"), ("온톨로지", "docs/ontology/cms.ttl, cms_shapes.ttl, cms_protege.owl", "계량기·설비·계측값 관계"), ("Graphify", "graphify-out/graph.json", "기준 문서 탐색 그래프"), ("실행 데이터", "PostgreSQL/MongoDB", "계측 처리 데이터이며 Vector DB 대상 아님"))),
    Section("2. 벡터DB/GraphDB 기술 설계 명세", table=(("구분", "현재 설계"), ("Vector DB 대상", "기준 명세, 품질검증 문서, 참고 문서"), ("문서 조각화", "제목 단위 중심, 원천 경로와 제목 메타데이터 포함"), ("제외 대상", "비밀값, 인증정보, 로컬 실험 파일, 생성 캐시, 벤치마크·모델 실험"), ("Graphify", "기준 문서 전용 그래프이며 원천 사실 자체는 아님"), ("RDF/OWL", "CMS 네임스페이스 온톨로지와 SHACL 제약"))),
    Section("2.1 벡터 데이터베이스 설계", ("Vector DB는 `docs/specs/data_platform_contract.md`, `runtime_architecture.md`, `measurement_processing_policy.md`, `meter_metadata.md`, `ontology_schema.md`, `knowledge_db_contract.md`, `llm_contract.md`, `docs/qa/qa_contract.md`, `source_inventory.md`, `measurement_glossary.md`를 주요 적재 대상으로 삼는다. 실제 임베딩 모델과 색인 성능은 아직 별도 근거가 필요하다.",)),
    Section("2.2 그래프 데이터베이스 설계", ("온톨로지 클래스는 계량기, 전기 계량기, 열 계량기, 기상 계량기, 설비 그룹, 건물, 계량기 역할, 하드웨어 모델, 중복 계량기 쌍, 메타데이터 문서 등을 포함한다. 주요 관계는 그룹 소속, 건물 위치, 역할 보유, 하드웨어 모델 보유, 중복 관계, 문서 정의 관계 등이다.",)),
    Section("2.3 비정형 데이터 및 메모리 관리 구조", ("문서 검색은 원천 등급과 메타데이터를 기준으로 수행한다. LLM은 기준 기록 체계가 아니며, 사실 주장은 검색된 원천 맥락, 데이터베이스·근거 조회 결과, 정책 제약으로 추적 가능해야 한다.",)),
    Section("2. 검색 성능 및 정확도 검증 결과", ("현재 검증 범위는 로컬 산출물과 처리 계약 점검 중심이다. Graphify 명세 기준 노드 264개, 연결 239개가 확인되었고, 누락 원천 파일과 기준 문서 범위 밖 파일 목록은 빈 배열로 기록되어 있다. 실제 Vector DB 유사도 점수나 GraphDB 조회 지연 시간은 미확인이다.",)),
    Section("2.1 성능 및 정확도 지표 검증", table=(("평가 항목", "현재 근거", "상태"), ("Graphify 그래프", "노드 264개, 연결 239개", "확인됨"), ("온톨로지 산출물", "cms.ttl, cms_shapes.ttl, cms_protege.owl 존재", "확인됨"), ("계량기 등록 목록", "계량기 URN 81개, 중복 계량기 쌍 12개", "확인됨"), ("Vector DB 문서 조각 수", "실제 적재 명세 없음", "미확인"), ("GraphDB 지연 시간", "접속 지점·조회 로그 없음", "미확인"))),
    Section("2.2 검색 실패 시 대응 전략", ("검색 실패나 원천 부족 시 기본 LLM 지식으로 단정하지 않고 정보 부족 또는 재검증 필요로 응답한다. 보정·참조 데이터, 생성 캐시, 로컬 실험 산출물은 명시 표기 없는 서비스 기준 사실로 사용하지 않는다.",)),
    Section("3. 데이터 운영 흐름 및 생명주기", table=(("단계", "처리"), ("적재", "기준 문서를 제목 단위로 조각화하고 메타데이터 부여"), ("검색", "질문 유형별 검색 경로 지정"), ("검토", "원천 등급과 근거 상태 확인"), ("갱신", "검증된 기준 문서 변경 시 재적재"), ("삭제/제외", "비밀값, 캐시, 실험 파일, 비기준 산출물 제외"))),
    Section("4. 구축 결과", ("구축 결과는 기준 문서 목록, 온톨로지 산출물, Graphify 기준 문서 그래프, 검증 명령 근거로 정리한다. 실제 Vector DB 행 수나 GraphDB 서버 조회 결과는 제출 전 추가 확보 대상이다.",)),
    Section("적재 결과 요약", table=(("항목", "확인 결과"), ("Vector DB 대상 문서", "기준 문서 10개 목록 정의"), ("Graphify", "graph.json 노드 264개, 연결 239개"), ("온톨로지", "클래스 18개, 객체 속성 15개, 데이터 속성 19개 휴리스틱 확인"), ("계량기 메타데이터", "계량기 81개, 전기 71개, 열 9개, 기상 1개, 중복 계량기 쌍 12개"), ("검증 명령", "cms skeleton contracts ok"))),
    Section("결론 및 향후 개선 방향", ("CMS 지식 근거화 구조는 Vector DB 계약, Graphify 탐색 그래프, RDF/OWL 온톨로지를 분리해 원천 기반 응답을 지원한다. 향후 실제 Vector DB 적재 명세, 표본 검색 결과, SPARQL·GraphDB 조회 결과, 온톨로지 검증 로그를 추가해 구축 근거를 보강해야 한다.",)),
]


def main() -> None:
    replace_after_second_table(TEMPLATES["ai_system_architecture"], OUTPUTS["ai_system_architecture"], AI_SECTIONS)
    replace_after_second_table(TEMPLATES["multi_agent_test_report"], OUTPUTS["multi_agent_test_report"], TEST_SECTIONS)
    replace_after_second_table(TEMPLATES["vector_graph_db_result"], OUTPUTS["vector_graph_db_result"], VDB_SECTIONS)

    all_issues: dict[str, list[str]] = {}
    for key, out in OUTPUTS.items():
        issues = validate_pair(TEMPLATES[key], out)
        all_issues[out.name] = issues

    handoff = [
        "# Modeling Evaluation DOCX Handoff",
        "",
        "## 생성 파일",
    ]
    for out in OUTPUTS.values():
        handoff.append(f"- `{out}`")
    handoff.extend([
        "",
        "## 편집 방식",
        "- Python 표준 라이브러리 `zipfile`/`xml.etree.ElementTree`로 원본 DOCX를 복사한 뒤 본문 XML을 수정했습니다.",
        "- 각 문서 본문의 첫 두 `<w:tbl>` 요소와 그 앞 표지·기본정보 영역은 보존했습니다.",
        "- 첫 두 표 뒤 본문을 CMS 프로젝트 제출용 내용으로 교체했습니다.",
        "- 본문 다이어그램은 `scripts/verify/add_modeling_docx_diagrams.py`에서 기존 `docs/specs/diagrams/` SVG를 DOCX 삽입용 PNG로 렌더링해 추가합니다.",
        "",
        "## 첫 페이지 보존 검증",
    ])
    for name, issues in all_issues.items():
        first_ok = "first_two_tables_changed" not in issues
        parse_ok = True
        handoff.append(f"- `{name}`: first two tables identical = {first_ok}, zip/xml parse = {parse_ok}, issues = {issues or 'none'}")
    handoff.extend([
        "",
        "## 남은 미확인 항목",
        "- 전체 pytest 통과 결과는 아직 확인되지 않았습니다.",
        "- 실제 Vector DB 적재 명세, 임베딩 모델, 문서 조각 수, 유사도 점수는 아직 확인되지 않았습니다.",
        "- 실제 GraphDB 접속 지점, 조회 결과, 지연 시간은 아직 확인되지 않았습니다.",
        "- 온톨로지 검증과 SPARQL 기본 조회는 제출 전 재검증이 필요합니다.",
        "- FastAPI 실행 화면 캡처와 Graphify 브라우저 캡처는 별도 확보가 필요합니다.",
        "",
        "## 검수 포인트",
        "- 첫 페이지 표지·기본정보 디자인이 원본과 동일한지 Word에서 육안 확인해야 합니다.",
        "- 본문 표와 문단이 제출 양식에서 과도하게 깨지지 않았는지 확인해야 합니다.",
        "- 미확인 성능 수치가 사실 주장처럼 남아 있지 않은지 확인해야 합니다.",
        "- LangGraph가 일반 대화 경로로 표현되지 않았는지 확인해야 합니다.",
        "- Vector DB, Graphify, RDF/OWL 온톨로지 용어가 혼동되지 않았는지 확인해야 합니다.",
    ])
    (FINAL_DIR / "handoff.md").write_text("\n".join(handoff) + "\n", encoding="utf-8")

    for name, issues in all_issues.items():
        print(f"{name}: {issues or 'ok'}")


if __name__ == "__main__":
    main()
