"""11차 3단계 준비: 합친 구성(S8: RAG + 규칙 그래프 / 도메인 규칙 번갈아 → PageRank)의 앵커별 상위 N을 후보 풀로 (질의, 후보) 쌍 만들기.
PR12 80문항은 6차(prep6)와 같은 앵커 순서·질의를 쓰므로 이미 매긴 점수를 그대로 이어 쓴다. 풀 크기: PR12 25, PR #11 도메인 한정 50.
사용: python prep10.py <scratchpad>   (fullsys_eval.py 를 먼저 돌려 fullsys_pa.pkl 을 만든다)"""
import sys, pickle, json, collections
from pathlib import Path
NS = Path(sys.argv[1]); sys.stdout.reconfigure(encoding="utf-8")
PA = pickle.load(open(NS / "fullsys_pa.pkl", "rb"))
b6 = pickle.load(open(NS / "pairs6.pkl", "rb")); b9 = pickle.load(open(NS / "pairs9.pkl", "rb"))
texts = dict(b6["texts"]); texts.update({k: v for k, v in b9["texts"].items() if k not in texts})
M = "S8 S3 / 도메인 규칙 번갈아 → PageRank"
items = {"pr12": [], "pr11d": []}
for x in b6["items"]["pr12"]:
    docs = PA["PR12 80문항"][x["case"]][M].get(x["anchor"], [])
    items["pr12"].append({**x, "docs": [d for d in docs if d in texts][:25]})
for x in b9["items"]["pr11"]:
    if x["case"] not in PA["PR #11 도메인 한정"]: continue
    docs = PA["PR #11 도메인 한정"][x["case"]][M].get(x["anchor"], [])
    items["pr11d"].append({**x, "docs": [d for d in docs if d in texts][:50]})
pickle.dump({"items": items, "texts": texts}, open(NS / "pairs10.pkl", "wb"))
for k, v in items.items(): print(k, "앵커", len(v), "쌍", sum(len(x["docs"]) for x in v))
# 이미 매긴 점수 이어 쓰기 (PR12는 같은 앵커 번호 i와 후보 d)
for m, files in (("d3", ["scores6_d3.pkl"]), ("kanana", ["scores6_kananaN.pkl", "scores6_kananaS.pkl"])):
    for f in files:
        old = pickle.load(open(NS / f, "rb")); new = {}
        for i, x in enumerate(items["pr12"]):
            for d in x["docs"]:
                if ("pr12", i, d) in old: new[("pr12", i, d)] = old[("pr12", i, d)]
        pickle.dump(new, open(NS / f.replace(".pkl", "_fs.pkl"), "wb"))
        print(f, "이어 쓸 점수", len(new))
todo = sum(1 for i, x in enumerate(items["pr12"]) for d in x["docs"]) - len(new)
print("PR12 새로 매길 쌍 약", todo, "+ PR #11", sum(len(x["docs"]) for x in items["pr11d"]))
