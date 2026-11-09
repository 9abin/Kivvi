"""3차 실험: (a) 정답 짝이 어떤 길로 이어져 있나, (b) 종류 맞춤 가중치, (c) 규칙 먼저+PageRank 채우기, (d) 저장된 RAG 순위와 합치기. API 없음."""
import sys, json, re, pickle, collections
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tlib as L
T = L.T
sys.stdout.reconfigure(encoding="utf-8")
NS, D = T.NS, T.D
nodes, cases = T.nodes, T.cases
tests = pickle.load(open(NS / "tests1.pkl", "rb"))
gold = {g["id"]: g for g in map(json.loads, open(D / "gold.jsonl", encoding="utf-8"))}
art = T.re.sub if False else None
strip = lambda s: re.sub(r"-\d+-\d+-\d+$", "", s)

# ---------- (a) 정답 짝이 이어진 길 (시작 조에서 최단 경로의 관계 이름, PART_OF 제외)
def shortest(a, b, maxh=4):
    prev = {a: None}; fr = [a]
    for h in range(maxh):
        nx = []
        for u in fr:
            for v, rel, d in T.adj.get(u, []):
                if v in prev: continue
                prev[v] = (u, rel, d); nx.append(v)
                if v == b:
                    path = []; cur = b
                    while prev[cur]:
                        u2, r2, d2 = prev[cur]; path.append(r2 + ("<" if d2 == "in" else ">")); cur = u2
                    return path[::-1]
        fr = nx
    return None

kind_of_pair = {}
for c in cases:
    g = gold[c["id"]]
    for k in g["target_issue_ids"]:
        f = g["after_facts"][k]
        pair = tuple(sorted(strip(f[s]["logical_item_id"]) for s in ("left", "right")))
        kind_of_pair[(c["id"], pair)] = f.get("kind")
rows_ = []
for c in cases:
    A = [a for a, _, _ in c["anchors"]]
    for (cid, pair), kd in kind_of_pair.items():
        if cid != c["id"]: continue
        anch = [x for x in pair if x in A]; partner = [x for x in pair if x not in A]
        if not anch or not partner: continue
        p = shortest(anch[0], partner[0])
        rows_.append((kd, c["type"], tuple(p) if p else None))
print("## (a) 정답 쌍 1개씩(개정된 쪽 → 짝) 최단 경로")
print("쌍 종류별 건수:", dict(collections.Counter(r[0] for r in rows_)))
for kd in ("broken_citation", "value_mismatch"):
    sub = [r for r in rows_ if r[0] == kd]
    c1 = collections.Counter(("없음" if r[2] is None else f"{len(r[2])}홉") for r in sub)
    print(f"\n{kd} ({len(sub)}쌍): 길이 분포 {dict(c1)}")
    top = collections.Counter(" ".join(r[2]) if r[2] else "도달 불가" for r in sub).most_common(8)
    for p, n in top: print(f"  {n:3d}  {p}")

# ---------- (b) 종류 맞춤 가중치: 번호 이동 / 값 변경 따로
CH = T.K.ONTOLOGY["change_paths"]
def kind_weights(kind):
    w = {}
    for rel, d, wt in CH.get(kind, []):
        if rel in ("CITES_REG", "SUPPLEMENTS"): continue
        w[rel] = max(w.get(rel, 0), wt)
    return w

def rank_kind_aware(alpha=0.3, hubpow=0.0, widen=0.0, limit=100):
    out = {}
    for c in T.cases:
        A = [a for a, _, _ in c["anchors"]]; Aset = set(A)
        lists = []
        for a, kind, _ in c["anchors"]:
            w = kind_weights(kind)
            for r in L.REL:
                if r not in w and widen: w[r] = widen
            PT = L.transition(w, hubpow)
            P = L.ppr_all(PT, [a], alpha)[:, 0].copy()
            P[~L.unit_mask] = -1
            for x in Aset: P[L.idx[x]] = -1
            lists.append([L.names[i] for i in np.argsort(-P)[:limit] if P[i] > 1e-9])
        out[c["id"]] = T.pool(lists, limit)
    return out

PT_team = L.transition(L.W)
r_team = L.rank_ppr(PT_team, 0.3)
exp = collections.OrderedDict()
exp["규칙표 1홉 (비교)"] = tests["규칙표 1홉 (kg.impact)"]
exp["PageRank 팀 가중치 (비교)"] = r_team
exp["PageRank 개정 종류 맞춤 가중치"] = rank_kind_aware()
exp["PageRank 종류 맞춤 + 나머지 관계 0.1"] = rank_kind_aware(widen=0.1)
for hp in (0.3, 0.6):
    exp[f"PageRank 팀 가중치 + 허브 감쇠 {hp}"] = L.rank_ppr(L.transition(L.W, hp), 0.3)
