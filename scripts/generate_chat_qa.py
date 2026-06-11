import os
import json
import re
import time
import psycopg2
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime
from collections import Counter

load_dotenv(Path(__file__).parent.parent / ".env")

conn = psycopg2.connect(
    host=os.getenv("DB_HOST"),
    port=os.getenv("DB_PORT"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    dbname=os.getenv("DB_NAME"),
)

client = OpenAI(api_key=os.getenv("OPEN_API_KEY"))


# ============================================================
# 1. DB 데이터 로드
#    기존 generate_qa.py 구조 유지
# ============================================================

def fetch_data():
    cur = conn.cursor()

    cur.execute("""
        SELECT timestamp, anomaly_type, severity, description, actual_w, predicted_w, residual_w
        FROM public.anomaly_results
        WHERE gateway_failure = FALSE OR gateway_failure IS NULL
        ORDER BY RANDOM()
        LIMIT 50;
    """)
    anomalies = cur.fetchall()

    cur.execute("""
        SELECT period, total_consumption_kwh, self_sufficiency_pct, avg_cop,
               anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh
        FROM public.monthly_report
        ORDER BY period;
    """)
    monthly = cur.fetchall()

    cur.execute("""
        SELECT equipment_id, equipment_name, title, cause, action, status
        FROM public.work_orders
        ORDER BY created_at DESC
        LIMIT 5;
    """)
    work_orders = cur.fetchall()

    cur.execute("""
        SELECT ts, meter_urn, measurement, value
        FROM ems.cr_measurement_1h
        WHERE meter_urn IN ('H1.Z16', 'H1.Z20', 'V.Z84', 'H1.Z11', 'H1.Z12')
        ORDER BY RANDOM()
        LIMIT 50;
    """)
    meters = cur.fetchall()

    return anomalies, monthly, work_orders, meters


def load_domain_knowledge():
    base = Path("backend/src/knowledge/")

    content = (base / "domain_knowledge.py").read_text(encoding="utf-8")

    def extract(var_name):
        start_token = f'{var_name} = """'
        start = content.find(start_token)
        if start == -1:
            return ""
        start += len(start_token)
        end = content.find('"""', start)
        if end == -1:
            return ""
        return content[start:end]

    parts = [
        extract("DOMAIN_KNOWLEDGE_PROMPT"),
        extract("KFEMS_STANDARD_TERMS"),
        extract("ANOMALY_DOMAIN_PROMPT"),
        extract("ANOMALY_RECOMMENDATION_PROMPT"),
        extract("FORECAST_RECOMMENDATION_PROMPT"),
        (base / "01_meter_domain_v2.md").read_text(encoding="utf-8"),
        (base / "02_feature_domain_v3.md").read_text(encoding="utf-8"),
        (base / "03_fems_cms_domain_v2.md").read_text(encoding="utf-8"),
    ]

    return "\n\n".join(parts)


# ============================================================
# 2. Chat QA용 규칙
# ============================================================

CATEGORY_DISTRIBUTION = {
    "anomaly": 5,
    "cms": 5,
    "report": 5,
    "rag": 5,
    "forecast": 5,
}

PERSONA_DISTRIBUTION = {
    "field_engineer": 6,
    "energy_manager": 6,
    "operator": 7,
    "team_lead": 6,
}

QUESTION_TYPE_DISTRIBUTION = {
    "short": 15,     # 5~20자: Chat QA의 핵심
    "normal": 7,     # 8~40자: 너무 엄격하게 보지 않음
    "followup": 3,   # 후속질문
}

BAD_QUESTION_PATTERNS = [
    "분석해 주세요",
    "분석해주세요",
    "진단해 주세요",
    "진단해주세요",
    "평가해 주세요",
    "평가해주세요",
    "설명해 주세요",
    "설명해주세요",
    "보고서를 작성",
    "상세히",
    "종합적으로",
    "데이터를 기반으로",
    "원인을 분석",
    "KPI를 분석",
    "이상탐지 결과를 분석",
    "운영 상태를 분석",
]

FORBIDDEN_WORDS = [
    "한전",
    "수전량",
    "수전 전력",
    "수전전력",
    "계통수전",
    "MWh",
    "비계획적 셧다운",
    "비정상적으로 정지",
    "완전 고장",
    "고장 확정",
]


# 사용자 질문에는 "고장?" 같은 표현이 들어갈 수 있으므로 answer만 검사합니다.
ANSWER_FORBIDDEN_PATTERNS = [
    "고장",
    "셧다운",
    "비정상적으로 정지",
    "완전 고장",
    "고장 확정",
    "고등급 이상",
    "HIGH 등급이 빈번",
    "HIGH 등급의 이상이 빈번",
    "고등급 이상이 많",
]

ALLOWED_ANOMALY_TYPES = [
    "PowerSpike",
    "Unknown",
    "COPDrop",
    "CHPOutage",
    "PVNightNonZero",
    "ResidualSpike",
    "NightConsumption",
]


def clean_json_text(result):
    result = result.strip()

    if result.startswith("```"):
        result = result.split("\n", 1)[1]
    if result.endswith("```"):
        result = result.rsplit("```", 1)[0]

    result = result.strip()
    result = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', result)
    return result


def question_type_by_length(question):
    q = re.sub(r"\s+", "", question)
    length = len(q)

    if length <= 20:
        return "short"
    if length <= 40:
        return "normal"
    return "long"


# ============================================================
# 3. Chat QA 생성
# ============================================================

def generate_chat_qa(
    anomalies,
    monthly,
    work_orders,
    meters,
    domain_knowledge,
    batch_num,
    total_batches,
    prev_questions=None,
):
    prev_block = ""
    if prev_questions:
        prev_block = "\n\n[이미 만든 질문 목록 — 중복 금지]\n" + "\n".join(f"- {q}" for q in prev_questions[-150:])

    system_prompt = f"""당신은 Honda R&D Europe GmbH Offenbach 시설의 EMS 챗봇 학습 데이터를 만드는 데이터셋 설계자입니다.

아래 도메인 지식을 숙지하고, 실제 사용자가 챗봇에 입력할 법한 Chat QA를 생성해야 합니다.

{domain_knowledge}

절대 규칙:
1. 시설은 독일 Offenbach입니다.
2. 전력망은 독일 공공 전력망입니다.
3. "한전", "수전량", "수전 전력", "수전전력", "계통수전" 표현은 절대 금지입니다.
4. 반드시 "외부 계통 전력" 또는 "grid_P" 표현을 사용합니다.
5. 고장 확정 표현은 금지입니다.
   - 사용 가능: "이상 후보", "추정 원인", "점검 권고", "가능성이 있습니다"
   - 금지: "고장입니다", "고장 확정", "비계획적 셧다운", "비정상적으로 정지", "완전 고장"
6. 제공된 DB 데이터에 없는 날짜, 수치, 건수, 비율을 만들면 안 됩니다.
7. 계산값은 반드시 "제공값으로 계산하면"이라고 명시해야 합니다.
8. answer는 반드시 존댓말로 작성합니다.
9. forecast는 실제 예측값을 생성하면 안 됩니다.
10. forecast 답변에는 반드시 "현재 추세로 보면" 또는 "반복 패턴 기반으로 추정하면"을 포함합니다.
11. forecast 답변에는 반드시 "실제 예측 모델 수치는 없으므로 참고용입니다"를 포함합니다.
12. V.Z84는 PV 생산 계량기입니다. 외부 계통 계량기나 grid_P 계량기로 설명하면 안 됩니다.
13. 단위는 kWh만 사용합니다. MWh는 금지입니다.
14. anomaly_type은 다음 7종만 사용할 수 있습니다: {", ".join(ALLOWED_ANOMALY_TYPES)}
15. anomaly MEDIUM/LOW 등급에서 actual_w, predicted_w, residual_w가 NULL이면 잔차 수치를 언급하면 안 됩니다.
16. work_orders의 헬스스코어는 title 컬럼의 "헬스 64 · 최근 이상 98건" 같은 값을 그대로 사용합니다.
17. 헬스 스코어 기준:
    - 85 이상: 정상
    - 60~84: 주의
    - 60 미만: 경고
18. answer에 사용한 모든 날짜·수치·건수의 원천을 evidence 필드에 반드시 명시합니다.
19. 단일 monthly_report 행만 제공된 경우 "전월 대비", "이전 달보다", "증가 추세", "감소 추세", "높아졌습니다", "낮아졌습니다", "줄어들 가능성" 표현을 사용하면 안 됩니다.
20. evidence에 없는 HIGH, MEDIUM, LOW 등급의 건수나 빈도는 생성하면 안 됩니다. "HIGH 등급이 빈번합니다", "고등급 이상이 많습니다" 같은 표현은 금지합니다.
21. answer는 evidence에 명시된 사실만 설명해야 합니다. evidence에 없는 비교·추세·빈도·원인 추정은 금지합니다.

Chat QA 목적:
- Expert QA가 아닙니다.
- 실제 현장 기사, 운영자, 에너지 관리자, 팀장이 카카오톡처럼 짧게 묻는 질문을 학습시키는 데이터입니다.
- 질문은 짧고 자연스러워야 합니다.
- 답변은 3~6문장 정도로 짧게 작성합니다.
- 답변은 보고서 형식이 아니라 챗봇 응답처럼 작성합니다.
- 다만 답변에는 숫자와 근거가 반드시 포함되어야 합니다.

질문 스타일 좋은 예:
- CHP 괜찮아?
- CHP 또 문제야?
- 태양광 왜 자꾸 이상 떠?
- 태양광 또 이상 떴네?
- 이번 달 전기 많이 썼어?
- 이번 달 전기 왜 늘었어?
- 제일 문제 있는 설비 뭐야?
- 오늘 제일 위험한 설비 뭐야?
- 이 경보 무시해도 돼?
- 이거 바로 가봐야 돼?
- 지금 당장 확인해야 할 거 있어?
- 자급률 왜 떨어졌어?
- 작업지시서 왜 떴어?
- 또 작업지시 떴어?
- 점검 가야 돼?

질문 말투 추가 규칙:
- 질문의 70% 이상은 실제 사용자가 급하게 입력한 것처럼 작성합니다.
- 주어 생략, 조사 생략, 문장 미완성 형태를 적극 사용합니다.
- 너무 정중한 요청문보다 짧은 확인/판단 질문을 우선합니다.
- 예: "지금 괜찮은 거 맞아?", "오늘 뭐가 제일 문제야?", "외부 계통 전력 많이 쓴 거야?", "이거 심각해?", "바로 조치해야 돼?" 

질문 스타일 나쁜 예:
- 2023년 12월의 PowerSpike 이상탐지 결과를 분석해 주세요.
- 현재 CHP 설비의 상태를 진단해 주세요.
- 2023년 11월 KPI를 분석해 주세요.
- 월간 에너지 사용량 데이터를 기반으로 종합 분석해 주세요.

사용자 유형:
1. field_engineer
   - 현장 기사
   - 매우 짧고 직접적
   - 예: "CHP 괜찮아?", "점검 가야 돼?", "이상 또 떴어?"

2. energy_manager
   - 에너지 관리자
   - 비용, KPI, 자급률, 외부 계통 전력 중심
   - 예: "이번 달 전기 많이 썼어?", "자급률 왜 떨어졌어?"

3. operator
   - 운영자
   - 현재 상태, 경보, 조치 중심
   - 예: "지금 확인할 거 있어?", "이 경보 무시해도 돼?"

4. team_lead
   - 팀장
   - 요약, 우선순위, 리스크 중심
   - 예: "오늘 제일 큰 이슈 뭐야?", "어디부터 보면 돼?"

답변 스타일:
- 3~6문장
- 존댓말
- 숫자 포함
- evidence에 있는 실제 수치만 사용
- 과도한 제목/마크다운 금지
- "핵심 요약", "KPI 분석" 같은 보고서 제목 남발 금지
- 조치는 짧게 1~2개만 제시
- answer에서 "고장", "셧다운", "비정상적으로 정지", "완전 고장", "고장 확정" 표현 금지
- CHPOutage는 반드시 "운전 중단 가능성" 또는 "이상 후보"로만 표현
- 헬스스코어 60~84는 반드시 "주의"로 표현
- 헬스스코어 60 미만만 "경고"로 표현
- report 답변에서 단일 월 수치만 근거로 "증가 추세", "감소 추세", "줄어들 가능성"을 말하지 말 것
- report는 "2023년 12월 기준"처럼 기준 시점 중심으로 답변할 것
- forecast에서만 "현재 추세로 보면", "반복 패턴 기반으로 추정하면" 표현 사용 가능
- evidence에 없는 전월 비교 금지
- evidence에 없는 추세 표현 금지
- evidence에 없는 HIGH/LOW/MEDIUM 빈도 설명 금지
- answer는 evidence를 요약하는 수준으로 작성
"""

    user_prompt = f"""아래 실제 DB 데이터를 바탕으로 Chat QA 25개를 만들어주세요. (배치 {batch_num}/{total_batches})
{prev_block}

[anomaly_results]
컬럼: timestamp, anomaly_type, severity, description, actual_w, predicted_w, residual_w
주의: MEDIUM/LOW 등급은 actual_w/predicted_w/residual_w가 NULL인 경우가 많습니다. NULL이면 잔차 수치 사용 금지.
{anomalies}

[monthly_report]
컬럼: period, total_consumption_kwh, self_sufficiency_pct, avg_cop, anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh
{monthly}

[work_orders]
컬럼: equipment_id, equipment_name, title, cause, action, status
헬스스코어는 title 컬럼에 "헬스 64 · 최근 이상 98건" 형태로 존재합니다. cause 컬럼에 진단 내용이 포함됩니다.
{work_orders}

[ems.cr_measurement_1h]
컬럼: ts, meter_urn, measurement, value
V.Z84 사용 가능한 measurement: I1, I2, I3, P, P1, P2, P3, PF1, PF2, PF3, U1, U2, U3, W, W_in, W_out
{meters}

출력 형식:
순수 JSON 배열만 출력하세요.
마크다운 금지.
```json 금지.
[ 로 시작해서 ] 로 끝나야 합니다.

각 항목 스키마:
{{
  "id": "chat_{batch_num:02d}_001",
  "question": "...",
  "answer": "...",
  "category": "anomaly/cms/report/rag/forecast",
  "persona": "field_engineer/energy_manager/operator/team_lead",
  "question_type": "short/normal/followup",
  "difficulty": "easy/medium/hard",
  "evidence": ["..."]
}}

분배 규칙:
- 총 25개
- 카테고리별 각 5개:
  - anomaly 5개
  - cms 5개
  - report 5개
  - rag 5개
  - forecast 5개
- persona 분배:
  - field_engineer 6개
  - energy_manager 6개
  - operator 7개
  - team_lead 6개
- question_type 분배:
  - short 15개: 공백 제외 5~20자
  - normal 7개: 공백 제외 8~40자
  - followup 3개: 이전 답변에 이어 묻는 듯한 짧은 후속질문
- difficulty 분배:
  - easy 10개
  - medium 10개
  - hard 5개

카테고리별 생성 방향:
1. anomaly
   - 사용자가 이상 경보를 보고 짧게 묻는 느낌
   - 예: "이상 또 떴어?", "이거 무시해도 돼?", "제일 심한 거 뭐야?"
   - severity, anomaly_type, timestamp 등 실제 제공값 사용

2. cms
   - 설비 상태, 작업지시, 헬스스코어에 대한 자연스러운 질문
   - 예: "CHP 괜찮아?", "작업지시서 왜 떴어?", "점검 가야 돼?"
   - work_orders title의 헬스스코어를 그대로 사용

3. report
   - 월간 사용량, 자급률, COP, grid_dependency_pct, pv_kwh, chp_kwh 중심
   - 예: "이번 달 전기 많이 썼어?", "자급률 왜 떨어졌어?", "외부 계통 전력 의존도 어때?"
   - monthly_report의 실제 period만 사용

4. rag
   - 계량기/설비/용어를 실제 운영자가 물어보는 느낌
   - 예: "V.Z84가 외부 계통 전력이야?", "H1.Z20 값 봐도 돼?"
   - 반드시 실제 meter_urn 또는 실제 measurement/value를 포함

5. forecast
   - 미래를 묻지만 실제 예측값을 만들지 말 것
   - 예: "다음 주 더 쓸까?", "이 추세 계속 갈까?", "앞으로 괜찮을까?"
   - 반드시 "현재 추세로 보면" 또는 "반복 패턴 기반으로 추정하면" 포함
   - 반드시 "실제 예측 모델 수치는 없으므로 참고용입니다" 포함
   - 구체적인 미래 예측 수치 금지

evidence 작성 규칙:
- answer에 사용한 모든 날짜·수치·건수의 원천 row를 명시
- anomaly 예:
  "anomaly_results: 2023-04-20 21:00 PowerSpike MEDIUM residual_w=76435"
- monthly_report 예:
  "monthly_report: 2023-12 total_consumption_kwh=12345 self_sufficiency_pct=63.11"
- meter 예:
  "cr_measurement_1h: H1.Z20 2023-01-15 U1=233.42"
- work_orders 예:
  "work_orders: equipment_id=chp title=헬스 64 · 최근 이상 98건"
- evidence에 없는 수치는 answer에 절대 사용하지 마세요.

중요:
- 질문에 "분석해 주세요", "진단해 주세요", "평가해 주세요", "상세히", "종합적으로" 같은 전문가식 문장을 넣지 마세요.
- 질문은 반드시 실제 사람이 급하게 챗봇에 치는 말투여야 합니다.
- answer는 짧되 근거 수치는 반드시 포함하세요.
- answer에서 "고장", "셧다운", "비정상적으로 정지", "완전 고장", "고장 확정" 표현을 쓰지 마세요.
- CHPOutage는 "운전 중단 가능성" 또는 "이상 후보"로만 표현하세요.
- 헬스스코어 64는 경고가 아니라 반드시 주의입니다.
- 단일 monthly_report 월만 보고 "증가 추세", "감소 추세", "줄어들 가능성"을 말하지 마세요.
- report는 기준 월의 상태를 설명하고, forecast만 추세 표현을 사용하세요.
- answer는 evidence에 있는 정보만 재구성하세요.
- evidence에 없는 비교, 추세, 빈도, 원인 분석을 만들지 마세요.
- "전월 대비", "이전 달보다", "증가 추세", "감소 추세", "높아졌습니다", "낮아졌습니다" 표현 금지
- "HIGH 등급이 많다", "HIGH 등급이 빈번하다", "고등급 이상" 표현 금지
"""

    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=16000,
        temperature=0.8,
    )

    return clean_json_text(response.choices[0].message.content)


