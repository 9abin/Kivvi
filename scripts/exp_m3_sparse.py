"""키워드 검색(BM25) 자리에 BGE-M3 sparse(어휘 가중치)를 넣었을 때 비교 (정답 근거 Recall만 본다).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_m3_sparse.py --questions eval/answer_eval_100.jsonl --luna

BGE-M3는 임베딩에 쓰는 모델과 같다. 마지막 은닉층 위에 sparse_linear(1024→1)을 얹어 토큰별 가중치를 얻는다
(FlagEmbedding의 lexical_weights와 같은 계산: relu(linear(은닉)), 같은 토큰은 최댓값, 특수 토큰 제외).
질문-문서 점수는 겹치는 토큰의 가중치 곱의 합이다. 문서 쪽 가중치는 eval/runs/m3_sparse_docs.pkl에 캐시한다.

후보 구성 비교(원질문 + 바꿔쓴 검색어 각각의 목록을 가중 RRF로 합침):
  하이브리드(BM25)         벡터 0.7 + BM25 0.3                  (현재)
  벡터 + M3 sparse        벡터 0.7 + M3 sparse 0.3             (BM25 대신)
  벡터 + BM25 + M3 sparse 벡터 0.7 + BM25 0.15 + M3 sparse 0.15
  M3 sparse 단독           M3 sparse만
"""
import argparse
import json
import os
import pickle
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import torch  # noqa: E402
from huggingface_hub import hf_hub_download  # noqa: E402
from scipy.sparse import csr_matrix  # noqa: E402

import config  # noqa: E402
import src.search.search as ss  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import CANDIDATES, RERANK_TOP, Searcher, group_by_article  # noqa: E402

