#!/usr/bin/env bash
# validate_opensearch_podman.sh
#
# Validates OpenSearch chunks from inside the digitize backend container
# (OpenSearch is NOT exposed on the host — only reachable via the internal
# pod network using its container-hostname).
#
# Usage:
#   # Copy once:
#   scp hack/bench/validate_opensearch_podman.sh root@10.48.64.151:/tmp/
#   # Run:
#   ssh root@10.48.64.151 'bash /tmp/validate_opensearch_podman.sh'
#
# ENV overrides (all have working defaults for this deployment):
#   DIGITIZE_CONTAINER   default: digitize-2311b7426a-backend-server
#   OS_HOST              default: opensearch-484627381b   (from OPENSEARCH_HOST env in digitize)
#   OS_PORT              default: 9200
#   OS_USER              default: admin
#   OS_PASS              default: read from /var/lib/containers/storage/volumes/opensearch-secret-*/password
#   OS_DB_PREFIX         default: rag_db   (OPENSEARCH_DB_PREFIX lowercased)
#   OS_INDEX_NAME        default: rag_index (OPENSEARCH_INDEX_NAME lowercased)
#   TOP_K                default: 5

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✅  $*${NC}"; }
err()  { echo -e "${RED}❌  $*${NC}"; }
info() { echo -e "${BLUE}ℹ️   $*${NC}"; }
warn() { echo -e "${YELLOW}⚠️   $*${NC}"; }
hdr()  { echo; echo "════════════════════════════════════════════════════════"; echo "  $*"; echo "════════════════════════════════════════════════════════"; }

# ── Defaults (auto-detected from the live deployment) ─────────────────────────
DIGITIZE_CONTAINER="${DIGITIZE_CONTAINER:-digitize-2311b7426a-backend-server}"
OS_HOST="${OS_HOST:-opensearch-484627381b}"
OS_PORT="${OS_PORT:-9200}"
OS_USER="${OS_USER:-admin}"
OS_DB_PREFIX="${OS_DB_PREFIX:-rag_db}"
OS_INDEX_NAME="${OS_INDEX_NAME:-rag_index}"
TOP_K="${TOP_K:-5}"

# Read password from secret volume if not already set
if [[ -z "${OS_PASS:-}" ]]; then
    SECRET_VOL=$(ls /var/lib/containers/storage/volumes/ 2>/dev/null \
        | grep "^opensearch-secret-" | head -1 || true)
    if [[ -n "$SECRET_VOL" ]]; then
        OS_PASS=$(cat "/var/lib/containers/storage/volumes/${SECRET_VOL}/_data/password" 2>/dev/null || true)
    fi
    OS_PASS="${OS_PASS:-Ev\$nts@EWnkvol7c}"
fi

# Compute the actual index name (matches _generate_index_name in opensearch.py)
INDEX_NAME=$(python3 -c "
import hashlib
prefix = '${OS_DB_PREFIX}'.lower()
name   = '${OS_INDEX_NAME}'.lower()
h      = hashlib.md5(name.encode()).hexdigest()
print(f'{prefix}_{h}')
")

# Helper: run a curl command INSIDE the digitize container
os_curl() {
    local path="$1"; shift
    local extra_args=("$@")
    podman exec "$DIGITIZE_CONTAINER" \
        curl -sk -u "${OS_USER}:${OS_PASS}" \
        "${extra_args[@]}" \
        "https://${OS_HOST}:${OS_PORT}${path}"
}

# ── Step 1: confirm digitize container is running ─────────────────────────────
hdr "Step 1 — Confirm digitize container"
if ! podman inspect "$DIGITIZE_CONTAINER" &>/dev/null; then
    err "Container not found: $DIGITIZE_CONTAINER"
    echo "Running containers:"
    podman ps --format "table {{.Names}}\t{{.Status}}"
    exit 1
fi
ok "Container running: $DIGITIZE_CONTAINER"
info "Reaching OpenSearch at: https://${OS_HOST}:${OS_PORT}"
info "Computed index name:    $INDEX_NAME"

# ── Step 2: cluster health ────────────────────────────────────────────────────
hdr "Step 2 — OpenSearch cluster health"
HEALTH=$(os_curl "/_cluster/health?pretty")
echo "$HEALTH"

STATUS=$(echo "$HEALTH" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")
case "$STATUS" in
    green)  ok  "Cluster status: green" ;;
    yellow) warn "Cluster status: yellow (single-node — expected for this deployment)" ;;
    red)    err  "Cluster status: RED — has unassigned/failed shards!" ;;
    *)      err  "Could not parse cluster health (status=$STATUS)" ;;
