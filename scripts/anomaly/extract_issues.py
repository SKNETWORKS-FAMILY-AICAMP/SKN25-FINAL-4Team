import yaml, os, pandas as pd
from datetime import datetime, timezone

records = []
issue_dir = 'issues/automatic_issues'

fnames = [f for f in os.listdir(issue_dir) if f.endswith('.yaml')]
print(f'YAML 파일 총 {len(fnames)}개 처리 시작')

for i, fname in enumerate(fnames):
    meter_urn = fname.replace('_issues_automatic.yaml', '')
    with open(os.path.join(issue_dir, fname)) as f:
        data = yaml.safe_load(f)
    if not data:
        continue
    count = 0
    for issue_id, issue in data.items():
        reason = issue.get('reason', '')
        if reason not in ['zero', 'single_leap', 'lasting_leap']:
            continue
        try:
            records.append({
                'meter_urn': meter_urn,
                'issue_id': issue_id,
                'reason': reason,
                'time_start': datetime.fromtimestamp(int(issue['time_start']), tz=timezone.utc),
                'time_end': datetime.fromtimestamp(int(issue['time_end']), tz=timezone.utc),
                'comment': issue.get('comment', '')
            })
            count += 1
        except Exception as e:
            print(f'오류: {fname} {issue_id} {e}')
    if count > 0:
        print(f'[{i+1}/{len(fnames)}] {meter_urn}: {count}건')

print(f'\n전체 파싱 완료. 저장 중...')
df = pd.DataFrame(records)
df.to_csv('outputs/anomaly/issue_labels_zero_leap.csv', index=False)
print('총 건수:', len(df))
print(df['reason'].value_counts())
print('계량기 수:', df['meter_urn'].nunique())
