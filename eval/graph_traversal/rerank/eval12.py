"""12차 4단계: 신규 평가셋 30건에서 합친 구성(RAG + 규칙 그래프 + 도메인 그래프, S8 순서)의 후보 풀을 판단 모델(d3-nano, 카카오)로 다시 줄 세운 결과.
사용: python eval12.py <scratchpad>"""
import sys, pickle, collections
from pathlib import Path
import numpy as np
NS = Path(sys.argv[1]); sys.stdout.reconfigure(encoding="utf-8")
P = pickle.load(open(NS / "pairs11.pkl", "rb"))["items"]["kit"]
cases = {c["id"]: c for c in pickle.load(open(NS / "fullsys_kit.pkl", "rb"))["sets"]["4도메인 신규 30건"]}
SC = {"카카오": pickle.load(open(NS / "scores6_kananaS_kit.pkl", "rb")), "d3-nano": pickle.load(open(NS / "scores6_d3_kit.pkl", "rb"))}
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
def rr(m, i, x): return sorted(x["docs"], key=lambda d: -SC[m].get(("kit", i, d), -1e9))
S = collections.OrderedDict()
S["합친 그래프 순서"] = lambda i, x: x["docs"]
S["카카오만"] = lambda i, x: rr("카카오", i, x)
S["d3-nano만"] = lambda i, x: rr("d3-nano", i, x)
S["그래프 + 카카오"] = lambda i, x: rrf([x["docs"], rr("카카오", i, x)])
S["그래프 + d3-nano"] = lambda i, x: rrf([x["docs"], rr("d3-nano", i, x)])
S["그래프 + d3-nano + 카카오"] = lambda i, x: rrf([x["docs"], rr("d3-nano", i, x), rr("카카오", i, x)])
print("점수 못 매긴 쌍:", {m: sum(1 for i, x in enumerate(P) for d in x["docs"] if ("kit", i, d) not in SC[m]) for m in SC})
subs = collections.OrderedDict([("전체 30건", lambda c: True), ("개발 12건", lambda c: c["split"] == "개발"), ("봉인 18건", lambda c: c["split"] == "봉인"), ("D07 제외 29건", lambda c: c["id"] != "D07")])
ranked = {n: {x["case"]: fn(i, x) for i, x in enumerate(P)} for n, fn in S.items()}
for nm, f in subs.items():
    cs = [c for cid, c in cases.items() if f(c)]; tot = sum(len(c["wanted"]) for c in cs); withw = [c for c in cs if c["wanted"]]
    print(f"\n### {nm}: 문항 {len(cs)} (정답 있는 {len(withw)}), 정답 {tot}쌍")
    print("| 방법 | @5 | @10 | @20 | @30 | 문항 평균 @10 / @20 | 앵커당 10개 남길 때 정답 (후보) |"); print("|---|---|---|---|---|---|---|")
    for n, rk in ranked.items():
        cell = [f"{sum(len(set(c['wanted']) & set(rk[c['id']][:k])) for c in cs)/tot:.0%}" for k in (5, 10, 20, 30)]
        mac = lambda k: np.mean([len(set(c["wanted"]) & set(rk[c["id"]][:k])) / len(c["wanted"]) for c in withw])
        n10 = sum(len(set(c["wanted"]) & set(rk[c["id"]][:10])) for c in cs) / tot
        print(f"| {n} | " + " | ".join(cell) + f" | {mac(10):.0%} / {mac(20):.0%} | {n10:.0%} (10) |")
