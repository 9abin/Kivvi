"""새 봉인 문항(eval/new_items/changes.jsonl): 파이프라인(RAG 포함)이 만드는 후보와 서로 다른 검색기(밀집 · BM25 · 하이브리드)의 상위 후보를 합쳐
라벨러용 풀(out/new_items/pool_<id>.md)을 만든다. 한 시스템에 유리하지 않도록 풀은 여러 방법의 합이다.
  uv run python scripts/exp_new_items.py
"""
import collections
import glob
import json
import os
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
import eval_collect_dev as ecd  # noqa: E402
from exp_query_text_dev import BM25  # noqa: E402
from regcheck.impact_view import load_context, run  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

K = 30
V = ecd.V


def main():
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ctx = load_context(ROOT, regs)
    by = {r["name"]: k for k, r in regs.items()}
    emb = np.load(V / "embeddings.npy"); emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    chunks = ecd.jl(V / "chunks.jsonl")
    ext = np.load(ROOT / "out/kg/ext_emb.npy"); ext_ids = json.loads((ROOT / "out/kg/ext_ids.json").read_text(encoding="utf-8"))
    ids = [c["article_id"] for c in chunks] + ext_ids; E = np.vstack([emb, ext])
    texts = [c["text"] for c in chunks] + [ctx.unit_text.get(k, "") for k in ext_ids]
    bm = BM25(texts); enc = SentenceTransformer("BAAI/bge-m3", device="cuda"); enc.max_seq_length = 512

    def uniq(order, k):
        out, seen = [], set()
        for j in order:
            x = ids[j]
            if x not in seen:
                seen.add(x); out.append(x)
            if len(out) >= k:
                break
        return out

    def dense(q, k):
        return uniq(np.argsort(-(E @ enc.encode([q[:1200]], normalize_embeddings=True)[0]))[:400], k)

    def lex(q, k):
        return uniq(np.argsort(-bm.scores(q))[:400], k)

    def rag(q, k):
        qa = enc.encode([q[:1200]], normalize_embeddings=True)[0]
        rrf = collections.defaultdict(float)
        for r_, j in enumerate(np.argsort(-(E @ qa))[:200]):
            rrf[j] += 1 / (60 + r_)
        for r_, j in enumerate(np.argsort(-bm.scores(q))[:200]):
            rrf[j] += 1 / (60 + r_)
        return uniq(sorted(rrf, key=lambda j: -rrf[j]), k)

    out = ROOT / "out/new_items"; out.mkdir(parents=True, exist_ok=True)
    for line in (ROOT / "eval/new_items/changes.jsonl").read_text(encoding="utf-8").splitlines():
        it = json.loads(line); m = it["수정"]; i = it["id"]
        r = run(ctx, regs, by[m["규정"]], m["개정문"], i, rag=rag)
        cq = r["cq"]
        if cq.kind == "분석 실패":
            print(i, "분석 실패:", cq.error); continue
        start = set(cq.starts)
        t = ctx.unit_text.get((cq.starts or [""])[0], "")[:600]
        q = f"{m['조']}\n{m['개정문']}\n{t}"
        pipe = {c.id for c in r["view"]["all"]}
        pool = (pipe | set(dense(q, K)) | set(lex(q, K)) | set(rag(q, K))) - start
        pool = {u for u in pool if u in ctx.unit_text}
        rows = [f"# {i}  {m['규정']} {m['조']} {m['항'] or ''}항\n\n개정문: {m['개정문']}\n\n바뀐 조 원문:\n{t}\n\n---\n"]
        for u in sorted(pool):
            rows.append(f"## [{u}] {regs[u.split(':')[0]]['name']}\n{ctx.unit_text.get(u, '')[:600]}\n")
        (out / f"pool_{i}.md").write_text("\n".join(rows), encoding="utf-8")
        print(i, cq.kind, "파이프라인 후보", len(pipe), "풀", len(pool), "|", cq.evidence[:50])


if __name__ == "__main__":
    main()
