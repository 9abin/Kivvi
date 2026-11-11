"""2차 실험: 방법별 분해, 관계 하나씩 빼 보기, 가중치 학습(관계 묶음 단위 교차검증), 저장된 RAG 순위와 합치기. API 없음."""
import sys, json, re, pickle, collections, random
from pathlib import Path
import numpy as np
import scipy.sparse as sp
sys.path.insert(0, str(Path(__file__).resolve().parent))
import traverse as T

sys.stdout.reconfigure(encoding="utf-8")
NS, D = T.NS, T.D
nodes, rows, cases = T.nodes, T.rows, T.cases
idx = {n: i for i, n in enumerate(nodes)}
names = list(nodes)
N = len(names)
unit_mask = np.array([T.isunit(n) for n in names])
SKIP = ("PART_OF", "ARTICLE_OF", "ALIAS_OF")
REL = sorted({r["type"] for r in rows if r["type"] not in SKIP})

src_i = np.array([idx[r["src"]] for r in rows if r["type"] not in SKIP])
dst_i = np.array([idx[r["dst"]] for r in rows if r["type"] not in SKIP])
typ = np.array([r["type"] for r in rows if r["type"] not in SKIP])


def transition(wts, hubpow=0.0, allow=None):
    w = np.array([wts.get(t, 0.0) for t in typ], dtype=float)
    keep = w > 0
    a = np.concatenate([src_i[keep], dst_i[keep]]); b = np.concatenate([dst_i[keep], src_i[keep]])
    ww = np.concatenate([w[keep], w[keep]])
    M = sp.csr_matrix((ww, (a, b)), shape=(N, N))
    d = np.asarray(M.sum(axis=1)).ravel()
    if hubpow:                                   # 이웃이 많은 노드에서 퍼지는 양을 줄인다
        cnt = np.asarray((M > 0).sum(axis=1)).ravel()
        d = d * np.maximum(cnt, 1) ** hubpow
    d[d == 0] = 1.0
    P = sp.diags(1.0 / d) @ M
    return P.T.tocsr()                           # p <- P^T p


def ppr_all(PT, starts, alpha=0.3, iters=30):
    S = np.zeros((N, len(starts)))
    for j, s in enumerate(starts): S[idx[s], j] = 1.0
    p = S.copy()
    for _ in range(iters):
        p = alpha * S + (1 - alpha) * (PT @ p)
    return p


def rank_ppr(PT, alpha=0.3, hubpow=0.0, limit=100, cases_=None, iters=30):
    ranked = {}
    for c in (cases_ or cases):
        A = [a for a, _, _ in c["anchors"]]
        Aset = set(A)
        P = ppr_all(PT, A, alpha, iters)
        lists = []
        for j, a in enumerate(A):
            col = P[:, j].copy(); col[~unit_mask] = -1
            for x in Aset: col[idx[x]] = -1
            top = np.argsort(-col)[:limit]
            lists.append([names[i] for i in top if col[i] > 1e-9])
        ranked[c["id"]] = T.pool(lists, limit)
    return ranked


def recall(ranked, k, cs=None):
    cs = cs or cases
    tot = sum(len(c["wanted"]) for c in cs)
    return sum(len(set(c["wanted"]) & set(ranked[c["id"]][:k])) for c in cs) / tot


def rep(name, ranked, ks=(5, 10, 15, 20), cs=None):
    cs = cs or cases
    tot = sum(len(c["wanted"]) for c in cs)
    cells = []
    for k in ks:
        h = sum(len(set(c["wanted"]) & set(ranked[c["id"]][:k])) for c in cs)
        cells.append(f"{h} ({h/tot:.0%})")
    return f"| {name} | " + " | ".join(cells) + " |"


W = dict(T.W)
