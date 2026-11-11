"""6차: 논문에서 가져온 탐색 방식을 같은 평가셋(PR12 80문항, 팀 5~7차 36문항)에 구현해 비교. API 호출 없음.
가져온 방식과 출처
  L1 PageRank + 허브 감쇠 / 노드 특이도      HippoRAG (node specificity), BeamRAG (degree attenuation)
  L2 퍼짐 활성화(홉 감쇠 + 발화 임계값)       Query-Aware Spreading Activation arXiv 2606.30133, BeamRAG (hop decay)
  L3 인용 폐쇄(CITES 관계만 따라 닫힘)         Citation-Closure Retrieval for Regulatory Compliance QA, arXiv 2605.29742
  L4 단계적 확대(1홉 부족하면 K홉, 그래도 부족하면 PageRank)   A2RAG arXiv 2601.21162 (local 1-hop -> K-hop bridge -> PPR fallback)
기준선은 5차의 C(PageRank), E(가중 BFS 2홉), F(규칙 먼저 + PageRank 채움)."""
import sys, collections
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tlib as L
import pickle
T = L.T
sys.stdout.reconfigure(encoding="utf-8")
NS = T.NS
cases2 = pickle.load(open(NS / "tests4.pkl", "rb"))["cases2"]
sets = collections.OrderedDict([("PR12 80문항", T.cases), ("팀 5~7차 36문항", cases2)])
PT = {}


def pt(hub):
    if hub not in PT: PT[hub] = L.transition(L.W, hub)
    return PT[hub]


def top_units(P, A, limit=100):
    P = P.copy(); P[~L.unit_mask] = -1
    for x in A: P[L.idx[x]] = -1
    return [L.names[i] for i in np.argsort(-P)[:limit] if P[i] > 1e-9]


def ppr_hub(hub, alpha=0.3):
    return lambda a, k, A: top_units(L.ppr_all(pt(hub), [a], alpha)[:, 0], A)


def spread(hops, decay, hub, theta):
    """a0 = 1; 매 홉 a <- decay * P^T a, 임계값 theta 미만 노드는 발화하지 않는다. 점수 = 홉별 활성화 합."""
    def f(a, k, A):
        P = pt(hub); act = np.zeros(L.N); act[L.idx[a]] = 1.0; tot = act.copy()
        for _ in range(hops):
            fire = np.where(act >= theta, act, 0.0)
            act = decay * (P @ fire); tot += act
        tot[L.idx[a]] = 0
        return top_units(tot, A)
    return f


def closure(hops, rels):
    return lambda a, k, A: T.bfs(a, A, hops, rels=rels)


def rule_first(first_fn, fill_fn, m=100):
    def f(a, k, A):
        first = first_fn(a, k, A)[:m]
        return first + [x for x in fill_fn(a, k, A) if x not in set(first)]
    return f


def adaptive(min1=5, hops=2):
    """1홉 규칙 후보가 min1개 미만이면 가중 BFS를 더하고, 그래도 부족하면 PageRank로 채운다."""
    pr = ppr_hub(0.0)
    def f(a, k, A):
        out = list(T.m_rule1(a, k, A))
        if len(out) < min1: out += [x for x in T.m_wbfs(a, k, A, hops) if x not in set(out)]
        return out + [x for x in pr(a, k, A) if x not in set(out)]
    return f


ppr0 = ppr_hub(0.0)
M = collections.OrderedDict()
M["A 규칙표 1홉 (현재)"] = T.m_rule1
M["C PageRank (기준선)"] = ppr0
M["E 가중 BFS 2홉 (기준선)"] = lambda a, k, A: T.m_wbfs(a, k, A, 2)
M["F 규칙 먼저 + PageRank (기준선)"] = rule_first(T.m_rule1, ppr0)
for h in (0.25, 0.5, 1.0): M[f"L1 PageRank 허브 감쇠 {h}"] = ppr_hub(h)
for h in (0.5, 1.0): M[f"L1' 규칙 먼저 + PageRank 허브 감쇠 {h}"] = rule_first(T.m_rule1, ppr_hub(h))
for hops, decay, hub, th in ((2, 0.5, 0.0, 0.0), (3, 0.5, 0.0, 0.0), (3, 0.5, 0.5, 0.0), (3, 0.7, 0.5, 0.0), (3, 0.5, 0.5, 0.01), (3, 0.5, 0.5, 0.05)):
    M[f"L2 퍼짐 활성화 홉{hops} 감쇠{decay} 허브{hub} 임계{th}"] = spread(hops, decay, hub, th)
M["L2' 규칙 먼저 + 퍼짐 활성화(3홉 0.5 허브0.5)"] = rule_first(T.m_rule1, spread(3, 0.5, 0.5, 0.0))
M["L3 인용 폐쇄 2홉 (CITES만)"] = closure(2, ("CITES",))
M["L3 인용 폐쇄 3홉 (CITES만)"] = closure(3, ("CITES",))
M["L3' 규칙 먼저 + 인용 폐쇄 2홉 + PageRank"] = rule_first(T.m_rule1, rule_first(closure(2, ("CITES",)), ppr0))
M["L4 단계적 확대 (1홉<5 → 2홉 → PageRank)"] = adaptive(5)
M["L4 단계적 확대 (1홉<10 → 2홉 → PageRank)"] = adaptive(10)

ks = (5, 10, 20, 30)
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
print("\n### 두 평가셋 평균 (재현율 단순 평균)")
print("| 방법 | @5 | @10 | @20 |"); print("|---|---|---|---|")
for mn in M:
    print(f"| {mn} | " + " | ".join(f"{(res[('PR12 80문항', mn, k)] + res[('팀 5~7차 36문항', mn, k)]) / 2:.0%}" for k in (5, 10, 20)) + " |")
