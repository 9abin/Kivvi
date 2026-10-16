"""Luna 선택에 보여 주는 발췌 비교 v2: 앞부분 vs 관련 문맥, 입력 글자 수와 제목을 맞춘다 (정답 근거 Recall만 본다).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_snippet2.py --questions eval/answer_eval_100.jsonl --repeat 2

v1(exp_snippet.py)에서 고친 점
  - 양쪽 모두 같은 제목을 붙인다: 같은 조의 청크에서 찾은 실제 "제N조(제목)". 본문 발췌는 정확히 N자 이하로 같은 예산.
  - 관련 문맥은 가장 잘 맞는 문장에서 시작해 앞뒤로 '연속된' 문장만 늘리고, 잘린 쪽에는 …를 붙인다.
  - 220자 앞부분(현재 운영 방식) 기준선을 포함한다.
  - 문장 점수는 질문마다 한 번만 계산해 재사용한다. 발췌 생성 시간, Luna 호출 시간, 응답 해석 실패·기존 순서로 채운 횟수를 센다.
선택하는 청크는 항 단위 그대로다(앞 항의 적용 대상까지 가져오는 조 전체 문맥은 이 실험에서 하지 않는다).
"""
import argparse
import json
import os
import re
import sys
import time
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

KS = (10,)
POOL = 16
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
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--out", default="exp_snippet2.json")
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

    # 같은 조의 어느 청크에든 있는 "제N조(제목)"으로 조 제목을 찾는다 (2항 이후 청크에는 제목이 없다)
    art_title = {}
    for cid, r in s.row_info.items():
        m = TITLE.search((r["body"] or "")[:200])
        if m:
            art_title.setdefault(ckey[cid], m.group(1))

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

    # 문장과 문장 점수를 질문마다 한 번만 계산한다
    t0 = time.time()
    sent_cache = []
    for (g, _), cands in zip(pools, tops):
        per = {}
        for h in cands:
            body = re.sub(r"[ \t]+", " ", (s.row_info[h["chunk_id"]]["body_search"] or "").strip())
            sents = [x.strip() for x in SENT.split(body) if x and x.strip()]
            sc = list(s.reranker.predict([(g["q"], x) for x in sents], batch_size=16)) if len(sents) > 1 else [1.0] * len(sents)
            per[h["chunk_id"]] = (body, sents, sc)
        sent_cache.append(per)
    sent_secs = (time.time() - t0) / len(pools)
    print(f"후보와 문장 점수 준비 완료 (문장 점수 {sent_secs:.1f}초/질문)", flush=True)

    def excerpt(per, chunk_id, mode, n):
        body, sents, sc = per[chunk_id]
        if mode == "앞부분" or len(body) <= n or len(sents) <= 1:
            return body[:n]
        best = max(range(len(sents)), key=lambda i: sc[i])
        lo = hi = best
        used = min(len(sents[best]), n)
        while True:
            add = []
            if lo > 0 and used + 1 + len(sents[lo - 1]) <= n:
                add.append(("lo", len(sents[lo - 1])))
            if hi < len(sents) - 1 and used + 1 + len(sents[hi + 1]) <= n:
                add.append(("hi", len(sents[hi + 1])))
            if not add:
                break
            for side, ln in add:
                if used + 1 + ln > n:
                    continue
                if side == "lo":
                    lo -= 1
                else:
                    hi += 1
                used += 1 + ln
        text = " ".join(sents[lo:hi + 1])[:n]
        return ("… " if lo > 0 else "") + text + (" …" if hi < len(sents) - 1 else "")

    stats = {"fail": 0, "fill": 0, "calls": 0, "sec": 0.0}

    def select(i, k, mode, n):
        q, cands = pools[i][0]["q"], tops[i]
        lines = []
        for j, h in enumerate(cands, 1):
            r = s.row_info[h["chunk_id"]]
            doc = s.documents.get(r["doc_id"], {}).get("title") or r["doc_id"]
            t = art_title.get(ckey[h["chunk_id"]])
            art = re.match(r"제\d+조(?:의\d+)?", r["article"] or "")
            lines.append(f"[{j}] {doc} {art.group(0) if art else r['article']}{f'({t})' if t else ''} {r['article'] if art and r['article'] != art.group(0) else ''} | "
                         + excerpt(sent_cache[i], h["chunk_id"], mode, n))
        t0_ = time.time()
        try:
            out = llm.invoke(SELECT_PROMPT.format(k=k, q=q, cands="\n".join(lines))).content
            ids = [int(x) for x in json.loads(re.search(r"\{.*\}", out, re.S).group(0))["순서"]]
        except Exception:  # noqa: BLE001
            ids = []
            stats["fail"] += 1
        stats["calls"] += 1
        stats["sec"] += time.time() - t0_
        chosen, seen = [], set()
        for x in ids:
            if 1 <= x <= len(cands) and x not in seen:
                seen.add(x)
                chosen.append(cands[x - 1])
        chosen = chosen[:k]
        if len(chosen) < k:
            stats["fill"] += 1
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
    for mode, n in (("앞부분", 220), ("앞부분", 300), ("관련 문맥", 300), ("앞부분", 600), ("관련 문맥", 600)):
        for k in KS:
            runs = []
            for k_ in (stats,):
                k_.update({"fail": 0, "fill": 0, "calls": 0, "sec": 0.0})
            for _ in range(args.repeat):
                with ThreadPoolExecutor(8) as ex:
                    chosen = list(ex.map(lambda i: select(i, k, mode, n), range(len(pools))))
                runs.append(recall(chosen, k))
            avg = sum(r[0] for r in runs) / len(runs)
            full = sum(r[1] for r in runs) / len(runs)
            results[f"{mode} {n}자 k{k}"] = {"평균Recall": avg, "회차": [r[0] for r in runs], "전부": full, **stats}
            print(f"[{mode:5s} {n:3d}자] Luna {k:2d}개 선택  Recall 평균 {avg:.3f} (회차 {', '.join(f'{r[0]:.3f}' for r in runs)}) 전부 확보 {full:.1f}"
                  f" | 호출 평균 {stats['sec'] / stats['calls']:.1f}초, 해석 실패 {stats['fail']}, 개수 모자라 채움 {stats['fill']}/{stats['calls']}", flush=True)
            json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
