# scripts/

EDA, 전처리, Import P-Max 예측 모델 실행 스크립트.

```
scripts/
├── pipeline/       DB fetch, 전처리
├── forecasting/    Import P-Max 학습, 추론, 검증·승격, 롤백 CLI
├── eda/
│   ├── stl/        STL 분해 (static PNG + Plotly)
│   ├── correlation/ 계량기 간 상관관계 (static PNG + Plotly)
│   └── sliding/    슬라이딩 윈도우 (static PNG + Plotly)
└── utils/          기타 유틸
```

## 실행 방법

스크립트는 모두 프로젝트 루트에서 모듈로 실행:

```bash
python -m scripts.pipeline.preprocess
python -m scripts.eda.stl.eda_stl_electric
python -m scripts.forecasting.predict_all_import_pmax
```
