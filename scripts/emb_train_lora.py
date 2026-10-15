"""임베딩 LoRA 튜닝 (BGE-m3, 8GB GPU): 합성 질문 ↔ 청크, 어려운 오답 (docs/09 §10).

  uv run --extra db python scripts/emb_train_lora.py [--epochs 2] [--batch 24] [--lr 1e-4] [--tag v1]
입력: data/embedding/pairs_v3_train.jsonl (scripts/emb_build_pairs.py), 공유 DB의 청크 본문(읽기 전용)
학습: 질문 하나마다 (정답 1 + 어려운 오답 최대 4)를 문서로 보고, 한 배치의 다른 질문들의 문서도 오답으로 쓴다(in-batch).
  · 손실: InfoNCE, 온도 0.05. 같은 조(·별표)의 문서는 다른 질문의 오답에서 가린다 (같은 조의 다른 항은 정답 후보라서)
  · LoRA(r=16)만 학습하고 기반 모델은 고정. 그래디언트 체크포인팅으로 8GB에 맞춤
  · 문서 입력은 색인과 같은 모양(본문_검색 그대로), 질문 최대 96토큰, 문서 최대 192토큰
검증: 규정 단위로 뗀 검증 질문으로 에폭마다 「정답 청크의 순위」(검증 규정의 청크 전체 중 MRR·Recall@10)를 잰다
저장: data/embedding/model_ft_{tag}/ (LoRA를 합친 SentenceTransformer, scale=1.0)와 model_ft_{tag}_s05/ (LoRA 효과 절반 — WiSE-FT식 보간)
"""

import argparse
import collections
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import exp_embed_baseline as E  # noqa: E402

DATA = ROOT / "data" / "embedding"


