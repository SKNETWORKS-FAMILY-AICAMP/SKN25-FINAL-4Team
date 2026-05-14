# WORKLOG

## 현재 상태 요약

- 프로젝트 루트: `/home/playdata2/final_pj/energy-platform`
- 현재 단계: `DB 조회 -> 전처리 -> 이상탐지 -> 예측 -> API -> EDA` 프로토타입 완료
- 주 가상환경: `/home/playdata2/final_pj/.venv`
- DB는 외부 PostgreSQL/TimescaleDB, `SELECT only` 전제
- `.env` 핵심:
  - `DB_HOST=121.134.46.24`
  - `DB_PORT=5432`
  - `DB_NAME=SKN25`
  - `DB_USER=team4`
  - `DB_PASS=...`

## 중요 환경 메모

- DB 정보 자체는 정상이다.
- 샌드박스 안에서는 PostgreSQL 접속이 빈 `OperationalError`로 실패할 수 있다.
- 외부 DB가 필요한 실행은 권한 상승이 필요할 수 있다.
- `load_dotenv()`는 실행 위치에 민감하다. DB 스크립트는 가능하면 `energy-platform` 디렉터리 기준으로 실행한다.

## 메타데이터 / 문서

- 메타데이터:
  - `config/meter_metadata.json`
  - `config/meter_metadata.py`
- 설명표:
  - `METER_DESCRIPTION_TABLE.md`
  - `METER_DESCRIPTION_TABLE_V2.md`
  - `meter_description_table.csv`
  - `meter_description_table_v2.csv`
- 전략 문서:
  - `MODEL_STRATEGY_NOTES.md`
  - `GROUPING_STRATEGY_DRAFT.md`
- 기여 가이드:
  - `AGENTS.md`
- `H1.Z15`, `H1.Z17`, `H1.Z28`, `H1.Z29`
  - `anomaly_target = null`
  - 이유: feed/dist 성격, 양방향 가능, 공통 이상탐지 파이프라인 제외
- 논문 반영 메타데이터 보강 완료:
  - `H1/H2/H3/H4/V` 의미를 `physical location / sub-distribution` note로 보강
  - PV 설치 시점 note 추가
  - CHP 전기/열 설명 정교화
  - 위 변경은 `description`, `note`, `installation_note`, `location_prefix` 중심이며 파이프라인 필드는 변경하지 않음
- 위 메타데이터 보강 내용은 설명표(md/csv)에도 동기화 완료

## 구현된 스크립트

### 조회 / 전처리

- `scripts/fetch_h1z16_with_weather.py`
  - 전기 / 열량계 pivot 지원
  - `fetch_meter_data()` 재시도 로직 있음
    - 최대 3회
    - 5초 간격
- `scripts/issues_parser.py`
  - `issues.zip` 파싱
- `scripts/preprocess_h1z16.py`
  - 실제 핵심 함수는 `preprocess_meter(meter_urn, ...)`
  - `preprocess_h1z16()`는 H1.Z16 wrapper
  - long-gap 판정은 `anomaly_target` 단일 컬럼 기준
  - 현재 전처리의 이상치 처리는 `통계 기반`이 아니라 `규칙 기반`
    - 소비 전기 `P/P1/P2/P3 < 0 -> NaN`
    - `W < 0 -> NaN`
    - `|PF| > 1 -> NaN`
    - `Ta`, `Igm` 범위 규칙
  - 중요 변경:
    - `issues.zip` 재적용 제거
    - `선형보간(interpolate_short_gaps)` 제거
    - 배경:
      - 현재 DB의 `cr_measurement_1h`는 corrected/resampled 데이터로 보는 것이 타당
      - 따라서 issue 마스킹 + 재보간은 이중 전처리 가능성이 큼

### 이상탐지

- `scripts/anomaly_stl_h1z16.py`
  - STL + residual `±2σ`
  - 저장 파일:
    - `outputs/h1z16_stl_anomaly.png`
    - `outputs/h1z16_stl_decomposition.png`
  - raw 2023 시각화 노트북도 별도 생성됨
