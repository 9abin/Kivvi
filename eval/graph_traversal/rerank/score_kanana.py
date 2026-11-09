"""카카오 판단 모델(kanana-2-v-decide-3b-2611)로 (질의, 후보) 쌍 점수 매기기. 질문 둘: 예/아니오 확률(noul), 4단계 점수의 기대값(score).
점수 저장: scores6_kananaN.pkl (noul), scores6_kananaS.pkl (4단계 기대값). 중간 저장으로 이어 돌릴 수 있다."""
import sys, os, json, time, pickle
os.environ["HF_HUB_OFFLINE"] = "1"
import torch
from pathlib import Path
from huggingface_hub import snapshot_download
sys.stdout.reconfigure(encoding="utf-8")
NS = Path(sys.argv[1]); LIMIT = int(os.environ.get("LIMIT", "0"))
D = pickle.load(open(NS / "pairs6.pkl", "rb")); items, texts = D["items"], D["texts"]
from transformers import AutoModel
path = snapshot_download("kakaocorp/kanana-2-v-decide-3b-2611")
model = AutoModel.from_pretrained(path, trust_remote_code=True, dtype=torch.bfloat16).to("cuda").eval()
Q = {
    "N": {"type": "noul", "instructions": "후보 조항은 질의의 개정을 할 때 함께 확인하거나 고쳐야 하는 조항인가? (같은 사항의 값이 어긋나거나, 개정된 조항을 인용하거나, 같은 용어를 정의 사용하는 경우)"},
    "S": {"type": "score", "instructions": "개정된 조항을 고칠 때 후보 조항을 함께 확인해야 하는 정도는?", "criteria": ["무관", "약한 관련", "확인 권장", "반드시 함께 수정"]},
}
outN, outS = {}, {}
fN, fS = NS / "scores6_kananaN.pkl", NS / "scores6_kananaS.pkl"
if fN.exists(): outN = pickle.load(open(fN, "rb")); outS = pickle.load(open(fS, "rb"))
t0 = time.time(); n = 0
for s, v in [("team", items["team"]), ("pr12", items["pr12"])]:
    for i, x in enumerate(v[:LIMIT] if LIMIT else v):
        for d in (x["docs"][:25] if s == "pr12" else x["docs"]):
            key = (s, i, d)
            if key in outN: continue
            r = model.system_one(state={"개정": x["query"], "후보 조항": texts[d]}, questions=Q)["answers"]
            outN[key] = r["N"]["noul"]; outS[key] = r["S"]["score"]
            n += 1
            if n % 500 == 0:
                pickle.dump(outN, open(fN, "wb")); pickle.dump(outS, open(fS, "wb"))
                print(n, len(outN), round(time.time() - t0), "초", flush=True)
pickle.dump(outN, open(fN, "wb")); pickle.dump(outS, open(fS, "wb"))
print("끝", len(outN), round(time.time() - t0), "초", flush=True)
