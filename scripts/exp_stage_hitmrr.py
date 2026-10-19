"""단계별 Hit@1/3/5/10, MRR (정답 근거 (규정, 조) 기준). LLM 판정 없이 순위만 본다.

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_stage_hitmrr.py --questions <tiered 평가셋> --repeat 2

각 줄은 서로 다른 조의 순서 목록이고, Hit@t는 상위 t개 조 안에 정답 조가 하나라도 있는 질문의 비율, MRR은 첫 정답 조 순위의 역수 평균이다.
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
from langchain_openai import ChatOpenAI  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import RERANK_TOP, Searcher, group_by_article  # noqa: E402

TS = (1, 3, 5, 10)


def key(doc, art):
    m = re.match(r"\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    m = re.match(r"\s*(별표\s*\d+(?:\s*의\s*\d+)?)", art or "")
    return f"{doc}:" + (re.sub(r"\s+", "", m.group(1)) if m else re.sub(r"\s+", "", art or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--out", default="exp_stage_hitmrr.json")
    args = ap.parse_args()
    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    s._select_llm = ChatOpenAI(model=config.JUDGE_MODEL, temperature=0, timeout=120, max_retries=2)
    t2d = {v["title"]: d for d, v in s.documents.items()}
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT

    def distinct(hits):
        seen, out = set(), []
        for h in hits:
            k_ = ckey[h["chunk_id"]]
            if k_ not in seen:
                seen.add(k_)
                out.append(k_)
        return out

    data = []
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
        extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
        full = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken]
        ranked = s.rerank(q, [dict(h) for h in full[:RERANK_TOP]])
        ranked, _ = glossary.filter_candidates(q, ranked, s.glossary)
        order = group_by_article(ranked)
        data.append({"g": g, "exact": [ckey[h["chunk_id"]] for h in exact], "full": distinct(full), "rrf30": distinct(full[:RERANK_TOP]),
                     "order": order, "bge": distinct(order),
                     "want": {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}})

    def score(orders, cap=None):
        hits = {t: 0 for t in TS}
        rr = 0.0
        for d, o in zip(data, orders):
            o = (d["exact"] + [x for x in o if x not in d["exact"]])
            o = o[:cap] if cap else o
            pos = [i for i, x in enumerate(o, 1) if x in d["want"]]
            for t in TS:
                hits[t] += bool(pos and pos[0] <= t)
            rr += 1 / pos[0] if pos else 0
        n = len(data)
        return [hits[t] / n for t in TS], rr / n

    results = {}

    def show(name, orders, cap=None):
        h, m = score(orders, cap)
        results[name] = {"hit": h, "mrr": m}
        print(f"{name:34s} Hit@1/3/5/10 " + "/".join(f"{x:.2f}" for x in h) + f"  MRR {m:.3f}", flush=True)

    show("자르기 전 전체 후보 (조 순서)", [d["full"] for d in data])
    show("RRF 상위 30개 (조 순서)", [d["rrf30"] for d in data])
    show("BGE 상위 16개 조", [d["bge"] for d in data], 16)
    for k in (5, 8, 10, 12, 16):
        show(f"BGE만 k={k}", [d["bge"] for d in data], k)
    for k in (8, 10):
        hm = []
        for _ in range(args.repeat):
            with ThreadPoolExecutor(8) as ex:
                picked = list(ex.map(lambda a: s._llm_select(a[0]["g"]["q"], a[0]["order"][:16], k), [(d,) for d in data]))
            hm.append(score([distinct(p) for p in picked], k))
        h = [sum(x[0][i] for x in hm) / len(hm) for i in range(len(TS))]
        m = sum(x[1] for x in hm) / len(hm)
        results[f"BGE 16 -> Luna k={k}"] = {"hit": h, "mrr": m, "회차": [[x[0], x[1]] for x in hm]}
        print(f"{'BGE 16 -> Luna k=' + str(k):34s} Hit@1/3/5/10 " + "/".join(f"{x:.2f}" for x in h) + f"  MRR {m:.3f}  (평균 {args.repeat}회)", flush=True)
    json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
