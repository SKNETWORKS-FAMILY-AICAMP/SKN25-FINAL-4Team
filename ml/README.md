# ml — 잔차 타겟 예측 실험

## 목적

test6(파생변수 포함, v84 구조)과 동일한 설정에서  
**예측 타겟만 `P(t)` → `P(t) - P(t-1)` (lag-1 잔차)로 변경**하여  
모델이 persistence 대신 변화 패턴을 학습하는지 검증합니다.

### 배경

- 전체 45개 계량기 중 test split 기준 lag-1 자기상관 ≥ 0.9인 계량기가 22개
- 특히 **주기 진동형** 계량기(24h 사인파 패턴, 14개)는  
  절대값 예측보다 잔차 예측 시 변화 패턴 학습 신호가 강해질 것으로 기대
- test6 대비 차이: `config.py`의 `USE_RESIDUAL_TARGET = True` 플래그 하나

## 실험 구조

| 항목 | 내용 |
|------|------|
| 기반 실험 | test6 (파생변수 4개 포함, v61 LightGBM + v63 라우팅) |
| 변경점 | 타겟 = `P(t) - P(t-1)`, 복원 시 anchor(`P(t-1)`) 가산 |
| 모델 구성 | LSTM v1~v7, CatBoost, LightGBM, Ridge, Seasonal Naive → v84 앙상블 |
| 평가 지표 | MAE, RMSE, MAPE, WAPE, persistence MAE, beats_persistence |

## 타겟 변환 원리

```
학습:  y = P(t) - P(t-1)          (anchor = P(t-1))
복원:  P̂(t) = P(t-1) + ŷ
```

모든 모델(LSTM, CatBoost, LightGBM, Ridge)은 `bundle.y_train`을 정답으로 학습하므로  
자동으로 잔차를 학습합니다. Seasonal Naive만 별도로 anchor를 빼서 잔차 형태로 맞춥니다.

## 폴더 구조

```
ml/
├── README.md                  ← 이 파일
├── pipeline/
│   ├── train.py               ← 학습 진입점
│   ├── common/
│   │   ├── config.py          ← USE_RESIDUAL_TARGET=True 플래그 포함
│   │   ├── preprocessing.py   ← 잔차 타겟 생성 + anchor 저장
│   │   ├── ensemble.py        ← anchor 기반 복원 로직
│   │   ├── naive.py           ← seasonal naive 잔차 변환
│   │   └── ...                ← 나머지는 test6와 동일 (import 경로만 변경)
│   └── artifacts/
│       ├── 1h/                ← 1시간 예측 산출물
│       │   └── {meter_urn}/   ← 계량기별 폴더
│       └── 3h/                ← 3시간 예측 산출물
│           └── {meter_urn}/
```

## 실행 방법

```bash
# 1시간 예측
conda run -n skn25 python -m ml.pipeline.train --horizon 1

# 3시간 예측
conda run -n skn25 python -m ml.pipeline.train --horizon 3
```

## test6와 비교 관점

| 비교 항목 | 기대 방향 |
|-----------|---------|
| 주기 진동형 계량기 MAE | 개선 가능성 (변화 패턴 학습 강화) |
| 순수 persistence형 계량기 MAE | 유사 또는 소폭 악화 |
| beats_persistence 비율 | 주기 진동형 중심으로 증가 기대 |
