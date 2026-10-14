"""BGE로 줄 세운 상위 16개 조 → Luna가 그 16개만 보고 k개를 골라 근거로 쓴다 (정답 근거 Recall만 본다).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_listwise_topk.py --repeat 2

흐름: 후보 30개(캐시) → BGE 점수순 → 용어집 필터 → 같은 조 묶기 → 상위 16개 조 → Luna가 k개 선택(k=5·8·10) → 근거 조립.
Luna가 k개를 못 채우면 BGE 순서로 채운다(채운 횟수를 센다). 비교 기준은 BGE 상위 k개 조.
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
from langchain_openai import ChatOpenAI  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import Searcher, group_by_article  # noqa: E402

KS = (5, 8, 10)
POOL = 16
PROMPT = """한국벤처투자 내부 규정 검색 결과에서 직원 질문에 답하는 데 필요한 조문을 정확히 {k}개 고른다.
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
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--out", default="exp_listwise_topk.json")
    args = ap.parse_args()

    pre = pickle.load(open(os.path.join("eval", "runs", "bakeoff_pool_cache.pkl"), "rb"))
    s = Searcher()
    s.glossary_filter = True
    ss.CITE_WHOLE_ARTICLE = True
    ss.SIBLING_LIMIT = 3000
    need = sum(len(p["want"]) for p in pre)
    llm = ChatOpenAI(model=config.JUDGE_MODEL, temperature=0, timeout=120, max_retries=2)

    def line(i, h):
        r = s.row_info[h["chunk_id"]]
        title = s.documents.get(r["doc_id"], {}).get("title") or r["doc_id"]
        return f"[{i}] {title} {r['article']} | " + re.sub(r"\s+", " ", r["body_search"] or "")[:220]

    # BGE 순서 → 용어집 필터 → 조 묶기 → 상위 16개 조
    top16 = []
    for p in pre:
        ranked = s.rerank(p["g"]["q"], [dict(h) for h in p["pool"]])
        ranked, _ = glossary.filter_candidates(p["g"]["q"], ranked, s.glossary)
        top16.append(group_by_article(ranked)[:POOL])
    print("BGE 상위 16개 조 준비 완료", flush=True)

    def assemble_recall(chosen_per_q, k):
        got = full = 0
        for p, chosen in zip(pre, chosen_per_q):
            res = []
            for h in (p["exact"] + chosen)[:k]:
                it = s._entry(h, status=h.get("status"))
                it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                           "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                res.append(it)
            asm = s.assemble(res)
            used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
            got += len(p["want"] & used)
            full += p["want"] <= used
        return got / need, full

    results = {}
    for k in KS:
        r, f = assemble_recall(top16, k)
        results[f"BGE 상위{k}"] = [(r, f)]
        print(f"BGE 상위 {k:2d}개 조          Recall {r:.3f} (전부 {f:3d})", flush=True)

    for k in KS:
        for rep in range(1, args.repeat + 1):
            stats = {"fail": 0, "fill": 0}

            def one(a):
                p, cands = a
                text = PROMPT.format(k=k, q=p["g"]["q"], cands="\n".join(line(i, h) for i, h in enumerate(cands, 1)))
                m = re.search(r"\{.*\}", llm.invoke(text).content, re.S)
                try:
                    ids = [int(x) for x in json.loads(m.group(0))["순서"]]
                except Exception:  # noqa: BLE001
                    ids = []
                    stats["fail"] += 1
                seen, chosen = set(), []
                for i in ids:
                    if 1 <= i <= len(cands) and i not in seen:
                        seen.add(i)
                        chosen.append(cands[i - 1])
                chosen = chosen[:k]
                if len(chosen) < k:
                    stats["fill"] += 1
                    ids_set = {h["chunk_id"] for h in chosen}
                    chosen += [h for h in cands if h["chunk_id"] not in ids_set][:k - len(chosen)]
                return chosen

            t0 = time.time()
            with ThreadPoolExecutor(8) as ex:
                chosen_per_q = list(ex.map(one, zip(pre, top16)))
            r, f = assemble_recall(chosen_per_q, k)
            results.setdefault(f"Luna {k}", []).append((r, f))
            print(f"Luna가 16개 중 {k:2d}개 선택 #{rep}  Recall {r:.3f} (전부 {f:3d}) | 파싱 실패 {stats['fail']}, 개수 모자라 채움 {stats['fill']} | {time.time() - t0:.0f}초", flush=True)
            json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
