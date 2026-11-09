"""A 산출물(out.jsonl 4개)과 sample.json을 합쳐 산출/changes.jsonl 을 만든다. 사용: python build_changes.py <scratchpad>"""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
S = Path(sys.argv[1]); H = Path(__file__).resolve().parent
items = {o["id"]: o for o in json.loads((H / "sample.json").read_text(encoding="utf-8"))["items"]}
out = {}
for k in range(1, 5):
    p = S / f"A{k}" / "out.jsonl"
    for l in p.read_text(encoding="utf-8").splitlines():
        if l.strip(): r = json.loads(l); out[r["id"]] = r
rows = []
for i in sorted(items):
    o = items[i]; r = out.get(i)
    if not r: print("누락", i); continue
    rows.append({"id": i, "분할": "봉인", "수정": {"규정": o["reg"], "조": o["label"], "항": r.get("항"), "종류": o["종류"], "개정문": r["개정문"]}})
(H / "산출" / "changes.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
print(len(rows))
