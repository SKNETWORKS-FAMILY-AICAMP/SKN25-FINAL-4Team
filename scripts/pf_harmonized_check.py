import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from sqlalchemy import create_engine
from tqdm import tqdm
import os

os.makedirs('outputs/pf_harmonized', exist_ok=True)

engine = create_engine('postgresql://team4:teamteam4@192.168.0.12:5432/team4?options=-c%20jit%3Doff')

anomaly_ze66 = [
    '2023-01-02 22:00:00', '2023-01-18 09:00:00', '2023-01-27 05:00:00',
    '2023-02-12 00:00:00', '2023-02-14 22:00:00', '2023-03-27 14:00:00',
    '2023-04-20 20:00:00', '2023-06-05 10:00:00', '2023-06-18 03:00:00',
    '2023-06-19 23:00:00', '2023-06-29 19:00:00', '2023-08-02 05:00:00',
    '2023-09-03 22:00:00', '2023-09-15 14:00:00', '2023-09-30 20:00:00',
    '2023-10-28 19:00:00', '2023-11-04 04:00:00', '2023-11-17 14:00:00',
    '2023-11-22 08:00:00', '2023-12-05 16:00:00', '2023-12-06 21:00:00',
    '2023-12-15 23:00:00', '2023-12-18 17:00:00'
]

anomaly_ze67 = [
    '2023-01-09 21:00:00', '2023-01-11 11:00:00', '2023-01-24 21:00:00',
    '2023-04-01 11:00:00', '2023-04-15 11:00:00', '2023-05-12 20:00:00',
    '2023-11-11 18:00:00', '2023-11-14 19:00:00', '2023-11-29 09:00:00',
    '2023-12-08 18:00:00', '2023-12-12 05:00:00', '2023-12-13 23:00:00',
    '2023-12-28 11:00:00'
]

all_anomalies = [('h2_ze66', ts) for ts in anomaly_ze66] + [('h2_ze67', ts) for ts in anomaly_ze67]

for meter, ts in tqdm(all_anomalies, desc='PF 이상치 시각화'):
    ts_from = pd.Timestamp(ts) - pd.Timedelta(minutes=1)
    ts_to = pd.Timestamp(ts) + pd.Timedelta(minutes=1)

    q = f"""
    SELECT timestamp, value, value_type
    FROM honda.{meter}
    WHERE timestamp >= '{ts_from}' AND timestamp <= '{ts_to}'
    AND value_type = 'PF'
    ORDER BY timestamp
    """
    df = pd.read_sql(q, engine)

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(df['timestamp'], df['value'], marker='o', color='steelblue', linewidth=1)
    ax.axhline(y=1.0, color='red', linestyle='--', linewidth=1, label='PF=1.0 기준')
    ax.axhline(y=-1.0, color='red', linestyle='--', linewidth=1)
    ax.set_title(f'{meter.upper()} | PF 이상치 구간\n이상치 시각: {ts}', fontsize=12)
    ax.set_xlabel('timestamp')
    ax.set_ylabel('PF')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    plt.xticks(rotation=45)
    ax.legend()
    ax.grid(True, alpha=0.3)

    fname = ts.replace(' ', '_').replace(':', '-')
    plt.tight_layout()
    plt.savefig(f'outputs/pf_harmonized/{meter}_{fname}.png', dpi=120)
    plt.close()

print('완료. outputs/pf_harmonized/ 에 저장됨')