# ============================================================
# 4. 1차 로컬 검수
# ============================================================

def validate_batch_distribution(qa_list):
    """25개 배치 단위 분배 검증."""
    errors = []

    checks = {
        "category": CATEGORY_DISTRIBUTION,
        "persona": PERSONA_DISTRIBUTION,
        "question_type": QUESTION_TYPE_DISTRIBUTION,
    }

    for field, expected in checks.items():
        actual = Counter(item.get(field, "UNKNOWN") for item in qa_list)
        for key, expected_count in expected.items():
            actual_count = actual.get(key, 0)
            if actual_count != expected_count:
                errors.append(
                    f"{field} 분배 오류: {key} expected={expected_count}, actual={actual_count}"
                )

        unexpected = set(actual.keys()) - set(expected.keys())
        if unexpected:
            errors.append(f"{field} 예상 외 값 존재: {sorted(unexpected)}")

    return errors


def local_validate_chat_qa(qa_list):
    failed = []

    for i, qa in enumerate(qa_list):
        reasons = []

        question = qa.get("question", "")
        answer = qa.get("answer", "")
        category = qa.get("category", "")
        persona = qa.get("persona", "")
        question_type = qa.get("question_type", "")
        evidence = qa.get("evidence", [])

        full_text = f"{question}\n{answer}"

        if not question:
            reasons.append("question 없음")

        if not answer:
            reasons.append("answer 없음")

        if category not in CATEGORY_DISTRIBUTION:
            reasons.append(f"category 오류: {category}")

        if persona not in PERSONA_DISTRIBUTION:
            reasons.append(f"persona 오류: {persona}")

        if question_type not in QUESTION_TYPE_DISTRIBUTION:
            reasons.append(f"question_type 오류: {question_type}")

        # forecast는 실제 예측값을 만들지 않는 자연어 질문이 많으므로 evidence 없음은 허용합니다.
        # 단, forecast answer에 수치가 들어가면 evidence가 있어야 합니다.
        if category != "forecast":
            if not isinstance(evidence, list) or len(evidence) == 0:
                reasons.append("evidence 없음")
        else:
            has_number_in_answer = bool(re.search(r"\d", answer))
            if has_number_in_answer and (not isinstance(evidence, list) or len(evidence) == 0):
                reasons.append("forecast 수치 사용 시 evidence 필요")

        for bad in BAD_QUESTION_PATTERNS:
            if bad in question:
                reasons.append(f"전문가식 질문 패턴 포함: {bad}")

        for word in FORBIDDEN_WORDS:
            if word in full_text:
                reasons.append(f"금지어 포함: {word}")

        if "MWh" in full_text:
            reasons.append("MWh 단위 사용")

        if category == "forecast":
            if ("현재 추세로 보면" not in answer) and ("반복 패턴 기반으로 추정하면" not in answer):
                reasons.append("forecast 필수 표현 누락")
            if "실제 예측 모델 수치는 없으므로 참고용입니다" not in answer:
                reasons.append("forecast 참고 문구 누락")

        if category == "report":
            risky_trend_words = [
                "전월 대비",
                "이전 달보다",
                "증가 추세",
                "감소 추세",
                "줄어들 가능성",
                "늘어날 가능성",
                "높아졌습니다",
                "낮아졌습니다",
                "큰 변화는 없을 것으로 예상",
            ]

            for word in risky_trend_words:
                if word in answer:
                    reasons.append(
                        f"report에서 근거 약한 추세 표현 포함: {word}"
                    )

        if "V.Z84" in full_text:
            bad_vz84_contexts = [
                "외부 계통 계량기",
                "grid_P 계량기",
                "그리드 인입",
                "외부 계통 전력 계량기",
            ]

            for bad in bad_vz84_contexts:
                if bad in full_text:
                    reasons.append(f"V.Z84 설명 오류: {bad}")

        for bad in ANSWER_FORBIDDEN_PATTERNS:
            if bad in answer:
                reasons.append(
                    f"answer 고장 확정/금지 표현 포함: {bad}"
                )

        # Chat QA는 짧은 실제 사용자 말투가 핵심이므로 길이 검증은 완화합니다.
        compact_len = len(re.sub(r"\s+", "", question))
        if not (3 <= compact_len <= 45):
            reasons.append(f"질문 길이 범위 오류: {compact_len}자")

        if question_type == "followup" and not (3 <= compact_len <= 30):
            reasons.append(f"followup 길이 오류: {compact_len}자")

        if reasons:
            failed.append({"index": i, "reasons": reasons})

    return failed


