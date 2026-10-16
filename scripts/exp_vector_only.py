"""후보를 벡터 검색만으로 만들었을 때(키워드 BM25 제거)와 지금의 하이브리드를 비교한다 (정답 근거 Recall만 본다).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_vector_only.py --questions eval/answer_eval_100.jsonl

두 후보 구성을 같은 조건으로 비교한다.
  하이브리드  벡터 0.7 + 키워드 0.3 (원질문·바꿔쓴 검색어 각 두 목록, 현재 방식)
  벡터 단독   벡터 목록만 (원질문·바꿔쓴 검색어), 키워드 목록은 쓰지 않음
재는 것: 후보 30개 안에 정답이 있는 비율(상한), BGE 점수순 Recall(k=5·8·10·12·16)과 Hit·MRR, 질문당 후보 만드는 시간.
Luna 선택(BGE 상위 16개 조 → k개)은 --luna로 켠다.
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
from src.search import glossary  # noqa: E402
from src.search.search import RERANK_TOP, Searcher, group_by_article  # noqa: E402

KS = (5, 8, 10, 12, 16)


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--luna", action="store_true", help="BGE 상위 16개 조 → Luna가 k개(8·10) 선택도 잰다")
    ap.add_argument("--out", default="exp_vector_only.json")
    args = ap.parse_args()

    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    t2d = {v["title"]: d for d, v in s.documents.items()}
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    ss.CITE_WHOLE_ARTICLE = True
    ss.SIBLING_LIMIT = 3000

    pools = {"하이브리드": [], "벡터 단독": []}
    secs = {"하이브리드": 0.0, "벡터 단독": 0.0}
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        t = time.time()
        vec = s.vector_hits(q, None)
        vec_h = s.vector_hits(hint, None) if hint else []
        secs["벡터 단독"] += time.time() - t
        t = time.time()
        keyw = s.bm25_hits(q, None)
        key_h = s.bm25_hits(hint, None) if hint else []
        extra_h = (("벡터(바꿔쓴)", vec_h, wv), ("키워드(바꿔쓴)", key_h, wk)) if hint else ()
        hy = [h for h in s.fuse(vec, keyw, wv, wk, extra_h) if h["chunk_id"] not in taken][:RERANK_TOP]
        secs["하이브리드"] += time.time() - t  # 키워드 검색 + 합침 시간만. 벡터 검색 시간은 반복문이 끝난 뒤 합산한다
        vo = [h for h in s.fuse(vec, [], 1.0, 0.0, (("벡터(바꿔쓴)", vec_h, 1.0),) if hint else ()) if h["chunk_id"] not in taken][:RERANK_TOP]
        pools["하이브리드"].append((g, exact, hy))
        pools["벡터 단독"].append((g, exact, vo))
    # 하이브리드의 후보 만드는 시간 = 벡터 검색 시간 + 키워드·합침 시간
    secs["하이브리드"] += secs["벡터 단독"]

    need = sum(len({key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}) for g, *_ in pools["하이브리드"])
    results = {}
    for name, rows in pools.items():
        in_pool = sum(len({key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]} & {ckey[h["chunk_id"]] for h in pool})
                      for g, _, pool in rows)
        order_all = []
        for g, exact, pool in rows:
            ranked = s.rerank(g["q"], [dict(h) for h in pool])
            ranked, _ = glossary.filter_candidates(g["q"], ranked, s.glossary)
            order_all.append(group_by_article(ranked))

        def recall(orders, k):
            got = full = 0
            for (g, exact, _), order in zip(rows, orders):
                want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
                res = []
                for h in (exact + order)[:k]:
                    it = s._entry(h, status=h.get("status"))
                    it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                               "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                    res.append(it)
                asm = s.assemble(res)
                used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
                got += len(want & used)
                full += want <= used
            return got / need, full

        firsts = []
        for (g, _, _), order in zip(rows, order_all):
            idx = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"] if "부칙" not in e["조항"] and "별지" not in e["조항"]}
            if idx:
                pos = [i for i, h in enumerate(order, 1) if ckey[h["chunk_id"]] in idx]
                firsts.append(pos[0] if pos else None)
        hit = [sum(1 for f in firsts if f and f <= t) / len(firsts) for t in (1, 3, 5, 10)]
        mrr = sum(1 / f for f in firsts if f) / len(firsts)
        cells = {k: recall(order_all, k) for k in KS}
        results[name] = {"후보안정답": in_pool / need, "Recall": cells, "hit": hit, "mrr": mrr, "후보초": secs[name] / len(gold)}
        print(f"\n[{name}] 후보 30개 안에 정답 {in_pool}/{need} = {in_pool / need:.3f} | 후보 만드는 시간(검색만) {secs[name] / len(gold):.2f}초/질문", flush=True)
        print("  BGE 점수순 Recall " + " ".join(f"k{k} {r:.3f}({f})" for k, (r, f) in cells.items()), flush=True)
        print(f"  Hit@1/3/5/10 " + "/".join(f"{x:.2f}" for x in hit) + f" MRR {mrr:.3f}", flush=True)

        if args.luna:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(model=config.JUDGE_MODEL, temperature=0, timeout=120, max_retries=2)
            s._select_llm = llm
            for k in (8, 10):
                def one(a):
                    (g, _, _), order = a
                    return s._llm_select(g["q"], order[:16], k)
                with ThreadPoolExecutor(8) as ex:
                    chosen = list(ex.map(one, zip(rows, order_all)))
                r, f = recall(chosen, k)
                results[name][f"Luna k{k}"] = (r, f)
                print(f"  BGE 16개 → Luna {k}개 선택 Recall {r:.3f} ({f})", flush=True)
        json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
