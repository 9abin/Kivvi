"""기본 화면(review_list.split_view)의 크기와 재현율을 4개 평가 묶음에서 잰다. 소음 경로 목록은 4묶음 합으로 정했으므로 시험용으로 빼지 않은 사후 확인이다.
  uv run python scripts/eval_default_view.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
import eval_collect_dev as ecd  # noqa: E402
from regcheck.review_list import split_view  # noqa: E402

K1 = ecd.K
for tag, kit, sp in (("개발", K1, "개발"), ("1차봉인", K1, "봉인"), ("2차", ROOT / "eval/impact_label_kit_r2/산출", "봉인"), ("3차", ROOT / "eval/impact_label_kit_r3/산출", "봉인")):
    ecd.K = kit
    pos, neg, _, _ = ecd.build(sp)
    tot = sum(len(r["truth"]) for r in pos)
    d = a = 0; dn = an = 0
    for r in pos:
        v = split_view(r["cands"])
        d += len(r["truth"] & {c.id for c in v["default"]}); a += len(r["truth"] & {c.id for c in v["all"]})
        dn += len(v["default"]); an += len(v["all"])
    sizes = sorted(len(split_view(r["cands"])["default"]) for r in pos)
    negs = [len(split_view(r["cands"])["default"]) for r in neg]
    print(f"[{tag}] 정답 {tot} | 기본 화면 {d}/{tot} ({d/tot:.0%}) vs 전체 {a}/{tot} ({a/tot:.0%}) | 후보 수 문항당 기본 중앙값 {sizes[len(sizes)//2]} (평균 {dn/len(pos):.0f}) vs 전체 평균 {an/len(pos):.0f} | 영향 없음 문항 기본 화면 후보 {negs}")
