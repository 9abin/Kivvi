"""2차 실험: 방법별 분해, 관계 하나씩 빼 보기, 가중치 학습(관계 묶음 단위 교차검증), 저장된 RAG 순위와 합치기. API 없음."""
import sys, json, re, pickle, collections, random
from pathlib import Path
import numpy as np
import scipy.sparse as sp
sys.path.insert(0, str(Path(__file__).resolve().parent))
import traverse as T  # 같은 argv(NS, pr12data)를 쓴다

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
base = ranked_base = None

# ---------------- E1: 분해 (새 문제 / 해소 / 개정 종류)
out = []
PT = transition(W)
ppr03 = rank_ppr(PT, 0.3)
tests = pickle.load(open(NS / "tests1.pkl", "rb"))
sets = {"새 문제 문항": [c for c in cases if c["type"] == "new"], "해소 문항": [c for c in cases if c["type"] == "resolved"],
        "번호 이동 포함 문항": [c for c in cases if any(k == "번호 이동" for _, k, _ in c["anchors"])],
        "값 변경만 있는 문항": [c for c in cases if all(k == "값 변경" for _, k, _ in c["anchors"])]}
print("## E1 분해 (상위 10 / 15)")
for sname, cs in sets.items():
    tot = sum(len(c["wanted"]) for c in cs)
    print(f"\n{sname}: 문항 {len(cs)}, 정답 짝 {tot}")
    print("| 방법 | @5 | @10 | @15 | @20 |"); print("|---|---|---|---|---|")
    for nm, rk in (("규칙표 1홉", tests["규칙표 1홉 (kg.impact)"]), ("collect(PR12 방식)", tests["collect (PR12 방식)"]),
                   ("전관계 BFS 1홉", tests["전관계 BFS 1홉"]), ("가중 BFS 2홉", tests["가중 BFS 2홉"]), ("PageRank 0.3", ppr03)):
        print(rep(nm, rk, cs=cs))

# ---------------- E2: 관계 하나씩 빼 보기 (PageRank 0.3)
print("\n## E2 관계 하나 빼고 PageRank (상위 10 적중)")
b10 = recall(ppr03, 10) * 177
print(f"전체 관계: {b10:.0f}")
res = []
for rel in sorted(W):
    w2 = {k: v for k, v in W.items() if k != rel}
    r = rank_ppr(transition(w2), 0.3)
    res.append((rel, recall(r, 10) * 177, recall(r, 5) * 177))
for rel, h10, h5 in sorted(res, key=lambda x: x[1]):
    print(f"- {rel} 뺌: @10 {h10:.0f} ({h10-b10:+.0f}), @5 {h5:.0f}")
print("관계 없는 것(W에 없던 것) 추가:")
for rel in [x for x in REL if x not in W]:
    w2 = dict(W); w2[rel] = 0.3
    r = rank_ppr(transition(w2), 0.3)
    print(f"- {rel} 추가(0.3): @10 {recall(r,10)*177:.0f}, @5 {recall(r,5)*177:.0f}")

# ---------------- E3: 가중치 학습 (관계 묶음 단위 교차검증)
gold = {g["id"]: g for g in map(json.loads, open(D / "gold.jsonl", encoding="utf-8"))}
def keys_of(c):
    return {k.split(":")[0] for k in gold[c["id"]]["target_issue_ids"]}
allkeys = sorted(set().union(*(keys_of(c) for c in cases)))
random.seed(0); random.shuffle(allkeys)
folds = {k: i % 5 for i, k in enumerate(allkeys)}
print("\n관계 묶음 수", len(allkeys))


def objective(w, cs, alpha=0.3):
    r = rank_ppr(transition(w), alpha, cases_=cs, limit=30)
    return recall(r, 10, cs) + 0.5 * recall(r, 5, cs)


grid = [0.0, 0.1, 0.3, 0.6, 1.0]
learned_rank = {}
cv_rows = []
for f in range(5):
    test = [c for c in cases if min(folds[k] for k in keys_of(c)) == f]
    train = [c for c in cases if not (keys_of(c) & {k for k in allkeys if folds[k] == f})]
    if not test or not train: continue
    w = {r: 0.3 for r in REL}                    # 모든 관계 0.3에서 시작
    best = objective(w, train)
    for rnd in range(2):
        for rel in REL:
            for g in grid:
                if g == w[rel]: continue
                w2 = dict(w); w2[rel] = g
                o = objective(w2, train)
                if o > best + 1e-9: best, w = o, w2
    r = rank_ppr(transition(w), 0.3, cases_=test)
    r0 = rank_ppr(transition(W), 0.3, cases_=test)
    ru = rank_ppr(transition({x: 0.3 for x in REL}), 0.3, cases_=test)
    cv_rows.append((f, len(train), len(test), sum(len(c["wanted"]) for c in test), recall(r, 10, test), recall(r0, 10, test), recall(ru, 10, test), recall(r, 5, test), recall(r0, 5, test)))
    learned_rank.update(r)
    print(f"fold {f}: 학습 {len(train)}문항, 시험 {len(test)}문항/정답 {cv_rows[-1][3]} | @10 학습한 가중치 {cv_rows[-1][4]:.0%} vs 팀 가중치 {cv_rows[-1][5]:.0%} vs 균등 {cv_rows[-1][6]:.0%} | @5 {cv_rows[-1][7]:.0%} vs {cv_rows[-1][8]:.0%}")
tw = sum(r[3] for r in cv_rows)
print(f"가중 평균 @10: 학습 {sum(r[3]*r[4] for r in cv_rows)/tw:.1%}, 팀 가중치 {sum(r[3]*r[5] for r in cv_rows)/tw:.1%}, 균등 {sum(r[3]*r[6] for r in cv_rows)/tw:.1%}; @5: 학습 {sum(r[3]*r[7] for r in cv_rows)/tw:.1%}, 팀 {sum(r[3]*r[8] for r in cv_rows)/tw:.1%}")
pickle.dump({"ppr03": ppr03}, open(NS / "tests2.pkl", "wb"))
