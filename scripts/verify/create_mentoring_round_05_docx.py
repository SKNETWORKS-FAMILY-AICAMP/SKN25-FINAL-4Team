from __future__ import annotations

import re
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import xml.etree.ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ET.register_namespace("w", W_NS)
W = f"{{{W_NS}}}"

ROOT = Path("/home/viowlet/Projects/SKN25-FINAL-4Team")
WORK = ROOT / "reports/mentoring/drive_work"
TEMPLATE = WORK / "mentor_round_00_template.docx"
OUTPUT = WORK / "멘토링 수행일지 수강생용_ 윤치영멘토_4조_5회차.docx"

ROWS = {
    2: ["날짜", "2026 년 06 월 02 일 ( 5 차)"],
    3: ["시간", "11 시 36 분 ~ 13 시 09 분 ( 1시간 33분)"],
    4: ["참여자", "이근혁, 전운열, 신문수, 여해준, 최원준"],
    5: ["멘토명", "윤치영", "멘티명", "이근혁, 전운열, 신문수, 여해준, 최원준"],
    6: ["프로젝트 주제", "대화형 데이터 분석 에이전트 및 자동 리포팅 플랫폼"],
    8: [
        "열검침 계량기 데이터 분석에서 비지도 이상 탐지의 검증 한계를 점검하고, 라벨 기반 평가 대신 통계 기준과 도메인 규칙을 결합한 이상 후보 정의 방식으로 전환한다. LSTM 학습 실패 가능성, 윈도우 크기와 평가 지표, 계량기별 전처리 규칙, 실시간 데이터 흐름, 자연어 기반 에너지 관리 서비스 방향을 함께 정리한다."
    ],
    10: [
        "계량기별 이상 후보 기준과 전처리 규칙을 구체화하고, WAPE·MAE·RMSE를 함께 활용한 윈도우 크기 비교 실험을 수행한다.\nLSTM 학습이 실제로 진행되었는지 손실 추세와 기준 모델 비교로 검증하고, LightGBM 등 경쟁 모델과 비교할 기준을 정리한다.\n공장 에너지 관리와 설비 모니터링을 결합한 스마트 에너지 플랫폼의 차별화 요소, 정부 지원 사업 활용 방안, 자연어 챗봇 기반 보고서 생성 시나리오를 발표 자료에 반영한다.\n소형 언어모델 후보, 프롬프트 자동 개선, 서브 에이전트 운용 규칙, API 토큰 및 컴퓨팅 자원 사용량 관리 기준을 검토한다."
    ],
    12: [
        "1. 비지도 이상 탐지 검증 방향 전환\n라벨이 없는 계량기 데이터에서는 모델이 탐지한 피크를 실제 이상으로 단정하기 어렵다는 점을 확인하였다. 피크 발생 여부만으로 정상 운영 변화와 이상 상황을 구분하기 어렵고, 사람이 부여한 라벨도 일관된 정답으로 보기 어렵다. 따라서 라벨 기반 성능 평가를 중심에 두기보다 통계 기준값, 계량기별 패턴, 결측·중복 여부, 도메인 규칙을 결합해 이상 후보를 정의하는 방식으로 전환하기로 하였다. 구매 데이터와 맞지 않는 값도 단순 오류로 버리기보다 데이터 이상치로 별도 표식화하여 관리하는 방향을 검토하였다.\n\n2. LSTM 학습 검증과 기준 모델 비교\nLSTM은 학습이 실패해도 마지막 입력값과 유사한 값을 반복 출력하여 예측이 잘 되는 것처럼 보일 수 있으므로, 조기 종료 결과만으로 학습 성공을 판단하지 않기로 하였다. 학습 손실과 검증 손실이 실제로 감소하는지 확인하고, 단순 반복 예측이나 이동평균 같은 기준 모델과 비교하여 학습 효과를 검증해야 한다. LightGBM 계열 모델은 지연 특성을 명시적으로 넣어 비교할 수 있으므로, LSTM과 구조적 차이를 고려해 모델별 학습 완료 여부와 입력 특성을 함께 점검하기로 하였다.\n\n3. 윈도우 크기와 평가 지표\n윈도우 크기는 MAE 하나만으로 결정하지 않고 WAPE, RMSE를 함께 확인하기로 하였다. MAE는 해석이 쉽지만 계량기 규모 차이에 민감하고, WAPE는 규모가 다른 계량기 간 비교에 유리하지만 일부 구간에서 과소평가 가능성이 있다. RMSE는 큰 오차에 민감하므로 급격한 이상치 영향을 확인하는 보조 지표로 활용한다.\n\n4. 서비스 차별화와 사업화 방향\n기존 탄소중립 솔루션이 통계 기반 분석과 대시보드 중심으로 운영되는 경우가 많다는 점을 확인하고, 본 프로젝트는 예측, 실시간 이상 후보 탐지, 자연어 챗봇, 자동 보고서 생성을 결합한 스마트 에너지 관리 플랫폼으로 차별화하기로 하였다. 공장 에너지 관리와 설비 모니터링을 결합하고, 전력 계량기 데이터만으로 설비 상태 진단 가능성을 검토한다. 정부 지원 사업, 인공지능 바우처, 지방자치단체 지원 사업을 활용하면 중소기업의 초기 도입 장벽을 낮출 수 있으며, 데이터 축적 후 고도화 기능을 유료화하는 로드맵을 검토하였다.\n\n5. 자연어 기반 에너지 관리 기능\n사용자가 특정 기간의 피크 발생, 정전 가능성, 설비 상태, 요금 변화, 보고서 작성 등을 자연어로 질문하면 시스템이 근거 데이터와 함께 답변하는 구조를 핵심 차별점으로 정리하였다. 예를 들어 특정 기간의 피크가 정전 때문인지 묻는 경우, 해당 기간의 계량기 값, 결측 여부, 이상 후보, 설비 이벤트, 요금 영향을 함께 제시해야 한다.\n\n6. 데이터 파이프라인과 계량기별 전처리\n회의에서는 MongoDB 원천 이벤트를 PostgreSQL 1분 등간격 템플릿으로 정렬하고, 예측·학습용 참조 테이블과 이상 탐지·대시보드용 기술 테이블을 분리하는 흐름이 논의되었다. 다만 실시간 스트리밍 통합 가능 상태는 회의 발언 기준이며, 실제 운영 검증 결과와는 구분한다. 계량기마다 결측 패턴과 이벤트 발생 간격이 다르므로 81개 계량기 단위의 개별 전처리 규칙이 필요하다. 동일한 1분 안에 여러 이벤트가 발생하면 정각에 가장 가까운 값을 선택하는 규칙을 후보로 검토하였다.\n\n7. 논문 기반 전처리의 한계와 자체 규칙 정의\n논문에 제시된 전처리 방식은 연구팀 내부 규칙에 의존하는 경우가 많아 모든 계량기별 값을 역추적하기 어렵다. 따라서 논문 규칙을 그대로 따르기보다 현재 데이터 특성에 맞는 자체 규칙을 정의하고, 결측·보정·중복 처리 근거를 추적 가능하게 남기는 방향이 더 실용적이라고 정리하였다.\n\n8. 언어모델 운영과 자원 최적화\nLLM을 많이 사용하는 구조는 비용 부담이 크므로 반복 업무에는 비용 효율적인 소형 언어모델과 프롬프트 자동 개선 방식을 함께 검토하기로 하였다. 모든 업무를 미세조정으로 해결하기보다는 프롬프트 설계와 자동 개선을 병행하고, 서버 비용·응답 속도·정확도 균형을 고려한다. 서브 에이전트는 개발 효율을 높일 수 있지만 토큰 낭비와 로컬 자원 과부하 위험이 있으므로 역할, 작업 범위, 종료 조건, 검수 기준을 명확히 정해 운용하기로 하였다."
    ],
}


