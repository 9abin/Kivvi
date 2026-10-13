"""검색 품질 측정. eval/questions.jsonl의 질문으로 검색 구성 요소를 따로 비교한다.

실행 (프로젝트 루트에서):
  uv run python -m src.eval.run                 # 네 가지 방식 전부
  uv run python -m src.eval.run --mode bm25     # 한 방식만
  uv run python -m src.eval.run --rerank-header off   # 리랭커 머리말 끄고 비교

방식 (모두 실제 검색 코드 src/search/search.py의 단계를 그대로 쓴다)
  vector      : 벡터 검색만
  bm25        : BM25 키워드 검색만
  rrf         : 벡터 + BM25를 가중 RRF로 합친 결과
  rrf_rerank  : RRF 결과 상위 30개를 리랭커로 다시 정렬한 결과
  search      : 실제 검색 전체 (정확 조회 + RRF + 리랭킹, 확장은 제외)

지표
  Hit@3, Hit@10: 정답 청크가 상위 3개 / 10개 안에 드는 질문의 비율
  MRR: 정답 순위의 역수 평균 (높을수록 좋음)
유형별(내용 / 문서명 / 항번호)과 세부유형별(단일조문 / 용어정의 / 조문간인용 등, 없으면 미지정)로도 나눠서 본다.

채점 단위: 조. 정답 파일은 항 단위 시절 ID('X_제8조_2항')로 적혀 있어서, 정답과 검색 결과를 모두 조 ID로 맞춘 뒤
같은 조가 여러 번 나오면 첫 번째만 센다. 그래서 항 단위 색인과 조 단위 색인을 같은 잣대로 비교할 수 있다.
(PG_DATABASE 환경 변수로 비교할 색인 DB를 고른다. 예: 항 단위 기준 색인은 regulations_para)

주의: 질문 세트(현재 277개, 90개 규정 전부 포함)로 가중치를 맞추면 이 세트에 과적합될 수 있다.
가중치를 조정할 때는 질문을 나눠 일부를 검증용으로 남긴다.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

from config import BASE_DIR
from src.chunk.articles import article_id
from src.eval.dataset import QUESTION_SUBTYPES
from src.search.search import RERANK_TOP, Searcher
from src.store.pgstore import PgStore

sys.stdout.reconfigure(encoding="utf-8")

QUESTIONS = os.path.join(BASE_DIR, "eval", "questions.jsonl")
K = 10
MODES = ["vector", "bm25", "rrf", "rrf_rerank", "search"]


def load_questions(path=QUESTIONS):
    """하나 또는 여러 질문 파일을 읽어 합친다. 블라인드 세트(v2+v3)처럼 여러 파일을 한 번에
    돌릴 때는 파일을 복사해서 합치지 않고 경로 목록을 그대로 넘긴다."""
    paths = [path] if isinstance(path, str) else path
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            rows.extend(json.loads(line) for line in f if line.strip())
    return rows


def check_gold(questions):
    """정답 청크 ID가 DB에 있고 색인 대상인지 확인한다. 없으면 측정이 의미 없으므로 중단한다."""
    store = PgStore()
    ids = sorted({g for q in questions for g in q["정답"]})
    with store.conn.cursor() as cur:  # 항 단위 색인이면 조 ID 정답은 그 조의 항 청크가 있으면 된다
        cur.execute("SELECT chunk_id FROM chunks")
        found = {article_id(cid) for (cid,) in cur.fetchall()}
    store.close()
    missing = [i for i in ids if article_id(i) not in found]
    if missing:
        raise SystemExit(f"정답 청크가 DB에 없음: {missing}")
    print(f"정답 청크 {len(ids)}개 확인 완료")


def by_article(hits):
    """검색 결과를 조 ID 목록으로 바꾼다. 항 단위 색인에서 같은 조의 항이 여러 번 나오면 첫 번째만 남긴다."""
    out, seen = [], set()
    for h in hits:
        a = article_id(h["chunk_id"])
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def rank_of(hits, gold):
    """정답 조들 중 가장 앞선 순위(1부터, 조 단위). 없으면 None."""
    gold = {article_id(g) for g in gold}
    for i, a in enumerate(by_article(hits), start=1):
        if a in gold:
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
        # 항 단위 색인은 같은 조가 여러 번 나와 조 단위로 줄면 10개가 안 될 수 있어서 넉넉히 받는다
        return searcher.search(text, k=RERANK_TOP, expand_siblings=False, expand_cites=False)
    raise ValueError(mode)


def concentration(hits):
    """상위 K개 중 가장 많이 나온 한 규정이 차지한 비율. 머리말로 같은 규정이 상위를 독점하는지 본다."""
    docs = [a.split("_", 1)[0] for a in by_article(hits)[:K]]
    if not docs:
        return 0.0
    return max(docs.count(d) for d in set(docs)) / len(docs)


def recall_at_k(hits, gold, k=K):
    """정답이 여러 개인 질문에서, 상위 k개 안에 정답 중 몇 퍼센트가 들어왔는지.
    정답이 하나뿐이면 Hit@k와 같은 값이라 의미가 없다. 정답이 둘 이상일 때만 쓴다."""
    gold = {article_id(g) for g in gold}
    found = set(by_article(hits)[:k]) & gold
    return len(found) / len(gold)


def run(modes, rerank_header=None, questions_path=QUESTIONS):
    questions = load_questions(questions_path)
    check_gold(questions)

    searcher = Searcher()
    if rerank_header is not None:
        searcher.rerank_header = rerank_header
    print(f"색인 DB: {os.environ.get('PG_DATABASE')} | 리랭커 머리말: {'켬' if searcher.rerank_header else '끔'}")
    results = {}
    concentrations = {}
    recalls = {}
    for mode in modes:
        ranks = []  # (유형, 질문, 순위 또는 None, 세부유형)
        content_concentration = []
        multi_gold_recall = []  # 정답이 2개 이상인 질문만
        for q in questions:
            hits = hits_for(searcher, mode, q["q"])
            gold = set(q["정답"])
            ranks.append((q["유형"], q["q"], rank_of(hits, gold), q.get("세부유형", "미지정")))
            if q["유형"] == "내용":
                content_concentration.append(concentration(hits))
            if len({article_id(g) for g in gold}) > 1:  # 조 단위로 맞춰도 정답이 둘 이상일 때만
                multi_gold_recall.append(recall_at_k(hits, gold))
        results[mode] = ranks
        concentrations[mode] = sum(content_concentration) / max(len(content_concentration), 1)
        recalls[mode] = (sum(multi_gold_recall) / len(multi_gold_recall), len(multi_gold_recall)) \
            if multi_gold_recall else (None, 0)
    searcher.close()

    print_report(results, len(questions))
    print(f"\n쏠림: 내용 질문 상위 {K}개 중 한 규정이 차지한 비율 평균 (낮을수록 여러 규정이 섞임)")
    for mode, value in concentrations.items():
        print(f"  {mode:<12}{value:.2f}")

    print(f"\nRecall@{K}: 정답이 2개 이상인 질문에서, 정답 중 상위 {K}개 안에 들어온 비율 평균")
    print("  (정답이 1개뿐인 질문은 Hit@k와 같은 값이라 뺀다)")
    for mode, (value, n) in recalls.items():
        if value is None:
            print(f"  {mode:<12}해당 질문 없음")
        else:
            print(f"  {mode:<12}{value:.2f}  (n={n})")


def summarize(ranks):
    n = len(ranks)
    if n == 0:
        return {"n": 0, "hit3": 0.0, "hit10": 0.0, "mrr": 0.0}
    found = [row[2] for row in ranks if row[2] is not None]
    hit3 = sum(1 for r in found if r <= 3) / n
    hit10 = sum(1 for r in found if r <= K) / n
    mrr = sum(1 / r for r in found) / n
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
        by_subtype = defaultdict(list)
        for row in ranks:
            by_subtype[row[3]].append(row)
        for t in [*QUESTION_SUBTYPES, "미지정"]:
            if t not in by_subtype:
                continue
            s = summarize(by_subtype[t])
            print(f"{mode:<12}{'└' + t:<10}{s['n']:>6}{s['hit3']:>9.2f}{s['hit10']:>9.2f}{s['mrr']:>8.2f}")
        print()

    print("놓친 질문 (상위 10위 안에 정답 없음)")
    for mode, ranks in results.items():
        misses = [(t, q) for t, q, r, _ in ranks if r is None]
        print(f"  [{mode}] {len(misses)}개")
        for t, q in misses:
            print(f"    - ({t}) {q}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=MODES, default=None)
    ap.add_argument("--rerank-header", choices=["on", "off"], default=None,
                    help="리랭커 입력 머리말 비교용. 생략하면 config.RERANK_HEADER를 따른다")
    ap.add_argument("--questions", nargs="+", default=[QUESTIONS],
                    help="질문 파일 경로 (여러 개 가능). 생략하면 eval/questions.jsonl "
                        "(예: --questions eval/questions_v2.jsonl eval/questions_v3.jsonl)")
    args = ap.parse_args()
    header = None if args.rerank_header is None else args.rerank_header == "on"
    run([args.mode] if args.mode else MODES, rerank_header=header, questions_path=args.questions)
