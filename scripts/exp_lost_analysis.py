"""후보 30개 안에 있었는데 최종 8개에서 빠진 정답 근거를 문항별로 뜯어 본다 (LLM 호출 없음).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_lost_analysis.py --questions eval/answer_eval_100.jsonl --diag eval/runs/stage_diag_100.json

문항마다 다음을 보여 준다: 빠진 정답 조문(제목, 리랭크 점수와 순위, RRF 순위) / 리랭크 상위 8개 조(제목, 점수, 정답 여부).
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
from src.search.search import RERANK_TOP, Searcher  # noqa: E402


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--diag", default=os.path.join("eval", "runs", "stage_diag_100.json"))
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--out", default="lost_analysis_100.json")
    args = ap.parse_args()

    gold = {json.loads(line)["id"]: json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()}
    diag = json.load(open(args.diag, encoding="utf-8"))
    lost = collections.defaultdict(list)
    for r in diag:
        if r["단계"].startswith("C"):
            lost[r["id"]].append(r)
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))

    s = Searcher()
    s.glossary_filter = True
    t2d = {v["title"]: d for d, v in s.documents.items()}
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
    title_of = {d: v["title"] for d, v in s.documents.items()}
    snippet = {}
    for cid, r in s.row_info.items():
        k0 = key(r["doc_id"], r.get("article"))
        if k0 not in snippet:
            body = (r["body"] or "").strip().replace("\n", " ")
            m = re.search(r"제\d+조(?:의\d+)?\(([^)]+)\)", body)
            snippet[k0] = (m.group(1) if m else body[:24], body[:90])

    def label(kk):
        doc_id, art = kk.split(":", 1)
        title = snippet.get(kk, ("", ""))[0]
        return f"{title_of.get(doc_id, doc_id)} {art}" + (f"({title})" if title else "")

    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    out = []
    for qid in sorted(lost):
        g = gold[qid]
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
        extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
        fused = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken]
        rrf_pos = {}
        for h in fused[:RERANK_TOP]:
            rrf_pos.setdefault(ckey[h["chunk_id"]], len(rrf_pos) + 1)
        ranked = s.rerank(q, [dict(h) for h in fused[:RERANK_TOP]])
        score, order = {}, []
        for h in ranked:
            kk = ckey[h["chunk_id"]]
            if kk not in score:
                score[kk] = h["rerank"]
                order.append(kk)
        want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
        print(f"\n### {qid} [{g['유형']}] {q}")
        print(f"  바꿔 쓴 검색어: {rew.get(q, '')[:70]}")
        rec = {"id": qid, "q": q, "lost": [], "top8": []}
        for r in lost[qid]:
            kk = key(t2d.get(r["규정"]), r["조항"])
            pos = order.index(kk) + 1 if kk in order else None
            print(f"  [빠진 정답] {label(kk)} | 리랭크 {pos}위 (점수 {score.get(kk, 0):.3f}), RRF {rrf_pos.get(kk)}위")
            print(f"      내용: {snippet.get(kk, ('', ''))[1]}")
            rec["lost"].append({"조": label(kk), "리랭크위": pos, "점수": score.get(kk), "RRF위": rrf_pos.get(kk)})
        print("  [리랭크 상위 8개 조]")
        for i, kk in enumerate(order[:8], 1):
            mark = "정답" if kk in want else "    "
            print(f"    {i}. {mark} {label(kk)[:58]:58s} {score[kk]:.3f}")
            rec["top8"].append({"조": label(kk), "정답": kk in want, "점수": score[kk]})
        out.append(rec)
    json.dump(out, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
