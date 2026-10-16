"""정답 조문이 검색 단계마다 몇 위였는지 따라가서, 어디서 밀렸는지 본다.

단계: 벡터(원래 질문) / BM25(원래 질문) / 보조 검색어(바꿔 쓴 검색어 + 동의어 + 용어집 신호) /
     가중 RRF 합침 / 리랭크(상위 30개를 원래 질문으로 채점). 실제 Searcher.search와 같은 함수를 쓴다.
최종(리랭크 뒤) 10위 밖인 질문은 정답과 그 위 조문들의 리랭크 점수·본문 앞부분을 보여 준다.
바꿔 쓴 검색어는 src/eval/run_multi.py가 저장한 eval/runs/multi-cache.jsonl을 쓴다(없으면 보조 검색어 없이).

실행 (저장소 루트에서):
  uv run python -m src.eval.rank_trace                 # GPU
  uv run python -m src.eval.rank_trace --device cpu    # GPU를 다른 작업이 쓰고 있을 때
  uv run python -m src.eval.rank_trace --no-synonyms --out rank_trace_nosyn.json   # 동의어 사전 끄고
  uv run python -m src.eval.rank_trace --questions eval/questions_100_retrieval.jsonl --llm-rerank --out rank_trace_llm_on.json
      # 교차 인코더 뒤 2단계 LLM 리랭크(검색_설계.md 5절)까지 돌려 "리랭크"(교차 인코더)와 "LLM리랭크" 순위를 함께 기록
  uv run python -m src.eval.rank_trace --summarize eval/runs/rank_trace_llm_on.json   # 저장된 결과로 R@k 표만 다시 계산
"""
import argparse
import json
import os
import sys

from config import BASE_DIR
from src.chunk.articles import article_id
from src.eval.run import by_article
from src.eval.run_multi import CACHE, gold_groups
from src.search import glossary, synonyms
from src.search.search import RERANK_TOP, Searcher

sys.stdout.reconfigure(encoding="utf-8")

QUESTIONS = os.path.join(BASE_DIR, "eval", "questions_190_retrieval.jsonl")


def make_searcher(device):
    """Searcher는 GPU로 고정돼 있어서, CPU일 때는 같은 속성을 CPU 모델로 채운 인스턴스를 만든다."""
    if device == "cuda":
        return Searcher()
    from kiwipiepy import Kiwi
    from sentence_transformers import CrossEncoder, SentenceTransformer
    from config import EMBED_MODEL, RERANK_HEADER, RERANK_MODEL, USE_SYNONYMS
    from src.store.pgstore import PgStore
    s = Searcher.__new__(Searcher)
    s.store, s.kiwi = PgStore(), Kiwi()
    s.model = SentenceTransformer(EMBED_MODEL, device="cpu")  # CPU는 fp16을 쓰지 않는다
    s.reranker = CrossEncoder(RERANK_MODEL, device="cpu")
    s.rerank_header = RERANK_HEADER
    s.synonym_table = synonyms.load() if USE_SYNONYMS else {}
    s.glossary = glossary.load()
    s.glossary_filter, s.last_glossary_excluded, s.last_hint = True, [], ""
    s._load_index()
    return s


def summarize(rows, key="리랭크_순위"):
    """검색_설계.md 5절 표의 숫자. 근거 묶음 기준 R@5/R@7/R@10(찾은 묶음 수 / 전체 묶음 수), 모든 묶음이 5위 안인 문항 수,
    리랭크 창 안에 있지만 6위 이하인 묶음 수, 창 밖(RRF 상위 60개 안에는 있는데 리랭크 창에 못 든) 묶음 수."""
    def best(group, order):
        ranks = [i for i, a in enumerate(order, start=1) if a in group]
        return min(ranks) if ranks else None
    total, found, all5, late, outside = 0, {5: 0, 7: 0, 10: 0}, 0, 0, 0
    for r in rows:
        if key not in r:
            continue
        groups = [set(g) for g in r["정답묶음"]]
        bests = [best(g, r[key]) for g in groups]
        total += len(groups)
        for b, g in zip(bests, groups):
            for k in found:
                found[k] += bool(b and b <= k)
            late += bool(b and b > 5)
            outside += b is None and best(g, r.get("합침_순위", [])) is not None
        all5 += all(b and b <= 5 for b in bests)
    return {"묶음": total, "R@5": found[5], "R@7": found[7], "R@10": found[10], "전부5위안": all5,
            "6위이하": late, "창밖": outside}


