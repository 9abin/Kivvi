"""정의 용어 영향(regcheck.terms) 평가: 정의마다 정의를 바꾸는 개정안을 만들어 카드 수(검토 부담)를 재고, 사람 판정용 표본을 뽑는다.

  uv run --no-sync python scripts/eval_terms.py [--sample 30] [--seed 1]
개정안: 정의 문장 끝의 「말한다」를 「말한다(시험)」로 바꾼다 — 정의 문장만 바뀌는 최소 개정 (뜻 변화는 사람이 상정).
재현율: 용어가 문자 그대로 쓰인 곳은 구조상 다 찾는다 (같은 규정 안). 그래서 여기서 재는 것은
  ① 카드 수 분포(담당자가 볼 양) ② 다른 규정 카드 수 ③ 표본 판정용 CSV — 「정의가 바뀌면 이 조의 뜻도 영향을 받나」를 사람이 표시
출력: eval/agent/term_sample.csv (원문 문장이 들어 있어 git 제외)
"""

import argparse
import collections
import csv
import json
import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.amend import AmendError, build_changeset  # noqa: E402
from regcheck.terms import definitions, impacts  # noqa: E402


def unit_of(a: dict, it: dict) -> str:
    p, i, s = it["path"]
    return a["label"] + (f"제{p}항" if p else "") + (f"제{i}호" if i else "")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    rels = [json.loads(l) for l in (ROOT / "out" / "relations" / "relations.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rows, stats, skipped = [], [], collections.Counter()
    for rid, reg in regs.items():
        for d in definitions(reg):
            art = next(x for x in reg["articles"] if x["id"] == d["article_id"])
            it = next(x for x in art["items"] if x["norm"] == d["text"])
            if "말한다" not in d["text"]:
                skipped["「말한다」 없음"] += 1
                continue
            try:
                cs, new = build_changeset(reg, f'{unit_of(art, it)} 중 "말한다"를 "말한다(시험)"로 한다.', "T")
            except AmendError:
                skipped["적용 못함"] += 1
                continue
            cards = [c for c in impacts(reg, new, regs, rels) if c["category"] == "review"]
            same = [c for c in cards if c["path"] == "정의 용어"]
            n_same = sum(len(c.get("articles") or [1]) for c in same)
            other_def = [c for c in cards if c["path"] == "정의 용어(다른 규정 정의)"]
            other_use = [c for c in cards if c["path"] == "정의 용어(다른 규정 사용)"]
            stats.append({"term": d["term"], "reg": reg["name"], "cards": len(cards), "same_articles": n_same,
                          "other_def": len(other_def), "other_use": len(other_use),
                          "other_use_unlinked": sum(c["priority"] == "낮음" for c in other_use)})
            for c in cards:
                rows.append({"용어": d["term"], "정의 규정": reg["name"], "경로": c["path"], "우선순위": c["priority"],
                             "대상 규정": c["reg_name"], "대상 조": c["article"], "대상 문장": c["context"][:300], "정의": d["text"][:300],
                             "판정(영향있음/없음/모름)": "", "메모": ""})
    n = len(stats)
    print(f"정의 {n}개로 시험 (건너뜀 {dict(skipped)})")
    print(f"정의 하나 바꿀 때 카드 수: 중앙값 {st.median(s['cards'] for s in stats)} · 평균 {st.mean(s['cards'] for s in stats):.1f} · 최대 {max(s['cards'] for s in stats)}")
    print(f"  같은 규정 안 영향 조 수: 중앙값 {st.median(s['same_articles'] for s in stats)} · 0곳인 정의 {sum(s['same_articles'] == 0 for s in stats)}")
    print(f"  다른 규정 정의 비교 카드가 있는 정의 {sum(s['other_def'] > 0 for s in stats)} · 다른 규정 사용 카드가 있는 정의 {sum(s['other_use'] > 0 for s in stats)}"
          f" (그중 인용 없는 규정 카드 {sum(s['other_use_unlinked'] for s in stats)}장)")
    top = sorted(stats, key=lambda s: -s["cards"])[:5]
    print("  카드가 가장 많은 정의:", [(s["reg"], s["term"], s["cards"]) for s in top])
    by_path = collections.Counter(r["경로"] for r in rows)
    print(f"카드 합계 {len(rows)} · 경로별 {dict(by_path)}")
    rng = random.Random(a.seed)
    sample = []
    for path, k in (("정의 용어", a.sample // 2), ("정의 용어(다른 규정 사용)", a.sample // 3), ("정의 용어(다른 규정 정의)", a.sample - a.sample // 2 - a.sample // 3)):
        pool = [r for r in rows if r["경로"] == path]
        sample += rng.sample(pool, min(k, len(pool)))
    out = ROOT / "eval" / "agent" / "term_sample.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(sample[0].keys()))
        w.writeheader()
        w.writerows(sample)
    print(f"판정 표본 {len(sample)}건 → {out}")


if __name__ == "__main__":
    main()
