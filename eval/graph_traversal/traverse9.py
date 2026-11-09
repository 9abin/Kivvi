"""9차: PR #11 자체 평가 문항(가상 개정 24건 중 정답 있는 12건, 정답 126쌍)에서 같은 탐색 방법들을 비교. API 호출 없음.
문항은 팀 브랜치 feature/ontology-boost-external-eval-261106 의 eval/impact_label_kit/산출/{changes,labels_final}.jsonl 을 작업 폴더 pr11set/ 에 꺼내 둔다.
PR #11 본문의 숫자(후보 20개 안 정답 포함률, 12문항: RAG 33%, 그래프 60%, 그래프 먼저 + RAG 67%)와 같은 지표로 본다."""
import sys, json, re, glob, collections, pickle
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tlib as L
T = L.T
sys.stdout.reconfigure(encoding="utf-8")
NS = T.NS
regs = {}
for p in glob.glob("C:/Users/Admin/Downloads/kvic_pr11/out/parsed/[0-9]*.json"):
    r = json.loads(Path(p).read_text(encoding="utf-8")); regs[r["name"]] = r["reg_id"]


def unit_id(reg, jo):
    rid = regs.get(reg)
    if rid is None: return None
    m = re.fullmatch(r"제(\d+)조(?:의\s*(\d+))?", jo.strip())
    cand = [f"{rid}:{m.group(1)}" + (f"의{m.group(2)}" if m.group(2) else "")] if m else [f"{rid}:{jo.replace(' ', '')}", f"{rid}:{re.sub(r'제(\d+)-(\d+)조', r'\1의\2', jo)}"]
    for c in cand:
        if c in T.nodes: return c
    return None


ch = {c["id"]: c for c in map(json.loads, open(NS / "pr11set" / "changes.jsonl", encoding="utf-8"))}
cases3, miss = [], collections.Counter()
for l in map(json.loads, open(NS / "pr11set" / "labels_final.jsonl", encoding="utf-8")):
    items = l["영향"]
    if not items: continue
    c = ch[l["id"]]; s = c["수정"]
    a = unit_id(s["규정"], s["조"])
    if a is None: miss["시작 조 노드 없음"] += 1; continue
    wanted = []
    for x in items:
        u = unit_id(x["규정"], x["조"])
        if u is None: miss["정답 노드 없음"] += 1
        elif u != a: wanted.append(u)
    cases3.append({"id": l["id"], "split": c["분할"], "kind": s["종류"], "anchors": [(a, s["종류"], None)], "wanted": sorted(set(wanted)), "n_total": len(items)})
tot_all = sum(c["n_total"] for c in cases3); tot = sum(len(c["wanted"]) for c in cases3)
print(f"사용 문항 {len(cases3)} (개발 {sum(c['split']=='개발' for c in cases3)}, 봉인 {sum(c['split']!='개발' for c in cases3)}), 정답 {tot_all}쌍 중 그래프에서 찾을 수 있는 {tot}쌍, 못 맞춘 {dict(miss)}")
print("종류:", dict(collections.Counter(c['kind'] for c in cases3)))
PTc = {}
def pt(h):
    if h not in PTc: PTc[h] = L.transition(L.W, h)
    return PTc[h]
def ppr(h):
    def f(a, k, A):
        P = L.ppr_all(pt(h), [a], 0.3)[:, 0].copy(); P[~L.unit_mask] = -1
        for x in A: P[L.idx[x]] = -1
        return [L.names[i] for i in np.argsort(-P)[:100] if P[i] > 1e-9]
    return f
def rule_first(first_fn, fill_fn):
    def f(a, k, A):
        first = first_fn(a, k, A)
        return first + [x for x in fill_fn(a, k, A) if x not in set(first)]
    return f
M = collections.OrderedDict()
M["A 규칙표 1홉 (현재)"] = T.m_rule1
M["PR11 collect (그래프 경로 + 전이 규칙, 문구·의미 검색 없음)"] = T.m_collect
M["C PageRank"] = ppr(0.0)
M["E 가중 BFS 2홉"] = lambda a, k, A: T.m_wbfs(a, k, A, 2)
M["F 규칙 먼저 + PageRank (추천)"] = rule_first(T.m_rule1, ppr(0.0))
M["허브 감쇠 PageRank 1.0"] = ppr(1.0)
ks = (5, 10, 20, 30, 50)
rk_all = {}
print(f"\n### PR11 문항 {len(cases3)}건: 찾을 수 있는 정답 {tot}쌍(전체 {tot_all}쌍 기준 비율은 괄호 안 아래 표)")
print("| 방법 | " + " | ".join(f"@{k}" for k in ks) + " |"); print("|---|" + "---|" * len(ks))
for mn, fn in M.items():
    rk = {c["id"]: T.pool([fn(a, kd, {x for x, _, _ in c['anchors']}) for a, kd, _ in c["anchors"]], 100) for c in cases3}
    rk_all[mn] = rk
    cells = []
    for k in ks:
        h = sum(len(set(c["wanted"]) & set(rk[c["id"]][:k])) for c in cases3)
        cells.append(f"{h} ({h/tot_all:.0%} / 찾을수있는것 {h/tot:.0%})")
    print(f"| {mn} | " + " | ".join(cells) + " |")
pickle.dump({"cases3": cases3, "rk": rk_all}, open(NS / "tests9.pkl", "wb"))
