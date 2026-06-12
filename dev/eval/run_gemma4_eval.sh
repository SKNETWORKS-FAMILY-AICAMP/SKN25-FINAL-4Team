#!/bin/bash
# RunPod에서 Gemma 4 12B 평가 실행 스크립트
# 사용법: bash run_gemma4_eval.sh
# 전제: Ollama가 localhost:11434에서 gemma4:12b 서빙 중

set -e

echo "=== Gemma 4 12B 평가 시작 ==="

# 1. 의존성 설치 (없으면)
pip install openai python-dotenv -q

# 2. 환경변수 설정
export LLM_PROVIDER="${LLM_PROVIDER:-ollama}"
export LLM_MODEL="${LLM_MODEL:-gemma4:12b}"
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434/v1}"
export JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.5}"
if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "❌ OPENAI_API_KEY 없음 — judge 평가를 위해 환경변수로 먼저 설정하세요."
  exit 1
fi

# 3. Ollama 상태 확인
echo "Ollama 상태 확인..."
curl -s http://localhost:11434/api/tags | python3 -c "
import sys, json
data = json.load(sys.stdin)
models = [m['name'] for m in data.get('models', [])]
print('설치된 모델:', models)
if 'gemma4:12b' not in models:
    print('❌ gemma4:12b 없음 — ollama pull gemma4:12b 먼저 실행')
    sys.exit(1)
print('✅ gemma4:12b 확인')
"

# 4. 평가 실행 (dev/eval/ 경로에서)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
python3 harness.py --quality

echo "=== 평가 완료 ==="
echo "결과 파일: reports/experiments/test07_llm_answer_gate/run_*/ 디렉토리 확인"
