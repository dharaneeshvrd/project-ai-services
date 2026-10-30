"""
OpenSearch v1 vs v2 schema benchmark.

Measures:
  - Bulk insert throughput  (chunks/sec, total wall time)
  - Search latency          (p50 / p95 / p99 across N queries, all three modes)
  - Retrieval overhead      (pure result-object building: field extraction in the
                             service layer — _source dict walk in v1 vs docvalue
                             list unwrap in v2) at top_k=10
  - Response payload size   (bytes per hit, from the actual response)

Usage (run from the repo root, venv already activated):

  # On branch update-opensearch-client (v2):
  OPENSEARCH_HOST=localhost OPENSEARCH_PORT=9200 \
  OPENSEARCH_USERNAME=admin OPENSEARCH_PASSWORD=AiServices@12345 \
  OPENSEARCH_DB_PREFIX=RAG_DB OPENSEARCH_INDEX_NAME=bench_test \
  EMB_ENDPOINT=http://localhost:8100/emb \
  EMB_MODEL=ibm-granite/granite-embedding-278m-multilingual \
  EMB_MAX_MODEL_LEN=512 \
  PYTHONPATH=services \
  hack/tilt/.venv/bin/python hack/bench/bench_opensearch.py \
      --docs ~/Documents/ibm_redbooks \
      --label v2 \
      --out hack/bench/results.json

  # Then switch to main and run again with --label v1

Run both labels to get a side-by-side comparison printed at the end.
Re-run with --compare-only to just print the comparison from a previous results.json.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from statistics import median, quantiles

# ---------------------------------------------------------------------------
# PDF / text extraction
# ---------------------------------------------------------------------------

def _extract_pdf(path: Path) -> str:
    from pdfminer.high_level import extract_text
    return extract_text(str(path)) or ""

def _extract_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")

EXTRACTORS = {
    ".pdf": _extract_pdf,
    ".txt": _extract_text,
    ".md":  _extract_text,
}

def extract(path: Path) -> str:
    fn = EXTRACTORS.get(path.suffix.lower())
    if fn is None:
        return ""
    try:
        return fn(path)
    except Exception as e:
        print(f"  [warn] could not extract {path.name}: {e}", file=sys.stderr)
        return ""

# ---------------------------------------------------------------------------
# Chunking (same logic as hack/tilt/ingest.py)
# ---------------------------------------------------------------------------

def chunk_text(text: str, size: int = 1800, overlap: int = 200):
    text = text.strip()
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(text.rfind("\n\n", start, end), text.rfind(". ", start, end))
            if cut > start + size // 2:
                end = cut + 1
        piece = text[start:end].strip()
        if piece:
            yield piece
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)

def build_chunks(path: Path, docs_dir: Path, chunk_chars: int = 1800) -> list[dict]:
    text = extract(path)
    if not text.strip():
        return []
    pieces = list(chunk_text(text, size=chunk_chars))
    doc_id = hashlib.md5(str(path.resolve()).encode()).hexdigest()
    return [
        {
            "page_content": piece,
            "filename":     path.name,
            "doc_id":       doc_id,
            "type":         "text",
            "source":       str(path.relative_to(docs_dir)),
            "language":     "en",
            "chunk_index":  i,
            "total_chunks": len(pieces),
        }
        for i, piece in enumerate(pieces)
    ]

# ---------------------------------------------------------------------------
# Benchmark helpers
# ---------------------------------------------------------------------------

def _pct(values: list[float], p: int) -> float:
    """Return the p-th percentile of values."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = max(0, int(len(sorted_v) * p / 100) - 1)
    return round(sorted_v[idx], 4)

def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(median(values), 4)

def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)

# ---------------------------------------------------------------------------
# Search payload size measurement
# ---------------------------------------------------------------------------

def measure_response_bytes(client, index_name: str, query_vector: list) -> dict:
    """
    Run one search of each mode and record the raw response JSON size in bytes,
    plus the per-hit payload size.

    Uses schema-agnostic queries (no language filter, match_all for sparse) so
    the measurement works identically on both v1 and v2 indexes.
    """
    results = {}
    modes = {
        "dense": {
            "size": 5,
            "query": {
                "knn": {
                    "embedding": {
                        "vector": query_vector,
                        "k": 15,
                        "filter": {"match_all": {}},
                    }
                }
            },
        },
        "sparse": {
            "size": 5,
            "query": {"match": {"text": "cloud computing infrastructure"}},
        },
    }
    for mode, body in modes.items():
        raw = client.search(
            index=index_name,
            body=body,
            params={"search_pipeline": "hybrid_pipeline"},
        )
        raw_bytes = len(json.dumps(raw).encode())
        hits = raw.get("hits", {}).get("hits", [])
        per_hit = len(json.dumps(hits[0]).encode()) if hits else 0
        results[mode] = {"response_bytes": raw_bytes, "first_hit_bytes": per_hit, "n_hits": len(hits)}
    return results

