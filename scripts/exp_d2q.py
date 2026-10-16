"""doc2query(조문별 예상 질문 벡터)와 '서로 다른 조 30개 후보'를 시험한다 (정답 근거 Recall).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/gen_doc2query.py --out eval/runs/doc2query_questions.json
  python scripts/exp_d2q.py --questions <tiered 평가셋> --repeat 2

후보 구성(리랭커에 들어가는 30개):
  P0 현재          원질문·바꿔쓴 검색어의 벡터/키워드 목록 4개를 가중 RRF로 합친 상위 30개 항
  P1 + 예상 질문    P0에 목록 2개를 더한다: 원질문·바꿔쓴 검색어와 가장 가까운 예상 질문을 가진 조의 항들
                   (조마다 예상 질문 최대 유사도로 순위, 상위 50개 조, 조당 항 3개까지). 가중치 0.7(벡터와 같음), 평가를 보고 바꾸지 않는다
  P2 서로 다른 조 30   P0와 같은 4개 목록에서 서로 다른 조가 30개가 될 때까지 (조마다 RRF 최상위 항 하나)
  P3 서로 다른 조 30 + 예상 질문   P1의 6개 목록에서 같은 방식
각 후보를 BGE로 리랭크한 뒤 BGE만 k=5, 8, 10, 12, 16과 BGE 상위 16개 조 -> Luna 8, 10개(반복)의 정답 근거 Recall(전체/필수)을 센다.
"""
import argparse
import collections
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import config  # noqa: E402
import src.search.search as ss  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import Searcher, group_by_article  # noqa: E402

KS = (5, 8, 10, 12, 16)


