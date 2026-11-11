"""10차: PR #11 그래프(+ 보강 커밋)에 PR #12 도메인 온톨로지를 합쳤을 때 후보 찾기가 달라지는지 세 평가셋에서 비교. API 없음.
사용: EXT=0 python traverse10.py <scratchpad> <pr12data>   (합치기 전)
      EXT=1 python traverse10.py <scratchpad> <pr12data>   (합친 뒤)
평가셋: PR12 80문항(177짝), PR #11 12건(126짝, tests9.pkl), 팀 5~7차 36문항(106짝, tests4.pkl)."""
import sys, os, collections, pickle
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import traverse as T
EXT = os.environ.get("EXT", "0") == "1"
NS = T.NS
if EXT:
    import ext_graph
    ext_graph.apply(T, str(NS / "p12db" / "domain_ontology.reviewed.db"))
import tlib as L
sys.stdout.reconfigure(encoding="utf-8")
sets = collections.OrderedDict([
    ("PR12 80문항", T.cases),
    ("PR #11 12건", pickle.load(open(NS / "tests9.pkl", "rb"))["cases3"]),
    ("팀 5~7차 36문항", pickle.load(open(NS / "tests4.pkl", "rb"))["cases2"]),
])
PT = L.transition(L.W)


def ppr(a, A):
    P = L.ppr_all(PT, [a], 0.3)[:, 0].copy(); P[~L.unit_mask] = -1
    for x in A: P[L.idx[x]] = -1
    return [L.names[i] for i in np.argsort(-P)[:100] if P[i] > 1e-9]


def dom_rule(a, kind, A):
    if not EXT: return []
    sc = T.DOM_SHARE.get(a, {})
    return [x for x, _ in sorted(sc.items(), key=lambda kv: (-kv[1], kv[0])) if x not in A and x in T.nodes and T.isunit(x)]


def chain(*fns):
    def f(a, k, A):
        out, seen = [], set()
        for fn in fns:
            for x in fn(a, k, A):
                if x not in seen: seen.add(x); out.append(x)
        return out
    return f


M = collections.OrderedDict()
M["A 규칙표 1홉"] = T.m_rule1
M["C PageRank"] = lambda a, k, A: ppr(a, A)
M["F 규칙 먼저 + PageRank"] = chain(T.m_rule1, lambda a, k, A: ppr(a, A))
if EXT:
    M["D 도메인 규칙 (같은 개념·속성 근거)"] = dom_rule
    M["G 규칙 → 도메인 규칙 → PageRank"] = chain(T.m_rule1, dom_rule, lambda a, k, A: ppr(a, A))
    M["H 규칙 / 도메인 규칙 번갈아 → PageRank"] = lambda a, k, A: T.pool([T.m_rule1(a, k, A), dom_rule(a, k, A)], 100) + [x for x in ppr(a, A) if x not in set(T.m_rule1(a, k, A)) | set(dom_rule(a, k, A))]
ks = (5, 10, 20, 30)
print(f"# 그래프: {'PR #11 + 보강 + PR #12 도메인 온톨로지' if EXT else 'PR #11 + 보강 (기준)'}")
res = {}
for sname, cs in sets.items():
    tot = sum(len(c["wanted"]) for c in cs)
    print(f"\n### {sname}: 정답 짝 {tot}")
    print("| 방법 | " + " | ".join(f"@{k}" for k in ks) + " |"); print("|---|" + "---|" * len(ks))
    for mn, fn in M.items():
        rk = {c["id"]: T.pool([fn(a, kd, {x for x, _, _ in c['anchors']}) for a, kd, _ in c["anchors"]], 100) for c in cs}
        cells = []
        for k in ks:
            h = sum(len(set(c["wanted"]) & set(rk[c["id"]][:k])) for c in cs); cells.append(f"{h} ({h/tot:.0%})"); res[(sname, mn, k)] = h / tot
        print(f"| {mn} | " + " | ".join(cells) + " |", flush=True)
pickle.dump(res, open(NS / f"res10_ext{int(EXT)}.pkl", "wb"))
