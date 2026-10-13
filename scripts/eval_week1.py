"""1주차 사람 검토(§C)와 인용 구조 검사 결과를 대조한다.

  uv run python scripts/eval_week1.py
먼저 scripts/extract_edges.py 를 실행해 out/edges/edges.jsonl 을 만든다.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = json.loads((ROOT / "eval" / "week1_citations.json").read_text(encoding="utf-8"))
EDGES = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges.jsonl").read_text(encoding="utf-8").splitlines()]
PROBLEM = {"missing_article", "missing_item", "missing_chapter", "missing_appendix", "deleted_target",
           "name_mismatch", "unknown_regulation", "title_mismatch"}


def main() -> None:
    hit = 0
    structural = [c for c in GOLD["cases"] if c["expect"] == "structural"]
    for c in GOLD["cases"]:
        cand = [e for e in EDGES if e["src"] == c["src"] and c["raw"] in e["raw"]]
        found = [e for e in cand if e["status"] in c.get("status", [])]
        if c["expect"] == "structural":
            hit += bool(found)
            mark = "탐지" if found else "놓침"
        else:
            mark = "의미 검사 대상" + (f" (구조상 {cand[0]['status']})" if cand else " (엣지 없음)")
        e = (found or cand or [None])[0]
        print(f"{c['id']:5} {mark:22} {e['raw'] if e else '-':22} {e['note'][:50] if e else ''}")
    # 사람 정답에 없는 구조 검사 결과 (21건 본문·별표·별지 기준). 부칙은 옛 번호가 남는 경우가 많아 따로 센다
    gold_src = {c["src"] for c in GOLD["cases"]}
    extra = [e for e in EDGES if e["status"] in PROBLEM and e["src"] not in gold_src]
    body_extra = [e for e in extra if e["src_kind"] != "부칙"]
    print(f"\n구조 검사 대상 {len(structural)}건 중 {hit}건 탐지 (재현율 {hit / len(structural):.0%})")
    print(f"정답 목록 밖 결과: 본문·별표·별지 {len(body_extra)}건, 부칙 {len(extra) - len(body_extra)}건 (사람 확인 필요)")
    for e in body_extra:
        print(f"  [{e['status']}] {e['src']} {e['raw']} | {e['note'][:60]}")
    sys.exit(0 if hit == len(structural) else 1)


if __name__ == "__main__":
    main()
