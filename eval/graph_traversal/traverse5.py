"""5차: 후보 조합들을 두 평가셋(PR12 80문항, 팀 5~7차 36문항)에서 한 표로 비교. 순위 합치기(RRF), 종류 맞춤 PageRank, 규칙 먼저 + PageRank 채우기."""
import sys, json, re, pickle, collections
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tlib as L
T = L.T
sys.stdout.reconfigure(encoding="utf-8")
NS = T.NS
t4 = pickle.load(open(NS / "tests4.pkl", "rb"))
cases2 = t4["cases2"]
sets = collections.OrderedDict([("PR12 80문항", T.cases), ("팀 5~7차 36문항", cases2)])

CH = T.K.ONTOLOGY["change_paths"]
def kind_weights(kind, widen=0.0):
    w = {}
    for rel, d, wt in CH.get(kind, []):
        if rel in ("CITES_REG", "SUPPLEMENTS"): continue
        w[rel] = max(w.get(rel, 0), wt)
    if widen:
        for r in L.REL:
            if r not in w: w[r] = widen
    return w
PTcache = {}
def PT_for(w):
    key = tuple(sorted(w.items()))
    if key not in PTcache: PTcache[key] = L.transition(w)
    return PTcache[key]

def lists_for(c, fn):
    A = {a for a, _, _ in c["anchors"]}
    return T.pool([fn(a, k, A) for a, k, _ in c["anchors"]], 100)

def ppr_fn(w_fn, alpha=0.3):
    def f(a, kind, A):
        P = L.ppr_all(PT_for(w_fn(kind)), [a], alpha)[:, 0].copy()
        P[~L.unit_mask] = -1
        for x in A: P[L.idx[x]] = -1
        top = np.argsort(-P)[:100]
        return [L.names[i] for i in top if P[i] > 1e-9]
    return f

def rrf(*ls, k=60):
    sc = collections.defaultdict(float)
    for lst in ls:
        for i, x in enumerate(lst[:100], 1): sc[x] += 1 / (k + i)
    return sorted(sc, key=lambda x: (-sc[x], x))

def union_rule(a, kind, A):
    return T.pool([T.m_rule1(a, k, A) for k in ("값 변경", "삭제", "번호 이동", "정의 변경", "위임·법령 변경", "내용 변경")], 200)

def rule_first(m, ppr_f, rule_f):
    def f(a, kind, A):
        first = rule_f(a, kind, A)[:m]
        return first + [x for x in ppr_f(a, kind, A) if x not in set(first)]
    return f

team_w = lambda kind: L.W
ppr_team = ppr_fn(team_w)
ppr_kind = ppr_fn(lambda k: kind_weights(k, 0.1))
def rrf_fn(rule_f, ppr_f):
    return lambda a, kind, A: rrf(rule_f(a, kind, A), ppr_f(a, kind, A))

methods = collections.OrderedDict()
methods["A 규칙표 1홉 (현재)"] = T.m_rule1
methods["B 규칙표 전 종류 합집합"] = union_rule
methods["C PageRank (팀 가중치)"] = ppr_team
methods["D PageRank (종류 맞춤 + 나머지 0.1)"] = ppr_kind
methods["E 가중 BFS 2홉"] = lambda a, k, A: T.m_wbfs(a, k, A, hops=2)
methods["F 규칙 먼저 + PageRank 채움"] = rule_first(100, ppr_team, T.m_rule1)
methods["G 규칙 상위 5 먼저 + PageRank"] = rule_first(5, ppr_team, T.m_rule1)
methods["H 순위합치기(RRF) 규칙+PageRank"] = rrf_fn(T.m_rule1, ppr_team)
methods["I 순위합치기(RRF) 규칙+종류맞춤 PageRank"] = rrf_fn(T.m_rule1, ppr_kind)
methods["J 규칙(전 종류) 먼저 + PageRank"] = rule_first(100, ppr_team, union_rule)

ks = (5, 10, 15, 20, 30)
res = {}
for sname, cs in sets.items():
    tot = sum(len(c["wanted"]) for c in cs)
    print(f"\n### {sname}: 정답 짝 {tot}")
    print("| 방법 | " + " | ".join(f"@{k}" for k in ks) + " |"); print("|---|" + "---|" * len(ks))
    for mname, fn in methods.items():
        rk = {c["id"]: lists_for(c, fn) for c in cs}
        cells = []
        for k in ks:
            h = sum(len(set(c["wanted"]) & set(rk[c["id"]][:k])) for c in cs); cells.append(f"{h} ({h/tot:.0%})")
            res[(sname, mname, k)] = h / tot
        print(f"| {mname} | " + " | ".join(cells) + " |")
        res[(sname, mname, "rk")] = rk
# 두 평가셋을 같은 비중(정답 비율 평균)으로 본 점수
print("\n### 두 평가셋 평균(각 평가셋의 재현율을 단순 평균)")
print("| 방법 | @5 | @10 | @20 |"); print("|---|---|---|---|")
for mname in methods:
    print(f"| {mname} | " + " | ".join(f"{(res[('PR12 80문항', mname, k)] + res[('팀 5~7차 36문항', mname, k)]) / 2:.0%}" for k in (5, 10, 20)) + " |")
pickle.dump({k: v for k, v in res.items() if k[2] != "rk"}, open(NS / "res5.pkl", "wb"))
pickle.dump({k: v for k, v in res.items() if k[2] == "rk"}, open(NS / "rk5.pkl", "wb"))
