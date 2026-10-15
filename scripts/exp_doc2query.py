"""학습 없이 합성 질문을 색인에 붙이는 방식(doc2query) 시험 — 임베딩 튜닝의 대안 (docs/09 §10-9).

  uv run --extra db python scripts/exp_doc2query.py
이미 만들어 둔 합성 질문(data/embedding/pairs_v3_good.jsonl, 조 1,000개분 = 전체 조의 약 절반)을 청크 옆에 붙인다.
  · 벡터: 질문 하나하나를 따로 임베딩해 그 청크에 연결. 청크 점수 = max(청크 자체 유사도, 그 청크 질문들의 최대 유사도)
  · BM25: 청크 형태소 + 질문 형태소를 합친 문서
후보 단계 재현율을 기준선(bge-m3 현행 하이브리드)과 같은 조건으로 잰다 (개발용·시험용). 질문이 붙은 조의 근거와 안 붙은 조의 근거를 나눠 본다:
안 붙은 조는 건드리지 않았으므로 기준선과 같아야 한다 (대조군).
"""

import collections
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import exp_embed_baseline as E  # noqa: E402

DATA = ROOT / "data" / "embedding"
NS = (10, 30, 50, 100)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    from kiwipiepy import Kiwi
    from sentence_transformers import SentenceTransformer

    kiwi = Kiwi()
    rows = E.load_db()
    ids = [r["chunk_id"] for r in rows]
    idx = {c: i for i, c in enumerate(ids)}
    node = [E.node_of_chunk(c) for c in ids]
    names = {r["title"]: r["doc_id"] for r in rows}
    indexed_nodes = set(n for n in node if n)
    E_doc = np.load(DATA / "emb_BAAI_bge_m3_v0.npy")
    pairs = [json.loads(l) for l in (DATA / "pairs_v3_good.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    covered = {p["node"] for p in pairs}
    print(f"붙일 질문 {len(pairs)}개 · 질문이 붙은 조 {len(covered)}개 (색인 조·별표 {len(indexed_nodes)}개 중)")
    owner = np.array([idx[p["chunk_id"]] for p in pairs])

    model = SentenceTransformer("BAAI/bge-m3", device="cuda")
    model.half()
    QE = model.encode([p["q"] for p in pairs], normalize_embeddings=True, convert_to_numpy=True, batch_size=64, show_progress_bar=False)

    skip = E.SKIP_TAGS
    qtok = lambda t: [x.form for x in kiwi.tokenize(t) if not x.tag.startswith(skip)]  # noqa: E731
    base_docs = [(r["chunk_id"], (r["body_tokens"] or "").split()) for r in rows]
    extra = collections.defaultdict(list)
    for p in pairs:
        extra[p["chunk_id"]] += [x.form for x in kiwi.tokenize(p["q"])]
    d2q_docs = [(c, toks + extra.get(c, [])) for c, toks in base_docs]
    bm_base, bm_d2q = E.BM25(base_docs), E.BM25(d2q_docs)

    def rrf(lists):
        sc = collections.defaultdict(float)
        for hits, w in lists:
            for rk, c in enumerate(hits, 1):
                sc[c] += w / (E.RRF_K + rk)
        return [c for c, _ in sorted(sc.items(), key=lambda x: -x[1])]

    def dense(Q, use_q):
        S = Q @ E_doc.T
        if use_q:
            SQ = Q @ QE.T
            best = np.full_like(S, -1.0)
            for k, o in enumerate(owner):                       # 질문 → 부모 청크로 최대값 모으기
                best[:, o] = np.maximum(best[:, o], SQ[:, k])
            S = np.maximum(S, best)
        return [[ids[j] for j in np.argsort(-S[i])[:100]] for i in range(len(Q))]

    for label, gpath, rpath in (("개발용", DATA / "answer_eval_100.jsonl", DATA / "rewrite-cache.json"),
                                ("시험용", DATA / "answer_eval_holdout.jsonl", DATA / "rewrite-cache-holdout.json")):
        gold = [json.loads(l) for l in gpath.read_text(encoding="utf-8").splitlines() if l.strip()]
        rw = json.loads(rpath.read_text(encoding="utf-8"))
        qs = []
        for g in gold:
            need = []
            for ev in g["근거"]:
                n = E.node_of_gold(names.get(ev["규정"], "?"), ev["조항"])
                if n and n in indexed_nodes and n not in need:
                    need.append(n)
            if need:
                qs.append({"q": g["q"], "rw": rw.get(g["q"], ""), "need": need})
        Qv = model.encode([q["q"] for q in qs], normalize_embeddings=True, convert_to_numpy=True, batch_size=32)
        Rv = model.encode([q["rw"] or q["q"] for q in qs], normalize_embeddings=True, convert_to_numpy=True, batch_size=32)
        K0 = [bm_base.top(qtok(q["q"]), 100) for q in qs]
        K0r = [bm_base.top(qtok(q["rw"]), 100) if q["rw"] else [] for q in qs]
        K1 = [bm_d2q.top(qtok(q["q"]), 100) for q in qs]
        K1r = [bm_d2q.top(qtok(q["rw"]), 100) if q["rw"] else [] for q in qs]
        V0, V0r, V1, V1r = dense(Qv, False), dense(Rv, False), dense(Qv, True), dense(Rv, True)
        C = E.CAND
        variants = {
            "기준선 (현행 하이브리드)": [rrf([(v[:C], .7), (k[:C], .3), (r[:C], .7), (kr[:C], .3)]) for v, k, r, kr in zip(V0, K0, V0r, K0r)],
            "벡터에만 질문 붙임": [rrf([(v[:C], .7), (k[:C], .3), (r[:C], .7), (kr[:C], .3)]) for v, k, r, kr in zip(V1, K0, V1r, K0r)],
            "BM25에만 질문 붙임": [rrf([(v[:C], .7), (k[:C], .3), (r[:C], .7), (kr[:C], .3)]) for v, k, r, kr in zip(V0, K1, V0r, K1r)],
            "벡터+BM25 모두": [rrf([(v[:C], .7), (k[:C], .3), (r[:C], .7), (kr[:C], .3)]) for v, k, r, kr in zip(V1, K1, V1r, K1r)],
        }
        print(f"\n===== {label}: 질문 {len(qs)}개 · 근거 {sum(len(q['need']) for q in qs)}곳 (질문이 붙은 조의 근거 {sum(1 for q in qs for n in q['need'] if n in covered)}곳, 안 붙은 조 {sum(1 for q in qs for n in q['need'] if n not in covered)}곳)")
        print("방식\t" + "\t".join(f"@{n}" for n in NS) + "   |  붙은 조 근거 @30·@100  |  안 붙은 조(대조군) @30·@100")
        for name, cands in variants.items():
            def rec(sel, n):
                tot = hit = 0
                for q, cs in zip(qs, cands):
                    got = {E.node_of_chunk(c) for c in cs[:n]}
                    for x in q["need"]:
                        if sel(x):
                            tot += 1
                            hit += x in got
                return hit / tot if tot else float("nan")
            allr = "\t".join(f"{rec(lambda x: True, n):.3f}" for n in NS)
            cov = f"{rec(lambda x: x in covered, 30):.3f}·{rec(lambda x: x in covered, 100):.3f}"
            unc = f"{rec(lambda x: x not in covered, 30):.3f}·{rec(lambda x: x not in covered, 100):.3f}"
            print(f"{name}\t{allr}   |  {cov}  |  {unc}")


if __name__ == "__main__":
    main()
