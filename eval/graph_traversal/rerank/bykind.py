import sys, pickle, collections
from pathlib import Path
NS = Path(sys.argv[1]); sys.stdout.reconfigure(encoding="utf-8")
sys.argv = [sys.argv[0], str(NS)]
exec(open(NS / "eval7.py", encoding="utf-8").read().split("R = {}")[0])
fn = STR["그래프+카카오+jina (RRF)"]
kinds = collections.defaultdict(list)
for c in cases2: kinds[c["kind"]].append(c)
print("| 개정 종류 | 문항 | 정답 | 풀 50 안 | 앵커당 N=5 | N=10 | N=20 | N=30 | N=50 |")
print("|---|---|---|---|---|---|---|---|---|")
for kd, cs in sorted(kinds.items(), key=lambda kv: -len(kv[1])):
    tot = sum(len(c["wanted"]) for c in cs)
    per = {c["id"]: fn("team", next(i for i, x in enumerate(items["team"]) if x["case"] == c["id"]), items["team"][next(i for i, x in enumerate(items["team"]) if x["case"] == c["id"])]) for c in cs}
    inpool = sum(len(set(c["wanted"]) & set(items["team"][next(i for i, x in enumerate(items["team"]) if x["case"] == c["id"])]["docs"])) for c in cs)
    cells = [f"{sum(len(set(c['wanted']) & set(per[c['id']][:N])) for c in cs)}/{tot}" for N in (5, 10, 20, 30, 50)]
    print(f"| {kd} | {len(cs)} | {tot} | {inpool} | " + " | ".join(cells) + " |")
