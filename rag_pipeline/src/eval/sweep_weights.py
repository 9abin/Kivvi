"""RRF 가중치(벡터:키워드) 비교. eval/questions.jsonl 277개로 어느 비율이 나은지 본다.

실행 (rag_pipeline 폴더에서):
  uv run python -m src.eval.sweep_weights

방법
  1. 질문을 유형(내용/문서명/항번호) 비율을 유지하면서 학습용 80% / 검증용 20%로 나눈다
     (고정 시드, 재현 가능). 검증용은 가중치를 고르는 동안 전혀 보지 않는다.
  2. 학습용 질문마다 벡터 상위 50개·BM25 상위 50개를 한 번만 계산해 캐시한다.
     가중치는 "합치는 비율"만 바꾸므로, 후보마다 벡터·BM25를 다시 돌릴 필요가 없다.
  3. 후보 가중치마다 캐시된 결과를 가중 RRF로만 다시 합쳐서(리랭킹 없이) Hit@3/10/MRR과
     쏠림(내용 질문 상위 10개 중 한 규정이 차지한 비율)을 학습용 세트로 측정한다.
  4. Hit@10 → MRR 순으로 가장 좋은 후보를 고른다.
  5. 고른 가중치와 현재 기본값(config.HYBRID_VECTOR_WEIGHT/KEYWORD_WEIGHT)을 검증용 세트로
     "실제 검색 전체"(정확 조회 + RRF + 리랭킹)까지 포함해서 딱 한 번만 비교한다.

주의: 3번은 리랭킹을 뺀 RRF만의 비교라서 빠르다. 최종 판단은 반드시 5번(검증용 + 리랭킹 포함)으로 한다.
"""
import random
import sys
from collections import defaultdict

from config import HYBRID_KEYWORD_WEIGHT, HYBRID_VECTOR_WEIGHT
from src.eval.run import K, check_gold, concentration, load_questions, rank_of, summarize
from src.search.search import Searcher

sys.stdout.reconfigure(encoding="utf-8")

SEED = 42
VAL_RATIO = 0.2
# 후보 (벡터 가중치, 키워드 가중치). 양 끝(단독)과 현재값(0.7, 0.3) 주변을 촘촘히 본다.
CANDIDATES = [
    (1.0, 0.0), (0.9, 0.1), (0.8, 0.2), (0.7, 0.3),
    (0.6, 0.4), (0.5, 0.5), (0.3, 0.7), (0.0, 1.0),
]


def stratified_split(questions, val_ratio=VAL_RATIO, seed=SEED):
    """유형 비율을 유지하며 학습용/검증용으로 나눈다. 시드가 고정되어 항상 같게 나뉜다."""
    by_type = defaultdict(list)
    for i, q in enumerate(questions):
        by_type[q["유형"]].append(i)
    rng = random.Random(seed)
    val_idx = set()
    for idxs in by_type.values():
        idxs = idxs[:]
        rng.shuffle(idxs)
        n_val = round(len(idxs) * val_ratio)
        val_idx.update(idxs[:n_val])
    train = [q for i, q in enumerate(questions) if i not in val_idx]
    val = [q for i, q in enumerate(questions) if i in val_idx]
    return train, val


def build_cache(searcher, questions):
    """질문마다 벡터·BM25 상위 50개를 한 번만 계산해 둔다."""
    cache = []
    for q in questions:
        vec = searcher.vector_hits(q["q"], None)
        key = searcher.bm25_hits(q["q"], None)
        cache.append((q, vec, key))
    return cache


def evaluate_weights(searcher, cache, vector_weight, keyword_weight):
    """캐시된 벡터·BM25 결과를 주어진 가중치로만 다시 합쳐서 채점한다 (리랭킹 없음)."""
    ranks = []
    content_conc = []
    for q, vec, key in cache:
        fused = searcher.fuse(vec, key, vector_weight=vector_weight, keyword_weight=keyword_weight)
        ranks.append((q["유형"], q["q"], rank_of(fused, set(q["정답"]))))
        if q["유형"] == "내용":
            content_conc.append(concentration(fused))
    return ranks, sum(content_conc) / max(len(content_conc), 1)


