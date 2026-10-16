"""RRF 상위 30개 안에서 정답 근거가 몇 번째에 있는지 분포 (서로 다른 조 기준 순위), BGE 리랭크 뒤 순위와 함께. 전체/필수/보조로 나눈다."""
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

BINS = [(1, 1), (2, 3), (4, 5), (6, 8), (9, 10), (11, 16), (17, 30)]


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
rows = []  # (tier, RRF 조 순위 또는 None, BGE 조 순위 또는 None, 후보 안 청크 순위)
sizes = []
for g in gold:
    q = g["q"]
    exact = s.exact_hits(q)
    taken = {h["chunk_id"] for h in exact}
    hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
    vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
    extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
    a30 = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken][:RERANK_TOP]
    ranked = s.rerank(q, [dict(h) for h in a30])
    ranked, _ = glossary.filter_candidates(q, ranked, s.glossary)

    def pos(hits):
        out = {}
        for h in hits:
            out.setdefault(ckey[h["chunk_id"]], len(out) + 1)
        return out
    prrf, pbge = pos(a30), pos(ranked)
    sizes.append(len(prrf))
    seen = set()
    for e in g["근거"]:
        k_ = key(t2d.get(e["규정"]), e["조항"])
        if k_ in seen:
            continue
        seen.add(k_)
        rows.append((e.get("tier", "필수"), prrf.get(k_), pbge.get(k_)))
print(f"RRF 상위 30개 항이 가리키는 서로 다른 조 수: 평균 {sum(sizes) / len(sizes):.1f}개 (최소 {min(sizes)}, 최대 {max(sizes)})\n")


def show(title, idx):
    for label, sel in (("전체", lambda t: True), ("필수", lambda t: t == "필수"), ("보조", lambda t: t != "필수")):
        items = [r[idx] for r in rows if sel(r[0])]
        n = len(items)
        cells = []
        for lo, hi in BINS:
            c = sum(1 for x in items if x and lo <= x <= hi)
            cells.append(f"{lo}위" if lo == hi else f"{lo}~{hi}위")
            cells[-1] += f" {c}"
        out30 = sum(1 for x in items if not x)
        print(f"[{title}] {label} {n}개: " + ", ".join(cells) + f", 30개 밖 {out30}")
    print()


show("RRF 순서", 1)
show("BGE 리랭크 뒤 순서", 2)
for label, sel in (("전체", lambda t: True), ("필수", lambda t: t == "필수")):
    items = [r for r in rows if sel(r[0])]
    n = len(items)
    print(f"누적 ({label} {n}개) 상위 k개 조 안에 든 비율: " + ", ".join(f"k={k} RRF {sum(1 for r in items if r[1] and r[1] <= k) / n:.3f} / BGE {sum(1 for r in items if r[2] and r[2] <= k) / n:.3f}" for k in (1, 3, 5, 8, 10, 16, 30)))
s.close()
