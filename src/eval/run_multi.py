"""질문 나누기 전후 검색 비교. 지금 방식(질문 한 줄 + 바꿔 쓴 검색어)과 나누는 방식(search_plan)을 같은 질문으로 잰다.

실행 (프로젝트 루트에서):
  uv run python -m src.eval.run_multi                                   # 복수 질문 20개 (eval/questions_multi.jsonl)
  uv run python -m src.eval.run_multi --questions eval/questions.jsonl  # 단일 질문 277개: 나누기가 해치지 않는지

지표 (조 단위, src/eval/run.py와 같은 채점)
  모두@6, 모두@10: 정답 조가 상위 6개 / 10개 안에 모두 든 질문의 비율. 복수 질문의 성공 기준이다.
                 6은 답변 단계가 하위 질문 2개일 때 받는 개수(search_plan의 k=5 → 6)다.
  Hit@10: 정답 조 중 하나라도 상위 10개 안에 든 질문의 비율
GPT 응답(바꿔 쓴 검색어, 질문 나누기)은 eval/runs/multi-cache.jsonl에 저장해 다시 돌릴 때 재사용한다.
"""
import argparse
import hashlib
import json
import os
import sys
import time

from config import BASE_DIR
from src.answer.generate import PLAN_PROMPT, REWRITE_PROMPT, Generator
from src.chunk.articles import article_id
from src.eval.run import by_article, load_questions
from src.search.search import Searcher

sys.stdout.reconfigure(encoding="utf-8")

MULTI = os.path.join(BASE_DIR, "eval", "questions_multi.jsonl")
CACHE = os.path.join(BASE_DIR, "eval", "runs", "multi-cache.jsonl")
K = 10


def _version(prompt):
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8]


def load_cache():
    """질문별 GPT 응답. 같은 질문이 여러 번 저장돼 있으면 마지막 것을 쓴다."""
    if not os.path.exists(CACHE):
        return {}
    with open(CACHE, encoding="utf-8") as f:
        return {r["q"]: r for r in map(json.loads, f) if r}


def llm_inputs(gen, cache, q):
    """질문 하나에 대한 (바꿔 쓴 검색어, 하위 질문 목록). 캐시에 없거나 프롬프트가 바뀌었으면 GPT를 부르고 저장한다."""
    hit = cache.get(q)
    versions = {"rewrite_v": _version(REWRITE_PROMPT), "plan_v": _version(PLAN_PROMPT)}
    if hit and "rewrite_v" not in hit:  # 버전 기록 전 캐시: REWRITE_PROMPT는 그 뒤로 바뀌지 않았다
        hit["rewrite_v"] = versions["rewrite_v"]
    if not hit or any(hit.get(k) != v for k, v in versions.items()):
        rewritten = hit["rewritten"] if hit and hit.get("rewrite_v") == versions["rewrite_v"] else gen.rewrite_query(q)
        plan = hit["plan"] if hit and hit.get("plan_v") == versions["plan_v"] else gen.plan_queries(q)
        cache[q] = {"q": q, "rewritten": rewritten, "plan": plan, **versions}
        with open(CACHE, "a", encoding="utf-8") as f:
            f.write(json.dumps(cache[q], ensure_ascii=False) + "\n")
    return cache[q]["rewritten"], cache[q]["plan"]


def gold_groups(q):
    """정답을 근거 묶음으로. "정답묶음"이 있으면(별표 조각 여럿 = 근거 하나) 그대로, 없으면 조 하나가 한 묶음."""
    if q.get("정답묶음"):
        return [{article_id(c) for c in g} for g in q["정답묶음"]]
    return [{a} for a in sorted({article_id(g) for g in q["정답"]})]


def score(articles, groups):
    """모두@k: 모든 묶음에서 하나 이상이 상위 k개 안에 있다. Hit@10: 어느 묶음이든 하나라도 있다."""
    def all_in(k):
        return all(g & set(articles[:k]) for g in groups)
    return {"모두@6": all_in(6), "모두@10": all_in(K), "Hit@10": any(g & set(articles[:K]) for g in groups)}


def run(path):
    questions = load_questions(path)
    gen, searcher, cache = Generator(), Searcher(), load_cache()
    rows = []
    t0 = time.time()
    for i, q in enumerate(questions, start=1):
        rewritten, plan = llm_inputs(gen, cache, q["q"])
        groups = gold_groups(q)
        before = by_article(searcher.search(q["q"], k=K, rewritten=rewritten,
                                            expand_siblings=False, expand_cites=False))
        after = by_article(searcher.search_plan(plan, k=K, expand_siblings=False, expand_cites=False))
        rows.append({"q": q["q"], "group": q.get("어려움", "전체"), "plan": plan,
                     "before": score(before, groups), "after": score(after, groups),
                     "missing_after": sorted(min(g) for g in groups if not g & set(after[:6]))})
        print(f"  {i}/{len(questions)} ({time.time() - t0:.0f}초)", flush=True)
    searcher.close()
    report(rows)


def table(rows, title):
    n = len(rows)
    print(f"\n[{title}] 질문 {n}개, 하위 질문 수 평균 {sum(len(r['plan']) for r in rows) / n:.2f}")
    print(f"{'지표':<10}{'지금':>8}{'나누기':>8}")
    for m in ["모두@6", "모두@10", "Hit@10"]:
        b = sum(r["before"][m] for r in rows) / n
        a = sum(r["after"][m] for r in rows) / n
        print(f"{m:<10}{b:>8.2f}{a:>8.2f}")


def report(rows):
    table(rows, "전체")
    groups = list(dict.fromkeys(r["group"] for r in rows))
    if len(groups) > 1:  # 질문 파일에 '어려움' 구분이 있으면 묶음별로도 본다
        for g in groups:
            table([r for r in rows if r["group"] == g], g)

    print("\n결과가 달라진 질문 (모두@6 기준)")
    for r in rows:
        b, a = r["before"]["모두@6"], r["after"]["모두@6"]
        if a != b:
            print(f"  {'좋아짐' if a else '나빠짐'}: {r['q']}")
            print(f"      하위 질문: {' | '.join(p['질문'] for p in r['plan'])}")
    print("\n나눠도 상위 6개에 정답이 다 안 든 질문")
    for r in rows:
        if not r["after"]["모두@6"]:
            print(f"  - {r['q']}\n      빠진 정답: {', '.join(r['missing_after'])} | 하위 질문: {' | '.join(p['질문'] for p in r['plan'])}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", nargs="+", default=[MULTI])
    run(ap.parse_args().questions)
