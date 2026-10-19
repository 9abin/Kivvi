"""리랭커가 Recall을 올리는지 깎는지 가른다 (LLM 답변 없이 정답 근거 Recall만 본다).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_rerank_ablation.py --questions eval/answer_eval_100.jsonl

같은 후보(RRF 합침 상위 30개)를 세 가지 순서로 정렬해 상위 k개(조 단위)를 근거로 쓴 결과를 비교한다.
  RRF 순서   리랭커 없음. 합친 순위 그대로
  리랭크     현재 방식 (BGE-reranker-v2-m3)
  완벽한 순서 정답 근거가 있는 조를 맨 앞에 둔 순서. 후보 30개 안에 있는 정답은 다 잡는 상한선(리랭커 개선의 여지)
나머지(같은 조 묶기, 용어집 필터, 형제 항·인용 조립)는 현재 설정과 같다.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import config  # noqa: E402
import src.search.search as ss  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import RERANK_TOP, Searcher, group_by_article  # noqa: E402


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    if args.limit:
        gold = gold[:args.limit]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    t2d = {v["title"]: d for d, v in s.documents.items()}
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    ss.CITE_WHOLE_ARTICLE = True
    ss.SIBLING_LIMIT = 3000

    pre = []
    for i, g in enumerate(gold, 1):
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
        extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
        fused = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken]
        pool = fused[:RERANK_TOP]
        reranked = s.rerank(q, [dict(h) for h in pool])
        want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
        # 완벽한 순서: 정답 조의 청크를 맨 앞에 (같은 그룹 안에서는 리랭크 순서 유지)
        perfect = sorted(reranked, key=lambda h: 0 if ckey[h["chunk_id"]] in want else 1)
        pre.append((g, exact, {"RRF 순서": [dict(h) for h in pool], "리랭크": reranked, "완벽한 순서": perfect}, want))
        if i % 25 == 0:
            print(f"  후보 {i}/{len(gold)}", flush=True)

    ks = (8, 12, 16)
    pool_rec = sum(len(w & {ckey[h["chunk_id"]] for h in o["리랭크"]}) for _, _, o, w in pre)
    need = sum(len(w) for *_, w in pre)
    print(f"\n리랭커에 들어가는 후보 30개 안에 정답 근거가 있는 비율: {pool_rec}/{need} = {pool_rec / need:.3f}  (모든 순서의 상한)")
    print(f"{'순서':10s} " + " ".join(f"k={k:<2d} Recall(전부확보)   " for k in ks))
    out = {}
    for order in ("RRF 순서", "리랭크", "완벽한 순서"):
        cells = []
        for k in ks:
            got = full = 0
            for g, exact, orders, want in pre:
                ranked, _ = glossary.filter_candidates(g["q"], orders[order], s.glossary)
                ranked = group_by_article(ranked)
                res = []
                for h in (exact + ranked)[:k]:
                    it = s._entry(h, status=h.get("status"))
                    it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                               "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                    res.append(it)
                asm = s.assemble(res)
                used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
                got += len(want & used)
                full += want <= used
            out[f"{order} k{k}"] = got / need
            cells.append(f"{got / need:.3f} ({full:3d})        ")
        print(f"{order:10s} " + " ".join(cells), flush=True)
    json.dump(out, open(os.path.join("eval", "runs", "exp_rerank_ablation.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
