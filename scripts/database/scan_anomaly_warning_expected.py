from __future__ import annotations
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict
import os, psycopg

ROOT=Path('/workspace')
ANOMALY_ROOT=ROOT/'artifacts/anomaly/3h'
START_TS='2023-01-01 00:00:00+09'
CUTOFF_TS='2023-12-01 00:00:00+09'

def parse_ts(value):
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=timezone.utc)
    return parsed

def env(path):
    d={}
    for line in Path(path).read_text(errors='ignore').splitlines():
        s=line.strip()
        if s and not s.startswith('#') and '=' in s:
            k,v=s.split('=',1); d[k.strip()]=v.strip().strip('"').strip("'")
    return d

def files():
    return sorted(ANOMALY_ROOT.glob('*/test_predictions.csv')) + sorted(ANOMALY_ROOT.glob('*/validation_predictions.csv'))

start=parse_ts(START_TS); cutoff=parse_ts(CUTOFF_TS)
keys=set(); by=defaultdict(int); raw_rows=0; paths=defaultdict(set)
for p in files():
    with p.open(newline='', encoding='utf-8') as fh:
        for row in csv.DictReader(fh):
            meter=row['meter_urn'].strip(); origin=parse_ts(row['input_end_ts'].strip())
            if origin>=cutoff: continue
            target_start=parse_ts(row['target_start_ts'].strip())
            for step in (1,2,3):
                target=target_start+timedelta(hours=step-1)
                if start<=target<cutoff:
                    raw_rows += 1
                    key=(meter, origin, step)
                    if key not in keys:
                        keys.add(key); by[meter]+=1; paths[meter].add(str(p))
print('expected_unique_total|'+str(len(keys)))
print('expected_raw_total|'+str(raw_rows))
for m in sorted(by):
    print('expected|'+m.replace('urn:ngsi-ld:Meter:','')+'|'+str(by[m])+'|files='+str(len(paths[m])))
# DB actual and gaps
e=env('/workspace/.env')
conn=psycopg.connect(host=e['DB_HOST'], port=int(e.get('DB_PORT','5432')), dbname=e['DB_NAME'], user=e['DB_USER'], password=e['DB_PASSWORD'])
with conn, conn.cursor() as cur:
    cur.execute("""
      SELECT meter_urn, forecast_origin_ts, lead_step
      FROM mart.anomaly_warning_1h
      WHERE target_ts >= %(start)s::timestamptz AND target_ts < %(cutoff)s::timestamptz
    """, {'start': START_TS, 'cutoff': CUTOFF_TS})
    actual=set((m,o,int(s)) for m,o,s in cur.fetchall())
print('actual_unique_total|'+str(len(actual)))
missing=keys-actual; extra=actual-keys
mb=defaultdict(int); eb=defaultdict(int)
for m,_,_ in missing: mb[m]+=1
for m,_,_ in extra: eb[m]+=1
print('missing_total|'+str(len(missing)))
for m in sorted(mb): print('missing|'+m.replace('urn:ngsi-ld:Meter:','')+'|'+str(mb[m]))
print('extra_total|'+str(len(extra)))
for m in sorted(eb): print('extra|'+m.replace('urn:ngsi-ld:Meter:','')+'|'+str(eb[m]))
