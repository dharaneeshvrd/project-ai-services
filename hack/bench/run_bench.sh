#!/bin/bash
# Runs the OpenSearch v1 vs v2 benchmark across both git branches.
#
# Usage (from repo root, while tilt is running):
#   bash hack/bench/run_bench.sh
#
# What it does:
#   1. Runs bench on the current branch (update-opensearch-client = v2)
#   2. Stashes any uncommitted changes, switches to main, runs bench for v1
#   3. Switches back and prints the side-by-side comparison
#
# Prerequisites:
#   - tilt up -f Tiltfile.rag is running (OpenSearch + model server up)
#   - No uncommitted changes that can't be stashed (working tree must be clean
#     enough that the opensearch.py swap doesn't conflict)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

VENV="hack/tilt/.venv/bin/python"
BENCH="hack/bench/bench_opensearch.py"
RESULTS="hack/bench/results.json"
DOCS="${BENCH_DOCS:-$HOME/Documents/ibm_redbooks}"
MAX_CHUNKS="${BENCH_MAX_CHUNKS:-1000}"
SEARCH_REPS="${BENCH_SEARCH_REPS:-30}"

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)

export PYTHONPATH="$REPO_ROOT/services"
export OPENSEARCH_HOST="${OPENSEARCH_HOST:-localhost}"
export OPENSEARCH_PORT="${OPENSEARCH_PORT:-9200}"
export OPENSEARCH_USERNAME="${OPENSEARCH_USERNAME:-admin}"
export OPENSEARCH_PASSWORD="${OPENSEARCH_PASSWORD:-AiServices@12345}"
export OPENSEARCH_DB_PREFIX="${OPENSEARCH_DB_PREFIX:-RAG_DB}"
export OPENSEARCH_INDEX_NAME="${OPENSEARCH_INDEX_NAME:-bench_test}"
export OPENSEARCH_NUM_SHARDS="${OPENSEARCH_NUM_SHARDS:-1}"
export EMB_ENDPOINT="${EMB_ENDPOINT:-http://localhost:8100/emb}"
export EMB_MODEL="${EMB_MODEL:-ibm-granite/granite-embedding-278m-multilingual}"
export EMB_MAX_MODEL_LEN="${EMB_MAX_MODEL_LEN:-512}"

echo "============================================================"
echo "  OpenSearch schema benchmark: v1 vs v2"
echo "  Docs dir   : $DOCS"
echo "  Max chunks : $MAX_CHUNKS"
echo "  Search reps: $SEARCH_REPS"
echo "  Results    : $RESULTS"
echo "============================================================"

# ------------------------------------------------------------------ #
# Sanity-check: OpenSearch must be reachable                          #
# ------------------------------------------------------------------ #
echo ""
echo "Checking OpenSearch connectivity..."
if ! curl -fsk -u "$OPENSEARCH_USERNAME:$OPENSEARCH_PASSWORD" \
        "https://$OPENSEARCH_HOST:$OPENSEARCH_PORT/_cluster/health" \
        -o /dev/null; then
    echo "ERROR: OpenSearch not reachable at $OPENSEARCH_HOST:$OPENSEARCH_PORT"
    echo "       Start tilt first:  tilt up -f Tiltfile.rag"
    exit 1
fi
echo "  ✓ OpenSearch is up"

# ------------------------------------------------------------------ #
# Sanity-check: model server (embeddings) must be reachable           #
# ------------------------------------------------------------------ #
EMB_HOST=$(echo "$EMB_ENDPOINT" | sed 's|http://||' | cut -d/ -f1)
if ! curl -fs "http://$EMB_HOST/health" -o /dev/null 2>/dev/null; then
    echo "ERROR: model server not reachable at http://$EMB_HOST"
    echo "       Start tilt first:  tilt up -f Tiltfile.rag"
    exit 1
fi
echo "  ✓ Model server is up"

# Remove stale results so we get a clean comparison
rm -f "$RESULTS"

# ------------------------------------------------------------------ #
# Step 1: benchmark current branch (v2)                               #
# ------------------------------------------------------------------ #
echo ""
echo ">>> Step 1/2: benchmarking $CURRENT_BRANCH (v2 schema) ..."
"$VENV" "$BENCH" \
    --docs "$DOCS" \
    --label "v2 ($CURRENT_BRANCH)" \
    --out "$RESULTS" \
    --max-chunks "$MAX_CHUNKS" \
    --chunk-chars 1800 \
    --search-reps "$SEARCH_REPS"

# ------------------------------------------------------------------ #
# Step 2: switch to main, benchmark (v1), switch back                 #
# ------------------------------------------------------------------ #
echo ""
echo ">>> Step 2/2: switching to main for v1 benchmark ..."

# Stash only if there are dirty tracked files
if ! git diff --quiet HEAD; then
    echo "  Stashing uncommitted changes..."
    git stash push -m "bench: temp stash before switching to main"
    STASHED=1
else
    STASHED=0
fi

git checkout main
echo "  On branch: $(git rev-parse --abbrev-ref HEAD)"

"$VENV" "$BENCH" \
    --docs "$DOCS" \
    --label "v1 (main)" \
    --out "$RESULTS" \
    --max-chunks "$MAX_CHUNKS" \
    --chunk-chars 1800 \
    --search-reps "$SEARCH_REPS"

echo ""
echo ">>> Switching back to $CURRENT_BRANCH ..."
git checkout "$CURRENT_BRANCH"

if [ "$STASHED" -eq 1 ]; then
    echo "  Restoring stashed changes..."
    git stash pop
fi

# ------------------------------------------------------------------ #
# Final comparison                                                     #
# ------------------------------------------------------------------ #
echo ""
echo ">>> Final comparison:"
"$VENV" "$BENCH" --out "$RESULTS" --compare-only
