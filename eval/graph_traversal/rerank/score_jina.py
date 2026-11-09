"""jina-reranker-v3 (listwise, CC-BY-NC) 점수. 앵커마다 후보를 25개씩 묶어 한 번에 순위를 매긴다. PR12는 후보 풀 25, 팀은 50."""
import sys, os, pickle, time
os.environ["HF_HUB_OFFLINE"] = "1"
import torch
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
NS = Path(sys.argv[1]); LIMIT = int(os.environ.get("LIMIT", "0"))
D = pickle.load(open(NS / "pairs6.pkl", "rb")); items, texts = D["items"], D["texts"]
from transformers import AutoModel
m = AutoModel.from_pretrained("jinaai/jina-reranker-v3", dtype=torch.float16, trust_remote_code=True).to("cuda").eval()
scores = {}; t0 = time.time(); n = 0
for s, v in [("team", items["team"]), ("pr12", items["pr12"])]:
    for i, x in enumerate(v[:LIMIT] if LIMIT else v):
        docs = x["docs"][:25] if s == "pr12" else x["docs"]
        for b in range(0, len(docs), 25):
            chunk = docs[b:b + 25]
            res = m.rerank(x["query"], [texts[d] for d in chunk])
            # 결과는 점수 순 정렬. 원래 위치(index)를 돌려주는지 확인해서 매핑
            for r in res:
                idx = r.get("index")
                if idx is None: raise SystemExit("index 없음: " + str(r.keys()))
                scores[(s, i, chunk[idx])] = float(r["relevance_score"])
        n += 1
        if n % 40 == 0: print(n, len(scores), round(time.time() - t0), "초", flush=True)
pickle.dump(scores, open(NS / "scores6_jina.pkl", "wb"))
print("끝", len(scores), round(time.time() - t0), "초", flush=True)
