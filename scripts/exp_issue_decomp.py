"""쟁점 검색이 후보에 준 변화를 분해한다 (정답 근거 Recall만 본다, LLM 호출 없음, 쟁점 계획 캐시 재사용).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_issue_decomp.py --questions eval/answer_eval_100.jsonl

세는 것
  1. 전체 후보(자르기 전)에 정답 근거가 몇 개 있는지: 현재 4개 목록 합집합 / 쟁점 목록을 더한 합집합
  2. 상위 30개로 자른 뒤: 쟁점 검색 때문에 새로 들어온 정답, 밀려난 정답, 전체 후보에는 있는데 30개 밖인 정답, 어디에도 없는 정답
후보 구성 비교 (모두 BGE 원질문 리랭크, 최종 k=10):
  A30   현재 상위 30개
  A36   현재 상위 36개 (후보 수만 늘린 대조군)
  E30   쟁점 목록까지 합쳐 상위 30개 (지난번 E)
  P36   현재 상위 30개를 그대로 두고 쟁점 검색에서 중복 없는 후보를 최대 6개 추가
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

KS = (8, 10, 12, 16)


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--plan-cache", default=os.path.join("eval", "runs", "issue-plan-cache.json"))
    ap.add_argument("--out", default="exp_issue_decomp.json")
    args = ap.parse_args()

    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    cache = json.load(open(args.plan_cache, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    t2d = {v["title"]: d for d, v in s.documents.items()}
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    ss.CITE_WHOLE_ARTICLE = True
    ss.SIBLING_LIMIT = 3000

    per_q = []
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        base = [("벡터", s.vector_hits(q, None), wv), ("키워드", s.bm25_hits(q, None), wk),
                ("벡터(바꿔쓴)", s.vector_hits(hint, None) if hint else [], wv),
                ("키워드(바꿔쓴)", s.bm25_hits(hint, None) if hint else [], wk)]
        iss = [x for i in cache[q]["쟁점"] for x in ((f"벡터(쟁점{i})", s.vector_hits(i, None), wv), (f"키워드(쟁점{i})", s.bm25_hits(i, None), wk))]
        per_q.append((g, exact, taken, base, iss))
    print("후보 목록 준비 완료", flush=True)

    def fuse(lists, taken):
        f = s.fuse(lists[0][1], [], lists[0][2], 0.0, lists[1:])
        return [h for h in f if h["chunk_id"] not in taken]

    need = sum(len({key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}) for g, *_ in per_q)
    cov = {"기존 전체": 0, "쟁점 포함 전체": 0}
    gained = displaced = lost_cut = never = 0
    pools = {"A30": [], "A36": [], "E30": [], "P36": []}
    for g, exact, taken, base, iss in per_q:
        want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
        fa, fe = fuse(base, taken), fuse(base + iss, taken)
        keys = lambda hits: {ckey[h["chunk_id"]] for h in hits}  # noqa: E731
        cov["기존 전체"] += len(want & keys(fa))
        cov["쟁점 포함 전체"] += len(want & keys(fe))
        a30, e30 = fa[:RERANK_TOP], fe[:RERANK_TOP]
        ka, ke = want & keys(a30), want & keys(e30)
        gained += len(ke - ka)
        displaced += len(ka - ke)
        lost_cut += len((want & keys(fe)) - ke)
        never += len(want - keys(fe))
        ids30 = {h["chunk_id"] for h in a30}
        extra = [h for h in fe if h["chunk_id"] not in ids30 and h["chunk_id"] not in {x["chunk_id"] for x in fa[:RERANK_TOP]}]
        # 쟁점 목록에서만 새로 들어온 후보(기존 4개 목록 합집합에 없는 것)를 우선한다
        base_ids = {h["chunk_id"] for h in fa}
        new_only = [h for h in extra if h["chunk_id"] not in base_ids][:6]
        pools["A30"].append(a30)
        pools["A36"].append(fa[:RERANK_TOP + 6])
        pools["E30"].append(e30)
        pools["P36"].append(a30 + new_only)
    print(f"\n전체 후보(자르기 전)에 정답: 기존 {cov['기존 전체']}/{need} ({cov['기존 전체'] / need:.3f}), 쟁점 포함 {cov['쟁점 포함 전체']}/{need} ({cov['쟁점 포함 전체'] / need:.3f})")
    print(f"상위 30개 자르기 비교(기존 vs 쟁점 합침): 새로 들어온 정답 {gained}개, 밀려난 정답 {displaced}개, 전체 후보에는 있으나 30개 밖 {lost_cut}개, 어디에도 없음 {never}개", flush=True)

    results = {"분해": {"전체후보_기존": cov["기존 전체"], "전체후보_쟁점포함": cov["쟁점 포함 전체"], "새로": gained, "밀림": displaced, "30밖": lost_cut, "없음": never}}
    for name, lists in pools.items():
        in_pool = sum(len({key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]} & {ckey[h["chunk_id"]] for h in pl}) for (g, *_), pl in zip(per_q, lists))
        orders = []
        for (g, *_), pl in zip(per_q, lists):
            ranked = s.rerank(g["q"], [dict(h) for h in pl])
            ranked, _ = glossary.filter_candidates(g["q"], ranked, s.glossary)
            orders.append(group_by_article(ranked))

        def recall(k):
            got = full = 0
            for (g, exact, *_), order in zip(per_q, orders):
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

        cells = {k: recall(k) for k in KS}
        results[name] = {"후보안정답": in_pool, "Recall": cells}
        print(f"[{name}] 후보 안 정답 {in_pool}/{need} | BGE Recall " + " ".join(f"k{k} {r:.3f}({f})" for k, (r, f) in cells.items()), flush=True)
        json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
