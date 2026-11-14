"""후보 단계에서 놓친 근거의 해부: 질문 자체를 못 찾은 것인가, 같은 질문의 다른 근거는 찾았는데 이것만 놓친 것인가.

  uv run --extra db python scripts/exp_miss_anatomy.py
개발용 94문항(근거 126곳), 현행 하이브리드(원질문 + 바꿔 쓴 검색어, 벡터·BM25 4목록 RRF, 리포트 40)의 후보 N개 기준.
놓친 근거를 나눈다:
  A. 질문 전체 미스   — 그 질문의 근거가 후보 안에 하나도 없음 (일상어·개념 간극, 시작점 없음)
  B. 부분 미스(같은 규정) — 같은 질문의 다른 근거는 있는데 이것만 없음, 그 근거와 같은 규정 (연결된 조항: 정의·예외·인접)
  C. 부분 미스(다른 규정) — 같은 질문의 다른 근거는 있는데 이것만 없고 규정도 다름
그리고 이것이 질문과 얼마나 멀었는지(원질문 벡터 순위), 이미 찾은 근거와 얼마나 비슷한지(조문 임베딩 유사도)를 센다.
CPU로 돌려 GPU를 쓰는 서버와 함께 실행할 수 있다.
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
    names = {r["title"]: r["doc_id"] for r in rows}
    indexed = {E.node_of_chunk(c) for c in ids if E.node_of_chunk(c)}
    E_doc = np.load(DATA / "emb_BAAI_bge_m3_v0.npy")
    node_vecs: dict[str, list[int]] = collections.defaultdict(list)
    for i, c in enumerate(ids):
        n = E.node_of_chunk(c)
        if n:
            node_vecs[n].append(i)
    bm = E.BM25([(r["chunk_id"], (r["body_tokens"] or "").split()) for r in rows])
    qtok = lambda t: [x.form for x in kiwi.tokenize(t) if not x.tag.startswith(E.SKIP_TAGS)]  # noqa: E731
    model = SentenceTransformer("BAAI/bge-m3", device="cpu")

    def rrf(lists):
        sc = collections.defaultdict(float)
        for hits, w in lists:
            for rk, c in enumerate(hits, 1):
                sc[c] += w / (E.RRF_K + rk)
        return [c for c, _ in sorted(sc.items(), key=lambda x: -x[1])]

    def vec(t):
        S = model.encode([t], normalize_embeddings=True)[0] @ E_doc.T
        return [ids[j] for j in np.argsort(-S)[:100]], S

    gold = [json.loads(l) for l in (DATA / "answer_eval_100.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rw = json.loads((DATA / "rewrite-cache.json").read_text(encoding="utf-8"))
    node_doc = {}
    for c in ids:
        n = E.node_of_chunk(c)
        if n:
            node_doc[n] = doc_of[c]
    stats = {10: collections.Counter(), 30: collections.Counter()}
    far = {10: [], 30: []}          # 놓친 근거의 원질문 벡터 순위 (조 단위)
    simfound = {10: [], 30: []}     # 놓친 근거와 「같은 질문에서 찾은 근거」 중 가장 비슷한 것의 조문 유사도
    multi = collections.Counter()
    for g in gold:
        need = []
        for ev in g["근거"]:
            n = E.node_of_gold(names.get(ev["규정"], "?"), ev["조항"])
            if n and n in indexed and n not in need:
                need.append(n)
        if not need:
            continue
        multi["근거 1개 질문" if len(need) == 1 else "근거 2개 이상 질문"] += 1
        v0, S0 = vec(g["q"])
        v1, _ = vec(rw.get(g["q"]) or g["q"])
        k0 = bm.top(qtok(g["q"]), 100)
        k1 = bm.top(qtok(rw[g["q"]]), 100) if rw.get(g["q"]) else []
        C = E.CAND
        cs = rrf([(v0[:C], .7), (k0[:C], .3), (v1[:C], .7), (k1[:C], .3)])
        qrank = {}
        for rk, i in enumerate(np.argsort(-S0), 1):
            n = E.node_of_chunk(ids[i])
            if n and n not in qrank:
                qrank[n] = rk
        for top in (10, 30):
            got = {E.node_of_chunk(c) for c in cs[:top]}
            found = [n for n in need if n in got]
            for n in need:
                if n in got:
                    continue
                if not found:
                    kind = "A 질문 전체 미스(이 질문의 근거가 하나도 없음)"
                else:
                    kind = ("B 부분 미스·같은 규정" if any(node_doc[f] == node_doc[n] for f in found)
                            else "C 부분 미스·다른 규정")
                    best = max(float((E_doc[node_vecs[n]] @ E_doc[node_vecs[f]].T).max()) for f in found)
                    simfound[top].append(best)
                stats[top][kind] += 1
                far[top].append(qrank.get(n, 999))
    print(f"질문 {sum(multi.values())}개: {dict(multi)}")
    for top in (10, 30):
        tot = sum(stats[top].values())
        print(f"\n[후보 {top}개 기준] 놓친 근거 {tot}곳")
        for k, v in sorted(stats[top].items()):
            print(f"  {k}: {v}곳 ({v / tot:.0%})")
        print(f"  놓친 근거의 원질문 벡터 순위(조 단위): 중앙 {st.median(far[top]):.0f} · 100위 밖 {sum(x > 100 for x in far[top])}곳")
        if simfound[top]:
            a = np.array(simfound[top])
            print(f"  (부분 미스) 같은 질문에서 이미 찾은 근거와의 조문 유사도: 중앙 {np.median(a):.2f} · 0.70 미만 {np.mean(a < 0.70):.0%} · 0.78 이상 {np.mean(a >= 0.78):.0%}")


if __name__ == "__main__":
    main()
