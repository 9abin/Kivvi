"""후보 30개를 다르게 만들면 정답이 얼마나 더 들어오는지 (후보 단계 Recall만, LLM 호출 없음).

  현재         RRF 상위 30개 항
  서로 다른 조 30  RRF 순서에서 서로 다른 조가 30개가 될 때까지 (같은 조의 항은 한 번만 세고, 후보에는 그 조의 RRF 최상위 항을 넣는다)
  목록별 보장    RRF 상위 N개 + 4개 검색 목록 각각의 상위 m개 합집합
"""
import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import config  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import Searcher  # noqa: E402


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    m = re.match(r"\s*(별표\s*\d+(?:\s*의\s*\d+)?)", art or "")
    return f"{doc}:" + (re.sub(r"\s+", "", m.group(1)) if m else re.sub(r"\s+", "", art or ""))


ap = argparse.ArgumentParser()
ap.add_argument("--questions", required=True)
ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
args = ap.parse_args()
gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
s = Searcher()
s.glossary_filter = True
t2d = {v["title"]: d for d, v in s.documents.items()}
ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT


def gold_keys(g):
    # 정답이 별표 하위 표기("별표 5 주1")여도 상위 별표 키로 맞춘다
    allk = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
    core = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"] if e.get("tier", "필수") == "필수"}
    return allk, core


variants = ["현재 (RRF 상위 30개 항)", "서로 다른 조 30개", "서로 다른 조 40개",
            "RRF 상위 24 + 목록별 상위 3", "RRF 상위 20 + 목록별 상위 5", "RRF 상위 30 + 목록별 상위 3"]
tot = {"all": 0, "core": 0}
res = {v: {"all": 0, "core": 0, "size": 0, "arts": 0} for v in variants}
for g in gold:
    q = g["q"]
    exact = s.exact_hits(q)
    taken = {h["chunk_id"] for h in exact}
    hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
    L = [s.vector_hits(q, None), s.bm25_hits(q, None), s.vector_hits(hint, None) if hint else [], s.bm25_hits(hint, None) if hint else []]
    extra = (("벡터(바꿔쓴)", L[2], wv), ("키워드(바꿔쓴)", L[3], wk)) if hint else ()
    fused = [h for h in s.fuse(L[0], L[1], wv, wk, extra) if h["chunk_id"] not in taken]
    allk, core = gold_keys(g)
    tot["all"] += len(allk); tot["core"] += len(core)

    def distinct(n):
        seen, out = set(), []
        for h in fused:
            if ckey[h["chunk_id"]] not in seen:
                seen.add(ckey[h["chunk_id"]]); out.append(h)
                if len(out) >= n:
                    break
        return out

    def quota(n, m):
        ids = {h["chunk_id"]: h for h in fused[:n]}
        for lst in L:
            for h in [x for x in lst if x["chunk_id"] not in taken][:m]:
                ids.setdefault(h["chunk_id"], h)
        return list(ids.values())

    pools = {variants[0]: fused[:30], variants[1]: distinct(30), variants[2]: distinct(40),
             variants[3]: quota(24, 3), variants[4]: quota(20, 5), variants[5]: quota(30, 3)}
    for name, pool in pools.items():
        ks = {ckey[h["chunk_id"]] for h in pool} | {ckey[h["chunk_id"]] for h in exact}
        res[name]["all"] += len(allk & ks); res[name]["core"] += len(core & ks)
        res[name]["size"] += len(pool); res[name]["arts"] += len({ckey[h["chunk_id"]] for h in pool})
print(f"정답 근거 {tot['all']}개 (필수 {tot['core']}개), 100문항. 별표 하위 표기는 별표 키로 맞춤")
for name, v in res.items():
    print(f"{name:28s} 후보 안 정답: 전체 {v['all']}/{tot['all']} ({v['all'] / tot['all']:.3f}), 필수 {v['core']}/{tot['core']} ({v['core'] / tot['core']:.3f}) | 리랭커 입력 항 평균 {v['size'] / 100:.1f}개, 서로 다른 조 평균 {v['arts'] / 100:.1f}개")
s.close()
