#!/usr/bin/env python3
"""Build a Graphify knowledge graph (graph.json) directly from the live DB.

Graphify's own markdown/LLM extraction condenses documents and drops table-body
detail, so it cannot capture our FK / ontology relationships. We already know
every relationship deterministically from the catalog and ontology rows, so this
builds the node-link `graph.json` directly — no LLM, no API key, no cost, exact.

Output is the standard Graphify node-link format consumed by `graphify tree`,
`graphify query`, and `graphify explain`.

Nodes:  one per table; one per ontology instance (meter, building, equipment
        group, hardware model, meter role, measurement code).
Edges:  table->table foreign keys; meter -> building / equipment group / role /
        hardware; meter -> measurement code (measures); meter <-> meter
        (redundant_with); instance -> its table (instance_of).

Run (venv lacks psycopg/dotenv):
    uv run --with 'psycopg[binary]' --with python-dotenv --python .venv/bin/python \
        scripts/knowledge/build.py --out graphify-out/graph.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from dotenv import load_dotenv

DEFAULT_SCHEMAS = ("live", "mart", "ontology", "ops", "qa", "reference")

# table -> (id prefix, label prefix, community)
INSTANCE_SPEC = {
    "ontology.meter": ("meter", "Meter", 6),
    "ontology.building": ("bldg", "Building", 7),
    "ontology.equipment_group": ("eg", "Equipment group", 8),
    "ontology.hardware_model": ("hw", "Hardware", 9),
    "ontology.meter_role": ("role", "Role", 10),
    "ontology.measurement_code": ("mc", "Measurement", 11),
}
SCHEMA_COMMUNITY = {"live": 0, "mart": 1, "ontology": 2, "ops": 3, "qa": 4, "reference": 5}

# FK column on ontology.meter -> friendly edge relation.
METER_FK_RELATION = {
    "building_code": "located_in",
    "equipment_group_code": "part_of",
    "meter_role_code": "has_role",
    "hardware_model_code": "uses_hardware",
}

SOURCE_FILE = "generated/db_schema.md"


def _connect(sslmode: str) -> psycopg.Connection:
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if url:
        conn = psycopg.connect(url, sslmode=sslmode, connect_timeout=10)
    else:
        conn = psycopg.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            sslmode=sslmode,
            connect_timeout=10,
        )
    conn.read_only = True
    conn.autocommit = True
    return conn


def _safe(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", str(value)).strip("_").lower()


def _tbl_id(schema: str, table: str) -> str:
    return f"tbl__{_safe(schema)}__{_safe(table)}"


def _inst_id(prefix: str, key: str) -> str:
    return f"{prefix}__{_safe(key)}"


def _pk_columns(cur, qualified: str) -> list[str]:
    cur.execute(
        """
        SELECT a.attname
        FROM pg_constraint con
        JOIN unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
        WHERE con.conrelid = %s::regclass AND con.contype = 'p'
        ORDER BY k.ord
        """,
        (qualified,),
    )
    return [r[0] for r in cur.fetchall()]


def build(conn: psycopg.Connection, schemas: tuple[str, ...]) -> dict:
    nodes: list[dict] = []
    links: list[dict] = []
    seen_nodes: set[str] = set()
    seen_links: set[tuple[str, str, str]] = set()

    def add_node(nid: str, label: str, community: int, file_type: str) -> None:
        if nid in seen_nodes:
            return
        seen_nodes.add(nid)
        nodes.append({
            "id": nid,
            "label": label,
            "norm_label": label.lower(),
            "file_type": file_type,
            "source_file": SOURCE_FILE,
            "source_location": None,
            "community": community,
        })

    def add_link(src: str, tgt: str, relation: str) -> None:
        if src not in seen_nodes or tgt not in seen_nodes or src == tgt:
            return
        key = (src, tgt, relation)
        if key in seen_links:
            return
        seen_links.add(key)
        links.append({
            "source": src,
            "target": tgt,
            "relation": relation,
            "confidence": "EXTRACTED",
            "confidence_score": 1.0,
            "weight": 1.0,
            "source_file": SOURCE_FILE,
            "source_location": None,
        })

    with conn.cursor() as cur:
        # --- table nodes ---
        cur.execute(
            """
            SELECT n.nspname, c.relname
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = ANY(%s) AND c.relkind IN ('r','p','v','m')
            ORDER BY n.nspname, c.relname
            """,
            (list(schemas),),
        )
        for schema, table in cur.fetchall():
            add_node(_tbl_id(schema, table), f"{schema}.{table}",
                     SCHEMA_COMMUNITY.get(schema, 0), "table")

        # --- table -> table foreign keys ---
        cur.execute(
            """
            SELECT sn.nspname, sc.relname, tn.nspname, tc.relname
            FROM pg_constraint con
            JOIN pg_class sc ON sc.oid = con.conrelid
            JOIN pg_namespace sn ON sn.oid = sc.relnamespace
            JOIN pg_class tc ON tc.oid = con.confrelid
            JOIN pg_namespace tn ON tn.oid = tc.relnamespace
            WHERE con.contype = 'f' AND sn.nspname = ANY(%s)
            """,
            (list(schemas),),
        )
        for ss, st, ts, tt in cur.fetchall():
            add_link(_tbl_id(ss, st), _tbl_id(ts, tt), "references")

        # --- ontology instance nodes ---
        inst_key_to_id: dict[tuple[str, str], str] = {}
        for qualified, (prefix, label_prefix, community) in INSTANCE_SPEC.items():
            schema, table = qualified.split(".", 1)
            pk = _pk_columns(cur, qualified)
            if not pk:
                continue
            pkcol = pk[0]
            cur.execute(sql.SQL("SELECT {pk} FROM {t} ORDER BY {pk}").format(
                pk=sql.Identifier(pkcol), t=sql.Identifier(schema, table)))
            for (key,) in cur.fetchall():
                nid = _inst_id(prefix, key)
                add_node(nid, f"{label_prefix} {key}", community, "instance")
                inst_key_to_id[(qualified, str(key))] = nid
                add_link(nid, _tbl_id(schema, table), "instance_of")

        meter = "ontology.meter"
        bldg = "ontology.building"

        # --- meter -> building / equipment group / role / hardware ---
        cur.execute(
            "SELECT meter_urn, building_code, equipment_group_code, meter_role_code, "
            "hardware_model_code FROM ontology.meter"
        )
        meter_targets = {
            "building_code": "ontology.building",
            "equipment_group_code": "ontology.equipment_group",
            "meter_role_code": "ontology.meter_role",
            "hardware_model_code": "ontology.hardware_model",
        }
        for urn, b, eg, role, hw in cur.fetchall():
            mid = inst_key_to_id.get((meter, str(urn)))
            if not mid:
                continue
            for col, val in (("building_code", b), ("equipment_group_code", eg),
                             ("meter_role_code", role), ("hardware_model_code", hw)):
                if val is None:
                    continue
                tgt = inst_key_to_id.get((meter_targets[col], str(val)))
                if tgt:
                    add_link(mid, tgt, METER_FK_RELATION[col])

        # --- meter -> measurement code (measures) ---
        cur.execute("SELECT DISTINCT meter_urn, measurement_code FROM ontology.meter_measurement")
        for urn, mc in cur.fetchall():
            mid = inst_key_to_id.get((meter, str(urn)))
            tid = inst_key_to_id.get(("ontology.measurement_code", str(mc)))
            if mid and tid:
                add_link(mid, tid, "measures")

        # --- meter <-> meter redundancy ---
        cur.execute("SELECT primary_meter_urn, redundant_meter_urn FROM ontology.redundancy_pair")
        for a, b in cur.fetchall():
            ai = inst_key_to_id.get((meter, str(a)))
            bi = inst_key_to_id.get((meter, str(b)))
            if ai and bi:
                add_link(ai, bi, "redundant_with")

    return {
        # Undirected so graphify query/explain traverse both ways (e.g. an
        # equipment group can reach the meters that belong to it). Relation
        # direction is still preserved in each edge's source/target/relation.
        "directed": False,
        "multigraph": False,
        "graph": {"source": "live_db", "generator": "scripts/knowledge/build.py"},
        "nodes": nodes,
        "links": links,
        "hyperedges": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="graphify-out/graph.json", help="output graph.json path")
    parser.add_argument("--schemas", default=",".join(DEFAULT_SCHEMAS))
    parser.add_argument("--sslmode", default=os.getenv("DB_SSLMODE", "prefer"))
    args = parser.parse_args(argv)

    schemas = tuple(s.strip() for s in args.schemas.split(",") if s.strip())
    with _connect(args.sslmode) as conn:
        graph = build(conn, schemas)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}: {len(graph['nodes'])} nodes, {len(graph['links'])} edges", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
