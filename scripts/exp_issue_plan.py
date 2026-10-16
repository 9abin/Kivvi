"""검색 전 '쟁점 계획' 단계 시험 (정답 근거 Recall만 본다, 최종 답변은 만들지 않는다).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_issue_plan.py --questions eval/answer_eval_100.jsonl

단계: 질문 → Luna가 (맥락, 확인할 쟁점 2~3개, 미확정 사항)을 만든다. 규정 이름 목록을 참고하게 한다.
      → 후보: 현재(원질문+바꿔쓴 검색어) / 현재 + 쟁점별 검색 목록
      → 리랭크 질문: 원질문 / 원질문+바꿔쓴 검색어 / 원질문+쟁점 / 후보마다 (원질문, 쟁점별) 점수의 최댓값
쟁점 계획은 eval/runs/issue-plan-cache.json에 캐시한다. 평가 정답은 계획 입력에 쓰지 않는다.
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
from langchain_openai import ChatOpenAI  # noqa: E402

KS = (5, 8, 10, 12, 16)
PROMPT = """직원이 사내 규정 검색창에 쓴 구어체 질문을 보고, 답하려면 사내 규정에서 무엇을 확인해야 하는지 검색 계획으로 정리한다.
사내 규정 이름 목록(참고용, 이 안에서 관련 있을 만한 것을 떠올린다):
{docs}

출력은 JSON 하나만 쓴다.
{{"맥락": "질문의 상황이 규정에서 어떤 제도·금지·허용·절차로 다뤄질지 한두 문장",
  "쟁점": ["규정에서 찾아볼 질문 (공식 용어로, 혼자 읽어도 뜻이 통하게)", "...", "..."],
  "미확정": "질문만으로는 알 수 없어서 규정 적용 여부를 정할 수 없는 사실"}}
