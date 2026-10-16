"""BGE만의 '필수 근거 Recall'을 질문마다 직접 세어 확인한다 (exp_baseline.py의 값과 대조).

사용: python scripts/exp_core_check_bge.py --questions <tiered 평가셋>
현재 설정과 장 정보를 채운 설정 두 가지에 대해 k=5·8·10·12·16의 정답 근거 Recall(전체/필수/보조)을 센다.
"""
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
import src.search.search as ss  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import RERANK_TOP, Searcher, group_by_article  # noqa: E402


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--chunks", default=os.path.join("out", "chunks.jsonl"))
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
    sec_map = {}
    for line in open(args.chunks, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            if r.get("장"):
                sec_map[key(r["고유번호"], r["조항"])] = r["장"]

    def set_sections(on):
        for c, r in s.row_info.items():
            r["section"] = sec_map.get(ckey[c]) if on else None

    set_sections(False)
    data = []
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
        extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
        a30 = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken][:RERANK_TOP]
        want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
        core = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"] if e.get("tier", "필수") == "필수"}
        data.append({"g": g, "exact": exact, "a30": a30, "want": want, "core": core})
    n_all = sum(len(d["want"]) for d in data)
    n_core = sum(len(d["core"]) for d in data)
    print(f"정답 근거 {n_all}개, 필수 {n_core}개, 보조 {n_all - n_core}개", flush=True)

    for name, sec in (("BGE만 (현재)", False), ("BGE만 + 장 정보", True)):
        set_sections(sec)
        orders = []
        for d in data:
            ranked = s.rerank(d["g"]["q"], [dict(h) for h in d["a30"]])
            ranked, _ = glossary.filter_candidates(d["g"]["q"], ranked, s.glossary)
            orders.append(group_by_article(ranked))
        set_sections(False)
        print(f"\n[{name}]", flush=True)
        for k in (5, 8, 10, 12, 16):
            a = c = 0
            for d, order in zip(data, orders):
                res = []
                for h in (d["exact"] + order)[:k]:
                    it = s._entry(h, status=h.get("status"))
                    it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                               "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                    res.append(it)
                asm = s.assemble(res)
                used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
                a += len(d["want"] & used)
                c += len(d["core"] & used)
            print(f"  k={k:2d}: 전체 {a}/{n_all} ({a / n_all:.3f}), 필수 {c}/{n_core} ({c / n_core:.3f}), 보조 {a - c}/{n_all - n_core} ({(a - c) / (n_all - n_core):.3f})", flush=True)
    s.close()


if __name__ == "__main__":
    main()
