"""고재현 모드(다홉 확장) 효과: 개정문이 있는 평가에서 확장 전후의 recall(전체 후보 · 기본 화면)과 후보 수를 잰다.
  uv run python scripts/eval_expand.py
평가 4묶음(개발 · 1~3차, AI 라벨)과 새 봉인 20문항. 가중치는 다른 평가의 경로별 정밀도(kg.EXPAND_WEIGHTS)이며 여기서 맞추지 않았다.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
import eval_collect_dev as ecd  # noqa: E402
from regcheck import collect as C  # noqa: E402
from regcheck.review_list import split_view  # noqa: E402

SETS = (("개발", ecd.K, "개발"), ("1차봉인", ecd.K, "봉인"), ("2차", ROOT / "eval/impact_label_kit_r2/산출", "봉인"), ("3차", ROOT / "eval/impact_label_kit_r3/산출", "봉인"))


def main():
    out = {}
    for hops in (0, 2, 3):
        C.DEFAULT_EXPAND = hops
        tot = a = d = na = nd = items = 0
        for tag, kit, sp in SETS:
            ecd.K = kit
            pos, _, _, _ = ecd.build(sp)
            for r in pos:
                v = split_view(r["cands"])
                tot += len(r["truth"]); a += len(r["truth"] & {c.id for c in v["all"]}); d += len(r["truth"] & {c.id for c in v["default"]})
                na += len(v["all"]); nd += len(v["default"]); items += 1
        out[hops] = (tot, a, d, na / items, nd / items)
        print(f"홉 {hops or '끔'}: 정답 {tot} | 전체 후보에 오른 정답 {a} ({a/tot:.1%}) | 기본 화면 {d} ({d/tot:.1%}) | 문항당 전체 후보 {na/items:.0f} · 기본 화면 {nd/items:.0f}", flush=True)
    return out


if __name__ == "__main__":
    main()