def key(doc, art):
    m = re.match(r"\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    m = re.match(r"\s*(별표\s*\d+(?:\s*의\s*\d+)?)", art or "")
    return f"{doc}:" + (re.sub(r"\s+", "", m.group(1)) if m else re.sub(r"\s+", "", art or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--d2q", default=os.path.join("eval", "runs", "doc2query_questions.json"))
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--out", default="exp_d2q.json")
    args = ap.parse_args()
    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    d2q = json.load(open(args.d2q, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    s._select_llm = ChatOpenAI(model=config.JUDGE_MODEL, timeout=120, max_retries=2)
    t2d = {v["title"]: d for d, v in s.documents.items()}
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
    by_key = collections.defaultdict(list)
    for cid, k_ in ckey.items():
        by_key[k_].append(cid)
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    ss.CITE_WHOLE_ARTICLE = True
    ss.SIBLING_LIMIT = 3000

    # 예상 질문 임베딩
    qkeys, qtexts = [], []
    for k_, v in d2q.items():
        for q in v["질문"]:
            qkeys.append(k_)
            qtexts.append(q)
    QE = s.model.encode(qtexts, normalize_embeddings=True, batch_size=64, show_progress_bar=False)
    qkeys = np.array(qkeys)
    uniq = list(dict.fromkeys(qkeys.tolist()))
    pos = {k_: np.where(qkeys == k_)[0] for k_ in uniq}
    print(f"조문 {len(d2q)}개, 예상 질문 {len(qtexts)}개 임베딩 완료", flush=True)

    # 평가 질문과 거의 같은 예상 질문이 있는지 (오염 확인)
    EQ = s.model.encode([g["q"] for g in gold], normalize_embeddings=True, show_progress_bar=False)
    mx = (EQ @ QE.T).max(axis=1)
    print(f"평가 질문과 예상 질문의 최대 유사도: 중앙값 {np.median(mx):.3f}, 0.90 이상 {int((mx >= 0.90).sum())}개, 0.95 이상 {int((mx >= 0.95).sum())}개", flush=True)

    def d2q_list(text):
        qv = s.model.encode([text], normalize_embeddings=True, show_progress_bar=False)[0]
        sims = QE @ qv
        best = {k_: float(sims[pos[k_]].max()) for k_ in uniq}
        top = sorted(best, key=lambda k_: -best[k_])[:50]
        out = []
        for k_ in top:
            for cid in by_key.get(k_, [])[:3]:
                out.append({"chunk_id": cid, **s.row_info[cid]})
        return out

    data = []
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        base = [("벡터", s.vector_hits(q, None), wv), ("키워드", s.bm25_hits(q, None), wk),
                ("벡터(바꿔쓴)", s.vector_hits(hint, None) if hint else [], wv),
                ("키워드(바꿔쓴)", s.bm25_hits(hint, None) if hint else [], wk)]
        extra = [("예상질문", d2q_list(q), wv), ("예상질문(바꿔쓴)", d2q_list(hint) if hint else [], wv)]
        allk = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
        core = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"] if e.get("tier", "필수") == "필수"}
        data.append({"g": g, "exact": exact, "taken": taken, "base": base, "extra": extra, "want": allk, "core": core})
    n_all = sum(len(d["want"]) for d in data)
    n_core = sum(len(d["core"]) for d in data)
    print(f"정답 근거 {n_all}개 (필수 {n_core}개)", flush=True)

    def fused_of(d, with_d2q):
        lists = d["base"] + (d["extra"] if with_d2q else [])
        f = s.fuse(lists[0][1], [], lists[0][2], 0.0, lists[1:])
        return [h for h in f if h["chunk_id"] not in d["taken"]]

    def distinct(f, n):
        seen, out = set(), []
        for h in f:
            if ckey[h["chunk_id"]] not in seen:
                seen.add(ckey[h["chunk_id"]])
                out.append(h)
                if len(out) >= n:
                    break
        return out

    def used_sets(orders, k):
        out = []
        for d, order in zip(data, orders):
            res = []
            for h in (d["exact"] + order)[:k]:
                it = s._entry(h, status=h.get("status"))
                it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                           "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                res.append(it)
            asm = s.assemble(res)
            out.append({key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]})
        return out

    pools_def = {"P0 현재": lambda d: fused_of(d, False)[:30], "P1 + 예상 질문": lambda d: fused_of(d, True)[:30],
                 "P2 서로 다른 조 30": lambda d: distinct(fused_of(d, False), 30),
                 "P3 서로 다른 조 30 + 예상 질문": lambda d: distinct(fused_of(d, True), 30)}
    results = {}
    base_pool_keys = None
    for name, fn in pools_def.items():
        pools = [fn(d) for d in data]
        keys = [{ckey[h["chunk_id"]] for h in p} | {ckey[h["chunk_id"]] for h in d["exact"]} for d, p in zip(data, pools)]
        a = sum(len(d["want"] & k_) for d, k_ in zip(data, keys))
        c = sum(len(d["core"] & k_) for d, k_ in zip(data, keys))
        if base_pool_keys is None:
            base_pool_keys = keys
        gain = sum(len((d["want"] & k_) - b) for d, k_, b in zip(data, keys, base_pool_keys))
        lost = sum(len((d["want"] & b) - k_) for d, k_, b in zip(data, keys, base_pool_keys))
        print(f"\n[{name}] 후보 안 정답: 전체 {a}/{n_all} ({a / n_all:.3f}), 필수 {c}/{n_core} ({c / n_core:.3f}) | P0 대비 새로 {gain}, 빠진 {lost}", flush=True)
        orders = []
        for d, p in zip(data, pools):
            ranked = s.rerank(d["g"]["q"], [dict(h) for h in p])
            ranked, _ = glossary.filter_candidates(d["g"]["q"], ranked, s.glossary)
            orders.append(group_by_article(ranked))
        line = []
        for k in KS:
            U = used_sets(orders, k)
            ra = sum(len(d["want"] & u) for d, u in zip(data, U))
            rc = sum(len(d["core"] & u) for d, u in zip(data, U))
            line.append(f"k={k} {ra / n_all:.3f}/{rc / n_core:.3f}")
        print("  BGE만 (전체/필수) " + " | ".join(line), flush=True)
        res_n = {}
        for n in (8, 10):
            runs = []
            for _ in range(args.repeat):
                with ThreadPoolExecutor(8) as ex:
                    picked = list(ex.map(lambda x: s._llm_select(x[0]["g"]["q"], x[1][:16], n), zip(data, orders)))
                U = used_sets(picked, n)
                runs.append((sum(len(d["want"] & u) for d, u in zip(data, U)), sum(len(d["core"] & u) for d, u in zip(data, U))))
            res_n[n] = runs
            print(f"  BGE 16 -> Luna {n}개: 전체 {sum(r[0] for r in runs) / len(runs) / n_all:.3f} ({'/'.join(str(r[0]) for r in runs)}), "
                  f"필수 {sum(r[1] for r in runs) / len(runs) / n_core:.3f} ({'/'.join(str(r[1]) for r in runs)})", flush=True)
        results[name] = {"pool": [a, c], "luna": {str(n): v for n, v in res_n.items()}}
        json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
