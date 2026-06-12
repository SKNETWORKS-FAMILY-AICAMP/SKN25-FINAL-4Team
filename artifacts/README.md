# artifacts

모델 학습 산출물과 운영 중 생성되는 결과를 저장하는 폴더입니다.

이 팀 repo에는 개인정보/민감정보/대용량 산출물 관리를 위해 실제 모델 weight, scaler, routing, threshold, 추론 결과를 커밋하지 않습니다. 이 폴더의 README와 `.gitkeep`만 repo에 포함합니다.

## 하위 폴더/파일

| 경로 | 역할 |
|------|------|
| `3h/{meter_urn}/` | active 3h 추론에 필요한 계량기별 모델 파일을 배치하는 위치 |
| `thresholds/val_thresholds.csv` | 사전 경보용 시간대별 정상 P 범위 파일 위치 |
| `train_summary_3h.csv` | active 3h 학습 성능 요약 파일 위치 |
| `candidate/` | 새 재학습 결과가 업로드/저장되는 영역 |
| `archive/` | promote 시 기존 active artifact를 백업하는 영역 |
| `incoming_uploads/` | API로 업로드된 압축 artifact 임시 저장 영역 |
| `training_jobs/` | API가 RunPod 재학습 job 상태를 기록하는 영역 |
| `inference_results/` | 단일 timestamp 추론 CSV 저장 기본 위치 |

## active artifact 예시

학습 없이 바로 추론하려면 별도 전달받은 산출물을 아래 구조로 배치해야 합니다.

```text
artifacts/3h/H1.Z10/
  routing.json
  train_meta.json
  feature_columns.json
  input_scaler.joblib
  target_scaler.joblib
  hour_bias_corrections.csv
  ridge.joblib
  catboost.cbm
  lstm_v*.pt
  lightgbm_t_plus_*.txt
artifacts/thresholds/val_thresholds.csv
artifacts/train_summary_3h.csv
```

## 주의

- `.gitignore`에서 `artifacts/**`는 기본 제외됩니다.
- 모델 산출물은 private storage, 별도 압축 파일, 또는 재학습 결과 업로드 API로 관리하는 것을 권장합니다.
- DB 접속 정보나 API key는 절대 이 폴더에 저장하지 않습니다.
