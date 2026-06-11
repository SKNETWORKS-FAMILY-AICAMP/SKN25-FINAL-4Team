# Router Accuracy Eval — 팀원 500문항 기반 의도 분류기 정확도

> 평가 시각(UTC): `2026-06-11T07:51:10.003796+00:00`  | 문항 수: 500  | 규칙 히트율: 34.8%  | LLM 폴백: OFF (기본값=rag)

## 라우트 매핑

| 팀원 라우트 | 우리 라우트 |
|---|---|
| `quick_answer` | `rag` |
| `evidence_answer` | `anomaly` |
| `needs_job` | `report` |
| `approval_required` | `off_topic` |
| `report_shell` | `report` |

## Overall

| metric | value |
|---|---:|
| accuracy | **48.6%** |
| macro_f1 | **51.9%** |
| correct  | 243 / 500 |

## Per-route

| route | support | predicted | correct | precision | recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| anomaly | 100 | 29 | 29 | 100.0% | 29.0% | 45.0% |
| off_topic | 100 | 64 | 64 | 100.0% | 64.0% | 78.0% |
| rag | 100 | 326 | 100 | 30.7% | 100.0% | 46.9% |
| report | 200 | 66 | 50 | 75.8% | 25.0% | 37.6% |

## Confusion matrix

rows=expected, columns=predicted

| expected \ predicted | anomaly | off_topic | rag | report |
|---|---:|---:|---:|---:|
| anomaly | 29 | 0 | 40 | 16 |
| off_topic | 0 | 64 | 36 | 0 |
| rag | 0 | 0 | 100 | 0 |
| report | 0 | 0 | 150 | 50 |

## 오분류 샘플 (최대 30건)

| # | 질문 | teammate_route | expected | predicted | method |
|---|---|---|---|---|---|
| 1 | 2025년 4월 COP 저하 이상과 냉매 누설 이상을 비교 분석한 결과는? | evidence_answer | anomaly | rag | default |
| 2 | 2025년 5월 압력 변동 이상 중 Critical 등급 건수와 상세 내역은? | evidence_answer | anomaly | rag | default |
| 3 | 2025년 1분기 전압 불균형 이상 탐지 결과의 월별 추세는 어떻게 되나요? | evidence_answer | anomaly | rag | default |
| 4 | 2025년 3월 전력 급증 이상의 발생 시간대별 분포를 분석해주세요. | evidence_answer | anomaly | rag | default |
| 5 | 최근 3개월간 소음 증가 이상 탐지 건수의 증감 추세 분석 결과는? | evidence_answer | anomaly | rag | default |
| 6 | 2025년 1월부터 5월까지 진동 과다 이상의 월별 발생 패턴을 분석해주세요. | evidence_answer | anomaly | rag | default |
| 7 | 최근 분기 압력 변동 이상과 전압 불균형 이상의 상관관계 분석 결과는? | evidence_answer | anomaly | rag | default |
| 8 | 2025년 3월 유량 감소 이상과 진동 과다 이상의 동시 발생 사례 분석 결과는? | evidence_answer | anomaly | rag | default |
| 9 | 2025년 3월 온도 이상과 압력 변동 이상 간의 교차 분석 결과를 보여주세요. | evidence_answer | anomaly | rag | default |
| 10 | 최근 3개월간 냉매 누설 이상의 심각도가 Critical인 건의 발생 간격 분석은? | evidence_answer | anomaly | rag | default |
| 11 | 2025년 1~2분기 이상 탐지 패턴 비교로 설비 건전성 악화 신호를 진단해주세요. | evidence_answer | anomaly | rag | default |
| 12 | 최근 12개월 모든 이상 유형 종합하여 리스크 우선순위 평가 결과는? | evidence_answer | anomaly | rag | default |
| 13 | 2025년 3월 월간 에너지 리포트의 COP 값은 얼마인가요? | evidence_answer | anomaly | report | rule |
| 14 | 지난달 자가소비율은 몇 퍼센트였나요? | evidence_answer | anomaly | rag | default |
| 15 | 2025년 4월 계통의존도 수치를 알려주세요. | evidence_answer | anomaly | report | rule |
| 16 | 2025년 1월 총소비전력량은 몇 kWh였나요? | evidence_answer | anomaly | rag | default |
| 17 | 2025년 5월 월간 리포트의 냉방에너지 소비량은 얼마인가요? | evidence_answer | anomaly | report | rule |
| 18 | 2025년 3월과 4월의 COP 값을 비교하여 추세를 분석해주세요. | evidence_answer | anomaly | forecast | rule |
| 19 | 최근 3개월간 자가소비율의 월별 변화 추이와 증감 요인은 무엇인가요? | evidence_answer | anomaly | report | rule |
| 20 | 2025년 1분기 계통의존도 평균값과 전년 동기 대비 차이를 분석해주세요. | evidence_answer | anomaly | report | rule |
| 21 | 2025년 4월 총소비전력의 전월 대비 증감률과 주요 변동 요인을 알려주세요. | evidence_answer | anomaly | rag | default |
| 22 | 2025년 5월 월간 리포트에서 COP, 자가소비율, 계통의존도를 종합 분석해주세요. | evidence_answer | anomaly | report | rule |
| 23 | 2025년 2월과 3월의 난방에너지 소비량 비교 및 증감 원인 분석 결과는? | evidence_answer | anomaly | rag | default |
| 24 | 2025년 4월 COP가 전월 대비 하락한 원인을 월간 리포트 기반으로 분석해주세요. | evidence_answer | anomaly | report | rule |
| 25 | 2025년 3월 자가소비율 80% 달성의 주요 기여 요인은 무엇인가요? | evidence_answer | anomaly | rag | default |
| 26 | 2025년 1분기 총소비전력의 월별 추세와 계절적 요인 분석 결과를 보여주세요. | evidence_answer | anomaly | forecast | rule |
| 27 | 2025년 5월 계통의존도가 전월 대비 상승한 원인과 대응 방안은 무엇인가요? | evidence_answer | anomaly | report | rule |
| 28 | 2025년 4월 냉방에너지 소비량이 전년 동월 대비 변화한 원인을 분석해주세요. | evidence_answer | anomaly | rag | default |
| 29 | 최근 6개월간 평균 COP 추이와 설비 효율 저하 신호 여부를 평가해주세요. | evidence_answer | anomaly | report | rule |
| 30 | 2025년 3월 자가소비율과 계통의존도의 상관관계 분석 결과를 알려주세요. | evidence_answer | anomaly | report | rule |

## 해석 노트

- `cms` 라우트는 데이터셋에 없음 — support=0이므로 매크로 F1 계산에서 제외.
- `report_shell`(100건)은 `context.qa_blocked=True` 의존. 메시지 텍스트만으로 분류 시 어려움.
- `approval_required`(100건)은 '운영 테이블 변경·서버 파일 덮어쓰기' → `off_topic` 매핑.
- LLM 폴백 OFF 시 규칙 미분류 항목은 `rag` 기본값 처리.
