#!/bin/bash
# Focused retrieval benchmark: v1 (main) vs v2 (current branch).
# Usage: bash hack/bench/run_retrieval_bench.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

VENV="hack/tilt/.venv/bin/python"
BENCH="hack/bench/bench_retrieval.py"
RESULTS="hack/bench/retrieval_results.json"
DOCS="${BENCH_DOCS:-$HOME/Documents/ibm_redbooks}"
ROUNDS="${BENCH_ROUNDS:-5}"
REPS="${BENCH_REPS:-60}"
TOP_K="${BENCH_TOP_K:-10}"
MAX_CHUNKS="${BENCH_MAX_CHUNKS:-1000}"
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)

export PYTHONPATH="$REPO_ROOT/services"
export OPENSEARCH_HOST="${OPENSEARCH_HOST:-localhost}"
export OPENSEARCH_PORT="${OPENSEARCH_PORT:-9200}"
export OPENSEARCH_USERNAME="${OPENSEARCH_USERNAME:-admin}"
export OPENSEARCH_PASSWORD="${OPENSEARCH_PASSWORD:-AiServices@12345}"
export OPENSEARCH_DB_PREFIX="${OPENSEARCH_DB_PREFIX:-RAG_DB}"
export OPENSEARCH_INDEX_NAME="${OPENSEARCH_INDEX_NAME:-bench_retrieval}"
export OPENSEARCH_NUM_SHARDS="${OPENSEARCH_NUM_SHARDS:-1}"
export EMB_ENDPOINT="${EMB_ENDPOINT:-http://localhost:8100/emb}"
export EMB_MODEL="${EMB_MODEL:-ibm-granite/granite-embedding-278m-multilingual}"
export EMB_MAX_MODEL_LEN="${EMB_MAX_MODEL_LEN:-512}"

echo "============================================================"
echo "  Retrieval latency benchmark: v1 vs v2"
echo "  Rounds: $ROUNDS  |  Reps/round: $REPS  |  top_k: $TOP_K"
echo "  Max chunks: $MAX_CHUNKS  |  Results: $RESULTS"
echo "============================================================"

curl -fsk -u "$OPENSEARCH_USERNAME:$OPENSEARCH_PASSWORD" \
    "https://$OPENSEARCH_HOST:$OPENSEARCH_PORT/_cluster/health" -o /dev/null \
    || { echo "ERROR: OpenSearch not reachable. Start tilt first."; exit 1; }
curl -fs "http://localhost:8100/health" -o /dev/null 2>/dev/null \
    || { echo "ERROR: Model server not reachable. Start tilt first."; exit 1; }
echo "  ✓ Services up"

rm -f "$RESULTS"

# ---- v2 (current branch) ----
echo ""
echo ">>> [1/2] benchmarking $CURRENT_BRANCH (v2) ..."
"$VENV" "$BENCH" \
    --docs "$DOCS" --label "v2 ($CURRENT_BRANCH)" \
    --out "$RESULTS" --rounds "$ROUNDS" --reps "$REPS" \
    --top-k "$TOP_K" --max-chunks "$MAX_CHUNKS" \
    2>&1 | grep -v "^2026\|INFO\|it/s\]"

# ---- v1 (main) ----
echo ""
echo ">>> [2/2] switching to main (v1) ..."
if ! git diff --quiet HEAD; then
    git stash push -m "retrieval-bench: temp stash"
    STASHED=1
else
    STASHED=0
fi

git checkout main
echo "  On branch: $(git rev-parse --abbrev-ref HEAD)"

"$VENV" "$BENCH" \
    --docs "$DOCS" --label "v1 (main)" \
    --out "$RESULTS" --rounds "$ROUNDS" --reps "$REPS" \
    --top-k "$TOP_K" --max-chunks "$MAX_CHUNKS" \
    2>&1 | grep -v "^2026\|INFO\|it/s\]"

echo ""
echo ">>> Switching back to $CURRENT_BRANCH ..."
git checkout "$CURRENT_BRANCH"
[ "$STASHED" -eq 1 ] && git stash pop

echo ""
echo ">>> Final comparison:"
"$VENV" "$BENCH" --out "$RESULTS" --compare-only 2>/dev/null
