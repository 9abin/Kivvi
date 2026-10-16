"""리랭크 질문에 바꿔쓴 검색어를 붙인 방식(B)으로 (1) BGE만, (2) BGE 상위 16개 조 → Luna가 k개 선택을 비교한다 (정답 근거 Recall만 본다).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_b_luna.py --questions eval/answer_eval_100.jsonl --repeat 2

후보는 현재 방식(원질문+바꿔쓴 검색어, 벡터+BM25 가중 RRF 상위 30개)이다.
리랭크 질문: 원질문(현재) / 원질문 + 바꿔쓴 검색어(B). Luna에게는 원질문만 보여 준다(답변 단계와 같다).
"""
import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import config  # noqa: E402
import src.search.search as ss  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import RERANK_TOP, Searcher, group_by_article  # noqa: E402

KS = (5, 8, 10, 12)
POOL = 16


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--out", default="exp_b_luna.json")
    args = ap.parse_args()

    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    s._select_llm = ChatOpenAI(model=config.JUDGE_MODEL, temperature=0, timeout=120, max_retries=2)
    t2d = {v["title"]: d for d, v in s.documents.items()}
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    ss.CITE_WHOLE_ARTICLE = True
    ss.SIBLING_LIMIT = 3000

    pools = []
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
        extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
        fused = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken][:RERANK_TOP]
        pools.append((g, exact, fused))
    print("후보 준비 완료", flush=True)

    need = sum(len({key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}) for g, *_ in pools)

    def recall(chosen_per_q, k):
        got = full = 0
        for (g, exact, _), chosen in zip(pools, chosen_per_q):
            want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
            res = []
            for h in (exact + chosen)[:k]:
                it = s._entry(h, status=h.get("status"))
                it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                           "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                res.append(it)
            asm = s.assemble(res)
            used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
            got += len(want & used)
            full += want <= used
        return got / need, full

    def hit_mrr(chosen_per_q):
        firsts = []
        for (g, _, _), chosen in zip(pools, chosen_per_q):
            idx = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"] if "부칙" not in e["조항"] and "별지" not in e["조항"]}
            if idx:
                pos = [i for i, h in enumerate(chosen, 1) if ckey[h["chunk_id"]] in idx]
                firsts.append(pos[0] if pos else None)
        return [sum(1 for f in firsts if f and f <= t) / len(firsts) for t in (1, 3, 5, 10)], sum(1 / f for f in firsts if f) / len(firsts)

    results = {}
    for label, use_b in (("현재 (리랭크 질문 = 원질문)", False), ("B (리랭크 질문 = 원질문 + 바꿔쓴 검색어)", True)):
        orders = []
        for g, exact, fused in pools:
            q = g["q"]
            rq = q + " " + rew.get(q, "") if use_b else q
            ranked = s.rerank(rq, [dict(h) for h in fused])
            ranked, _ = glossary.filter_candidates(q, ranked, s.glossary)
            orders.append(group_by_article(ranked))
        cells = {k: recall(orders, k) for k in KS}
        hm = hit_mrr(orders)
        results[label] = {"BGE만": cells}
        print(f"\n[{label}] BGE만     " + " ".join(f"k{k} {r:.3f}({f})" for k, (r, f) in cells.items()), flush=True)
        print("  Hit@1/3/5/10 " + "/".join(f"{x:.2f}" for x in hm[0]) + f" MRR {hm[1]:.3f}", flush=True)
        for k in KS:
            runs = []
            for rep in range(1, args.repeat + 1):
                with ThreadPoolExecutor(8) as ex:
                    chosen = list(ex.map(lambda a: s._llm_select(a[0][0]["q"], a[1][:POOL], k), zip(pools, orders)))
                runs.append((recall(chosen, k), hit_mrr(chosen)))
            avg = sum(r[0][0] for r in runs) / len(runs)
            full = sum(r[0][1] for r in runs) / len(runs)
            hm = runs[0][1]
            results[label][f"Luna k{k}"] = {"평균Recall": avg, "회차": [r[0][0] for r in runs], "전부": full, "hit": hm[0], "mrr": hm[1]}
            print(f"  BGE 상위 {POOL}개 → Luna {k:2d}개  Recall 평균 {avg:.3f} (회차 {', '.join(f'{r[0][0]:.3f}' for r in runs)}) 전부 확보 {full:.1f}"
                  f" | Hit@1/3/5/10 " + "/".join(f"{x:.2f}" for x in hm[0]) + f" MRR {hm[1]:.3f}", flush=True)
        json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
