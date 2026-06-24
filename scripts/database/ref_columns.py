from pathlib import Path
import psycopg
vals={}
for line in Path('/workspace/.env').read_text(errors='ignore').splitlines():
    s=line.strip()
    if s and not s.startswith('#') and '=' in s:
        k,v=s.split('=',1); vals[k.strip()]=v.strip().strip('"').strip("'")
with psycopg.connect(host=vals['DB_HOST'], port=int(vals.get('DB_PORT','5432')), dbname=vals['DB_NAME'], user=vals['DB_USER'], password=vals['DB_PASSWORD'], connect_timeout=10, autocommit=True) as conn:
  with conn.cursor() as cur:
    for table in ['corrected_resampled_1h','corrected_resampled_15min']:
      print('## reference.'+table)
      cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='reference' AND table_name=%s ORDER BY ordinal_position", (table,))
      for r in cur.fetchall(): print('|'.join(r))
