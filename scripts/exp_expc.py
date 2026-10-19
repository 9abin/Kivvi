"""법제처 해석례로 후보를 늘리는 경로 시험 (정답 근거 Recall만 본다, LLM 호출 없음).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_expc.py --questions eval/answer_eval_100.jsonl

경로: 질문 → 비슷한 해석례(안건명+질의요지 앞 300자 BGE-M3 유사도) → 해석례가 다룬 법령·조 → 그 법령·조를 인용하는 내규 청크(cites의 외부 인용)
     → 이 청크들을 후보 합치기(RRF)에 다섯 번째 목록으로 더한다.
비교: 현재 후보(벡터+BM25, 원질문·바꿔쓴 검색어) vs 해석례 목록을 더한 후보.
같이 센다: 해석례 목록에서 들어온 청크 중 정답 조문이 몇 개인지(정밀도), 해석례 경로가 청크를 낸 질문 수.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import config  # noqa: E402
import src.search.search as ss  # noqa: E402
from src.citations import law_lookup as LL  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import RERANK_TOP, Searcher, group_by_article  # noqa: E402

KS = (5, 8, 10, 12, 16)
LAW_DIR = os.path.join("data", "manual", "law_rag.261013")


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--law-dir", default=LAW_DIR)
    ap.add_argument("--out", default="exp_expc.json")
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

    # --- 법령·조 → 내규 청크 (cites의 외부 인용을 뒤집은 색인) ---
    titles, _ = LL.load(os.path.join(args.law_dir, "law_rag.261013.sqlite3"))
    by_article, by_law = defaultdict(list), defaultdict(list)
    for cid, r in s.row_info.items():
        for c in r.get("cites") or []:
            if c.get("유형") != "외부" or not c.get("법령명"):
                continue
            name = LL._norm(c["법령명"])
            suffix = next((x for x in ("시행규칙", "시행령") if name.endswith(x)), "")
            base = name[:-len(suffix)] if suffix else name
            aliases = {LL._norm(k): LL._norm(v) for k, v in LL.LAW_ALIASES.items()}
            doc = titles.get(name) or titles.get(aliases.get(base, base) + suffix)
            if not doc:
                continue
            k_ = LL.article_key(c.get("원문", ""))
            if k_:
                by_article[(doc, k_)].append(cid)
            by_law[doc].append(cid)
    print(f"내규 청크가 인용하는 (법령, 조) {len(by_article)}개, 법령 {len(by_law)}개", flush=True)

    # --- 해석례 임베딩과 연결 ---
    ex = sqlite3.connect("file:" + os.path.join(args.law_dir, "expc.261013.sqlite3") + "?mode=ro", uri=True)
    rows = ex.execute("select expc_id, title, question from interpretations").fetchall()
    links = defaultdict(list)
    for expc_id, law_title, art in ex.execute("select expc_id, law_title, article_key from interpretation_links"):
        links[expc_id].append((titles.get(LL._norm(law_title)), art))
    D = s.model.encode([(t or "") + " " + (q or "").strip()[:300] for _, t, q in rows], normalize_embeddings=True,
                       batch_size=16, show_progress_bar=False)

    def expc_hits(qtext, n, thr, law_level):
        sims = D @ s.model.encode([qtext], normalize_embeddings=True, show_progress_bar=False)[0]
        order = [i for i in np.argsort(-sims)[:n] if sims[i] >= thr]
        out, seen = [], set()
        for i in order:
            for doc, art in links[rows[i][0]]:
                cands = by_article.get((doc, art), []) if art else []
                if not cands and law_level and doc:
                    cands = by_law.get(doc, [])
                for cid in cands:
                    if cid not in seen:
                        seen.add(cid)
                        out.append({"chunk_id": cid, **s.row_info[cid]})
        return out

    per_q = []
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        per_q.append({"g": g, "exact": exact, "taken": taken,
                      "vec": s.vector_hits(q, None), "vec_h": s.vector_hits(hint, None) if hint else [],
                      "bm": s.bm25_hits(q, None), "bm_h": s.bm25_hits(hint, None) if hint else []})
    print("후보 목록 준비 완료", flush=True)

    variants = [("현재 (해석례 없음)", None),
                ("+해석례 상위3, 유사도 0.55 이상, 같은 조만", (3, 0.55, False)),
                ("+해석례 상위5, 유사도 0.50 이상, 같은 조만", (5, 0.50, False)),
                ("+해석례 상위5, 유사도 0.50 이상, 법령 전체로 확장", (5, 0.50, True))]
    need = sum(len({key(t2d.get(e["규정"]), e["조항"]) for e in p["g"]["근거"]}) for p in per_q)
    results = {}
    for name, cfg in variants:
        pools, inj_total, inj_gold, inj_q = [], 0, 0, 0
        for p in per_q:
            lists = [("벡터", p["vec"], wv), ("키워드", p["bm"], wk), ("벡터(바꿔쓴)", p["vec_h"], wv), ("키워드(바꿔쓴)", p["bm_h"], wk)]
            if cfg:
                inj = expc_hits(p["g"]["q"], *cfg)
                lists.append(("해석례", inj, wk))
                want = {key(t2d.get(e["규정"]), e["조항"]) for e in p["g"]["근거"]}
                if inj:
                    inj_q += 1
                inj_total += len({ckey[h["chunk_id"]] for h in inj})
                inj_gold += len({ckey[h["chunk_id"]] for h in inj} & want)
            fused = s.fuse(lists[0][1], [], lists[0][2], 0.0, [x for x in lists[1:]])
            pools.append([h for h in fused if h["chunk_id"] not in p["taken"]][:RERANK_TOP])
        in_pool = sum(len({key(t2d.get(e["규정"]), e["조항"]) for e in p["g"]["근거"]} & {ckey[h["chunk_id"]] for h in pl})
                      for p, pl in zip(per_q, pools))
        orders = []
        for p, pl in zip(per_q, pools):
            ranked = s.rerank(p["g"]["q"], [dict(h) for h in pl])
            ranked, _ = glossary.filter_candidates(p["g"]["q"], ranked, s.glossary)
            orders.append(group_by_article(ranked))

        def recall(k):
            got = full = 0
            for p, order in zip(per_q, orders):
                want = {key(t2d.get(e["규정"]), e["조항"]) for e in p["g"]["근거"]}
                res = []
                for h in (p["exact"] + order)[:k]:
                    it = s._entry(h, status=h.get("status"))
                    it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                               "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                    res.append(it)
                asm = s.assemble(res)
                used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
                got += len(want & used)
                full += want <= used
            return got / need, full

        cells = {k: recall(k) for k in KS}
        results[name] = {"후보안정답": in_pool / need, "Recall": cells, "주입질문": inj_q, "주입조": inj_total, "주입정답": inj_gold}
        print(f"\n[{name}] 후보 30개 안에 정답 {in_pool}/{need} = {in_pool / need:.3f}", flush=True)
        print("  BGE 점수순 Recall " + " ".join(f"k{k} {r:.3f}({f})" for k, (r, f) in cells.items()), flush=True)
        if cfg:
            print(f"  해석례 경로: 청크를 낸 질문 {inj_q}/100, 들어온 조 {inj_total}개 중 정답 조 {inj_gold}개", flush=True)
        json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
