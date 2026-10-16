"""정답 근거(규정, 조)가 최종 근거에 못 들어온 이유를 검색 단계별로 나눈다 (LLM 호출은 바꿔쓰기 캐시가 없을 때만).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_stage_diagnosis.py --questions eval/answer_eval_100.jsonl --out stage_diag_100.json

설정은 현재 기본값(k=TOP_K, 같은 조 묶기, 조 전체 인용 복구)이다. 정답 근거마다 다음 중 하나로 분류한다.
  확보          최종 근거에 들어옴
  A 후보 없음   벡터·키워드·바꿔 쓴 검색어의 후보 어디에도 없음
  B RRF 30위 밖 후보에는 있으나 RRF 합침 30위 밖이라 리랭커가 보지 못함
  F 용어집 제외 리랭크까지 갔으나 용어집 필터가 후보에서 뺌
  C 리랭크 밀림 리랭크 후보 안에 있으나 상위 k개(조 단위) 밖
  D 조립 탈락   상위 k개에 있었으나 근거 조립에서 빠짐
부칙·별지는 색인에 없어 따로 센다.
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
from src.search.search import RERANK_TOP, Searcher, _article_base, group_by_article  # noqa: E402


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--rewrite-cache", default="eval/runs/rewrite-cache.json")
    ap.add_argument("--out", default="stage_diag_100.json")
    args = ap.parse_args()

    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    t2d = {v["title"]: d for d, v in s.documents.items()}
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    k = config.TOP_K

    rows = []
    for i, g in enumerate(gold, 1):
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
        extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
        fused = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken]
        fused_rank = {}
        for r, h in enumerate(fused, 1):
            fused_rank.setdefault(ckey[h["chunk_id"]], r)           # 조 단위로 처음 나온 RRF 순위
        ranked = s.rerank(q, [dict(h) for h in fused[:RERANK_TOP]])
        ranked_keys = {ckey[h["chunk_id"]] for h in ranked}
        filtered, _ = glossary.filter_candidates(q, ranked, s.glossary)
        grouped = group_by_article(filtered)
        grouped_rank = {}
        for r, h in enumerate(grouped, 1):
            grouped_rank.setdefault(ckey[h["chunk_id"]], r)
        top = (exact + grouped)[:k]
        top_keys = {ckey[h["chunk_id"]] for h in top}
        results = []
        for h in top:
            it = s._entry(h, status=h.get("status"))
            it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                       "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
            results.append(it)
        asm = s.assemble(results)
        used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
        for e in g["근거"]:
            kk = key(t2d.get(e["규정"]), e["조항"])
            kind = "부칙" if "부칙" in e["조항"] else "별지" if "별지" in e["조항"] else "별표" if "별표" in e["조항"] else "조문"
            if kk in used:
                stage = "확보"
            elif kind in ("부칙", "별지"):
                stage = "색인 없음(부칙·별지)"
            elif kk in top_keys:
                stage = "D 조립 탈락"
            elif kk in ranked_keys and kk not in grouped_rank:
                stage = "F 용어집 제외"
            elif kk in grouped_rank:
                stage = "C 리랭크 밀림"
            elif kk in fused_rank:
                stage = "B RRF 30위 밖"
            else:
                stage = "A 후보 없음"
            rows.append({"id": g["id"], "유형": g["유형"], "규정": e["규정"], "조항": e["조항"], "종류": kind, "단계": stage,
                         "RRF순위": fused_rank.get(kk), "리랭크후순위": grouped_rank.get(kk)})
        if i % 20 == 0:
            print(f"  {i}/{len(gold)}", flush=True)

    json.dump(rows, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    uniq = {(r["id"], r["규정"], key("", r["조항"])): r for r in rows}      # 같은 조를 여러 번 인용한 근거는 한 번으로
    c = collections.Counter(r["단계"] for r in uniq.values())
    print(f"\n정답 근거 {len(uniq)}개(문항 안에서 같은 조는 한 번으로 센 값)")
    for name, n in sorted(c.items()):
        print(f"  {n:3d}  {name}")
    print("\n[C 리랭크 밀림: 조 단위 순위 분포(k=%d 밖)]" % k)
    cr = sorted(r["리랭크후순위"] for r in uniq.values() if r["단계"].startswith("C"))
    print("  ", cr)
    print("[B RRF 30위 밖: RRF 순위]")
    print("  ", sorted(r["RRF순위"] for r in uniq.values() if r["단계"].startswith("B")))
    s.close()


if __name__ == "__main__":
    main()
