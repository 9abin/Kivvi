"""정답 근거(규정, 조)가 검색 결과 안에서 몇 번째에 서 있는지 분포를 본다 (LLM 호출 없음, 바꿔쓰기 캐시 사용).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_rank_distribution.py --questions eval/answer_eval_100.jsonl

순위는 '서로 다른 조'를 센 순서다(같은 조의 항이 여럿이어도 한 번). 같은 후보 30개를 세 가지 순서로 비교한다.
  벡터 단독 / 키워드 단독: 원질문으로 각각 검색한 목록 (후보 30개와 무관, 참고용)
  RRF 순서   : RRF 합침 순서 그대로 (리랭커 없음)
  리랭크     : 현재 방식
부칙·별지는 색인에 없어 뺀다. 후보 30개 밖은 '30 밖'으로 센다.
"""
import argparse
import collections
import json
import os
import re
import statistics as st
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import config  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import RERANK_TOP, Searcher  # noqa: E402

BINS = [(1, 1), (2, 3), (4, 5), (6, 8), (9, 12), (13, 16), (17, 30)]


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def positions(hits, ckey):
    """청크 목록 → {조 키: 서로 다른 조 기준 순위}"""
    pos = {}
    for h in hits:
        pos.setdefault(ckey[h["chunk_id"]], len(pos) + 1)
    return pos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    args = ap.parse_args()

    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    t2d = {v["title"]: d for d, v in s.documents.items()}
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT

    rows = []
    for i, g in enumerate(gold, 1):
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
        extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
        fused = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken]
        pool = fused[:RERANK_TOP]
        reranked = s.rerank(q, [dict(h) for h in pool])
        p_vec, p_key = positions(vec, ckey), positions(keyw, ckey)
        p_rrf, p_rr = positions(pool, ckey), positions(reranked, ckey)
        for e in g["근거"]:
            if "부칙" in e["조항"] or "별지" in e["조항"]:
                continue
            kk = key(t2d.get(e["규정"]), e["조항"])
            rows.append({"id": g["id"], "k": kk, "벡터": p_vec.get(kk), "키워드": p_key.get(kk), "RRF": p_rrf.get(kk), "리랭크": p_rr.get(kk),
                         "후보수": len(p_rr)})
        if i % 25 == 0:
            print(f"  {i}/{len(gold)}", flush=True)

    # 같은 조를 여러 번 인용한 근거는 한 번으로
    uniq = {(r["id"], r["k"]): r for r in rows}
    items = list(uniq.values())
    n = len(items)
    print(f"\n색인 대상 정답 근거 {n}개 — 순위 분포 (서로 다른 조 기준)")
    print(f"{'순위 구간':10s} {'벡터 단독':>9s} {'키워드 단독':>10s} {'RRF 순서':>9s} {'리랭크':>7s}")
    for lo, hi in BINS:
        cell = {o: sum(1 for r in items if r[o] and lo <= r[o] <= hi) for o in ("벡터", "키워드", "RRF", "리랭크")}
        label = f"{lo}위" if lo == hi else f"{lo}~{hi}위"
        print(f"{label:10s} {cell['벡터']:9d} {cell['키워드']:10d} {cell['RRF']:9d} {cell['리랭크']:7d}")
    print(f"{'후보 30 밖':10s} {sum(1 for r in items if not r['벡터'] or r['벡터'] > 30):9d} {sum(1 for r in items if not r['키워드'] or r['키워드'] > 30):10d} "
          f"{sum(1 for r in items if not r['RRF']):9d} {sum(1 for r in items if not r['리랭크']):7d}")
    print("\n누적: 상위 k개 조 안에 정답이 있는 비율 (색인 대상 %d개 기준)" % n)
    print(f"{'k':>4s} {'벡터':>7s} {'키워드':>7s} {'RRF':>7s} {'리랭크':>7s}")
    for k in (1, 3, 5, 8, 12, 16, 20, 30):
        print(f"{k:4d} " + " ".join(f"{sum(1 for r in items if r[o] and r[o] <= k) / n:7.3f}" for o in ("벡터", "키워드", "RRF", "리랭크")))
    inpool = [r for r in items if r["리랭크"]]
    up = sum(1 for r in inpool if r["리랭크"] < r["RRF"])
    dn = sum(1 for r in inpool if r["리랭크"] > r["RRF"])
    print(f"\n후보 30 안의 정답 {len(inpool)}개: 리랭커가 RRF보다 앞으로 올린 것 {up}개, 뒤로 내린 것 {dn}개, 같은 것 {len(inpool) - up - dn}개")
    print(f"  정답의 위치 중앙값: RRF {st.median(r['RRF'] for r in inpool):.1f}위 → 리랭크 {st.median(r['리랭크'] for r in inpool):.1f}위")
    big = [(r["RRF"], r["리랭크"]) for r in inpool if r["리랭크"] > r["RRF"] + 2]
    print(f"  리랭커가 3칸 이상 뒤로 내린 정답: {len(big)}개 (예: {big[:6]})")
    json.dump(items, open(os.path.join("eval", "runs", "rank_distribution_100.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
