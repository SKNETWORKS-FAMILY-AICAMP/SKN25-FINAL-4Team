# Router Accuracy Eval — 팀원 500문항 기반 의도 분류기 정확도

> 평가 시각(UTC): `2026-06-11T07:59:01.356409+00:00`  | 문항 수: 500  | 규칙 히트율: 36.8%  | LLM 폴백: OFF (기본값=rag)

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
| accuracy | **50.6%** |
| macro_f1 | **55.0%** |
| correct  | 253 / 500 |

## Per-route

| route | support | predicted | correct | precision | recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| anomaly | 100 | 39 | 39 | 100.0% | 39.0% | 56.1% |
| off_topic | 100 | 64 | 64 | 100.0% | 64.0% | 78.0% |
| rag | 100 | 316 | 100 | 31.6% | 100.0% | 48.1% |
| report | 200 | 66 | 50 | 75.8% | 25.0% | 37.6% |

## Confusion matrix

rows=expected, columns=predicted

| expected \ predicted | anomaly | off_topic | rag | report |
|---|---:|---:|---:|---:|
| anomaly | 39 | 0 | 30 | 16 |
| off_topic | 0 | 64 | 36 | 0 |
| rag | 0 | 0 | 100 | 0 |
| report | 0 | 0 | 150 | 50 |

## 오분류 샘플 (최대 30건)

| # | 질문 | teammate_route | expected | predicted | method |
|---|---|---|---|---|---|
| 1 | 2025년 1~2분기 이상 탐지 패턴 비교로 설비 건전성 악화 신호를 진단해주세요. | evidence_answer | anomaly | rag | default |
| 2 | 최근 12개월 모든 이상 유형 종합하여 리스크 우선순위 평가 결과는? | evidence_answer | anomaly | rag | default |
| 3 | 2025년 3월 월간 에너지 리포트의 COP 값은 얼마인가요? | evidence_answer | anomaly | report | rule |
| 4 | 지난달 자가소비율은 몇 퍼센트였나요? | evidence_answer | anomaly | rag | default |
| 5 | 2025년 4월 계통의존도 수치를 알려주세요. | evidence_answer | anomaly | report | rule |
| 6 | 2025년 1월 총소비전력량은 몇 kWh였나요? | evidence_answer | anomaly | rag | default |
| 7 | 2025년 5월 월간 리포트의 냉방에너지 소비량은 얼마인가요? | evidence_answer | anomaly | report | rule |
| 8 | 2025년 3월과 4월의 COP 값을 비교하여 추세를 분석해주세요. | evidence_answer | anomaly | forecast | rule |
| 9 | 최근 3개월간 자가소비율의 월별 변화 추이와 증감 요인은 무엇인가요? | evidence_answer | anomaly | report | rule |
| 10 | 2025년 1분기 계통의존도 평균값과 전년 동기 대비 차이를 분석해주세요. | evidence_answer | anomaly | report | rule |
| 11 | 2025년 4월 총소비전력의 전월 대비 증감률과 주요 변동 요인을 알려주세요. | evidence_answer | anomaly | rag | default |
| 12 | 2025년 5월 월간 리포트에서 COP, 자가소비율, 계통의존도를 종합 분석해주세요. | evidence_answer | anomaly | report | rule |
| 13 | 2025년 2월과 3월의 난방에너지 소비량 비교 및 증감 원인 분석 결과는? | evidence_answer | anomaly | rag | default |
| 14 | 2025년 4월 COP가 전월 대비 하락한 원인을 월간 리포트 기반으로 분석해주세요. | evidence_answer | anomaly | report | rule |
| 15 | 2025년 3월 자가소비율 80% 달성의 주요 기여 요인은 무엇인가요? | evidence_answer | anomaly | rag | default |
| 16 | 2025년 1분기 총소비전력의 월별 추세와 계절적 요인 분석 결과를 보여주세요. | evidence_answer | anomaly | forecast | rule |
| 17 | 2025년 5월 계통의존도가 전월 대비 상승한 원인과 대응 방안은 무엇인가요? | evidence_answer | anomaly | report | rule |
| 18 | 2025년 4월 냉방에너지 소비량이 전년 동월 대비 변화한 원인을 분석해주세요. | evidence_answer | anomaly | rag | default |
| 19 | 최근 6개월간 평균 COP 추이와 설비 효율 저하 신호 여부를 평가해주세요. | evidence_answer | anomaly | report | rule |
| 20 | 2025년 3월 자가소비율과 계통의존도의 상관관계 분석 결과를 알려주세요. | evidence_answer | anomaly | report | rule |
| 21 | 2025년 2월 월간 리포트에서 난방에너지가 총소비전력에서 차지하는 비중은? | evidence_answer | anomaly | report | rule |
| 22 | 2025년 5월 COP, 자가소비율, 총소비전력을 전월과 비교하여 종합 평가해주세요. | evidence_answer | anomaly | rag | default |
| 23 | 2025년 1분기 월간 리포트의 핵심 성과 지표(COP, 자가소비율, 계통의존도) 요약은? | evidence_answer | anomaly | report | rule |
| 24 | 2025년 4월 총소비전력 증가의 주요 원인을 시간대별 소비 패턴으로 분석해주세요. | evidence_answer | anomaly | rag | default |
| 25 | 2025년 3월 냉방에너지와 난방에너지 소비 비율 및 계절 전환기 특성 분석 결과는? | evidence_answer | anomaly | rag | default |
| 26 | 2025년 5월 자가소비율 목표 대비 달성률과 미달성 시 원인 분석을 해주세요. | evidence_answer | anomaly | rag | default |
| 27 | 최근 3개월간 계통의존도 감소 추세의 지속 가능성에 대한 분석 결과는? | evidence_answer | anomaly | report | rule |
| 28 | 2025년 4월 COP의 설비별 기여도를 분석하여 효율 개선 포인트를 도출해주세요. | evidence_answer | anomaly | rag | default |
| 29 | 2025년 2월과 3월의 총소비전력 차이(diff)와 주요 변동 항목을 분석해주세요. | evidence_answer | anomaly | rag | default |
| 30 | 2025년 1분기 대비 2분기 자가소비율 변화 전망과 근거 데이터를 제시해주세요. | evidence_answer | anomaly | forecast | rule |

## 해석 노트

- `cms` 라우트는 데이터셋에 없음 — support=0이므로 매크로 F1 계산에서 제외.
- `report_shell`(100건)은 `context.qa_blocked=True` 의존. 메시지 텍스트만으로 분류 시 어려움.
- `approval_required`(100건)은 '운영 테이블 변경·서버 파일 덮어쓰기' → `off_topic` 매핑.
- LLM 폴백 OFF 시 규칙 미분류 항목은 `rag` 기본값 처리.
