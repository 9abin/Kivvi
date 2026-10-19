"""BGE 리랭커 입력을 바꿔 보는 실험: 조 제목 추가, 문장 단위 점수 (정답 근거 Recall만 본다, LLM 호출 없음).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_bge_sentence.py --questions eval/answer_eval_100.jsonl

같은 후보(현재 방식 상위 30개 항)를 BGE가 점수 매기는 방식만 바꿔 비교한다.
  현재          [문서명 장 조항] 본문 전체 (원질문)
  장 추가       [문서명 장 조항] 본문 전체. 장은 DB에 비어 있어 조 단위 청킹 결과(out/chunks.jsonl)에서 채움
  제목 추가     [문서명 장 조항(조 제목)] 본문 전체. 조 제목은 같은 조의 청크에서 찾은 "제N조(제목)"
  문장 최대     청크를 문장으로 나눠 (원질문, 문장) 점수 중 최댓값
  문장 최대+전체 문장 최대와 현재 점수의 평균
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
from src.search.search import RERANK_TOP, Searcher, group_by_article  # noqa: E402

KS = (5, 8, 10, 12, 16)
SENT = re.compile(r"(?<=[.다요])\s+|\n+")
TITLE = re.compile(r"제\d+조(?:의\d+)?\(([^)]+)\)")


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--chunks", default=os.path.join("out", "chunks.jsonl"), help="장 정보가 있는 조 단위 청크 파일")
    ap.add_argument("--out", default="exp_bge_sentence.json")
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

    art_title = {}
    for cid, r in s.row_info.items():
        m = TITLE.search((r["body"] or "")[:200])
        if m:
            art_title.setdefault(ckey[cid], m.group(1))

    # 장 정보: DB에는 비어 있으므로 조 단위 청킹 결과(out/chunks.jsonl)의 장을 (규정, 조)로 연결한다
    sec_map = {}
    for line in open(args.chunks, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            if r.get("장"):
                sec_map[key(r["고유번호"], r["조항"])] = r["장"]
    indexed = [c for c, r in s.row_info.items()]
    hit = sum(1 for c in indexed if ckey[c] in sec_map)
    print(f"장 정보 연결: 색인 청크 {len(indexed)}개 중 {hit}개 ({hit / len(indexed):.0%}), (규정, 조) {len(sec_map)}개", flush=True)

    def set_sections(on):
        for c, r in s.row_info.items():
            r["section"] = sec_map.get(ckey[c]) if on else None

    def titled_text(cid):
        r = s.row_info[cid]
        doc = s.documents.get(r["doc_id"], {}).get("title") or r["doc_id"]
        t = art_title.get(ckey[cid])
        art = re.match(r"제\d+조(?:의\d+)?", r["article"] or "")
        label = " ".join(x for x in (doc, r.get("section"), (art.group(0) + f"({t})" + r["article"][len(art.group(0)):]) if (art and t) else r["article"]) if x)
        return f"[{label}] {r['body_search']}"

    per_q = []
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
        extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
        fused = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken][:RERANK_TOP]
        per_q.append((g, exact, fused))
    print("후보 준비 완료", flush=True)

    need = sum(len({key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}) for g, *_ in per_q)

    def evaluate(name, scorer):
        t0 = time.time()
        orders = []
        for g, exact, fused in per_q:
            q = g["q"]
            sc = scorer(q, fused)
            ranked = sorted(({**h, "rerank": float(x)} for h, x in zip(fused, sc)), key=lambda h: -h["rerank"])
            ranked, _ = glossary.filter_candidates(q, ranked, s.glossary)
            orders.append(group_by_article(ranked))
        secs = (time.time() - t0) / len(per_q)

        def recall(k):
            got = full = 0
            for (g, exact, _), order in zip(per_q, orders):
                want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
                res = []
                for h in (exact + order)[:k]:
                    it = s._entry(h, status=h.get("status"))
                    it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                               "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                    res.append(it)
                asm = s.assemble(res)
                used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
                got += len(want & used)
                full += want <= used
            return got / need, full

        firsts = []
        for (g, _, _), order in zip(per_q, orders):
            idx = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"] if "부칙" not in e["조항"] and "별지" not in e["조항"]}
            if idx:
                pos = [i for i, h in enumerate(order, 1) if ckey[h["chunk_id"]] in idx]
                firsts.append(pos[0] if pos else None)
        hit = [sum(1 for f in firsts if f and f <= t) / len(firsts) for t in (1, 3, 5, 10)]
        mrr = sum(1 / f for f in firsts if f) / len(firsts)
        cells = {k: recall(k) for k in KS}
        print(f"\n[{name}] 리랭크 {secs:.1f}초/질문", flush=True)
        print("  Recall " + " ".join(f"k{k} {r:.3f}({f})" for k, (r, f) in cells.items()), flush=True)
        print("  Hit@1/3/5/10 " + "/".join(f"{x:.2f}" for x in hit) + f" MRR {mrr:.3f}", flush=True)
        return {"Recall": cells, "hit": hit, "mrr": mrr, "초": secs}

    def current(q, hits):
        return s.reranker.predict([(q, s.rerank_text(h["chunk_id"])) for h in hits], batch_size=16)

    def titled(q, hits):
        return s.reranker.predict([(q, titled_text(h["chunk_id"])) for h in hits], batch_size=16)

    def sent_max(q, hits):
        out = []
        for h in hits:
            body = re.sub(r"[ \t]+", " ", (s.row_info[h["chunk_id"]]["body_search"] or "").strip())
            sents = [x.strip() for x in SENT.split(body) if x and x.strip()] or [body]
            out.append(max(s.reranker.predict([(q, x) for x in sents], batch_size=16)))
        return out

    def sent_plus_full(q, hits):
        a, b = sent_max(q, hits), current(q, hits)
        return [(x + y) / 2 for x, y in zip(a, b)]

    results = {}
    for name, fn, sec in (("현재", current, False), ("장 추가", current, True), ("제목 추가", titled, False), ("장+제목 추가", titled, True),
                          ("문장 최대", sent_max, False), ("문장 최대+전체 평균", sent_plus_full, False)):
        set_sections(sec)
        results[name] = evaluate(name, fn)
        json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
