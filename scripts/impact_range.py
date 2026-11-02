"""역방향 영향 범위: 고친 조를 인용하는 곳을 인용 간선으로 거꾸로 찾는다 (규칙 · 코드만, LLM 없음).

  uv run python scripts/impact_range.py "인사규정 제30조"
  uv run python scripts/impact_range.py 24024:30 --hops 3
  uv run python scripts/impact_range.py "근로기준법 제60조"        # 상위 법령 조문을 인용하는 규정
  uv run python scripts/impact_range.py "직원채용지침 별표1" --json out/impact.json
입력: out/edges/edges_all.jsonl, out/parsed
결과는 영향 후보 목록이다. 충돌 여부는 판정하지 않는다 (AGENTS §3). 1단계 = 직접 인용, 2단계 이상 = 인용 전이(참고).
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.impact import SUSPECT, Index, summarize  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("start", help='조 ID(24024:30) 또는 이름("인사규정 제30조", "직원채용지침 별표1", "근로기준법 제60조")')
    ap.add_argument("--hops", type=int, default=2)
    ap.add_argument("--json", default="", help="결과를 JSON으로 저장")
    a = ap.parse_args()
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    idx = Index(edges, regs)
    node = idx.resolve(a.start)
    if not node:
        sys.exit(f"찾을 수 없음: {a.start}  (조 ID, 또는 「규정명 제N조」「규정명 별표N」「법령명 제N조」 형식)")
    res = idx.impact(node, a.hops)
    s = summarize(res)
    print(f"■ {res['name']}  [{node}]  — 이 곳을 인용하는 조 {len(res['hits'])}개, 규정 {s['규정 수']}개")
    print("  (직접 인용 = 1단계, 그 이후는 인용 전이로 참고용. 충돌 판정이 아닌 확인 후보)")
    for d in sorted({h["hops"] for h in res["hits"]}):
        hs = [h for h in res["hits"] if h["hops"] == d]
        print(f"\n[{d}단계{' · 직접 인용' if d == 1 else ' · 인용 전이(참고)'}] {len(hs)}개")
        for h in hs:
            flag = " ⚠구조 검사 의심" if h["suspect"] else ""
            via = "" if d == 1 else f"  ← {idx.name_of(h['via'])}"
            print(f"  · {h['name']}{via}{flag}")
            e = h["edges"][0]
            print(f"      “{e['raw']}” — {e['context'][:70].strip()}")
    if res["reg_level"]:
        print(f"\n[규정 단위 인용] 이 규정 전체를 가리키는 곳 {len(res['reg_level'])}개 (조 단위로 좁혀지지 않음)")
        for r in res["reg_level"]:
            print(f"  · {r['name']}  “{r['raw']}”")
    if not res["hits"] and not res["reg_level"]:
        print("\n  인용 간선으로는 영향받는 곳이 없습니다. 인용 없이 같은 사항을 다루는 조는 이 방법으로 찾지 못합니다.")
    print("\n※ 인용 간선(amend.edge)으로만 찾은 결과입니다. 인용 문구가 없는 관계(병행 규정, 같은 사항 다른 문서)는 포함되지 않고, 장 단위 인용은 따라가지 않습니다.")
    if a.json:
        Path(a.json).write_text(json.dumps({**res, "summary": s}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"저장: {a.json}")


if __name__ == "__main__":
    main()
