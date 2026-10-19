"""RRF 상위 30개 밖으로 밀린 정답 근거가 왜 밀렸는지 분석한다 (LLM 호출 없음).

항목마다: 네 검색 목록(원질문 벡터·키워드, 바꿔쓴 검색어 벡터·키워드) 각각에서의 순위, RRF 순위, 정답 조의 크기·본문 길이·호 단위 여부,
정답 규정이 상위 30개에 다른 조로 들어와 있는지, 상위 30개가 어떤 규정으로 채워졌는지를 구한다.
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


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


ap = argparse.ArgumentParser()
ap.add_argument("--questions", required=True)
ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
ap.add_argument("--out", default="outside30.json")
args = ap.parse_args()
gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
s = Searcher()
s.glossary_filter = True
t2d = {v["title"]: d for d, v in s.documents.items()}
title_of = {d: v["title"] for d, v in s.documents.items()}
ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
by_key = collections.defaultdict(list)
for cid, k_ in ckey.items():
    by_key[k_].append(cid)
doc_size = collections.Counter(r["doc_id"] for r in s.row_info.values())
wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
out = []
for g in gold:
    q = g["q"]
    exact = s.exact_hits(q)
    taken = {h["chunk_id"] for h in exact}
    hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
    L = {"벡터": s.vector_hits(q, None), "키워드": s.bm25_hits(q, None),
         "벡터(바꿔쓴)": s.vector_hits(hint, None) if hint else [], "키워드(바꿔쓴)": s.bm25_hits(hint, None) if hint else []}
    extra = (("벡터(바꿔쓴)", L["벡터(바꿔쓴)"], wv), ("키워드(바꿔쓴)", L["키워드(바꿔쓴)"], wk)) if hint else ()
    fused = [h for h in s.fuse(L["벡터"], L["키워드"], wv, wk, extra) if h["chunk_id"] not in taken]
    a30 = fused[:RERANK_TOP]
    a30_docs = collections.Counter(h["doc_id"] for h in a30)
    a30_arts = {ckey[h["chunk_id"]] for h in a30}
    seen = set()
    for e in g["근거"]:
        k_ = key(t2d.get(e["규정"]), e["조항"])
        if k_ in seen:
            continue
        seen.add(k_)
        if k_ in a30_arts or k_ in {ckey[h["chunk_id"]] for h in exact}:
            continue
        ids = set(by_key.get(k_, []))
        ranks = {}
        for name, hits in L.items():
            r_ = [i for i, h in enumerate(hits, 1) if h["chunk_id"] in ids]
            ranks[name] = min(r_) if r_ else None
        rr = [i for i, h in enumerate(fused, 1) if h["chunk_id"] in ids]
        doc_id = t2d.get(e["규정"])
        lens = [len(s.row_info[c]["body_search"] or "") for c in ids]
        out.append({"id": g["id"], "유형": g["유형"], "q": q, "tier": e.get("tier", "필수"), "규정": e["규정"], "조항": e["조항"],
                    "순위": ranks, "RRF순위": min(rr) if rr else None, "후보수": len(fused),
                    "조 청크수": len(ids), "본문길이": max(lens) if lens else 0, "호단위": "호" in e["조항"],
                    "규정 청크수": doc_size.get(doc_id, 0), "30개 안 같은 규정 청크": a30_docs.get(doc_id, 0),
                    "30개 규정": [(title_of[d][:10], n) for d, n in a30_docs.most_common(3)],
                    "별표": "별표" in e["조항"], "힌트": hint[:60]})
json.dump(out, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
n = len(out)
print(f"30개 밖 정답 {n}개")
inl = [o for o in out if o["RRF순위"]]
print(f"  후보 목록(4개 목록 합집합)에는 있음 {len(inl)}개 (RRF 31~60위 {sum(1 for o in inl if o['RRF순위'] <= 60)}, 61~100위 {sum(1 for o in inl if 60 < o['RRF순위'] <= 100)}, 101위 이후 {sum(1 for o in inl if o['RRF순위'] > 100)}) | 어느 목록에도 없음 {n - len(inl)}개")
cnt = collections.Counter()
for o in out:
    found = [k for k, v in o["순위"].items() if v]
    cnt[len(found)] += 1
print("  몇 개 목록에서 발견됐나:", {f"{k}개 목록": v for k, v in sorted(cnt.items())})
only = collections.Counter()
for o in out:
    found = tuple(k for k, v in o["순위"].items() if v)
    only[found] += 1
print("  발견된 목록 조합:", {"+".join(k) or "없음": v for k, v in only.most_common()})
print("  tier:", dict(collections.Counter(o["tier"] for o in out)), "| 유형:", dict(collections.Counter(o["유형"] for o in out)))
print("  정답 규정이 30개 후보에 다른 조로 이미 들어와 있음(같은 규정 안 경쟁):", sum(1 for o in out if o["30개 안 같은 규정 청크"] > 0), "개 | 규정 자체가 30개에 없음:", sum(1 for o in out if o["30개 안 같은 규정 청크"] == 0), "개")
print("  호 단위 정답:", sum(1 for o in out if o["호단위"]), "개 | 별표:", sum(1 for o in out if o["별표"]), "개")
print("  정답 조의 청크 수(항 개수) 중앙값:", st.median(o["조 청크수"] for o in out), "| 정답 청크 본문 길이 중앙값:", st.median(o["본문길이"] for o in out), "자 (전체 청크 중앙값 약 87자)")
print("  정답 규정의 전체 청크 수 중앙값:", st.median(o["규정 청크수"] for o in out))
print("\n항목별 (문항, tier, 정답, RRF순위, 4개 목록 순위[벡터/키워드/벡터바꿔쓴/키워드바꿔쓴])")
for o in sorted(out, key=lambda o: (o["RRF순위"] is None, o["RRF순위"] or 0)):
    r = o["순위"]
    print(f"  {o['id']} {o['tier']} {o['규정'][:9]} {o['조항'][:18]} | RRF {o['RRF순위']}/{o['후보수']} | {r['벡터']}/{r['키워드']}/{r['벡터(바꿔쓴)']}/{r['키워드(바꿔쓴)']} | 청크 {o['조 청크수']}개 {o['본문길이']}자 | 30개 안 같은 규정 {o['30개 안 같은 규정 청크']}개 | {o['q'][:26]}")
s.close()
