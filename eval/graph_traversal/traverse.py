"""그래프 탐색 방법 비교 (API 없음). 단위: 문제 있는 80문항의 정답 짝(case, partner) 177개.
시작 조 = 개정된 조(여러 개면 조별로 후보를 만들고 번갈아 합침). 평가 = 상위 k개 안에 정답 짝이 드는 비율."""
import sys, json, re, pickle, collections, math, itertools
from pathlib import Path
NS = Path(sys.argv[1]); D = Path(sys.argv[2])
sys.path.insert(0, str(NS / "kgcode" / "src"))
sys.stdout.reconfigure(encoding="utf-8")
from regcheck import kg as K
from regcheck.collect import collect
from regcheck.change_query import ChangeQuery

G = pickle.load(open(NS / "kg.pkl", "rb")); nodes, rows = G["nodes"], G["rows"]
cases = pickle.load(open(NS / "cases.pkl", "rb"))
KG = K.KG(nodes, rows)
UNIT = ("Article", "Supplement", "Annex", "Form")
isunit = lambda n: nodes[n]["type"] in UNIT and not nodes[n].get("deleted")
reg_of = lambda n: nodes[n].get("reg")

# 인접 목록 (PART_OF 제외: 모든 조가 규정에 속해 전부 이어지기 때문)
adj = collections.defaultdict(list)           # u -> [(v, rel, dir)]
for r in rows:
    if r["type"] in ("PART_OF", "ARTICLE_OF", "ALIAS_OF"): continue
    adj[r["src"]].append((r["dst"], r["type"], "out"))
    adj[r["dst"]].append((r["src"], r["type"], "in"))
deg = {u: len(v) for u, v in adj.items()}


def excl(cands, anchors):
    return [c for c in cands if c not in anchors and c in nodes and isunit(c)]


# ---------- 방법들: (anchor, kind, anchors_set) -> 순위 목록
def m_rule1(a, kind, A):                       # 팀 규칙표 1홉 (kg.impact)
    res = KG.impact(a, kind)["candidates"]
    return excl([n for n, _ in sorted(res.items(), key=lambda kv: (-kv[1]["score"], kv[0]))], A)


def m_collect(a, kind, A):                     # PR12가 쓴 방식: collect (의미검색, 문구검색 없음)
    cq = ChangeQuery("x", reg_of(a), kind, starts=[a])
    c = collect(KG, cq, lambda t: [], lambda q, k: [], {"title": nodes[a].get("title", ""), "old": "", "new": "", "amend": ""})
    return excl([x.id for x in c], A)


def bfs(a, A, hops, rels=None, hub=60):
    """모든 관계를 방향 없이 따라 hops홉까지. 순위 = (홉 작은 순, 경로 수 많은 순)."""
    dist = {a: 0}; cnt = collections.Counter(); frontier = [a]
    for h in range(1, hops + 1):
        nxt = []
        for u in frontier:
            if h > 1 and deg.get(u, 0) > hub: continue
            for v, rel, d in adj.get(u, []):
                if rels and rel not in rels: continue
                if v in dist and dist[v] < h: continue
                if v not in dist: dist[v] = h; nxt.append(v)
                if dist[v] == h: cnt[v] += 1
        frontier = nxt
    out = [n for n in dist if n != a]
    return excl(sorted(out, key=lambda n: (dist[n], -cnt[n], n)), A)


W = dict(K.EXPAND_WEIGHTS)                     # 팀이 잰 관계별 정밀도


def m_wbfs(a, kind, A, hops=2):
    best = {}
    frontier = {a: 1.0}; seen = {a}
    for h in range(1, hops + 1):
        nxt = {}
        for u, sc in frontier.items():
            nb = [(v, rel) for v, rel, d in adj.get(u, []) if rel in W]
            if h > 1 and len(nb) > 60: continue
            damp = 1 / max(len(nb), 1) ** 0.25 if h > 1 else 1.0
            for v, rel in nb:
                if v in seen: continue
                s = sc * W[rel] * damp * (0.5 if h > 1 else 1)
                if v not in nxt or s > nxt[v]: nxt[v] = s
        for v, s in nxt.items():
            seen.add(v)
            if isunit(v): best[v] = max(best.get(v, 0), s)
        frontier = nxt
    out = sorted(best, key=lambda n: (-best[n], n))
    return excl(out, A)


def ppr(a, A, alpha=0.3, iters=30, wts=None):
    wts = wts or W
    p = {a: 1.0}
    for _ in range(iters):
        q = collections.defaultdict(float)
        for u, m in p.items():
            nb = [(v, wts[rel]) for v, rel, d in adj.get(u, []) if rel in wts]
            if not nb:
                q[a] += m * (1 - alpha); continue
            z = sum(w for _, w in nb)
            for v, w in nb: q[v] += m * (1 - alpha) * w / z
        q[a] += alpha
        p = q
    out = [n for n in p if n != a and isunit(n)]
    return excl(sorted(out, key=lambda n: (-p[n], n)), A), p


