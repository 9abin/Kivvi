"""정답 조문이 검색 단계마다 몇 위였는지 따라가서, 어디서 밀렸는지 본다.

단계: 벡터(원래 질문) / BM25(원래 질문) / 보조 검색어(바꿔 쓴 검색어 + 동의어 + 용어집 신호) /
     가중 RRF 합침 / 리랭크(상위 30개를 원래 질문으로 채점). 실제 Searcher.search와 같은 함수를 쓴다.
최종(리랭크 뒤) 10위 밖인 질문은 정답과 그 위 조문들의 리랭크 점수·본문 앞부분을 보여 준다.
바꿔 쓴 검색어는 src/eval/run_multi.py가 저장한 eval/runs/multi-cache.jsonl을 쓴다(없으면 보조 검색어 없이).

실행 (저장소 루트에서):
  uv run python -m src.eval.rank_trace                 # GPU
  uv run python -m src.eval.rank_trace --device cpu    # GPU를 다른 작업이 쓰고 있을 때
  uv run python -m src.eval.rank_trace --no-synonyms --out rank_trace_nosyn.json   # 동의어 사전 끄고
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


def rank(articles, groups):
    """정답 묶음 중 가장 앞선 순위 (조 단위, 1부터). 없으면 None."""
    flat = set().union(*groups)
    return next((i for i, a in enumerate(articles, start=1) if a in flat), None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    ap.add_argument("--questions", default=QUESTIONS)
    ap.add_argument("--no-synonyms", action="store_true", help="동의어 사전을 끄고 실행 (전후 비교용)")
    ap.add_argument("--out", default="rank_trace.json", help="eval/runs/ 아래 저장할 파일 이름")
    args = ap.parse_args()
    questions = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    cache = {}
    if os.path.exists(CACHE):
        cache = {r["q"]: r for r in map(json.loads, open(CACHE, encoding="utf-8"))}
    s = make_searcher(args.device)
    if args.no_synonyms:
        s.synonym_table = {}
    rows = []
    for i, q in enumerate(questions, 1):
        text, groups = q["q"], gold_groups(q)
        rewritten = cache.get(text, {}).get("rewritten")
        vec, key = s.vector_hits(text, None), s.bm25_hits(text, None)
        hint = " ".join(x for x in (rewritten, synonyms.expand(text, s.synonym_table),
                                    glossary.signal_terms(text, s.glossary)) if x)
        extra = ()
        if hint:
            extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), 0.7), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), 0.3))
        fused = s.fuse(vec, key, extra=extra)
        reranked = s.rerank(text, [dict(h) for h in fused[:RERANK_TOP]])
        row = {"q": text, "hint": hint,
               "벡터": rank(by_article(vec), groups), "키워드": rank(by_article(key), groups),
               "보조벡터": rank(by_article(extra[0][1]), groups) if extra else None,
               "합침": rank(by_article(fused), groups), "리랭크": rank(by_article(reranked), groups)}
        flat = set().union(*groups)
        row["top"] = [(article_id(h["chunk_id"]), round(h["rerank"], 4), h["chunk_id"] in flat or article_id(h["chunk_id"]) in flat,
                       s.rerank_text(h["chunk_id"])[:90].replace("\n", " ")) for h in reranked[:6]]
        gold_hits = [h for h in reranked if article_id(h["chunk_id"]) in flat]
        row["gold"] = [(article_id(h["chunk_id"]), round(h["rerank"], 4), s.rerank_text(h["chunk_id"])[:90].replace("\n", " "))
                       for h in gold_hits[:2]]
        rows.append(row)
        print(f"  {i}/{len(questions)}", flush=True)
    s.close()

    out = os.path.join(BASE_DIR, "eval", "runs", args.out)
    json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n = len(rows)
    print(f"\n질문 {n}개: 단계별로 정답이 10위 안 / 30위 안 / 못 찾음")
    for k in ["벡터", "키워드", "보조벡터", "합침", "리랭크"]:
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