def encode(model, texts, max_len, grad=True):
    feats = model.tokenize(texts)
    feats = {k: v.to("cuda") for k, v in feats.items() if torch.is_tensor(v)}   # ST 6은 문자열 항목도 섞어 돌려준다
    for k in ("input_ids", "attention_mask"):
        feats[k] = feats[k][:, :max_len]
    with torch.autocast("cuda", dtype=torch.float16):
        out = model(feats)["sentence_embedding"]
    return F.normalize(out.float(), dim=-1)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--batch", type=int, default=24)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--r", type=int, default=16)
    ap.add_argument("--temp", type=float, default=0.05)
    ap.add_argument("--tag", default="v1")
    ap.add_argument("--max-steps", type=int, default=0, help="시험용: 이 단계에서 멈춤")
    a = ap.parse_args()
    from peft import LoraConfig, get_peft_model
    from sentence_transformers import SentenceTransformer

    rows = E.load_db()
    text = {r["chunk_id"]: r["body_search"] for r in rows}
    node = {r["chunk_id"]: E.node_of_chunk(r["chunk_id"]) for r in rows}
    data = [json.loads(l) for l in (DATA / "pairs_v3_train.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    train = [x for x in data if x["split"] == "train"]
    val = [x for x in data if x["split"] == "val"]
    print(f"학습 {len(train)} · 검증 {len(val)} · 오답 평균 {np.mean([len(x['negs']) for x in train]):.2f}개/질문")

    model = SentenceTransformer("BAAI/bge-m3", device="cuda")
    model.max_seq_length = 192
    enc = model[0].auto_model
    cfg = LoraConfig(r=a.r, lora_alpha=2 * a.r, lora_dropout=0.05, target_modules=["query", "key", "value", "dense"], bias="none")
    peft_model = get_peft_model(enc, cfg)
    model[0].auto_model = peft_model
    peft_model.print_trainable_parameters()
    enc.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    enc.enable_input_require_grads()
    model.train()
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=a.lr, weight_decay=0.01)
    steps_per_epoch = math.ceil(len(train) / a.batch)
    total = steps_per_epoch * a.epochs
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / max(1, int(0.05 * total))) * 0.5 * (1 + math.cos(math.pi * min(1.0, s / total))))
    scaler = torch.amp.GradScaler("cuda")

    def val_metrics():
        """검증 규정의 청크 전체를 문서 풀로, 검증 질문마다 정답 청크의 조 순위 (조 단위 MRR, Recall@10)"""
        model.eval()
        regs = {x["chunk_id"].split("_")[0] for x in val}
        pool = [r["chunk_id"] for r in rows if r["chunk_id"].split("_")[0] in regs]
        with torch.no_grad():
            D = torch.cat([encode(model, [text[c] for c in pool[i:i + 64]], 192) for i in range(0, len(pool), 64)])
            Qv = torch.cat([encode(model, [x["q"] for x in val[i:i + 64]], 96) for i in range(0, len(val), 64)])
        S = (Qv @ D.T).cpu().numpy()
        rr, r10 = [], 0
        for k, x in enumerate(val):
            seen, rank = set(), None
            for j in np.argsort(-S[k]):
                n = node[pool[j]]
                if n in seen:
                    continue
                seen.add(n)
                if n == x["node"]:
                    rank = len(seen)
                    break
            rr.append(1 / rank if rank else 0)
            r10 += bool(rank and rank <= 10)
        model.train()
        return float(np.mean(rr)), r10 / len(val)

    mrr0, r100 = val_metrics()
    print(f"[검증 시작 전] 조 단위 MRR {mrr0:.3f} · Recall@10 {r100:.3f} (검증 규정 청크 풀)")
    rng = random.Random(5)
    step, t0 = 0, time.time()
    for ep in range(a.epochs):
        order = list(range(len(train)))
        rng.shuffle(order)
        run = []
        for b in range(0, len(order), a.batch):
            batch = [train[i] for i in order[b:b + a.batch]]
            docs, doc_node, owner = [], [], []
            for qi, x in enumerate(batch):
                for c in [x["chunk_id"]] + x["negs"]:
                    docs.append(text[c])
                    doc_node.append(node[c])
                    owner.append(qi)
            q = encode(model, [x["q"] for x in batch], 96)
            d = encode(model, docs, 192)
            logits = (q @ d.T) / a.temp
            # 정답 위치, 같은 조의 다른 문서는 가림
            pos_idx = torch.tensor([owner.index(qi) for qi in range(len(batch))], device="cuda")
            mask = torch.zeros_like(logits, dtype=torch.bool)
            for qi, x in enumerate(batch):
                for j, n in enumerate(doc_node):
                    if n == x["node"] and j != int(pos_idx[qi]):
                        mask[qi, j] = True
            logits = logits.masked_fill(mask, -1e4)
            loss = F.cross_entropy(logits, pos_idx)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            scaler.step(opt)
            scaler.update()
            sched.step()
            step += 1
            run.append(float(loss))
            if step % 20 == 0:
                print(f"  에폭 {ep + 1} 단계 {step}/{total} 손실 {np.mean(run[-20:]):.4f} · {time.time() - t0:.0f}초 · GPU {torch.cuda.max_memory_allocated() / 2**30:.1f}GB", flush=True)
            if a.max_steps and step >= a.max_steps:
                break
        mrr, r10 = val_metrics()
        print(f"[에폭 {ep + 1} 끝] 검증 조 단위 MRR {mrr:.3f} (시작 전 {mrr0:.3f}) · Recall@10 {r10:.3f} (시작 전 {r100:.3f})", flush=True)
        if a.max_steps and step >= a.max_steps:
            break

    # 저장: adapter를 디스크에 두고, 기본 모델에 효과 1.0·0.5로 합친 두 모델을 만든다 (0.5 = 기본 모델과의 보간, 망각 완화)
    adapter = DATA / f"lora_{a.tag}"
    peft_model.save_pretrained(str(adapter))
    del model, peft_model, enc, opt
    import gc
    gc.collect()
    torch.cuda.empty_cache()
    from peft import PeftModel
    from peft.tuners.lora import LoraLayer
    for name, scale in (("", 1.0), ("_s05", 0.5)):
        base = SentenceTransformer("BAAI/bge-m3", device="cpu")
        pm = PeftModel.from_pretrained(base[0].auto_model, str(adapter))
        for m in pm.modules():
            if isinstance(m, LoraLayer):
                for k in m.scaling:
                    m.scaling[k] = m.scaling[k] * scale
        base[0].auto_model = pm.merge_and_unload()
        out = DATA / f"model_ft_{a.tag}{name}"
        base.save(str(out))
        print(f"저장: {out} (LoRA 효과 {scale})")
    print(f"총 {time.time() - t0:.0f}초")


if __name__ == "__main__":
    main()
