"""조 전체 넘기기에서 8,000자 예산 때문에 조가 사라지는지, 줄여 넣기(shorten_article) 전후로 비교한다.

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_budget_shorten.py --questions <tiered 평가셋> [--ks 8,10]

실제 Searcher._as_articles와 assemble을 그대로 쓴다. BGE 순서의 서로 다른 조 k개를 넣고,
"전"은 줄여 넣기를 끈 상태(MIN_SHORTENED를 아주 크게), "후"는 켠 상태에서 센다.
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
from src.search.search import RERANK_TOP, SHORTENED_NOTE, Searcher, group_by_article  # noqa: E402


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
    ap.add_argument("--ks", default="8,10")
    ap.add_argument("--cap", type=int, default=0, help="조 하나의 상한(ARTICLE_CAP). 0이면 기본값")
    args = ap.parse_args()
    ks = [int(x) for x in args.ks.split(",")]
    if args.cap:
        ss.ARTICLE_CAP = args.cap
    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    t2d = {v["title"]: d for d, v in s.documents.items()}
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    orders = []
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
        orders.append((exact, group_by_article(ranked), {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}))
    print(f"질문 {len(gold)}개 준비 완료", flush=True)

    original_min = ss.MIN_SHORTENED
    for k in ks:
        for label, min_short in (("전 (줄여 넣기 끔)", 10 ** 9), ("후 (줄여 넣기 켬)", original_min)):
            ss.MIN_SHORTENED = min_short
            n_art = lost_art = shortened = gold_in = gold_kept = q_gold_lost = 0
            chars = []
            for exact, order, want in orders:
                results = s._as_articles(exact + order, k, True)
                asm = s.assemble(results)
                kept = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
                in_res = {key(r["doc_id"], r["article"]) for r in results}
                n_art += len(in_res)
                lost_art += len(in_res - kept)
                shortened += sum(1 for grp in asm["groups"] for it in grp["items"] if SHORTENED_NOTE.strip() in it["body"])
                gold_in += len(want & in_res)
                gold_kept += len(want & kept)
                q_gold_lost += bool((want & in_res) - kept)
                for i, r in enumerate(results):
                    kk = key(r["doc_id"], r["article"])
                    if kk in want and kk not in kept:
                        print(f"    정답 조 사라짐 [{label[:1]}] k={k} {kk} 순위 {i + 1}/{len(results)}, 조 길이 {len(r['body'])}자, "
                              f"앞 조들 길이 {[len(x['body']) for x in results[:i]]}", flush=True)
                chars.append(asm["chars"])
            print(f"[k={k}] {label}: 사라진 조 {lost_art}/{n_art}, 줄여 넣은 조 {shortened}, 정답 조 {gold_kept}/{gold_in} 유지 "
                  f"(잃은 질문 {q_gold_lost}), 근거 글자 평균 {sum(chars) / len(chars):.0f}자, 최대 {max(chars)}자", flush=True)
    ss.MIN_SHORTENED = original_min
    s.close()


if __name__ == "__main__":
    main()
