"""리랭커 모델 비교 (LLM 답변 없이 정답 근거 Recall과 정답 순위만 본다).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_reranker_bakeoff.py --questions eval/answer_eval_100.jsonl

같은 후보(RRF 합침 상위 30개)를 모델마다 다시 줄 세워 비교한다. 후보 계산은 한 번만 하고 모델만 바꾼다.
재는 것: 최종 근거 Recall(k=8·12·16, 현재 설정과 같은 조립), 정답이 상위 k개 조 안에 드는 누적 비율, 정답 위치 중앙값,
        RRF보다 3칸 이상 뒤로 내린 정답 수, 질문당 리랭크 시간.
기준선: RRF 순서(리랭커 없음)와 완벽한 순서(상한선).
주의: gte-multilingual-reranker-base는 trust_remote_code=True가 필요해서 허브의 모델 코드를 내려받아 실행한다.
"""
import argparse
import gc
import json
import os
import pickle
import re
import statistics as st
import sys
import time

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import config  # noqa: E402
import src.search.search as ss  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import RERANK_TOP, Searcher, group_by_article  # noqa: E402

# (표시 이름, 모델, 종류, 옵션)
MODELS = [
    ("현재 bge-reranker-v2-m3", "BAAI/bge-reranker-v2-m3", "ce", {}),
    ("현재 모델, 머리말 끔", "BAAI/bge-reranker-v2-m3", "ce", {"header": False}),
    ("bge-reranker-v2-m3-ko", "dragonkue/bge-reranker-v2-m3-ko", "ce", {}),
    ("Qwen3-Reranker-0.6B", "Qwen/Qwen3-Reranker-0.6B", "qwen", {}),
    # gte는 모델 코드가 현재 환경과 맞지 않아 GPU 오류가 나므로 맨 뒤에 둔다(오류가 다른 모델에 번지지 않게)
    ("gte-multilingual-reranker-base", "Alibaba-NLP/gte-multilingual-reranker-base", "ce", {"trust_remote_code": True}),
]
KS = (8, 12, 16)


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def load_scorer(model_id, kind, opts):
    """(점수 함수, 해제 함수). 점수 함수는 [(질문, 본문), ...] → 점수 목록."""
    import torch
    if kind == "ce":
        from sentence_transformers import CrossEncoder
        ce = CrossEncoder(model_id, device="cuda", trust_remote_code=opts.get("trust_remote_code", False))
        if opts.get("trust_remote_code"):
            # 허브의 모델 코드가 예전 transformers의 get_extended_attention_mask를 기대한다. 인코더용 표준 구현을 덧붙인다.
            for m in ce.model.modules():
                cls = type(m)
                if cls.__name__ == "NewModel" and not hasattr(cls, "get_extended_attention_mask"):
                    def _ext(self, attention_mask, input_shape=None, device=None, dtype=None):
                        dt = dtype or self.dtype
                        return (1.0 - attention_mask[:, None, None, :].to(dt)) * torch.finfo(dt).min
                    cls.get_extended_attention_mask = _ext
            # transformers 5.x는 저장되지 않는 버퍼(position_ids, 회전 위치 임베딩의 inv_freq·cos/sin 캐시)를 초기화하지 않고
            # 쓰레기 값으로 남긴다. 그대로 쓰면 인덱스 오류(GPU에서는 device-side assert)가 나거나 점수가 의미 없어진다.
            for n, b in ce.model.named_buffers():
                if n.endswith("position_ids"):
                    b.data = torch.arange(b.shape[-1]).expand_as(b).clone().to(b.dtype)
            for m in ce.model.modules():
                if hasattr(m, "_set_cos_sin_cache") and hasattr(m, "dim"):
                    dt, dev = m.cos_cached.dtype, m.cos_cached.device
                    m.inv_freq = (1.0 / (m.base ** (torch.arange(0, m.dim, 2).float() / m.dim))).to(dev)
                    m._set_cos_sin_cache(int(m.max_position_embeddings * getattr(m, "scaling_factor", 1.0)), dev, dt)
        return (lambda pairs: list(ce.predict(pairs, batch_size=16, show_progress_bar=False))), ce
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(model_id, padding_side="left")
    model = AutoModelForCausalLM.from_pretrained(model_id, torch_dtype=torch.float16).cuda().eval()
    yes, no = tok.convert_tokens_to_ids("yes"), tok.convert_tokens_to_ids("no")
    prefix = ("<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. "
              "Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n")
    suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    inst = "Given a Korean employee question, retrieve the internal company regulation passage that answers it"

    def score(pairs):
        out = []
        for i in range(0, len(pairs), 8):
            batch = [prefix + f"<Instruct>: {inst}\n<Query>: {q}\n<Document>: {d}" + suffix for q, d in pairs[i:i + 8]]
            enc = tok(batch, padding=True, truncation=True, max_length=4096, return_tensors="pt").to("cuda")
            with torch.no_grad():
                logits = model(**enc).logits[:, -1, :]
            two = torch.stack([logits[:, no], logits[:, yes]], dim=1).float()
            out += torch.softmax(two, dim=1)[:, 1].tolist()
        return out

    return score, model


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="", help="이 표시 이름만 (쉼표 구분)")
    ap.add_argument("--out", default="exp_reranker_bakeoff.json")
    args = ap.parse_args()

    import torch
    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    if args.limit:
        gold = gold[:args.limit]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    t2d = {v["title"]: d for d, v in s.documents.items()}
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    ss.CITE_WHOLE_ARTICLE = True
    ss.SIBLING_LIMIT = 3000

    cache_path = os.path.join("eval", "runs", "bakeoff_pool_cache.pkl")
    if os.path.exists(cache_path) and not args.limit:
        pre = pickle.load(open(cache_path, "rb"))
        print("후보 계산 캐시를 읽음", flush=True)
    else:
        pre = []
        for g in gold:
            q = g["q"]
            exact = s.exact_hits(q)
            taken = {h["chunk_id"] for h in exact}
            hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
            vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
            extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
            fused = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken]
            want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
            idx_want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"] if "부칙" not in e["조항"] and "별지" not in e["조항"]}
            pre.append({"g": g, "exact": exact, "pool": fused[:RERANK_TOP], "want": want, "idx": idx_want})
        if not args.limit:
            pickle.dump(pre, open(cache_path, "wb"))
    # 후보 계산이 끝나면 GPU에서 임베딩 모델과 Searcher의 리랭커를 내린다. 이후에는 비교할 모델 하나만 GPU에 둔다(메모리 부족 방지).
    for attr in ("model", "reranker"):
        if hasattr(s, attr):
            delattr(s, attr)
    gc.collect()
    torch.cuda.empty_cache()
    need = sum(len(p["want"]) for p in pre)
    print(f"후보 계산 완료: 질문 {len(pre)}개, 정답 근거 {need}개", flush=True)

    def positions(order):
        pos = {}
        for h in order:
            pos.setdefault(ckey[h["chunk_id"]], len(pos) + 1)
        return pos

    rrf_pos = [positions(p["pool"]) for p in pre]

    def evaluate(name, orders, secs):
        """orders: 질문별 줄 세운 후보(청크 목록)"""
        row = {"이름": name, "초": secs}
        for k in KS:
            got = full = 0
            for p, order in zip(pre, orders):
                ranked, _ = glossary.filter_candidates(p["g"]["q"], order, s.glossary)
                ranked = group_by_article(ranked)
                res = []
                for h in (p["exact"] + ranked)[:k]:
                    it = s._entry(h, status=h.get("status"))
                    it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                               "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                    res.append(it)
                asm = s.assemble(res)
                used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
                got += len(p["want"] & used)
                full += p["want"] <= used
            row[f"R{k}"], row[f"F{k}"] = got / need, full
        items, down, mid = [], 0, []
        for p, order, rp in zip(pre, orders, rrf_pos):
            pos = positions(order)
            for kk in p["idx"]:
                if kk in pos:
                    items.append(pos[kk])
                    mid.append(pos[kk])
                    if kk in rp and pos[kk] > rp[kk] + 2:
                        down += 1
        n_idx = sum(len(p["idx"]) for p in pre)
        row["top1"], row["top3"], row["top8"] = (sum(1 for x in items if x <= t) / n_idx for t in (1, 3, 8))
        # 질문 단위 Hit@k / MRR: 색인 대상 정답 조 중 가장 앞선 것의 순위(서로 다른 조 기준)
        firsts = []
        for p, order in zip(pre, orders):
            if p["idx"]:
                pos = positions(order)
                firsts.append(min((pos[kk] for kk in p["idx"] if kk in pos), default=None))
        for t in (1, 3, 5, 10):
            row[f"hit{t}"] = sum(1 for f in firsts if f and f <= t) / len(firsts)
        row["mrr"] = sum(1 / f for f in firsts if f) / len(firsts)
        row["중앙값"] = st.median(mid) if mid else None
        row["뒤로3칸"] = down
        print(f"{name:34s} Recall k8 {row['R8']:.3f}({row['F8']:3d}) k12 {row['R12']:.3f}({row['F12']:3d}) k16 {row['R16']:.3f}({row['F16']:3d}) | "
              f"Hit@1/3/5/10 {row['hit1']:.2f}/{row['hit3']:.2f}/{row['hit5']:.2f}/{row['hit10']:.2f} MRR {row['mrr']:.3f} | 정답 상위1 {row['top1']:.3f} 상위3 {row['top3']:.3f} 상위8 {row['top8']:.3f} | 중앙값 {row['중앙값']} | 3칸 뒤로 {down} | {secs}", flush=True)
        return row

    def rerank_all(scorer):
        """모든 질문의 후보를 이 모델로 줄 세운다. 함수 안에서만 scorer를 쓰므로 끝나면 모델을 붙잡지 않는다."""
        t = time.time()
        orders = []
        for i, p in enumerate(pre, 1):
            pairs = [(p["g"]["q"], s.rerank_text(h["chunk_id"])) for h in p["pool"]]
            scores = scorer(pairs)
            orders.append(sorted((dict(h, rerank=float(sc)) for h, sc in zip(p["pool"], scores)), key=lambda h: -h["rerank"]))
            if i % 25 == 0:
                print(f"    리랭크 {i}/{len(pre)} ({time.time() - t:.0f}초)", flush=True)
        return orders, f"{(time.time() - t) / len(pre):.2f}초/질문"

    def save():
        json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    results = [evaluate("기준: RRF 순서(리랭커 없음)", [p["pool"] for p in pre], "-")]
    results.append(evaluate("상한: 완벽한 순서", [sorted(p["pool"], key=lambda h: 0 if ckey[h["chunk_id"]] in p["want"] else 1) for p in pre], "-"))

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    loaded = {}
    for name, model_id, kind, opts in MODELS:
        if only and name not in only:
            continue
        try:
            if model_id not in loaded:
                for k_ in list(loaded):                      # 이전 모델을 내리고 메모리를 비운다
                    del loaded[k_]
                gc.collect()
                torch.cuda.empty_cache()
                t = time.time()
                loaded[model_id] = load_scorer(model_id, kind, opts)
                print(f"  모델 불러옴: {model_id} ({time.time() - t:.0f}초)", flush=True)
            s.rerank_header = opts.get("header", config.RERANK_HEADER)
            orders, secs = rerank_all(loaded[model_id][0])
            results.append(evaluate(name, orders, secs))
            save()
        except Exception as e:  # noqa: BLE001
            print(f"  [실패] {name}: {repr(e)[:200]}", flush=True)
            results.append({"이름": name, "오류": repr(e)[:200]})
    s.rerank_header = config.RERANK_HEADER
    json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