# ============================================================
# 5. GPT 검수
# ============================================================

def validate_chat_qa_with_gpt(qa_list):
    """GPT 기반 QA 검증. 실패한 항목 인덱스 반환."""

    system_prompt = """당신은 Honda R&D Europe EMS 챗봇용 Chat QA 데이터셋 검수자입니다.

아래 규칙을 위반한 항목을 찾아내세요.

검증 규칙:
1. 질문이 실제 사용자 말투가 아니면 FAIL입니다.
   - FAIL 예: "분석해 주세요", "진단해 주세요", "상세히 설명해 주세요", "종합적으로 평가해 주세요"
   - PASS 예: "CHP 괜찮아?", "이거 무시해도 돼?", "이번 달 많이 썼어?"

2. 금지어가 있으면 FAIL입니다.
   - 한전, 수전량, 수전 전력, 수전전력, 계통수전, MWh
   - 단, "외부 계통 전력"과 "grid_P"는 권장 표현이므로 절대 FAIL 처리하지 마세요.

3. answer에 고장 확정 표현이 있으면 FAIL입니다.
   - 비계획적 셧다운, 셧다운, 비정상적으로 정지, 고장 확정, 완전 고장, 고장입니다, 고장
   - 단, question에서 사용자가 "고장?"이라고 묻는 것은 허용합니다.
   - CHPOutage는 "운전 중단 가능성" 또는 "이상 후보"로만 표현해야 합니다.

4. evidence가 비어 있으면 FAIL입니다.
   - 단, forecast 카테고리는 evidence가 비어 있어도 PASS입니다.
   - forecast라도 answer에 구체 수치가 있으면 evidence가 필요합니다.

5. DB에 없는 것처럼 보이는 구체 수치를 answer에서 단정적으로 만들면 FAIL입니다.
   - 단, evidence에 같은 수치가 있으면 PASS입니다.
   - 단일 월 evidence만 있는데 report 답변에서 "전월 대비", "이전 달보다", "증가 추세", "감소 추세", "줄어들 가능성", "높아졌습니다", "낮아졌습니다"를 말하면 FAIL입니다.
   - evidence에 없는 HIGH/MEDIUM/LOW 빈도 표현은 FAIL입니다. 예: "HIGH 등급이 빈번합니다", "고등급 이상이 많습니다".

6. forecast 항목은 반드시 아래 표현 중 하나가 있어야 합니다.
   - 현재 추세로 보면
   - 반복 패턴 기반으로 추정하면
   그리고 반드시 아래 문구가 있어야 합니다.
   - 실제 예측 모델 수치는 없으므로 참고용입니다

7. V.Z84를 외부 계통 전력 계량기 또는 grid_P 계량기로 설명하면 FAIL입니다.
   V.Z84는 PV 생산 계량기입니다.
   단, V.Z84의 P/P1/P2/P3/PF1/PF2/PF3/U1/U2/U3/I1/I2/I3/W/W_in/W_out 측정값을 묻거나 설명하는 것은 PASS입니다.

8. 헬스스코어 해석 오류는 FAIL입니다.
   - 85 이상 정상
   - 60~84 주의
   - 60 미만 경고

출력 형식:
순수 JSON 배열만 출력하세요.
[
  {"index": 0, "result": "PASS"},
  {"index": 1, "result": "FAIL", "reason": "질문이 전문가식 문장입니다"}
]
마크다운 금지.
"""

    user_prompt = json.dumps(
        [
            {
                "index": i,
                "question": qa.get("question", ""),
                "answer": qa.get("answer", ""),
                "category": qa.get("category", ""),
                "persona": qa.get("persona", ""),
                "question_type": qa.get("question_type", ""),
                "evidence": qa.get("evidence", []),
            }
            for i, qa in enumerate(qa_list)
        ],
        ensure_ascii=False,
    )

    response = client.chat.completions.create(
        model=os.getenv("OPENAI_VALIDATOR_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=5000,
        temperature=0,
    )

    result = clean_json_text(response.choices[0].message.content)

    failed = []
    try:
        verdicts = json.loads(result)
        for v in verdicts:
            if v.get("result") == "FAIL":
                idx = v.get("index")
                reason = v.get("reason", "")
                if isinstance(idx, int) and 0 <= idx < len(qa_list):
                    print(f"  [GPT FAIL] #{idx + 1} {reason}")
                    print(f"             질문: {qa_list[idx].get('question', '')[:80]}")
                    failed.append(idx)
    except json.JSONDecodeError as e:
        print(f"  [검증 파싱 실패] {e} — GPT 검증은 건너뜁니다.")

    return failed


# ============================================================
# 6. 저장 및 통계
# ============================================================

def add_ids(qa_list, batch_num):
    for idx, qa in enumerate(qa_list, start=1):
        qa["id"] = f"chat_{batch_num:02d}_{idx:03d}"
    return qa_list


def print_stats(qa_list):
    print("  [통계]")
    for key in ["category", "persona", "question_type", "difficulty"]:
        counter = Counter(item.get(key, "UNKNOWN") for item in qa_list)
        print(f"   - {key}: {dict(counter)}")


def save_outputs(all_items, output_dir):
    json_path = output_dir / "chat_qa_dataset.json"
    jsonl_path = output_dir / "chat_qa_dataset.jsonl"

    json_path.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")

    with jsonl_path.open("w", encoding="utf-8") as f:
        for item in all_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"\n전체 저장 완료:")
    print(f"- JSON : {json_path}")
    print(f"- JSONL: {jsonl_path}")