# ---------------------------------------------------------------------------
# Core benchmark
# ---------------------------------------------------------------------------

def run_benchmark(label: str, chunks: list[dict], query_texts: list[str], n_search_reps: int = 30) -> dict:
    """
    Run the full insert + search benchmark against the currently active opensearch.py code.
    Returns a dict of measurements tagged with `label`.
    """
    import common.misc_utils as misc_utils
    import common.db_utils as db
    from common.emb_utils import get_embedder
    from common.misc_utils import get_embedding_endpoint

    misc_utils.create_llm_session(pool_maxsize=4)
    emb_cfg = get_embedding_endpoint()
    embedder = get_embedder(emb_cfg["emb_model"], emb_cfg["emb_endpoint"], emb_cfg["max_model_len"])
    store = db.get_vector_store()

    print(f"\n{'='*60}")
    print(f"  Benchmarking label: {label}")
    print(f"  Index:  {store.index_name}")
    print(f"  Chunks: {len(chunks)}")
    print(f"{'='*60}")

    # ------------------------------------------------------------------ #
    # 1. Clean slate — delete the bench index if it already exists        #
    # ------------------------------------------------------------------ #
    if store.client.indices.exists(index=store.index_name):
        print(f"  Dropping existing index {store.index_name}...")
        store.client.indices.delete(index=store.index_name)

    # ------------------------------------------------------------------ #
    # 2. INSERT benchmark                                                  #
    # ------------------------------------------------------------------ #
    print(f"\n[INSERT] inserting {len(chunks)} chunks ...")
    t0 = time.perf_counter()
    ok = store.insert_chunks(chunks, embedding=embedder, batch_size=50)
    insert_wall = time.perf_counter() - t0
    if not ok:
        print("  ERROR: insert_chunks returned False", file=sys.stderr)

    insert_cps = round(len(chunks) / insert_wall, 2)   # chunks per second
    print(f"  Wall time : {insert_wall:.2f}s")
    print(f"  Throughput: {insert_cps} chunks/sec")

    # ------------------------------------------------------------------ #
    # 3. Warm-up (one search per mode to prime the connection pool)       #
    # ------------------------------------------------------------------ #
    probe_vec = embedder.embed_query(query_texts[0])
    for mode in ("dense", "sparse", "hybrid"):
        try:
            store.search(query_texts[0], vector=probe_vec, top_k=5, mode=mode)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 4. SEARCH benchmark (end-to-end incl. embed call)                   #
    # ------------------------------------------------------------------ #
    latencies: dict[str, list[float]] = {"dense": [], "sparse": [], "hybrid": []}
    print(f"\n[SEARCH] {n_search_reps} reps × {len(query_texts)} queries × 3 modes ...")
    for _rep in range(n_search_reps):
        for q in query_texts:
            vec = embedder.embed_query(q)
            for mode in ("dense", "sparse", "hybrid"):
                t = time.perf_counter()
                try:
                    store.search(q, vector=vec, top_k=5, mode=mode)
                except Exception as e:
                    print(f"  [warn] search error ({mode}): {e}", file=sys.stderr)
                latencies[mode].append(time.perf_counter() - t)

    search_stats = {}
    for mode, lats in latencies.items():
        lats_ms = [l * 1000 for l in lats]
        search_stats[mode] = {
            "n":   len(lats_ms),
            "mean_ms":  _mean(lats_ms),
            "p50_ms":   _median(lats_ms),
            "p95_ms":   _pct(lats_ms, 95),
            "p99_ms":   _pct(lats_ms, 99),
        }
        print(f"  {mode:8s}  mean={search_stats[mode]['mean_ms']:6.1f}ms  "
              f"p50={search_stats[mode]['p50_ms']:6.1f}ms  "
              f"p95={search_stats[mode]['p95_ms']:6.1f}ms  "
              f"p99={search_stats[mode]['p99_ms']:6.1f}ms")

    # ------------------------------------------------------------------ #
    # 5. RETRIEVAL benchmark — pure result-building overhead              #
    #                                                                      #
    # Uses a pre-computed vector so the embedding call is outside the      #
    # timed region. top_k=10 stresses per-hit field extraction:           #
    #   v1: deserialise _source JSON + walk metadata dict                 #
    #   v2: unwrap docvalue_fields lists (columnar, no JSON parsing)      #
    # Run more reps (n_search_reps * 2) to get stable tail percentiles.  #
    # ------------------------------------------------------------------ #
    retrieval_reps = n_search_reps * 2
    retrieval_lats: dict[str, list[float]] = {"dense": [], "sparse": [], "hybrid": []}
    print(f"\n[RETRIEVAL] {retrieval_reps} reps × {len(query_texts)} queries × 3 modes  (pre-computed vectors, top_k=10) ...")
    # Pre-compute all query vectors so embed cost is fully excluded
    query_vecs = [embedder.embed_query(q) for q in query_texts]
    for _rep in range(retrieval_reps):
        for q, vec in zip(query_texts, query_vecs):
            for mode in ("dense", "sparse", "hybrid"):
                t = time.perf_counter()
                try:
                    store.search(q, vector=vec, top_k=10, mode=mode)
                except Exception as e:
                    print(f"  [warn] retrieval error ({mode}): {e}", file=sys.stderr)
                retrieval_lats[mode].append(time.perf_counter() - t)

    retrieval_stats = {}
    for mode, lats in retrieval_lats.items():
        lats_ms = [l * 1000 for l in lats]
        retrieval_stats[mode] = {
            "n":        len(lats_ms),
            "mean_ms":  _mean(lats_ms),
            "p50_ms":   _median(lats_ms),
            "p95_ms":   _pct(lats_ms, 95),
            "p99_ms":   _pct(lats_ms, 99),
        }
        print(f"  {mode:8s}  mean={retrieval_stats[mode]['mean_ms']:6.1f}ms  "
              f"p50={retrieval_stats[mode]['p50_ms']:6.1f}ms  "
              f"p95={retrieval_stats[mode]['p95_ms']:6.1f}ms  "
              f"p99={retrieval_stats[mode]['p99_ms']:6.1f}ms")

    # ------------------------------------------------------------------ #
    # 6. Response payload size                                             #
    # ------------------------------------------------------------------ #
    print(f"\n[SIZE] measuring response payload sizes ...")
    size_stats = measure_response_bytes(store.client, store.index_name, probe_vec.tolist() if hasattr(probe_vec, "tolist") else probe_vec)
    for mode, s in size_stats.items():
        print(f"  {mode:8s}  response={s['response_bytes']:,} bytes  first_hit={s['first_hit_bytes']:,} bytes  hits={s['n_hits']}")

    return {
        "label":     label,
        "chunks":    len(chunks),
        "insert": {
            "wall_sec":       round(insert_wall, 3),
            "chunks_per_sec": insert_cps,
        },
        "search":    search_stats,
        "retrieval": retrieval_stats,
        "payload":   size_stats,
    }

