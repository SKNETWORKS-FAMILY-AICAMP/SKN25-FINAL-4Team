# eda/stl/

STL 시계열 분해 (Trend / Seasonal / Residual).

| 파일 | 내용 |
|---|---|
| `eda_stl_electric.py` | 전기 계량기 STL static PNG 생성. `FEATURE_UNITS`, `build_input_series`, `meter_config` 등 STL 공통 유틸 제공 |
| `eda_stl_heat.py` | 열량계 STL static PNG 생성. `eda_stl_electric`의 유틸을 재사용 |
| `eda_plotly_stl_dashboard_electric.py` | 전기 계량기 STL Plotly 인터랙티브 대시보드 생성. `generate_dashboard` 함수 제공 |
| `eda_plotly_stl_dashboard_thermal.py` | 열량계 STL Plotly 대시보드 생성. `generate_dashboard`를 열량계용으로 재사용 |
| `eda_plotly_stl_homoscedasticity_dashboard.py` | STL residual 등분산성 검정(Breusch-Pagan) Plotly 대시보드 |