# ============================================================
# 7. main
# ============================================================

def main():
    session_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 25개씩 4배치 = 100개
    # 필요하면 CHAT_QA_BATCHES 환경변수로 조정 가능
    total_batches = int(os.getenv("CHAT_QA_BATCHES", "4"))

    output_dir = Path(f"scripts/outputs/chat_qa/{session_timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    all_items = []
    prev_questions = []

    print(f"출력 폴더: {output_dir}")

    for batch_num in range(1, total_batches + 1):
        print(f"\nDB에서 데이터 새로 뽑는 중... (배치 {batch_num}/{total_batches})")
        anomalies, monthly, work_orders, meters = fetch_data()

        print("도메인 지식 로드 중...")
        domain_knowledge = load_domain_knowledge()

        print(f"GPT로 Chat QA 생성 중... (배치 {batch_num}/{total_batches})")
        result = generate_chat_qa(
            anomalies=anomalies,
            monthly=monthly,
            work_orders=work_orders,
            meters=meters,
            domain_knowledge=domain_knowledge,
            batch_num=batch_num,
            total_batches=total_batches,
            prev_questions=prev_questions,
        )

        raw_path = output_dir / f"raw_batch{batch_num}.txt"
        raw_path.write_text(result, encoding="utf-8")

        try:
            qa_list = json.loads(result)
        except json.JSONDecodeError as e:
            print(f"JSON 파싱 실패 (배치 {batch_num}): {e}")
            print(f"raw 결과 저장: {raw_path}")
            continue

        qa_list = add_ids(qa_list, batch_num)

        print("배치 분배 검증 중...")
        distribution_errors = validate_batch_distribution(qa_list)
        if distribution_errors:
            print("  [분배 오류]")
            for err in distribution_errors:
                print(f"       - {err}")
            print("  ※ 분배 오류는 경고입니다. Chat QA 품질 검증은 계속 진행합니다.")

        print("로컬 검증 중...")
        local_failed = local_validate_chat_qa(qa_list)
        local_failed_idx = set()
        for item in local_failed:
            idx = item["index"]
            local_failed_idx.add(idx)
            print(f"  [LOCAL FAIL] #{idx + 1} / {qa_list[idx].get('question', '')}")
            for reason in item["reasons"]:
                print(f"       - {reason}")

        print("GPT 검증 생략...")
        gpt_failed_idx = set()
        failed_idx = local_failed_idx | gpt_failed_idx

        if failed_idx:
            print(f"  → {len(failed_idx)}개 항목 제거.")
            qa_list = [qa for i, qa in enumerate(qa_list) if i not in failed_idx]
        else:
            print("  → 전체 통과.")

        print_stats(qa_list)

        batch_path = output_dir / f"batch{batch_num}.json"
        batch_path.write_text(json.dumps(qa_list, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"배치 저장 완료. {len(qa_list)}개 저장: {batch_path}")

        all_items.extend(qa_list)
        prev_questions.extend([item.get("question", "") for item in qa_list])

        # API 과부하 방지
        time.sleep(1)

    save_outputs(all_items, output_dir)
    print(f"\n최종 생성 수: {len(all_items)}개")


if __name__ == "__main__":
    main()