# ---------------------------------------------------------------------------
# Comparison printer
# ---------------------------------------------------------------------------

def print_comparison(results: list[dict]):
    if len(results) < 2:
        print("\nNeed at least 2 labels to compare. Run with both --label v1 and --label v2.")
        return

    by_label = {r["label"]: r for r in results}
    labels = list(by_label.keys())

    # Always show v1 → v2 if both present; otherwise first → second
    v1_key = next((l for l in labels if "v1" in l.lower()), None)
    v2_key = next((l for l in labels if "v2" in l.lower()), None)
    if v1_key and v2_key:
        a, b = v1_key, v2_key
    else:
        a, b = labels[0], labels[1]
    ra, rb = by_label[a], by_label[b]

    def pct_change(old, new):
        if old == 0:
            return "N/A"
        delta = (new - old) / old * 100
        sign = "+" if delta >= 0 else ""
        return f"{sign}{delta:.1f}%"

    def better(metric, old, new):
        # for throughput higher is better; for latency/size lower is better
        if metric == "throughput":
            return new > old
        return new < old

    def arrow(metric, old, new):
        if old == 0:
            return ""
        if better(metric, old, new):
            return "✅"
        return "🔴"

    print(f"\n{'='*70}")
    print(f"  COMPARISON:  {a}  →  {b}")
    print(f"{'='*70}")

    # Insert
    print(f"\n  INSERT THROUGHPUT")
    print(f"  {'metric':<28} {a:>12} {b:>12} {'change':>10}  ")
    print(f"  {'-'*66}")
    for metric, key, higher_is_better in [
        ("wall time (sec)",   "wall_sec",       False),
        ("chunks/sec",        "chunks_per_sec", True),
    ]:
        va = ra["insert"][key]
        vb = rb["insert"][key]
        ch = pct_change(va, vb)
        ar = arrow("throughput" if higher_is_better else "latency", va, vb)
        print(f"  {metric:<28} {va:>12.2f} {vb:>12.2f} {ch:>10}  {ar}")

    # Search latency
    print(f"\n  SEARCH LATENCY — end-to-end incl. embed (ms)")
    print(f"  {'mode / metric':<28} {a:>12} {b:>12} {'change':>10}  ")
    print(f"  {'-'*66}")
    for mode in ("dense", "sparse", "hybrid"):
        for stat in ("mean_ms", "p50_ms", "p95_ms", "p99_ms"):
            va = ra["search"][mode][stat]
            vb = rb["search"][mode][stat]
            ch = pct_change(va, vb)
            ar = arrow("latency", va, vb)
            print(f"  {mode+'/'+stat:<28} {va:>12.1f} {vb:>12.1f} {ch:>10}  {ar}")

    # Retrieval latency (pre-computed vectors, top_k=10)
    if "retrieval" in ra and "retrieval" in rb:
        print(f"\n  RETRIEVAL LATENCY — pre-computed vector, top_k=10 (ms)")
        print(f"  {'mode / metric':<28} {a:>12} {b:>12} {'change':>10}  ")
        print(f"  {'-'*66}")
        for mode in ("dense", "sparse", "hybrid"):
            for stat in ("mean_ms", "p50_ms", "p95_ms", "p99_ms"):
                va = ra["retrieval"][mode][stat]
                vb = rb["retrieval"][mode][stat]
                ch = pct_change(va, vb)
                ar = arrow("latency", va, vb)
                print(f"  {mode+'/'+stat:<28} {va:>12.1f} {vb:>12.1f} {ch:>10}  {ar}")

    # Payload size
    print(f"\n  RESPONSE PAYLOAD SIZE (bytes)")
    print(f"  {'mode / metric':<28} {a:>12} {b:>12} {'change':>10}  ")
    print(f"  {'-'*66}")
    for mode in ra["payload"]:
        if mode not in rb["payload"]:
            continue
        for metric in ("response_bytes", "first_hit_bytes"):
            va = ra["payload"][mode][metric]
            vb = rb["payload"][mode][metric]
            ch = pct_change(va, vb)
            ar = arrow("latency", va, vb)
            print(f"  {mode+'/'+metric:<28} {va:>12,} {vb:>12,} {ch:>10}  {ar}")

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docs",         default="~/Documents/ibm_redbooks", help="Directory of PDFs/txts to index")
    ap.add_argument("--label",        default="run", help="Label for this run (e.g. v1, v2)")
    ap.add_argument("--out",          default="hack/bench/results.json", help="JSON file to accumulate results")
    ap.add_argument("--search-reps",  type=int, default=30, help="Search repetitions per query")
    ap.add_argument("--max-chunks",   type=int, default=1000, help="Cap total chunks to keep the run short")
    ap.add_argument("--chunk-chars",  type=int, default=1800, help="Target chunk size in characters (~512 tokens at 3.5 chars/tok)")
    ap.add_argument("--compare-only", action="store_true", help="Skip benchmark, just print comparison from --out")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing results
    existing: list[dict] = []
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    if args.compare_only:
        print_comparison(existing)
        return 0

    # ------------------------------------------------------------------ #
    # Build chunks from PDFs                                              #
    # ------------------------------------------------------------------ #
    docs_dir = Path(args.docs).expanduser()
    if not docs_dir.is_dir():
        print(f"ERROR: docs directory not found: {docs_dir}", file=sys.stderr)
        return 1

    supported_exts = set(EXTRACTORS.keys())
    doc_files = sorted(p for p in docs_dir.rglob("*") if p.is_file() and p.suffix.lower() in supported_exts)
    if not doc_files:
        print(f"ERROR: no supported files ({', '.join(sorted(supported_exts))}) found in {docs_dir}", file=sys.stderr)
        return 1

    print(f"Found {len(doc_files)} document(s) in {docs_dir}")
    print(f"Chunk size: {args.chunk_chars} chars (~{args.chunk_chars // 4} tokens)")
    all_chunks: list[dict] = []
    for p in doc_files:
        c = build_chunks(p, docs_dir, chunk_chars=args.chunk_chars)
        print(f"  {p.name}: {len(c)} chunks")
        all_chunks.extend(c)
        if len(all_chunks) >= args.max_chunks:
            break

    all_chunks = all_chunks[:args.max_chunks]
    print(f"Total chunks to index: {len(all_chunks)}")

    # Representative queries drawn from IBM Redbooks topics
    query_texts = [
        "What are the best practices for IBM cloud infrastructure deployment?",
        "How does IBM Power Systems handle memory management?",
        "Explain the IBM Z mainframe security architecture",
        "What is the role of LPAR in IBM virtualization?",
        "How to configure high availability on IBM systems?",
    ]

    # ------------------------------------------------------------------ #
    # Run benchmark                                                        #
    # ------------------------------------------------------------------ #
    result = run_benchmark(
        label=args.label,
        chunks=all_chunks,
        query_texts=query_texts,
        n_search_reps=args.search_reps,
    )

    # Replace existing result with same label, append otherwise
    existing = [r for r in existing if r.get("label") != args.label]
    existing.append(result)
    out_path.write_text(json.dumps(existing, indent=2))
    print(f"\nResults saved → {out_path}")

    # Print comparison if we now have 2+ labels
    print_comparison(existing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
