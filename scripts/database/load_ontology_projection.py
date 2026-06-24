#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path('/workspace')
METER_METADATA_PATH = ROOT / 'src/cms/knowledge/meter_metadata.json'
MEASUREMENT_POLICY_PATH = ROOT / 'docs/specs/measurement_processing_policy.md'
ONTOLOGY_TTL_PATH = ROOT / 'docs/ontology/cms.ttl'
SEED_PATH = ROOT / 'artifacts/ontology/ontology_seed.json'
STARTED_AT = datetime.now(timezone.utc)
LOAD_ID = 'ontology_projection_20260616'

ROLE_SIGN_CONVENTIONS = {
    'consumption': 'positive_consumption_negative_quality_candidate',
    'production': 'negative_production_positive_noise_or_reverse_flow',
    'thermal_flow': 'direction_depends_on_equipment_context',
    'weather': 'no_sign_convention',
}


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(errors='ignore').splitlines():
        s = line.strip()
        if s and not s.startswith('#') and '=' in s:
            k, v = s.split('=', 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def db_kwargs() -> dict[str, Any]:
    env = load_dotenv(ROOT / '.env')
    missing = [key for key in ['DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASSWORD'] if not env.get(key)]
    if missing:
        raise SystemExit({'status': 'missing_db_env', 'missing': missing})
    return {
        'host': env['DB_HOST'],
        'port': int(env.get('DB_PORT', '5432')),
        'dbname': env['DB_NAME'],
        'user': env['DB_USER'],
        'password': env['DB_PASSWORD'],
        'connect_timeout': 10,
    }


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def slug(value: str) -> str:
    value = value.replace('.', '_').replace('-', '_').replace('/', '_').replace(' ', '_')
    value = re.sub(r'[^A-Za-z0-9_]+', '_', value)
    value = re.sub(r'_+', '_', value).strip('_')
    return value or 'unknown'


def parse_measurement_policy() -> list[dict[str, Any]]:
    text = MEASUREMENT_POLICY_PATH.read_text(encoding='utf-8')
    rows: list[dict[str, Any]] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith('## 7. Measurement dictionary'):
            in_section = True
            continue
        if in_section and line.startswith('## 8.'):
            break
        if not in_section or not line.startswith('| `'):
            continue
        parts = [part.strip() for part in line.strip().strip('|').split('|')]
        if len(parts) < 8:
            continue
        code = parts[0].strip('`')
        if code in {'measurement', 'Pa'}:
            # Pa is documented from the paper vocabulary but is explicitly absent from
            # the active harmonized archive inventory. Keep this initial projection to
            # the 40 observed archive codes reviewed for db-helper grounding.
            continue
        rows.append({
            'measurement_code': code,
            'description': parts[1],
            'family': parts[2],
            'source_update_mode': parts[3],
            'one_min_policy': parts[4],
            'aggregate_policy': parts[5],
            'missing_policy': parts[6],
            'canonical_eligibility': parts[7],
        })
    if len(rows) != 40:
        raise SystemExit({'status': 'unexpected_measurement_count', 'count': len(rows)})
    return rows


def load_sources() -> dict[str, Any]:
    metadata = json.loads(METER_METADATA_PATH.read_text(encoding='utf-8'))
    seed = json.loads(SEED_PATH.read_text(encoding='utf-8'))
    meters = metadata['meters']
    groups = metadata['equipment_groups']
    redundancy_pairs = metadata['redundancy_pairs']
    measurements = parse_measurement_policy()
    hardware_models = seed['hardware_models']
    hardware_assignments = seed['hardware_assignments']
    triples = seed.get('triples', [])

    expected = {
        'meters': 81,
        'equipment_groups': 17,
        'redundancy_pairs': 12,
        'hardware_models': 6,
        'hardware_assignments': 81,
        'measurement_codes': 40,
        'ttl_triples': 3006,
    }
    actual = {
        'meters': len(meters),
        'equipment_groups': len(groups),
        'redundancy_pairs': len(redundancy_pairs),
        'hardware_models': len(hardware_models),
        'hardware_assignments': len(hardware_assignments),
        'measurement_codes': len(measurements),
        'ttl_triples': int(seed.get('ttl_triples', len(triples))),
    }
    if actual != expected:
        raise SystemExit({'status': 'unexpected_source_counts', 'expected': expected, 'actual': actual})

    return {
        'metadata': metadata,
        'meters': meters,
        'groups': groups,
        'redundancy_pairs': redundancy_pairs,
        'measurements': measurements,
        'hardware_models': hardware_models,
        'hardware_assignments': hardware_assignments,
        'triples': triples,
        'expected': expected,
        'source_paths': {
            'meter_metadata': rel(METER_METADATA_PATH),
            'measurement_policy': rel(MEASUREMENT_POLICY_PATH),
            'ontology_ttl': rel(ONTOLOGY_TTL_PATH),
            'seed': rel(SEED_PATH),
        },
        'source_hashes': {
            'meter_metadata': sha256(METER_METADATA_PATH),
            'measurement_policy': sha256(MEASUREMENT_POLICY_PATH),
            'ontology_ttl': sha256(ONTOLOGY_TTL_PATH),
            'seed': sha256(SEED_PATH),
        },
    }


DDL = [
    'CREATE SCHEMA IF NOT EXISTS ontology',
    '''
    CREATE TABLE IF NOT EXISTS ontology.load_log (
        load_id text PRIMARY KEY,
        status text NOT NULL CHECK (status IN ('started', 'success', 'failed')),
        source_paths jsonb NOT NULL,
        source_hashes jsonb NOT NULL,
        counts jsonb NOT NULL,
        loaded_at timestamptz NOT NULL DEFAULT now()
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS ontology.building (
        building_code text PRIMARY KEY,
        label text NOT NULL,
        source_path text NOT NULL,
        source_hash text NOT NULL,
        load_id text NOT NULL,
        loaded_at timestamptz NOT NULL DEFAULT now()
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS ontology.equipment_group (
        equipment_group_code text PRIMARY KEY,
        label text NOT NULL,
        meter_count integer NOT NULL CHECK (meter_count >= 0),
        anomaly_priority integer NOT NULL CHECK (anomaly_priority BETWEEN 1 AND 4),
        source_path text NOT NULL,
        source_hash text NOT NULL,
        load_id text NOT NULL,
        loaded_at timestamptz NOT NULL DEFAULT now()
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS ontology.meter_role (
        meter_role_code text PRIMARY KEY,
        label text NOT NULL,
        sign_convention text NOT NULL,
        source_path text NOT NULL,
        source_hash text NOT NULL,
        load_id text NOT NULL,
        loaded_at timestamptz NOT NULL DEFAULT now()
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS ontology.hardware_model (
        hardware_model_code text PRIMARY KEY,
        manufacturer text NOT NULL,
        model_name text NOT NULL,
        meter_category text NOT NULL,
        source_path text NOT NULL,
        source_hash text NOT NULL,
        load_id text NOT NULL,
        loaded_at timestamptz NOT NULL DEFAULT now()
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS ontology.meter (
        meter_urn text PRIMARY KEY,
        meter_domain text NOT NULL CHECK (meter_domain IN ('electricity', 'thermal', 'weather')),
        meter_role_code text NOT NULL REFERENCES ontology.meter_role(meter_role_code),
        equipment_group_code text NOT NULL REFERENCES ontology.equipment_group(equipment_group_code),
        building_code text NOT NULL REFERENCES ontology.building(building_code),
        equipment_name text NOT NULL DEFAULT '',
        anomaly_priority integer NOT NULL CHECK (anomaly_priority BETWEEN 1 AND 4),
        sign_convention text NOT NULL,
        hardware_model_code text NOT NULL REFERENCES ontology.hardware_model(hardware_model_code),
        source_path text NOT NULL,
        source_hash text NOT NULL,
        load_id text NOT NULL,
        loaded_at timestamptz NOT NULL DEFAULT now()
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS ontology.redundancy_pair (
        primary_meter_urn text NOT NULL REFERENCES ontology.meter(meter_urn),
        redundant_meter_urn text NOT NULL REFERENCES ontology.meter(meter_urn),
        equipment_group_code text NOT NULL REFERENCES ontology.equipment_group(equipment_group_code),
        equipment_name text NOT NULL DEFAULT '',
        note text NOT NULL DEFAULT '',
        source_path text NOT NULL,
        source_hash text NOT NULL,
        load_id text NOT NULL,
        loaded_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (primary_meter_urn, redundant_meter_urn),
        CHECK (primary_meter_urn <> redundant_meter_urn)
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS ontology.measurement_code (
        measurement_code text PRIMARY KEY,
        description text NOT NULL,
        family text NOT NULL,
        source_update_mode text NOT NULL,
        one_min_policy text NOT NULL,
        aggregate_policy text NOT NULL,
        missing_policy text NOT NULL,
        canonical_eligibility text NOT NULL,
        source_path text NOT NULL,
        source_hash text NOT NULL,
        load_id text NOT NULL,
        loaded_at timestamptz NOT NULL DEFAULT now()
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS ontology.triple (
        subject text NOT NULL,
        predicate text NOT NULL,
        object_value text NOT NULL,
        object_kind text NOT NULL CHECK (object_kind IN ('uri', 'literal', 'bnode')),
        source_path text NOT NULL,
        source_hash text NOT NULL,
        load_id text NOT NULL,
        loaded_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (subject, predicate, object_value)
    )
    ''',
    '''
    CREATE OR REPLACE VIEW ontology.meter_context AS
    SELECT
        m.meter_urn,
        m.meter_domain,
        m.meter_role_code,
        m.equipment_group_code,
        g.label AS equipment_group_label,
        m.building_code,
        m.equipment_name,
        m.anomaly_priority,
        m.sign_convention,
        m.hardware_model_code,
        h.manufacturer,
        h.model_name,
        rp.redundant_meter_urn,
        rr.primary_meter_urn AS redundant_for_meter
    FROM ontology.meter m
    JOIN ontology.equipment_group g USING (equipment_group_code)
    JOIN ontology.hardware_model h USING (hardware_model_code)
    LEFT JOIN ontology.redundancy_pair rp ON rp.primary_meter_urn = m.meter_urn
    LEFT JOIN ontology.redundancy_pair rr ON rr.redundant_meter_urn = m.meter_urn
    ''',
]


def apply_ddl(cur) -> None:
    for stmt in DDL:
        cur.execute(stmt)


def upsert_many(cur, sql: str, rows: list[dict[str, Any]]) -> None:
    if rows:
        cur.executemany(sql, rows)


def load_projection(conn, sources: dict[str, Any]) -> dict[str, int]:
    metadata = sources['metadata']
    meters = sources['meters']
    groups = sources['groups']
    role_codes = sorted({rec['role'] for rec in meters.values()})
    buildings = sorted({rec['building'] for rec in meters.values()})
    hardware_assignments = {rec['meter_urn']: rec['hardware_model_code'] for rec in sources['hardware_assignments']}
    hashes = sources['source_hashes']
    paths = sources['source_paths']

    with conn.cursor() as cur:
        apply_ddl(cur)
        cur.execute(
            '''INSERT INTO ontology.load_log (load_id, status, source_paths, source_hashes, counts, loaded_at)
               VALUES (%s, 'started', %s, %s, %s, now())
               ON CONFLICT (load_id) DO UPDATE SET status='started', source_paths=EXCLUDED.source_paths, source_hashes=EXCLUDED.source_hashes, counts=EXCLUDED.counts, loaded_at=EXCLUDED.loaded_at''',
            (LOAD_ID, Jsonb(paths), Jsonb(hashes), Jsonb(sources['expected'])),
        )
        upsert_many(cur, '''
            INSERT INTO ontology.building (building_code, label, source_path, source_hash, load_id, loaded_at)
            VALUES (%(building_code)s, %(label)s, %(source_path)s, %(source_hash)s, %(load_id)s, now())
            ON CONFLICT (building_code) DO UPDATE SET label=EXCLUDED.label, source_path=EXCLUDED.source_path, source_hash=EXCLUDED.source_hash, load_id=EXCLUDED.load_id, loaded_at=EXCLUDED.loaded_at
        ''', [{'building_code': code, 'label': code, 'source_path': paths['meter_metadata'], 'source_hash': hashes['meter_metadata'], 'load_id': LOAD_ID} for code in buildings])
        upsert_many(cur, '''
            INSERT INTO ontology.equipment_group (equipment_group_code, label, meter_count, anomaly_priority, source_path, source_hash, load_id, loaded_at)
            VALUES (%(code)s, %(label)s, %(meter_count)s, %(priority)s, %(source_path)s, %(source_hash)s, %(load_id)s, now())
            ON CONFLICT (equipment_group_code) DO UPDATE SET label=EXCLUDED.label, meter_count=EXCLUDED.meter_count, anomaly_priority=EXCLUDED.anomaly_priority, source_path=EXCLUDED.source_path, source_hash=EXCLUDED.source_hash, load_id=EXCLUDED.load_id, loaded_at=EXCLUDED.loaded_at
        ''', [{'code': code, 'label': rec.get('label', code), 'meter_count': int(rec['meter_count']), 'priority': int(rec['priority']), 'source_path': paths['meter_metadata'], 'source_hash': hashes['meter_metadata'], 'load_id': LOAD_ID} for code, rec in sorted(groups.items())])
        upsert_many(cur, '''
            INSERT INTO ontology.meter_role (meter_role_code, label, sign_convention, source_path, source_hash, load_id, loaded_at)
            VALUES (%(code)s, %(label)s, %(sign)s, %(source_path)s, %(source_hash)s, %(load_id)s, now())
            ON CONFLICT (meter_role_code) DO UPDATE SET label=EXCLUDED.label, sign_convention=EXCLUDED.sign_convention, source_path=EXCLUDED.source_path, source_hash=EXCLUDED.source_hash, load_id=EXCLUDED.load_id, loaded_at=EXCLUDED.loaded_at
        ''', [{'code': code, 'label': code, 'sign': ROLE_SIGN_CONVENTIONS[code], 'source_path': paths['meter_metadata'], 'source_hash': hashes['meter_metadata'], 'load_id': LOAD_ID} for code in role_codes])
        upsert_many(cur, '''
            INSERT INTO ontology.hardware_model (hardware_model_code, manufacturer, model_name, meter_category, source_path, source_hash, load_id, loaded_at)
            VALUES (%(hardware_model_code)s, %(manufacturer)s, %(model_name)s, %(meter_category)s, %(source_path)s, %(source_hash)s, %(load_id)s, now())
            ON CONFLICT (hardware_model_code) DO UPDATE SET manufacturer=EXCLUDED.manufacturer, model_name=EXCLUDED.model_name, meter_category=EXCLUDED.meter_category, source_path=EXCLUDED.source_path, source_hash=EXCLUDED.source_hash, load_id=EXCLUDED.load_id, loaded_at=EXCLUDED.loaded_at
        ''', [{**rec, 'source_path': paths['ontology_ttl'], 'source_hash': hashes['ontology_ttl'], 'load_id': LOAD_ID} for rec in sources['hardware_models']])
        upsert_many(cur, '''
            INSERT INTO ontology.meter (meter_urn, meter_domain, meter_role_code, equipment_group_code, building_code, equipment_name, anomaly_priority, sign_convention, hardware_model_code, source_path, source_hash, load_id, loaded_at)
            VALUES (%(meter_urn)s, %(meter_domain)s, %(meter_role_code)s, %(equipment_group_code)s, %(building_code)s, %(equipment_name)s, %(anomaly_priority)s, %(sign_convention)s, %(hardware_model_code)s, %(source_path)s, %(source_hash)s, %(load_id)s, now())
            ON CONFLICT (meter_urn) DO UPDATE SET meter_domain=EXCLUDED.meter_domain, meter_role_code=EXCLUDED.meter_role_code, equipment_group_code=EXCLUDED.equipment_group_code, building_code=EXCLUDED.building_code, equipment_name=EXCLUDED.equipment_name, anomaly_priority=EXCLUDED.anomaly_priority, sign_convention=EXCLUDED.sign_convention, hardware_model_code=EXCLUDED.hardware_model_code, source_path=EXCLUDED.source_path, source_hash=EXCLUDED.source_hash, load_id=EXCLUDED.load_id, loaded_at=EXCLUDED.loaded_at
        ''', [
            {
                'meter_urn': urn,
                'meter_domain': rec['domain'],
                'meter_role_code': rec['role'],
                'equipment_group_code': rec['group'],
                'building_code': rec['building'],
                'equipment_name': rec.get('equipment', ''),
                'anomaly_priority': int(rec['priority']),
                'sign_convention': rec['sign'],
                'hardware_model_code': hardware_assignments[urn],
                'source_path': paths['meter_metadata'],
                'source_hash': hashes['meter_metadata'],
                'load_id': LOAD_ID,
            }
            for urn, rec in sorted(meters.items())
        ])
        upsert_many(cur, '''
            INSERT INTO ontology.redundancy_pair (primary_meter_urn, redundant_meter_urn, equipment_group_code, equipment_name, note, source_path, source_hash, load_id, loaded_at)
            VALUES (%(primary)s, %(redundant)s, %(group)s, %(equipment)s, %(note)s, %(source_path)s, %(source_hash)s, %(load_id)s, now())
            ON CONFLICT (primary_meter_urn, redundant_meter_urn) DO UPDATE SET equipment_group_code=EXCLUDED.equipment_group_code, equipment_name=EXCLUDED.equipment_name, note=EXCLUDED.note, source_path=EXCLUDED.source_path, source_hash=EXCLUDED.source_hash, load_id=EXCLUDED.load_id, loaded_at=EXCLUDED.loaded_at
        ''', [{**rec, 'note': rec.get('note', ''), 'source_path': paths['meter_metadata'], 'source_hash': hashes['meter_metadata'], 'load_id': LOAD_ID} for rec in sources['redundancy_pairs']])
        upsert_many(cur, '''
            INSERT INTO ontology.measurement_code (measurement_code, description, family, source_update_mode, one_min_policy, aggregate_policy, missing_policy, canonical_eligibility, source_path, source_hash, load_id, loaded_at)
            VALUES (%(measurement_code)s, %(description)s, %(family)s, %(source_update_mode)s, %(one_min_policy)s, %(aggregate_policy)s, %(missing_policy)s, %(canonical_eligibility)s, %(source_path)s, %(source_hash)s, %(load_id)s, now())
            ON CONFLICT (measurement_code) DO UPDATE SET description=EXCLUDED.description, family=EXCLUDED.family, source_update_mode=EXCLUDED.source_update_mode, one_min_policy=EXCLUDED.one_min_policy, aggregate_policy=EXCLUDED.aggregate_policy, missing_policy=EXCLUDED.missing_policy, canonical_eligibility=EXCLUDED.canonical_eligibility, source_path=EXCLUDED.source_path, source_hash=EXCLUDED.source_hash, load_id=EXCLUDED.load_id, loaded_at=EXCLUDED.loaded_at
        ''', [{**rec, 'source_path': paths['measurement_policy'], 'source_hash': hashes['measurement_policy'], 'load_id': LOAD_ID} for rec in sources['measurements']])
        cur.execute('DELETE FROM ontology.triple WHERE source_path = %s', (paths['ontology_ttl'],))
        upsert_many(cur, '''
            INSERT INTO ontology.triple (subject, predicate, object_value, object_kind, source_path, source_hash, load_id, loaded_at)
            VALUES (%(subject)s, %(predicate)s, %(object_value)s, %(object_kind)s, %(source_path)s, %(source_hash)s, %(load_id)s, now())
            ON CONFLICT (subject, predicate, object_value) DO UPDATE SET object_kind=EXCLUDED.object_kind, source_path=EXCLUDED.source_path, source_hash=EXCLUDED.source_hash, load_id=EXCLUDED.load_id, loaded_at=EXCLUDED.loaded_at
        ''', [{**rec, 'source_path': paths['ontology_ttl'], 'source_hash': hashes['ontology_ttl'], 'load_id': LOAD_ID} for rec in sources['triples']])
        cur.execute(
            '''UPDATE ontology.load_log SET status='success', counts=%s, loaded_at=now() WHERE load_id=%s''',
            (Jsonb(sources['expected']), LOAD_ID),
        )
    return sources['expected']


def verify(conn) -> dict[str, Any]:
    out: dict[str, Any] = {}
    with conn.cursor() as cur:
        checks = {
            'building': 'SELECT count(*) FROM ontology.building',
            'equipment_group': 'SELECT count(*) FROM ontology.equipment_group',
            'meter_role': 'SELECT count(*) FROM ontology.meter_role',
            'hardware_model': 'SELECT count(*) FROM ontology.hardware_model',
            'meter': 'SELECT count(*) FROM ontology.meter',
            'redundancy_pair': 'SELECT count(*) FROM ontology.redundancy_pair',
            'measurement_code': 'SELECT count(*) FROM ontology.measurement_code',
            'triple': 'SELECT count(*) FROM ontology.triple WHERE source_path = %s',
            'orphan_meter_group': 'SELECT count(*) FROM ontology.meter m LEFT JOIN ontology.equipment_group g USING (equipment_group_code) WHERE g.equipment_group_code IS NULL',
            'orphan_meter_building': 'SELECT count(*) FROM ontology.meter m LEFT JOIN ontology.building b USING (building_code) WHERE b.building_code IS NULL',
            'orphan_meter_role': 'SELECT count(*) FROM ontology.meter m LEFT JOIN ontology.meter_role r USING (meter_role_code) WHERE r.meter_role_code IS NULL',
            'orphan_meter_hardware': 'SELECT count(*) FROM ontology.meter m LEFT JOIN ontology.hardware_model h USING (hardware_model_code) WHERE h.hardware_model_code IS NULL',
            'orphan_redundancy_primary': 'SELECT count(*) FROM ontology.redundancy_pair rp LEFT JOIN ontology.meter m ON m.meter_urn = rp.primary_meter_urn WHERE m.meter_urn IS NULL',
            'orphan_redundancy_redundant': 'SELECT count(*) FROM ontology.redundancy_pair rp LEFT JOIN ontology.meter m ON m.meter_urn = rp.redundant_meter_urn WHERE m.meter_urn IS NULL',
            'same_redundancy_endpoint': 'SELECT count(*) FROM ontology.redundancy_pair WHERE primary_meter_urn = redundant_meter_urn',
        }
        for name, sql in checks.items():
            if name == 'triple':
                cur.execute(sql, (rel(ONTOLOGY_TTL_PATH),))
            else:
                cur.execute(sql)
            out[name] = int(cur.fetchone()[0])
        cur.execute("SELECT meter_urn, equipment_group_code, building_code, meter_role_code, redundant_meter_urn FROM ontology.meter_context WHERE meter_urn = 'H2.Z64'")
        out['sample_h2_z64'] = list(cur.fetchone())
        cur.execute("SELECT count(*) FROM ontology.meter WHERE equipment_group_code='server_power'")
        out['server_power_meter_count'] = int(cur.fetchone()[0])
        cur.execute("SELECT count(*) FROM ontology.redundancy_pair WHERE equipment_group_code='server_power'")
        out['server_power_redundancy_pair_count'] = int(cur.fetchone()[0])
        cur.execute('SELECT building_code, count(*) FROM ontology.meter GROUP BY 1 ORDER BY 1')
        out['building_counts'] = dict(cur.fetchall())
        cur.execute('SELECT hardware_model_code, count(*) FROM ontology.meter GROUP BY 1 ORDER BY 1')
        out['hardware_distribution'] = dict(cur.fetchall())
        cur.execute("SELECT run_id, status FROM ops.pmax_log WHERE run_id='preload_2023_jan_nov_20260616_pmax'")
        out['non_ontology_smoke_pmax_log_visible'] = bool(cur.fetchone())
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sources = load_sources()
    summary = {
        'stage': 'ontology_projection_prepared',
        'load_id': LOAD_ID,
        'counts': sources['expected'],
        'source_paths': sources['source_paths'],
        'ddl_objects': [
            'ontology.load_log', 'ontology.building', 'ontology.equipment_group', 'ontology.meter_role',
            'ontology.hardware_model', 'ontology.meter', 'ontology.redundancy_pair',
            'ontology.measurement_code', 'ontology.triple', 'ontology.meter_context'
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    if args.dry_run:
        return 0
    with psycopg.connect(**db_kwargs()) as conn:
        try:
            counts = load_projection(conn, sources)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    with psycopg.connect(**db_kwargs()) as conn:
        result = verify(conn)
    print(json.dumps({'stage': 'ontology_projection_done', 'counts': counts, 'verification': result}, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
