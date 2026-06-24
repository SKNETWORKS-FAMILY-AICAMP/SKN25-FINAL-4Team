#!/usr/bin/env bash
set -euo pipefail
cd /home/viowlet/Projects/CMS
LOG="incoming/evidence/orchestrator/gate5_build/gate5_docker_build.log"
{
  echo ""
  echo "## Gate5 direct deduplicated build start $(date -Is)"
} | tee -a "$LOG"
run() {
  echo "\n## $*" | tee -a "$LOG"
  "$@" 2>&1 | tee -a "$LOG"
}
run docker build -f stacks/stream_runtime/Dockerfile -t cms:phase1 .
run docker build -f stacks/edge_stream/Containerfile -t cms:edge_stream .
run docker build -f stacks/distributed_consumer/Containerfile -t cms:consumer .
run docker build -f stacks/model_serving/Containerfile -t cms:model-serving .
run docker build -f stacks/langgraph/Dockerfile -t cms:langgraph .
run docker build -f stacks/frontend/Dockerfile -t cms-frontend:local .
echo "\nGATE5_DOCKER_BUILD=PASS" | tee -a "$LOG"
