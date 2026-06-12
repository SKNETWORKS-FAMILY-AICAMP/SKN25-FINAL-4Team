# src/

서비스에서 재사용하는 Python 도메인 코드다. 실행 명령을 직접 정의하기보다
API와 `scripts/`에서 호출하는 기능을 제공한다.

| 폴더 | 내용 |
|---|---|
| `agents/` | 향후 LLM 도구 orchestration을 위한 기본 구조 |
| `anomaly/` | 통계, Isolation Forest, LSTM Autoencoder 기반 이상탐지 모델 |
| `db/` | DB 연결, 조회, 단위 보정 |
| `forecasting/` | 예측 모델 학습·추론·운영 로직 |
| `preprocessing/` | 전기·열·날씨 데이터 전처리 |
| `rag/` | 도메인 문서 검색 기본 구조 |

Import P-Max 운영 코드는 `forecasting/import_pmax/`에 있다.

