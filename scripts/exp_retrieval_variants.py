"""검색 개선 후보를 LLM 답변 없이 '정답 근거가 최종 근거에 들어오는 비율(Recall)'로 비교한다.

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_retrieval_variants.py --questions eval/answer_eval_100.jsonl --out exp_variants_100.json

질문마다 후보 계산(벡터·키워드 검색, RRF, 리랭크)은 한 번만 하고, 설정별로는 그 결과를 골라 쓴다.
바꿔 쓴 검색어는 질문마다 한 번만 만들어 eval/runs/rewrite-cache.json에 저장해 모든 설정이 같은 검색어를 쓴다.
측정: 정답 근거 (규정, 조) 중 답변 근거(assemble 결과)에 들어온 비율. 답변 후 필터(표 범위·용어집 후처리)는 거치지 않는다.

설정
  A 기준 / B 조 전체 인용 복구 / C k=8 / D 같은 조 묶기 / E 동의어 사전 / F 원질문 벡터 상위 10 보호 / G 글자 상한 해제(진단)
  뒤쪽은 위를 합친 조합.
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import config  # noqa: E402
import src.search.search as ss  # noqa: E402
from src.search import glossary, synonyms  # noqa: E402
from src.search.search import RERANK_TOP, Searcher, _article_base  # noqa: E402

# 이름: (k, 동의어, 인용복구, 조 묶기, 원질문 보호, 상한 해제)
VARIANTS = {
    "A 기준": dict(k=5),
    "B 조 전체 인용 복구": dict(k=5, whole=True),
    "C k=8": dict(k=8),
    "D 같은 조 묶기": dict(k=5, dedupe=True),
    "E 동의어 사전": dict(k=5, syn=True),
    "F 원질문 벡터 보호": dict(k=5, protect=True),
    "G 글자 상한 해제(진단)": dict(k=5, unlimited=True),
    "B+C": dict(k=8, whole=True),
    "B+D": dict(k=5, whole=True, dedupe=True),
    "C+D": dict(k=8, dedupe=True),
    "B+C+D": dict(k=8, whole=True, dedupe=True),
    "B+C+D+E": dict(k=8, whole=True, dedupe=True, syn=True),
    "B+C+D+F": dict(k=8, whole=True, dedupe=True, protect=True),
}


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def rewrites(questions):
    path = os.path.join("eval", "runs", "rewrite-cache.json")
    cache = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    todo = [q for q in questions if q not in cache]
    if todo:
        from langchain_openai import ChatOpenAI

        from src.answer.generate import Generator
        gen = Generator()
        gen.model = ChatOpenAI(model=config.OPENAI_MODEL, temperature=0, timeout=120, max_retries=2)
        for i, q in enumerate(todo, 1):
            cache[q] = gen.rewrite_query(q)
            print(f"  바꿔쓰기 {i}/{len(todo)}", flush=True)
        json.dump(cache, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--out", default="exp_variants_100.json")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    if args.limit:
        gold = gold[:args.limit]
    rew = rewrites([g["q"] for g in gold])

    s = Searcher()
    s.glossary_filter = True
    syn_table = synonyms.load()
    t2d = {v["title"]: d for d, v in s.documents.items()}
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT

    # 1) 후보 계산: 동의어 없음/있음 두 가지 검색어로, 질문마다 한 번씩
    cand = {}
    t0 = time.time()
    for i, g in enumerate(gold, 1):
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        vec = s.vector_hits(q, None)
        keyw = s.bm25_hits(q, None)
        vec_top = [h for h in vec if h["chunk_id"] not in taken][:10]
        for use_syn in (False, True):
            hint = " ".join(x for x in (rew[q], synonyms.expand(q, syn_table) if use_syn else "",
                                       glossary.signal_terms(q, s.glossary)) if x)
            extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
            fused = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken]
            pool = fused[:RERANK_TOP]
            pool_ids = {h["chunk_id"] for h in pool}
            add = [dict(h) for h in vec_top if h["chunk_id"] not in pool_ids]
            ranked = s.rerank(q, [dict(h) for h in pool] + add)        # 리랭크 점수는 후보별 독립이라 한 번에 계산
            cand[(g["id"], use_syn)] = (exact, ranked, pool_ids)
        print(f"  후보 {i}/{len(gold)} ({time.time() - t0:.0f}초)", flush=True)

    # 2) 설정별로 근거 조립 후 Recall 계산
    results, per_q = {}, {}
    for name, v in VARIANTS.items():
        ss.CITE_WHOLE_ARTICLE = bool(v.get("whole"))
        ss.SIBLING_LIMIT = 10 ** 9 if v.get("unlimited") else 3000
        budget = 10 ** 9 if v.get("unlimited") else config.CONTEXT_CHAR_BUDGET
        got = need = full = got_idx = need_idx = full_idx = 0
        chars, arts = [], []
        per_q[name] = {}
        for g in gold:
            exact, ranked, pool_ids = cand[(g["id"], bool(v.get("syn")))]
            rr = [h for h in ranked if v.get("protect") or h["chunk_id"] in pool_ids]
            rr, _ = glossary.filter_candidates(g["q"], rr, s.glossary)
            if v.get("dedupe"):
                seen, picked = set(), []
                for h in rr:
                    kk = (h["doc_id"], _article_base(h["article"]))
                    if kk not in seen:
                        seen.add(kk)
                        picked.append(h)
                rr = picked
            results_ = []
            for h in (exact + rr)[:v["k"]]:
                it = s._entry(h, status=h.get("status"))
                it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                           "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                results_.append(it)
            asm = s.assemble(results_, budget=budget)
            used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
            want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
            want_idx = {w for w in want if ":부칙" not in w and ":별지" not in w}        # 색인되는 종류만
            got += len(want & used); need += len(want); full += want <= used
            got_idx += len(want_idx & used); need_idx += len(want_idx); full_idx += want_idx <= used
            chars.append(asm["chars"]); arts.append(len({(it["doc_id"], _article_base(it["article"])) for grp in asm["groups"] for it in grp["items"]}))
            per_q[name][g["id"]] = sorted(want - used)
        results[name] = dict(recall=got / need, recall_indexed=got_idx / need_idx, full=full, full_indexed=full_idx,
                             chars=sum(chars) / len(chars), arts=sum(arts) / len(arts))
        r = results[name]
        print(f"{name:20s} Recall {r['recall']:.3f} (색인 대상만 {r['recall_indexed']:.3f}) | 전부 확보 {full}/{len(gold)} "
              f"(색인 대상만 {full_idx}) | 평균 {r['chars']:.0f}자, {r['arts']:.1f}개 조", flush=True)

    json.dump({"results": results, "missing": per_q}, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
