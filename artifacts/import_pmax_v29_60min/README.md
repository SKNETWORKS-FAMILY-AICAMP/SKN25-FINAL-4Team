# Import P-Max 운영 모델 산출물

4개 논리 계량기의 운영 추론에 사용하는 모델 산출물이다.

- 입력: 15분 간격 최근 24시간, 총 96행
- 출력: 향후 60분에 대한 15분 단위 예측 4개
- 피처: 기상 정보를 제외한 22개
- 앙상블: LightGBM 2개, XGBoost 1개, CatBoost 1개

필수 폴더 구조:

```text
input_24h/predict_60min/{logical_meter}/
  _candidate_models/*.joblib
  v29/manifest.json
  v29/ensemble_weights.csv
```

추론 코드는 이 폴더에서 모델과 가중치를 불러와 최종 예측값을 생성한다.

운영 시 추가될 수 있는 메타데이터:

| 파일 | 내용 |
|---|---|
| `deployment_metrics.json` | 승격한 candidate의 검증 지표와 digest |
| `promotion.json` | 승격 시각, archive 경로, inference smoke 결과 |
| `rollback.json` | 명시적 롤백 이력 |

평가 보고서와 test 예측은 운영 artifact에 포함하지 않는다. candidate는 승격
성공 후 삭제되며, 승격 실패 시 원인 확인과 재시도를 위해 보존한다.
