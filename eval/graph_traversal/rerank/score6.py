"""리랭커 점수 계산: python score6.py <작업폴더> <모델키>. 모든 (질의, 후보) 쌍에 점수를 매겨 scores6_<키>.pkl 로 저장."""
import sys, os, pickle, time, math
from pathlib import Path
os.environ["HF_HUB_OFFLINE"] = os.environ.get("HF_HUB_OFFLINE", "1")
import torch
NS = Path(sys.argv[1]); KEY = sys.argv[2]
sys.stdout.reconfigure(encoding="utf-8")
D = pickle.load(open(NS / "pairs6.pkl", "rb")); items, texts = D["items"], D["texts"]
LIMIT = int(os.environ.get("LIMIT", "0"))   # 시험 실행용: 앵커 몇 개만
MAXLEN = int(os.environ.get("MAXLEN", "512"))
pairs = []   # (set, idx, doc, query, text)
for s, v in items.items():
    for i, x in enumerate(v[:LIMIT] if LIMIT else v):
        for d in x["docs"]:
            pairs.append((s, i, d, x["query"], texts[d]))
print(KEY, "쌍 수", len(pairs), flush=True)
dev = "cuda"
t0 = time.time()
scores = {}

CE = {"bge": "BAAI/bge-reranker-v2-m3", "bgeko": "dragonkue/bge-reranker-v2-m3-ko", "gte": "Alibaba-NLP/gte-multilingual-reranker-base"}
QW = {"qwen06": "Qwen/Qwen3-Reranker-0.6B", "qwen4b": "Qwen/Qwen3-Reranker-4B"}

if KEY in CE:
    from sentence_transformers import CrossEncoder
    m = CrossEncoder(CE[KEY], device=dev, max_length=MAXLEN, trust_remote_code=True, model_kwargs={"torch_dtype": torch.float16})
    order = sorted(range(len(pairs)), key=lambda i: len(pairs[i][3]) + len(pairs[i][4]))
    bs = 32
    for b in range(0, len(order), bs):
        idx = order[b:b + bs]
        out = m.predict([(pairs[i][3], pairs[i][4]) for i in idx], batch_size=bs, show_progress_bar=False)
        for i, sc in zip(idx, out): scores[(pairs[i][0], pairs[i][1], pairs[i][2])] = float(sc)
        if (b // bs) % 50 == 0: print(b, len(order), round(time.time() - t0), flush=True)
elif KEY in QW:
    from transformers import AutoTokenizer, AutoModelForCausalLM
    name = QW[KEY]
    tok = AutoTokenizer.from_pretrained(name, padding_side="left")
    kw = {"torch_dtype": torch.float16}
    if KEY == "qwen4b":
        kw.update(device_map="auto", max_memory={0: "6800MiB", "cpu": "24GiB"})
        m = AutoModelForCausalLM.from_pretrained(name, **kw).eval()
    else:
        m = AutoModelForCausalLM.from_pretrained(name, **kw).to(dev).eval()
    yes, no = tok.convert_tokens_to_ids("yes"), tok.convert_tokens_to_ids("no")
    pre = "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n"
    suf = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    inst = "다른 조항을 개정할 때, 문서(후보 조항)가 질의의 개정된 조항과 함께 고치거나 확인해야 하는 조항(같은 사항의 값이 어긋나거나, 개정된 조항을 인용하거나, 같은 용어를 정의·사용)인지 판단하라"
    fmt = lambda q, d: f"{pre}<Instruct>: {inst}\n<Query>: {q}\n<Document>: {d}{suf}"
    order = sorted(range(len(pairs)), key=lambda i: len(pairs[i][3]) + len(pairs[i][4]))
    bs = 8 if KEY == "qwen06" else 2
    for b in range(0, len(order), bs):
        idx = order[b:b + bs]
        enc = tok([fmt(pairs[i][3], pairs[i][4]) for i in idx], padding=True, truncation=True, max_length=MAXLEN + 256, return_tensors="pt").to(m.device)
        with torch.no_grad():
            lg = m(**enc, logits_to_keep=1).logits[:, -1, :]
        p = torch.softmax(torch.stack([lg[:, no], lg[:, yes]], 1).float(), 1)[:, 1]
        for i, sc in zip(idx, p.tolist()): scores[(pairs[i][0], pairs[i][1], pairs[i][2])] = sc
        if (b // bs) % 100 == 0: print(b, len(order), round(time.time() - t0), flush=True)
else:
    raise SystemExit("모르는 모델 키")
pickle.dump(scores, open(NS / f"scores6_{KEY}.pkl", "wb"))
print("끝", round(time.time() - t0), "초", len(scores), flush=True)
