#!/usr/bin/env python3
"""Finalize the Korean-facing Huang 2022 EMS report.

This script is intentionally presentation-focused. It keeps technical identifiers in
artifact tables, but the visible report prose uses consistent Korean modeling terms:
종속변수(타겟), 설명변수, 관측 범위, 설비군 집계.
"""
from __future__ import annotations

from pathlib import Path
import json
import re
import zipfile

import pandas as pd

ROOT = Path("/home/viowlet/Projects/SKN25-FINAL-4Team")
REPORT = ROOT / "reports/a_clean_huang2022_benchmark"
TABLES = REPORT / "tables"
HTML = REPORT / "report.html"
MD = REPORT / "report.md"
PLOTLY = REPORT / "report_plotly.html"
PKG = ROOT / "reports/a_clean_huang2022_benchmark_package.zip"


def df_to_markdown(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in df.iterrows():
        vals = [str(row[col]).replace("|", "/") for col in cols]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def replace_md_section(text: str, heading: str, next_heading: str, body: str) -> str:
    pat = rf"({re.escape(heading)}\n\n).*?(\n\n{re.escape(next_heading)})"
    return re.sub(pat, lambda m: m.group(1) + body + m.group(2), text, flags=re.S)


def replace_html_section(text: str, section_no: str, title: str, body: str) -> str:
    pat = rf'<section class="card"><h2>{re.escape(section_no)}\. {re.escape(title)}</h2>.*?</section>'
    repl = f'<section class="card"><h2>{section_no}. {title}</h2>{body}</section>'
    return re.sub(pat, repl, text, flags=re.S)


def table_html(df: pd.DataFrame) -> str:
    return '<div class="table-wrap">' + df.to_html(index=False, classes="data-table", escape=False, border=0) + "</div>"


# 1. User-facing tables.
feature_policy = pd.DataFrame([
    {
        "구분": "종속변수(타겟)",
        "사용 내용": "각 설비군의 다음 시점 1시간 전력 소비량",
        "적용 모델": "전체 모델",
        "설명": "모델이 예측하는 값이다. 이번 보고서의 종속변수는 A-clean 4개 설비군 전력 소비량이다.",
    },
    {
        "구분": "설명변수: 과거 전력값",
        "사용 내용": "종속변수 자체의 직전 1~24시간 전력값",
        "적용 모델": "SVR, XGBoost",
        "설명": "한 시점을 예측할 때 직전 24시간 전력 흐름을 24개 숫자 입력으로 펼쳐 사용한다.",
    },
    {
        "구분": "설명변수: 과거 전력 흐름",
        "사용 내용": "직전 24시간의 전력값과 시간대 정보",
        "적용 모델": "LSTM",
        "설명": "24시간 흐름을 순서대로 넣는다. 각 시간에는 전력값과 하루 중 시간 위치 정보가 들어간다.",
    },
    {
        "구분": "설명변수: 시간대 주기",
        "사용 내용": "하루 24시간에서 현재 시간이 어디에 위치하는지 나타내는 순환값",
        "적용 모델": "전체 모델",
        "설명": "0시와 23시가 멀리 떨어진 숫자로 보이지 않도록 시간대를 원형 정보로 표현한다.",
    },
    {
        "구분": "제외한 설명변수",
        "사용 내용": "기상, 태양광·열병합 발전 전력, 다른 계량기나 다른 타겟, 이동 통계, 냉난방도일, 외부 이벤트",
        "적용 모델": "전체 모델",
        "설명": "Huang et al. 2022 방식에 맞춰 과거 전력값과 시간대 정보만 남겼다.",
    },
])
feature_policy.to_csv(TABLES / "feature_input_policy_ko.csv", index=False, encoding="utf-8-sig")


horizon_business = pd.DataFrame([
    {
        "예측 범위": "15~30분",
        "분류": "초단기 부하 예측",
        "운영 목적": "주파수 추종, sub-hourly 부하 제어, 신재생 출력 변동 대응",
        "의사결정 성격": "자동 제어와 계통 보조서비스에 가까운 영역",
    },
    {
        "예측 범위": "1시간",
        "분류": "초단기/단기 경계",
        "운영 목적": "HVAC 선행 제어, 피크 수요 관리, 수요반응 대응, ESS 충방전 보정, 이상 징후 감지",
        "의사결정 성격": "당일 운영자가 설비 제어를 조정할 수 있는 실행 단위",
    },
    {
        "예측 범위": "6시간",
        "분류": "일중 단기 예측",
        "운영 목적": "당일 기상 변화와 부하 흐름을 반영한 냉난방·설비 운전 재조정",
        "의사결정 성격": "반일 단위 운영 계획 보정",
    },
    {
        "예측 범위": "24시간",
        "분류": "단기 부하 예측",
        "운영 목적": "하루 전 전력 조달, 일일 설비 가동 계획, ESS 일일 운전 계획",
        "의사결정 성격": "전일 계획과 조달 의사결정",
    },
])
horizon_business.to_csv(TABLES / "one_hour_horizon_business_value_ko.csv", index=False, encoding="utf-8-sig")

one_hour_cases = pd.DataFrame([
    {
        "활용 영역": "HVAC 선행 제어",
        "1시간 예측의 쓰임": "건물의 열적 관성과 온도 전달 지연을 고려해 설정 온도와 운전 강도를 미리 조정한다.",
        "해석 포인트": "분 단위 제어보다 운영 여유가 있고, 하루 전 계획보다 최신 계측값을 반영한다.",
    },
    {
        "활용 영역": "피크 수요 관리",
        "1시간 예측의 쓰임": "피크 가능성이 보이면 사전 냉방, 부하 분산, 배터리 방전 같은 조치를 검토한다.",
        "해석 포인트": "요금과 설비 운전의 의사결정 시간이 맞물리는 구간이다.",
    },
    {
        "활용 영역": "수요반응 대응",
        "1시간 예측의 쓰임": "이행 가능한 감축량을 당일 부하 흐름에 맞춰 다시 계산하고, 미이행 위험을 줄인다.",
        "해석 포인트": "전일 계획의 오차를 운영 직전에 보정하는 역할이다.",
    },
    {
        "활용 영역": "ESS 또는 배터리 운영",
        "1시간 예측의 쓰임": "부하 상승이 예상되는 시간에 맞춰 충방전과 충전 상태 범위를 조정한다.",
        "해석 포인트": "불필요한 충방전 반복을 줄이는 운영 판단에 쓸 수 있다.",
    },
    {
        "활용 영역": "이상 징후 감지",
        "1시간 예측의 쓰임": "실측값과 1시간 전 예측값의 차이를 최근 오차 분포와 비교해 급격한 설비 이상을 탐지한다.",
        "해석 포인트": "정상 예상 부하에서 벗어난 시점을 빠르게 찾는 진단 보조 지표다.",
    },
])
one_hour_cases.to_csv(TABLES / "one_hour_use_cases_ko.csv", index=False, encoding="utf-8-sig")

input_rationale = pd.DataFrame([
    {
        "타당성 요인": "24시간 주기성",
        "근거": "건물 전력 부하는 업무 시작, 점심, 퇴근, 야간 운전처럼 하루 단위 패턴을 반복한다.",
        "모델 의미": "직전 24시간 전력 흐름은 다음 1시간 부하를 설명하는 기본 자기회귀 입력이다.",
    },
    {
        "타당성 요인": "업무시간 패턴",
        "근거": "평일·주말, 재실 상태, 장비 운전 모드가 직전 하루 부하 곡선에 반영된다.",
        "모델 의미": "별도 일정 정보가 없어도 최근 부하 흐름이 운영 모드의 대리 정보가 된다.",
    },
    {
        "타당성 요인": "열적 관성",
        "근거": "건물 구조체와 냉방·환기 계통은 전력 사용 이후에도 열 상태와 운전 상태의 잔류 영향을 남긴다.",
        "모델 의미": "과거 전력값은 다음 시점 열 요구량과 설비 부하를 간접적으로 반영한다.",
    },
    {
        "타당성 요인": "경량 운용성",
        "근거": "외생 변수를 최소화하면 입력 관리와 실시간 계산 부담이 줄어든다.",
        "모델 의미": "BEMS 운영 환경에서 빠르게 반복 실행할 수 있는 비교 기준을 만든다.",
    },
])
input_rationale.to_csv(TABLES / "one_hour_input_rationale_ko.csv", index=False, encoding="utf-8-sig")

technical_map = pd.DataFrame([
    {"한글 설명": "직전 1~24시간 전력값", "실제 컬럼": "target_lag_1 ~ target_lag_24", "비고": "SVR/XGBoost 표 형식 입력"},
    {"한글 설명": "전력값 표준화 순서 입력", "실제 컬럼": "target_scaled", "비고": "LSTM 입력의 전력 채널"},
    {"한글 설명": "시간대 순환값", "실제 컬럼": "hour_sin, hour_cos", "비고": "하루 24시간 주기 표현"},
    {"한글 설명": "기상 입력", "실제 컬럼": "Ta, Igm", "비고": "이번 실험에서 제외"},
    {"한글 설명": "태양광·열병합 발전 전력", "실제 컬럼": "pv_P, chp_P", "비고": "이번 실험에서 제외"},
    {"한글 설명": "이동 통계 입력", "실제 컬럼": "rolling mean/std/min/max", "비고": "이번 실험에서 제외"},
])
technical_map.to_csv(TABLES / "feature_column_mapping_reference.csv", index=False, encoding="utf-8-sig")

policy = pd.DataFrame([
    {
        "정책": "STRICT_BENCHMARK",
        "정의": "모든 구성 계량기가 관측된 시점만 종속변수 산정에 포함한다.",
        "적용 조건": "학습, 2022년, 2023년 전 구간에서 구성 계량기 누락이 거의 없다.",
        "예상 사용처": "중앙 냉방, 국소 냉방처럼 안정적인 설비군 타겟",
        "읽는 방법": "가장 해석이 쉽다. 행 수가 줄어들 수 있으므로 관측 범위를 함께 본다.",
    },
    {
        "정책": "FIXED_PANEL_BENCHMARK",
        "정의": "기간 전체에서 안정적으로 관측되는 구성 계량기 묶음을 고정하고 그 합을 종속변수로 둔다.",
        "적용 조건": "일부 계량기가 장기 미관측이지만, 제외해도 예측 대상 의미가 크게 흔들리지 않는다.",
        "예상 사용처": "건물 H2, 전체 소비량의 보수적 대안",
        "읽는 방법": "구성 계량기가 고정되어 비교가 안정적이다. 제외된 계량기의 부하 비중을 반드시 표시한다.",
    },
    {
        "정책": "COVERAGE_THRESHOLD_TARGET",
        "정의": "구성 계량기 관측률 또는 부하 비중이 기준 이상인 시점만 산정에 포함한다.",
        "적용 조건": "누락된 계량기의 부하 기여가 작고, 큰 집계 타겟의 행 수를 충분히 확보해야 한다.",
        "예상 사용처": "`계통 수전 전력`, 일부 설비군 집계(group aggregate)",
        "읽는 방법": "관측 범위 기준과 부하 비중 기준을 성능표 옆에 같이 둔다.",
    },
    {
        "정책": "VERSIONED_TARGET",
        "정의": "구성 계량기 묶음이 바뀌는 시점을 타겟 버전으로 분리한다.",
        "적용 조건": "설비 교체, 계량기 추가·종료, 장기 미관측 전환이 특정 시점에 발생한다.",
        "예상 사용처": "계통 전력, 건물 H2, 전체 소비량",
        "읽는 방법": "한 타겟 안에서 의미가 바뀌는 문제를 줄인다. 성능표는 버전별로도 나누어 본다.",
    },
    {
        "정책": "OBSERVED_SUM_DIAGNOSTIC",
        "정의": "현재 관측된 구성 계량기만 합산하고, 관측 계량기 수와 관측 범위를 함께 기록한다.",
        "적용 조건": "흐름 확인, 관측 범위 점검, 대시보드 목적이 크다.",
        "예상 사용처": "관측 범위 점검, 흐름 파악, 대시보드",
        "읽는 방법": "모델링 타겟 확정보다 진단 목적에 가깝다. 값이 이어져 보여도 구성 범위가 시간에 따라 바뀔 수 있다.",
    },
    {
        "정책": "EXCLUDE",
        "정의": "예측 목적과 맞지 않거나 품질 기준을 만족하지 못해 타겟 후보에서 제외한다.",
        "적용 조건": "발전 계량, 열 계량, 기상, 중복 동시합산, 품질 불충분에 해당한다.",
        "예상 사용처": "타겟 제외. 설명변수 활용은 별도 검토",
        "읽는 방법": "타겟에서 제외해도 설명변수로 쓸 수 있는지는 별도로 검토한다.",
    },
])
policy.to_csv(TABLES / "target_policy_explanation_ko.csv", index=False, encoding="utf-8-sig")

# 2. Canonical visible prose.
input_text = (
    "모든 모델은 종속변수(타겟) 자체의 과거 전력값과 시간대 주기 정보만 설명변수로 사용한다. "
    "다른 계량기, 다른 타겟, 기상, 태양광·열병합 발전 전력, 이동 통계, 냉난방도일, 외부 이벤트 변수는 이번 입력에서 제외했다. "
    "게이트웨이 장애 여부와 타겟 정책 관련 컬럼은 학습 행 필터링과 평가 구간 해석에만 사용했으며, 모델 입력으로 넣지 않았다."
)
input_shape_text = (
    "SVR과 XGBoost는 직전 24시간 전력값을 24개 숫자 항목으로 펼치고, "
    "하루 중 시간 위치를 나타내는 두 값을 더해 총 26개 숫자 입력을 사용한다. "
    "LSTM은 같은 정보를 펼치지 않고 직전 24시간 흐름으로 넣으며, 각 시간에는 과거 전력값과 시간대 정보가 들어간다."
)
policy_text = (
    "타겟 정책은 여러 계량기를 합산해 종속변수(타겟)를 만들 때 적용하는 데이터 품질 규칙이다. "
    "같은 설비군이라도 구성 계량기 중 일부가 빠지면 종속변수 값의 의미가 달라진다. "
    "정책은 이 차이를 문서화해 학습·평가에 쓸 타겟과 진단용으로만 볼 타겟을 구분하는 기준이다."
)
policy_criteria = (
    "핵심 판단 기준은 구성 계량기의 동시 관측 여부, 누락 계량기의 부하 비중, "
    "계량기 구성 변화가 특정 시점 이후 계속되는지 여부다. `계통 수전 전력`, 설비군 집계(group aggregate), "
    "건물 집계는 같은 표에서 성능만 비교하지 않고 관측 범위와 타겟 버전을 함께 점검한다."
)

one_hour_value_text = (
    "1시간 앞 예측은 실시간 제어와 전일 계획 사이의 운영 간격을 메운다. "
    "분 단위 예측은 자동 제어와 보조서비스에 가깝고, 24시간 예측은 조달과 일일 계획에 가깝다. "
    "1시간 예측은 당일 계측값을 반영하면서도 HVAC, 피크 수요, 수요반응, ESS 운전, 이상 징후 점검을 조정할 시간을 준다."
)
one_hour_dayahead_text = (
    "하루 전 예측은 전력 조달과 일일 운전 계획을 정하는 데 적합하다. "
    "1시간 예측은 당일 실제 부하 흐름을 반영해 그 계획을 보정한다. "
    "따라서 이번 1시간 ahead 실험은 장기 계획용 수요 전망보다 운영 직전 제어와 진단에 가까운 성능을 본다."
)
one_hour_input_text = (
    "직전 24시간 전력값을 쓰는 이유는 건물 부하의 하루 주기성과 자기회귀성이 강하기 때문이다. "
    "업무시간, 야간 운전, 냉방·환기 반복 부하, 열적 관성은 최근 하루 전력 곡선에 상당 부분 반영된다. "
    "시간대 주기 정보는 같은 전력 수준이라도 새벽, 업무시간, 야간의 의미가 다르다는 점을 보완한다."
)
target_text = (
    "현재 비교에 사용한 A-clean 4개 타겟은 안정적인 전력 소비 설비군이다. "
    "건물 H2, 전체 소비량, 계통 전력 계열처럼 구성 변화나 전력 부호 해석이 섞이는 대상은 "
    "의미를 먼저 고정한 뒤 별도 실험군으로 다룬다."
)
metric_text = (
    "RMSE는 예측값과 실제값 차이를 제곱해 평균낸 뒤 제곱근을 취한 값이다. 단위는 종속변수 전력 P와 같다. "
    "MAE는 절대오차 평균이고, R²는 실제 변동을 모델이 설명한 비율이다. "
    "이 보고서에서는 2022 RMSE를 모델 비교 기준으로 두고, 2023 MAE/RMSE/R²를 별도 연도 평가 지표로 둔다."
)

# 3. Markdown rewrite.
md_text = MD.read_text(encoding="utf-8")
md_sec2 = (
    one_hour_value_text + "\n\n" +
    one_hour_dayahead_text + "\n\n" +
    df_to_markdown(horizon_business) + "\n\n" +
    df_to_markdown(one_hour_cases) + "\n\n" +
    "### 직전 24시간 입력의 의미" + "\n\n" +
    one_hour_input_text + "\n\n" +
    df_to_markdown(input_rationale)
)
md_sec3 = input_text + "\n\n" + input_shape_text + "\n\n" + df_to_markdown(feature_policy)
md_sec5 = policy_text + "\n\n" + policy_criteria + "\n\n" + df_to_markdown(policy)
# Rebuild the report body from the stable generated sections so heading numbers stay consistent.
parts = re.split(r"\n(?=## \d+\. )", md_text)
section_map = {}
for part in parts[1:]:
    title = part.split("\n", 1)[0]
    key = re.sub(r"^## \d+\. ", "", title)
    section_map[key] = part
sec1 = replace_md_section(section_map.get("실험 범위와 데이터 분할", ""), "## 1. 실험 범위와 데이터 분할", "## 2. 입력 피처 구성", section_map.get("실험 범위와 데이터 분할", "").split("\n\n", 1)[1] if "\n\n" in section_map.get("실험 범위와 데이터 분할", "") else "") if False else section_map.get("실험 범위와 데이터 분할", "")
sec1 = re.sub(r"^## \d+\. ", "## 1. ", sec1, count=1)
sec3_old = section_map.get("예측 산출물 기간", "")
sec3_old = re.sub(r"^## \d+\. ", "## 4. ", sec3_old, count=1)
sec6_old = section_map.get("핵심 결과", "")
# Existing result sections are preserved and renumbered below.
renamed_sections = []
for new_no, key in [
    ("8", "핵심 결과"),
    ("9", "2022년 비교 결과: 대상별 RMSE 최저 모델"),
    ("10", "대상별 모델군 최저 결과"),
    ("11", "전체 모델 결과 파일"),
    ("12", "그래프 구성"),
]:
    part = section_map.get(key, "")
    if part:
        part = re.sub(r"^## \d+\. ", f"## {new_no}. ", part, count=1)
        renamed_sections.append(part)
md_text = "\n\n".join([
    "# EMS A-clean 전력 소비 예측 모델 비교",
    sec1.strip(),
    "## 2. 1시간 ahead 예측의 운영 의미\n\n" + md_sec2,
    "## 3. 입력 피처 구성\n\n" + md_sec3,
    sec3_old.strip(),
    "## 5. 타겟 정책\n\n" + md_sec5,
    "## 6. 예측 대상\n\n" + target_text + "\n\n" + re.search(r"\n\n(\| 대상.*?)(?=\n\n##|\Z)", section_map.get("예측 대상", ""), flags=re.S).group(1),
    "## 7. 지표 정의\n\n" + metric_text,
] + renamed_sections) + "\n"
md_text = md_text.replace("target으로 사용한다", "타겟으로 사용한다")
md_text = md_text.replace("설비 group", "설비군")
md_text = md_text.replace("설비군 집계(설비군 집계(group aggregate))", "설비군 집계(group aggregate)")
md_text = md_text.replace("feature 활용", "설명변수 활용")
MD.write_text(md_text, encoding="utf-8")

# 4. HTML rewrite and readability pass.
html_text = HTML.read_text(encoding="utf-8")
html_text = re.sub(
    r"body\{([^}]*)\}",
    lambda m: "body{" + re.sub(r";?font-size:\d+px", "", m.group(1)).rstrip(";") + ";font-size:18px}",
    html_text,
    count=1,
    flags=re.S,
)
css_replacements = {
    "header h1{margin:0 auto 12px;font-size:36px;": "header h1{margin:0 auto 12px;font-size:42px;",
    "header p{max-width:1080px;margin:0 auto;color:#fdebd3;font-size:16px}": "header p{max-width:1080px;margin:0 auto;color:#fdebd3;font-size:18px}",
    "main{max-width:1280px;margin:0 auto;padding:30px 24px 70px}": "main{max-width:1360px;margin:0 auto;padding:34px 28px 76px}",
    "border-radius:22px;padding:24px;margin:20px 0;": "border-radius:22px;padding:30px;margin:24px 0;",
    "h2{margin:4px 0 14px;font-size:23px;": "h2{margin:4px 0 16px;font-size:28px;",
    "h3{margin:0 0 12px;font-size:17px;": "h3{margin:0 0 12px;font-size:21px;",
    "p{margin:8px 0}": "p{margin:10px 0;font-size:18px}",
    "font-size:13px;color:var(--muted)": "font-size:16px;color:var(--muted)",
    "font-size:12px;color:var(--muted);": "font-size:15px;color:var(--muted);",
    "background:#f8eedf;font-size:13px": "background:#f8eedf;font-size:16px",
    "padding:10px 12px;": "padding:12px 14px;",
    "padding:9px 12px;": "padding:11px 14px;",
    "font-size:12px;margin-right:6px": "font-size:14px;margin-right:6px",
}
for old, new in css_replacements.items():
    html_text = html_text.replace(old, new)
html_sec2 = (
    f"<p>{one_hour_value_text}</p><p>{one_hour_dayahead_text}</p>"
    f"{table_html(horizon_business)}"
    f"{table_html(one_hour_cases)}"
    f"<h3>직전 24시간 입력의 의미</h3><p>{one_hour_input_text}</p>"
    f"{table_html(input_rationale)}"
)
html_sec3 = f"<p>{input_text}</p><p>{input_shape_text}</p>{table_html(feature_policy)}"
html_sec5 = f"<p>{policy_text}</p><p>{policy_criteria}</p>{table_html(policy)}"
html_sec6_prefix = f"<p>{target_text}</p>"
# Insert or replace the 1-hour business rationale card.
business_card = f'<section class="card"><h2>2. 1시간 ahead 예측의 운영 의미</h2>{html_sec2}</section>'
html_text = re.sub(r'<section class="card"><h2>2\. 1시간 ahead 예측의 운영 의미</h2>.*?</section>', business_card, html_text, flags=re.S)
if '2. 1시간 ahead 예측의 운영 의미' not in html_text:
    html_text = html_text.replace('<section class="card"><h2>2. 입력 피처 구성</h2>', business_card + '\n<section class="card"><h2>2. 입력 피처 구성</h2>', 1)
for old_title, new_title in {
    '2. 입력 피처 구성': '3. 입력 피처 구성',
    '3. 예측 산출물 기간': '4. 예측 산출물 기간',
    '4. 타겟 정책': '5. 타겟 정책',
    '5. 예측 대상': '6. 예측 대상',
    '6. 지표 정의': '7. 지표 정의',
    '7. 핵심 결과': '8. 핵심 결과',
    '8. 2022년 비교 결과: 대상별 RMSE 최저 모델': '9. 2022년 비교 결과: 대상별 RMSE 최저 모델',
    '9. 대상별 모델군 최저 결과': '10. 대상별 모델군 최저 결과',
    '10. 전체 모델 결과': '11. 전체 모델 결과',
    '10. 전체 모델 결과 파일': '11. 전체 모델 결과 파일',
    '11. 그래프 구성': '12. 그래프 구성',
}.items():
    html_text = html_text.replace(f'<h2>{old_title}</h2>', f'<h2>{new_title}</h2>')
html_text = replace_html_section(html_text, "3", "입력 피처 구성", html_sec3)
html_text = replace_html_section(html_text, "5", "타겟 정책", html_sec5)
html_text = re.sub(
    r'(<section class="card"><h2>6\. 예측 대상</h2>).*?(<div class="table-wrap">)',
    lambda m: m.group(1) + html_sec6_prefix + m.group(2),
    html_text,
    flags=re.S,
)
html_text = re.sub(
    r'(<section class="card"><h2>7\. 지표 정의</h2><p>).*?(</p></section>)',
    lambda m: m.group(1) + metric_text + m.group(2),
    html_text,
    flags=re.S,
)
html_text = re.sub(
    r'(<section class="card two"><div><h2>7\. 지표 정의</h2><p>).*?(</p></div><div><h2>8\. 핵심 결과</h2>)',
    lambda m: m.group(1) + metric_text + m.group(2),
    html_text,
    flags=re.S,
)
html_text = html_text.replace("target으로 사용한다", "타겟으로 사용한다")
html_text = html_text.replace("설비 group", "설비군")
html_text = html_text.replace("설비군 집계(설비군 집계(group aggregate))", "설비군 집계(group aggregate)")
html_text = html_text.replace("feature 활용", "설명변수 활용")
HTML.write_text(html_text, encoding="utf-8")

# 5. Plotly page gets the same font treatment and terminology if present.
if PLOTLY.exists():
    plotly = PLOTLY.read_text(encoding="utf-8")
    plotly = re.sub(r"line-height:1(?:\.6|\.65)(?:;font-size:\d+px)*", "line-height:1.65;font-size:18px", plotly)
    plotly = plotly.replace("font-size:18px5", "font-size:18px")
    plotly = plotly.replace("font-size:12px", "font-size:15px")
    plotly = plotly.replace("font-size:13px", "font-size:16px")
    business_plotly = f"<section><h2>1시간 ahead 예측의 운영 의미</h2><p>{one_hour_value_text}</p><p>{one_hour_dayahead_text}</p><p>{one_hour_input_text}</p></section>"
    plotly = re.sub(r"<section><h2>1시간 ahead 예측의 운영 의미</h2>.*?</section>", business_plotly, plotly, flags=re.S)
    if "1시간 ahead 예측의 운영 의미" not in plotly:
        plotly = plotly.replace("<section><h2>입력 피처 구성</h2>", business_plotly + "\n<section><h2>입력 피처 구성</h2>", 1)
    plotly = re.sub(
        r"<section><h2>입력 피처 구성</h2><p>.*?</p><p>.*?</p></section>",
        f"<section><h2>입력 피처 구성</h2><p>{input_text}</p><p>{input_shape_text}</p></section>",
        plotly,
        flags=re.S,
    )
    for old, new in {
        "target으로 사용한다": "타겟으로 사용한다",
        "설비 group": "설비군",
        "설비군 집계(설비군 집계(group aggregate))": "설비군 집계(group aggregate)",
        "feature 활용": "설명변수 활용",
        "단위는 target 전력 P와 같다": "단위는 종속변수 전력 P와 같다",
    }.items():
        plotly = plotly.replace(old, new)
    plotly = re.sub(r"font-size:18px(?:;font-size:18px)+", "font-size:18px", plotly)
    plotly = re.sub(r"font-size:15px(?:;font-size:15px)+", "font-size:15px", plotly)
    plotly = re.sub(r"font-size:16px(?:;font-size:16px)+", "font-size:16px", plotly)
    PLOTLY.write_text(plotly, encoding="utf-8")

# 6. Rebuild zip packages.
for zipname, files in {
    "html_files_only.zip": [REPORT / "report.html", REPORT / "report_plotly.html"],
    "report_html_only.zip": [REPORT / "report.html"],
}.items():
    with zipfile.ZipFile(REPORT / zipname, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if f.exists():
                z.write(f, f.name)

with zipfile.ZipFile(PKG, "w", zipfile.ZIP_DEFLATED) as z:
    for f in [REPORT / "report.html", REPORT / "report_plotly.html", REPORT / "report.md", REPORT / "local_report_manifest.json", REPORT / "index.md"]:
        if f.exists():
            z.write(f, f.relative_to(REPORT.parent))
    for f in sorted((REPORT / "figures").glob("*.png")):
        z.write(f, f.relative_to(REPORT.parent))
    for f in sorted(TABLES.glob("*.csv")):
        z.write(f, f.relative_to(REPORT.parent))

# 7. Validation manifest.
html_text = HTML.read_text(encoding="utf-8")
md_text = MD.read_text(encoding="utf-8")
scan_terms = [
    "target으로 사용", "설비 group", "건물 건물", "예측 예측", "feature 활용",
    "font-size:13px", "font-size:12px", "단위는 target", "target 값", "grid_import_P", "환기 설비",
]
term_hits = {t: {"html": html_text.count(t), "md": md_text.count(t)} for t in scan_terms if html_text.count(t) or md_text.count(t)}
with zipfile.ZipFile(PKG) as z:
    bad = z.testzip()
manifest_path = REPORT / "local_report_manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
manifest.update({
    "korean_modeling_terms_reviewed": True,
    "visible_font_size_increased": True,
    "policy_table_rewritten": True,
    "one_hour_business_rationale_added": True,
    "one_hour_horizon_table_saved": "tables/one_hour_horizon_business_value_ko.csv",
    "one_hour_use_case_table_saved": "tables/one_hour_use_cases_ko.csv",
    "one_hour_input_rationale_saved": "tables/one_hour_input_rationale_ko.csv",
    "technical_feature_mapping_saved": "tables/feature_column_mapping_reference.csv",
    "visible_problem_term_hits": term_hits,
    "package_bad_file": bad,
})
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

print(json.dumps({
    "status": "done",
    "report_html": str(HTML),
    "report_md": str(MD),
    "problem_term_hits": term_hits,
    "package_bad_file": bad,
}, ensure_ascii=False, indent=2))
