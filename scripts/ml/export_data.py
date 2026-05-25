"""학습 데이터 CSV 내보내기 (RunPod 업로드용).

DB에서 피처 엔지니어링까지 완료된 DataFrame을 CSV로 저장.
런팟에서 train_vmd_lstm.py --csv 옵션으로 사용.

Usage:
    uv run --with pandas --with "psycopg[binary]" --with python-dotenv \
           python scripts/ml/export_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from data_loader import TRAIN_START, VAL_END, add_features, load_raw

OUT_DIR = Path("outputs/data")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    print("▶ DB에서 데이터 로드 중...")
    df = load_raw()
    df = add_features(df)

    # train + val 구간만 추출
    df = df[(df.index >= TRAIN_START) & (df.index <= VAL_END)].dropna()

    out_path = OUT_DIR / "ems_features.csv"
    df.to_csv(out_path)

    print(f"▶ 저장 완료: {out_path}")
    print(f"  기간  : {df.index[0].date()} ~ {df.index[-1].date()}")
    print(f"  행 수 : {len(df):,}")
    print(f"  컬럼  : {list(df.columns)}")
    print(f"\n런팟 업로드 후 실행:")
    print(f"  python train_vmd_lstm.py --csv ems_features.csv")


if __name__ == "__main__":
    main()
