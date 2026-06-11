import os
import json
import psycopg2
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime

load_dotenv(Path(__file__).parent.parent / ".env")

conn = psycopg2.connect(
    host=os.getenv("DB_HOST"),
    port=os.getenv("DB_PORT"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    dbname=os.getenv("DB_NAME"),
)

client = OpenAI(api_key=os.getenv("OPEN_API_KEY"))

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

    cur.execute("SELECT period, total_consumption_kwh, self_sufficiency_pct, avg_cop, anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh FROM public.monthly_report ORDER BY period;")
    monthly = cur.fetchall()

    cur.execute("""
        SELECT equipment_id, equipment_name, title, cause, action, status
        FROM public.work_orders
        ORDER BY created_at DESC LIMIT 5;
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
        start = content.find(f'{var_name} = """') + len(f'{var_name} = """')
        end = content.find('"""', start)
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

def generate_qa(anomalies, monthly, work_orders, meters, domain_knowledge, batch_num, prev_questions=None):
    prev_block = ""
    if prev_questions:
        prev_block = "\n\n[이미 만든 질문 목록 — 중복 금지]\n" + "\n".join(f"- {q}" for q in prev_questions)

    system_prompt = f"""당신은 Honda R&D Europe GmbH 에너지 관리 시스템(EMS) 전문가입니다.
아래 도메인 지식을 완전히 숙지하고 있습니다.

{domain_knowledge}

절대 규칙 (위반 시 전체 답변 무효):
1. 시설은 독일 Offenbach, 전력망은 독일 공공 전력망
2. 한전/수전량/수전 전력/계통수전 사용 금지 → 반드시 "외부 계통 전력" 또는 "grid_P" 사용
3. 고장 확정 표현 금지 → "이상 후보", "추정 원인", "점검 권고", "가능성이 있습니다"로만 표현
4. 제공된 DB 데이터에 없는 수치 절대 생성 금지 → 없는 값은 만들지 말 것
5. 계산값은 반드시 "제공값으로 계산하면"이라고 명시
6. 전체 answer 존댓말(~입니다, ~합니다)로만 작성
7. 단순 DB 조회 질문 금지 → 반드시 도메인 해석/판단이 필요한 질문만 작성
8. forecast는 반드시 제공된 DB 수치 기반 반복 패턴 또는 월간 추세로만 구성
9. V.Z84는 PV 생산 계량기임 → "외부 계통 전력 계량기", "그리드 인입", "수전" 표현 절대 금지. V.Z84에는 PF, Q, WQ 측정값이 없음 → PF1/PF2/PF3만 사용
10. 전력 소비량 단위는 반드시 kWh → MWh 사용 절대 금지
11. forecast에서 구체적 % 수치 또는 kW 수치 생성 금지 → "현재 추세로 보면" 수준으로만 표현
12. 플레이스홀더 절대 금지 → HIGH: X, MEDIUM: Y, N건, 다수 건, X건 등 반드시 실제 수치로 대체
13. anomaly/rag/cms 답변에서 날짜·잔차·센서값은 반드시 제공된 DB 데이터에 실제로 존재하는 값만 사용 → 그럴듯한 날짜나 수치 생성 금지. 근거가 없으면 "대표 이벤트 기준"이라고만 표현
14. report 답변은 반드시 제공된 monthly_report에 포함된 기간(period)만 사용 → 제공되지 않은 연도/월 생성 금지
15. CHPOutage 이상은 "운전 중단 가능성" 또는 "이상 후보" 수준으로만 표현 → "비계획적 셧다운", "비정상적으로 정지" 등 확정 표현 금지
16. 각 QA의 answer에 사용한 수치·날짜의 원천을 evidence 필드에 반드시 명시
17. anomaly MEDIUM/LOW 등급은 actual_w/predicted_w/residual_w가 NULL일 수 있음 → 잔차 수치가 제공된 경우에만 사용. 없으면 잔차 언급 금지
18. anomaly_type은 7종만 존재 → PowerSpike, Unknown, COPDrop, CHPOutage, PVNightNonZero, ResidualSpike, NightConsumption. 이 외의 유형 생성 금지
19. work_orders의 헬스스코어는 title 컬럼에 "헬스 64 · 최근 이상 98건" 형태로 존재 → 반드시 이 값을 그대로 사용. 임의로 health_score 수치 생성 금지

## 헬스 스코어 기준 (CMS 설비 판단 시 반드시 적용)
- 85 이상: 정상
- 60~84: 주의 (모니터링 강화 필요)
- 60 미만: 경고 (즉각 점검 필요)

## anomaly 실제 답변 예시 (형식만 참고. 수치는 예시이며 실제 DB 수치가 아님)

질문: 최근 이상탐지 원인 분석해줘

답변:
### 🚨 핵심 요약
최근 20건의 이상탐지 중 HIGH 1건, MEDIUM 19건이 보고되었습니다. 대부분 PVNightNonZero와 PowerSpike 유형이며, PVNightNonZero가 가장 주의가 필요합니다.

### 🔍 유형별 분석
**[PVNightNonZero] 10건 (HIGH: 1 / MEDIUM: 9)**
- 대표 시각: [반드시 제공된 anomaly_results의 실제 timestamp 사용]
- 센서값: 야간 PV 출력이 비정상적으로 0 초과
- 추정 원인: 인버터 오프셋 또는 센서 오류 가능성
- ⬇ 일시적인 가능성 있음

**[PowerSpike] 10건 (HIGH: 0 / MEDIUM: 10)**
- 대표 시각: [반드시 제공된 anomaly_results의 실제 timestamp 사용]
- 센서값: 변압기 계통 전력 급증
- 추정 원인: 대형 설비 동시 기동에 의한 부하 급증 가능성
- ⬆ 악화 징조, 역률 불량 등이 지속될 경우 추가적 조치 필요

### ✅ 즉시 조치 목록
1. 기술팀에서 야간 PV 출력 문제를 해결하기 위해 인버터를 점검하고 필요한 경우 재시작 또는 센서 교정 수행.
2. 운영팀이 설비 스케줄을 확인하고 대형 설비의 동시 기동을 최소화하도록 조정.
3. 전기팀이 그리드 실측값과 피더별 전류를 확인하여 역률 개선 필요성 여부를 검토.

### 📋 배경 참고
이번 이상은 게이트웨이 장애 구간과 겹치지 않습니다.

---

## cms 실제 답변 예시 (형식만 참고. 수치는 예시이며 실제 DB 수치가 아님)

질문: 열병합발전 설비 진단해줘

답변:
### 🩺 진단 요약
현재 열병합발전 설비의 헬스 스코어는 [work_orders title에서 실제 값] → [정상/주의/경고] 등급입니다.

### 🔍 추정 원인
- 실측 전력값이 [실제 actual_w 범위]로 나타나며 예측값과 큰 차이가 발생 → CHPOutage 이상 후보
- [실제 측정 데이터 기반 관찰 사항]

### ✅ 권장 조치
1. chp_P와 chp_heat_P 동시 출력 상태 점검하여 운전 중단 가능성 확인
2. 가스 공급 상태 점검하여 연료 공급 이상 후보 여부 확인
3. 그리드 의존도 변동 여부 모니터링

---

## 카테고리별 답변 형식

### report 답변 형식
## 1. 핵심 요약
(3줄 이내)
## 2. KPI 분석
(소비량 단위 kWh, 자급률 %, COP — 반드시 전월 대비 또는 전년 동기 대비 수치 비교 포함. 단순 나열 금지)
## 3. 이상탐지 현황
(anomaly_count 기반)
## 4. 개선 권고사항
(구체적 수치 포함)

### forecast 답변 형식
- 현재 추세: monthly_report 또는 anomaly 발생 추세 기반 서술
- 위험 요인: 추세 지속 시 예상되는 문제
- 운영 영향: 설비/에너지 운영에 미치는 영향
- 권장 대응: 구체적 조치 방향
- 반드시 포함: "실제 예측 모델 수치는 없으므로 참고용입니다"
"""

    user_prompt = f"""아래 실제 DB 데이터를 바탕으로 질문-정답 25개를 만들어줘. (배치 {batch_num}/4)
{prev_block}

[anomaly_results]
컬럼: timestamp, anomaly_type, severity, description, actual_w, predicted_w, residual_w
주의: MEDIUM/LOW 등급은 actual_w/predicted_w/residual_w가 NULL인 경우가 많음. NULL이면 잔차 수치 사용 금지.
{anomalies}

[monthly_report]
컬럼: period, total_consumption_kwh, self_sufficiency_pct, avg_cop, anomaly_count, grid_dependency_pct, pv_kwh, chp_kwh
{monthly}

[work_orders]
컬럼: equipment_id, equipment_name, title, cause, action, status
헬스스코어는 title 컬럼에 "헬스 64 · 최근 이상 98건" 형태로 존재. cause 컬럼에 진단 내용 포함.
{work_orders}

[ems.cr_measurement_1h]
컬럼: ts, meter_urn, measurement, value
V.Z84 사용 가능한 measurement: I1, I2, I3, P, P1, P2, P3, PF1, PF2, PF3, U1, U2, U3, W, W_in, W_out
{meters}

출력 형식 (순수 JSON 배열):
{{"question": "...", "answer": "...", "category": "...", "difficulty": "easy/medium/hard", "evidence": ["출처1", "출처2"]}}

evidence 작성 규칙:
- answer에 사용한 모든 날짜·수치·건수의 원천 row를 명시
- anomaly: "anomaly_results: 2023-04-20 21:00 PowerSpike MEDIUM residual_w=76435"
- monthly_report: "monthly_report: 2023-12 self_sufficiency_pct=63.11"
- meter: "cr_measurement_1h: H1.Z20 2023-01-15 U1=233.42"
- work_orders: "work_orders: equipment_id=chp title=헬스 64 · 최근 이상 98건"
- evidence에 없는 수치는 answer에 절대 사용 금지

카테고리별 분배 (각 5개):
- anomaly: 5개 (이상탐지 원인 분석, 심각도 해석, 조치 판단) — anomaly 답변 예시 형식 사용. 날짜는 반드시 제공된 anomaly_results 실제 timestamp만 사용. MEDIUM/LOW에서 잔차가 NULL이면 잔차 언급 금지
- cms: 5개 (설비 진단, 작업지시 해석, 헬스스코어 판단) — cms 답변 예시 형식 사용. 반드시 헬스스코어 해석(정상/주의/경고) 포함. anomaly 조치 나열 금지. work_orders의 title/cause 내용 적극 활용
- report: 5개 (KPI 해석, 월간 추세 분석, 수치 비교) — report 답변 형식 사용. 반드시 전월 대비 또는 전년 동기 대비 비교 포함. 제공된 monthly_report 기간 외 월 사용 금지
- rag: 5개 — 반드시 실제 계량기 URN(H1.Z16, H1.Z20, V.Z84 등) 또는 실제 측정값을 포함한 운영 상황 기반 질문. 단순 정의 암기 문제 금지. 사용하는 센서값은 반드시 제공된 cr_measurement_1h 실제 값만 사용
- forecast: 5개 — 반드시 monthly_report 수치 또는 anomaly 발생 추세 기반. 계절 일반론 금지. 현재 추세/위험 요인/운영 영향/권장 대응 포함. 구체적 % 또는 kW 수치 생성 금지

조건:
- 직접 제공된 DB 수치만 사용. 없는 수치 절대 생성 금지
- 계산값은 "제공값으로 계산하면"이라고 명시
- 단순 DB 조회 질문 금지 → 반드시 해석/판단/분석이 필요한 질문
- 한국어, 존댓말로 작성
- difficulty 분배: easy 8개, medium 12개, hard 5개

순수 JSON 배열만 출력. ```json 마크다운 절대 금지. [ 로 시작해서 ] 로 끝나야 함.
"""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=16000,
    )

    result = response.choices[0].message.content.strip()
    if result.startswith("```"):
        result = result.split("\n", 1)[1]
    if result.endswith("```"):
        result = result.rsplit("```", 1)[0]
    return result.strip()


def validate_qa(qa_list):
    """GPT 기반 QA 검증. 실패한 항목 인덱스 반환."""

    system_prompt = """당신은 에너지 관리 시스템(EMS) QA 데이터셋 검수자입니다.
아래 규칙을 위반한 항목을 찾아내세요.

검증 규칙:
1. 플레이스홀더 금지 — HIGH: X, MEDIUM: Y, N건, 다수 건, X건 등 미완성 템플릿 표현
2. MWh 단위 금지 — "MWh"라는 단어가 전력 소비량 맥락에서 사용되면 위반. kW, kWh는 정상.
3. 고장 확정 표현 금지 — 비계획적 셧다운, 비정상적으로 정지, 고장 확정, 완전 고장
4. 헬스스코어 해석 오류 금지 — 60~84는 반드시 주의, 60 미만은 경고, 85 이상은 정상. 예: 64는 반드시 주의 등급. 64를 경고로 표현하면 위반.
5. evidence 없음 금지 — evidence 필드가 비어있거나 없으면 위반

각 항목에 대해 PASS 또는 FAIL과 위반 규칙 번호를 반환하세요.

출력 형식 (순수 JSON 배열):
[{"index": 0, "result": "PASS"}, {"index": 1, "result": "FAIL", "reason": "규칙 4 위반: 헬스스코어 64를 경고로 해석"}, ...]

순수 JSON 배열만 출력. 마크다운 금지.
"""

    user_prompt = json.dumps(
        [{"index": i, "question": qa.get("question", ""), "answer": qa.get("answer", ""), "evidence": qa.get("evidence", [])}
         for i, qa in enumerate(qa_list)],
        ensure_ascii=False
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=4000,
    )

    result = response.choices[0].message.content.strip()
    if result.startswith("```"):
        result = result.split("\n", 1)[1]
    if result.endswith("```"):
        result = result.rsplit("```", 1)[0]

    failed = []
    try:
        verdicts = json.loads(result)
        for v in verdicts:
            if v.get("result") == "FAIL":
                idx = v.get("index")
                reason = v.get("reason", "")
                print(f"  [FAIL] #{idx+1} {reason}")
                print(f"         질문: {qa_list[idx]['question'][:50]}")
                failed.append(idx)
    except json.JSONDecodeError as e:
        print(f"  [검증 파싱 실패] {e} — 검증 건너뜀")

    return failed


def main():
    session_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prev_questions = []

    for batch_num in range(1, 5):
        print(f"DB에서 데이터 새로 뽑는 중... (배치 {batch_num}/4)")
        anomalies, monthly, work_orders, meters = fetch_data()

        print("도메인 지식 로드 중...")
        domain_knowledge = load_domain_knowledge()

        print(f"GPT로 질문-정답 생성 중... (배치 {batch_num}/4)")
        result = generate_qa(anomalies, monthly, work_orders, meters, domain_knowledge, batch_num, prev_questions)

        try:
            import re
            result = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', result)
            qa_list = json.loads(result)

            print("검증 중...")
            failed = validate_qa(qa_list)
            if failed:
                print(f"  → {len(failed)}개 항목 제거.")
                qa_list = [qa for i, qa in enumerate(qa_list) if i not in failed]
            else:
                print("  → 전체 통과.")

            output_path = Path(f"scripts/qa_dataset_{session_timestamp}_batch{batch_num}.json")
            output_path.write_text(json.dumps(qa_list, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"완료. {len(qa_list)}개 저장: {output_path}")
            prev_questions = [item["question"] for item in qa_list]

        except json.JSONDecodeError as e:
            print(f"JSON 파싱 실패 (배치 {batch_num}): {e}")
            raw_path = Path(f"scripts/qa_dataset_raw_{session_timestamp}_batch{batch_num}.txt")
            raw_path.write_text(result, encoding="utf-8")
            print(f"raw 결과 저장: {raw_path}")


if __name__ == "__main__":
    main()