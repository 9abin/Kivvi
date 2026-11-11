"""11차 4단계: 합친 구성(RAG + PR #11 규칙 그래프 + PR #12 도메인 그래프, S8 순서)의 후보 풀을 판단 모델(d3-nano, 카카오)로 다시 줄 세운 결과.
사용: python eval11.py <scratchpad>"""
import sys, pickle, collections
from pathlib import Path
NS = Path(sys.argv[1]); sys.stdout.reconfigure(encoding="utf-8")
P = pickle.load(open(NS / "pairs10.pkl", "rb"))["items"]
FS = pickle.load(open(NS / "fullsys.pkl", "rb"))["sets"]
SETS = {"pr12": ("PR12 80문항", FS["PR12 80문항"]), "pr11d": ("PR #11 도메인 한정", FS["PR #11 도메인 한정"])}
SC = {"카카오": pickle.load(open(NS / "scores6_kananaS_fs.pkl", "rb")), "d3-nano": pickle.load(open(NS / "scores6_d3_fs.pkl", "rb"))}
def pool(lists, limit=300):
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
        for j, x in enumerate(r, 1): sc[x] += 1 / (k + j)
    return sorted(sc, key=lambda x: (-sc[x], x))
def rr(s, m, i, x): return sorted(x["docs"], key=lambda d: -SC[m].get((s, i, d), -1e9))
S = collections.OrderedDict()
S["합친 그래프 순서 (RAG + 규칙 + 도메인 + PageRank)"] = lambda s, i, x: x["docs"]
S["카카오만"] = lambda s, i, x: rr(s, "카카오", i, x)
S["d3-nano만"] = lambda s, i, x: rr(s, "d3-nano", i, x)
S["그래프 + 카카오"] = lambda s, i, x: rrf([x["docs"], rr(s, "카카오", i, x)])
S["그래프 + d3-nano"] = lambda s, i, x: rrf([x["docs"], rr(s, "d3-nano", i, x)])
S["그래프 + d3-nano + 카카오"] = lambda s, i, x: rrf([x["docs"], rr(s, "d3-nano", i, x), rr(s, "카카오", i, x)])
miss = {m: sum(1 for s, v in P.items() for i, x in enumerate(v) for d in x["docs"][:25 if s == "pr12" else 50] if (s, i, d) not in SC[m]) for m in SC}
print("점수 못 매긴 쌍:", miss)
for s, (name, cs) in SETS.items():
    tot = sum(len(c["wanted"]) for c in cs)
    pooled = collections.defaultdict(set)
    for x in P[s]: pooled[x["case"]] |= set(x["docs"][:25 if s == "pr12" else 50])
    ceil = sum(len(set(c["wanted"]) & pooled[c["id"]]) for c in cs)
    print(f"\n### {name}: 문항 {len(cs)}, 정답 {tot}쌍, 후보 풀 안 정답 {ceil}쌍 ({ceil/tot:.0%} = 재현율 상한)")
    print("| 방법 | 케이스 @5 | @10 | @20 | @30 | 앵커당 10개만 남길 때 (케이스당 후보 수) |"); print("|---|---|---|---|---|---|")
    for n, fn in S.items():
        per = collections.defaultdict(list)
        for i, x in enumerate(P[s]):
            if x["case"] in pooled: per[x["case"]].append(fn(s, i, x))
        rk = {c: pool(v) for c, v in per.items()}
        cell = [f"{sum(len(set(c['wanted']) & set(rk[c['id']][:k])) for c in cs)/tot:.0%}" for k in (5, 10, 20, 30)]
        keep = {c: set(sum([l[:10] for l in v], [])) for c, v in per.items()}
        n10 = sum(len(set(c["wanted"]) & keep[c["id"]]) for c in cs) / tot; sz = sum(len(v) for v in keep.values()) / len(keep)
        print(f"| {n} | " + " | ".join(cell) + f" | {n10:.0%} ({sz:.0f}) |")
