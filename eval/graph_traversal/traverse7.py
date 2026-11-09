"""7차: G-Retriever의 PCST(Prize-Collecting Steiner Tree)식 '연결된 작은 덩어리로 고르기'를 같은 평가셋에 적용. API 없음.
방식(단순화): 노드 상금 = 개정된 조에서의 PageRank 점수(조 단위 노드만), 선 비용 = lam / 관계 정밀도 가중치.
개정된 조를 뿌리로 한 최단경로 트리 위에서 '상금 합 - 선 비용 합'이 최대인 연결 부분 트리를 동적계획법으로 구한다(뿌리 있는 PCST의 트리 근사).
후보 목록은 집합이므로 순위가 필요하다: lam을 키워 가며 부분 트리를 줄일 때 끝까지 남는 정도(남는 최대 lam)로 순위를 매긴다.
선택된 노드마다 뿌리까지의 경로(부모 사슬)가 나오므로 '왜 영향권인지'를 보여 주는 근거 경로가 부산물로 생긴다.
논문 구현(pcst_fast 등 외부 패키지)이 아니라 이 트리 근사를 직접 구현했다."""
import sys, heapq, collections, pickle
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tlib as L
T = L.T
sys.stdout.reconfigure(encoding="utf-8")
NS = T.NS
cases2 = pickle.load(open(NS / "tests4.pkl", "rb"))["cases2"]
sets = collections.OrderedDict([("PR12 80문항", T.cases), ("팀 5~7차 36문항", cases2)])
LAMS = np.geomspace(1e-4, 3.0, 40)[::-1]          # 큰 값부터(부분 트리가 작은 쪽부터)
PTc = {}


def pt(h):
    if h not in PTc: PTc[h] = L.transition(L.W, h)
    return PTc[h]


def spt(a, hubcost):
    """뿌리 a에서 관계 가중치(정밀도)의 역수를 비용으로 한 최단경로 트리. 반환: 부모, 부모까지 선 비용, 거리."""
    dist = {a: 0.0}; par = {}; pc = {}; pq = [(0.0, a)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, 1e18): continue
        du = len(T.adj.get(u, [])) if hubcost else 0
        for v, rel, _ in T.adj.get(u, []):
            w = T.W.get(rel)
            if not w: continue
            c = 1.0 / w + (0.15 * np.log1p(du) if hubcost else 0.0)
            nd = d + c
            if nd < dist.get(v, 1e18) - 1e-12:
                dist[v] = nd; par[v] = u; pc[v] = c; heapq.heappush(pq, (nd, v))
    return par, pc, dist


def pcst_rank(a, A, prize_hub=0.0, hubcost=False, limit=100):
    p = L.ppr_all(pt(prize_hub), [a], 0.3)[:, 0].copy()
    p[~L.unit_mask] = 0.0
    p[L.idx[a]] = 0.0
    for x in A: p[L.idx[x]] = 0.0
    p = p / (p.max() or 1.0)
    par, pc, dist = spt(a, hubcost)
    order = sorted(dist, key=lambda n: -dist[n])                   # 먼 노드부터(자식이 먼저)
    children = collections.defaultdict(list)
    for v, u in par.items(): children[u].append(v)
    pr = {n: p[L.idx[n]] for n in dist}
    best = {}
    for lam in LAMS:
        g = {}
        for v in order:                                            # 아래에서 위로
            g[v] = pr[v] + sum(max(0.0, g[c] - lam * pc[c]) for c in children[v])
        stack = [a]
        while stack:                                               # 위에서 아래로: 이득이 남는 가지만 따라감
            u = stack.pop()
            for c in children[u]:
                if g[c] - lam * pc[c] > 0:
                    if c not in best: best[c] = lam                # 처음 선택된 lam = 남는 최대 lam
                    stack.append(c)
    out = [n for n in best if T.isunit(n) and n not in A]
    out.sort(key=lambda n: (-best[n], -pr[n], n))
    return out[:limit], par


def pcst_fn(**kw): return lambda a, k, A: pcst_rank(a, A, **kw)[0]


def rule_first(first_fn, fill_fn):
    def f(a, k, A):
        first = first_fn(a, k, A)
        return first + [x for x in fill_fn(a, k, A) if x not in set(first)]
    return f


def ppr(h):
    def f(a, k, A):
        P = L.ppr_all(pt(h), [a], 0.3)[:, 0].copy(); P[~L.unit_mask] = -1
        for x in A: P[L.idx[x]] = -1
        return [L.names[i] for i in np.argsort(-P)[:100] if P[i] > 1e-9]
    return f


M = collections.OrderedDict()
M["A 규칙표 1홉 (현재)"] = T.m_rule1
M["C PageRank (기준선)"] = ppr(0.0)
M["F 규칙 먼저 + PageRank (기준선)"] = rule_first(T.m_rule1, ppr(0.0))
M["P1 PCST (상금 = PageRank)"] = pcst_fn()
M["P2 PCST (상금 = 허브 감쇠 PageRank 1.0)"] = pcst_fn(prize_hub=1.0)
M["P3 PCST (선 비용에 허브 벌점)"] = pcst_fn(hubcost=True)
M["P4 규칙 먼저 + PCST 채움"] = rule_first(T.m_rule1, pcst_fn())

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

# 근거 경로 예시: 첫 케이스의 첫 앵커에서 상위 3개 후보의 뿌리까지 경로
c = T.cases[0]; a = c["anchors"][0][0]; A = {x for x, _, _ in c["anchors"]}
out, par = pcst_rank(a, A)
print(f"\n근거 경로 예시 (시작 조 {a})")
for n in out[:3]:
    path = [n]
    while path[-1] in par: path.append(par[path[-1]])
    print("  ", " <- ".join(path[:5]))
