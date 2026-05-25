# eda/correlation/

계량기 간 상관관계 분석. feature 내 군집화와 별도로, 계량기 간 전체 상관관계를 시각화.

| 파일 | 내용 |
|---|---|
| `eda_raw_correlation_representative_electric.py` | 전기 대표 계량기(P, U1, PF) 기준 계량기 간 상관 heatmap static PNG 생성. `fetch_meter_raw_df` 제공 |
| `eda_raw_correlation_all_thermal.py` | 열량계 전체 상관 heatmap static PNG 생성 |
| `eda_plotly_corr_dashboard_electric.py` | 전기 계량기 상관관계 Plotly 인터랙티브 대시보드 |
| `eda_plotly_corr_dashboard_thermal.py` | 열량계 상관관계 Plotly 인터랙티브 대시보드 |
| `eda_correlation_electric_representative_plotly.py` | 전기 대표 계량기 선정용 상관 Plotly 대시보드 (군집화 분석 산출물 생성) |
| `eda_correlation_thermal_relationship_plotly.py` | 열량계 상위/하위 관계, 미가동 계량기 파악용 관계 분석 Plotly 대시보드 |