def cell_text(tc: ET.Element) -> str:
    return "\n".join(
        "".join(t.text or "" for t in p.iter(f"{W}t")).strip()
        for p in tc.findall(f"{W}p")
        if "".join(t.text or "" for t in p.iter(f"{W}t")).strip()
    )


def clone_ppr(tc: ET.Element) -> ET.Element | None:
    for p in tc.findall(f"{W}p"):
        ppr = p.find(f"{W}pPr")
        if ppr is not None:
            return ET.fromstring(ET.tostring(ppr, encoding="utf-8"))
    return None


def clone_rpr(tc: ET.Element) -> ET.Element | None:
    for r in tc.iter(f"{W}r"):
        rpr = r.find(f"{W}rPr")
        if rpr is not None:
            return ET.fromstring(ET.tostring(rpr, encoding="utf-8"))
    return None


def make_paragraph(text: str, ppr_template: ET.Element | None, rpr_template: ET.Element | None) -> ET.Element:
    p = ET.Element(f"{W}p")
    if ppr_template is not None:
        p.append(ET.fromstring(ET.tostring(ppr_template, encoding="utf-8")))
    r = ET.SubElement(p, f"{W}r")
    if rpr_template is not None:
        r.append(ET.fromstring(ET.tostring(rpr_template, encoding="utf-8")))
    t = ET.SubElement(r, f"{W}t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text
    return p


def set_cell(tc: ET.Element, text: str) -> None:
    tcpr = tc.find(f"{W}tcPr")
    ppr = clone_ppr(tc)
    rpr = clone_rpr(tc)
    for child in list(tc):
        if child is not tcpr:
            tc.remove(child)
    for paragraph_text in text.split("\n"):
        tc.append(make_paragraph(paragraph_text, ppr, rpr))


def update_docx() -> None:
    if not TEMPLATE.exists():
        raise FileNotFoundError(TEMPLATE)
    shutil.copy2(TEMPLATE, OUTPUT)
    with ZipFile(TEMPLATE, "r") as zin:
        members = {item.filename: zin.read(item.filename) for item in zin.infolist()}
    root = ET.fromstring(members["word/document.xml"])
    tbl = next(root.iter(f"{W}tbl"))
    rows = tbl.findall(f"{W}tr")
    for row_num, values in ROWS.items():
        cells = rows[row_num - 1].findall(f"{W}tc")
        if len(cells) != len(values):
            raise ValueError(f"row {row_num} has {len(cells)} cells, expected {len(values)}")
        for tc, value in zip(cells, values):
            set_cell(tc, value)
    members["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as zout:
        for name, data in members.items():
            zout.writestr(name, data)


def validate() -> list[str]:
    issues: list[str] = []
    with ZipFile(OUTPUT) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    text = "\n".join(t.text or "" for t in root.iter(f"{W}t"))
    for token in ["EMS", "ems.", "FEMS", "fems", "production 완료", "전체 pytest pass"]:
        if token in text:
            issues.append(f"forbidden:{token}")
    if re.search(r"[\U0001F300-\U0001FAFF]", text):
        issues.append("emoji_found")
    required = ["2026 년 06 월 02 일 ( 5 차)", "윤치영", "비지도 이상 탐지", "계량기별 전처리", "회의 발언 기준"]
    for token in required:
        if token not in text:
            issues.append(f"missing:{token}")
    return issues


if __name__ == "__main__":
    update_docx()
    issues = validate()
    print(f"output={OUTPUT}")
    print(f"size={OUTPUT.stat().st_size}")
    print(f"issues={issues or 'ok'}")