- `scripts/anomaly_if_h1z16.py`
  - Isolation Forest
  - 다변량 feature 기반
- `scripts/anomaly_lstm_h1z16.py`
  - LSTM 기반 이상탐지
- `scripts/anomaly_ensemble_h1z16.py`
  - STL / IF / LSTM 결합

### 예측

- `scripts/predict_h1z16.py`
  - 현재 타깃: `delta_W`
  - PyTorch LSTM
  - 추가 함수:
    - `predict_meter(meter_urn: str, steps: int = 24)`

### 전체 파이프라인

- `scripts/pipeline_full.py`
  - 전체 meter 대상 이상탐지/앙상블
  - 현재 생성 파일:
    - `outputs/anomaly_results_all.csv`
    - `outputs/anomaly_results_summary.csv`
    - `outputs/anomaly_results_detail.csv`
  - detail 컬럼:
    - `ts`
    - `meter_urn`
    - `anomaly_stl`
    - `anomaly_if`
    - `anomaly_lstm`
    - `ensemble_level`
    - `cause_hint`
  - `valid_df.empty` 또는 `feature_cols == []`이면 skip
    - `error = "유효 데이터 없음"`

## 파이프라인 결과 / 해결한 문제

- `pipeline_full.py` 재실행 완료
  - `detail shape = (3333748, 6)`였고, 이후 `cause_hint` 컬럼 추가됨
  - `summary shape = (80, 7)`
- `H2.Z71`
  - 빈 데이터로 실패하던 문제 해결
  - 현재 `유효 데이터 없음`으로 skip 처리
- `H1.Z310`
  - 일시적 DB 끊김 문제 있었으나 재시도 로직으로 보완
- `cause_hint` 로직 추가 완료
  - 조합별 진단 문구 저장

## API 상태

- FastAPI 앱 구성 완료:
  - `api/main.py`
  - `api/routers/anomaly.py`
  - `api/routers/meters.py`
  - `api/routers/upload.py`
  - `api/routers/predict.py`
- 주요 검증 완료:
  - `/`
  - `/anomaly/summary`
  - `/anomaly/{meter_urn}`
  - `/anomaly/{meter_urn}/timeline`
  - `/anomaly/{meter_urn}/stats`
  - `/meters`
  - `/meters/types`
  - `/meters/{meter_urn}`
  - `/upload/csv`
  - `/predict` 에러 케이스
- 현재 구현 기준으로는 `이상탐지/예측/메타데이터/CSV 업로드 결과를 API로 노출하는 단계`까지 완료
- 주의:
  - `/predict/H1.Z16` 정상 케이스는 LSTM 재학습 때문에 오래 걸린다

## EDA 상태

### 기존 노트북

- `notebooks/01_eda_overview.ipynb`
  - 메타데이터 + anomaly 결과 overview
- `notebooks/02_eda_meter_profile.ipynb`
  - 전처리 데이터 기준 상세 EDA
  - 기본 `H1.Z16`
  - 추가 대표 계량기:
    - `H1.Z20`
    - `V.K21`
    - `H2.Z61`
- `notebooks/03_eda_1h_vs_15m_compare.ipynb`
  - `H1.Z16` 1h vs 15m 비교
- `notebooks/04_eda_redundant_pair_compare.ipynb`
  - 이중화 계량기쌍 비교
  - 전체 pair summary 포함

### raw 기준 추가 노트북

- `notebooks/05_eda_meter_profile_raw.ipynb`
  - raw 단일 계량기 EDA
- `notebooks/06_eda_redundant_pair_compare_raw.ipynb`
  - raw 이중화 pair 비교
- `notebooks/07_h1z16_raw_2023_stl_view.ipynb`
  - raw 2023년 H1.Z16 시계열/STL decomposition/anomaly

### EDA 산출물 경로

- `outputs/eda/`
- `outputs/eda_raw/`
- `outputs/redundant_pair_compare/`
- `outputs/redundant_pair_compare_raw/`
- `outputs/stl_raw_2023/`

## 현재까지 확인한 중요한 해석

### H1.Z16

