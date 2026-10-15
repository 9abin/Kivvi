"""규정 단위 검색의 상한 시험 (읽기 전용): '어느 규정인지'는 후보 30 안에서 얼마나 맞히나, 규정 전문을 넣으면 얼마나 긴가.

  uv run --extra db python scripts/exp_reg_level.py
1) 규정별 본문 길이 분포 (문서 단위 컨텍스트 투입이 가능한 크기인가)
2) 개발용·시험용 정답 근거에 대해: 후보 30(현행 하이브리드)에 정답 '규정'이 있는 비율, 상위 N개 규정(후보 수 기준 순위)에 든 비율,
   정답 '조'가 후보 30에 든 비율과 비교. 상위 N개 규정의 전문 길이 합계도 함께 낸다 (그 전문을 통째로 LLM에 넣는 경우의 비용).
"""

import collections
import json
import statistics as st
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import exp_embed_baseline as E  # noqa: E402

DATA = ROOT / "data" / "embedding"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    from kiwipiepy import Kiwi
    from sentence_transformers import SentenceTransformer

    kiwi = Kiwi()
    rows = E.load_db()
    ids = [r["chunk_id"] for r in rows]
    doc_of = {r["chunk_id"]: r["chunk_id"].split("_")[0] for r in rows}
    node2doc = {}
    for c in ids:
        n = E.node_of_chunk(c)
        if n:
            node2doc[n] = doc_of[c]
    title = {r["chunk_id"].split("_")[0]: r["title"] for r in rows}
    names = {r["title"]: r["doc_id"] for r in rows}
    chars = collections.Counter()
    for r in rows:
        chars[doc_of[r["chunk_id"]]] += len(r["body_search"] or "")
    cs = sorted(chars.values())
    print(f"규정 {len(chars)}개 · 청크 {len(rows)}개")
    print("규정 본문 글자수: 중앙값 %d · 75%% %d · 90%% %d · 최대 %d" % (st.median(cs), cs[int(len(cs) * .75)], cs[int(len(cs) * .9)], cs[-1]))
    print("  (한국어는 대략 글자수 ÷ 1.5 ~ 2 = 토큰 수 정도로 본다)")
    big = sorted(chars.items(), key=lambda x: -x[1])[:5]
    print("  가장 긴 규정:", [(title[d], c) for d, c in big])
    print("  1만 글자 이하 규정 수:", sum(c <= 10000 for c in cs), " · 3만 글자 이하:", sum(c <= 30000 for c in cs))

    E_doc = np.load(DATA / "emb_BAAI_bge_m3_v0.npy")
    model = SentenceTransformer("BAAI/bge-m3", device="cuda")
    model.half()
    skip = E.SKIP_TAGS
    qtok = lambda t: [x.form for x in kiwi.tokenize(t) if not x.tag.startswith(skip)]  # noqa: E731
    bm = E.BM25([(r["chunk_id"], (r["body_tokens"] or "").split()) for r in rows])

    def rrf(lists):
        sc = collections.defaultdict(float)
        for hits, w in lists:
            for rk, c in enumerate(hits, 1):
                sc[c] += w / (E.RRF_K + rk)
        return [c for c, _ in sorted(sc.items(), key=lambda x: -x[1])]

    def dense(Q):
        S = Q @ E_doc.T
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
                if n and n in node2doc and n not in need:
                    need.append(n)
            if need:
                qs.append({"q": g["q"], "rw": rw.get(g["q"], ""), "need": need})
        Qv = model.encode([q["q"] for q in qs], normalize_embeddings=True, convert_to_numpy=True, batch_size=32)
        Rv = model.encode([q["rw"] or q["q"] for q in qs], normalize_embeddings=True, convert_to_numpy=True, batch_size=32)
        V, Vr = dense(Qv), dense(Rv)
        cands = []
        for i, q in enumerate(qs):
            K = bm.top(qtok(q["q"]), 100)
            Kr = bm.top(qtok(q["rw"]), 100) if q["rw"] else []
            cands.append(rrf([(V[i][:E.CAND], .7), (K[:E.CAND], .3), (Vr[i][:E.CAND], .7), (Kr[:E.CAND], .3)]))
        tot = sum(len(q["need"]) for q in qs)
        print(f"\n===== {label}: 질문 {len(qs)} · 근거 {tot}곳")
        art30 = sum(E.node_of_chunk(c) in q["need"] for q, cs_ in zip(qs, cands) for c in [None] if False)
        got_art = sum(n in {E.node_of_chunk(c) for c in cs_[:30]} for q, cs_ in zip(qs, cands) for n in q["need"])
        print(f"조 단위: 정답 조가 후보 30 안  {got_art}/{tot} = {got_art / tot:.3f}")
        for topn in (1, 2, 3, 5):
            hit = 0
            full_sizes, qhit, qfull = [], 0, 0
            for q, cs_ in zip(qs, cands):
                cnt = collections.Counter()
                for rk, c in enumerate(cs_[:30], 1):
                    cnt[doc_of[c]] += 1.0 / (10 + rk)             # 상위에 많이 나온 규정일수록 점수
                top = [d for d, _ in cnt.most_common(topn)]
                ok = [node2doc[n] in top for n in q["need"]]
                hit += sum(ok)
                qfull += all(ok)
                full_sizes.append(sum(chars[d] for d in top))
            print(f"  상위 {topn}개 규정에 정답 규정 포함: 근거 {hit}/{tot} = {hit / tot:.3f} · 질문의 근거를 전부 포함한 비율 {qfull / len(qs):.3f} · 전문 합계 글자 중앙값 {int(st.median(full_sizes))} / 최대 {max(full_sizes)}")
        anyd = sum(node2doc[n] in {doc_of[c] for c in cs_[:30]} for q, cs_ in zip(qs, cands) for n in q["need"])
        print(f"  후보 30에 등장한 규정 전체에 정답 규정 포함: {anyd}/{tot} = {anyd / tot:.3f}")


if __name__ == "__main__":
    main()
