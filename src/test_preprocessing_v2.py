import sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve() / "src"))

from ems.db import load_env, fetch_measurements
from ems.preprocessing import run_pipeline
import pandas as pd

load_env()

# H1.Z15 I1 테스트 (절댓값 처리 확인)
print("=== H1.Z15 I1 절댓값 처리 테스트 ===")
df = fetch_measurements("H1.Z15", "I1", "2020-01-01", "2020-02-01", "1h")
print("원본 음수 건수:", (df["value"] < 0).sum())
result = run_pipeline(df)
print("전처리 후 음수 건수:", (result["value"] < 0).sum())

# H2.ZE66 PF 테스트 (NaN 처리 확인)
print("\n=== H2.ZE66 PF NaN 처리 테스트 ===")
df2 = fetch_measurements("H2.ZE66", "PF", "2022-04-01", "2022-05-01", "1h")
print("원본 PF>1 건수:", (df2["value"] > 1).sum())
result2 = run_pipeline(df2)
print("전처리 후 NaN 건수:", result2["value"].isna().sum())
print("전처리 후 quality_flag:", result2["quality_flag"].value_counts(dropna=False))

# H2.Z311 변류기 보정 테스트
print("\n=== H2.Z311 변류기 보정 테스트 ===")
df3 = fetch_measurements("H2.Z311", "P", "2020-07-01", "2020-08-01", "1h")
result3 = run_pipeline(df3)
print("원본 평균:", df3["value"].mean())
print("전처리 후 평균 (×0.8 적용 기대):", result3["value"].mean())