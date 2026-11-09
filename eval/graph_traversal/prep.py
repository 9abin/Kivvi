import sys, json, re, pickle, collections
from pathlib import Path
NS = Path(sys.argv[1]); D = Path(sys.argv[2])
sys.stdout.reconfigure(encoding="utf-8")
G = pickle.load(open(NS / "kg.pkl", "rb")); nodes = G["nodes"]
art = lambda s: re.sub(r"-\d+-\d+-\d+$", "", s)
gold = {g["id"]: g for g in map(json.loads, open(D / "gold.jsonl", encoding="utf-8"))}
inp = {g["id"]: g for g in map(json.loads, open(D / "inputs.jsonl", encoding="utf-8"))}
cases = []
miss_anchor = 0; tot_anchor = 0
for cid, g in gold.items():
    ops = inp[cid]["amendment_ops"]
    anchors = []
    for op in ops:
        a = op.get("logical_article_id") or op.get("logical_unit_id") or art(op["logical_item_id"])
        kind = "번호 이동" if op["op"] == "renumber" else "값 변경"
        anchors.append((a, kind, op))
    anchor_ids = {a for a, _, _ in anchors}
    pairs = [tuple(sorted(art(g["after_facts"][k][s]["logical_item_id"]) for s in ("left", "right"))) for k in g["target_issue_ids"]]
    wanted = set()
    for p in pairs:
        for x in p:
            if x not in anchor_ids: wanted.add(x)
    if g["scenario_type"] == "clean": continue
    for a, _, _ in anchors:
        tot_anchor += 1; miss_anchor += a not in nodes
    cases.append({"id": cid, "type": g["scenario_type"], "family": g["family"], "anchors": anchors, "wanted": sorted(wanted), "pairs": pairs})
print("문제 있는 문항", len(cases), "정답 짝 합", sum(len(c["wanted"]) for c in cases), "시작 조 못 찾음", miss_anchor, "/", tot_anchor)
print("wanted not in KG:", sum(1 for c in cases for w in c["wanted"] if w not in nodes))
print(collections.Counter(c["family"] for c in cases).most_common(5), len({c["family"] for c in cases}))
print([ (c["id"], [a for a,_,_ in c["anchors"]][:3], c["wanted"][:3]) for c in cases[:3]])
pickle.dump(cases, open(NS / "cases.pkl", "wb"))
