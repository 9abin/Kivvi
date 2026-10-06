"""검색 품질 측정. eval/questions.jsonl의 질문으로 검색 구성 요소를 따로 비교한다.

실행 (rag_pipeline 폴더에서):
  uv run python -m src.eval.run                 # 네 가지 방식 전부
  uv run python -m src.eval.run --mode bm25     # 한 방식만

방식 (모두 실제 검색 코드 src/search/search.py의 단계를 그대로 쓴다)
  vector      : 벡터 검색만
  bm25        : BM25 키워드 검색만
  rrf         : 벡터 + BM25를 가중 RRF로 합친 결과
  rrf_rerank  : RRF 결과 상위 30개를 리랭커로 다시 정렬한 결과
  search      : 실제 검색 전체 (정확 조회 + RRF + 리랭킹, 확장은 제외)

지표
  Hit@3, Hit@10: 정답 청크가 상위 3개 / 10개 안에 드는 질문의 비율
  MRR: 정답 순위의 역수 평균 (높을수록 좋음)
유형별(내용 / 문서명 / 항번호)로도 나눠서 본다.

주의: 질문 세트가 31개뿐이라 이 결과로 가중치를 맞추면 과적합이다. 골든셋 확장 후에 판단한다.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

from config import BASE_DIR
from src.search.search import RERANK_TOP, Searcher
from src.store.pgstore import PgStore

sys.stdout.reconfigure(encoding="utf-8")

QUESTIONS = os.path.join(BASE_DIR, "eval", "questions.jsonl")
K = 10
MODES = ["vector", "bm25", "rrf", "rrf_rerank", "search"]


def load_questions():
    with open(QUESTIONS, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def check_gold(questions):
    """정답 청크 ID가 DB에 있고 색인 대상인지 확인한다. 없으면 측정이 의미 없으므로 중단한다."""
    store = PgStore()
    ids = sorted({g for q in questions for g in q["정답"]})
    found = {r["chunk_id"] for r in store.get(ids)}
    store.close()
    missing = [i for i in ids if i not in found]
    if missing:
        raise SystemExit(f"정답 청크가 DB에 없음: {missing}")
    print(f"정답 청크 {len(ids)}개 확인 완료")


def rank_of(hits, gold):
    """정답 청크들 중 가장 앞선 순위(1부터). 없으면 None."""
    for i, h in enumerate(hits, start=1):
        if h["chunk_id"] in gold:
            return i
    return None


def hits_for(searcher, mode, text):
    """방식별 결과 목록. 모든 방식이 같은 단계 함수를 쓴다."""
    if mode == "vector":
        return searcher.vector_hits(text, None)
    if mode == "bm25":
        return searcher.bm25_hits(text, None)
    fused = searcher.fuse(searcher.vector_hits(text, None), searcher.bm25_hits(text, None))
    if mode == "rrf":
        return fused
    if mode == "rrf_rerank":
        return searcher.rerank(text, fused[:RERANK_TOP])
    if mode == "search":
        return searcher.search(text, k=K, expand_siblings=False, expand_cites=False)
    raise ValueError(mode)


def run(modes):
    questions = load_questions()
    check_gold(questions)

    searcher = Searcher()
    results = {}
    for mode in modes:
        ranks = []  # (유형, 질문, 순위 또는 None)
        for q in questions:
            hits = hits_for(searcher, mode, q["q"])
            ranks.append((q["유형"], q["q"], rank_of(hits, set(q["정답"]))))
        results[mode] = ranks
    searcher.close()

    print_report(results, len(questions))


def summarize(ranks):
    n = len(ranks)
    if n == 0:
        return {"n": 0, "hit3": 0.0, "hit10": 0.0, "mrr": 0.0}
    hit3 = sum(1 for _, _, r in ranks if r is not None and r <= 3) / n
    hit10 = sum(1 for _, _, r in ranks if r is not None and r <= K) / n
    mrr = sum((1 / r) for _, _, r in ranks if r is not None) / n
    return {"n": n, "hit3": hit3, "hit10": hit10, "mrr": mrr}


def print_report(results, total):
    print(f"\n질문 {total}개, 상위 {K}개까지 확인\n")
    print(f"{'방식':<12}{'유형':<10}{'질문수':>6}{'Hit@3':>9}{'Hit@10':>9}{'MRR':>8}")
    for mode, ranks in results.items():
        by_type = defaultdict(list)
        for row in ranks:
            by_type[row[0]].append(row)
        for t in ["내용", "문서명", "항번호"]:
            s = summarize(by_type.get(t, []))
            print(f"{mode:<12}{t:<10}{s['n']:>6}{s['hit3']:>9.2f}{s['hit10']:>9.2f}{s['mrr']:>8.2f}")
        s = summarize(ranks)
        print(f"{mode:<12}{'전체':<10}{s['n']:>6}{s['hit3']:>9.2f}{s['hit10']:>9.2f}{s['mrr']:>8.2f}")
        print()

    print("놓친 질문 (상위 10위 안에 정답 없음)")
    for mode, ranks in results.items():
        misses = [(t, q) for t, q, r in ranks if r is None]
        print(f"  [{mode}] {len(misses)}개")
        for t, q in misses:
            print(f"    - ({t}) {q}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=MODES, default=None)
    args = ap.parse_args()
    run([args.mode] if args.mode else MODES)
