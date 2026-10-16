"""Luna 선택에 보여 주는 발췌를 비교한다: 앞부분 N자 vs 관련 문맥 N자 (정답 근거 Recall만 본다).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_snippet.py --questions eval/answer_eval_100.jsonl --repeat 2

같은 후보(현재 방식으로 줄 세운 BGE 상위 16개 조), 같은 Luna 선택 프롬프트, 같은 입력 글자 수(N)로 발췌만 바꾼다.
  앞부분    본문 앞 N자 (현재 방식은 220자)
  관련 문맥 조 제목(첫 줄) + 질문과 가장 잘 맞는 문장과 그 앞뒤 문장을 N자까지, 원래 순서대로
문장 점수는 BGE 리랭커(질문, 문장)로 계산한다. Luna에게는 원질문만 보여 준다.
"""
import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import config  # noqa: E402
import src.search.search as ss  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import RERANK_TOP, SELECT_PROMPT, Searcher, group_by_article  # noqa: E402

KS = (8, 10)
POOL = 16
SENT = re.compile(r"(?<=[.다요])\s+|\n+")


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--out", default="exp_snippet.json")
    args = ap.parse_args()

    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    llm = ChatOpenAI(model=config.JUDGE_MODEL, temperature=0, timeout=120, max_retries=2)
    t2d = {v["title"]: d for d, v in s.documents.items()}
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    ss.CITE_WHOLE_ARTICLE = True
    ss.SIBLING_LIMIT = 3000

    pools, tops = [], []
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
        extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
        fused = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken][:RERANK_TOP]
        ranked = s.rerank(q, [dict(h) for h in fused])
        ranked, _ = glossary.filter_candidates(q, ranked, s.glossary)
        pools.append((g, exact))
        tops.append(group_by_article(ranked)[:POOL])
    print("후보 준비 완료", flush=True)

    def context_snippet(q, chunk_id, n):
        body = (s.row_info[chunk_id]["body_search"] or "").strip()
        head = body.split("\n")[0][:60]
        sents = [x.strip() for x in SENT.split(body) if x and x.strip()]
        if len(body) <= n or len(sents) <= 1:
            return re.sub(r"\s+", " ", body)[:n]
        sc = s.reranker.predict([(q, x) for x in sents], batch_size=16)
        best = max(range(len(sents)), key=lambda i: sc[i])
        keep, used = {best}, len(sents[best])
        lo, hi = best - 1, best + 1
        while used < n and (lo >= 0 or hi < len(sents)):
            for j in (lo, hi):
                if 0 <= j < len(sents) and used + len(sents[j]) <= n and j not in keep:
                    keep.add(j)
                    used += len(sents[j])
            lo, hi = lo - 1, hi + 1
            if lo < 0 and hi >= len(sents):
                break
        text = " ".join(sents[i] for i in sorted(keep))
        return re.sub(r"\s+", " ", (head + " … " + text) if 0 not in keep else text)[:n + 60]

    def select(q, cands, k, mode, n):
        lines = []
        for i, h in enumerate(cands, 1):
            r = s.row_info[h["chunk_id"]]
            title = s.documents.get(r["doc_id"], {}).get("title") or r["doc_id"]
            snip = re.sub(r"\s+", " ", r["body_search"] or "")[:n] if mode == "앞부분" else context_snippet(q, h["chunk_id"], n)
            lines.append(f"[{i}] {title} {r['article']} | {snip}")
        try:
            out = llm.invoke(SELECT_PROMPT.format(k=k, q=q, cands="\n".join(lines))).content
            ids = [int(x) for x in json.loads(re.search(r"\{.*\}", out, re.S).group(0))["순서"]]
        except Exception:  # noqa: BLE001
            ids = []
        chosen, seen = [], set()
        for i in ids:
            if 1 <= i <= len(cands) and i not in seen:
                seen.add(i)
                chosen.append(cands[i - 1])
        chosen = chosen[:k]
        return chosen + [h for h in cands if h["chunk_id"] not in {c["chunk_id"] for c in chosen}][:k - len(chosen)]

    need = sum(len({key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}) for g, _ in pools)

    def recall(chosen_per_q, k):
        got = full = 0
        for (g, exact), chosen in zip(pools, chosen_per_q):
            want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
            res = []
            for h in (exact + chosen)[:k]:
                it = s._entry(h, status=h.get("status"))
                it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                           "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                res.append(it)
            asm = s.assemble(res)
            used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
            got += len(want & used)
            full += want <= used
        return got / need, full

    results = {}
    for n in (300, 600):
        for mode in ("앞부분", "관련 문맥"):
            for k in KS:
                runs = []
                for _ in range(args.repeat):
                    with ThreadPoolExecutor(8) as ex:
                        chosen = list(ex.map(lambda a: select(a[0][0]["q"], a[1], k, mode, n), zip(pools, tops)))
                    runs.append(recall(chosen, k))
                avg = sum(r[0] for r in runs) / len(runs)
                full = sum(r[1] for r in runs) / len(runs)
                results[f"{mode} {n}자 k{k}"] = {"평균Recall": avg, "회차": [r[0] for r in runs], "전부": full}
                print(f"[{mode:5s} {n}자] Luna {k:2d}개 선택  Recall 평균 {avg:.3f} (회차 {', '.join(f'{r[0]:.3f}' for r in runs)}) 전부 확보 {full:.1f}", flush=True)
                json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
