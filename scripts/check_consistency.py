"""규정 사이 일관성 점검: 기관·호칭 사전 · 기한 표 · 이동 표지.

  uv run python scripts/check_consistency.py
입력: out/parsed/, out/relations/relations.jsonl, data/manual/vocab.json
출력: out/consistency/ (findings.jsonl, deadlines.tsv, usage.tsv, definitions.json, moves.tsv)
판정이 아니라 담당자 확인 후보다.
"""

import collections
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck import consistency, moves  # noqa: E402

OUT = ROOT / "out" / "consistency"


def tsv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    rels = [json.loads(l) for l in (ROOT / "out" / "relations" / "relations.jsonl").read_text(encoding="utf-8").splitlines()]
    vocab = json.loads((ROOT / "data" / "manual" / "vocab.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)

    rows = consistency.deadlines(regs)
    mv = moves.parse_moves(regs)
    findings = consistency.vocab_findings(regs, vocab) + consistency.deadline_findings(rows) + moves.stale_by_moves(regs, mv, rels) + moves.move_conflicts(mv) + consistency.structure_findings(regs, rels)
    diffs = consistency.definition_diffs(regs)

    (OUT / "findings.jsonl").write_text("\n".join(json.dumps(f, ensure_ascii=False) for f in findings), encoding="utf-8")
    tsv(OUT / "deadlines.tsv", rows)
    tsv(OUT / "usage.tsv", consistency.usage_matrix(regs, vocab))
    tsv(OUT / "moves.tsv", mv)
    (OUT / "definitions.json").write_text(json.dumps(diffs, ensure_ascii=False, indent=1), encoding="utf-8")

    print("기한 표", len(rows), "줄 | 이동 표지", len(mv), "줄 | 정의가 둘 이상 규정에 있는 용어", len(diffs), "(문구 다름", sum(not d["same_text"] for d in diffs), ")")
    for k, v in sorted(collections.Counter((f["rule"], f["priority"]) for f in findings).items()):
        print(" ", k, v)
    print("결과 →", OUT)


if __name__ == "__main__":
    main()
