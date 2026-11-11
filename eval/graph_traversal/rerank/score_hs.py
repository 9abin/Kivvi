"""hs-decide-1-flash (Hindsight, 0.75B, Apache-2.0) 로컬 점수. vLLM 없이 transformers로 읽는다.
공식 serving 코드(typed_render, schema)로 입력을 만들고, 질문마다 ':' 위치의 LM head 로짓을 읽어 softmax한다.
사용: python score_hs.py <scratchpad> smoke   (예시 한 건으로 형식 확인)
      python score_hs.py <scratchpad>         (평가셋 전체, 카카오 kananaS와 같은 질문)"""
import sys, os, pickle, time
from pathlib import Path
import torch
sys.stdout.reconfigure(encoding="utf-8")
NS = Path(sys.argv[1]); MODE = sys.argv[2] if len(sys.argv) > 2 else "full"
sys.path.insert(0, str(NS / "hs" / "serving"))
from hindsight_decision import schema
from hindsight_decision.prompt import load_settings
from hindsight_decision.typed import classifier_ids, question_logits, slot_rows, slot_token
from hindsight_decision.typed_render import render_request
from transformers import AutoModelForCausalLM, AutoTokenizer

MD = NS / "hs" / "model"
tok = AutoTokenizer.from_pretrained(MD)
st = load_settings(MD)
m = AutoModelForCausalLM.from_pretrained(MD, dtype=torch.bfloat16).to("cuda").eval()
ids = classifier_ids(tok); slot_id = slot_token(tok)
cls_ids = torch.tensor(ids, device="cuda")


@torch.no_grad()
def decide(req):
    r = render_request(tok, req, st)
    x = torch.tensor([r.ids], device="cuda")
    h = m.model(input_ids=x).last_hidden_state[0]
    pos = [i for i, t in enumerate(r.ids) if t == slot_id]
    rows = (h[pos] @ m.lm_head.weight[cls_ids].T).float().cpu().tolist()   # 슬롯 위치의 후보 토큰 로짓만
    sel = slot_rows(r, slot_id, len(pos))
    out = {}
    for q, lg in zip(r.questions, question_logits([rows[i] for i in sel], r, ids)):
        out[q.id] = schema.answer(req.questions[q.id], schema.softmax(lg))
    return out


def Q(instr, levels):
    return {"type": "score", "instructions": instr, "criteria": levels}


REL = Q("위 개정을 하면 후보 조항을 함께 확인하거나 고쳐야 하는 정도",
        ["개정과 무관한 조항", "같은 주제를 다루지만 개정이 후보 내용에 영향을 주지 않음",
         "개정 내용과 충돌하거나 어긋날 수 있어 확인이 권장됨", "개정하면 후보 조항도 반드시 함께 수정해야 함(같은 값·인용·정의가 걸림)"])

if MODE == "smoke":
    req = schema.SystemOneRequest.model_validate({"state": {"query": "When is the team offsite?", "documents": {"D00": "The offsite moved from April 10 to April 17.", "D01": "The team offsite is on April 10 in Denver."}},
        "questions": {"latest": {"type": "choice", "instructions": "Which document gives the current offsite date?", "criteria": {"D00": None, "D01": None}},
                      "moved": {"type": "noul", "instructions": "Did the offsite date change?"},
                      "D01": Q("How well does document `documents.D01` answer `query`?", ["about something else", "same topic, no help", "helps", "answers"])}})
    print("공식 예시 기대값: latest=D00, moved≈0.97, D01 score≈1.4")
    for k, v in decide(req).items(): print(k, {a: b for a, b in v.items() if a in ("choice", "noul", "score", "probabilities")})
    sys.exit()

D = pickle.load(open(NS / "pairs6.pkl", "rb")); items, texts = D["items"], D["texts"]
OUT = NS / "scores6_hsS.pkl"
scores = pickle.load(open(OUT, "rb")) if OUT.exists() else {}
LIMIT = int(os.environ.get("LIMIT", "0")); t0 = time.time(); n = 0
for s in ("team", "pr12"):
    for i, x in enumerate(items[s][:LIMIT] if LIMIT else items[s]):
        for d in (x["docs"][:25] if s == "pr12" else x["docs"]):
            if (s, i, d) in scores: continue
            req = schema.SystemOneRequest.model_validate({"state": {"개정": x["query"], "후보 조항": texts[d][:1200]}, "questions": {"rel": REL}})
            scores[(s, i, d)] = decide(req)["rel"]["score"]; n += 1
            if n % 500 == 0: pickle.dump(scores, open(OUT, "wb")); print(n, round(time.time() - t0), "초", flush=True)
pickle.dump(scores, open(OUT, "wb")); print("끝", len(scores), round(time.time() - t0), "초")
