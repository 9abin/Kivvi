"""B1, B2 구간 파일(스크래치 B?_k/산출/labels_B?.jsonl)을 합치고 일치도를 계산한다. 사용: python merge_bc.py <scratchpad>"""
import json, re, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
S = Path(sys.argv[1]); H = Path(__file__).resolve().parent
J = lambda p: [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]
norm = lambda s: re.sub(r"\s+", "", s or "")
ch = {c["id"]: c for c in J(H / "산출/changes.jsonl")}
L = {}
for b in ("B1", "B2"):
    L[b] = {}
    for k in range(1, 5):
        for r in J(S / f"{b}_{k}/산출/labels_{b}.jsonl"): L[b][r["id"]] = r
    assert set(L[b]) == set(ch), (b, set(ch) - set(L[b]))
    (H / f"산출/labels_{b}.jsonl").write_text("\n".join(json.dumps(L[b][i], ensure_ascii=False) for i in sorted(L[b])) + "\n", encoding="utf-8")
key = lambda e: (e["규정"], norm(e["조"]))
tot = both = only1 = only2 = 0
print("| 개정 | 종류 | B1 | B2 | 일치 | B1만 | B2만 |"); print("|---|---|---|---|---|---|---|")
for i in sorted(ch):
    s1 = {key(e) for e in L["B1"][i]["영향"]}; s2 = {key(e) for e in L["B2"][i]["영향"]}
    b, o1, o2 = s1 & s2, s1 - s2, s2 - s1
    tot += len(s1 | s2); both += len(b); only1 += len(o1); only2 += len(o2)
    print(f"| {i} | {ch[i]['수정']['종류']} | {len(s1)} | {len(s2)} | {len(b)} | {len(o1)} | {len(o2)} |")
print(f"\n합집합 {tot}, 일치 {both} ({both/max(tot,1):.0%}), B1만 {only1}, B2만 {only2}")
