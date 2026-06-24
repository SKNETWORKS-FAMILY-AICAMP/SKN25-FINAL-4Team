#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/artifact/fetch_models.sh <pmax|anomaly>

Environment variables:
  CMS_PC3_ARTIFACT_SSH   SSH target for PC3, for example skn25@host
  CMS_PC3_ARTIFACT_PORT  SSH port, default 22
  CMS_PC3_ARTIFACT_ROOT  Remote artifact root, default /home/skn25/cms-stream-deploy/artifacts

This script fetches model artifacts into artifacts/external/ without committing binary files.
Run the validation command from artifacts/manifests/manifest.yaml after fetch.
USAGE
}

artifact=${1:-}
if [[ -z "${artifact}" || "${artifact}" == "-h" || "${artifact}" == "--help" ]]; then
  usage
  exit 0
fi

case "${artifact}" in
  pmax)
    remote_subdir="pmax"
    target_dir="artifacts/external/pmax/import_pmax_production_release_20260608"
    ;;
  anomaly)
    remote_subdir="anomaly"
    target_dir="artifacts/external/anomaly/test6_residual_v84_3h_share_20260609"
    ;;
  *)
    echo "unsupported artifact: ${artifact}" >&2
    usage >&2
    exit 2
    ;;
esac

ssh_target=${CMS_PC3_ARTIFACT_SSH:?Set CMS_PC3_ARTIFACT_SSH, for example skn25@pc3-host}
ssh_port=${CMS_PC3_ARTIFACT_PORT:-22}
remote_root=${CMS_PC3_ARTIFACT_ROOT:-/home/skn25/cms-stream-deploy/artifacts}
remote_path="${remote_root%/}/${remote_subdir}/"

mkdir -p "${target_dir}"
rsync -a --info=progress2 --delete \
  -e "ssh -p ${ssh_port}" \
  "${ssh_target}:${remote_path}" \
  "${target_dir}/"

find "${target_dir}" -type f -print0 | sort -z | xargs -0 sha256sum > "${target_dir}/checksums.sha256"
echo "fetched=${artifact} target=${target_dir}"
echo "checksums=${target_dir}/checksums.sha256"
