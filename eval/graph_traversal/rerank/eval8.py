"""8차: 판단 모델 5개(카카오, jina, d3-nano, hs-decide, Jev-Style)와 순위 합치기 조합 비교. 후보 풀은 6차와 같다(PR12 25, 팀 50).
사용: python eval8.py <scratchpad>"""
import sys, os, pickle, collections
from pathlib import Path
import numpy as np
NS = Path(sys.argv[1]); sys.stdout.reconfigure(encoding="utf-8")
D = pickle.load(open(NS / "pairs6.pkl", "rb")); items = D["items"]
for x in items["pr12"]: x["docs"] = x["docs"][:25]
cases2 = pickle.load(open(NS / "tests4.pkl", "rb"))["cases2"]; cases = pickle.load(open(NS / "cases.pkl", "rb"))
cs_of = {"pr12": cases, "team": cases2}
MODELS = {"kananaS": "카카오 4단계", "jina": "jina-v3", "d3": "d3-nano", "hsS": "hs-decide", "js": "Jev-Style 2B"}
SC = {m: pickle.load(open(NS / f"scores6_{m}.pkl", "rb")) for m in MODELS}
for m in MODELS: print(m, "점수 수", len(SC[m]))

def pool(lists, limit=200):
    out, seen = [], set()
    for i in range(max(map(len, lists), default=0)):
        for l in lists:
            if i < len(l) and l[i] not in seen:
                seen.add(l[i]); out.append(l[i])
                if len(out) >= limit: return out
    return out
def rrf(ranks, k=60):
    sc = collections.defaultdict(float)
    for r in ranks:
        for i, x in enumerate(r, 1): sc[x] += 1 / (k + i)
    return sorted(sc, key=lambda x: (-sc[x], x))
def rr(s, m, x, i): return sorted(x["docs"], key=lambda d: -SC[m].get((s, i, d), -1e9))
S = collections.OrderedDict()
S["그래프만"] = lambda s, i, x: x["docs"]
for m, n in MODELS.items(): S[n + "만"] = (lambda m: lambda s, i, x: rr(s, m, x, i))(m)
def combo(name, ms, graph=True):
    S[name] = lambda s, i, x: rrf(([x["docs"]] if graph else []) + [rr(s, m, x, i) for m in ms])
combo("그래프+카카오+jina (이전 권장)", ["kananaS", "jina"])
combo("그래프+d3", ["d3"])
combo("그래프+d3+카카오", ["d3", "kananaS"])
combo("그래프+d3+카카오+jina", ["d3", "kananaS", "jina"])
combo("그래프+5모델 전부", list(MODELS))
combo("5모델 전부(그래프 없이)", list(MODELS), graph=False)
def recall(s, fn, k=None, N=None):
    per = collections.defaultdict(list)
    for i, x in enumerate(items[s]): per[x["case"]].append(fn(s, i, x))
    cs = cs_of[s]; tot = sum(len(c["wanted"]) for c in cs)
    if N: keep = {c: set(sum([l[:N] for l in v], [])) for c, v in per.items()}; hit = sum(len(set(c["wanted"]) & keep[c["id"]]) for c in cs); size = np.mean([len(keep[c["id"]]) for c in cs])
    else: rk = {c: pool(v) for c, v in per.items()}; hit = sum(len(set(c["wanted"]) & set(rk[c["id"]][:k])) for c in cs); size = None
    return hit / tot, size
print("\n## 앵커당 상위 N 재현율 (PR12 / 팀), 두 평가셋 평균")
print("| 방법 | N=5 PR12 | N=5 팀 | N=10 PR12 | N=10 팀 | N=10 평균 | N=20 평균 |"); print("|---|---|---|---|---|---|---|")
for n, fn in S.items():
    r = {(s, N): recall(s, fn, N=N)[0] for s in ("pr12", "team") for N in (5, 10, 20)}
    print(f"| {n} | {r[('pr12',5)]:.0%} | {r[('team',5)]:.0%} | {r[('pr12',10)]:.0%} | {r[('team',10)]:.0%} | {(r[('pr12',10)]+r[('team',10)])/2:.0%} | {(r[('pr12',20)]+r[('team',20)])/2:.0%} |")
print("\n## 케이스 단위 상위 k 재현율, 두 평가셋 평균")
print("| 방법 | @5 | @10 | @15 | @20 |"); print("|---|---|---|---|---|")
for n, fn in S.items():
    print(f"| {n} | " + " | ".join(f"{(recall('pr12', fn, k=k)[0] + recall('team', fn, k=k)[0]) / 2:.0%}" for k in (5, 10, 15, 20)) + " |")
