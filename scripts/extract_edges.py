"""out/parsed/*.json → 인용 엣지 추출·해석.

  uv run python scripts/extract_edges.py          # 담당 21건을 출발점으로 (대상 해석은 90건 전체 기준)
  uv run python scripts/extract_edges.py --all
결과: out/edges/edges.jsonl, out/edges/_status.tsv, out/edges/laws.tsv
"""

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from parse_regs import assigned_names  # noqa: E402
from regcheck.citations import STATUS, Registry, extract_edges  # noqa: E402
from regcheck.normalize import compact  # noqa: E402

PARSED = ROOT / "out" / "parsed"
OUT = ROOT / "out" / "edges"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    regs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(PARSED.glob("[0-9]*.json"))]
    if len(regs) < 90:
        sys.exit("out/parsed에 90건이 필요합니다. 먼저 scripts/parse_regs.py --all 실행")
    registry = Registry(regs)
    names = None if args.all else set(assigned_names())
    edges = []
    for r in regs:
        if names and r["name"] not in names:
            continue
        for e in extract_edges(r, registry):
            d = e.to_dict()
            d["src_reg"] = r["reg_id"]
            d["src_name"] = r["name"]
            edges.append(d)
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "edges.jsonl").open("w", encoding="utf-8") as f:
        for d in edges:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    by = collections.Counter((d["src_kind"], d["target_type"], d["status"]) for d in edges)
    rows = ["출발\t대상유형\t상태\t설명\t건수"] + [
        f"{k}\t{t}\t{s}\t{STATUS.get(s, '')}\t{n}" for (k, t, s), n in sorted(by.items())
    ]
    (OUT / "_status.tsv").write_text("\n".join(rows), encoding="utf-8")
    # 법령 목록 (한석희 법률 DB 대상 범위용): 띄어쓰기만 다른 표기는 묶고, 가장 많이 쓴 표기를 대표로
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for d in edges:
        if d["status"] == "law_pending":
            groups[compact(d["target_doc"])].append(d)
    law_rows = []
    for key, ds in groups.items():
        spellings = collections.Counter(d["target_doc"] for d in ds)
        name = spellings.most_common(1)[0][0]
        law_rows.append((name, len(ds), len({d["src_reg"] for d in ds}), " / ".join(s for s in spellings if s != name)))
    law_rows.sort(key=lambda r: (r[0].startswith("(약칭"), -r[1]))
    laws = law_rows
    (OUT / "laws.tsv").write_text(
        "법령명\t인용 수\t인용 규정 수\t다른 표기\n" + "\n".join("\t".join(map(str, r)) for r in law_rows), encoding="utf-8"
    )
    print("\n".join(rows))
    print(f"\n엣지 {len(edges)}개, 법령명 {len(laws)}종 → {OUT}")


if __name__ == "__main__":
    main()