def print_sweep_table(rows):
    print(f"{'벡터:키워드':<12}{'전체 Hit@3':>11}{'Hit@10':>9}{'MRR':>8}{'쏠림':>8}   유형별 Hit@10 (내용/문서명/항번호)")
    for vw, kw, overall, by_type, conc in rows:
        by_type_str = " / ".join(f"{summarize(by_type[t])['hit10']:.2f}" for t in ["내용", "문서명", "항번호"])
        print(f"{vw:.1f}:{kw:.1f}      {overall['hit3']:>10.2f}{overall['hit10']:>9.2f}"
              f"{overall['mrr']:>8.2f}{conc:>8.2f}   {by_type_str}")


def sweep(searcher, train):
    """후보 가중치를 학습용 세트로만 비교한다. 리랭킹은 빼서 빠르게 돈다."""
    cache = build_cache(searcher, train)
    rows = []
    for vw, kw in CANDIDATES:
        ranks, conc = evaluate_weights(searcher, cache, vw, kw)
        overall = summarize(ranks)
        by_type = defaultdict(list)
        for row in ranks:
            by_type[row[0]].append(row)
        rows.append((vw, kw, overall, by_type, conc))
    return rows


def pick_best(rows):
    """Hit@10 → MRR 순으로 가장 좋은 후보를 고른다."""
    ranked = sorted(rows, key=lambda r: (r[2]["hit10"], r[2]["mrr"]), reverse=True)
    return ranked[0]


def validate(searcher, questions, vector_weight, keyword_weight, label):
    """검증용 세트로, 리랭킹까지 포함한 실제 search()로 딱 한 번만 확인한다."""
    ranks = []
    content_conc = []
    for q in questions:
        hits = searcher.search(q["q"], k=K, expand_siblings=False, expand_cites=False,
                               vector_weight=vector_weight, keyword_weight=keyword_weight)
        ranks.append((q["유형"], q["q"], rank_of(hits, set(q["정답"]))))
        if q["유형"] == "내용":
            content_conc.append(concentration(hits))
    overall = summarize(ranks)
    by_type = defaultdict(list)
    for row in ranks:
        by_type[row[0]].append(row)
    conc = sum(content_conc) / max(len(content_conc), 1)
    print(f"\n[검증용, 실제 검색 전체(정확 조회+RRF+리랭킹), {label} {vector_weight:.1f}:{keyword_weight:.1f}]")
    print(f"  전체 Hit@3 {overall['hit3']:.2f}  Hit@10 {overall['hit10']:.2f}  MRR {overall['mrr']:.2f}  쏠림 {conc:.2f}")
    for t in ["내용", "문서명", "항번호"]:
        s = summarize(by_type.get(t, []))
        print(f"  {t:<6} Hit@3 {s['hit3']:.2f}  Hit@10 {s['hit10']:.2f}  MRR {s['mrr']:.2f}  (n={s['n']})")
    return overall


def main():
    questions = load_questions()
    check_gold(questions)
    train, val = stratified_split(questions)
    print(f"학습용 {len(train)}개 / 검증용 {len(val)}개 (시드 {SEED}, 검증용은 가중치를 고르는 동안 보지 않음)\n")

    searcher = Searcher()

    print("=== 1단계: 학습용 세트로 가중치 후보 비교 (RRF만, 리랭킹 없음) ===")
    rows = sweep(searcher, train)
    print_sweep_table(rows)

    best_vw, best_kw, best_overall, _, _ = pick_best(rows)
    print(f"\n학습용 기준 최고: 벡터 {best_vw:.1f} : 키워드 {best_kw:.1f} "
         f"(Hit@10 {best_overall['hit10']:.2f}, MRR {best_overall['mrr']:.2f})")

    print("\n=== 2단계: 검증용 세트로 딱 한 번 확인 (실제 검색 전체) ===")
    validate(searcher, val, HYBRID_VECTOR_WEIGHT, HYBRID_KEYWORD_WEIGHT, "현재 기본값")
    if (best_vw, best_kw) != (HYBRID_VECTOR_WEIGHT, HYBRID_KEYWORD_WEIGHT):
        validate(searcher, val, best_vw, best_kw, "학습용 최고 후보")
    else:
        print("\n학습용에서 고른 값이 현재 기본값과 같습니다. 바꿀 근거가 없습니다.")
    searcher.close()


if __name__ == "__main__":
    main()
