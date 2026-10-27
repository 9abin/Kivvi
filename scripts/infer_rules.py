"""규칙 추론: 규칙 레지스트리(data/manual/rules.json)로 「의심」 관계를 만들어 저장.

  uv run python scripts/infer_rules.py                    # 엣지 생성 → out/rules/inferred.jsonl
  uv run python scripts/infer_rules.py --ai [--limit 20]  # luna가 엣지마다 한 줄 설명·의도된 차이 가능성을 덧붙임 → out/rules/ai_notes.json
  uv run python scripts/infer_rules.py --only DL-01,DEF-01
입력: out/parsed/, out/relations/relations.jsonl, data/manual/rules.json
출력: out/rules/inferred.jsonl (관련 조항 표와 같은 칸 + rule_id · via · priority · target_context), out/rules/summary.json
엣지는 늘 검증 상태 「의심」·신뢰도 「후보」다. 사람이 확인해야 「확인됨」. 원문 관계 표(relations.jsonl)는 건드리지 않는다.
"""

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck import ruleset  # noqa: E402

OUT = ROOT / "out" / "rules"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="쉼표로 구분한 규칙 ID만 (예: DL-01,CHAIN-01)")
    ap.add_argument("--ai", action="store_true", help="luna로 엣지마다 AI 의견을 덧붙임 (공개 규정 문장만 보냄)")
    ap.add_argument("--model", default="gpt-5.6-luna")
    ap.add_argument("--limit", type=int, default=None, help="--ai로 설명할 엣지 수 상한")
    a = ap.parse_args()
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    rels = [json.loads(l) for l in (ROOT / "out" / "relations" / "relations.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rules = ruleset.load_rules(only=set(a.only.split(",")) if a.only else None)
    edges = ruleset.infer(regs, rels, rules)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "inferred.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in edges), encoding="utf-8")
    cnt = collections.Counter(e["rule_id"] for e in edges)
    (OUT / "summary.json").write_text(json.dumps({"rules": [r["id"] for r in rules], "edges": dict(cnt), "total": len(edges)}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"엣지 {len(edges)}개 (규칙 {len(rules)}개 사용)")
    for rid, n in sorted(cnt.items()):
        print(f"  {rid}\t{n}")
    if a.ai:
        notes = ruleset.annotate(edges, regs, a.model, limit=a.limit)
        c = collections.Counter(v["intent"] for v in notes.values())
        print(f"AI 의견 {len(notes)}건 ({a.model}) · 의도된 차이 가능성 {dict(c)}")
    print("결과 →", OUT)


if __name__ == "__main__":
    main()
