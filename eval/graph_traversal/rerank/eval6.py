"""리랭커 평가: 후보 풀 50 -> 상위 k. 모델별, 그래프 순위와 합친 방식별로 재현율과 '정답을 90% 담기 위해 필요한 후보 수'를 잰다."""
import sys, pickle, glob, collections, re
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
NS = Path(sys.argv[1])
sys.stdout.reconfigure(encoding="utf-8")
import os
D = pickle.load(open(NS / "pairs6.pkl", "rb")); items = D["items"]
P12 = int(os.environ.get("PR12POOL", "50"))
for x in items["pr12"]: x["docs"] = x["docs"][:P12]
t4 = pickle.load(open(NS / "tests4.pkl", "rb")); cases2 = t4["cases2"]
cases = pickle.load(open(NS / "cases.pkl", "rb"))
cs_of = {"pr12": cases, "team": cases2}
names = {"pr12": "PR12 80문항", "team": "팀 5~7차 36문항"}
SC = {}
for f in sorted(glob.glob(str(NS / "scores6_*.pkl"))):
    SC[re.search(r"scores6_(.*)\.pkl", f).group(1)] = pickle.load(open(f, "rb"))
print("점수가 있는 모델:", list(SC))


def pool(lists, limit=200):
    out, seen = [], set()
    for i in range(max(map(len, lists), default=0)):
        for l in lists:
            if i < len(l) and l[i] not in seen:
                seen.add(l[i]); out.append(l[i])
                if len(out) >= limit: return out
    return out


def rrf(ranks, w=None, k=60):
    sc = collections.defaultdict(float)
    for j, r in enumerate(ranks):
        for i, x in enumerate(r, 1): sc[x] += (w[j] if w else 1) / (k + i)
    return sorted(sc, key=lambda x: (-sc[x], x))


def rerank(setname, model, x, i):
    s = SC[model]
    return sorted(x["docs"], key=lambda d: -s.get((setname, i, d), -1e9))


def build(setname, fn):
    """fn(setname, i, x) -> 앵커의 후보 순위. 케이스 단위로 번갈아 합침."""
    per_case = collections.defaultdict(list)
    for i, x in enumerate(items[setname]):
        per_case[x["case"]].append(fn(setname, i, x))
    return {cid: pool(ls) for cid, ls in per_case.items()}


def recall_at(setname, ranked, k):
    cs = cs_of[setname]
    tot = sum(len(c["wanted"]) for c in cs)
    return sum(len(set(c["wanted"]) & set(ranked[c["id"]][:k])) for c in cs) / tot


def k_needed(setname, ranked, frac=0.9):
    """풀 50 안에 있는 정답의 90%를 담으려면 문항마다 상위 몇 개까지 봐야 하나 (전체 평균, 정답 짝 기준 최소 k의 중앙값/90백분위)."""
    cs = cs_of[setname]
    ks = []
    for c in cs:
        lst = ranked[c["id"]]
        for w in c["wanted"]:
            ks.append(lst.index(w) + 1 if w in lst else 999)
    ks = np.array(ks)
    return int(np.percentile(ks, 50)), int(np.percentile(ks, 90))


strategies = collections.OrderedDict()
strategies["그래프 순위 그대로 (F)"] = lambda s, i, x: x["docs"]
for m in SC:
    strategies[f"[{m}] 리랭커만"] = (lambda mm: lambda s, i, x: rerank(s, mm, x, i))(m)
    strategies[f"[{m}] 그래프+리랭커 RRF"] = (lambda mm: lambda s, i, x: rrf([x["docs"], rerank(s, mm, x, i)]))(m)
    strategies[f"[{m}] 그래프 상위3 보호 + 리랭커"] = (lambda mm: lambda s, i, x: x["docs"][:3] + [d for d in rerank(s, mm, x, i) if d not in set(x["docs"][:3])])(m)
if len(SC) >= 2:
    ms = list(SC)
    strategies["[전 모델 평균 순위] 리랭커만"] = lambda s, i, x: rrf([rerank(s, m, x, i) for m in ms])
    strategies["[전 모델 평균 순위 + 그래프] RRF"] = lambda s, i, x: rrf([x["docs"]] + [rerank(s, m, x, i) for m in ms], w=[len(ms)] + [1] * len(ms))

ks = (5, 10, 15, 20, 30)
res = {}
for sname in ("pr12", "team"):
    _pooled = collections.defaultdict(set)
    for _x in items[sname]: _pooled[_x["case"]] |= set(_x["docs"])
    _in = sum(len(set(c["wanted"]) & _pooled[c["id"]]) for c in cs_of[sname]); _tw = sum(len(c["wanted"]) for c in cs_of[sname])
    print(f"\n### {names[sname]} (후보 풀 {P12 if sname=='pr12' else 50} 안 정답 {_in}/{_tw})")
    print("| 방법 | " + " | ".join(f"@{k}" for k in ks) + " | 정답 짝이 나오는 순위 (중앙값 / 90%) |"); print("|---|" + "---|" * (len(ks) + 1))
    for name, fn in strategies.items():
        try:
            rk = build(sname, fn)
        except KeyError:
            continue
        cells = [f"{recall_at(sname, rk, k):.0%}" for k in ks]
        a, b = k_needed(sname, rk)
        print(f"| {name} | " + " | ".join(cells) + f" | {a} / {b if b < 999 else '풀 밖'} |")
        res[(sname, name)] = [recall_at(sname, rk, k) for k in ks]
pickle.dump(res, open(NS / "res6.pkl", "wb"))