def print_summary(rows):
    print("\n근거 묶음 기준 (검색_설계.md 5절 표 형식): R@5 / 전부 찾은 문항 / R@7 / R@10 / 리랭크 6위 이하 / 창 밖")
    for key, label in (("리랭크_순위", "교차 인코더"), ("LLM리랭크_순위", "LLM 리랭크")):
        m = summarize(rows, key)
        if not m["묶음"]:
            continue
        n = m["묶음"]
        print(f"  {label:<8} R@5 {m['R@5'] / n:.3f} ({m['R@5']}/{n})  전부 {m['전부5위안']}  R@7 {m['R@7'] / n:.3f}  "
              f"R@10 {m['R@10'] / n:.3f}  6위 이하 {m['6위이하']}  창 밖 {m['창밖']}")


def rank(articles, groups):
    """정답 묶음 중 가장 앞선 순위 (조 단위, 1부터). 없으면 None."""
    flat = set().union(*groups)
    return next((i for i, a in enumerate(articles, start=1) if a in flat), None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    ap.add_argument("--questions", default=QUESTIONS)
    ap.add_argument("--no-synonyms", action="store_true", help="동의어 사전을 끄고 실행 (전후 비교용)")
    ap.add_argument("--synonyms", action="store_true", help="config와 상관없이 동의어 사전을 켜고 실행 (전후 비교용)")
    ap.add_argument("--no-rewrite", action="store_true", help="GPT가 바꿔 쓴 검색어를 쓰지 않고 실행 (전후 비교용)")
    ap.add_argument("--out", default="rank_trace.json", help="eval/runs/ 아래 저장할 파일 이름")
    ap.add_argument("--llm-rerank", action="store_true",
                    help="교차 인코더 뒤에 2단계 LLM 리랭크(config.LLM_RERANK_*)를 돌려 'LLM리랭크' 순위도 기록 (전후 비교용)")
    ap.add_argument("--llm-model", default=None, help="LLM 리랭크 모델 (기본 config.LLM_RERANK_MODEL)")
    ap.add_argument("--llm-top", type=int, default=None, help="LLM에 넘길 교차 인코더 상위 개수 (기본 config.LLM_RERANK_TOP)")
    ap.add_argument("--summarize", default=None, help="저장된 결과 json 경로. 검색 없이 표만 다시 계산")
    args = ap.parse_args()
    if args.summarize:
        print_summary(json.load(open(args.summarize, encoding="utf-8")))
        return
    questions = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    cache = {}
    if os.path.exists(CACHE):
        cache = {r["q"]: r for r in map(json.loads, open(CACHE, encoding="utf-8"))}
    s = make_searcher(args.device)
    if args.no_synonyms:
        s.synonym_table = {}
    if args.synonyms:
        s.synonym_table = synonyms.load()
    llm = None
    if args.llm_rerank:
        from config import LLM_RERANK_MODEL, LLM_RERANK_TOP
        from src.search.llm_rerank import LLMReranker
        llm = LLMReranker(model=args.llm_model or LLM_RERANK_MODEL, top=args.llm_top or LLM_RERANK_TOP)
        print(f"LLM 리랭크: {llm.model_name}, 상위 {llm.top}개")
    rows = []
    for i, q in enumerate(questions, 1):
        text, groups = q["q"], gold_groups(q)
        rewritten = None if args.no_rewrite else cache.get(text, {}).get("rewritten")
        vec, key = s.vector_hits(text, None), s.bm25_hits(text, None)
        hint = " ".join(x for x in (rewritten, synonyms.expand(text, s.synonym_table),
                                    glossary.signal_terms(text, s.glossary)) if x)
        extra = ()
        if hint:
            extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), 0.7), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), 0.3))
        fused = s.fuse(vec, key, extra=extra)
        reranked = s.rerank(text, [dict(h) for h in s.rerank_window(fused)])  # MERGE_BEFORE_RERANK도 여기서 적용
        row = {"q": text, "hint": hint,
               "벡터": rank(by_article(vec), groups), "키워드": rank(by_article(key), groups),
               "보조벡터": rank(by_article(extra[0][1]), groups) if extra else None,
               "합침": rank(by_article(fused), groups), "리랭크": rank(by_article(reranked), groups)}
        # 근거가 어느 단계에서 빠졌는지 나누려고 단계별 조 목록을 남긴다 (src/eval/loss_breakdown.py)
        row["후보"] = sorted(set(by_article(vec)) | set(by_article(key))
                           | (set(by_article(extra[0][1])) | set(by_article(extra[1][1])) if extra else set()))
        row["합침_순위"] = by_article(fused)[:60]
        row["리랭크_순위"] = by_article(reranked)  # Recall@k 계산용 (조 ID, 리랭크 순서)
        row["정답묶음"] = [sorted(g) for g in groups]
        flat = set().union(*groups)
        if llm is not None:  # 2단계: 교차 인코더 상위 llm.top개를 LLM이 재채점. 실패 문서는 그 자리 (llm_rerank None)
            llm_ranked = llm.rerank(text, [dict(h) for h in reranked], s.rerank_text)
            row["LLM리랭크"] = rank(by_article(llm_ranked), groups)
            row["LLM리랭크_순위"] = by_article(llm_ranked)
            row["llm_top"] = [(article_id(h["chunk_id"]), h.get("llm_rerank"), round(h["rerank"], 4),
                               h["chunk_id"] in flat or article_id(h["chunk_id"]) in flat) for h in llm_ranked[:llm.top]]
        row["top"] = [(article_id(h["chunk_id"]), round(h["rerank"], 4), h["chunk_id"] in flat or article_id(h["chunk_id"]) in flat,
                       s.rerank_text(h["chunk_id"])[:90].replace("\n", " ")) for h in reranked[:6]]
        gold_hits = [h for h in reranked if article_id(h["chunk_id"]) in flat]
        row["gold"] = [(article_id(h["chunk_id"]), round(h["rerank"], 4), s.rerank_text(h["chunk_id"])[:90].replace("\n", " "))
                       for h in gold_hits[:2]]
        rows.append(row)
        print(f"  {i}/{len(questions)}", flush=True)
    s.close()
    if llm is not None:
        print(f"LLM 호출 {llm.calls}회 (캐시 적중 제외), 캐시 {llm.cache_path}")

    out = os.path.join(BASE_DIR, "eval", "runs", args.out)
    json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print_summary(rows)
    n = len(rows)
    print(f"\n질문 {n}개: 단계별로 정답이 10위 안 / 30위 안 / 못 찾음")
    for k in ["벡터", "키워드", "보조벡터", "합침", "리랭크"] + (["LLM리랭크"] if llm is not None else []):
        v = [r[k] for r in rows]
        print(f"  {k:<6} {sum(1 for x in v if x and x <= 10):>4} {sum(1 for x in v if x and x <= 30):>4} {sum(1 for x in v if not x):>4}")
    print("\n리랭크 뒤 10위 밖 (단계별 순위: 벡터/키워드/보조벡터/합침/리랭크)")
    for r in rows:
        if r["리랭크"] and r["리랭크"] <= 10:
            continue
        f = lambda x: "-" if x is None else str(x)  # noqa: E731
        print(f"\n- {r['q']}\n  순위 {f(r['벡터'])}/{f(r['키워드'])}/{f(r['보조벡터'])}/{f(r['합침'])}/{f(r['리랭크'])}  보조 검색어: {r['hint'][:60]}")
        for a, sc, txt in r["gold"]:
            print(f"  정답 {a} 점수 {sc}: {txt}")
        for a, sc, ok, txt in r["top"][:3]:
            print(f"  {'정답' if ok else '상위'} {a} 점수 {sc}: {txt}")
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
