# scripts/

EDA 및 전처리 스크립트.

```
scripts/
├── pipeline/       DB fetch, 전처리
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
```