- raw 기준 데이터는 `2018-01-01`부터 존재
- 전처리 때문에 2018~2023 초반이 비어진 것이 아님
- 2023-09 이후 고부하 구간이 커서 6년치 그래프에서 앞 구간이 눌려 보였던 것
- raw 월별 집계상:
  - `2018~2023-08`: 대체로 `~440` 기저부하 수준
  - `2023-09` 이후: 본격 고부하 운전 시작
  - `2023-10`부터 매우 강한 고부하 상태

### 2018년 미계측/후발 계량기

- 일부 계량기는 실제로 2018년에 `anomaly_target` 데이터가 없음
- 예:
  - `V.Z84`
  - `H1.Z310`
  - `H2.Z311`
  - `H3.Z312`
  - 여러 `ZE` redundant pair
- 파일:
  - `outputs/eda/2018_meter_coverage_summary.csv`

### DB 1h 데이터와 issues 해석

- 논문 / Dryad 설명 기준:
  - `issues.zip`은 단순 가이드가 아니라 `문제 구간 + correction 방식 이력`
  - raw -> correction -> resampling 파이프라인이 이미 존재
- 현재 판단:
  - DB `ems.cr_measurement_1h`는 corrected/resampled 1h 데이터 성격으로 보는 것이 타당
  - 따라서 `issues.zip`을 다시 적용해서 `NaN` 처리하고 다시 보간하는 것은 이중 전처리 가능성이 큼
- 그 결과 현재 전처리는:
  - `issues.zip` 재적용 제거
  - `선형보간` 제거
  - `pivot`, `weather join`, numeric cast, 최소 sanity check, long-gap 마킹, 시간 파생변수만 유지

### 이중화 계량기쌍

- `H1.Z20` / `H1.ZE20` raw 비교 결과:
  - 전체 패턴 상관은 높음 (`corr≈0.9888`)
  - 일부 시점 차이가 매우 큼
  - 단순 오차보다는 부호/스케일/보정/집계 차이 가능성 검토 필요

## 모델 해석 메모

- `STL`
  - 단일 핵심 시계열(`P` 또는 `Tdiff`) 이상탐지
  - 설명력이 좋음
- `IF`
  - 다변량 상태 조합 이상탐지
  - 단일 `P` 모델이 아니라 `P + PF + I + 시간/날씨` 조합을 봄
  - 무엇이 이상한지는 후처리 해석 필요
- `LSTM`
  - 시간 패턴 이탈 탐지
- 현재 판단:
  - 메인 타깃:
    - 전기: `P`
    - 열량: `Tdiff`
  - `PF`, `I1~I3`, `P1~P3`는 우선 보조 진단 변수로 두는 것이 적절

## 모델 전략 메모 (A/B/C)

- `A`: 다른 계량기 `P`를 feature로 추가
- `B`: 여러 계량기를 군집 단위 하나의 모델로 묶음
- `C`: 계량기 정체성은 유지하되 모델 파라미터를 공유

현재 합의에 가까운 방향:
- 이상탐지
  - `STL`: 개별 유지
  - `IF`: `B` 우선 검토

## 다음 분석 / 정제 전략 메모

현재 방향은 `전처리 -> 모델`을 바로 고정하기보다, 먼저 시계열 구조를 충분히 보고
`구조적으로 가능한 값`과 `실제 이상치`를 구분한 뒤 정제 기준을 확정하는 쪽이 적절하다.

### 1단계: STL로 데이터 구조 파악 (EDA)

- `STL`로 트렌드 / 계절성 / 잔차를 시각화한다.
- 먼저 `"이 구간이 이상한가 아닌가"`를 눈으로 판단한다.
- 특히 발전 계량기에서 나타나는 양수 구간이:
  - 실제 이상인지
  - 아니면 구조적으로 나올 수 있는 값인지
  를 먼저 해석한다.
- 이 단계 판단을 바탕으로 이상 구간을:
  - `NaN` 처리할지
  - 유지할지
  결정한다.

### 2단계: 슬라이딩 윈도우 기반 이상 구간 재확인

