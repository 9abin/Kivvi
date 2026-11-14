"""의미 이웃 표 생성 (regcheck.neighbors): 조문마다 다른 규정에서 같은 주제로 보이는 조문.

  uv run --no-sync python scripts/build_neighbors.py [--min 0.78] [--per 3] [--sample 30]
입력: out/vector/BAAI__bge-m3 (scripts/build_vectors.py). 출력: out/relations/neighbors.json
--sample N: 점수 구간(0.78~0.82, 0.82~0.88, 0.88~)마다 고르게 뽑은 판정용 표본 → eval/agent/neighbor_sample.csv (원문이 들어 있어 git 제외)
"""

import argparse
import csv
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.neighbors import MAX_PER, MIN_SCORE, OUT, build, save  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=float, default=MIN_SCORE)
    ap.add_argument("--per", type=int, default=MAX_PER)
    ap.add_argument("--sample", type=int, default=0)
    a = ap.parse_args()
    nb = build(min_score=a.min, max_per=a.per)
    save(nb)
    rels = [json.loads(l) for l in (ROOT / "out" / "relations" / "relations.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    linked = {(r["src_article"], str(r["target"])) for r in rels} | {(str(r["target"]), r["src_article"]) for r in rels}
    pairs = [(a_, x["article_id"]) for a_, v in nb.items() for x in v]
    print(f"이웃이 있는 조 {len(nb)}개 · 이웃 쌍 {len(pairs)}개 · 그중 인용으로 이어진 쌍 {sum(p in linked for p in pairs)}개 → {OUT}")

    if a.sample:
        regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
        arts = {x["id"]: (r, x) for r in regs.values() for x in r["articles"]}
        seen, rows, rng = set(), [], random.Random(1)
        bands = [(0.78, 0.82), (0.82, 0.88), (0.88, 1.01)]
        for lo, hi in bands:
            pool = [(a_, x["article_id"], x["score"]) for a_, v in nb.items() for x in v if lo <= x["score"] < hi]
            rng.shuffle(pool)
            k = a.sample // len(bands) + (1 if (lo, hi) == bands[-1] and a.sample % len(bands) else 0)
            for a_, b, sc in pool:
                if len([r for r in rows if r["점수 구간"] == f"{lo}~{hi}"]) >= k:
                    break
                if frozenset((a_, b)) in seen or a_ not in arts or b not in arts:
                    continue
                seen.add(frozenset((a_, b)))
                (ra, xa), (rb, xb) = arts[a_], arts[b]
                rows.append({"점수": sc, "점수 구간": f"{lo}~{hi}", "인용으로 이어짐": (a_, b) in linked,
                             "조문 A": f"{ra['name']} {xa['label']}({xa['title']})", "A 본문": xa["text"][:400],
                             "조문 B": f"{rb['name']} {xb['label']}({xb['title']})", "B 본문": xb["text"][:400],
                             "판정(한쪽을 고치면 다른 쪽도 봐야 함 / 같은 주제지만 무관 / 다른 주제)": "", "메모": ""})
        path = ROOT / "eval" / "agent" / "neighbor_sample.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"판정 표본 {len(rows)}건 → {path}")


if __name__ == "__main__":
    main()
