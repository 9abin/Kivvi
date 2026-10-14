"""리스트와이즈 리랭킹 비교 (정답 근거 Recall만 본다, 최종 답변은 만들지 않는다).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_listwise.py --questions eval/answer_eval_100.jsonl

같은 후보(RRF 상위 30개, scripts/exp_reranker_bakeoff.py의 캐시)를 세 가지로 줄 세워 비교한다.
  BGE 점수순   현재 방식 (기준선)
  전부 교체    후보 30개를 RRF 순서로 LLM에 보여 주고 필요한 순서대로 고르게 함
  보완         BGE 상위 16개를 BGE 순서로 보여 주고 필요한 순서대로 고르게 함
LLM이 고른 청크를 앞에 두고, 나머지는 BGE 순서로 뒤를 채운다. 그 뒤(조 묶기, 용어집 필터, 조립)는 현재와 같다.
"""
import argparse
import json
import os
import pickle
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
from src.search import glossary  # noqa: E402
from src.search.search import Searcher, group_by_article  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402

KS = (8, 10, 12, 16)
SNIP = 220
PICK = 12

PROMPT = """한국벤처투자 내부 규정 검색 결과를 다시 줄 세운다.
직원 질문에 답하려면 읽어야 하는 조문을 중요한 순서대로 최대 {n}개 고른다.
- 질문에 직접 답하는 조문을 앞에 두고, 답의 조건·예외·정의·기준표를 알려 주는 조문도 포함한다.
- 같은 규정의 비슷한 조문만 몰아 고르지 말고, 질문에 꼭 필요한 것만 고른다.
- 후보 번호만 JSON으로 답한다: {{"순서": [번호, 번호, ...]}}

질문: {q}

후보:
{cands}"""


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="exp_listwise.json")
    args = ap.parse_args()

    pre = pickle.load(open(os.path.join("eval", "runs", "bakeoff_pool_cache.pkl"), "rb"))
    if args.limit:
        pre = pre[:args.limit]
    s = Searcher()
    s.glossary_filter = True
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
    ss.CITE_WHOLE_ARTICLE = True
    ss.SIBLING_LIMIT = 3000
    need = sum(len(p["want"]) for p in pre)
    llm = ChatOpenAI(model=config.JUDGE_MODEL, temperature=0, timeout=120, max_retries=2)

    def line(i, h):
        r = s.row_info[h["chunk_id"]]
        title = s.documents.get(r["doc_id"], {}).get("title") or r["doc_id"]
        body = re.sub(r"\s+", " ", r["body_search"] or "")[:SNIP]
        return f"[{i}] {title} {r['article']} | {body}"

    def pick(q, cands, n=PICK):
        text = PROMPT.format(n=n, q=q, cands="\n".join(line(i, h) for i, h in enumerate(cands, 1)))
        out = llm.invoke(text).content
        m = re.search(r"\{.*\}", out, re.S)
        try:
            ids = [int(x) for x in json.loads(m.group(0))["순서"]]
        except Exception:  # noqa: BLE001
            ids = []
        seen, res = set(), []
        for i in ids:
            if 1 <= i <= len(cands) and i not in seen:
                seen.add(i)
                res.append(cands[i - 1])
        return res

    # BGE 기준 순서
    t = time.time()
    bge = []
    for p in pre:
        bge.append(s.rerank(p["g"]["q"], [dict(h) for h in p["pool"]]))
    print(f"BGE 줄 세우기 완료 ({time.time() - t:.0f}초)", flush=True)

    def merged(chosen, base):
        ids = {h["chunk_id"] for h in chosen}
        return chosen + [h for h in base if h["chunk_id"] not in ids]

    def run_llm(name, make_cands):
        t0 = time.time()

        def one(a):
            p, b = a
            cands = make_cands(p, b)
            return merged(pick(p["g"]["q"], cands), b)

        with ThreadPoolExecutor(8) as ex:
            orders = list(ex.map(one, zip(pre, bge)))
        return orders, (time.time() - t0) / len(pre)

    def evaluate(name, orders, secs):
        row = {"이름": name, "LLM초": secs}
        for k in KS:
            got = full = 0
            for p, order in zip(pre, orders):
                ranked, _ = glossary.filter_candidates(p["g"]["q"], order, s.glossary)
                ranked = group_by_article(ranked)
                res = []
                for h in (p["exact"] + ranked)[:k]:
                    it = s._entry(h, status=h.get("status"))
                    it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                               "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                    res.append(it)
                asm = s.assemble(res)
                used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
                got += len(p["want"] & used)
                full += p["want"] <= used
            row[f"R{k}"], row[f"F{k}"] = got / need, full
        print(f"{name:12s} " + " ".join(f"k{k} {row[f'R{k}']:.3f}({row[f'F{k}']:3d})" for k in KS) + f" | LLM {secs:.1f}초/질문", flush=True)
        return row

    results = [evaluate("BGE 점수순", bge, 0.0)]
    json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for name, mk in (("전부 교체", lambda p, b: p["pool"]), ("보완(16개)", lambda p, b: b[:16])):
        orders, secs = run_llm(name, mk)
        results.append(evaluate(name, orders, secs))
        json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
