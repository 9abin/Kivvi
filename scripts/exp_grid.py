"""검색 설정 격자 비교 (LLM 답변 없이 정답 근거 Recall만 본다).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_grid.py --questions eval/answer_eval_100.jsonl --out exp_grid_100.json

세 가지를 한 번에 잰다. 질문마다 벡터·키워드 후보와 리랭크 점수를 한 번만 구해 설정별로 재사용한다
(리랭크 점수는 후보별로 독립이라 같은 질문의 같은 청크는 다시 계산하지 않는다).
  1. k × 리랭크 후보 폭: k 8·10·12·16, 후보 폭(RRF 상위 N개) 30·50·70
  2. 벡터·키워드 가중치: 0.7/0.3(현재), 0.85/0.15, 1.0/0.0, 0.5/0.5
  3. 용어 상한선(오라클): 정답 조문의 제목(과 규정 이름)을 검색어에 넣었을 때. 용어를 완벽하게 보강해도 오르는 한계를 본다.
기본 설정은 현재 값이다: k=TOP_K, 같은 조 묶기, 조 전체 인용 복구, 바꿔쓰기 캐시(eval/runs/rewrite-cache.json).
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import config  # noqa: E402
import src.search.search as ss  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import Searcher, _article_base, group_by_article  # noqa: E402


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def base_article(art):
    m = re.match(r"(제\d+조(?:의\d+)?)", art or "")
    if m:
        return m.group(1)
    m = re.match(r"(별표\s*\d+(?:의\d+)?)", art or "")
    return re.sub(r"\s+", "", m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--out", default="exp_grid_100.json")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    if args.limit:
        gold = gold[:args.limit]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    t2d = {v["title"]: d for d, v in s.documents.items()}
    title_of = {d: v["title"] for d, v in s.documents.items()}
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}

    # 정답 조문 제목(오라클 용어): (규정, 조) → 조 제목 또는 별표 첫 줄
    titles = {}
    for cid, r in s.row_info.items():
        b = base_article(r.get("article"))
        if not b:
            continue
        k0 = (title_of[r["doc_id"]], b)
        if k0 in titles:
            continue
        m = re.search(r"제\d+조(?:의\d+)?\(([^)]+)\)", r["body"] or "")
        titles[k0] = m.group(1) if m else (r["body"] or "").strip().split("\n")[0][:40]

    def oracle_hint(g, with_doc):
        terms = []
        for e in g["근거"]:
            if "부칙" in e["조항"] or "별지" in e["조항"]:
                continue
            b = base_article(e["조항"])
            t = titles.get((e["규정"], b)) if b else None
            if t:
                terms.append(t)
            if with_doc:
                terms.append(e["규정"])
        return " ".join(dict.fromkeys(terms))

    wv0, wk0 = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    HINTS = ("현재", "오라클 제목", "오라클 제목+규정")

    # 설정 목록: (이름, 가중치, 힌트, 리랭크 후보 폭, k)
    configs = []
    for pool in (30, 50, 70):
        for kk in (8, 10, 12, 16):
            configs.append((f"격자 후보{pool} k{kk}", (wv0, wk0), "현재", pool, kk))
    for w in ((0.85, 0.15), (1.0, 0.0), (0.5, 0.5)):
        configs.append((f"가중치 {w[0]}/{w[1]}", w, "현재", 30, config.TOP_K))
        configs.append((f"가중치 {w[0]}/{w[1]} 후보50 k12", w, "현재", 50, 12))
    configs.append(("오라클 제목 (k8, 후보30)", (wv0, wk0), "오라클 제목", 30, config.TOP_K))
    configs.append(("오라클 제목+규정 (k8, 후보30)", (wv0, wk0), "오라클 제목+규정", 30, config.TOP_K))
    configs.append(("오라클 제목 (k12, 후보50)", (wv0, wk0), "오라클 제목", 50, 12))

    # 질문별 후보 목록과 리랭크 점수(캐시)
    pre = {}
    t0 = time.time()
    for i, g in enumerate(gold, 1):
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
        extra = {}
        for hv in HINTS:
            base_hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
            if hv == "오라클 제목":
                hint = (base_hint + " " + oracle_hint(g, False)).strip()
            elif hv == "오라클 제목+규정":
                hint = (base_hint + " " + oracle_hint(g, True)).strip()
            else:
                hint = base_hint
            extra[hv] = (("벡터(바꿔쓴)", s.vector_hits(hint, None)), ("키워드(바꿔쓴)", s.bm25_hits(hint, None))) if hint else ()
        pre[g["id"]] = {"exact": exact, "taken": taken, "vec": vec, "key": keyw, "extra": extra, "score": {}}
        if i % 20 == 0:
            print(f"  후보 {i}/{len(gold)} ({time.time() - t0:.0f}초)", flush=True)

    ss.CITE_WHOLE_ARTICLE = True
    ss.SIBLING_LIMIT = 3000
    results = {}
    t1 = time.time()
    for ci, (name, (wv, wk), hv, pool, kk) in enumerate(configs, 1):
        got = need = full = got_i = need_i = full_i = 0
        chars, arts = [], []
        for g in gold:
            p = pre[g["id"]]
            ex = tuple((nm, hits, wv if nm.startswith("벡터") else wk) for nm, hits in p["extra"][hv])
            fused = [h for h in s.fuse(p["vec"], p["key"], wv, wk, ex) if h["chunk_id"] not in p["taken"]]
            cands = fused[:pool]
            todo = [dict(h) for h in cands if h["chunk_id"] not in p["score"]]
            if todo:
                for h in s.rerank(g["q"], todo):
                    p["score"][h["chunk_id"]] = h["rerank"]
            ranked = sorted((dict(h, rerank=p["score"][h["chunk_id"]]) for h in cands), key=lambda h: -h["rerank"])
            ranked, _ = glossary.filter_candidates(g["q"], ranked, s.glossary)
            ranked = group_by_article(ranked)
            res = []
            for h in (p["exact"] + ranked)[:kk]:
                it = s._entry(h, status=h.get("status"))
                it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                           "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                res.append(it)
            asm = s.assemble(res)
            used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
            want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
            wi = {w for w in want if ":부칙" not in w and ":별지" not in w}
            got += len(want & used)
            need += len(want)
            full += want <= used
            got_i += len(wi & used)
            need_i += len(wi)
            full_i += wi <= used
            chars.append(asm["chars"])
            arts.append(len({(it["doc_id"], _article_base(it["article"])) for grp in asm["groups"] for it in grp["items"]}))
        r = dict(recall=got / need, recall_indexed=got_i / need_i, full=full, full_indexed=full_i,
                 chars=sum(chars) / len(chars), arts=sum(arts) / len(arts))
        results[name] = r
        print(f"[{ci}/{len(configs)}] {name:30s} Recall {r['recall']:.3f} (색인 대상만 {r['recall_indexed']:.3f}) | "
              f"전부 확보 {full}/{len(gold)} | 평균 {r['chars']:.0f}자, {r['arts']:.1f}개 조 ({time.time() - t1:.0f}초)", flush=True)

    json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
