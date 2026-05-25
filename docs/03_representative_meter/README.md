# 03_representative_meter/

계량기 간 상관관계 기반 군집화 및 대표 계량기 선정.

| 파일 | 내용 |
|---|---|
| `OVERALL_REPRESENTATIVE_METER_SELECTION.md` | 전기 계량기 군집화 원본. 배전 포함 소비 64개, 18개 군집, 최종 37개 그룹 |
| `OVERALL_REPRESENTATIVE_METER_SELECTION.html` | 위 md의 HTML 버전 |
| `OVERALL_REPRESENTATIVE_METER_SELECTION_filtered.html` | 배전 17개 제외 후 최종 대표 계량기 목록. 소비 31개 그룹, 발전 2개 그룹 |
| `METER_DATA_PERIOD_filtered.html` | 배전 제외 계량기별 DB 수집 기간 및 레코드 수. 군집 내 최다/최소 데이터 계량기 정리 |
| `OVERALL_REPRESENTATIVE_PAIRWISE_DETAILS.html` | 군집 내 계량기 pair별 상관계수 상세. 배전 제외 적용 후 잔존 pair 검증 |
| `REDUNDANT_PAIR_COVERAGE.html` | ZE 시리즈 등 중복 pair의 데이터 수집 기간 비교. 원본과 ZE 계열 간 기간 불일치 확인용 |
| `THERMAL_CLUSTERING_SUMMARY.html` | 열량계 군집화 요약. overall/P 기준, 임계값 0.6/0.7 조합별 결과 비교 |
| `GROUPING_STRATEGY_DRAFT.md` | 군집 모델 전략 초안. B방식(군집 단일 모델) vs C방식(파라미터 공유) 적용 기준 |
