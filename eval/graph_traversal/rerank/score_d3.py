"""d3-nano (vLLM Semantic Router, Decision 3.0, 2.2B, Apache-2.0) 로컬 점수. 저장소 코드(trust_remote_code)를 읽고 검토한 뒤 실행.
hs-decide-1-flash(score_hs.py)와 같은 질문·후보 풀. 사용: python score_d3.py <scratchpad> [smoke]"""
import sys, os, pickle, time, json
from pathlib import Path
import torch
sys.stdout.reconfigure(encoding="utf-8")
NS = Path(sys.argv[1]); MODE = sys.argv[2] if len(sys.argv) > 2 else "full"
from transformers import AutoModel
m = AutoModel.from_pretrained(NS / "d3model", trust_remote_code=True)
REL = {"type": "score", "instructions": "위 개정을 하면 후보 조항을 함께 확인하거나 고쳐야 하는 정도",
       "criteria": ["개정과 무관한 조항", "같은 주제를 다루지만 개정이 후보 내용에 영향을 주지 않음",
                    "개정 내용과 충돌하거나 어긋날 수 있어 확인이 권장됨", "개정하면 후보 조항도 반드시 함께 수정해야 함(같은 값·인용·정의가 걸림)"]}

if MODE == "smoke":
    r = m.system_one(state="The order arrived damaged yesterday. The customer has a receipt and asks for a replacement today.",
        questions={"route": {"type": "choice", "instructions": "Which team should handle this request?", "criteria": {"returns": "Refunds, replacements and damaged deliveries", "billing": "Payments, invoices and charges", "technical": "Product setup and faults"}},
                   "receipt": {"type": "noul", "instructions": "Does the customer have a receipt?"}})
    print(json.dumps(r["answers"], ensure_ascii=False)[:600])
    r = m.system_one(state={"개정": "휴가 일수 15일 → 20일", "후보 조항": "연차휴가는 15일로 한다."}, questions={"rel": REL})
    print(r["answers"]["rel"]["score"], r["answers"]["rel"]["probabilities"])
    sys.exit()

D = pickle.load(open(NS / "pairs6.pkl", "rb")); items, texts = D["items"], D["texts"]
OUT = NS / "scores6_d3.pkl"
scores = pickle.load(open(OUT, "rb")) if OUT.exists() else {}
LIMIT = int(os.environ.get("LIMIT", "0")); t0 = time.time(); n = 0
for s in ("team", "pr12"):
    for i, x in enumerate(items[s][:LIMIT] if LIMIT else items[s]):
        for d in (x["docs"][:25] if s == "pr12" else x["docs"]):
            if (s, i, d) in scores: continue
            r = m.system_one(state={"개정": x["query"], "후보 조항": texts[d][:1200]}, questions={"rel": REL})
            scores[(s, i, d)] = r["answers"]["rel"]["score"]; n += 1
            if n % 500 == 0: pickle.dump(scores, open(OUT, "wb")); print(n, round(time.time() - t0), "초", flush=True)
pickle.dump(scores, open(OUT, "wb")); print("끝", len(scores), round(time.time() - t0), "초")
