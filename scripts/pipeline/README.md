# pipeline/

DB fetch 및 전처리. 다른 EDA 스크립트가 공통으로 import하는 핵심 모듈.

| 파일 | 내용 |
|---|---|
| `fetch_h1z16_with_weather.py` | DB 연결(`build_engine`), 계량기 데이터 fetch(`fetch_meter_data`), 날씨 join 유틸. 대부분의 EDA 스크립트가 이 모듈을 import함 |
| `preprocess.py` | raw fetch → 날씨 join → numeric cast → 물리 규칙 기반 NaN 처리 공통 전처리. `fetch_joined_data`, `preprocess_meter` 제공 |
| `preprocess_h1z16.py` | `preprocess.py`의 H1.Z16 특화 래퍼 |
| `preprocess_h1z16_legacy.py` | 전처리 초기 버전 백업. `issues.zip` 파서 연동 포함. 현재는 참조용 |
