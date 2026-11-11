"""7차 평가: (1) 조합 후보 비교, (2) 후보 개수 N별 재현율과 정밀도, (3) 정상 변경 문항에서 점수 기준선이 거짓 경보를 줄이는가."""
import sys, os, pickle, collections, re
from pathlib import Path
import numpy as np
NS = Path(sys.argv[1]); sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))
D = pickle.load(open(NS / "pairs6.pkl", "rb")); items = D["items"]
P12 = int(os.environ.get("PR12POOL", "25"))
for x in items["pr12"]: x["docs"] = x["docs"][:P12]
t4 = pickle.load(open(NS / "tests4.pkl", "rb")); cases2 = t4["cases2"]
cases = pickle.load(open(NS / "cases.pkl", "rb"))
cs_of = {"pr12": cases, "team": cases2}
names = {"pr12": "PR12 80문항", "team": "팀 5~7차 36문항"}
SC = {m: pickle.load(open(NS / f"scores6_{m}.pkl", "rb")) for m in ("kananaS", "kananaN", "jina", "bge")}


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


def rr(s, m, x, i): return sorted(x["docs"], key=lambda d: -SC[m].get((s, i, d), -1e9))


STR = collections.OrderedDict()
STR["그래프만"] = lambda s, i, x: x["docs"]
STR["카카오4단계만"] = lambda s, i, x: rr(s, "kananaS", x, i)
STR["jina만"] = lambda s, i, x: rr(s, "jina", x, i)
STR["그래프+카카오 (RRF)"] = lambda s, i, x: rrf([x["docs"], rr(s, "kananaS", x, i)])
STR["카카오+jina (RRF)"] = lambda s, i, x: rrf([rr(s, "kananaS", x, i), rr(s, "jina", x, i)])
STR["그래프+카카오+jina (RRF)"] = lambda s, i, x: rrf([x["docs"], rr(s, "kananaS", x, i), rr(s, "jina", x, i)])
STR["그래프2배+카카오+jina (RRF)"] = lambda s, i, x: rrf([x["docs"], rr(s, "kananaS", x, i), rr(s, "jina", x, i)], w=[2, 1, 1])


def build(s, fn):
    per = collections.defaultdict(list)
    for i, x in enumerate(items[s]): per[x["case"]].append(fn(s, i, x))
    return {c: pool(l) for c, l in per.items()}


def rec(s, rk, k):
    cs = cs_of[s]; tot = sum(len(c["wanted"]) for c in cs)
    return sum(len(set(c["wanted"]) & set(rk[c["id"]][:k])) for c in cs) / tot


R = {}
print("## (1) 조합 비교 (상위 5 / 10 / 15 / 20)")
for s in ("pr12", "team"):
    print(f"\n### {names[s]}")
    print("| 방법 | @5 | @10 | @15 | @20 |"); print("|---|---|---|---|---|")
    for n, fn in STR.items():
        rk = build(s, fn); R[(s, n)] = rk
        print(f"| {n} | " + " | ".join(f"{rec(s, rk, k):.0%}" for k in (5, 10, 15, 20)) + " |")
print("\n두 평가셋 평균")
print("| 방법 | @5 | @10 | @15 | @20 |"); print("|---|---|---|---|---|")
for n in STR:
    print(f"| {n} | " + " | ".join(f"{(rec('pr12', R[('pr12', n)], k) + rec('team', R[('team', n)], k)) / 2:.0%}" for k in (5, 10, 15, 20)) + " |")

best = "그래프+카카오+jina (RRF)"
print(f"\n## (2) 후보 개수 N별 ({best}): 케이스당 앵커 수가 달라 '앵커당 N개'로 자르고 케이스에서 합침")
print("| 앵커당 N | PR12 재현율 | PR12 정답/후보 | 팀 재현율 | 팀 정답/후보 | 케이스당 후보 수(PR12 / 팀) |"); print("|---|---|---|---|---|---|")
fn = STR[best]
for N in (3, 5, 7, 10, 12, 15, 20, 25):
    row = []; sizes = []
    for s in ("pr12", "team"):
        per = collections.defaultdict(list)
        for i, x in enumerate(items[s]): per[x["case"]].append(fn(s, i, x)[:N])
        keep = {c: set(sum(l, [])) for c, l in per.items()}
        cs = cs_of[s]; tot = sum(len(c["wanted"]) for c in cs)
        hit = sum(len(set(c["wanted"]) & keep[c["id"]]) for c in cs); cand = sum(len(keep[c["id"]]) for c in cs)
        row += [f"{hit/tot:.0%}", f"{hit/cand:.1%}"]; sizes.append(cand / len(cs))
    print(f"| {N} | " + " | ".join(row) + f" | {sizes[0]:.0f} / {sizes[1]:.0f} |")