- 쟁점은 2~3개. 서로 다른 조문을 찾게 하는 것이어야 한다 (기준, 조건·예외, 절차 등).
- 질문의 상황이 규정이 정한 대상에 해당한다고 결론 내리지 않는다. 해당하는지 확인할 규정을 찾는 질문으로 쓴다.
- 질문에 없는 사실이나 수치를 만들지 않는다. 답을 쓰지 않는다."""


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
    ap.add_argument("--out", default="exp_issue_plan.json")
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

    # --- 쟁점 계획 (캐시) ---
    cache = json.load(open(args.plan_cache, encoding="utf-8")) if os.path.exists(args.plan_cache) else {}
    llm = ChatOpenAI(model=config.JUDGE_MODEL, temperature=0, timeout=120, max_retries=2)
    docs = "\n".join(sorted(v["title"] for v in s.documents.values()))

    def plan(q):
        t = time.time()
        out = llm.invoke(PROMPT.format(docs=docs) + "\n\n질문: " + q).content
        try:
            d = json.loads(re.search(r"\{.*\}", out, re.S).group(0))
            return {"맥락": d.get("맥락", ""), "쟁점": [x for x in d.get("쟁점", []) if x][:3], "미확정": d.get("미확정", ""), "초": time.time() - t}
        except Exception:  # noqa: BLE001
            return {"맥락": "", "쟁점": [], "미확정": "", "초": time.time() - t, "실패": True}

    todo = [g["q"] for g in gold if g["q"] not in cache]
    with ThreadPoolExecutor(8) as ex:
        for q, p in zip(todo, ex.map(plan, todo)):
            cache[q] = p
    json.dump(cache, open(args.plan_cache, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    fails = sum(1 for g in gold if cache[g["q"]].get("실패"))
    print(f"쟁점 계획 {len(gold)}개 (실패 {fails}), 쟁점 평균 {sum(len(cache[g['q']]['쟁점']) for g in gold) / len(gold):.1f}개", flush=True)
    for g in gold[:3]:
        print("  예)", g["q"], "→", cache[g["q"]]["쟁점"], flush=True)

    # --- 후보 목록 ---
    per_q = []
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        pl = cache[q]
        per_q.append({"g": g, "exact": exact, "taken": taken, "hint": hint,
                      "base": [("벡터", s.vector_hits(q, None), wv), ("키워드", s.bm25_hits(q, None), wk),
                               ("벡터(바꿔쓴)", s.vector_hits(hint, None) if hint else [], wv),
                               ("키워드(바꿔쓴)", s.bm25_hits(hint, None) if hint else [], wk)],
                      "iss": [x for i in pl["쟁점"] for x in ((f"벡터(쟁점{i})", s.vector_hits(i, None), wv),
                                                                (f"키워드(쟁점{i})", s.bm25_hits(i, None), wk))]})
    print("후보 목록 준비 완료", flush=True)

    def pool(p, with_issues):
        lists = p["base"] + (p["iss"] if with_issues else [])
        fused = s.fuse(lists[0][1], [], lists[0][2], 0.0, lists[1:])
        return [h for h in fused if h["chunk_id"] not in p["taken"]][:RERANK_TOP]

    def score(query, hits):
        return s.reranker.predict([(query, s.rerank_text(h["chunk_id"])) for h in hits], batch_size=16)

    need = sum(len({key(t2d.get(e["규정"]), e["조항"]) for e in p["g"]["근거"]}) for p in per_q)

    def evaluate(name, with_issues, mode):
        t0 = time.time()
        orders, in_pool = [], 0
        for p in per_q:
            hits = pool(p, with_issues)
            in_pool += len({key(t2d.get(e["규정"]), e["조항"]) for e in p["g"]["근거"]} & {ckey[h["chunk_id"]] for h in hits})
            q, iss = p["g"]["q"], cache[p["g"]["q"]]["쟁점"]
            if mode == "원질문":
                sc = score(q, hits)
            elif mode == "원질문+바꿔쓴":
                sc = score(q + " " + rew.get(q, ""), hits)
            elif mode == "원질문+쟁점":
                sc = score(q + " (확인할 쟁점: " + "; ".join(iss) + ")", hits)
            else:  # 후보마다 원질문·쟁점별 점수의 최댓값
                allsc = [score(x, hits) for x in [q] + iss]
                sc = [max(col) for col in zip(*allsc)]
            ranked = sorted(({**h, "rerank": float(x)} for h, x in zip(hits, sc)), key=lambda h: -h["rerank"])
            ranked, _ = glossary.filter_candidates(q, ranked, s.glossary)
            orders.append(group_by_article(ranked))
        secs = (time.time() - t0) / len(per_q)

        def recall(k):
            got = full = 0
            for p, order in zip(per_q, orders):
                want = {key(t2d.get(e["규정"]), e["조항"]) for e in p["g"]["근거"]}
                res = []
                for h in (p["exact"] + order)[:k]:
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
        for p, order in zip(per_q, orders):
            idx = {key(t2d.get(e["규정"]), e["조항"]) for e in p["g"]["근거"] if "부칙" not in e["조항"] and "별지" not in e["조항"]}
            if idx:
                pos = [i for i, h in enumerate(order, 1) if ckey[h["chunk_id"]] in idx]
                firsts.append(pos[0] if pos else None)
        hit = [sum(1 for f in firsts if f and f <= t) / len(firsts) for t in (1, 3, 5, 10)]
        mrr = sum(1 / f for f in firsts if f) / len(firsts)
        cells = {k: recall(k) for k in KS}
        print(f"\n[{name}] 후보 안 정답 {in_pool}/{need} = {in_pool / need:.3f} | 검색+리랭크 {secs:.1f}초/질문", flush=True)
        print("  Recall " + " ".join(f"k{k} {r:.3f}({f})" for k, (r, f) in cells.items()), flush=True)
        print("  Hit@1/3/5/10 " + "/".join(f"{x:.2f}" for x in hit) + f" MRR {mrr:.3f}", flush=True)
        return {"후보안정답": in_pool / need, "Recall": cells, "hit": hit, "mrr": mrr, "초": secs}

    results = {}
    for name, wi, mode in [("A 현재 (후보 현재, 리랭크 원질문)", False, "원질문"),
                           ("B 리랭크 질문에 바꿔쓴 검색어 추가", False, "원질문+바꿔쓴"),
                           ("C 리랭크 질문에 쟁점 추가", False, "원질문+쟁점"),
                           ("D 리랭크는 원질문·쟁점별 점수 최댓값", False, "최댓값"),
                           ("E 후보에 쟁점 검색 추가, 리랭크 원질문", True, "원질문"),
                           ("F 후보에 쟁점 검색 추가, 리랭크 원질문+쟁점", True, "원질문+쟁점"),
                           ("G 후보에 쟁점 검색 추가, 리랭크 최댓값", True, "최댓값")]:
        results[name] = evaluate(name, wi, mode)
        json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