# 방향: CITES를 거슬러(이 조를 인용하는 조)만
def one_dir(rel_dir_keep):
    import scipy.sparse as sp
    typ, src_i, dst_i = L.typ, L.src_i, L.dst_i
    ws, aa, bb = [], [], []
    for t, s_, d_ in zip(typ, src_i, dst_i):
        w = L.W.get(t, 0)
        if w <= 0: continue
        # 조가 인용 대상(dst)을 향해 퍼지지 않고, 인용 대상에서 인용하는 쪽(src)으로만: 질문의 "이 조를 가리키는 조"
        if t == "CITES" and rel_dir_keep == "in_only":
            aa.append(d_); bb.append(s_); ws.append(w)
        else:
            aa += [s_, d_]; bb += [d_, s_]; ws += [w, w]
    M = sp.csr_matrix((ws, (aa, bb)), shape=(L.N, L.N)); d = np.asarray(M.sum(axis=1)).ravel(); d[d == 0] = 1
    return (sp.diags(1 / d) @ M).T.tocsr()
exp["PageRank 팀 가중치, 인용은 '이 조를 가리키는 조' 방향만"] = L.rank_ppr(one_dir("in_only"), 0.3)

# ---------- (c) 규칙 먼저 + PageRank 채우기
def rule_then(rank_b, m):
    out = {}
    for c in cases:
        A = {a for a, _, _ in c["anchors"]}
        first = T.pool([T.m_rule1(a, k, A) for a, k, _ in c["anchors"]])[:m]
        out[c["id"]] = first + [x for x in rank_b[c["id"]] if x not in set(first)]
    return out
for m in (3, 5):
    exp[f"규칙 1홉 상위 {m}개 먼저 + PageRank로 채움"] = rule_then(r_team, m)

print("\n## (b),(c) 상위 k 적중 (정답 짝 177)")
print("| 방법 | @5 | @10 | @15 | @20 |"); print("|---|---|---|---|---|")
for name, rk in exp.items(): print(L.rep(name, rk))
pickle.dump(exp, open(NS / "tests3.pkl", "wb"))

# ---------- (d) 저장된 RAG 순위와 합치기 (PR12 저장 기록, 개정 후 상태)
ret = {q["id"]: q for q in map(json.loads, open(D / "retrieval.jsonl", encoding="utf-8"))}
def rag_lists(c):
    st = ret[c["id"]]["states"]["after"]; mp = st["article_logical_ids"]
    return [[mp.get(i, i) for i in f["rankings"]["rag"]] for f in st["focus_retrieval"]]
rag_rank = {}
for c in cases:
    A = {a for a, _, _ in c["anchors"]}
    rag_rank[c["id"]] = [x for x in T.pool(rag_lists(c), 100) if x not in A]
def rrf(lists_a, lists_b, k=60):
    sc = collections.defaultdict(float)
    for lst in (lists_a, lists_b):
        for i, x in enumerate(lst[:100], 1): sc[x] += 1 / (k + i)
    return sorted(sc, key=lambda x: (-sc[x], x))
def mix_quota(rag, other, a, budget):
    out = list(rag[:a]); seen = set(out)
    for x in other:
        if len(out) >= budget: break
        if x not in seen: out.append(x); seen.add(x)
    for x in rag[a:]:
        if len(out) >= budget: break
        if x not in seen: out.append(x); seen.add(x)
    return out
print("\n## (d) 저장된 RAG 순위(개정 후 상태, 개정된 조 제외)와 합치기")
print("| 방법 | @5 | @10 | @15 | @20 |"); print("|---|---|---|---|---|")
print(L.rep("RAG만 (PR12 저장 순위, 상위 30까지)", rag_rank))
combo = collections.OrderedDict()
combo["RRF(RAG, 규칙 1홉)"] = {c["id"]: rrf(rag_rank[c["id"]], exp["규칙표 1홉 (비교)"][c["id"]]) for c in cases}
combo["RRF(RAG, PageRank 팀 가중치)"] = {c["id"]: rrf(rag_rank[c["id"]], r_team[c["id"]]) for c in cases}
for a in (0, 2, 3, 5):
    combo[f"자리 나눔 RAG {a}개 + PageRank (총 k)"] = {c["id"]: mix_quota(rag_rank[c["id"]], r_team[c["id"]], a, 100) for c in cases}
    combo[f"자리 나눔 RAG {a}개 + 규칙 1홉 (총 k)"] = {c["id"]: mix_quota(rag_rank[c["id"]], exp["규칙표 1홉 (비교)"][c["id"]], a, 100) for c in cases}
for name, rk in combo.items(): print(L.rep(name, rk))
print("\n(자리 나눔은 앞 a개를 RAG에서, 나머지를 그래프 방법에서 채우므로 k가 달라도 앞 a개는 같음)")
pickle.dump({"rag": rag_rank, "combo": combo}, open(NS / "tests3b.pkl", "wb"))
