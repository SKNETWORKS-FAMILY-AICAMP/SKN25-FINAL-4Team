#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/artifact/verify_models.sh <pmax|anomaly>

Verifies fetched model artifacts against the PC3 remote checksum manifests stored in artifacts/manifests/.
USAGE
}

artifact=${1:-}
case "${artifact}" in
  pmax)
    target_dir="artifacts/external/pmax/import_pmax_production_release_20260608"
    checksum_file="artifacts/manifests/pmax_remote.sha256"
    layout_check=(python3 scripts/serving/validate_artifacts.py --pmax-root "${target_dir}" --json)
    ;;
  anomaly)
    target_dir="artifacts/external/anomaly/test6_residual_v84_3h_share_20260609"
    checksum_file="artifacts/manifests/anomaly_remote.sha256"
    layout_check=(python3 scripts/serving/validate_artifacts.py --anomaly-root "${target_dir}" --json)
    ;;
  -h|--help|'')
    usage
    exit 0
    ;;
  *)
    echo "unsupported artifact: ${artifact}" >&2
    usage >&2
    exit 2
    ;;
esac

if [[ ! -d "${target_dir}" ]]; then
  echo "missing target_dir: ${target_dir}" >&2
  exit 3
fi
if [[ ! -f "${checksum_file}" ]]; then
  echo "missing checksum_file: ${checksum_file}" >&2
  exit 4
fi

(
  cd "${target_dir}"
  sha256sum -c "../../../manifests/$(basename "${checksum_file}")"
)
PYTHONPATH=src "${layout_check[@]}"
