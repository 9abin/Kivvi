"""조 전체 넘기기(팀원 RETURN_ARTICLE)로 조를 통째로 넣으면 8,000자 예산에서 잘리는 조가 있는지 본다.

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_budget_check.py --questions <tiered 평가셋> [--device cpu]

BGE 순서의 서로 다른 조 k개를 고르고, 각 조의 모든 항을 이어 붙인 길이(찾은 항·같은 조 표시 글자 포함)를 센다.
assemble과 같은 규칙(예산을 넘는 항목은 통째로 건너뜀)으로 조립해, 들어가지 못한 조와 그중 정답 조를 센다.
"""
import argparse
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
from src.eval.rank_trace import make_searcher  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import RERANK_TOP, _article_base, _order_key, group_by_article  # noqa: E402

HIT_MARK, SIB_MARK = "▶▶ [찾은 항] ", "▷ [같은 조] "
BUDGET = config.CONTEXT_CHAR_BUDGET


def key(doc, art):
    m = re.match(r"\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    m = re.match(r"\s*(별표\s*\d+(?:\s*의\s*\d+)?)", art or "")
    return f"{doc}:" + (re.sub(r"\s+", "", m.group(1)) if m else re.sub(r"\s+", "", art or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--ks", default="8,10")
    args = ap.parse_args()
    ks = [int(x) for x in args.ks.split(",")]
    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    s = make_searcher(args.device)
    t2d = {v["title"]: d for d, v in s.documents.items()}
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    sizes, over = [], []
    stat = {k: dict(q_over=0, results=0, dropped=0, gold_total=0, gold_in_results=0, gold_dropped=0, q_gold_dropped=0, cite_dropped=0,
                    chars=[]) for k in ks}
    for n, g in enumerate(gold, 1):
        q = g["q"]
        taken = {h["chunk_id"] for h in s.exact_hits(q)}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
        extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
        full = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken]
        ranked = s.rerank(q, [dict(h) for h in full[:RERANK_TOP]])
        ranked, _ = glossary.filter_candidates(q, ranked, s.glossary)
        order = group_by_article(ranked)
        want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
        for k in ks:
            entries = []
            for h in order[:k]:
                doc, base = h["doc_id"], _article_base(h["article"])
                rows = s.store.find_article(doc, base)
                rows.sort(key=lambda r: _order_key(r["article"]))
                marked = len(rows) > 1
                body = "\n".join((HIT_MARK if r["chunk_id"] == h["chunk_id"] else SIB_MARK) + r["body"] if marked else r["body"] for r in rows)
                cites = []
                for r in rows:
                    for c in s._cited({"chunk_id": r["chunk_id"], "doc_id": doc, "article": r["article"]}):
                        cites.append(c)
                entries.append({"key": key(doc, base), "n": len(body), "cites": cites, "base": (doc, base)})
                if k == ks[0]:
                    sizes.append(len(body))
            chosen = {e["base"] for e in entries}
            used, kept = 0, set()
            for e in entries:  # assemble: 검색 결과 먼저, 예산을 넘는 것은 통째로 건너뜀
                if used + e["n"] <= BUDGET:
                    used += e["n"]
                    kept.add(e["key"])
            cite_drop = 0
            seen = set()
            for e in entries:
                for c in e["cites"]:
                    if (c["doc_id"], _article_base(c["article"])) in chosen or c["chunk_id"] in seen:
                        continue
                    seen.add(c["chunk_id"])
                    if used + len(c["body"]) <= BUDGET:
                        used += len(c["body"])
                    else:
                        cite_drop += 1
            tot = sum(e["n"] for e in entries)
            st_ = stat[k]
            st_["q_over"] += tot > BUDGET
            st_["results"] += len(entries)
            st_["dropped"] += sum(1 for e in entries if e["key"] not in kept)
            inres = want & {e["key"] for e in entries}
            lost = inres - kept
            st_["gold_total"] += len(want)
            st_["gold_in_results"] += len(inres)
            st_["gold_dropped"] += len(lost)
            st_["q_gold_dropped"] += bool(lost)
            st_["cite_dropped"] += cite_drop
            st_["chars"].append(tot)
        if n % 20 == 0:
            print(f"  {n}/{len(gold)}", flush=True)
    print(f"\n조 전체 길이(상위 {ks[0]}개 조 기준): 중앙값 {st.median(sizes):.0f}자, 평균 {st.mean(sizes):.0f}자, 90퍼센타일 {sorted(sizes)[int(len(sizes) * .9)]}자, 최대 {max(sizes)}자, "
          f"예산({BUDGET}자)보다 긴 조 {sum(1 for x in sizes if x > BUDGET)}개 / {len(sizes)}개")
    for k, v in stat.items():
        ch = sorted(v["chars"])
        print(f"\n[k={k}] 조 전체를 이어 붙인 길이 중앙값 {st.median(ch):.0f}자, 90퍼센타일 {ch[int(len(ch) * .9)]}자, 최대 {ch[-1]}자")
        print(f"  예산 초과 질문 {v['q_over']}/{len(gold)}, 들어가지 못한 조 {v['dropped']}/{v['results']}개, 인용 대상 못 넣은 것 {v['cite_dropped']}개")
        print(f"  정답 조: 검색 결과에 든 {v['gold_in_results']}/{v['gold_total']}개 중 예산 때문에 빠진 {v['gold_dropped']}개 (질문 {v['q_gold_dropped']}개)")
    s.close()


if __name__ == "__main__":
    main()