esac

# ── Step 3: list all rag_ indices ─────────────────────────────────────────────
hdr "Step 3 — RAG indices in OpenSearch"
os_curl "/_cat/indices/rag_*?v&h=index,docs.count,store.size,health" || \
    warn "No rag_* indices found yet — nothing ingested?"

# ── Step 4: schema version ────────────────────────────────────────────────────
hdr "Step 4 — Schema version check for $INDEX_NAME"
MAPPING=$(os_curl "/${INDEX_NAME}/_mapping" 2>/dev/null)

if echo "$MAPPING" | python3 -c "import json,sys; d=json.load(sys.stdin); exit(0 if d else 1)" 2>/dev/null; then
    SCHEMA_VER=$(echo "$MAPPING" | python3 -c "
import json, sys
m = json.load(sys.stdin)
idx = next(iter(m.values()))
v = idx['mappings'].get('_meta', {}).get('schema_version', 'MISSING')
print(v)
" 2>/dev/null || echo "parse_error")

    HAS_DOC_ID=$(echo "$MAPPING" | python3 -c "
import json, sys
m = json.load(sys.stdin)
props = next(iter(m.values()))['mappings'].get('properties', {})
print('yes' if 'doc_id' in props else 'no')
" 2>/dev/null || echo "no")

    HAS_METADATA_OBJ=$(echo "$MAPPING" | python3 -c "
import json, sys
m = json.load(sys.stdin)
props = next(iter(m.values()))['mappings'].get('properties', {})
print('yes' if 'metadata' in props else 'no')
" 2>/dev/null || echo "no")

    if [[ "$SCHEMA_VER" == "2" ]]; then
        ok "schema_version = 2  (v2 — flat top-level doc_values layout ✓)"
    elif [[ "$SCHEMA_VER" == "1" ]] || [[ "$SCHEMA_VER" == "MISSING" ]]; then
        err "schema_version = ${SCHEMA_VER}  (LEGACY v1 — migration has NOT run!)"
        warn "Restart the digitize service to trigger auto-migration."
    else
        warn "schema_version = ${SCHEMA_VER} (unexpected value)"
    fi

    [[ "$HAS_DOC_ID"       == "yes" ]] && ok  "Top-level 'doc_id' field present (v2 ✓)" \
                                          || err "Missing top-level 'doc_id' — still nested?"
    [[ "$HAS_METADATA_OBJ" == "no"  ]] && ok  "No nested 'metadata' object (v2 clean ✓)" \
                                          || warn "Nested 'metadata' object present — likely v1"
else
    err "Index not found or empty: $INDEX_NAME"
    warn "Has anything been ingested through the digitize service?"
    exit 0
fi

# ── Step 5: retrieve sample chunks via v2 path ────────────────────────────────
hdr "Step 5 — Retrieve ${TOP_K} sample chunks  (v2 path: _source:false + docvalue_fields)"

SAMPLE=$(os_curl "/${INDEX_NAME}/_search" \
    -H "Content-Type: application/json" \
    -d "{
      \"size\": ${TOP_K},
      \"_source\": false,
      \"docvalue_fields\": [\"chunk_id\",\"doc_id\",\"filename\",\"language\",
                             \"page_number\",\"chunk_index\",\"total_chunks\",
                             \"type\",\"source\",\"created_at\"],
      \"stored_fields\": [\"text\"],
      \"query\": {\"match_all\": {}}
    }")

TOTAL=$(echo "$SAMPLE" | python3 -c "
import json, sys
r = json.load(sys.stdin)
t = r['hits']['total']
print(t['value'] if isinstance(t, dict) else t)
" 2>/dev/null || echo "?")
ok "Total chunks in index: $TOTAL"
echo

echo "$SAMPLE" | python3 -c "
import json, sys, textwrap

data = json.load(sys.stdin)
hits = data['hits']['hits']
if not hits:
    print('  (no hits — index may be empty)')
    sys.exit(0)

def fv(fields, name, default='N/A'):
    v = fields.get(name)
    if v is None: return default
    return v[0] if isinstance(v, list) else v

for i, hit in enumerate(hits, 1):
    f = hit.get('fields', {})
    text_raw = fv(f, 'text')
    snippet  = textwrap.shorten(str(text_raw), width=110, placeholder=' [...]')
    print(f'  ── Chunk {i} ──────────────────────────────────────────────────')
    print(f'  _id          : {hit[\"_id\"]}')
    print(f'  chunk_id     : {fv(f, \"chunk_id\")}')
    print(f'  doc_id       : {fv(f, \"doc_id\")}')
    print(f'  filename     : {fv(f, \"filename\")}')
    print(f'  language     : {fv(f, \"language\")}')
    print(f'  page_number  : {fv(f, \"page_number\")}')
    print(f'  chunk_index  : {fv(f, \"chunk_index\")} / {fv(f, \"total_chunks\")}')
    print(f'  type         : {fv(f, \"type\")}')
    print(f'  source       : {fv(f, \"source\")}')
    print(f'  created_at   : {fv(f, \"created_at\")}')
    print(f'  text snippet : {snippet}')
    print()
"

# ── Step 6: validate v2 retrieval path explicitly ─────────────────────────────
hdr "Step 6 — Validate v2 retrieval path (_source absent, fields via docvalue_fields)"

FIELD_CHECK=$(os_curl "/${INDEX_NAME}/_search" \
    -H "Content-Type: application/json" \
    -d '{
      "size": 1,
      "_source": false,
      "docvalue_fields": ["doc_id","filename","page_number"],
      "stored_fields":   ["text"],
      "query": {"match_all": {}}
    }')

echo "$FIELD_CHECK" | python3 -c "
import json, sys
r = json.load(sys.stdin)
hits = r['hits']['hits']
if not hits:
    print('  (no hits — cannot validate retrieval path)')
    sys.exit(0)
h = hits[0]
source_absent = '_source' not in h
fields_present = 'fields' in h and 'doc_id' in h.get('fields', {})
text_present   = 'text' in h.get('fields', {})
ok   = '\033[0;32m✅ \033[0m'
fail = '\033[0;31m❌ \033[0m'
print(f'{ok if source_absent   else fail}_source absent from response : {source_absent}')
print(f'{ok if fields_present  else fail}doc_id in docvalue_fields    : {fields_present}')
print(f'{ok if text_present    else fail}text in stored_fields        : {text_present}')
if source_absent and fields_present and text_present:
    print()
    print('\033[0;32m  v2 retrieval path confirmed ✓\033[0m')
else:
    print()
    print('\033[0;31m  v2 retrieval path check FAILED\033[0m')
"

# ── Step 7: quick keyword search (sparse) ─────────────────────────────────────
hdr "Step 7 — Sparse keyword search (sanity check)"

# Pick a word from the first chunk's text as a test query
TEST_WORD=$(echo "$SAMPLE" | python3 -c "
import json, sys
hits = json.load(sys.stdin)['hits']['hits']
if not hits: print('document'); sys.exit()
text = ''
f = hits[0].get('fields', {})
v = f.get('text')
text = (v[0] if isinstance(v, list) else v) if v else ''
words = [w.strip('.,;:()[]\"') for w in text.split() if len(w) > 5]
print(words[0] if words else 'document')
" 2>/dev/null || echo "document")

info "Test query word: '$TEST_WORD'"

KW_RESULT=$(os_curl "/${INDEX_NAME}/_search" \
    -H "Content-Type: application/json" \
    -d "{
      \"size\": 3,
      \"_source\": false,
      \"docvalue_fields\": [\"doc_id\",\"filename\",\"chunk_index\"],
      \"stored_fields\": [\"text\"],
      \"query\": {\"match\": {\"text\": \"$TEST_WORD\"}}
    }")

KW_HITS=$(echo "$KW_RESULT" | python3 -c "
import json, sys
r = json.load(sys.stdin)
t = r['hits']['total']
print(t['value'] if isinstance(t, dict) else t)
" 2>/dev/null || echo "0")

if [[ "$KW_HITS" -gt 0 ]]; then
    ok "Sparse search for '$TEST_WORD' returned $KW_HITS hit(s)"
else
    warn "Sparse search returned 0 hits for '$TEST_WORD' (index may be empty or word not present)"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
hdr "Validation complete"
ok "Index validated: $INDEX_NAME"
echo
info "To run a custom keyword search from the digitize container:"
echo
echo "  podman exec $DIGITIZE_CONTAINER \\"
echo "    curl -sk -u '${OS_USER}:${OS_PASS}' \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"query\":{\"match\":{\"text\":\"YOUR KEYWORD\"}},\"size\":5,\"_source\":false,\"stored_fields\":[\"text\"],\"docvalue_fields\":[\"doc_id\",\"filename\",\"page_number\"]}' \\"
echo "    'https://${OS_HOST}:${OS_PORT}/${INDEX_NAME}/_search' | python3 -m json.tool"
