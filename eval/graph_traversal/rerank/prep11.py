"""12차 3단계 준비(신규 평가셋 30건): 합친 구성(S8)의 앵커별 상위 50을 후보 풀로 (질의, 후보) 쌍을 만든다.
사용: python prep11.py <scratchpad>   (kit_eval.py 를 먼저 돌려 fullsys_pa_kit.pkl 을 만든다)"""
import sys, pickle
from pathlib import Path
NS = Path(sys.argv[1]); sys.stdout.reconfigure(encoding="utf-8")
PA = pickle.load(open(NS / "fullsys_pa_kit.pkl", "rb"))["4도메인 신규 30건"]
cases = {c["id"]: c for c in pickle.load(open(NS / "fullsys_kit.pkl", "rb"))["sets"]["4도메인 신규 30건"]}
texts = pickle.load(open(NS / "pairs6.pkl", "rb"))["texts"]
M = "S8 S3 / 도메인 규칙 번갈아 → PageRank"
items = []
for cid, c in cases.items():
    a = c["anchors"][0][0]
    docs = [d for d in PA[cid][M].get(a, []) if d in texts][:50]
    items.append({"case": cid, "anchor": a, "kind": c["kind"], "query": f"개정: {c['amend']}\n개정된 조항: {texts.get(a, a)[:700]}", "docs": docs, "wanted": c["wanted"]})
pickle.dump({"items": {"kit": items}, "texts": texts}, open(NS / "pairs11.pkl", "wb"))
print("앵커", len(items), "쌍", sum(len(x["docs"]) for x in items), "풀 안 정답", sum(len(set(x["wanted"]) & set(x["docs"])) for x in items), "/", sum(len(x["wanted"]) for x in items))
