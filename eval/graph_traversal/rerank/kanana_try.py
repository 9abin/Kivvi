import sys, os, json, time, pickle
os.environ["HF_HUB_OFFLINE"] = "1"
import torch
from pathlib import Path
from huggingface_hub import snapshot_download
sys.stdout.reconfigure(encoding="utf-8")
dev = sys.argv[2] if len(sys.argv) > 2 else "cpu"
NS = Path(sys.argv[1])
D = pickle.load(open(NS / "pairs6.pkl", "rb")); items, texts = D["items"], D["texts"]
x = items["pr12"][0]
path = snapshot_download("kakaocorp/kanana-2-v-decide-3b-2611")
from transformers import AutoModel
t0 = time.time()
model = AutoModel.from_pretrained(path, trust_remote_code=True, dtype=torch.bfloat16).to(dev).eval()
print("로드", round(time.time() - t0), "초", flush=True)
Q = {
    "함께확인": {"type": "noul", "instructions": "후보 조항은 질의의 개정을 할 때 함께 확인하거나 고쳐야 하는 조항인가? (같은 사항의 값이 어긋나거나, 개정된 조항을 인용하거나, 같은 용어를 정의 사용하는 경우)"},
    "관련도": {"type": "score", "instructions": "개정된 조항을 고칠 때 후보 조항을 함께 확인해야 하는 정도는?", "criteria": ["무관", "약한 관련", "확인 권장", "반드시 함께 수정"]},
}
for d in x["docs"][:3]:
    t0 = time.time()
    state = {"개정": x["query"], "후보 조항": texts[d]}
    r = model.system_one(state=state, questions=Q)
    print(d, d in x["wanted"], round(time.time() - t0, 1), "초")
    print(json.dumps(r["answers"], ensure_ascii=False)[:600])
