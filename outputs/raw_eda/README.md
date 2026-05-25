# raw_eda 구조 안내

`raw_eda/`는 DB raw 기반 EDA 산출물만 모아두는 폴더다.

## 최상위 구분

- `correlation/`
  - 상관계수 분석 결과
- `sliding_window/`
  - 구간 통계 및 시계열 창 기반 탐색 결과
- `stl/`
  - STL 분해 및 이상치 탐색 결과

## correlation

- `correlation/static/png/`
  - 정적 heatmap PNG
  - `electric/6year`, `electric/yearly`, `electric/seasonal`
  - `thermal/6year`, `thermal/yearly`, `thermal/seasonal`
- `correlation/static/csv/`
  - 상관계수 집계 CSV
  - 각 period 폴더에 `long.csv`, `summary.csv`
- `correlation/plotly/`
  - 통합 interactive dashboard
  - `electric_correlation_dashboard.html`
  - `thermal_correlation_dashboard.html`
- `correlation/analysis/`
  - 관계 해석용 개별 분석 dashboard
  - 예: `thermal_meter_relationship_dashboard.html`

개별 correlation HTML은 유지하지 않는다.
통합 dashboard에서 동일 내용을 확인한다.

## sliding_window

- `sliding_window/png/`
  - 계량기별 sliding window 정적 PNG
  - `electric/{meter_urn}/`
  - `thermal/{meter_urn}/`
- `sliding_window/csv/`
  - sliding window 집계 CSV
  - `electric/`, `thermal/`
- `sliding_window/plotly/`
  - 통합 interactive sliding window dashboard
  - `electric_sliding_dashboard.html`
  - `thermal_sliding_dashboard.html`

## stl

- `stl/png/`
  - 계량기별 STL PNG
  - `electric/{meter_urn}/`
  - `thermal/{meter_urn}/`
- `stl/csv/`
  - 계량기별 STL `detail.csv`, `summary.csv`
  - `electric/{meter_urn}/`
  - `thermal/{meter_urn}/`
- `stl/plotly/`
  - 통합 interactive STL dashboard
  - `electric_stl_dashboard.html`
  - `thermal_stl_dashboard.html`
  - 등분산성 점검 dashboard
    - `stl_homoscedasticity_dashboard.html`
    - `stl_homoscedasticity_manifest.json`
    - `homoscedasticity/{meter_urn}/{col}.js`
  - dashboard가 읽는 payload:
    - `electric/{meter_urn}/{col}.js`
    - `thermal/{meter_urn}/{col}.js`

## 운영 원칙

- 새 EDA 산출물은 이 구조에 맞춰 저장한다.
- 재생성 가능한 중간 산출물은 중복 보관하지 않는다.
- 정적 결과와 interactive 결과는 분리 유지한다.