def m_ppr(a, kind, A, alpha=0.3, **kw): return ppr(a, A, alpha, **kw)[0]


def m_beam(a, kind, A, w1=12, w2=12, extra=("CITES", "SAME_TITLE", "SAME_VALUE", "USES_TERM")):
    """규칙 1홉 후보를 보존하고, 그중 상위 w1개에서 한 걸음 더 간 것 중 점수 높은 w2개를 뒤에 붙인다 (가지치기 번지기)."""
    first = m_rule1(a, kind, A)
    sc1 = KG.impact(a, kind)["candidates"]
    second = {}
    for n in first[:w1]:
        for v, rel, d in adj.get(n, []):
            if rel not in extra or v in A or v in first: continue
            if deg.get(v, 0) > 60: continue
            s = sc1.get(n, {"score": 0.3})["score"] * W.get(rel, 0.2)
            if v not in second or s > second[v]: second[v] = s
    out2 = [x for x, _ in sorted(second.items(), key=lambda kv: (-kv[1], kv[0])) if x in nodes and isunit(x)]
    return first + [x for x in out2[:w2] if x not in first]


def m_quota(a, kind, A):
    """관계 종류별로 후보를 따로 모아 번갈아 뽑는다 (점수 하나로 줄 세우지 않음)."""
    res = KG.impact(a, kind)["candidates"]
    by = collections.defaultdict(list)
    for n, c in sorted(res.items(), key=lambda kv: (-kv[1]["score"], kv[0])):
        if n in A or not isunit(n): continue
        for p in c["paths"]: by[p].append(n)
    order = sorted(by, key=lambda p: -max(res[n]["score"] for n in by[p]))
    out = []; seen = set()
    for i in range(300):
        for p in order:
            if i < len(by[p]) and by[p][i] not in seen:
                seen.add(by[p][i]); out.append(by[p][i])
        if i > 100: break
    return out


# 케이스 단위 합치기 (앵커별 목록을 번갈아) 와 평가
def pool(lists, limit=300):
    out = []; seen = set()
    for i in range(max(map(len, lists), default=0)):
        for l in lists:
            if i < len(l) and l[i] not in seen:
                seen.add(l[i]); out.append(l[i])
                if len(out) >= limit: return out
    return out


def run(method, **kw):
    ranked = {}
    for c in cases:
        A = {a for a, _, _ in c["anchors"]}
        ranked[c["id"]] = pool([method(a, kind, A, **kw) for a, kind, _ in c["anchors"]])
    return ranked


def score(ranked, ks=(5, 10, 15, 20, 30, 50)):
    res = {}
    tot = sum(len(c["wanted"]) for c in cases)
    for k in ks:
        hit = sum(len(set(c["wanted"]) & set(ranked[c["id"]][:k])) for c in cases)
        full = sum(1 for c in cases if set(c["wanted"]) <= set(ranked[c["id"]][:k]))
        res[k] = (hit, full)
    size = sum(len(v) for v in ranked.values()) / len(ranked)
    return res, tot, size


def table(tests, ks=(5, 10, 15, 20, 30, 50)):
    tot = sum(len(c["wanted"]) for c in cases)
    print("정답 짝", tot, "개 (80문항)")
    print("| 방법 | " + " | ".join(f"@{k}" for k in ks) + " | 후보 수(평균) | 문항 전부@20 |")
    print("|---|" + "---|" * (len(ks) + 2))
    for name, rk in tests.items():
        res, tot, size = score(rk, ks)
        print(f"| {name} | " + " | ".join(f"{res[k][0]} ({res[k][0]/tot:.0%})" for k in ks) + f" | {size:.0f} | {res[20][1]}/80 |")


if __name__ == "__main__":
    tests = collections.OrderedDict()
    tests["규칙표 1홉 (kg.impact)"] = run(m_rule1)
    tests["collect (PR12 방식)"] = run(m_collect)
    tests["전관계 BFS 1홉"] = run(lambda a, k, A: bfs(a, A, 1))
    tests["전관계 BFS 2홉"] = run(lambda a, k, A: bfs(a, A, 2))
    tests["전관계 BFS 3홉"] = run(lambda a, k, A: bfs(a, A, 3))
    tests["가중 BFS 2홉"] = run(m_wbfs, hops=2)
    tests["가중 BFS 3홉"] = run(m_wbfs, hops=3)
    for al in (0.15, 0.3, 0.5):
        tests[f"개인화 PageRank (되돌아옴 {al})"] = run(m_ppr, alpha=al)
    tests["규칙 1홉 + 가지치기 번지기(12,12)"] = run(m_beam)
    tests["규칙 1홉 + 가지치기 번지기(6,12)"] = run(m_beam, w1=6, w2=12)
    tests["관계별 번갈아 뽑기"] = run(m_quota)
    table(tests)
    pickle.dump(tests, open(NS / "tests1.pkl", "wb"))
