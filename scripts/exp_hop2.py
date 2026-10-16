"""보완 검색(2단계) 시험: 첫 검색 결과에서 제도명을 뽑아 다른 항을 더 찾는다 (정답 근거 Recall만 본다).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_hop2.py --questions eval/answer_eval_100.jsonl --repeat 2

검색어는 원질문과 '실제 첫 검색 결과'로만 만든다. 평가 정답은 측정에만 쓴다.
  T  첫 검색 상위 조의 조 제목을 검색어로 쓴다 (LLM 없음)
  L  Luna가 질문과 첫 검색 상위 조(제목·앞부분)를 보고 보완 검색어를 만든다 (질문당 호출 1번)
검색어마다 다른 조의 항을 찾아(벡터+BM25 가중 RRF) 기존 후보 30개 밖의 것을 최대 6개 추가한다. 기존 30개는 그대로 둔다.
대조군: 후보 수만 늘린 W36(RRF 상위 36개), W100(RRF 상위 100개).
측정(후보 단계): 후보 30개 밖이던 정답(누락)을 몇 개 되찾았나, 추가 후보 수, 추가 시간. 정답 목록에 없는 조문을 오류로 세지 않는다.
측정(최종 단계): 같은 질문 전체에서 BGE만 k=8·10·16, BGE 상위 16개 조 → Luna 10개 선택(기존 정답이 보존되는지).
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
from src.search.search import Searcher, group_by_article  # noqa: E402

TITLE = re.compile(r"제\d+조(?:의\d+)?\(([^)]+)\)")
KS = (8, 10, 16)
EXTRA = 6

HOP_PROMPT = """직원 질문에 답하려면 첫 검색 결과의 조문과 함께 확인해야 하는 다른 규정이 있을 수 있다.
질문과 첫 검색 결과(조문 제목과 앞부분)를 보고, 다른 규정에서 같은 제도나 상황을 다루는 항을 찾기 위한 검색어를 만든다.
- 검색어는 1~3개, 각각 2~6단어. 첫 검색 결과에 이미 나온 제도, 상황, 대상의 이름을 쓴다.
- 질문의 말을 그대로 반복하지 말고, 이 제도가 적용되거나 영향을 주는 다른 처리(조건, 예외, 근태, 절차)를 찾을 수 있는 말을 쓴다.
- 답을 쓰지 않는다. 출력은 JSON 하나: {{"검색어": ["...", "..."]}}

질문: {q}

