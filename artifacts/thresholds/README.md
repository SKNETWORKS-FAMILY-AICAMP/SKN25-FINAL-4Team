# artifacts/thresholds

사전 경보용 정상 범위 threshold를 배치하는 위치입니다.

팀 repo에는 실제 `val_thresholds.csv`를 커밋하지 않습니다. 학습 없이 바로 추론하려면 별도 전달받은 threshold 파일을 이 폴더에 배치해야 합니다.

## 파일

| 파일 | 역할 |
|------|------|
| `val_thresholds.csv` | validation actual P 기준 시간대별 lower/upper 정상 범위. 추론 시 pred_t_plus_k와 비교 |

## 기준

- 시간대별 2~98 percentile 기반
- lower bound가 0 근처인 경우 음수 예측 소음을 줄이기 위해 floor=-50W 적용

## 주의

- 현재 threshold는 사전 경보용입니다.
- 사후 확인용 `|actual - pred|` error threshold와는 다른 개념입니다.
