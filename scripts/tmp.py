import pandas as pd
from sqlalchemy import create_engine
engine = create_engine('postgresql://team4:teamteam4@192.168.0.12:5432/team4?options=-c%20jit%3Doff')
df = pd.read_sql("""
SELECT timestamp, value, value_type
FROM honda.h1_k14
WHERE timestamp >= '2018-01-20 22:41:00+00:00'
AND timestamp <= '2018-01-20 23:11:00+00:00'
ORDER BY timestamp, value_type
""", engine)
print(df.to_string())
