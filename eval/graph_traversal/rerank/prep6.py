"""6차 준비: 리랭커에 넣을 (질의, 후보) 쌍 만들기. 후보 풀 = 방식 F(규칙 1홉 먼저 + PageRank 채움)의 앵커별 상위 50."""
import sys, json, re, pickle, glob, collections
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tlib as L
T = L.T
sys.stdout.reconfigure(encoding="utf-8")
NS = T.NS
POOL = 50

texts, titles = {}, {}
for p in glob.glob("C:/Users/Admin/Downloads/kvic_pr11/out/parsed/[0-9]*.json"):
    r = json.loads(Path(p).read_text(encoding="utf-8"))
    for a in r["articles"]:
        if not a.get("deleted"): texts[a["id"]] = f"{r['name']} {a['norm']}"
    for ad in r.get("addenda") or []:
        for a in ad.get("articles") or []: texts[a["id"]] = f"{r['name']} 부칙 {a['norm']}"
    for x in r.get("appendices") or []:
        texts[x["id"]] = f"{r['name']} {x.get('title') or ''}\n{(x.get('text') or '')[:1500]}"
t4 = pickle.load(open(NS / "tests4.pkl", "rb")); cases2 = t4["cases2"]
changes = {}
for r in (5, 6, 7):
    for l in open(NS / "teamlab" / f"changes_r{r}.jsonl", encoding="utf-8"):
        c = json.loads(l); changes[c["id"]] = c["수정"]["개정문"]

CH = T.K.ONTOLOGY["change_paths"]
PT = L.transition(L.W)


def ppr_list(a, A):
    P = L.ppr_all(PT, [a], 0.3)[:, 0].copy()
    P[~L.unit_mask] = -1
    for x in A: P[L.idx[x]] = -1
    return [L.names[i] for i in np.argsort(-P)[:200] if P[i] > 1e-9]


def method_F(a, kind, A):
    first = T.m_rule1(a, kind, A)
    return first + [x for x in ppr_list(a, A) if x not in set(first)]


def query_for(setname, c, a, op):
    head = texts.get(a, a)[:700]
    if setname == "pr12":
        if op["op"] == "renumber":
            chg = f"{op.get('old_label','')} → {op.get('new_label','')} (조 번호 이동)"
        else:
            chg = f"{op.get('old','')} → {op.get('new','')}"
    else:
        chg = changes[c["id"]]
    return f"개정: {chg}\n개정된 조항: {head}"


out = {"pr12": [], "team": []}
for setname, cs in (("pr12", T.cases), ("team", cases2)):
    for c in cs:
        A = {x for x, _, _ in c["anchors"]}
        for a, kind, op in c["anchors"]:
            lst = [x for x in method_F(a, kind, A) if x in texts][:POOL]
            out[setname].append({"case": c["id"], "anchor": a, "kind": kind, "query": query_for(setname, c, a, op),
                                 "docs": lst, "wanted": c["wanted"]})
pickle.dump({"items": out, "texts": {k: v[:1200] for k, v in texts.items()}}, open(NS / "pairs6.pkl", "wb"))
for s, v in out.items():
    print(s, "앵커 수", len(v), "쌍 수", sum(len(x["docs"]) for x in v))
    # 풀 안에 정답이 얼마나 있나(상한)
    tot = sum(len(c["wanted"]) for c in (T.cases if s == "pr12" else cases2))
    pooled = collections.defaultdict(set)
    for x in v: pooled[x["case"]] |= set(x["docs"])
    cs = T.cases if s == "pr12" else cases2
    hit = sum(len(set(c["wanted"]) & pooled[c["id"]]) for c in cs)
    print("  풀 50 안 정답:", hit, "/", tot)
print(out["pr12"][0]["query"][:300])
