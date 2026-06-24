#!/usr/bin/env python3
from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path('/home/viowlet/Projects/CMS')
SERVICE_DIRS = ['src', 'stacks', 'env', 'configs', 'scripts', 'dags', 'sql', 'artifacts', 'frontend']
EXCLUDED_TOP = {'incoming', 'docs'}
FORBIDDEN_PARTS = {'node_modules', '__pycache__', '.pytest_cache', '.ruff_cache', 'logs', 'artifacts', 'runtime_secrets', 'backup', 'backups', '_backups', '_archive'}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def main() -> int:
    failed: list[str] = []
    print('CHECK=required_dirs')
    for d in SERVICE_DIRS:
        ok = (ROOT / d).is_dir()
        print(f'{d}={"PASS" if ok else "FAIL"}')
        if not ok:
            failed.append(f'missing_dir:{d}')

    print('CHECK=manifest_parse')
    manifest_path = ROOT / 'service_manifest.yaml'
    try:
        manifest = yaml.safe_load(manifest_path.read_text())
        items = manifest.get('items', []) if isinstance(manifest, dict) else []
        ok = bool(items)
    except Exception as e:
        print(f'service_manifest=FAIL {e}')
        return 1
    print(f'service_manifest={"PASS" if ok else "FAIL"} items={len(items)}')
    if not ok:
        failed.append('manifest_empty')

    print('CHECK=manifest_file_consistency')
    for idx, item in enumerate(items):
        target = ROOT / item.get('target_path', '')
        if not target.is_file():
            failed.append(f'manifest_missing_file:{idx}:{target}')
            continue
        if item.get('checksum') != sha256(target):
            failed.append(f'checksum_mismatch:{target}')
    print('manifest_file_consistency=' + ('PASS' if not [f for f in failed if f.startswith(('manifest_missing_file','checksum_mismatch'))] else 'FAIL'))

    print('CHECK=forbidden_paths')
    forbidden=[]
    service_files=[]
    for d in SERVICE_DIRS:
        for p in (ROOT/d).rglob('*'):
            if p.is_file():
                service_files.append(p)
                rel=p.relative_to(ROOT).as_posix()
                if FORBIDDEN_PARTS.intersection(set(p.parts)):
                    forbidden.append(rel)
                if (p.name == '.env' or p.name.endswith('.env')) and not p.name.endswith('.env.example'):
                    forbidden.append(rel)
    print(f'service_files={len(service_files)}')
    print('forbidden_paths=' + ('PASS' if not forbidden else 'FAIL'))
    if forbidden:
        print('forbidden_sample=' + repr(forbidden[:30]))
        failed.append('forbidden_paths')

    print('CHECK=secret_scan')
    high_patterns=[
        re.compile(r'AKIA[0-9A-Z]{16}'),
        re.compile(r'sk-[A-Za-z0-9_-]{20,}'),
        re.compile(r'postgres(?:ql)?://[^\s/@]+:[^\s/@]+@'),
    ]
    assign_pat=re.compile(r'(?i)(password|passwd|token|api[_-]?key|secret|credential)[ \t]*[:=][ \t]*(?!<REDACTED>|<redacted>|true|false|none|template|required|pending|not_recorded|값|사용하지|출력하지|$)([^\s,}]+)')
    secret_hits=[]
    for p in service_files + [ROOT/'service_manifest.yaml', ROOT/'import_decisions.yaml', ROOT/'env_key_manifest.yaml', ROOT/'build_matrix.yaml']:
        try:
            text=p.read_text(errors='ignore')
        except Exception:
            continue
        for pat in high_patterns:
            for m in pat.finditer(text):
                secret_hits.append((str(p.relative_to(ROOT)), m.group(0)[:120]))
        if p.suffix.lower() in {'.yml','.yaml','.json','.conf','.ini','.cfg','.toml'} or p.name.endswith('.env.example'):
            for m in assign_pat.finditer(text):
                val=m.group(2).strip().strip('"\'')
                if val in {'', '***', '<REDACTED>', 'changeme', 'example', 'dummy'}:
                    continue
                if val.startswith('${') or val.startswith('?Set') or ':-' in val or ':?' in val or val.endswith('-local-only') or val == 'gradient-gauge':
                    continue
                secret_hits.append((str(p.relative_to(ROOT)), m.group(0)[:120]))
    print('secret_scan=' + ('PASS' if not secret_hits else 'FAIL'))
    if secret_hits:
        print('secret_hits=' + repr(secret_hits[:30]))
        failed.append('secret_scan')

    print('CHECK=python_syntax')
    py_files=[p for p in service_files if p.suffix=='.py']
    syntax_fail=[]
    for p in py_files:
        text=p.read_text(errors='ignore')
        try:
            ast.parse(text, filename=str(p))
        except Exception as e:
            syntax_fail.append((str(p.relative_to(ROOT)), str(e)[:180]))
    print(f'python_files={len(py_files)}')
    print('python_syntax=' + ('PASS' if not syntax_fail else 'FAIL'))
    if syntax_fail:
        print('syntax_fail_sample=' + repr(syntax_fail[:20]))
        failed.append('python_syntax')

    print('CHECK=shell_syntax')
    shell_files=[p for p in service_files if p.suffix=='.sh']
    shell_fail=[]
    for p in shell_files:
        proc=subprocess.run(['bash','-n',str(p)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            shell_fail.append((str(p.relative_to(ROOT)), proc.stderr[:180]))
    print(f'shell_files={len(shell_files)}')
    print('shell_syntax=' + ('PASS' if not shell_fail else 'FAIL'))
    if shell_fail:
        print('shell_fail_sample=' + repr(shell_fail[:20]))
        failed.append('shell_syntax')

    print('CHECK=root_entries')
    entries=sorted(p.name for p in ROOT.iterdir())
    print('root_entries=' + ','.join(entries))

    print('SUMMARY=' + ('PASS' if not failed else 'FAIL'))
    if failed:
        print('FAILED=' + repr(failed[:50]))
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
