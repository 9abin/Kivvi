"""11차 2단계: 모은 후보(RAG, PR #11 규칙 그래프, PR #12 도메인 그래프)를 합친 구성의 후보 찾기 재현율. API 없음.
사용: EXT=1 python fullsys_eval.py <scratchpad> <pr12data>  (fullsys_collect.py 를 먼저 돌려 fullsys.pkl 을 만든다)"""
import sys, os, collections, pickle
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import traverse as T
import ext_graph
NS = T.NS
ext_graph.apply(T, str(NS / "p12db" / "domain_ontology.reviewed.db"))
import tlib as L
sys.stdout.reconfigure(encoding="utf-8")
FS = pickle.load(open(NS / "fullsys.pkl", "rb")); sets, lists = FS["sets"], FS["lists"]
PT = L.transition(L.W)


def ppr(a, A):
    P = L.ppr_all(PT, [a], 0.3)[:, 0].copy(); P[~L.unit_mask] = -1
    for x in A: P[L.idx[x]] = -1
    return [L.names[i] for i in np.argsort(-P)[:100] if P[i] > 1e-9]


def dom_rule(a, A):
    sc = T.DOM_SHARE.get(a, {})
    return [x for x, _ in sorted(sc.items(), key=lambda kv: (-kv[1], kv[0])) if x not in A and T.isunit(x)]


def chain(*ls):
    out, seen = [], set()
    for l in ls:
        for x in l:
            if x not in seen: seen.add(x); out.append(x)
    return out


def per_anchor(ops, mode, A):
    by = collections.defaultdict(list)
    for o in ops:
        if o.get(mode):
            for cid_, sc, paths in o[mode]:
                by[o["article"]].append((sc, cid_, paths))
    res = {}
    for a, v in by.items():
        seen, out = set(), []
        for sc, cid_, paths in sorted(v, key=lambda t: -t[0]):
            if cid_ not in seen and cid_ not in A: seen.add(cid_); out.append((cid_, paths))
        res[a] = out
    return res


def methods(c, ops):
    A = {x for x, _, _ in c["anchors"]} | {o["article"] for o in ops}
    anchors = [a for a, _, _ in c["anchors"]]
    full, gr = per_anchor(ops, "full", A), per_anchor(ops, "graph", A)
    rag_only = {a: [(x, p) for x, p in v if all(q.startswith("RAG") for q in p)] for a, v in full.items()}
    M = collections.OrderedDict()
    g = lambda d, a: [x for x, _ in d.get(a, [])]
    M["S1 RAG만"] = {a: g(rag_only, a) for a in anchors}
    M["S2 PR #11 규칙 그래프 (문구·전이 규칙 포함, RAG 없음)"] = {a: g(gr, a) for a in anchors}
    M["S3 RAG + PR #11 규칙 그래프"] = {a: g(full, a) for a in anchors}
    M["S4 PR #12 도메인 그래프만"] = {a: dom_rule(a, A) for a in anchors}
    M["S5 RAG + 규칙 그래프 + 도메인 그래프"] = {a: chain(g(full, a), dom_rule(a, A)) for a in anchors}
    M["S6 S5 뒤를 확장 그래프 PageRank로 채움"] = {a: chain(g(full, a), dom_rule(a, A), ppr(a, A)) for a in anchors}
    M["S7 규칙 먼저 + 확장 그래프 PageRank (이전 방식)"] = {a: chain(T.m_rule1(a, c["anchors"][0][1], A), ppr(a, A)) for a in anchors}
    M["S8 S3 / 도메인 규칙 번갈아 → PageRank"] = {a: chain(T.pool([g(full, a), dom_rule(a, A)], 200), ppr(a, A)) for a in anchors}
    return M, A


ks = (5, 10, 20, 30, 50)
RK = {}
PA = {}   # 세트 -> 케이스 -> 방법 -> 앵커별 목록 (판단 모델로 다시 줄 세울 후보 풀의 바탕)
for sname, cs in sets.items():
    tot = sum(len(c["wanted"]) for c in cs)
    print(f"\n### {sname}: 문항 {len(cs)}, 정답 짝 {tot}")
    print("| 구성 | " + " | ".join(f"@{k}" for k in ks) + " | 케이스당 후보 수(평균) |"); print("|---|" + "---|" * (len(ks) + 1))
    acc = collections.OrderedDict()
    for c in cs:
        M, A = methods(c, lists[sname][c["id"]])
        PA.setdefault(sname, {})[c["id"]] = M
        for mn, per in M.items():
            rk = T.pool([per[a] for a in per], 300)
            acc.setdefault(mn, {})[c["id"]] = rk
    RK[sname] = acc
    for mn, rks in acc.items():
        cells = []
        for k in ks:
            h = sum(len(set(c["wanted"]) & set(rks[c["id"]][:k])) for c in cs); cells.append(f"{h} ({h/tot:.0%})")
        print(f"| {mn} | " + " | ".join(cells) + f" | {np.mean([len(v) for v in rks.values()]):.0f} |", flush=True)
pickle.dump(RK, open(NS / "fullsys_rk.pkl", "wb"))
pickle.dump(PA, open(NS / "fullsys_pa.pkl", "wb"))
