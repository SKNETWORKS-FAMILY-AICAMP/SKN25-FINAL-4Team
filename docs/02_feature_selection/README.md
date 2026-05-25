# 02_feature_selection/

계량기 내부 변수(P, U1, PF, W ...) 중 모델에 쓸 대표 feature 선정.

| 파일 | 내용 |
|---|---|
| `FEATURE_SELECTION.md` | 이상탐지 기준 feature 선정. 전기: P, U1, PF / 열: P, qv, Tdiff, Ta. Complete Linkage 클러스터링 근거 포함 |
| `FEATURE_SELECTION.html` | 위 md의 HTML 버전 |
| `FEATURE_SELECTION_PREDICTION.md` | 예측 모델(LSTM, XGBoost/LightGBM) 기준 feature 선정. 이상탐지와 별도로 작성 |
