"""7차 준비: 정상 변경(문제 없음) 40문항에서 후보 풀을 만들어, 점수 기준선이 거짓 경보를 줄이는지 본다. PR12 정답 파일의 clean 문항 사용."""
import sys, json, re, pickle, glob
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tlib as L
T = L.T
sys.stdout.reconfigure(encoding="utf-8")
NS, D = T.NS, T.D
POOL = int(__import__("os").environ.get("POOL", "15"))
base = pickle.load(open(NS / "pairs6.pkl", "rb")); texts = base["texts"]
art = lambda s: re.sub(r"-\d+-\d+-\d+$", "", s)
gold = {g["id"]: g for g in map(json.loads, open(D / "gold.jsonl", encoding="utf-8"))}
inp = {g["id"]: g for g in map(json.loads, open(D / "inputs.jsonl", encoding="utf-8"))}
PT = L.transition(L.W)


def ppr_list(a, A):
    P = L.ppr_all(PT, [a], 0.3)[:, 0].copy()
    P[~L.unit_mask] = -1
    for x in A: P[L.idx[x]] = -1
    return [L.names[i] for i in np.argsort(-P)[:200] if P[i] > 1e-9]


def method_F(a, kind, A):
    first = T.m_rule1(a, kind, A)
    return first + [x for x in ppr_list(a, A) if x not in set(first)]


out = []
for cid, g in gold.items():
    if g["scenario_type"] != "clean": continue
    ops = inp[cid]["amendment_ops"]
    anchors = []
    for op in ops:
        a = op.get("logical_article_id") or op.get("logical_unit_id") or art(op["logical_item_id"])
        anchors.append((a, "번호 이동" if op["op"] == "renumber" else "값 변경", op))
    A = {a for a, _, _ in anchors}
    for a, kind, op in anchors:
        if a not in L.nodes: continue
        lst = [x for x in method_F(a, kind, A) if x in texts][:POOL]
        chg = (f"{op.get('old_label','')} → {op.get('new_label','')} (조 번호 이동)" if op["op"] == "renumber" else f"{op.get('old','')} → {op.get('new','')}")
        out.append({"case": cid, "anchor": a, "kind": kind, "query": f"개정: {chg}\n개정된 조항: {texts.get(a, a)[:700]}", "docs": lst, "wanted": []})
pickle.dump({"items": {"clean": out}, "texts": texts}, open(NS / "pairs7.pkl", "wb"))
print("정상 변경 앵커", len(out), "쌍", sum(len(x["docs"]) for x in out), "문항", len({x['case'] for x in out}))
