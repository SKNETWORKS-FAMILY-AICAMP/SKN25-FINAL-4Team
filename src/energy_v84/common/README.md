# src/energy_v84/common

학습과 추론이 공유하는 내부 모듈입니다.

## 파일 역할

| 파일 | 역할 |
|------|------|
| `config.py` | 계량기 스펙, split 기간, 학습 기본값, artifact root 설정 |
| `mapping.py` | 서비스 대상 계량기와 대표/전이/개별 모델 매핑 |
| `db.py` | DB engine 생성과 계량기 시계열 조회 |
| `preprocessing.py` | 시간 feature, 파생변수, physical rule, window 생성 |
| `model.py`, `lstm.py` | LSTM 계열 모델 정의와 학습/예측 유틸 |
| `catboost_model.py` | CatBoost 학습/예측 |
| `lightgbm_model.py` | LightGBM 학습/예측/저장 |
| `ridge.py` | Ridge 보정 모델 |
| `naive.py` | seasonal naive baseline |
| `ensemble.py` | median ensemble, bias correction, metric 계산 |
| `router.py`, `selectors.py` | 라우팅/모델 선택 관련 로직 |
| `artifacts.py` | 모델/scaler/routing 저장 유틸 |
| `bias.py` | bias correction 보조 로직 |
| `plots.py` | 학습 리포트용 plot 저장 |
| `meter_tags.csv` | 알려진 계량기 이슈 태그. inference 결과에 issue 정보로 붙음 |

## 주의

- 이 폴더는 학습과 추론이 같이 사용하므로 함수 시그니처나 컬럼명을 바꿀 때 양쪽 영향을 확인해야 합니다.
- `meter_tags.csv`는 사람이 관리하는 운영 태그 파일입니다.