- `1년 단위`로 잘라 `6개 연도`를 겹쳐 시각화한다.
- 추가 확인이 필요하면 `월 단위`로도 나누어 시각화한다.
- 어느 구간에서 값이 튀는지 확인하고, 그것이 주기성을 띠는 값인지 본다.
- 즉:
  - 매년 반복되는 패턴인지
  - 특정 연도에만 튀는 값인지
  를 구분한다.
- 이 판단을 바탕으로 해당 구간의 `NaN 처리 여부`를 결정한다.

### 3단계: 정제된 데이터로 이상탐지 모델 학습

- `IF`
  - 가능한 한 정상 데이터만으로 `fit`한다.
  - 핵심은 `"정상이 무엇인지"`를 깨끗한 데이터로 학습시키는 것이다.
- `LSTM`
  - 정상 패턴 중심으로 `train`한다.
  - 복원 오차 기준으로 이상을 판정한다.

### 4단계: 이상탐지 파이프라인 반영

- `STL + IF + LSTM` 앙상블 구조는 기존 방향을 유지한다.
- 다만 `IF / LSTM`의 학습 데이터 품질을 앞 단계 정제를 통해 높인다.
- 즉 모델 구조 자체보다 `학습 입력 데이터의 신뢰도 향상`이 우선 과제다.

### 이상 구간 판단 근거 기록 기준

이상처럼 보이는 값을 바로 제거하지 말고, `유지 / NaN 처리` 판단의 근거를 함께 남긴다.
목적은 이후 재검토 시에도 같은 판단을 재현 가능하게 만드는 것이다.

#### 1. 연도별 동일 시점 분포 확인

- 같은 `월-일-시각` 기준으로 여러 해의 값을 겹쳐 본다.
- 예:
  - 매년 `7월 15일 14시`의 `P`
  - 매년 여름철 평일 `13~15시` 구간의 `P`
- 판단 기준:
  - 여러 해에서 비슷하게 반복되면 `구조적 패턴` 가능성이 크다.
  - 특정 연도에서만 크게 튀면 `이상치` 가능성이 크다.
- 기록 예시:
  - `2022, 2023, 2024` 동일 시점에도 유사한 고부하 존재 -> 유지 우선 검토
  - `2021`에만 동일 시점 대비 급락/급등 발생 -> NaN 처리 후보

#### 2. 월별 박스플롯 분포 확인

- `1월 ~ 12월` 단위로 값을 나누어 분포를 본다.
- 계절별로 원래 분포 폭이 다른지 먼저 확인한다.
- 판단 기준:
  - 해당 월의 분포 안에서 반복적으로 나타나는 값이면 바로 이상으로 보지 않는다.
  - 해당 월 분포 바깥의 극단값이고 반복성도 약하면 이상 후보로 본다.
- 기록 예시:
  - `8월`은 냉방/부하 영향으로 상단 분포가 넓음 -> 고부하 단독으로 제거하지 않음
  - `3월` 분포에서 극단적으로 벗어난 단발 스파이크 -> NaN 처리 후보

#### 3. 반복 출현 횟수 확인

- 튀는 값이 `얼마나 자주`, `어떤 주기`로 반복되는지 확인한다.
- 일별 / 주별 / 월별 / 연도별 반복 여부를 함께 본다.
- 판단 기준:
  - 매일, 매주, 매년 비슷한 시점에 반복되면 운영 패턴 또는 계절성일 가능성이 있다.
  - 특정 며칠 또는 특정 연도에만 단발적으로 나오면 이상 또는 오류 가능성이 크다.
- 기록 예시:
  - 매일 오전 9시에 짧은 피크 반복 -> 운영 스케줄 가능성, 유지 검토
  - 6년 중 특정 2~3일에만 급락 발생 -> 센서 오류 또는 이벤트 가능성, NaN 후보

#### 4. 최종 판단 문구 예시

- `유지`
  - 연도별 동일 시점 비교 시 유사 패턴 반복
  - 해당 월 분포 내에서 설명 가능
  - 반복 출현이 확인되어 구조적 패턴 가능성 높음
- `NaN 처리 후보`
  - 특정 연도/특정 시점에만 발생
  - 월별 분포에서 극단적 outlier
  - 반복성이 없거나 매우 약함

