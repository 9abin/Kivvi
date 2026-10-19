"""후보를 RRF 상위 50개로 넓혔을 때 세 가지 방식을 비교한다 (정답 근거 Recall만 본다).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_pool50.py --questions eval/answer_eval_100.jsonl --repeat 2

후보: 현재 방식(원질문+바꿔쓴 검색어, 벡터+BM25 가중 RRF) 상위 50개 항. 리랭크 질문은 원질문.
  1. BGE50          RRF 50개를 BGE로 줄 세워 상위 k개 조 (Luna 없음)
  2. BGE50→Luna     BGE 상위 16개 조 중 Luna가 k개 선택
  3. Luna 직접      RRF 순서 그대로 같은 조를 묶은 상위 50개 조 중 Luna가 k개 선택 (BGE 없음)
기준(후보 30개)은 이전 실험 값과 비교한다: BGE만 k=10 0.731, BGE 16→Luna 10 약 0.79, RRF 30→Luna 10 0.788.
"""
import argparse
import json
import os
import re
import sys
import time
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
from src.search.search import Searcher, group_by_article  # noqa: E402

POOL = 50
KS = (5, 8, 10, 12, 16)
LUNA_KS = (8, 10)


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
    ap.add_argument("--out", default="exp_pool50.json")
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
        fused = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken][:POOL]
        pools.append((g, exact, fused))
    print("후보 준비 완료", flush=True)

    need = sum(len({key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}) for g, *_ in pools)
    in_pool = sum(len({key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]} & {ckey[h["chunk_id"]] for h in f}) for g, _, f in pools)
    print(f"RRF 상위 {POOL}개 안의 정답 {in_pool}/{need} ({in_pool / need:.3f})  (상위 30개는 126/156)", flush=True)

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

    results = {"후보안정답": in_pool}

    # 1. BGE50
    t0 = time.time()
    bge_orders = []
    for g, exact, fused in pools:
        ranked = s.rerank(g["q"], [dict(h) for h in fused])
        ranked, _ = glossary.filter_candidates(g["q"], ranked, s.glossary)
        bge_orders.append(group_by_article(ranked))
    secs = (time.time() - t0) / len(pools)
    cells = {k: recall(bge_orders, k) for k in KS}
    hm = hit_mrr(bge_orders)
    results["BGE50"] = {"Recall": cells, "hit": hm[0], "mrr": hm[1], "초": secs}
    print(f"\n[1. BGE50만] 리랭크 {secs:.1f}초/질문\n  Recall " + " ".join(f"k{k} {r:.3f}({f})" for k, (r, f) in cells.items())
          + "\n  Hit@1/3/5/10 " + "/".join(f"{x:.2f}" for x in hm[0]) + f" MRR {hm[1]:.3f}", flush=True)

    # 2. BGE50 → 상위 16개 조 → Luna
    # 3. RRF 순서의 상위 50개 조 → Luna (BGE 없음)
    rrf_orders = []
    for g, exact, fused in pools:
        ranked, _ = glossary.filter_candidates(g["q"], [dict(h) for h in fused], s.glossary)
        rrf_orders.append(group_by_article(ranked)[:POOL])
    for name, orders, width in (("2. BGE50 → 상위 16개 → Luna", bge_orders, 16), ("3. RRF 50개 → Luna 직접", rrf_orders, POOL)):
        for k in LUNA_KS:
            runs = []
            for _ in range(args.repeat):
                t1 = time.time()
                with ThreadPoolExecutor(8) as ex:
                    chosen = list(ex.map(lambda a: s._llm_select(a[0][0]["q"], a[1][:width], k), zip(pools, orders)))
                runs.append((recall(chosen, k), hit_mrr(chosen), time.time() - t1))
            avg = sum(r[0][0] for r in runs) / len(runs)
            full = sum(r[0][1] for r in runs) / len(runs)
            results[f"{name} k{k}"] = {"평균": avg, "회차": [r[0][0] for r in runs], "전부": full, "hit": runs[0][1][0], "mrr": runs[0][1][1]}
            print(f"[{name}] Luna {k:2d}개  Recall 평균 {avg:.3f} (회차 {', '.join(f'{r[0][0]:.3f}' for r in runs)}) 전부 {full:.1f}"
                  f" | Hit@1/3/5/10 " + "/".join(f"{x:.2f}" for x in runs[0][1][0]) + f" MRR {runs[0][1][1]:.3f}", flush=True)
        json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