첫 검색 결과:
{first}"""


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
    ap.add_argument("--out", default="exp_hop2.json")
    args = ap.parse_args()

    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    llm = ChatOpenAI(model=config.JUDGE_MODEL, temperature=0, timeout=120, max_retries=2)
    s._select_llm = llm
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

    # 질문마다: 전체 RRF 순서, 현재 상위 30개, 첫 검색(BGE) 결과
    data = []
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
        extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
        full = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken]
        a30 = full[:30]
        ranked = s.rerank(q, [dict(h) for h in a30])
        ranked, _ = glossary.filter_candidates(q, ranked, s.glossary)
        first = group_by_article(ranked)[:5]
        want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
        data.append({"g": g, "exact": exact, "full": full, "a30": a30, "first": first, "want": want})
    print("첫 검색까지 준비 완료", flush=True)

    need = sum(len(d["want"]) for d in data)
    far = [(d["want"] & {ckey[h["chunk_id"]] for h in d["full"]}) - {ckey[h["chunk_id"]] for h in d["a30"]} for d in data]
    n_far = sum(len(x) for x in far)
    print(f"후보 30개 밖이지만 전체 후보에는 있는 정답(누락) {n_far}개", flush=True)

    def hop_hits(queries, d):
        in_a30 = {h["chunk_id"] for h in d["a30"]}
        skip_arts = {ckey[h["chunk_id"]] for h in d["first"]} | {ckey[h["chunk_id"]] for h in d["a30"]}
        seen, out = set(), []
        for qq in queries:
            fused = s.fuse(s.vector_hits(qq, None), s.bm25_hits(qq, None), wv, wk)
            n = 0
            for h in fused:
                c = h["chunk_id"]
                if c in in_a30 or c in seen or ckey[c] in skip_arts:
                    continue
                seen.add(c)
                out.append(h)
                n += 1
                if n >= 3:
                    break
        return out[:EXTRA]

    def titles_of(d):
        return list(dict.fromkeys(art_title[ckey[h["chunk_id"]]] for h in d["first"] if ckey[h["chunk_id"]] in art_title))[:3]

    def luna_queries(d):
        lines = []
        for h in d["first"]:
            r = s.row_info[h["chunk_id"]]
            doc = s.documents.get(r["doc_id"], {}).get("title") or r["doc_id"]
            lines.append(f"- {doc} {r['article']} ({art_title.get(ckey[h['chunk_id']], '')}) {re.sub(chr(10), ' ', r['body_search'] or '')[:100]}")
        try:
            out = llm.invoke(HOP_PROMPT.format(q=d["g"]["q"], first="\n".join(lines))).content
            return [x for x in json.loads(re.search(r"\{.*\}", out, re.S).group(0))["검색어"] if x][:3]
        except Exception:  # noqa: BLE001
            return []

    variants = {"A30 (기준)": [], "W36 (RRF 상위 36)": [], "W100 (RRF 상위 100)": [], "T 제목 검색": [], "L Luna 검색어": []}
    add_info = {}
    for name in variants:
        t0 = time.time()
        pools = []
        if name.startswith("A30"):
            pools = [d["a30"] for d in data]
        elif name.startswith("W36"):
            pools = [d["full"][:36] for d in data]
        elif name.startswith("W100"):
            pools = [d["full"][:100] for d in data]
        elif name.startswith("T"):
            pools = [d["a30"] + hop_hits(titles_of(d), d) for d in data]
        else:
            with ThreadPoolExecutor(8) as ex:
                qs = list(ex.map(luna_queries, data))
            pools = [d["a30"] + hop_hits(q_, d) for d, q_ in zip(data, qs)]
        secs = (time.time() - t0) / len(data)
        added = sum(len(p) - 30 for p in pools) / len(data)
        in_pool = sum(len(d["want"] & {ckey[h["chunk_id"]] for h in p}) for d, p in zip(data, pools))
        rec_far = sum(len(f & {ckey[h["chunk_id"]] for h in p}) for f, p in zip(far, pools))
        lost = sum(len((d["want"] & {ckey[h["chunk_id"]] for h in d["a30"]}) - {ckey[h["chunk_id"]] for h in p}) for d, p in zip(data, pools))
        add_info[name] = (in_pool, rec_far, lost, added, secs, pools)
        print(f"[{name}] 후보 안 정답 {in_pool}/{need} | 누락 {n_far}개 중 되찾음 {rec_far} | 기존 후보 정답 탈락 {lost} | 추가 후보 평균 {added:.1f}개 | 추가 시간 {secs:.1f}초/질문", flush=True)

    def recall(chosen_per_q, k):
        got = full_ = 0
        for d, chosen in zip(data, chosen_per_q):
            res = []
            for h in (d["exact"] + chosen)[:k]:
                it = s._entry(h, status=h.get("status"))
                it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                           "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                res.append(it)
            asm = s.assemble(res)
            used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
            got += len(d["want"] & used)
            full_ += d["want"] <= used
        return got / need, full_

    results = {}
    print("\n--- 최종 단계 (같은 100문항 전체) ---", flush=True)
    for name, (in_pool, rec_far, lost, added, secs, pools) in add_info.items():
        orders = []
        for d, p in zip(data, pools):
            ranked = s.rerank(d["g"]["q"], [dict(h) for h in p])
            ranked, _ = glossary.filter_candidates(d["g"]["q"], ranked, s.glossary)
            orders.append(group_by_article(ranked))
        cells = {k: recall(orders, k) for k in KS}
        runs = []
        for _ in range(args.repeat):
            with ThreadPoolExecutor(8) as ex:
                chosen = list(ex.map(lambda a: s._llm_select(a[0]["g"]["q"], a[1][:16], 10), zip(data, orders)))
            runs.append(recall(chosen, 10))
        avg = sum(r[0] for r in runs) / len(runs)
        results[name] = {"후보안정답": in_pool, "되찾음": rec_far, "탈락": lost, "추가": added, "초": secs, "BGE": cells, "Luna10": avg}
        print(f"[{name}] BGE만 " + " ".join(f"k{k} {r:.3f}({f})" for k, (r, f) in cells.items())
              + f" | BGE 16 → Luna 10 평균 {avg:.3f} (회차 {', '.join(f'{r[0]:.3f}' for r in runs)})", flush=True)
        json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