#### 5. 계량기별 판단 기록 예시

- `H1.Z20 / P / 2023-08 고부하 구간`
  - 연도별 동일 시점 비교 시 타 연도에도 유사 패턴 존재
  - 8월 분포상 설명 가능
  - 여름철 평일 오후 반복 출현 확인
  - 판단: `유지`

- `V.Z84 / P / 2021-03-12 04:00 급락`
  - 동일 시점 타 연도 재현 없음
  - 3월 분포 기준 극단값
  - 반복 출현 없음
  - 판단: `NaN 처리 후보`
  - `LSTM 이상탐지`: 개별 유지
- 예측
  - 주력 후보: `Boosting(CatBoost/LightGBM/XGBoost) + A + C`
  - 비교군: `예측 LSTM + A + C`

추가 메모:
- `IF`는 다변량 이상 시점 탐지에는 유리하지만, 무엇이 이상인지 설명은 후처리 규칙/편차 분석/기여도 해석이 필요
- 예측 LSTM에서 타 계량기 `P` feature 추가 기준은 `GROUPING_STRATEGY_DRAFT.md` 하단에 정리

## 해결된 부분

- `.env`의 `DB_NAME` 문제 수정
- `H2.Z71` skip 방어 로직
- `H1.Z310` 재시도 로직
- `detail / summary` CSV 분리 저장
- `cause_hint` 추가
- API 라우터 구현/검증
- Jupyter `.venv` 커널 구성
- notebook inline 이미지 표시 문제 해결
- raw 버전 EDA 노트북 추가
- STL decomposition 이미지 추가
- metadata 논문 반영 보강(1,2,3번) 완료 및 설명표 동기화
- 군집 초안 문서(`GROUPING_STRATEGY_DRAFT.md`) 작성
- 모델 전략 문서(`MODEL_STRATEGY_NOTES.md`)에 A/B/C 및 최적안 정리
- `preprocess_h1z16.py`에서 `issues.zip` 재적용과 선형보간 제거
- `anomaly_lstm_h1z16.py`의 raw/scaled `P` merge 문제 수정
  - 학습/추론은 scaled feature 유지
  - 결과 저장/표시는 raw `P` 기준으로 정리
- `anomaly_ensemble_h1z16.py`의 `P` 중복 merge 문제 수정
- `H1.Z16` 단일 anomaly 스크립트 최적화
  - `STL`, `IF`, `LSTM`은 각 1회 실행 후 결과 CSV 저장
  - `ensemble`은 개별 결과 CSV를 읽어서 merge만 수행
  - 재실행형 구조 제거
- `H1.Z16` 단일 anomaly 최신 재실행 완료
  - `STL`: `1174건 (2.23%)`
  - `IF`: `963건 (2.00%)`
  - `LSTM`: `325건 (3.38%)`
  - `Ensemble`: `DANGER 10건`, `WARNING 318건`
- 생성/갱신된 H1.Z16 결과 파일
  - `outputs/h1z16_stl_results.csv`
  - `outputs/h1z16_if_results.csv`
  - `outputs/h1z16_lstm_results.csv`
  - `outputs/h1z16_ensemble_results.csv`
- `pipeline_full.py` 호환성 정리
  - 공용 anomaly helper가 `P` 컬럼을 기대하므로
  - thermal meter는 `anomaly_target`을 임시 `P` alias로 맞춘 뒤 `STL/IF/LSTM/ensemble`에 전달
  - `run_ensemble(stl_df, if_df, lstm_df)` 새 시그니처에 맞게 수정

## 아직 남은 문제 / 후속 작업

1. `STL rolling 365일 sigma`
   - 전체 기간 기준선 왜곡 가능
   - 초기 미계측/부분계측 + 저부하/고부하 regime 혼합 반영 필요
2. `LSTM 커버리지 수정`
   - 현재 train 구간 처리 문제
   - sliding window로 전체 구간 추론 필요
3. `앙상블 train/test 분리 판정`
   - train: `STL+IF`
   - test: `STL+IF+LSTM`
