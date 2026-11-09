"""Jev-Style 2B v3 (개인 개발자 chaoliangUNSW, Apache-2.0, Qwen3.5-2B 기반) 로컬 점수. 저장소의 단일 파일 jev_style_decision.py만 사용(네트워크·서브프로세스 호출 없음을 확인).
score_hs.py, score_d3.py와 같은 질문·후보 풀. 사용: python score_js.py <scratchpad> [smoke]"""
import sys, os, pickle, time
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
NS = Path(sys.argv[1]); MODE = sys.argv[2] if len(sys.argv) > 2 else "full"
sys.path.insert(0, str(NS / "jmodel"))
from jev_style_decision import JevStyleDecision
m = JevStyleDecision(str(NS / "jmodel"), device="cuda", dtype="bfloat16")
LEVELS = ["개정과 무관한 조항", "같은 주제를 다루지만 개정이 후보 내용에 영향을 주지 않음",
          "개정 내용과 충돌하거나 어긋날 수 있어 확인이 권장됨", "개정하면 후보 조항도 반드시 함께 수정해야 함(같은 값·인용·정의가 걸림)"]
INS = "위 개정을 하면 후보 조항을 함께 확인하거나 고쳐야 하는 정도"


def score(state):
    r = m.decide(state, INS, options=LEVELS, qtype="score")
    p = r["probabilities"]
    return sum(int(k) * v for k, v in p.items()), p


if MODE == "smoke":
    r = m.decide({"ticket": "I was charged twice for my subscription this month.", "customer_tier": "pro"}, "Which team should handle this ticket?",
                 options={"billing": "payments, invoices, refunds", "technical": "bugs and outages", "sales": "new purchases"})
    print("공식 예시 기대값: billing 0.976", r["answer"], r["probabilities"])
    print(score({"개정": "휴가 일수 15일 → 20일", "후보 조항": "연차휴가는 15일로 한다."}))
    sys.exit()

D = pickle.load(open(NS / "pairs6.pkl", "rb")); items, texts = D["items"], D["texts"]
OUT = NS / "scores6_js.pkl"
scores = pickle.load(open(OUT, "rb")) if OUT.exists() else {}
LIMIT = int(os.environ.get("LIMIT", "0")); t0 = time.time(); n = 0
for s in ("team", "pr12"):
    for i, x in enumerate(items[s][:LIMIT] if LIMIT else items[s]):
        for d in (x["docs"][:25] if s == "pr12" else x["docs"]):
            if (s, i, d) in scores: continue
            scores[(s, i, d)] = score({"개정": x["query"], "후보 조항": texts[d][:1200]})[0]; n += 1
            if n % 500 == 0: pickle.dump(scores, open(OUT, "wb")); print(n, round(time.time() - t0), "초", flush=True)
pickle.dump(scores, open(OUT, "wb")); print("끝", len(scores), round(time.time() - t0), "초")
