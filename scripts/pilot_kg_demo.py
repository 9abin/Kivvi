"""육아휴직 예시 지식 그래프(data/manual/pilot/육아휴직_예시.json)를 읽어
  ① 규정문의 원문문장이 항목 본문에 실제로 있는지 검사하고
  ② 같은 (개념, 칸, 범위)를 문서 사이에서 비교해 값이 다른 쌍을 「의심」으로 보인다.
  uv run python scripts/pilot_kg_demo.py
판정이 아니라 확인 후보다(AGENTS §3). 규정문은 손으로 만든 시안이다."""
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    kg = json.loads((ROOT / "data" / "manual" / "pilot" / "육아휴직_예시.json").read_text(encoding="utf-8"))
    regs = [json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json")]
    items = {it["id"]: it["norm"] for r in regs for a in r["articles"] for it in a["items"]}
    names = {r["reg_id"]: r["name"] for r in regs}

    def doc(item_id: str) -> str:
        return names[item_id.split(":")[0]]

    print("① 원문 검사")
    bad = 0
    for s in kg["규정문"] + kg["인용"]:
        ok = s["원문문장"] in items.get(s["항목"], "")
        bad += not ok
        print(f"  {'ok ' if ok else 'NG '} {s.get('id', '인용')} {s['항목']}")
    print(f"  → 원문에 없는 문장 {bad}건\n")

    print("② 같은 (개념, 칸, 범위) 비교")
    groups = collections.defaultdict(list)
    for s in kg["규정문"]:
        groups[(s["개념"], s["칸"], s.get("범위", ""))].append(s)
    for key, ss in groups.items():
        docs = {doc(s["항목"]) for s in ss}
        if len(docs) < 2:
            print(f"  - {key[1]}: {doc(ss[0]['항목'])}에만 있음")
            continue
        vals = {(s.get("비교"), s["값"], s.get("단위")) for s in ss}
        flag = "같음" if len(vals) == 1 else "★ 의심: 값이 다름"
        print(f"  - {key[1]}: {flag}")
        for s in ss:
            print(f"      {doc(s['항목'])} {s['항목'].split(':')[1]} → {s.get('비교', '')} {s['값']}{s.get('단위', '')}  {s.get('비고', '')}")
    print("\n③ 연결(인용·위임)")
    for e in kg["인용"]:
        print(f"  {doc(e['항목'])} {e['항목'].split(':')[1]} -[{e['kind']}]-> {names.get(e['대상'].split(':')[0], e['대상'])}{(' 제' + e['대상'].split(':')[1] + '조') if ':' in e['대상'] else ''}")


if __name__ == "__main__":
    main()
