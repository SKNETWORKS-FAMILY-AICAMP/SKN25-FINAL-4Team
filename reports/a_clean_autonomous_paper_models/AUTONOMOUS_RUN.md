# A-clean paper-model autonomous run

## 목적

사용자 승인에 따라 A-clean 4개 target에 대해 논문에서 사용된 예측 모델 계열을 우선 재현·비교한다. 이 작업은 기존 A-clean dataset 및 첫 LSTM 결과와 분리된 자율 실험 branch-like run으로 기록한다.

## 자율 실행 기준

- 입력 dataset은 `outputs/modeling/a_clean_targets_1h/`로 고정한다.
- signed/net review target은 포함하지 않는다.
- RunPod 기존 인스턴스를 그대로 사용한다.
- 문제 발생 시 실행 가능한 범위에서 자율적으로 진단·수정한다.
- target 정의 변경, signed/net target 포함, commit/push, 기존 산출물 삭제는 하지 않는다.

## Run label

```text
a_clean_autonomous_paper_models
```

## 완료 산출물

```text
outputs/modeling/a_clean_paper_models_1h/
reports/a_clean_autonomous_paper_models/report.md
reports/a_clean_autonomous_paper_models/AUTONOMOUS_RUN.md
```

## 완료 요약

- Nature Scientific Data 논문은 forecast benchmark 모델표를 제공하지 않는 dataset descriptor로 확인했다.
- 논문 활용 방향과 기존 Huang-style 기록을 기준으로 lag/rolling/calendar/weather feature 기반 paper-adjacent model suite를 구성했다.
- 실행 모델: `ridge`, `hist_gradient_boosting`, `random_forest`, `extra_trees`, `mlp`.
- 비교 기준: 기존 `last_value`/seasonal naive 및 LSTM seq24 결과.
- 4개 target 중 3개에서 best paper-model이 `last_value` MAE를 개선했다.
- local에는 metrics/predictions/figures만 회수했고, 약 2.54 GiB의 remote joblib model binary는 회수하지 않았다.
