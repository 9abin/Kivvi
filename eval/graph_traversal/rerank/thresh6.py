"""찌꺼기 거르기: 점수 기준선(또는 상위 개수)으로 후보를 잘랐을 때 남는 후보 수와 지켜지는 정답 비율.
그래프 순위(F)가 만든 풀을 점수로 줄인다. 단위는 케이스(앵커별 후보를 합친 뒤)."""
import sys, os, pickle, glob, re, collections
from pathlib import Path
import numpy as np
NS = Path(sys.argv[1]); MODELS = sys.argv[2].split(",")
sys.stdout.reconfigure(encoding="utf-8")
D = pickle.load(open(NS / "pairs6.pkl", "rb")); items = D["items"]
P12 = int(os.environ.get("PR12POOL", "25"))
for x in items["pr12"]: x["docs"] = x["docs"][:P12]
t4 = pickle.load(open(NS / "tests4.pkl", "rb")); cases2 = t4["cases2"]
cases = pickle.load(open(NS / "cases.pkl", "rb"))
cs_of = {"pr12": cases, "team": cases2}
SC = {m: pickle.load(open(NS / f"scores6_{m}.pkl", "rb")) for m in MODELS}


def z(vals):
    a = np.array(vals, float); sd = a.std() or 1.0
    return (a - a.mean()) / sd


def table(sname):
    cs = cs_of[sname]
    tot = sum(len(c["wanted"]) for c in cs)
    xs = items[sname]
    by_case = collections.defaultdict(list)
    for i, x in enumerate(xs): by_case[x["case"]].append((i, x))
    poolsz = np.mean([sum(len(x["docs"]) for _, x in by_case[c["id"]]) for c in cs])
    print(f"\n### {sname}: 케이스당 풀 평균 {poolsz:.0f}개 (앵커별 풀 합), 정답 {tot}")
    print("| 점수 | 자르는 방법 | 남는 후보(케이스 평균) | 지켜진 정답 | 풀에 있던 정답 대비 |")
    print("|---|---|---|---|---|")
    pooled_in = sum(len(set(c["wanted"]) & set(d for _, x in by_case[c["id"]] for d in x["docs"])) for c in cs)
    for m in MODELS:
        s = SC[m]
        allv = [s[(sname, i, d)] for i, x in enumerate(xs) for d in x["docs"] if (sname, i, d) in s]
        if not allv: continue
        for mode, arg in (("상위 N(앵커별)", 5), ("상위 N(앵커별)", 10), ("상위 N(앵커별)", 15), ("분위수 이상", 0.5), ("분위수 이상", 0.7)):
            kept_n, hit = [], 0
            for c in cs:
                keep = set()
                for i, x in by_case[c["id"]]:
                    sc = {d: s.get((sname, i, d), -1e9) for d in x["docs"]}
                    if not sc: continue
                    if mode.startswith("상위"):
                        keep |= set(sorted(sc, key=lambda d: -sc[d])[:arg])
                    else:
                        thr = np.quantile(list(sc.values()), arg)
                        keep |= {d for d, v in sc.items() if v >= thr}
                kept_n.append(len(keep)); hit += len(set(c["wanted"]) & keep)
            print(f"| {m} | {mode} {arg} | {np.mean(kept_n):.0f} | {hit} ({hit/tot:.0%}) | {hit/pooled_in:.0%} |")


for s in ("team", "pr12"):
    table(s)
