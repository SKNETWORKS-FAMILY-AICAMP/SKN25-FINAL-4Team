import sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve() / "src"))

from ems.db import load_env, fetch_measurements
from ems.preprocessing import run_pipeline

load_env()
df = fetch_measurements("H1.W11", "P", "2020-01-01", "2020-02-01", "1h")

print("원본 shape:", df.shape)
result = run_pipeline(df)
print("전처리 후 shape:", result.shape)
print("컬럼:", result.columns.tolist())
print(result["quality_flag"].value_counts(dropna=False))
print(result[["is_building_event", "is_outage", "is_iqr_outlier"]].sum())