pickle.dump({k: v for k, v in R.items()}, open(NS / "R7.pkl", "wb"))

# (3) 정상 변경 거짓 경보 대리 지표
if (NS / "scores7_kananaS.pkl").exists():
    P7 = pickle.load(open(NS / "pairs7.pkl", "rb"))["items"]["clean"]
    cS = pickle.load(open(NS / "scores7_kananaS.pkl", "rb")); cN = pickle.load(open(NS / "scores7_kananaN.pkl", "rb"))
    done = sum(1 for i, x in enumerate(P7) if all(("clean", i, d) in cS for d in x["docs"]))
    print(f"\n## (3) 정상 변경 40문항 거짓 경보 대리 지표: 점수가 모두 계산된 앵커 {done}/{len(P7)}")
    POOLN = 15
    prob = []   # 문제 있는 문항의 앵커, 같은 풀 15
    for i, x in enumerate(items["pr12"]): prob.append((i, x))
    def anchor_stats(kind):
        out = []
        if kind == "clean":
            for i, x in enumerate(P7):
                ds = x["docs"][:POOLN]
                if all(("clean", i, d) in cS for d in ds): out.append([cS[("clean", i, d)] for d in ds])
        else:
            for i, x in prob:
                ds = x["docs"][:POOLN]
                out.append([SC["kananaS"].get(("pr12", i, d), np.nan) for d in ds])
        return out
    cl, pr = anchor_stats("clean"), anchor_stats("prob")
    print(f"앵커 수: 정상 변경 {len(cl)}, 문제 있는 문항 {len(pr)} (후보 풀 {POOLN})")
    print("| 통계 | 정상 변경 | 문제 있는 문항 |"); print("|---|---|---|")
    for nm, f in (("후보 점수 평균(0~3)", lambda v: np.nanmean(v)), ("후보 중 최대 점수 평균", lambda v: np.nanmax(v)), ("점수 2.0 이상 후보 수(앵커당 평균)", lambda v: np.sum(np.array(v) >= 2.0)), ("점수 1.5 이상 후보 수", lambda v: np.sum(np.array(v) >= 1.5)), ("점수 2.5 이상 후보 수", lambda v: np.sum(np.array(v) >= 2.5))):
        print(f"| {nm} | {np.mean([f(v) for v in cl]):.2f} | {np.mean([f(v) for v in pr]):.2f} |")
    # 앵커 단위 구분력 (문제 있는/없는 앵커를 최대 점수로 가를 때 AUC)
    a = np.array([np.nanmax(v) for v in pr]); b = np.array([np.nanmax(v) for v in cl])
    auc = np.mean([(x > y) + 0.5 * (x == y) for x in a for y in b])
    print(f"앵커 단위 구분력(후보 최대점수로 문제 있는 앵커와 정상 앵커를 가르는 AUC): {auc:.2f}")
    # 정답 쌍 점수 대 비정답 후보 점수 (문제 있는 문항, 후보 풀 15 안)
    pos, neg = [], []
    for i, x in prob:
        for d in x["docs"][:POOLN]:
            s_ = SC["kananaS"].get(("pr12", i, d))
            if s_ is None: continue
            (pos if d in x["wanted"] else neg).append(s_)
    print(f"문제 있는 문항 안에서 정답 후보 평균 점수 {np.mean(pos):.2f} (n={len(pos)}), 비정답 후보 {np.mean(neg):.2f} (n={len(neg)})")
    # 기준선을 정답의 80%를 지키도록 잡았을 때 정상 변경 앵커당 통과 후보 수
    for keep in (0.9, 0.8, 0.7):
        thr = np.quantile(pos, 1 - keep)
        passed_clean = np.mean([np.sum(np.array(v) >= thr) for v in cl]); passed_prob = np.mean([np.sum(np.array(v) >= thr) for v in pr])
        print(f"정답 후보의 {int(keep*100)}%를 통과시키는 기준선 {thr:.2f}: 앵커당 통과 후보 수 정상 변경 {passed_clean:.1f} / 문제 있는 문항 {passed_prob:.1f} (후보 풀 {POOLN} 중)")
