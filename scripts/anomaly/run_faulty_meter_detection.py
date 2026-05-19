import pandas as pd
import numpy as np
from sqlalchemy import create_engine

engine = create_engine(
    "postgresql://team4:teamteam4@121.134.46.24:5432/SKN25"
)

# ── 1단계: 이상 날짜 추출 ──────────────────────────────
anomaly_df = pd.read_csv('outputs/anomaly/anomaly_results_k6.csv')
anomaly_df['timestamp'] = pd.to_datetime(anomaly_df['timestamp'])
anomaly_df['is_anomaly'] = anomaly_df['recon_error'] > anomaly_df['threshold']

anomaly_dates = (
    anomaly_df[anomaly_df['is_anomaly'] == True]['timestamp']
    .dt.date.unique()
)
print(f"이상 날짜 총 {len(anomaly_dates)}일")


# ── 2단계: 날짜별 계량기 이상 특정 ───────────────────────
def detect_faulty_meters(date, engine, z_threshold=3.0):

    date_str = str(date)

    query = f"""
    SELECT ts, meter_urn, measurement, value
    FROM ems.cr_measurement_1h
    WHERE ts >= '{date_str}'::date - interval '7 days'
      AND ts <  '{date_str}'::date + interval '1 day'
    """
    df = pd.read_sql(query, engine)
    df['date'] = pd.to_datetime(df['ts']).dt.date
    target_date = pd.Timestamp(date).date()

    target   = df[df['date'] == target_date]
    baseline = df[df['date'] != target_date]

    if target.empty or baseline.empty:
        return []

    results = []
    groups = df.groupby(['meter_urn', 'measurement'])

    for (meter, meas), _ in groups:
        b = baseline[
            (baseline['meter_urn'] == meter) &
            (baseline['measurement'] == meas)
        ]['value']
        t = target[
            (target['meter_urn'] == meter) &
            (target['measurement'] == meas)
        ]['value']

        if b.empty or t.empty:
            continue

        b_mean = b.mean()
        b_std  = b.std()

        if b_std == 0 or pd.isna(b_std):
            continue

        # 실제 값 변화 비율 계산
        if abs(b_mean) < 1e-6:
            continue
        value_change_ratio = abs(t.mean() - b_mean) / abs(b_mean)

        z_scores = (t - b_mean) / b_std
        anomaly_mask = np.abs(z_scores) > z_threshold

        # ── 필터링 조건 ──────────────────────────────
        # 조건 1: 실제 값 변화 10% 이상
        if value_change_ratio < 0.1:
            continue
        # 조건 2: 이상 지속 2시간 이상
        if anomaly_mask.sum() < 2:
            continue

        if anomaly_mask.any():
            anomaly_ts = target[
                (target['meter_urn'] == meter) &
                (target['measurement'] == meas)
            ].loc[anomaly_mask.values, 'ts'].tolist()

            results.append({
                'date':                date,
                'meter_urn':           meter,
                'measurement':         meas,
                'anomaly_hours':       [str(t) for t in anomaly_ts],
                'anomaly_count':       int(anomaly_mask.sum()),
                'max_z_score':         round(float(np.abs(z_scores).max()), 3),
                'value_change_ratio':  round(float(value_change_ratio), 4),
                'target_mean':         round(float(t.mean()), 3),
                'baseline_mean':       round(float(b_mean), 3),
            })

    return sorted(results, key=lambda x: x['max_z_score'], reverse=True)


# ── 3단계: 전체 71일 순회 ────────────────────────────────
all_results = []

for i, date in enumerate(anomaly_dates):
    print(f"[{i+1}/{len(anomaly_dates)}] {date} 분석 중...")
    faulty = detect_faulty_meters(date, engine)
    if faulty:
        all_results.extend(faulty)
        for r in faulty[:3]:
            print(f"  {r['meter_urn']} / {r['measurement']} "
                  f"| max_z={r['max_z_score']} "
                  f"| 변화율={r['value_change_ratio']*100:.1f}% "
                  f"| {r['anomaly_count']}시간")
    else:
        print(f"  필터링 후 이상 없음")

# ── 4단계: 결과 저장 ──────────────────────────────────
result_df = pd.DataFrame(all_results)
result_df.to_csv('outputs/anomaly/faulty_meters_filtered.csv', index=False)
print(f"\n완료. 총 {len(result_df)}건 저장.")
print(result_df.head(10).to_string())