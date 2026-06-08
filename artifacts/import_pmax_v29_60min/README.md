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
평가 보고서와 저장된 test 예측은 candidate 폴더에 보관한다.
