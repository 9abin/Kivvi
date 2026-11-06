"""후보 정렬에 「바뀐 말이 글자로 든다」 신호를 넣으면 앞쪽 정답이 늘어나는지 4개 평가 묶음에서 잰다 (개발에서 정한 규칙이 아니라 시험용 사후 확인).
  uv run python scripts/exp_anchor_order.py
V0 지금 순서 / V1 글자 겹침을 단계보다 앞에 / V2 단계 안에서 글자 겹침을 앞에
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
import eval_collect_dev as ecd  # noqa: E402
from regcheck.collect import TIER_ORDER  # noqa: E402

N = lambda s: re.sub(r"\s+", "", s or "")
SETS = (("개발", ecd.K, "개발"), ("1차봉인", ecd.K, "봉인"), ("2차", ROOT / "eval/impact_label_kit_r2/산출", "봉인"), ("3차", ROOT / "eval/impact_label_kit_r3/산출", "봉인"))
TOPS = (5, 10, 20)


def overlap(c, anchors, ut):
    t = N(ut.get(c.id, ""))
    return any(a in t for a in anchors)


def main():
    tot = {v: {k: 0 for k in TOPS} for v in ("V0", "V1", "V2")}
    n_truth = 0
    rate = {"겹침": [0, 0], "겹침없음": [0, 0]}
    for tag, kit, sp in SETS:
        ecd.K = kit
        pos, _, ut, _ = ecd.build(sp)
        for r in pos:
            cq = r["cq"]
            anchors = [N(x) for x in (cq.removed + cq.old_values + cq.new_values) if len(N(x)) >= 2]
            c0 = list(r["cands"])
            ov = {c.id: overlap(c, anchors, ut) for c in c0}
            orders = {"V0": c0,
                      "V1": sorted(c0, key=lambda c: (not ov[c.id], TIER_ORDER[c.tier], -c.score)),
                      "V2": sorted(c0, key=lambda c: (TIER_ORDER[c.tier], not ov[c.id], -c.score))}
            n_truth += len(r["truth"])
            for v, lst in orders.items():
                for k in TOPS:
                    tot[v][k] += len(r["truth"] & {c.id for c in lst[:k]})
            for c in c0:
                key = "겹침" if ov[c.id] else "겹침없음"
                rate[key][0] += 1
                rate[key][1] += c.id in r["truth"]
    print(f"정답 {n_truth}건 (개발 · 1차 · 2차 · 3차 합)")
    for v, d in tot.items():
        print(v, "  ".join(f"상위 {k}: {d[k]}/{n_truth} ({d[k]/n_truth:.0%})" for k in TOPS))
    for k, (n, h) in rate.items():
        print(f"후보 중 {k}: {n}개, 정답 {h}개 ({h/max(n,1):.1%})")


if __name__ == "__main__":
    main()
