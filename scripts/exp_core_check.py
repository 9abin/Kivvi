"""Luna 8개와 10개의 '필수 근거 Recall'이 같게 나온 것이 맞는지 확인한다.

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_core_check.py --questions ../한국벤처투자_내부규정_최신개정본/eval/answer_eval_100_tiered.jsonl

같은 BGE 상위 16개 조에서 Luna가 8개와 10개를 고르게 해(반복 2번) 질문마다 최종 근거에 든 정답 근거를 (필수/보조)로 나눠 센다.
8개 → 10개로 갈 때 새로 든 정답 근거가 필수인지 보조인지 목록으로 보여 준다.
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


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--repeat", type=int, default=2)
    args = ap.parse_args()
    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    s._select_llm = ChatOpenAI(model=config.JUDGE_MODEL, temperature=0, timeout=120, max_retries=2)
    t2d = {v["title"]: d for d, v in s.documents.items()}
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    ss.CITE_WHOLE_ARTICLE = True
    ss.SIBLING_LIMIT = 3000
    data = []
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
        extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
        a30 = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken][:RERANK_TOP]
        ranked = s.rerank(q, [dict(h) for h in a30])
        ranked, _ = glossary.filter_candidates(q, ranked, s.glossary)
        want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
        core = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"] if e.get("tier", "필수") == "필수"}
        data.append({"g": g, "exact": exact, "order": group_by_article(ranked), "want": want, "core": core})
    n_core = sum(len(d["core"]) for d in data)
    n_all = sum(len(d["want"]) for d in data)
    print(f"정답 근거 {n_all}개, 필수 {n_core}개, 보조 {n_all - n_core}개", flush=True)

    def used_sets(chosen_per_q, k):
        out = []
        for d, chosen in zip(data, chosen_per_q):
            res = []
            for h in (d["exact"] + chosen)[:k]:
                it = s._entry(h, status=h.get("status"))
                it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                           "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                res.append(it)
            asm = s.assemble(res)
            out.append({key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]})
        return out

    for rep in range(1, args.repeat + 1):
        U = {}
        for k in (8, 10):
            with ThreadPoolExecutor(8) as ex:
                chosen = list(ex.map(lambda a: s._llm_select(a[0]["g"]["q"], a[1][:16], k), zip(data, [d["order"] for d in data])))
            U[k] = used_sets(chosen, k)
            a = sum(len(d["want"] & u) for d, u in zip(data, U[k]))
            c = sum(len(d["core"] & u) for d, u in zip(data, U[k]))
            print(f"[회차 {rep}] Luna {k}개: 전체 근거 {a}/{n_all} ({a / n_all:.3f}), 필수 근거 {c}/{n_core} ({c / n_core:.3f})", flush=True)
        gain_core = sum(len((d["core"] & U[10][i]) - U[8][i]) for i, d in enumerate(data))
        gain_aux = sum(len(((d["want"] - d["core"]) & U[10][i]) - U[8][i]) for i, d in enumerate(data))
        lost_core = sum(len((d["core"] & U[8][i]) - U[10][i]) for i, d in enumerate(data))
        lost_aux = sum(len(((d["want"] - d["core"]) & U[8][i]) - U[10][i]) for i, d in enumerate(data))
        print(f"  8개 → 10개로 갈 때: 필수 근거 새로 든 {gain_core}개 / 빠진 {lost_core}개, 보조 근거 새로 든 {gain_aux}개 / 빠진 {lost_aux}개", flush=True)
    s.close()


if __name__ == "__main__":
    main()
