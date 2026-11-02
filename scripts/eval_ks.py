"""후보 상한 k를 키웠을 때의 재현율 (후보 목록은 그대로, k만 바꾼다). 사전 등록 지표(@20)를 대신하지 않는 보조 표다.
  uv run python scripts/eval_ks.py
RAG만 목록은 의미 검색이 주는 후보(질의당 40개 안팎)까지라 k가 그보다 크면 더 늘지 않는다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
import eval_collect_dev as ecd  # noqa: E402

KS = (10, 20, 30, 50, 100)
K1 = ecd.K
for tag, kit, sp in (("개발(독립 아님)", K1, "개발"), ("2차(고친 뒤, 독립 아님)", ROOT / "eval/impact_label_kit_r2/산출", "봉인"), ("3차(독립)", ROOT / "eval/impact_label_kit_r3/산출", "봉인")):
    ecd.K = kit
    pos, neg, _, _ = ecd.build(sp)
    tot = sum(len(r["truth"]) for r in pos)
    print(f"\n[{tag}] 영향 있는 문항 {len(pos)}, 정답 {tot}건. 후보 수 중앙값(합침) {sorted(len(r['cands']) for r in pos)[len(pos)//2]}")
    for name in ("RAG만", "합침"):
        row = []
        for k in KS:
            h = 0
            for r in pos:
                lst = [c for c in r["cands"] if "RAG" in c.paths] if name == "RAG만" else r["cands"]
                h += len(r["truth"] & {c.id for c in lst[:k]})
            row.append(f"@{k} {h}/{tot} ({h/tot:.0%})")
        print(f"  {name:6} " + " | ".join(row))
