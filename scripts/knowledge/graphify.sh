#!/usr/bin/env bash
# Regenerate the DB-only Graphify knowledge graph for sLLM-facing navigation.
#
#   1. snapshot the live AWS DB structure + ontology data -> generated/db_schema.md
#      (human-readable reference / citation source)
#   2. build graphify-out/graph.json directly from the DB (deterministic, no LLM)
#   3. render the D3 tree HTML via `graphify tree`
#   4. rewrite graphify-out/manifest.json; optionally mirror to knowledge/ + wiki
#
# No LLM / API key needed: Graphify's own extractor condenses documents and cannot
# capture our FK / ontology relationships, so we build the node-link graph directly
# from the catalog (see scripts/knowledge/build.py). The result is a standard
# Graphify graph that `graphify query` / `explain` / `tree` consume.
# Graphify output is a local artifact (gitignored); only the scripts and docs commit.
#
# Usage:
#   scripts/knowledge/graphify.sh            # snapshot + build graph + tree + manifest
#   SYNC=1 scripts/knowledge/graphify.sh     # also mirror to knowledge/ + wiki
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

SOURCE_DIR="generated"
SNAPSHOT="${SOURCE_DIR}/db_schema.md"
OUT_DIR="graphify-out"
GRAPH="${OUT_DIR}/graph.json"
WIKI_DIR="${GRAPHIFY_WIKI_DIR:-$HOME/wiki/graphify/skn25_cms}"
UV_RUN=(uv run --with 'psycopg[binary]' --with python-dotenv --python .venv/bin/python)

echo "[1/4] snapshot live DB -> ${SNAPSHOT}"
"${UV_RUN[@]}" scripts/database/schema_snapshot.py --out "${SNAPSHOT}"

echo "[2/4] build graph.json directly from DB -> ${GRAPH}"
mkdir -p "${OUT_DIR}"
"${UV_RUN[@]}" scripts/knowledge/build.py --out "${GRAPH}"
# Render the D3 collapsible-tree HTML view.
graphify tree --graph "${GRAPH}" --output "${OUT_DIR}/graph_tree.html" --label skn25_cms_db

echo "[3/4] rewrite ${OUT_DIR}/manifest.json (project manifest)"
python3 - "$SNAPSHOT" "$OUT_DIR" <<'PY'
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

snapshot, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
data = snapshot.read_bytes()
manifest = {
    "project": "skn25_cms",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "graph_scope": "DB-only source scope",
    "scope": [str(snapshot)],
    "source_root": str(snapshot.parent),
    "source_file_count": 1,
    "source_files": [str(snapshot)],
    "file_sha256": {str(snapshot): hashlib.sha256(data).hexdigest()},
}
(out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"  wrote {out_dir/'manifest.json'}")
PY

if [[ "${SYNC:-0}" == "1" ]]; then
    echo "[4/4] mirror ${OUT_DIR}/ -> knowledge/graphify/ and wiki"
    mkdir -p knowledge/graphify
    cp -f "${OUT_DIR}"/graph.json "${OUT_DIR}"/manifest.json knowledge/graphify/ 2>/dev/null || true
    cp -f "${OUT_DIR}"/*.html knowledge/graphify/ 2>/dev/null || true
    if [[ -d "$(dirname "$WIKI_DIR")" ]]; then
        mkdir -p "$WIKI_DIR"
        cp -f "${OUT_DIR}"/* "$WIKI_DIR"/ 2>/dev/null || true
        echo "  mirrored to ${WIKI_DIR}"
    else
        echo "  wiki parent missing, skipped: ${WIKI_DIR}"
    fi
else
    echo "[4/4] sync skipped (set SYNC=1 to mirror to knowledge/graphify and wiki)"
fi

echo "done. graph: ${OUT_DIR}/graph.json"
