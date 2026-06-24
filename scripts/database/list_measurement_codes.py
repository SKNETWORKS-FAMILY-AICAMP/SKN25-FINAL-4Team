from pathlib import Path
text=Path('/workspace/docs/specs/measurement_processing_policy.md').read_text(encoding='utf-8')
in_section=False; rows=[]
for line in text.splitlines():
    if line.startswith('## 7. Measurement dictionary'):
        in_section=True; continue
    if in_section and line.startswith('## 8.'):
        break
    if not in_section or not line.startswith('| `'):
        continue
    parts=[part.strip() for part in line.strip().strip('|').split('|')]
    if len(parts)<8: continue
    rows.append(parts[0].strip('`'))
print('count|'+str(len(rows)))
for code in rows: print('code|'+code)
