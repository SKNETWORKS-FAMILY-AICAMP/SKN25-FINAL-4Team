#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path('/home/viowlet/Projects/CMS')
ENV_PATH = Path('/home/viowlet/Projects/SKN25-FINAL-4Team/.env')
LOG = ROOT / 'incoming' / 'evidence' / 'orchestrator' / 'gate4_artifacts' / 'pc3_artifact_fetch_validate.log'


def load_env() -> dict[str, str]:
    vals: dict[str, str] = {}
    for line in ENV_PATH.read_text(errors='ignore').splitlines():
        s = line.strip()
        if not s or s.startswith('#') or '=' not in s:
            continue
        key, value = s.split('=', 1)
        vals[key.strip()] = value.strip().strip('"').strip("'")
    return vals


def run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path = ROOT) -> None:
    with LOG.open('a', encoding='utf-8') as log:
        log.write('\n$ ' + ' '.join(cmd[:2] + ['...'] if cmd and cmd[0] == 'rsync' else cmd) + '\n')
        log.flush()
        proc = subprocess.run(cmd, cwd=cwd, env=env, text=True, stdout=log, stderr=subprocess.STDOUT, timeout=None)
        if proc.returncode != 0:
            raise SystemExit(proc.returncode)


def main() -> int:
    vals = load_env()
    required = ['SKN25_SSH_USER', 'SKN25_SSH_PASSWORD', 'SKN25_PC3_SSH_HOST', 'SKN25_PC3_SSH_PORT']
    missing = [key for key in required if not vals.get(key)]
    if missing:
        raise SystemExit(f'missing env keys: {missing}')

    LOG.write_text('PC3 artifact fetch/validate start\n', encoding='utf-8')
    env = os.environ.copy()
    env['SSHPASS'] = vals['SKN25_SSH_PASSWORD']
    ssh = f"sshpass -e ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p {vals['SKN25_PC3_SSH_PORT']}"
    remote = f"{vals['SKN25_SSH_USER']}@{vals['SKN25_PC3_SSH_HOST']}"

    jobs = [
        ('pmax', '/home/skn25/cms-stream-deploy/artifacts/pmax/', ROOT / 'artifacts' / 'external' / 'pmax' / 'import_pmax_production_release_20260608'),
        ('anomaly', '/home/skn25/cms-stream-deploy/artifacts/anomaly/', ROOT / 'artifacts' / 'external' / 'anomaly' / 'test6_residual_v84_3h_share_20260609'),
    ]
    for name, source, target in jobs:
        target.mkdir(parents=True, exist_ok=True)
        run(['rsync', '-a', '--delete', '--info=progress2', '-e', ssh, f'{remote}:{source}', f'{target}/'], env=env)
        run(['scripts/artifact/verify_models.sh', name], env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, cwd=ROOT)

    run(['python3', 'scripts/verify/release/gate3_verify.py'], env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, cwd=ROOT)
    with LOG.open('a', encoding='utf-8') as log:
        log.write('\nPC3 artifact fetch/validate PASS\n')
    print('PC3 artifact fetch/validate PASS')
    print(f'log={LOG}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
