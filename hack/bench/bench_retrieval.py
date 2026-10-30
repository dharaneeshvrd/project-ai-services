"""
Focused retrieval latency benchmark — v1 vs v2 index schema.

Inserts 1000 chunks (1800-char / ~450-token) once, then runs N rounds of
search calls with pre-computed vectors (embed cost fully excluded) so we
measure only the OS query + per-hit field-extraction path.

Usage (run from repo root with the tilt venv):

  # v2 branch:
  PYTHONPATH=services \
  OPENSEARCH_HOST=localhost OPENSEARCH_PORT=9200 \
  OPENSEARCH_USERNAME=admin OPENSEARCH_PASSWORD=AiServices@12345 \
  OPENSEARCH_DB_PREFIX=RAG_DB OPENSEARCH_INDEX_NAME=bench_retrieval \
  EMB_ENDPOINT=http://localhost:8100/emb \
  EMB_MODEL=ibm-granite/granite-embedding-278m-multilingual \
  EMB_MAX_MODEL_LEN=512 \
  hack/tilt/.venv/bin/python hack/bench/bench_retrieval.py \
      --docs ~/Documents/ibm_redbooks \
      --label v2 --rounds 5 --reps 60 \
      --out hack/bench/retrieval_results.json

  # main branch (v1):  same command with --label v1
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from statistics import median

# ---------------------------------------------------------------------------
# PDF / text extraction (same as bench_opensearch.py)
# ---------------------------------------------------------------------------

def _extract_pdf(path: Path) -> str:
    from pdfminer.high_level import extract_text
    return extract_text(str(path)) or ""

def _extract_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")

EXTRACTORS = {".pdf": _extract_pdf, ".txt": _extract_text, ".md": _extract_text}

def extract(path: Path) -> str:
    fn = EXTRACTORS.get(path.suffix.lower())
    if fn is None:
        return ""
    try:
        return fn(path)
    except Exception as e:
        print(f"  [warn] {path.name}: {e}", file=sys.stderr)
        return ""

# ---------------------------------------------------------------------------
# Chunking
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

def build_chunks(path: Path, docs_dir: Path) -> list[dict]:
    text = extract(path)
    if not text.strip():
        return []
    pieces = list(chunk_text(text))
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
# Stats helpers
# ---------------------------------------------------------------------------

def _pct(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    sv = sorted(values)
    idx = max(0, int(len(sv) * p / 100) - 1)
    return round(sv[idx], 4)

def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0

def _median(values: list[float]) -> float:
    return round(median(values), 4) if values else 0.0

def _stats(lats_ms: list[float]) -> dict:
    return {
        "n":       len(lats_ms),
        "mean_ms": _mean(lats_ms),
        "p50_ms":  _median(lats_ms),
        "p75_ms":  _pct(lats_ms, 75),
        "p95_ms":  _pct(lats_ms, 95),
        "p99_ms":  _pct(lats_ms, 99),
    }

# ---------------------------------------------------------------------------
# Core benchmark
# ---------------------------------------------------------------------------

QUERIES = [
    "What are the best practices for IBM cloud infrastructure deployment?",
    "How does IBM Power Systems handle memory management?",
    "Explain the IBM Z mainframe security architecture",
    "What is the role of LPAR in IBM virtualization?",
    "How to configure high availability on IBM systems?",
    "Describe the IBM storage area network connectivity options",
    "What are the performance tuning techniques for Db2 on IBM Z?",
    "How does IBM POWER handle simultaneous multithreading?",
]

def run(label: str, docs_dir: Path, max_chunks: int,
        rounds: int, reps_per_round: int, top_k: int) -> dict:

    import common.misc_utils as misc_utils
    import common.db_utils as db
    from common.emb_utils import get_embedder
    from common.misc_utils import get_embedding_endpoint

    misc_utils.create_llm_session(pool_maxsize=4)
    emb_cfg = get_embedding_endpoint()
    embedder = get_embedder(emb_cfg["emb_model"], emb_cfg["emb_endpoint"], emb_cfg["max_model_len"])
    store = db.get_vector_store()

    print(f"\n{'='*62}")
    print(f"  label   : {label}")
    print(f"  index   : {store.index_name}")
    print(f"  rounds  : {rounds}  ×  reps/round: {reps_per_round}")
    print(f"  queries : {len(QUERIES)}  |  top_k: {top_k}")
    print(f"{'='*62}")

    # ------------------------------------------------------------------ #
    # 1. Insert (clean slate)                                              #
    # ------------------------------------------------------------------ #
    doc_files = sorted(
        p for p in docs_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in EXTRACTORS
    )
    all_chunks: list[dict] = []
    for p in doc_files:
        all_chunks.extend(build_chunks(p, docs_dir))
        if len(all_chunks) >= max_chunks:
            break
    all_chunks = all_chunks[:max_chunks]

    if store.client.indices.exists(index=store.index_name):
        print(f"  Dropping existing index ...")
        store.client.indices.delete(index=store.index_name)

    print(f"  Inserting {len(all_chunks)} chunks ...")
    t0 = time.perf_counter()
    store.insert_chunks(all_chunks, embedding=embedder, batch_size=50)
    insert_sec = round(time.perf_counter() - t0, 2)
    print(f"  ✓ Inserted in {insert_sec}s  ({len(all_chunks)/insert_sec:.1f} chunks/sec)")

    # ------------------------------------------------------------------ #
    # 2. Pre-compute all query vectors                                     #
    # ------------------------------------------------------------------ #
    print(f"\n  Pre-computing {len(QUERIES)} query embeddings ...")
    query_vecs = [embedder.embed_query(q) for q in QUERIES]

    # ------------------------------------------------------------------ #
    # 3. Warm-up (discard JIT / connection pool cold starts)              #
    # ------------------------------------------------------------------ #
    print(f"  Warming up (1 round × {len(QUERIES)} queries × 3 modes) ...")
    for q, vec in zip(QUERIES, query_vecs):
        for mode in ("dense", "sparse", "hybrid"):
            try:
                store.search(q, vector=vec, top_k=top_k, mode=mode)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 4. Benchmark rounds                                                  #
    # ------------------------------------------------------------------ #
    all_lats: dict[str, list[float]] = {"dense": [], "sparse": [], "hybrid": []}
    round_means: dict[str, list[float]] = {"dense": [], "sparse": [], "hybrid": []}

    print(f"\n  Running {rounds} rounds × {reps_per_round} reps ...")
    for r in range(rounds):
        round_lats: dict[str, list[float]] = {"dense": [], "sparse": [], "hybrid": []}
        for _ in range(reps_per_round):
            for q, vec in zip(QUERIES, query_vecs):
                for mode in ("dense", "sparse", "hybrid"):
                    t = time.perf_counter()
                    try:
                        store.search(q, vector=vec, top_k=top_k, mode=mode)
                    except Exception as e:
                        print(f"    [warn] {mode}: {e}", file=sys.stderr)
                    elapsed_ms = (time.perf_counter() - t) * 1000
                    round_lats[mode].append(elapsed_ms)
                    all_lats[mode].append(elapsed_ms)

        for mode in ("dense", "sparse", "hybrid"):
            rm = _mean(round_lats[mode])
            round_means[mode].append(rm)
            print(f"    round {r+1:02d}  {mode:8s}  mean={rm:5.1f}ms")

    # ------------------------------------------------------------------ #
    # 5. Aggregate                                                         #
    # ------------------------------------------------------------------ #
    print(f"\n  AGGREGATE  ({rounds} rounds × {reps_per_round} reps × {len(QUERIES)} queries = {len(all_lats['dense'])} obs/mode)")
    print(f"  {'mode':<10} {'mean':>7} {'p50':>7} {'p75':>7} {'p95':>7} {'p99':>7}  round-to-round std")
    print(f"  {'-'*70}")

    stats: dict[str, dict] = {}
    for mode in ("dense", "sparse", "hybrid"):
        s = _stats(all_lats[mode])
        rms = round_means[mode]
        std = round((sum((x - _mean(rms))**2 for x in rms) / len(rms)) ** 0.5, 2) if len(rms) > 1 else 0.0
        s["round_std_ms"] = std
        stats[mode] = s
        print(f"  {mode:<10} {s['mean_ms']:>7.1f} {s['p50_ms']:>7.1f} {s['p75_ms']:>7.1f} "
              f"{s['p95_ms']:>7.1f} {s['p99_ms']:>7.1f}  ±{std:.2f}ms")

    return {
        "label":      label,
        "chunks":     len(all_chunks),
        "insert_sec": insert_sec,
        "rounds":     rounds,
        "reps":       reps_per_round,
        "top_k":      top_k,
        "retrieval":  stats,
    }

# ---------------------------------------------------------------------------
# Comparison printer
# ---------------------------------------------------------------------------

def print_comparison(results: list[dict]):
    if len(results) < 2:
        print("\nNeed 2 labels. Run with --label v1 then --label v2.")
        return

    by_label = {r["label"]: r for r in results}
    labels = list(by_label.keys())
    v1_key = next((l for l in labels if "v1" in l.lower()), None)
    v2_key = next((l for l in labels if "v2" in l.lower()), None)
    a, b = (v1_key, v2_key) if (v1_key and v2_key) else (labels[0], labels[1])
    ra, rb = by_label[a], by_label[b]

    def pct(old, new):
        if old == 0:
            return "N/A"
        d = (new - old) / old * 100
        return f"{'+' if d >= 0 else ''}{d:.1f}%"

    def mark(old, new):
        if old == 0:
            return ""
        return "✅" if new < old else "🔴"

    col_w = 30
    print(f"\n{'='*72}")
    print(f"  RETRIEVAL LATENCY (ms)  —  pre-computed vectors, top_k={ra['top_k']}")
    print(f"  {a}  →  {b}")
    meta_a = f"{ra['rounds']}r×{ra['reps']}rep×{len(QUERIES)}q = {ra['retrieval']['dense']['n']} obs"
    meta_b = f"{rb['rounds']}r×{rb['reps']}rep×{len(QUERIES)}q = {rb['retrieval']['dense']['n']} obs"
    print(f"  [{a}: {meta_a}]  [{b}: {meta_b}]")
    print(f"{'='*72}")

    for stat, label_str in [
        ("mean_ms", "mean"),
        ("p50_ms",  "p50"),
        ("p75_ms",  "p75"),
        ("p95_ms",  "p95"),
        ("p99_ms",  "p99"),
    ]:
        print(f"\n  {label_str.upper()}")
        print(f"  {'mode':<12} {a:>16} {b:>16} {'change':>10}  ")
        print(f"  {'-'*60}")
        for mode in ("dense", "sparse", "hybrid"):
            va = ra["retrieval"][mode][stat]
            vb = rb["retrieval"][mode][stat]
            ch = pct(va, vb)
            ar = mark(va, vb)
            print(f"  {mode:<12} {va:>16.2f} {vb:>16.2f} {ch:>10}  {ar}")

    print(f"\n  ROUND-TO-ROUND STD (stability)")
    print(f"  {'mode':<12} {a:>16} {b:>16}")
    print(f"  {'-'*44}")
    for mode in ("dense", "sparse", "hybrid"):
        sa = ra["retrieval"][mode].get("round_std_ms", 0)
        sb = rb["retrieval"][mode].get("round_std_ms", 0)
        print(f"  {mode:<12} {'±'+str(sa)+'ms':>16} {'±'+str(sb)+'ms':>16}")
    print()

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docs",       default="~/Documents/ibm_redbooks")
    ap.add_argument("--label",      default="run", help="v1 or v2")
    ap.add_argument("--out",        default="hack/bench/retrieval_results.json")
    ap.add_argument("--rounds",     type=int, default=5,  help="Independent measurement rounds")
    ap.add_argument("--reps",       type=int, default=60, help="Search reps per round")
    ap.add_argument("--top-k",      type=int, default=10)
    ap.add_argument("--max-chunks", type=int, default=1000)
    ap.add_argument("--compare-only", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    existing: list[dict] = []
    if out.exists():
        try:
            existing = json.loads(out.read_text())
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    if args.compare_only:
        print_comparison(existing)
        return 0

    docs_dir = Path(args.docs).expanduser()
    if not docs_dir.is_dir():
        print(f"ERROR: {docs_dir} not found", file=sys.stderr)
        return 1

    result = run(
        label=args.label,
        docs_dir=docs_dir,
        max_chunks=args.max_chunks,
        rounds=args.rounds,
        reps_per_round=args.reps,
        top_k=args.top_k,
    )

    existing = [r for r in existing if r.get("label") != args.label]
    existing.append(result)
    out.write_text(json.dumps(existing, indent=2))
    print(f"\nSaved → {out}")

    print_comparison(existing)
    return 0

if __name__ == "__main__":
    sys.exit(main())
