"""신호를 한꺼번에 쓰는 작은 학습 정렬이 지금의 단계 정렬보다 앞쪽 정답을 늘리는지 잰다 (시험용).
  uv run python scripts/exp_learned_order.py
특징: 경로 종류(있다 / 없다), 단계, 점수, 바뀐 말이 글자로 든다, 원래 순서, 수정 종류. 로지스틱 회귀.
평가: 한 묶음(개발 · 1차 · 2차 · 3차)을 빼고 나머지 셋으로 학습해 빠진 묶음에서 상위 5 · 10 · 20 정답 비율을 잰다.
"""
import re
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
import eval_collect_dev as ecd  # noqa: E402
from regcheck.collect import TIER_ORDER  # noqa: E402

N = lambda s: re.sub(r"\s+", "", s or "")
SETS = (("개발", ecd.K, "개발"), ("1차", ecd.K, "봉인"), ("2차", ROOT / "eval/impact_label_kit_r2/산출", "봉인"), ("3차", ROOT / "eval/impact_label_kit_r3/산출", "봉인"))
TOPS = (5, 10, 20)


def collect_rows():
    data = {}
    for tag, kit, sp in SETS:
        ecd.K = kit
        pos, _, ut, _ = ecd.build(sp)
        data[tag] = (pos, ut)
    return data


def main():
    data = collect_rows()
    paths = sorted({p.split(":")[0] for pos, _ in data.values() for r in pos for c in r["cands"] for p in c.paths})
    kinds = sorted({r["kind"] for pos, _ in data.values() for r in pos})

    def feats(r, ut):
        cq = r["cq"]
        anchors = [N(x) for x in (cq.removed + cq.old_values + cq.new_values) if len(N(x)) >= 2]
        X, ids = [], []
        for rank, c in enumerate(r["cands"]):
            ps = {p.split(":")[0] for p in c.paths}
            t = N(ut.get(c.id, ""))
            X.append([p in ps for p in paths] + [TIER_ORDER[c.tier] == k for k in range(3)] + [c.score, any(a in t for a in anchors), 1 / (1 + rank), len(ps)] + [r["kind"] == k for k in kinds])
            ids.append(c.id)
        return np.array(X, float), ids

    cache = {tag: [(r, *feats(r, ut)) for r in pos] for tag, (pos, ut) in data.items()}
    tot = {"지금 순서": {k: 0 for k in TOPS}, "학습 정렬": {k: 0 for k in TOPS}}
    n_truth = 0
    for hold in cache:
        Xtr = np.vstack([X for t, rows in cache.items() if t != hold for r, X, ids in rows])
        ytr = np.concatenate([[i in r["truth"] for i in ids] for t, rows in cache.items() if t != hold for r, X, ids in rows])
        m = LogisticRegression(max_iter=2000, C=1.0, class_weight="balanced").fit(Xtr, ytr)
        h = {k: 0 for k in TOPS}; b = {k: 0 for k in TOPS}; n = 0
        for r, X, ids in cache[hold]:
            n += len(r["truth"])
            order = [ids[j] for j in np.argsort(-m.decision_function(X), kind="stable")]
            for k in TOPS:
                h[k] += len(r["truth"] & set(order[:k])); b[k] += len(r["truth"] & set(ids[:k]))
        n_truth += n
        for k in TOPS:
            tot["학습 정렬"][k] += h[k]; tot["지금 순서"][k] += b[k]
        print(f"[{hold} 빼고 학습] 정답 {n}  지금 순서 " + " ".join(f"@{k} {b[k]}" for k in TOPS) + "  학습 정렬 " + " ".join(f"@{k} {h[k]}" for k in TOPS))
    print(f"\n합 정답 {n_truth}건")
    for name, d in tot.items():
        print(f"{name}: " + "  ".join(f"상위 {k} {d[k]}/{n_truth} ({d[k]/n_truth:.0%})" for k in TOPS))


if __name__ == "__main__":
    main()
