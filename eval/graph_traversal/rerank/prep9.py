"""9차 준비: PR #11 자체 평가 12건에서 리랭커에 넣을 (질의, 후보) 쌍 만들기. prep6.py와 같은 방식(후보 풀 = 방식 F의 앵커별 상위 50, 질의 = 개정문 + 개정된 조항).
먼저 ../traverse9.py 를 돌려 tests9.pkl 을 만들어 둔다."""
import sys, json, glob, pickle, collections
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tlib as L
T = L.T
sys.stdout.reconfigure(encoding="utf-8")
NS = T.NS
POOL = 50
texts = {}
for p in glob.glob("C:/Users/Admin/Downloads/kvic_pr11/out/parsed/[0-9]*.json"):
    r = json.loads(Path(p).read_text(encoding="utf-8"))
    for a in r["articles"]:
        if not a.get("deleted"): texts[a["id"]] = f"{r['name']} {a['norm']}"
    for ad in r.get("addenda") or []:
        for a in ad.get("articles") or []: texts[a["id"]] = f"{r['name']} 부칙 {a['norm']}"
    for x in r.get("appendices") or []:
        texts[x["id"]] = f"{r['name']} {x.get('title') or ''}\n{(x.get('text') or '')[:1500]}"
cases3 = pickle.load(open(NS / "tests9.pkl", "rb"))["cases3"]
chg = {c["id"]: c["수정"]["개정문"] for c in map(json.loads, open(NS / "pr11set" / "changes.jsonl", encoding="utf-8"))}
PT = L.transition(L.W)


def ppr_list(a, A):
    P = L.ppr_all(PT, [a], 0.3)[:, 0].copy(); P[~L.unit_mask] = -1
    for x in A: P[L.idx[x]] = -1
    return [L.names[i] for i in np.argsort(-P)[:200] if P[i] > 1e-9]


def method_F(a, kind, A):
    first = T.m_rule1(a, kind, A)
    return first + [x for x in ppr_list(a, A) if x not in set(first)]


out = []
for c in cases3:
    A = {x for x, _, _ in c["anchors"]}
    for a, kind, _ in c["anchors"]:
        lst = [x for x in method_F(a, kind, A) if x in texts][:POOL]
        out.append({"case": c["id"], "anchor": a, "kind": kind, "query": f"개정: {chg[c['id']]}\n개정된 조항: {texts.get(a, a)[:700]}", "docs": lst, "wanted": c["wanted"]})
pickle.dump({"items": {"pr11": out}, "texts": {k: v[:1200] for k, v in texts.items()}}, open(NS / "pairs9.pkl", "wb"))
pooled = collections.defaultdict(set)
for x in out: pooled[x["case"]] |= set(x["docs"])
tot = sum(len(c["wanted"]) for c in cases3); hit = sum(len(set(c["wanted"]) & pooled[c["id"]]) for c in cases3)
print("앵커", len(out), "쌍", sum(len(x["docs"]) for x in out), "풀 50 안 정답", hit, "/", tot)
