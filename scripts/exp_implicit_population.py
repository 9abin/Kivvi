"""연결 없는 관계의 모집단 크기 (읽기 전용 탐색): 인용 없이 이어지는 문제가 얼마나 있나.

  uv run --no-sync python scripts/exp_implicit_population.py
1) 용어 정의-사용: 「용어의 정의」류 조의 정의 용어가 다른 조문에서 쓰이는데, 그 정의 조를 인용하지 않는 곳
2) 같은 값 쌍: 서로 다른 규정에서 [같은 앞말 + 같은 숫자·단위]를 쓰는 항 쌍 중 인용으로 이어지지 않은 것 (앞말 = 숫자 바로 앞 낱말)
결과는 후보 수(모집단)일 뿐 정답이 아니다. 표본을 뽑아 사람이 맞는지 본다.
"""

import collections
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    rels = [json.loads(l) for l in (ROOT / "out" / "relations" / "relations.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    linked = set()
    for r in rels:
        s, t = r.get("src_article") or r.get("src"), r.get("target")
        linked.add((str(r["src"]).split(":")[0] + ":" + str(r.get("src_article", "")), str(t)))
    reg_pair = {(str(r["src"]).split(":")[0], str(r["target"]).split(":")[0]) for r in rels}
    print(f"규정 {len(regs)}개 · 관련 조항 표 {len(rels)}건 · 인용으로 이어진 규정 쌍 {len(reg_pair)}")

    # 1) 정의-사용
    defs = {}                                   # 용어 -> (규정, 조 라벨)
    for rid, r in regs.items():
        for a in r["articles"]:
            if a.get("deleted") or "정의" not in a["title"]:
                continue
            for m in re.finditer(r"[「『\"“]([가-힣A-Za-z0-9 ]{2,12})[」』\"”]\s*(?:이?란|라\s*함은|은|는)", a["text"]):
                defs.setdefault(m.group(1).strip(), (rid, a["label"]))
    print(f"\n[1] 정의 조 용어 {len(defs)}개 (예: {list(defs)[:8]})")
    uses_total = uses_other_reg = uses_unlinked = 0
    cites_def = collections.defaultdict(set)    # (정의 규정, 조) -> 인용한 출발 규정
    for r in rels:
        cites_def[str(r["target"])].add(str(r["src"]).split(":")[0])
    for term, (drid, dlab) in defs.items():
        if len(term) < 3:
            continue
        for rid, r in regs.items():
            hit = [a for a in r["articles"] if not a.get("deleted") and term in a["text"] and not (rid == drid and a["label"] == dlab)]
            if hit:
                uses_total += len(hit)
                if rid != drid:
                    uses_other_reg += len(hit)
                    if rid not in cites_def.get(f"{drid}:{dlab}", set()) and rid not in {x for x in cites_def.get(drid, set())}:
                        uses_unlinked += len(hit)
    print(f"    용어(3자 이상)가 쓰인 조문 {uses_total}곳 · 다른 규정에서 {uses_other_reg}곳 · 정의 조·규정을 인용하지 않는 규정에서 {uses_unlinked}곳")

    # 2) 같은 값 쌍
    pat = re.compile(r"([가-힣]{2,8})\s*(?:을|를|은|는|이|가|의)?\s*(\d+)\s*(회|일|개월|년|시간|퍼센트|%|만원|원|명|개)")
    paras = []                                   # (규정, 조, 앞말, 값)
    for rid, r in regs.items():
        for a in r["articles"]:
            if a.get("deleted"):
                continue
            for m in pat.finditer(a["text"]):
                paras.append((rid, a["label"], m.group(1), m.group(2) + m.group(3)))
    by_key = collections.defaultdict(list)
    for x in paras:
        by_key[(x[2], x[3])].append(x)
    pairs = []
    for key, xs in by_key.items():
        regs_in = {x[0] for x in xs}
        if len(regs_in) < 2:
            continue
        for i, a in enumerate(xs):
            for b in xs[i + 1:]:
                if a[0] != b[0] and (a[0], b[0]) not in reg_pair and (b[0], a[0]) not in reg_pair:
                    pairs.append((key, a, b))
    print(f"\n[2] 숫자+단위 표현 {len(paras)}곳 · 같은 (앞말, 값)을 서로 다른 규정이 쓰는 키 {sum(1 for k, v in by_key.items() if len({x[0] for x in v}) > 1)}개")
    print(f"    그중 두 규정이 인용으로 전혀 이어지지 않은 조문 쌍 {len(pairs)}개 (연결 없는 같은 값 후보)")
    random.Random(7).shuffle(pairs)
    for key, a, b in pairs[:12]:
        print(f"    {key[0]} {key[1]}: {regs[a[0]]['name']} {a[1]}  ↔  {regs[b[0]]['name']} {b[1]}")


if __name__ == "__main__":
    main()
