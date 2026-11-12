"""표준 순위 지표 + 라벨 일치도 (리포트 94). 판정 모델이 필요 없는 것만.

  python eval/relabel_v2/metrics.py rank <bm|val> <실행 결과.jsonl> [...]   # 순위 지표 (실행 결과: rule · other 목록)
  python eval/relabel_v2/metrics.py kappa <bm|val> <검색 후보 pool.json>    # B1 · B2 Cohen's kappa

순위 지표 (정답 = gold.jsonl의 id, 이진 관련도)
- 첫 화면 재현율: 첫 화면(합친 순서로 10개 + 10위 밖의 확정급, impact_pipeline.screen) 안의 정답 / 전체 정답. 나머지는 「더 보기」
- 전체 재현율: 첫 화면 + 더 보기(보인 목록 전부) 안의 정답 / 전체 정답
- Recall@k: 상위 k 안의 정답 수 / 전체 정답 수 (정답 단위로 모아 계산, micro)
- Hit@k (Success@k): 상위 k에 정답이 하나라도 있는 문항 비율 (정답 있는 문항만)
- MRR: 첫 정답 순위의 역수 평균 (정답 있는 문항만, 목록에 없으면 0)
- MAP: 문항별 Average Precision(정답이 나올 때마다의 정밀도 합 / 그 문항 정답 수)의 평균
- nDCG@k: DCG = Σ rel_i / log2(i+1), 이진 관련도, 이상적 순서로 나눔 (Järvelin & Kekäläinen 2002)
- 95% 구간: 문항 단위 부트스트랩 5,000번

Cohen's kappa (B1 · B2)
- 두 라벨러가 「살펴볼 조인가」를 같은 단위 집합에서 판정했다고 보고 계산한다. 그 집합 = 둘 중 하나가 낸 조 ∪ 검색 후보(pool).
  둘 다 안 낸 조가 있어야 kappa가 정의되므로 이렇게 둔다(정보 검색의 pooling과 같은 방식). 집합을 넓히면 kappa가 올라가는 점에 주의.
"""
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.stdout.reconfigure(encoding="utf-8")


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def gold(d):
    return {f"{d}:{r['id']}": {e["id"] for e in r["영향"] if e.get("id")} for r in jl(ROOT / f"eval/relabel_v2/{d}/gold.jsonl")}


def per_q(L, g, ks=(5, 10), first=None):
    """문항 하나의 값들. g가 비면 정답 단위 지표에만 0으로 들어간다. first = 첫 화면 id 집합(없으면 상위 10)."""
    hits = [1 if x in g else 0 for x in L]
    out = {f"r@{k}": sum(hits[:k]) for k in ks}
    out["first"] = len(g & set(L[:10] if first is None else first))
    out["all"] = sum(hits)
    out["n"] = len(g)
    if not g:
        return out
    out["hit@5"] = 1 if any(hits[:5]) else 0
    first = next((i for i, h in enumerate(hits) if h), None)
    out["rr"] = 1 / (first + 1) if first is not None else 0.0
    ap, c = 0.0, 0
    for i, h in enumerate(hits):
        if h:
            c += 1
            ap += c / (i + 1)
    out["ap"] = ap / len(g)
    dcg = sum(h / math.log2(i + 2) for i, h in enumerate(hits[:10]))
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(g), 10)))
    out["ndcg@10"] = dcg / idcg
    return out


def agg(rows):
    T = sum(r["n"] for r in rows) or 1
    w = [r for r in rows if r["n"]]
    m = lambda k: sum(r[k] for r in w) / max(len(w), 1)  # noqa: E731
    return {"첫 화면 재현율": sum(r["first"] for r in rows) / T, "전체 재현율": sum(r["all"] for r in rows) / T,
            "Recall@5": sum(r["r@5"] for r in rows) / T, "Recall@10": sum(r["r@10"] for r in rows) / T,
            "Hit@5": m("hit@5"), "MRR": m("rr"), "MAP": m("ap"), "nDCG@10": m("ndcg@10")}


def first_screen(rule, other, n=10):
    """impact_pipeline.screen과 같은 규칙(261112): 합친 순서로 n개 + n 밖의 확정급. 나머지는 「더 보기」."""
    ordered = rule + other
    return [s["id"] for s in ordered[:n]] + [s["id"] for s in ordered[n:] if s.get("tier") == "확정급"]


def rank(d, files):
    G = gold(d)
    print(f"## {d} 순위 지표 (정답 {sum(map(len, G.values()))}, 정답 있는 문항 {sum(1 for v in G.values() if v)})")
    names = ["첫 화면 재현율", "전체 재현율", "Recall@5", "Recall@10", "Hit@5", "MRR", "MAP", "nDCG@10"]
    print("| 실행 | 문항당 (첫 화면 / 전체) | " + " | ".join(names) + " |\n|---|---|" + "---|" * len(names))
    for f in files:
        R = {r["q"]: r for r in jl(f)}
        L = {q: [s["id"] for s in r["rule"]] + [s["id"] for s in r["other"]] for q, r in R.items()}
        F = {q: first_screen(r["rule"], r["other"]) for q, r in R.items()}
        rows = [per_q(L[q], G[q], first=F[q]) for q in sorted(R)]
        val = agg(rows)
        random.seed(0)
        bs = [agg([random.choice(rows) for _ in rows]) for _ in range(5000)]
        cells = []
        for k in names:
            xs = sorted(b[k] for b in bs)
            cells.append(f"{val[k]:.3f} ({xs[125]:.3f}~{xs[4874]:.3f})")
        print(f"| {Path(f).name} | {sum(map(len, F.values()))/len(F):.1f} / {sum(map(len, L.values()))/len(L):.1f} | " + " | ".join(cells) + " |")


def kappa(d, pool_file):
    pool = {p["q"]: {c["id"] for c in p["cands"]} for p in json.loads(Path(pool_file).read_text(encoding="utf-8")) if p["d"] == d}
    sys.path.insert(0, str(Path(__file__).parent))
    from resolve_ids import resolve, units
    lab, by_reg = units(ROOT)
    def ids(f):
        out = {}
        for r in jl(ROOT / f"eval/relabel_v2/{d}/{f}"):
            out[f"{d}:{r['id']}"] = {resolve(e, lab, by_reg)[0] for e in r["영향"]} - {None}
        return out
    A, B = ids("labels_B1.jsonl"), ids("labels_B2.jsonl")
    a = b = c = dd = 0
    for q in A:
        U = A[q] | B[q] | pool.get(q, set())
        for x in U:
            i, j = x in A[q], x in B[q]
            a += i and j; b += i and not j; c += (not i) and j; dd += (not i) and (not j)
    n = a + b + c + dd
    po = (a + dd) / n
    pe = ((a + b) / n) * ((a + c) / n) + ((c + dd) / n) * ((b + dd) / n)
    k = (po - pe) / (1 - pe)
    print(f"## {d} Cohen's kappa (B1 · B2): {k:.3f}  (둘 다 낸 {a}, B1만 {b}, B2만 {c}, 둘 다 안 냄 {dd}; 판정 집합 = 낸 조 ∪ 검색 후보)")


if __name__ == "__main__":
    if sys.argv[1] == "rank":
        rank(sys.argv[2], sys.argv[3:])
    elif sys.argv[1] == "kappa":
        kappa(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