KS = (5, 8, 10, 12, 16)
DOC_MAXLEN = 1300  # 가장 긴 청크가 1,210토큰


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--luna", action="store_true", help="BGE 상위 16개 조 → Luna가 k개(8·10) 선택도 잰다")
    ap.add_argument("--out", default="exp_m3_sparse.json")
    args = ap.parse_args()

    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    t2d = {v["title"]: d for d, v in s.documents.items()}
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    ss.CITE_WHOLE_ARTICLE = True
    ss.SIBLING_LIMIT = 3000

    # --- BGE-M3 sparse ---
    tok, enc_model = s.model.tokenizer, s.model[0].auto_model
    lin = torch.nn.Linear(enc_model.config.hidden_size, 1)
    lin.load_state_dict(torch.load(hf_hub_download("BAAI/bge-m3", "sparse_linear.pt"), map_location="cpu"))
    lin = lin.half().cuda().eval()
    special = {tok.cls_token_id, tok.eos_token_id, tok.pad_token_id, tok.unk_token_id}

    def sparse(texts, maxlen, bs=8):
        out = [None] * len(texts)
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        for b in range(0, len(order), bs):
            idx = order[b:b + bs]
            enc = tok([texts[i] for i in idx], padding=True, truncation=True, max_length=maxlen, return_tensors="pt").to("cuda")
            with torch.no_grad():
                w = torch.relu(lin(enc_model(**enc).last_hidden_state)).squeeze(-1).float().cpu().numpy()
            ids = enc["input_ids"].cpu().numpy()
            for r, i in enumerate(idx):
                d = {}
                for t, x in zip(ids[r], w[r]):
                    if x > 0 and int(t) not in special and x > d.get(int(t), 0):
                        d[int(t)] = float(x)
                out[i] = d
        return out

    cids = list(s.row_info)
    cache = os.path.join("eval", "runs", "m3_sparse_docs.pkl")
    if os.path.exists(cache):
        docs = pickle.load(open(cache, "rb"))
    else:
        t = time.time()
        docs = dict(zip(cids, sparse([s.row_info[c]["body_search"] or "" for c in cids], DOC_MAXLEN)))
        pickle.dump(docs, open(cache, "wb"))
        print(f"문서 {len(cids)}개 sparse 가중치 계산 ({time.time() - t:.0f}초)", flush=True)
    rows_i, cols_i, vals = [], [], []
    for r, c in enumerate(cids):
        for t, x in docs[c].items():
            rows_i.append(r), cols_i.append(t), vals.append(x)
    D = csr_matrix((vals, (rows_i, cols_i)), shape=(len(cids), len(tok)))

    def m3_hits(text):
        q = sparse([text], 512)[0]
        qv = csr_matrix((list(q.values()), (list(q.keys()), [0] * len(q))), shape=(len(tok), 1))
        sc = np.asarray((D @ qv).todense()).ravel()
        top = np.argsort(-sc)[:CANDIDATES]
        return [{"chunk_id": cids[i], **s.row_info[cids[i]]} for i in top if sc[i] > 0]

    # --- 질문별 목록 ---
    per_q = []
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        per_q.append({"g": g, "exact": exact, "taken": taken,
                      "vec": s.vector_hits(q, None), "vec_h": s.vector_hits(hint, None) if hint else [],
                      "bm": s.bm25_hits(q, None), "bm_h": s.bm25_hits(hint, None) if hint else [],
                      "m3": m3_hits(q), "m3_h": m3_hits(hint) if hint else []})
    print("후보 목록 준비 완료", flush=True)

    def pool(p, variant):
        v, vh, b, bh, m, mh = p["vec"], p["vec_h"], p["bm"], p["bm_h"], p["m3"], p["m3_h"]
        if variant == "하이브리드(BM25)":
            lists = (("벡터", v, wv), ("키워드", b, wk), ("벡터(바꿔쓴)", vh, wv), ("키워드(바꿔쓴)", bh, wk))
        elif variant == "벡터 + M3 sparse":
            lists = (("벡터", v, wv), ("키워드", m, wk), ("벡터(바꿔쓴)", vh, wv), ("키워드(바꿔쓴)", mh, wk))
        elif variant == "벡터 + BM25 + M3 sparse":
            lists = (("벡터", v, wv), ("키워드", b, wk / 2), ("M3", m, wk / 2),
                     ("벡터(바꿔쓴)", vh, wv), ("키워드(바꿔쓴)", bh, wk / 2), ("M3(바꿔쓴)", mh, wk / 2))
        else:  # M3 sparse 단독
            lists = (("M3", m, 1.0), ("M3(바꿔쓴)", mh, 1.0))
        fused = s.fuse(lists[0][1], [], lists[0][2], 0.0, lists[1:])
        return [h for h in fused if h["chunk_id"] not in p["taken"]][:RERANK_TOP]

    need = sum(len({key(t2d.get(e["규정"]), e["조항"]) for e in p["g"]["근거"]}) for p in per_q)
    results = {}
    for variant in ("하이브리드(BM25)", "벡터 + M3 sparse", "벡터 + BM25 + M3 sparse", "M3 sparse 단독"):
        pools = [pool(p, variant) for p in per_q]
        in_pool = sum(len({key(t2d.get(e["규정"]), e["조항"]) for e in p["g"]["근거"]} & {ckey[h["chunk_id"]] for h in pl})
                      for p, pl in zip(per_q, pools))
        orders = []
        for p, pl in zip(per_q, pools):
            ranked = s.rerank(p["g"]["q"], [dict(h) for h in pl])
            ranked, _ = glossary.filter_candidates(p["g"]["q"], ranked, s.glossary)
            orders.append(group_by_article(ranked))

        def recall(chosen_per_q, k):
            got = full = 0
            for p, chosen in zip(per_q, chosen_per_q):
                want = {key(t2d.get(e["규정"]), e["조항"]) for e in p["g"]["근거"]}
                res = []
                for h in (p["exact"] + chosen)[:k]:
                    it = s._entry(h, status=h.get("status"))
                    it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                               "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                    res.append(it)
                asm = s.assemble(res)
                used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
                got += len(want & used)
                full += want <= used
            return got / need, full

        firsts = []
        for p, order in zip(per_q, orders):
            idx = {key(t2d.get(e["규정"]), e["조항"]) for e in p["g"]["근거"] if "부칙" not in e["조항"] and "별지" not in e["조항"]}
            if idx:
                pos = [i for i, h in enumerate(order, 1) if ckey[h["chunk_id"]] in idx]
                firsts.append(pos[0] if pos else None)
        hit = [sum(1 for f in firsts if f and f <= t) / len(firsts) for t in (1, 3, 5, 10)]
        mrr = sum(1 / f for f in firsts if f) / len(firsts)
        cells = {k: recall(orders, k) for k in KS}
        results[variant] = {"후보안정답": in_pool / need, "Recall": cells, "hit": hit, "mrr": mrr}
        print(f"\n[{variant}] 후보 30개 안에 정답 {in_pool}/{need} = {in_pool / need:.3f}", flush=True)
        print("  BGE 점수순 Recall " + " ".join(f"k{k} {r:.3f}({f})" for k, (r, f) in cells.items()), flush=True)
        print("  Hit@1/3/5/10 " + "/".join(f"{x:.2f}" for x in hit) + f" MRR {mrr:.3f}", flush=True)

        if args.luna:
            from langchain_openai import ChatOpenAI
            s._select_llm = ChatOpenAI(model=config.JUDGE_MODEL, temperature=0, timeout=120, max_retries=2)
            for k in (8, 10):
                with ThreadPoolExecutor(8) as ex:
                    chosen = list(ex.map(lambda a: s._llm_select(a[0]["g"]["q"], a[1][:16], k), zip(per_q, orders)))
                r, f = recall(chosen, k)
                results[variant][f"Luna k{k}"] = (r, f)
                print(f"  BGE 16개 → Luna {k}개 선택 Recall {r:.3f} ({f})", flush=True)
        json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
