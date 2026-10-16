"""후보 단계 Recall: RRF 상위 30개(리랭커에 들어가는 후보)와 자르기 전 전체 후보에 정답 근거가 얼마나 있는가 (전체/필수/보조)."""
import argparse
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
from src.search.search import RERANK_TOP, Searcher  # noqa: E402


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


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
tot = {"all": 0, "core": 0, "aux": 0}
got = {n: {"all": 0, "core": 0, "aux": 0} for n in ("전체 후보", "RRF 상위 30개")}
for g in gold:
    q = g["q"]
    exact = s.exact_hits(q)
    taken = {h["chunk_id"] for h in exact}
    hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
    vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
    extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
    full = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken]
    want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
    core = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"] if e.get("tier", "필수") == "필수"}
    aux = want - core
    tot["all"] += len(want); tot["core"] += len(core); tot["aux"] += len(aux)
    for name, pool in (("전체 후보", full), ("RRF 상위 30개", full[:RERANK_TOP])):
        ks = {ckey[h["chunk_id"]] for h in pool} | {ckey[h["chunk_id"]] for h in exact}
        got[name]["all"] += len(want & ks); got[name]["core"] += len(core & ks); got[name]["aux"] += len(aux & ks)
for name, v in got.items():
    print(f"{name}: 전체 {v['all']}/{tot['all']} ({v['all'] / tot['all']:.3f}), 필수 {v['core']}/{tot['core']} ({v['core'] / tot['core']:.3f}), 보조 {v['aux']}/{tot['aux']} ({v['aux'] / tot['aux']:.3f})")
s.close()