4. `MAPE 개선`
   - `delta_W` 거의 0인 미가동 구간 제외
5. `5년/1년 재실험`
   - `2018~2022 train / 2023 test`
6. `관련 계량기 P feature 추가`
   - 예측 모델에 물리적으로 연결된 계량기 `P` 추가
   - leakage 확인 필수
7. `한국 데이터 어댑터`
   - 명시적 config 분리부터 시작
8. `원인추정 로직 고도화`
   - `cause_hint`는 현재 초안 수준
   - PF/I/온도/관련 계량기 기반 evidence 생성 규칙 고도화 필요
9. `프론트 / PDF 보고서 / RAG`
  - 제품화/데모용 후속 작업
  - 현재 우선순위는 모델 신뢰도 개선 뒤
10. `전처리 변경 반영 후 재실행`
   - 전처리 입력이 바뀌었으므로 이상탐지/예측 모델 및 결과 CSV는 재생성 필요
11. `pipeline_full.py`도 같은 구조로 최적화 필요
   - `pipeline_full.py`는 원래 meter별 1회 실행 구조라 중복 재실행 문제는 크지 않음
   - 다만 개별 결과 CSV를 별도 저장/재사용하는 전체 배치 구조로 확장할지는 후속 판단 필요
12. `pipeline_full.py` 재실행 필요
   - 전처리 변경(`issues.zip` 재적용 제거, 선형보간 제거) 이후 전체 anomaly CSV를 아직 최신 기준으로 재생성하지 않음
13. `예측 LSTM` 재실행 필요
   - 현재 예측은 `H1.Z16` 단일 baseline 구조
   - 전처리 변경 반영 후 `predict_h1z16.py` 재검증 필요

## 추천 우선순위

1. `LSTM 커버리지 수정`
2. `앙상블 train/test 분리`
3. `STL rolling sigma`
4. `MAPE 보정`
5. `5년/1년 재실험`
6. `관련 계량기 P feature`
7. `한국 데이터 어댑터`

## 다음 세션에서 바로 이어갈 수 있는 후보 작업

- `pipeline_full.py` 개선안 1~4 실제 반영
- `2018_meter_coverage_summary.csv` 기반으로 계량기별 학습 시작 시점 규칙 정리
- `PF/I`를 보조 진단 규칙으로 어떻게 붙일지 설계
- `H1.Z16` 외 대표 계량기 raw 2023 STL 노트북 복제
- `predict` 정상 케이스 API 실측 검증
- `GROUPING_STRATEGY_DRAFT.md` 기준으로 B/C용 group mapping 초안 작성
- 예측용 `A 방식` 후보 계량기 연결표 초안 작성
- `pipeline_full.py`에 결과 재사용형 ensemble 구조 반영 검토
- `H1.Z16` 외 개별 스크립트/전체 파이프라인에 LSTM merge 수정 범위 점검
- `pipeline_full.py` 전체 재실행
- `predict_h1z16.py` 재실행 및 결과 확인

## 자주 쓰는 실행 예시

### API 서버

```bash
cd /home/playdata2/final_pj
source .venv/bin/activate
PYTHONPATH=/home/playdata2/final_pj/energy-platform uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

### 전체 파이프라인

```bash
MPLCONFIGDIR=/tmp/matplotlib \
PYTHONPATH=/home/playdata2/final_pj/energy-platform \
/home/playdata2/final_pj/.venv/bin/python \
/home/playdata2/final_pj/energy-platform/scripts/pipeline_full.py
```

### STL

```bash
MPLCONFIGDIR=/tmp/matplotlib \
PYTHONPATH=/home/playdata2/final_pj/energy-platform \
/home/playdata2/final_pj/.venv/bin/python \
/home/playdata2/final_pj/energy-platform/scripts/anomaly_stl_h1z16.py
```

### 예측

```bash
MPLCONFIGDIR=/tmp/matplotlib \
PYTHONPATH=/home/playdata2/final_pj/energy-platform \
/home/playdata2/final_pj/.venv/bin/python \
/home/playdata2/final_pj/energy-platform/scripts/predict_h1z16.py
```
