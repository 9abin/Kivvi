"""현재 시스템의 recall을 평가 묶음별로 한 번에 잰다 (후보 상한별 recall 포함).
  uv run python scripts/eval_recall_all.py
A 영향 평가 4묶음(개정문 있음, AI 라벨), B 새 봉인 20문항(AI 라벨, 풀 = 여러 검색기의 합), C 휴직 · 보수 시험 36문항(AI 라벨 + 풀 보강 라벨),
D 동반 개정 벤치마크(개정문 없음, 같은 날 개정 기록). 정답은 현업이 확인한 것이 아니다.
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
from regcheck.impact_view import load_context, run  # noqa: E402
from regcheck.review_list import split_view  # noqa: E402

CAPS = (20, 30, 50)


def at(order, truth, n):
    return len(truth & set(order[:n]))


def line(name, tot, all_, dflt, caps, avg_all, avg_dflt):
    cs = "  ".join(f"@{n} {caps[n]}/{tot} ({caps[n]/max(tot,1):.0%})" for n in CAPS)
    print(f"  {name:22s} 정답 {tot:3d} | 전체 {all_}/{tot} ({all_/max(tot,1):.1%}) | 기본 화면 {dflt}/{tot} ({dflt/max(tot,1):.1%}) | {cs} | 후보 평균 전체 {avg_all:.0f} · 기본 화면 {avg_dflt:.0f}")


def section_a():
    print("\n## A. 영향 평가 4묶음 (개정문 있음)")
    R = ROOT
    SETS = (("개발", ecd.K, "개발"), ("1차 봉인", ecd.K, "봉인"), ("2차", R / "eval/impact_label_kit_r2/산출", "봉인"), ("3차", R / "eval/impact_label_kit_r3/산출", "봉인"))
    T = A = D = n = na = nd = 0
    C = collections.Counter()
    for tag, kit, sp in SETS:
        ecd.K = kit
        pos, _, _, _ = ecd.build(sp)
        t = a = d = 0; c = collections.Counter(); sa = sd = 0
        for r in pos:
            v = split_view(r["cands"]); order = [x.id for x in r["cands"]]
            t += len(r["truth"]); a += len(r["truth"] & {x.id for x in v["all"]}); d += len(r["truth"] & {x.id for x in v["default"]})
            sa += len(v["all"]); sd += len(v["default"])
            for k in CAPS:
                c[k] += at(order, r["truth"], k)
        line(tag, t, a, d, c, sa / len(pos), sd / len(pos))
        T += t; A += a; D += d; na += sa; nd += sd; n += len(pos); C.update(c)
    line("합계", T, A, D, C, na / n, nd / n)


def build_rag(ctx):
    from exp_query_text_dev import BM25
    from sentence_transformers import SentenceTransformer
    q4b = os.environ.get("RAG_EMB") == "q4b"   # Qwen3-Embedding-4B 색인 (out/vector/qwen3-embedding-4b-q8_0 + out/kg/ext_emb_q4b.npy). 기본은 bge-m3
    V = ROOT / "out/vector/qwen3-embedding-4b-q8_0" if q4b else ecd.V
    emb = np.load(V / "embeddings.npy"); emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    chunks = ecd.jl(ecd.V / "chunks.jsonl")
    ext = np.load(ROOT / ("out/kg/ext_emb_q4b.npy" if q4b else "out/kg/ext_emb.npy")); ext_ids = json.loads((ROOT / "out/kg/ext_ids.json").read_text(encoding="utf-8"))
    ids = [c["article_id"] for c in chunks] + ext_ids; E = np.vstack([emb, ext])
    bm = BM25([c["text"] for c in chunks] + [ctx.unit_text.get(k, "") for k in ext_ids])
    if q4b:
        from regcheck.vectors import IMPACT_QUERY_INSTRUCT, OllamaEncoder
        enc = OllamaEncoder("qwen3-embedding:4b-q8_0", IMPACT_QUERY_INSTRUCT)
    else:
        enc = SentenceTransformer("BAAI/bge-m3", device="cuda"); enc.max_seq_length = 512

    def rag(q, k):
        qa = enc.encode([q[:1200]], normalize_embeddings=True)[0]
        rrf = collections.defaultdict(float)
        for r_, j in enumerate(np.argsort(-(E @ qa))[:200]):
            rrf[j] += 1 / (60 + r_)
        for r_, j in enumerate(np.argsort(-bm.scores(q))[:200]):
            rrf[j] += 1 / (60 + r_)
        out, seen = [], set()
        for j in sorted(rrf, key=lambda j: -rrf[j]):
            if ids[j] not in seen:
                seen.add(ids[j]); out.append(ids[j])
            if len(out) >= k:
                break
        return out
    return rag


def labels_for(items_dir, out_dir, suffix=""):
    out = {}
    for line_ in (ROOT / items_dir / "changes.jsonl").read_text(encoding="utf-8").splitlines():
        it = json.loads(line_); i = it["id"]
        p = ROOT / out_dir / f"labels_{i}{suffix}.json"
        if not p.exists():
            p = ROOT / out_dir / f"labels_{i}.json"
        ys = {x["unit"].strip("[]") for x in json.loads(p.read_text(encoding="utf-8")) if x["label"] == "예"}
        pe = ROOT / "out/pool_extra" / f"labels_{i}.json"
        if pe.exists():
            ys |= {x["unit"].strip("[]") for x in json.loads(pe.read_text(encoding="utf-8")) if x["label"] == "예"}
        out[i] = (it["수정"], ys)
    return out


def section_bc(ctx, regs, rag):
    by = {r["name"]: k for k, r in regs.items()}
    for title, items_dir, out_dir, suffix in (("B. 새 봉인 20문항 (개정문 있음)", "eval/new_items", "out/new_items", ""),
                                              ("C-1. 휴직 시험 28문항", "eval/leave_items", "out/leave_items", ""), ("C-2. 보수 · 수당 시험 8문항", "eval/pay_items", "out/pay_items", "_v2")):
        print(f"\n## {title}")
        T = A = D = 0; c = collections.Counter(); sa = sd = n = 0; miss = []
        for i, (m, ys) in labels_for(items_dir, out_dir, suffix).items():
            if not ys:
                continue
            r = run(ctx, regs, by[m["규정"]], m["개정문"], i, rag=rag)
            if r["cq"].kind == "분석 실패":
                miss.append(i); continue
            v = r["view"]; order = [x.id for x in v["all"]]
            T += len(ys); A += len(ys & set(order)); D += len(ys & {x.id for x in v["default"]})
            sa += len(order); sd += len(v["default"]); n += 1
            for k in CAPS:
                c[k] += at(order, ys, k)
        line("합계(정답 있는 문항만)", T, A, D, c, sa / max(n, 1), sd / max(n, 1))


def main():
    section_a()
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ctx = load_context(ROOT, regs)
    section_bc(ctx, regs, build_rag(ctx))
    print("\n## D. 동반 개정 벤치마크 (개정문 없음, 같은 날 개정 기록)")
    import eval_coamend
    eval_coamend.main()


if __name__ == "__main__":
    main()
