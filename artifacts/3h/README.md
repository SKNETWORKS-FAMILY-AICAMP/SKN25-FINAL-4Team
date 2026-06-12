# artifacts/3h

active 3h 추론 모델 산출물을 배치하는 위치입니다.

팀 repo에는 실제 모델 파일을 커밋하지 않습니다. 학습 없이 바로 추론해야 할 때만 별도 전달받은 artifact를 이 폴더 아래에 복사합니다.

## 계량기별 포함 파일 예시

| 파일 | 역할 |
|------|------|
| `routing.json` | 해당 계량기의 최종 모델 라우팅 정보 |
| `train_meta.json` | 학습 시점의 보조 메타데이터 |
| `feature_columns.json` | 모델 입력 feature 순서 |
| `input_scaler.joblib` | 입력 feature scaler |
| `target_scaler.joblib` | target P/residual 복원용 scaler |
| `hour_bias_corrections.csv` | 시간대별 bias correction 값 |
| `ridge.joblib` | Ridge 보정 모델 |
| `catboost.cbm` | CatBoost 모델 |
| `lstm_v*.pt` | LSTM 계열 모델 weight |
| `lightgbm_t_plus_*.txt` | step별 LightGBM 모델 |

## 주의

- 이 폴더에 artifact가 없으면 추론은 `no_artifact` 또는 실패 상태를 반환할 수 있습니다.
- candidate를 promote하면 이 폴더는 새 active 모델로 교체됩니다.
