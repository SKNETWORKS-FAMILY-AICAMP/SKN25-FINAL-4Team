# A-clean 모델링 결정

**갱신일:** 2026-05-20

## 결정

첫 예측 baseline과 LSTM recipe 선정은 A 계열 전체 7개가 아니라 A-clean 4개 target으로 진행한다.

## A-clean target

| target_id | 해석 |
|---|---|
| `T1_group__central_cooling__P` | 중앙 냉각기 전력 소비 |
| `T1_group__local_cooling__P` | 로컬 냉방 전력 소비 |
| `T1_group__server_power__P` | 서버 전원·서버 냉방 전력 소비 |
| `T1_group__ventilation__P` | 환기 계통 전력 소비 |

## signed/net review target

| target_id | 분리 사유 |
|---|---|
| `T1_group__emission_lab__P` | `H1.Z15`, `H1.Z28`, `H1.Z29`에서 장기간 음수 P가 발생하며 W/W_out 검산상 outflow 방향과 일치 |
| `T2_building__H1__P` | emission_lab signed/net component를 포함하는 건물 aggregate |
| `T2_building__V__P` | `V.Z81`, `V.Z82` transformer/grid boundary meter의 signed/net flow 성격 |

## 근거

논문 원문은 부호 규약을 다음과 같이 설명한다.

```text
positive values are inflows/consumption,
negative values are outflows/production
```

DB 검산 결과, 주요 음수 meter에서 `P < 0`일 때 `ΔW < 0` 및 `W_out` 증가가 확인되었다. 따라서 해당 target을 소비량 benchmark에 섞으면 recipe 선정과 metric 해석이 왜곡될 수 있다.

## 산출물 기준

첫 모델링 입력은 다음을 사용한다.

```text
outputs/modeling/a_clean_targets_1h/
```

A 계열 전체 cache와 sign-risk 진단은 다음에 보존한다.

```text
outputs/modeling/a_targets_1h/
outputs/modeling/a_targets_1h/negative_diagnostics/
```

## metric 기준

A-clean baseline은 다음 metric을 기본으로 한다.

```text
MAE
RMSE
MAPE 또는 sMAPE는 zero/near-zero guardrail 적용 후 보조 지표로 보고
```

signed/net review target은 별도 family로 재정의한 뒤 MAE/RMSE 중심으로 평가한다.
